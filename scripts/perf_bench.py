"""StuLink 性能压测工具 v1.1

用法（可本机、也可在容器内跑）：
  python scripts/perf_bench.py probe      # 冒烟：确认会话有效、各端点 200 且非空
  python scripts/perf_bench.py bench      # 基线/复测：冷计算耗时 + 热缓存并发 P50/P95/P99

设计：
  - 登录态：用 app.session_interface 直接签发管理员 session cookie，绕开登录表单与
    频率限制。**必须同时写入三样**，否则会被安全机制判为无效会话：
      `_user_id`  Flask-Login 登录标识
      `_pwd_fp`   session_guard 的口令摘要（sha256(password_hash)），改密即失效
      `_id`       Flask-Login 会话标识（session_protection='strong'；缺失会**清空会话**）
  - 样本自动挑选：优先选**已划线且有成绩**的考试（report_* 未划线时会提前返回 error，
    测不到真实计算量）；学号自动取库里第一个。
  - 冷/热：冷样本用同一端点换一场考试；热并发用同一端点预热后 threads×n 并发。
  - 依赖：仅标准库 urllib（容器内未装 requests）。

可通过环境变量覆盖：
  STULINK_BENCH_BASE   默认 http://127.0.0.1:5000（主应用；往届查询在 5001）
  STULINK_BENCH_EXAM   热样本考试 id
  STULINK_BENCH_COLD   冷样本考试 id
  STULINK_BENCH_STU    学号

目标（用户规则）：接口响应 P95 < 500ms；冷计算单次 < 3s。
"""
import hashlib
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

BASE = os.environ.get('STULINK_BENCH_BASE', 'http://127.0.0.1:5000')
UA = 'stulink-perf-bench/1.1'

# (名称, 路径模板, 冷/热)
ENDPOINTS = [
    ('options',          '/grades/api/options', 'warm'),
    ('analysis_grade',   '/grades/api/analysis/grade?exam_id={e}&direction=', 'both'),
    ('analysis_class',   '/grades/api/analysis/class?exam_id={e}&class_name=01%E7%8F%AD', 'both'),
    ('analysis_subject', '/grades/api/analysis/subject?exam_id={e}&subject=%E8%AF%AD%E6%96%87&direction=', 'both'),
    ('analysis_teacher', '/grades/api/analysis/teacher?exam_id={e}&subject=%E8%AF%AD%E6%96%87', 'both'),
    ('pivot_meta',       '/grades/api/pivot/meta?exam_id={e}', 'warm'),
    ('pivot',            '/grades/api/pivot?exam_id={e}&row=class&col=&subject=%E6%80%BB%E5%88%86&direction=&measures=', 'both'),
    ('report_sl',        '/grades/api/report/subject-layer?exam_id={e}&direction=&layer=', 'both'),
    ('report_co',        '/grades/api/report/class-overview?exam_id={e}', 'both'),
    ('report_cs',        '/grades/api/report/class-subject?exam_id={e}&class_name=01%E7%8F%AD', 'both'),
    ('report_sc',        '/grades/api/report/subject-classes?exam_id={e}&direction=&layer=&subject=%E8%AF%AD%E6%96%87', 'both'),
    ('report_near',      '/grades/api/report/near-line?exam_id={e}&direction=&layer=&n=10&m=20', 'both'),
    ('teacher_ranks',    '/grades/api/report/teacher-ranks?exam_id={e}&direction=&layer=', 'both'),
    ('stu_options',      '/grades/api/student-query/options', 'warm'),
    ('stu_data',         '/grades/api/student-query/data?student_no={s}&exam_id={e}', 'warm'),
    ('page_report',      '/grades/report?exam_id={e}', 'warm'),
]

EXAM_WARM = 0      # 由 _pick_samples() 填充
EXAM_COLD = 0
STU_NO = ''


def _pick_samples():
    """自动挑选可用的考试/学号：优先「已划线且有成绩」（否则 report_* 提前返回 error）。"""
    global EXAM_WARM, EXAM_COLD, STU_NO
    from app.extensions import db
    with db.engines['grades'].connect() as c:
        banded = [r[0] for r in c.exec_driver_sql(
            'select exam_id, count(*) n from exam_bands '
            'group by exam_id order by n desc')]
        scored = {r[0]: r[1] for r in c.exec_driver_sql(
            'select exam_id, count(*) from exam_scores group by exam_id')}
        exams = {r[0] for r in c.exec_driver_sql('select id from exams')}
    banded = [e for e in banded if e in exams]
    scored_order = [e for e, _ in sorted(scored.items(), key=lambda kv: -kv[1])]
    fallback = scored_order[0] if scored_order else (banded[0] if banded else None)
    EXAM_WARM = int(os.environ.get('STULINK_BENCH_EXAM')
                    or (banded[0] if banded else fallback) or 0)
    EXAM_COLD = int(os.environ.get('STULINK_BENCH_COLD')
                    or next((e for e in (banded or scored_order) if e != EXAM_WARM),
                            EXAM_WARM))
    with db.engines['system'].connect() as c:
        STU_NO = os.environ.get('STULINK_BENCH_STU') or (
            c.exec_driver_sql('select student_number from students '
                              'where student_number is not null limit 1').scalar() or '')
    print('  样本：热考试=%s 冷考试=%s 学号=%s（共 %d 场，已划线 %d 场）'
          % (EXAM_WARM, EXAM_COLD, STU_NO or '-', len(exams), len(banded)))


def make_cookie():
    """签发管理员会话 cookie（含 _user_id / _pwd_fp / _id 三要素）。"""
    from app import create_app
    from app.extensions import db
    from app.models import User
    try:
        from flask_login.utils import _create_identifier
    except Exception:  # noqa: BLE001
        _create_identifier = None

    app = create_app()
    with app.app_context():
        user = (User.query.filter_by(role='admin').order_by(User.id).first()
                or User.query.first())
        if user is None:
            raise SystemExit('库中没有任何用户，无法签发会话')
        fp = hashlib.sha256((getattr(user, 'password_hash', '') or '')
                            .encode('utf-8')).hexdigest()
        print('  会话用户：%s（id=%s role=%s）' % (user.username, user.id, user.role))
        _pick_samples()

    if _create_identifier is not None:
        with app.test_request_context('/', headers={'User-Agent': UA},
                                      environ_overrides={'REMOTE_ADDR': '127.0.0.1'}):
            ident = _create_identifier()
    else:
        ident = hashlib.sha1(('127.0.0.1|%s' % UA).encode('utf-8')).hexdigest()

    with app.test_request_context('/'):
        return app.session_interface.get_signing_serializer(app).dumps(
            {'_user_id': str(user.id), '_fresh': True,
             '_id': ident, '_pwd_fp': fp})


def one(cookie, path):
    req = urllib.request.Request(
        BASE + path, headers={'Cookie': 'session=' + cookie, 'User-Agent': UA})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return (time.perf_counter() - t0) * 1000, r.status, len(r.read())
    except urllib.error.HTTPError as e:
        try:
            n = len(e.read() or b'')
        except Exception:  # noqa: BLE001
            n = 0
        return (time.perf_counter() - t0) * 1000, e.code, n
    except Exception as e:  # noqa: BLE001
        return float('nan'), -1, str(e)[:60]


def _path(tmpl, exam):
    return tmpl.format(e=exam, s=STU_NO)


def probe(cookie):
    bad = 0
    for name, tmpl, _ in ENDPOINTS:
        dt, sc, size = one(cookie, _path(tmpl, EXAM_WARM))
        # 200 且返回体不像登录页（9574B 是登录页长度）才算通过
        ok = sc == 200 and size != 9574
        bad += (not ok)
        print('%s %-16s %3s %8.0fms %s'
              % ('OK ' if ok else 'BAD', name, sc, dt, size))
    print('PROBE_OK' if bad == 0 else 'PROBE_FAIL x%d' % bad)


def bench(cookie, threads=8, n=24):
    rows = []
    for name, tmpl, mode in ENDPOINTS:
        cold = ''
        if mode in ('both', 'cold'):
            dt, sc, _ = one(cookie, _path(tmpl, EXAM_COLD))
            if sc != 200:
                rows.append((name, 'ERR%d' % sc, '', '', '', '', '', ''))
                continue
            cold = '%.0f' % dt
        path = _path(tmpl, EXAM_WARM)
        one(cookie, path)                      # 预热
        with ThreadPoolExecutor(max_workers=threads) as ex:
            res = list(ex.map(lambda _: one(cookie, path), range(n)))
        times = sorted(r[0] for r in res if r[1] == 200)
        codes = {r[1] for r in res}
        if not times:
            rows.append((name, cold, 'ERR%s' % codes, '', '', '', '', ''))
            continue
        p50 = statistics.median(times)
        p95 = times[min(len(times) - 1, int(len(times) * 0.95) - 1)]
        p99 = times[min(len(times) - 1, int(len(times) * 0.99) - 1)]
        rows.append((name, cold, '%.0f' % p50, '%.0f' % p95, '%.0f' % p99,
                     '%.0f' % times[-1], ','.join(map(str, sorted(codes))),
                     '%dKB' % (res[0][2] // 1024)))
    hdr = '%-17s %6s %5s %5s %5s %6s %5s %7s' % (
        'endpoint', 'cold', 'P50', 'P95', 'P99', 'max', 'code', 'size')
    print(hdr)
    print('-' * len(hdr))
    for r in rows:
        print('%-17s %6s %5s %5s %5s %6s %5s %7s' % r)
    over = [r[0] for r in rows if r[3].isdigit() and int(r[3]) > 500]
    slow = [r[0] for r in rows if r[1].isdigit() and int(r[1]) > 3000]
    print('\n样本：热考试=%s 冷考试=%s' % (EXAM_WARM, EXAM_COLD))
    print('热 P95>500ms: %s' % (over or '无'))
    print('冷计算>3s : %s' % (slow or '无'))


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'probe'
    ck = make_cookie()
    (probe if mode == 'probe' else bench)(ck)

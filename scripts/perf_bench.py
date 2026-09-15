"""StuLink 性能压测工具 v1.0

用法：
  python scripts/perf_bench.py probe      # 冒烟：确认签名 cookie 有效、各端点 200
  python scripts/perf_bench.py bench      # 基线/复测：冷计算耗时 + 热缓存并发 P50/P95/P99

设计：
  - 登录态：用 app.session_interface.get_signing_serializer 直接签发 admin session
    cookie，绕开登录表单与频率限制（等价已登录）。
  - 冷缓存：压测进程清不了服务端内存缓存，改用「考试23从未被请求过」保证冷；
    两次冷测之间需重启 5001 实例。
  - 热并发：考试30先预热 1 次，再 threads×n 并发，统计分位数。
  - 目标（用户规则）：接口响应 P95 < 500ms；冷计算单次 < 3s。
"""
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import requests  # noqa: E402

BASE = 'http://127.0.0.1:5001'
EXAM_COLD = 23       # ~10500 成绩行，冷计算样本
EXAM_WARM = 30       # 并发场景样本（预热后测分位数）
STU_NO = '20260001'

# (名称, 路径, 冷/热考试)
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
    ('stu_data',         f'/grades/api/student-query/data?student_no={STU_NO}&exam_id={{e}}', 'warm'),
    ('page_report',      '/grades/report?exam_id={e}', 'warm'),
]


def make_session():
    from app import create_app
    app = create_app()
    with app.test_request_context('/'):
        cookie = app.session_interface.get_signing_serializer(app).dumps(
            {'_user_id': '1', '_fresh': True})
    sess = requests.Session()
    sess.cookies.set('session', cookie, domain='127.0.0.1')
    return sess


def one(sess, path):
    t0 = time.perf_counter()
    try:
        r = sess.get(BASE + path, timeout=180)
        return (time.perf_counter() - t0) * 1000, r.status_code, len(r.content)
    except Exception as e:  # noqa: BLE001
        return float('nan'), -1, str(e)[:60]


def probe(sess):
    bad = 0
    for name, tmpl, _ in ENDPOINTS:
        path = tmpl.format(e=EXAM_WARM)
        dt, sc, size = one(sess, path)
        flag = 'OK ' if sc == 200 else 'BAD'
        if sc != 200:
            bad += 1
        print(f'{flag} {name:16} {sc} {dt:7.0f}ms {size if isinstance(size,int) else size}')
    print('PROBE_OK' if bad == 0 else f'PROBE_FAIL x{bad}')


def bench(sess, threads=8, n=24):
    rows = []
    for name, tmpl, mode in ENDPOINTS:
        cold_ms = ''
        if mode in ('both', 'cold'):
            dt, sc, _ = one(sess, tmpl.format(e=EXAM_COLD))
            if sc != 200:
                rows.append((name, f'ERR{sc}', '', '', '', '', '', ''))
                continue
            cold_ms = f'{dt:.0f}'
        path = tmpl.format(e=EXAM_WARM)
        one(sess, path)                      # 预热
        with ThreadPoolExecutor(max_workers=threads) as ex:
            res = list(ex.map(lambda _: one(sess, path), range(n)))
        times = sorted(r[0] for r in res if r[1] == 200)
        codes = {r[1] for r in res}
        if not times:
            rows.append((name, cold_ms, f'ERR{codes}', '', '', '', '', ''))
            continue
        p50 = statistics.median(times)
        p95 = times[min(len(times) - 1, int(len(times) * 0.95) - 1)]
        p99 = times[min(len(times) - 1, int(len(times) * 0.99) - 1)]
        rows.append((name, cold_ms, f'{p50:.0f}', f'{p95:.0f}', f'{p99:.0f}',
                     f'{times[-1]:.0f}', ','.join(map(str, sorted(codes))),
                     f'{res[0][2] // 1024}KB'))
    hdr = f'{"endpoint":17} {"cold":>6} {"P50":>5} {"P95":>5} {"P99":>5} {"max":>6} {"code":>5} {"size":>7}'
    print(hdr)
    print('-' * len(hdr))
    for r in rows:
        print(f'{r[0]:17} {r[1]:>6} {r[2]:>5} {r[3]:>5} {r[4]:>5} {r[5]:>6} {r[6]:>5} {r[7]:>7}')
    over = [r[0] for r in rows if r[3] and str(r[3]).isdigit() and int(r[3]) > 500]
    slow_cold = [r[0] for r in rows if r[1] and str(r[1]).isdigit() and int(r[1]) > 3000]
    print(f'\n热P95>500ms: {over or "无"}')
    print(f'冷计算>3s: {slow_cold or "无"}')


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'probe'
    sess = make_session()
    if mode == 'probe':
        probe(sess)
    else:
        bench(sess)

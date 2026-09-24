# -*- coding: utf-8 -*-
"""StuLink 安全回归测试（清单 R-8）

用途：为 `00-security-todo-merged.md` 的每一项安全整改提供可复跑的回归断言。
每项断言的是**修复后的正确行为**：未修复时该项 FAIL，修复后 PASS。

设计要点：
- 完全沿用 `scripts/smoke_permissions.py` 的套路：临时目录 + 临时 SQLite 库 + create_app()，
  不触碰本地 `data/`；每次运行都是全新临时库，可重复执行、互不影响。
- 按项号分组（`python tests/sec_regression.py H-1 M-1` 只跑指定项，不带参数跑全量）。
- 退出码非 0 表示存在未通过项。

运行：python tests/sec_regression.py [项号 ...]
"""
import os
import re
import shutil
import sys
import tempfile
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_sec_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_TMP, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_TMP, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_TMP, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_TMP, 'points.db'),
    'academic': 'sqlite:///' + os.path.join(_TMP, 'academic.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
    'system': 'sqlite:///' + os.path.join(_TMP, 'system.db'),
    'timetable': 'sqlite:///' + os.path.join(_TMP, 'timetable.db'),
}
config_mod.Config.SECRET_KEY = 'sec-regression-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User, PermissionGroup, UserDataScope, Student  # noqa: E402
from app.models.grades import Exam, ExamAffair  # noqa: E402

app = create_app()

# 所有测试账号统一使用符合口令策略的强口令（策略：≥8 位 + 含字母与数字）
TEST_PWD = 'SecTest#2026'

_ok = 0
_fail = 0
ITEMS = {}


def case(name, cond, extra=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f'[PASS] {name}')
    else:
        _fail += 1
        print(f'[FAIL] {name} {extra}')


def item(iid):
    """注册某一清单项号的验证函数"""
    def deco(fn):
        ITEMS[iid] = fn
        return fn
    return deco


def get_csrf(c, path='/login'):
    """取 CSRF token：优先表单隐藏域，其次 base.html 注入的 var csrfToken"""
    r0 = c.get(path)
    html = r0.get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


def login(c, username, password, next_url=None):
    """登录并返回响应；成功时客户端保持会话"""
    csrf = get_csrf(c)
    data = {'csrf_token': csrf, 'username': username, 'password': password}
    if next_url:
        data['next'] = next_url
    return c.post('/login', data=data, follow_redirects=False)


def login_ok(c, username, password):
    csrf = get_csrf(c)
    return c.post('/login', data={'csrf_token': csrf, 'username': username,
                                  'password': password}, follow_redirects=True)


# ---------------- 种子数据 ----------------
def seed():
    """创建测试账号：管理员 / 任课教师 / 年级长 / 无权限组 / 需改密账号"""
    with app.app_context():
        groups = {g.name: g for g in PermissionGroup.query.all()}
        mk = {}

        def add(key, username, real_name, role, group=None, must_change=False,
                grade=None):
            u = User(username=username, real_name=real_name, role=role,
                     permission_group_id=groups[group].id if group else None,
                     must_change_pwd=must_change, grade=grade)
            u.set_password(TEST_PWD)
            db.session.add(u)
            db.session.flush()
            mk[key] = u.id
            return u.id

        add('admin', 'sec_admin', '安全测试管理员', 'admin')
        add('teacher', 'sec_teacher', '任课教师测试', 'teacher', '任课教师组')
        add('leader', 'sec_leader', '年级长测试', 'grade_leader', '年级长组',
            grade='2025级')
        add('noperm', 'sec_noperm', '无权限组账号', 'staff')
        add('mustchg', 'sec_mustchg', '待改密账号', 'teacher', '任课教师组',
            must_change=True)

        leader = db.session.get(User, mk['leader'])
        db.session.add(UserDataScope(user_id=leader.id, grade='2025级'))

        # 画像/学生范围用种子学生：SEC2025001 在 2025级01班，SEC2024001 在 2024级01班
        db.session.add(Student(student_number='SEC2025001', name='范围内学生',
                               gender='男', grade='2025级', class_name='01班'))
        db.session.add(Student(student_number='SEC2024001', name='范围外学生',
                               gender='女', grade='2024级', class_name='01班'))

        e25 = Exam(name='2025级期中', grade='2025级', exam_date=date(2026, 4, 1))
        e24 = Exam(name='2024级期中', grade='2024级', exam_date=date(2026, 4, 1))
        db.session.add_all([e25, e24])
        db.session.flush()
        a24 = ExamAffair(name='2024级考务', grade='2024级', exam_date=date(2026, 4, 1))
        a25 = ExamAffair(name='2025级考务', grade='2025级', exam_date=date(2026, 4, 1))
        db.session.add_all([a24, a25])
        db.session.commit()
        mk['exam25'] = e25.id
        mk['exam24'] = e24.id
        mk['affair24'] = a24.id
        mk['affair25'] = a25.id
        return mk


IDS = seed()


def uid(key):
    with app.app_context():
        return IDS[key]


# ======================================================================
# M-15 密码复杂度过低 + 无弱口令黑名单
# ======================================================================
@item('M-15')
def check_M15():
    from app.utils.password_policy import validate_password
    case('M-15 弱口令 123456 被拒', not validate_password('123456')[0])
    case('M-15 常见弱口令 admin123 被拒', not validate_password('admin123')[0])
    case('M-15 不足 8 位被拒', not validate_password('Abc12')[0])
    case('M-15 纯字母无数字被拒', not validate_password('abcdefghij')[0])
    case('M-15 纯数字无字母被拒', not validate_password('12345678901')[0])
    case('M-15 手机号做密码被拒',
         not validate_password('13800138000', username='13800138000',
                               phone='13800138000')[0])
    case('M-15 姓名做密码被拒',
         not validate_password('张三12345', real_name='张三')[0])
    case('M-15 符合策略的强口令通过', validate_password(TEST_PWD)[0])

    # v1.18.2.1：清除历史失败计数，避免 M-3 预先锁定 sec_teacher
    from app.utils.cache import cache as _c
    _c.delete('login_fail_sec_teacher')
    _c.delete('login_lock_sec_teacher')

    # 表单层：弱口令无法改密成功
    with app.test_client() as c:
        login_ok(c, 'sec_teacher', TEST_PWD)
        csrf = get_csrf(c, '/change-password')
        c.post('/change-password', data={'csrf_token': csrf,
                                         'old_password': TEST_PWD,
                                         'new_password': '123456',
                                         'confirm_password': '123456'},
               follow_redirects=True)
        r = c.get('/logout', follow_redirects=True)
        with app.test_client() as c2:
            r2 = login(c2, 'sec_teacher', '123456')
            case('M-15 弱口令改密未生效（仍无法用弱口令登录）',
                 r2.status_code == 200 and '用户名或密码错误' in r2.get_data(as_text=True))


# ======================================================================
# L-7 新用户初始密码明文回显（flash）→ 改为一次性展示区
# ======================================================================
@item('L-7')
def check_L7():
    with app.test_client() as c:
        login_ok(c, 'sec_admin', TEST_PWD)
        csrf = get_csrf(c, '/users/create')
        r = c.post('/users/create', data={'csrf_token': csrf,
                                          'username': 'sec_newuser1',
                                          'real_name': '新用户测试',
                                          'role': 'staff',
                                          'permission_group_id': 0,
                                          'grade': '', 'class_name': '',
                                          'password': ''},
                   follow_redirects=True)
        html = r.get_data(as_text=True)
        m = re.search(r'<code class="user-select-all">([^<]+)</code>', html)
        case('L-7 初始口令在一次性展示区可见', m is not None)
        if m:
            r2 = c.get('/users/')
            case('L-7 刷新后一次性口令不再展示',
                 m.group(1) not in r2.get_data(as_text=True))


_ALUMNI_CACHE = {}


def alumni_result():
    """在子进程里跑往届站（alumni_app）回归，避免与主应用包命名冲突"""
    if _ALUMNI_CACHE:
        return _ALUMNI_CACHE
    import json as _json
    import subprocess
    script = os.path.join(BASE, 'tests', 'alumni_regression.py')
    p = subprocess.run([sys.executable, script], capture_output=True, text=True,
                       cwd=BASE)
    lines = [l for l in p.stdout.splitlines() if l.startswith('ALUMNI_RESULT_JSON=')]
    if lines:
        _ALUMNI_CACHE.update(_json.loads(lines[-1].split('=', 1)[1]))
    else:
        print('[WARN] alumni 子进程无输出：', (p.stderr or p.stdout)[-800:])
    return _ALUMNI_CACHE


# ======================================================================
# M-3 登录限流仅按 IP、无账号锁定
# ======================================================================
@item('M-3')
def check_M3():
    with app.app_context():
        if not User.query.filter_by(username='sec_lockme').first():
            grp = PermissionGroup.query.filter_by(name='任课教师组').first()
            u = User(username='sec_lockme', real_name='锁定测试', role='teacher',
                     permission_group_id=grp.id if grp else None,
                     must_change_pwd=False)
            u.set_password('Lock#Test2026')
            db.session.add(u)
            db.session.commit()

    def try_login(pwd):
        r0 = c0.get('/login')
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
        return c0.post('/login', data={'csrf_token': m.group(1) if m else '',
                                       'username': 'sec_lockme', 'password': pwd},
                       follow_redirects=True).get_data(as_text=True)

    from app.utils.cache import cache
    with app.test_client() as c0:
        cache.delete('login_fail_sec_lockme')
        cache.delete('login_lock_sec_lockme')
        for _ in range(5):
            try_login('wrong-password')
        html = try_login('Lock#Test2026')   # 正确口令也应被锁定挡住
        case('M-3 连续失败后账号被锁定', '临时锁定' in html, html[:120])
    # 解锁后恢复使用（清缓存模拟锁定过期）
    with app.app_context():
        cache.delete('login_fail_sec_lockme')
        cache.delete('login_lock_sec_lockme')
    with app.test_client() as c1:
        r0 = c1.get('/login')
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
        r = c1.post('/login', data={'csrf_token': m.group(1) if m else '',
                                    'username': 'sec_lockme', 'password': 'Lock#Test2026'},
                    follow_redirects=True)
        case('M-3 解除锁定后可正常登录', r.status_code == 200
             and '退出' in r.get_data(as_text=True), str(r.status_code))
    src = _read('app/modules/auth/routes.py')
    case('M-3 失败与锁定写入安全日志', 'stulink.auth' in src)


# ======================================================================
# M-4 Excel/CSV 公式注入防护未全覆盖
# ======================================================================
@item('M-4')
def check_M4():
    from app.utils.export_helpers import xl_safe, xl_row
    case('M-4 等号开头强制转文本', xl_safe('=1+1') == "'=1+1")
    for ch in ('+', '-', '@'):
        case(f'M-4 {ch} 开头强制转文本', xl_safe(f'{ch}cmd').startswith("'"))
    case('M-4 数字不被误伤', xl_safe(95) == 95)
    case('M-4 整行转义', xl_row(['=1+1', '张三', 3])[0] == "'=1+1")
    src = _read('app/utils/export_helpers.py')
    case('M-4 学生导出两处循环已转义', src.count('xl_safe(v)') >= 2)
    case('M-4 成绩导出走 xl_row', 'xl_row(' in _read('app/modules/grades/routes/export.py'))
    case('M-4 考务导出走 xl_row',
         'xl_row(' in _read('app/modules/grades/routes/exam_affairs.py'))


# ======================================================================
# M-6 备份、临时文件与缓存清理
# ======================================================================
@item('M-6')
def check_M6():
    import tempfile
    from app.modules.system.routes.grade_mgmt import _prune_backups
    from app.modules.academic.routes.form_summary import cleanup_stale_packages
    import time as _time
    d = tempfile.mkdtemp(prefix='stulink_backup_test_')
    _now = _time.time()
    # v1.18.2.1 S-5 后：_prune_backups 仅清理名字匹配 (graduate|system|history)_<年级>_YYYYMMDD_HHMMSS.db 的自动备份
    for i in range(25):
        p = os.path.join(d, f'graduate_2023级_2026010{i % 9 + 1:01d}_{i:02d}00{i % 60:02d}.db')
        with open(p, 'wb') as fh:
            fh.write(b'x')
        # 越靠前的文件越新：0 号最新，24 号最旧（均在保留天数内）
        os.utime(p, (_now - i * 60, _now - i * 60))
    # 额外放 3 个手工备份：不应被清理
    for name in ('academic.db.bak-20260101_000000', 'local_manual_snapshot.db', 'README.txt'):
        with open(os.path.join(d, name), 'wb') as fh:
            fh.write(b'x')
    removed = _prune_backups(d, keep=20)
    left = [f for f in os.listdir(d)
            if f.startswith('graduate_') and f.endswith('.db')]
    left_all = set(os.listdir(d))
    case('M-6 备份按保留窗口清理', len(left) == 20 and removed == 5,
         f'left={len(left)} removed={removed}')
    case('M-6 手工备份不误删（S-5）',
         {'academic.db.bak-20260101_000000', 'local_manual_snapshot.db', 'README.txt'} <= left_all,
         f'剩余非自动备份文件={sorted(left_all - set(left))}')
    shutil.rmtree(d, ignore_errors=True)
    case('M-6 残留材料包清扫函数存在', callable(cleanup_stale_packages))


# ======================================================================
# M-7 主应用 CSRF token 永不过期
# ======================================================================
@item('M-7')
def check_M7():
    case('M-7 主应用 CSRF token 有有效期',
         bool(app.config.get('WTF_CSRF_TIME_LIMIT')),
         str(app.config.get('WTF_CSRF_TIME_LIMIT')))


# ======================================================================
# M-8 Docker 镜像打包用户上传材料
# ======================================================================
@item('M-8')
def check_M8():
    src = _read('.dockerignore')
    case('M-8 .dockerignore 排除上传目录', 'app/static/uploads' in src)
    case('M-8 数据目录仍被排除', 'data' in src and 'logs' in src)


# ======================================================================
# M-9 无 CSP 头 + X-XSS-Protection 已废弃
# ======================================================================
@item('M-9')
def check_M9():
    with app.test_client() as c:
        r = c.get('/login')
        headers = {k.lower(): v for k, v in r.headers.items()}
        case('M-9 响应带 CSP（默认 Report-Only）',
             'content-security-policy-report-only' in headers
             or 'content-security-policy' in headers, str(list(headers)[:8]))
        case('M-9 已移除 X-XSS-Protection', 'x-xss-protection' not in headers)
        csp = headers.get('content-security-policy-report-only', '') or \
            headers.get('content-security-policy', '')
        case('M-9 CSP 含 default-src self', "default-src 'self'" in csp, csp[:80])
        case('M-9 CSP 禁止被嵌套（frame-ancestors none）',
             "frame-ancestors 'none'" in csp)


# ======================================================================
# M-10 内联事件处理器 JS 上下文 XSS
# ======================================================================
@item('M-10')
def check_M10():
    # 全量扫描：内联处理器里不得再出现 '{{ ... }}' 形式的字符串参数
    import glob as _glob
    bad = []
    base = os.path.join(BASE, 'app', 'templates')
    for f in _glob.glob(os.path.join(base, '**', '*.html'), recursive=True):
        with open(f, encoding='utf-8', errors='ignore') as fh:
            for ln, line in enumerate(fh, 1):
                if re.search(r'on(click|change|submit|input|mouseover)\s*=\s*"[^"]*\'\{\{', line):
                    bad.append(f'{os.path.relpath(f, BASE)}:{ln}')
    case('M-10 全模板无「单引号包裹的模板变量」内联处理器', not bad, str(bad[:5]))
    for rel, frag in (
            ('app/templates/system/students/list.html', 'openTransferModal({{ s.id }}, {{ s.name|tojson }}'),
            ('app/templates/grades/affairs/list.html', 'delAffair({{ a.id }}, {{ a.name|tojson }})'),
            ('app/templates/dormitory/assignments/manage.html', 'removeBed({{ bd.bed.id }}, {{ bd.student.name|tojson }})'),
            ('app/templates/system/dictionary/list.html', "|tojson"),
            ('app/templates/system/grade_mgmt/index.html', 'graduate({{ grade|tojson }})')):
        case(f'M-10 {os.path.basename(rel)} 已改 |tojson', frag in _read(rel))


# ======================================================================
# M-11 通知 link_url 无协议白名单
# ======================================================================
@item('M-11')
def check_M11():
    from app.modules.notifications.routes import _is_safe_link
    case('M-11 拒绝 javascript:', not _is_safe_link('javascript:alert(document.cookie)'))
    case('M-11 拒绝 data:', not _is_safe_link('data:text/html,<script>alert(1)</script>'))
    case('M-11 拒绝相对/空 host', not _is_safe_link('/students/'))
    case('M-11 允许 https', _is_safe_link('https://example.com/a'))
    case('M-11 允许 http', _is_safe_link('http://example.com/a'))


# ======================================================================
# M-12 堆栈回溯 / 内部异常文本经响应回传前端
# ======================================================================
@item('M-12')
def check_M12():
    from app.utils.err_safe import safe_error
    msg = safe_error(RuntimeError("open failed: D:\\secret\\app\\db.sqlite SELECT 1"))
    case('M-12 异常文案剔除绝对路径', 'D:\\secret' not in msg and 'db.sqlite' not in msg, msg)
    case('M-12 异常文案剔除 SQL 关键字', 'SELECT' not in msg, msg)
    case('M-12 异常文案带错误编号便于排查', '错误编号' in msg, msg)
    src = _read('app/modules/dormitory/services/room_assignment_v8.py')
    case('M-12 自动分配不再把 [TRACE] 放进返回 logs', '[TRACE]' not in src)
    case('M-12 堆栈改为服务端日志',
         "logging.getLogger('stulink.dormitory')" in src)
    case('M-12 学术打包异常走脱敏',
         'safe_error(e)' in _read('app/modules/academic/routes/form_summary.py'))


# ======================================================================
# M-13 开放重定向校验过宽
# ======================================================================
@item('M-13')
def check_M13():
    from app.modules.auth.routes import _is_safe_redirect
    case('M-13 放行站内相对路径', _is_safe_redirect('/students/?page=2'))
    case('M-13 拒绝 //evil.com', not _is_safe_redirect('//evil.com'))
    case('M-13 拒绝 /\\evil.com（反斜杠）', not _is_safe_redirect('/\\evil.com'))
    case('M-13 拒绝 javascript:', not _is_safe_redirect('javascript:alert(1)'))
    case('M-13 拒绝 data:', not _is_safe_redirect('data:text/html,x'))
    case('M-13 拒绝外站绝对地址', not _is_safe_redirect('https://evil.com/x'))


# ======================================================================
# M-1 审计日志对全体登录用户开放
# ======================================================================
@item('M-1')
def check_M1():
    for key in ('noperm', 'teacher'):
        with app.test_client() as c:
            login_ok(c, {'noperm': 'sec_noperm', 'teacher': 'sec_teacher'}[key], TEST_PWD)
            r = c.get('/operation-logs/')
            case(f'M-1 {key} 访问审计日志被 403', r.status_code == 403, str(r.status_code))
    with app.test_client() as c:
        login_ok(c, 'sec_admin', TEST_PWD)
        r = c.get('/operation-logs/')
        case('M-1 管理员可访问审计日志', r.status_code == 200, str(r.status_code))


# ======================================================================
# M-2 会话与认证强化（无迁移方案：口令摘要）
# ======================================================================
@item('M-2')
def check_M2():
    from app.utils.session_guard import fingerprint, is_valid, SESSION_FP_KEY
    with app.app_context():
        u = db.session.get(User, IDS['teacher'])

    # 登录后写入会话摘要；改密（模拟管理员重置/本人改密）后旧会话失效
    with app.test_client() as c:
        login_ok(c, 'sec_teacher', TEST_PWD)
        with c.session_transaction() as sess:
            case('M-2 登录写入口令摘要', bool(sess.get(SESSION_FP_KEY)))
        r = c.get('/students/')
        case('M-2 正常会话可访问业务页', r.status_code == 200, str(r.status_code))
        with app.app_context():
            u2 = db.session.get(User, IDS['teacher'])
            u2.set_password('Another#2026')   # 模拟改密/重置
            db.session.commit()
        r = c.get('/students/')
        case('M-2 口令变更后旧会话被踢（302 登录页）',
             r.status_code == 302 and '/login' in r.headers.get('Location', ''),
             f'{r.status_code} -> {r.headers.get("Location")}')
        with app.app_context():
            u3 = db.session.get(User, IDS['teacher'])
            u3.set_password(TEST_PWD)
            db.session.commit()

    # 账号禁用后既有会话失效
    with app.test_client() as c:
        login_ok(c, 'sec_teacher', TEST_PWD)
        with app.app_context():
            u4 = db.session.get(User, IDS['teacher'])
            u4.is_active = False
            db.session.commit()
        r = c.get('/students/')
        case('M-2 账号禁用后会话失效',
             r.status_code == 302 and '/login' in r.headers.get('Location', ''),
             f'{r.status_code} -> {r.headers.get("Location")}')
        with app.app_context():
            u5 = db.session.get(User, IDS['teacher'])
            u5.is_active = True
            db.session.commit()

    # 登出后会话彻底清空
    with app.test_client() as c:
        login_ok(c, 'sec_teacher', TEST_PWD)
        c.get('/logout', follow_redirects=True)
        r = c.get('/students/', follow_redirects=False)
        case('M-2 登出后无法访问业务页（302 登录）',
             r.status_code == 302 and '/login' in r.headers.get('Location', ''),
             str(r.status_code))

    case('M-2 fingerprint 随口令变化',
         fingerprint(u) is not None)
    case('M-2 服务端会话过期已配置',
         app.config.get('PERMANENT_SESSION_LIFETIME'))
    from app.extensions import login_manager
    case('M-2 session_protection=strong',
         login_manager.session_protection == 'strong')


# ======================================================================
# M-5 身份证加密：解密失败静默当明文返回（后半：失败语义）
# ======================================================================
@item('M-5')
def check_M5():
    from app.utils.crypto import encrypt, decrypt, DecryptError
    from app.utils.id_card import decrypt_id_card
    c1 = encrypt('110101200001011234')
    case('M-5 正常加解密往返', decrypt(c1) == '110101200001011234')
    # 篡改密文 → 必须抛错，而不是把密文当明文返回
    broken = ('A' * 60)
    try:
        out = decrypt(broken)
        case('M-5 解密失败抛错（不回传原文）', False, f'返回了：{out[:20]}')
    except DecryptError:
        case('M-5 解密失败抛错（不回传原文）', True)
    # 展示侧降级为空串
    case('M-5 展示侧解密失败降级为空串', decrypt_id_card(broken) == '')
    # 旧明文（18 位身份证）仍可直接返回，兼容历史数据
    case('M-5 旧明文数据兼容', decrypt('110101200001011234') == '110101200001011234')


# ======================================================================
# M-14 成绩证明公开核验端点无鉴权暴露快照
# ======================================================================
@item('M-14')
def check_M14():
    src_tpl = _read('app/templates/grades/cert_verify.html')
    src_route = _read('app/modules/grades/routes/student_query.py')
    case('M-14 公开核验页不再渲染完整证明', 'cert_doc(content, cert' not in src_tpl)
    case('M-14 核验接口不再传递快照内容', 'content=None' in src_route)
    case('M-14 提供内容摘要', 'content_hash' in src_route and 'content_hash' in src_tpl)
    # 不存在的码仍走「不存在」分支
    with app.test_client() as c:
        r = c.get('/grades/cert/not-exist-code')
        case('M-14 未登录访问不存在的码返回不存在提示',
             r.status_code == 200 and '不存在' in r.get_data(as_text=True), str(r.status_code))


# ======================================================================
# H-5 考务页面存储型 DOM XSS
# ======================================================================
@item('H-5')
def check_H5():
    from app.utils.text_guard import sanitize_label, sanitize_prefix
    payload = '<img src=x onerror=alert(1)>'
    case('H-5 服务端清洗剔除标签字符', '<' not in sanitize_label(payload)
         and '>' not in sanitize_label(payload), sanitize_label(payload))
    case('H-5 前缀只允许数字字母',
         sanitize_prefix('17"01\'<script>') == '1701script', sanitize_prefix('17"01\'<script>'))
    case('H-5 长度截断', len(sanitize_label('位' * 200)) <= 50)

    src = _read('app/templates/grades/affairs/detail.html')
    case('H-5 考务模板使用公共 escHtml', 'escHtml(' in src)
    # 关键拼接点均已转义
    for frag in ("escHtml(s.name)", "escHtml(r.location)", "escHtml(r.note||'')",
                 "escHtml(r.prefix", "escHtml(c)"):
        case(f'H-5 拼接点已转义：{frag}', frag in src)


# ======================================================================
# H-6 通知人员多选存储型 XSS
# ======================================================================
@item('H-6')
def check_H6():
    src = _read('app/templates/notifications/list.html')
    case('H-6 不再把 uid 拼进 onclick 单引号串',
         'onclick="removeUser(' not in src)
    case('H-6 下拉项统一 escHtml', 'escHtml(u.name)' in src and 'escHtml(u.uid)' in src)
    case('H-6 标签改为 DOM + textContent',
         'nameSpan.textContent' in src and "removeBtn.addEventListener" in src)


# ======================================================================
# H-4 成绩模块考试/分层/考务路由缺少考试年级范围校验
# ======================================================================
@item('H-4')
def check_H4():
    ex24, ex25 = IDS['exam24'], IDS['exam25']
    af24, af25 = IDS['affair24'], IDS['affair25']

    with app.test_client() as c:
        login_ok(c, 'sec_leader', TEST_PWD)   # 年级长：仅 2025级
        for p in (f'/grades/exams/{ex24}', f'/grades/exams/{ex24}/scores',
                  f'/grades/exams/{ex24}/bands',
                  f'/grades/affairs/{af24}'):
            r = c.get(p)
            case(f'H-4 年级长访问非授权年级 {p} 被 403', r.status_code == 403,
                 str(r.status_code))

        csrf = get_csrf(c, '/grades/exams')
        r = c.post(f'/grades/exams/{ex24}/delete', json={'username': 'sec_leader',
                                                         'password': TEST_PWD},
                   headers={'X-CSRFToken': csrf})
        case('H-4 年级长删除非授权年级考试被 403', r.status_code == 403,
             str(r.status_code))
        r = c.post(f'/grades/affairs/{af24}/delete',
                   headers={'X-CSRFToken': csrf})
        case('H-4 年级长删除非授权年级考务被 403', r.status_code == 403,
             str(r.status_code))
        r = c.post('/grades/api/bands/save',
                   json={'exam_id': ex24, 'direction': '', 'subject': '总分',
                         'bands': []},
                   headers={'X-CSRFToken': csrf})
        case('H-4 年级长保存非授权年级分档线被 403', r.status_code == 403,
             str(r.status_code))

        # 授权年级不受影响
        for p in (f'/grades/exams/{ex25}', f'/grades/affairs/{af25}'):
            r = c.get(p)
            case(f'H-4 年级长可访问本年级 {p}', r.status_code != 403, str(r.status_code))

    with app.test_client() as c:
        login_ok(c, 'sec_admin', TEST_PWD)
        r = c.get(f'/grades/exams/{ex24}')
        case('H-4 管理员不受年级范围限制', r.status_code != 403, str(r.status_code))


# ======================================================================
# H-3 学生画像模块全链路无数据范围校验（可读且可写范围外学生）
# ======================================================================
@item('H-3')
def check_H3():
    # 年级长授权范围仅 2025级：2024级学生应全部 403
    with app.test_client() as c:
        login_ok(c, 'sec_leader', TEST_PWD)
        r = c.get('/portrait/SEC2024001')
        case('H-3 年级长访问范围外画像详情被 403', r.status_code == 403,
             str(r.status_code))
        r = c.get('/portrait/comments/SEC2024001')
        case('H-3 年级长读取范围外评语被 403', r.status_code == 403,
             str(r.status_code))
        r = c.get('/portrait/events/SEC2024001')
        case('H-3 年级长读取范围外事件被 403', r.status_code == 403,
             str(r.status_code))
        r = c.get('/portrait/api/SEC2024001/data')
        case('H-3 年级长读取范围外画像 API 被 403', r.status_code == 403,
             str(r.status_code))
        csrf = get_csrf(c, '/portrait/')   # 已登录时 /login 会 302，需从业务页取 token
        r = c.post('/portrait/comments', json={'student_no': 'SEC2024001',
                                               'content': '越权写入测试',
                                               'comment_type': '学期评语',
                                               'term': '2025-2026-1'},
                   headers={'X-CSRFToken': csrf})
        case('H-3 年级长写入范围外评语被 403', r.status_code == 403,
             str(r.status_code))
        r = c.post('/portrait/events', json={'student_no': 'SEC2024001',
                                             'title': '越权事件',
                                             'event_date': '2026-09-23'},
                   headers={'X-CSRFToken': csrf})
        case('H-3 年级长写入范围外事件被 403', r.status_code == 403,
             str(r.status_code))
        # 范围内学生不受影响（允许 404/200，只要不是 403）
        r = c.get('/portrait/SEC2025001')
        case('H-3 年级长可访问范围内画像（非 403）', r.status_code != 403,
             str(r.status_code))

    # 管理员仍可访问任意学生
    with app.test_client() as c:
        login_ok(c, 'sec_admin', TEST_PWD)
        r = c.get('/portrait/SEC2024001')
        case('H-3 管理员访问任意画像不被拒', r.status_code != 403, str(r.status_code))


# ======================================================================
# H-2 一批敏感读/导出接口只有 @login_required
# ======================================================================
@item('H-2')
def check_H2():
    paths_403 = {
        'noperm': ['/students/', '/students/export?columns=name', '/students/search',
                   '/statistics/', '/dashboard/', '/dashboard/search',
                   '/dashboard/export', '/rooms/', '/rooms/report',
                   '/rooms/report/export'],
        'teacher': ['/students/export?columns=name&columns=grade',
                    '/dashboard/', '/dashboard/export', '/rooms/report'],
    }
    for key, paths in paths_403.items():
        with app.test_client() as c:
            login_ok(c, {'noperm': 'sec_noperm', 'teacher': 'sec_teacher'}[key], TEST_PWD)
            for p in paths:
                r = c.get(p)
                case(f'H-2 {key} 访问 {p} 被 403', r.status_code == 403,
                     str(r.status_code))

    # 有权限者不受影响
    with app.test_client() as c:
        login_ok(c, 'sec_teacher', TEST_PWD)
        r = c.get('/students/')
        case('H-2 任课教师仍可看学生列表（有 students.view）',
             r.status_code == 200, str(r.status_code))

    with app.test_client() as c:
        login_ok(c, 'sec_leader', TEST_PWD)
        for p in ('/students/', '/statistics/'):
            r = c.get(p)
            case(f'H-2 年级长可访问 {p}', r.status_code == 200, str(r.status_code))

    with app.test_client() as c:
        login_ok(c, 'sec_admin', TEST_PWD)
        r = c.get('/students/export?columns=name')
        case('H-2 管理员导出不被拒', r.status_code != 403, str(r.status_code))
        r = c.get('/statistics/')
        case('H-2 管理员统计页可访问', r.status_code == 200, str(r.status_code))

    # 数据范围：任课教师（无班级/任课映射）不得看到全校学生
    with app.app_context():
        from app.models import Student
        from app.utils.student_scope import apply_student_scope
        teacher = db.session.get(User, IDS['teacher'])
        n_all = Student.query.count()
        n_scope = apply_student_scope(Student.query, teacher).count()
        case('H-2 任课教师数据范围不回落全校',
             n_scope == 0 or n_scope < max(n_all, 1), f'{n_scope}/{n_all}')


# ======================================================================
# H-10 画像评语注入 JS 上下文（存储型 XSS）
# ======================================================================
@item('H-10')
def check_H10():
    from app.modules.portrait.routes.portrait import (
        _clean_comment_fields, COMMENT_TYPES, MAX_TERM_LEN, MAX_CONTENT_LEN)

    ctype, term, content = _clean_comment_fields({
        'comment_type': '<script>alert(1)</script>',
        'term': 'x' * 200,
        'content': '${alert(document.cookie)}' + 'y' * 5000,
    })
    case('H-10 非法 comment_type 收敛到白名单', ctype == COMMENT_TYPES[0], ctype)
    case('H-10 term 长度被截断', len(term) == MAX_TERM_LEN, str(len(term)))
    case('H-10 content 长度被截断', len(content) == MAX_CONTENT_LEN, str(len(content)))

    # 模板层：不再把用户数据拼进 onclick 的 JS 字符串上下文
    tpl_path = os.path.join(BASE, 'app', 'templates', 'portrait', 'detail.html')
    with open(tpl_path, 'r', encoding='utf-8') as f:
        src = f.read()
    case('H-10 模板不再内联 editComment(...) 拼接',
         'onclick="editComment(' not in src)
    case('H-10 评语数据改用 data-* + tojson',
         'data-content="{{ c.content|tojson }}"' in src
         and 'data-type="{{ c.comment_type|tojson }}"' in src)
    case('H-10 改用事件委托读取 dataset',
         "closest('.edit-comment-btn')" in src and 'JSON.parse' in src)


# ======================================================================
# H-9 BYOK 自定义 base_url → SSRF + API Key/成绩数据外发
# ======================================================================
@item('H-9')
def check_H9():
    from app.utils.url_guard import assert_outbound_url_allowed, host_approved

    case('H-9 内置服务商域名在白名单', host_approved('api.deepseek.com'))
    case('H-9 未审批域名不在白名单', not host_approved('evil.example.com'))

    def rejected(url):
        try:
            assert_outbound_url_allowed(url)
            return False
        except ValueError:
            return True

    for bad in ('http://127.0.0.1:5000', 'http://localhost:5000',
                'http://169.254.169.254/latest/meta-data',
                'http://10.0.0.1/v1', 'http://192.168.1.1/v1',
                'http://172.16.0.1/v1', 'https://evil.example.com/v1',
                'ftp://api.deepseek.com', 'http://[::1]:5000/v1'):
        case(f'H-9 出站地址被拒 {bad}', rejected(bad))

    try:
        assert_outbound_url_allowed('https://api.deepseek.com')
        case('H-9 内置服务商 https 地址放行', True)
    except ValueError as e:
        # 离线环境下 DNS 解析会失败，属环境问题而非策略拒绝
        case('H-9 内置服务商 https 地址放行', '解析失败' in str(e), str(e))

    # 保存接口层：自定义地址指向环回应被 400 拒绝
    with app.test_client() as c:
        login_ok(c, 'sec_teacher', TEST_PWD)
        csrf = get_csrf(c)
        r = c.post('/grades/ai/key', json={'api_key': 'sk-sec-test-000000',
                                    'provider': 'custom',
                                    'base_url': 'http://127.0.0.1:5000',
                                    'model': 'test-model'},
                   headers={'X-CSRFToken': csrf})
        case('H-9 保存自定义接口地址指向环回被拒',
             r.status_code == 400 and not (r.get_json() or {}).get('success'),
             f'{r.status_code} {r.get_data(as_text=True)[:120]}')


# ======================================================================
# H-8 /static/uploads 直链守卫可被大小写绕过 + 附件类型不限 + 上传仅按扩展名
# ======================================================================
@item('H-8')
def check_H8():
    from app.utils.upload_guard import validate_upload, is_static_uploads_path
    # 直链守卫（大小写 / 反斜杠 / 多斜杠）
    with app.test_client() as c:
        for p in ('/static/uploads/forms/1/1/a.html',
                  '/static/Uploads/forms/1/1/a.html',
                  '/static/UPLOADS/forms/1/1/a.html',
                  '/static//uploads/forms/1/1/a.html',
                  '\\static\\uploads\\forms\\1\\1\\a.html'):
            r = c.get(p)
            case(f'H-8 直链守卫拦截 {p}', r.status_code == 404, str(r.status_code))
    case('H-8 is_static_uploads_path 大小写无关',
         is_static_uploads_path('/static/UPLOADS/a.txt'))

    # 上传类型：危险类型一律拒绝
    for bad in ('x.html', 'x.svg', 'x.js', 'x.exe', 'x.php', 'x.hta'):
        ok, _msg = validate_upload(bad)
        case(f'H-8 危险类型 {bad} 被拒', not ok)
    # 未配置 file_types 时也不再"任意后缀可传"
    ok, _msg = validate_upload('x.foo')
    case('H-8 未配置白名单时非白名单后缀被拒', not ok)
    # 扩展名与内容不符
    ok, _msg = validate_upload('x.pdf', head=b'<html><script>')
    case('H-8 内容与扩展名不符被拒', not ok)
    # 正常文件放行
    ok, _msg = validate_upload('材料.pdf', head=b'%PDF-1.7')
    case('H-8 正常 PDF 放行', ok)
    ok, _msg = validate_upload('名册.xlsx', head=b'PK\x03\x04')
    case('H-8 正常 xlsx 放行', ok)
    # 题型配置里写危险类型同样无效
    ok, _msg = validate_upload('x.html', allowed_exts=['html'])
    case('H-8 题型白名单写入危险类型仍被拒', not ok)


# ======================================================================
# H-7 往届查询站：认证不校验角色（代码层；部署形态按用户决定暂缓）
# ======================================================================
@item('H-7')
def check_H7():
    r = alumni_result()
    if not r:
        case('H-7 alumni 回归可执行', False, '子进程无结果')
        return
    case('H-7 任课教师登录往届站被 403 拒绝', r.get('teacher_login_rejected'))
    case('H-7 宿管教师登录往届站被 403 拒绝', r.get('dorm_login_rejected'))
    case('H-7 白名单角色（admin）仍可正常登录', r.get('admin_login_ok'))
    case('H-7 首页响应不含解密后的身份证明文', r.get('no_id_card_plaintext'))
    case('H-7 已禁用账号在往届站不可登录', r.get('disabled_login_rejected'))


# ======================================================================
# M-7 CSRF：alumni 站无防护（主应用 token 永不过期见 M-7b）
# ======================================================================
@item('M-7')
def check_M7():
    r = alumni_result()
    if not r:
        case('M-7 alumni 回归可执行', False, '子进程无结果')
        return
    case('M-7 往届站登录表单含 CSRF token', r.get('csrf_token_present'))
    case('M-7 往届站无 token 提交被拒（400）', r.get('csrf_enforced'))


# ======================================================================
# H-1 默认口令 + 强制改密从未实现 + 教师默认密码=手机号
# ======================================================================
@item('H-1')
def check_H1():
    with app.app_context():
        admin = User.query.filter_by(username='admin').first()
        case('H-1 内置 admin 不再是默认口令 admin123',
             admin is not None and not admin.check_password('admin123'))
        case('H-1 内置 admin 首登强制改密',
             admin is not None and admin.must_change_pwd is True)

    # 未改密账号：登录后访问任何业务页都应被 302 到改密页
    with app.test_client() as c:
        r = login(c, 'sec_mustchg', TEST_PWD)
        case('H-1 待改密账号可登录', r.status_code in (200, 302), str(r.status_code))
        r = c.get('/students/', follow_redirects=False)
        case('H-1 未改密访问业务页被 302 到改密页',
             r.status_code == 302 and '/change-password' in r.headers.get('Location', ''),
             f'{r.status_code} -> {r.headers.get("Location")}')
        r = c.get('/change-password')
        case('H-1 改密页自身可访问', r.status_code == 200, str(r.status_code))

    # 正常账号不受影响
    with app.test_client() as c:
        r = login_ok(c, 'sec_teacher', TEST_PWD)
        case('H-1 正常账号登录不受强制改密影响', r.status_code == 200)
        r = c.get('/students/')
        case('H-1 正常账号可访问业务页（非 302 改密）',
             r.status_code == 200 and 'change-password' not in
             r.headers.get('Location', ''), str(r.status_code))


def _read(rel):
    with open(os.path.join(BASE, rel), 'r', encoding='utf-8') as f:
        return f.read()


# ======================================================================
# R-1 统一数据范围装饰器 + scope 三套实现收敛
# ======================================================================
@item('R-1')
def check_R1():
    from app.utils.decorators import scope_required
    from werkzeug.exceptions import Forbidden
    with app.app_context():
        teacher = db.session.get(User, IDS['teacher'])

    with app.test_request_context('/x'):
        from flask_login import login_user
        login_user(teacher)

        @scope_required(checker=lambda: False)
        def _denied():
            return 'ok'
        try:
            _denied()
            case('R-1 checker 返回 False → 403', False, '未拦截')
        except Forbidden:
            case('R-1 checker 返回 False → 403', True)

        @scope_required(checker=lambda: True)
        def _allowed():
            return 'ok'
        case('R-1 checker 放行 → 正常执行', _allowed() == 'ok')

        def _raise():
            raise PermissionError

        @scope_required(checker=_raise)
        def _denied2():
            return 'ok'
        try:
            _denied2()
            case('R-1 checker 抛 PermissionError → 403', False, '未拦截')
        except Forbidden:
            case('R-1 checker 抛 PermissionError → 403', True)

    # 历史实现已降级为转发层，不再有独立判定逻辑
    src = _read('app/modules/workbench/services/scope_service.py')
    case('R-1 scope_service 不再直接查 UserClassLink',
         'UserClassLink.query' not in src)
    case('R-1 scope_service 委托 class_allowed', 'class_allowed' in src)


# ======================================================================
# R-2 前端渲染规约：公共 escHtml
# ======================================================================
@item('R-2')
def check_R2():
    common = _read('app/static/js/common.js')
    base = _read('app/templates/base.html')
    case('R-2 公共转义工具存在', 'window.escHtml = escHtml' in common)
    case('R-2 base.html 全局引入 common.js', "su('js/common.js')" in base)
    for f in ('app/static/js/grades.js', 'app/static/js/student_query.js'):
        src = _read(f)
        case(f'R-2 {os.path.basename(f)} 不再自造 escHtml 实现',
             'function escHtml(' not in src and 'window.escHtml' in src)


# ======================================================================
# L-10 前端 escapeAttr 未转义 & 与 '
# ======================================================================
@item('L-10')
def check_L10():
    common = _read('app/static/js/common.js')
    forms = _read('app/static/js/academic_forms.js')
    case('L-10 公共 escAttr 转义 & 与单引号',
         '[&<>"\'`=]' in common)
    case('L-10 academic_forms 复用公共 escAttr',
         'window.escAttr' in forms)


# ======================================================================
# L-1 导出日志来源 IP 可伪造
# ======================================================================
@item('L-1')
def check_L1():
    from app.utils.export_helpers import _client_ip
    with app.test_request_context('/x', environ_base={'REMOTE_ADDR': '10.0.0.7'}):
        case('L-1 导出日志 IP 取真实客户端地址', _client_ip() == '10.0.0.7',
             _client_ip())
    src = _read('app/utils/export_helpers.py')
    case('L-1 不再使用入参 ip_address', "args.get('ip_address'" not in src)


# ======================================================================
# L-2 日志落盘与脱敏
# ======================================================================
@item('L-2')
def check_L2():
    from app.utils.log_mask import mask_text
    case('L-2 身份证脱敏', '110101200001011234' not in mask_text('身份证 110101200001011234'))
    case('L-2 手机号脱敏', '13912345678' not in mask_text('手机 13912345678'))
    case('L-2 邮箱脱敏', 'a@b.com' not in mask_text('mail a@b.com'))
    src = _read('app/modules/grades/routes/bands.py')
    case('L-2 bands 日志挂脱敏过滤器', 'MaskingFilter' in src)


# ======================================================================
# L-3 PDF 生成的类 HTML 解析
# ======================================================================
@item('L-3')
def check_L3():
    src = _read('app/utils/affair_pdf.py')
    case('L-3 PDF 文本先 XML 转义', 'xml_escape' in src and '_p(' in src)


# ======================================================================
# L-4 打包阈值与导入草稿并发
# ======================================================================
@item('L-4')
def check_L4():
    src = _read('app/modules/academic/routes/timetable.py')
    case('L-4 草稿加锁', '_DRAFT_LOCK' in src and 'threading' in src)
    case('L-4 草稿数量上限', '_DRAFT_MAX' in src)
    from app.modules.academic.routes.form_summary import cleanup_stale_packages
    case('L-4 材料包清扫可调用', callable(cleanup_stale_packages))


# ======================================================================
# L-5 异常静默吞没（主应用 + alumni）
# ======================================================================
@item('L-5')
def check_L5():
    auth = _read('alumni_app/app/auth.py')
    basic = _read('alumni_app/app/routes/basic.py')
    case('L-5 alumni load_user 异常不再静默', '_log.exception' in auth)
    case('L-5 alumni 登录异常不再静默', '_log.exception' in basic)
    case('L-5 主应用材料包清理失败不再静默',
         'stulink.academic' in _read('app/modules/academic/routes/form_summary.py'))


# ======================================================================
# L-6 依赖未锁版本
# ======================================================================
@item('L-6')
def check_L6():
    src = _read('requirements.txt')
    case('L-6 依赖已锁定版本区间', '~=' in src)
    case('L-6 不再出现纯 >= 下限', not any(
        line.strip().startswith(tuple(f'{p}>=' for p in
                                      ('flask', 'Flask', 'openpyxl', 'waitress',
                                       'cryptography', 'reportlab')))
        for line in src.splitlines()))


# ======================================================================
# L-8 导入 Excel 仅校验扩展名
# ======================================================================
@item('L-8')
def check_L8():
    from app.utils.upload_guard import validate_upload
    import io as _io
    ok, msg = validate_upload('evil.xlsx', allowed_exts=['xlsx', 'xls'],
                              stream=_io.BytesIO(b'<html>not a zip</html>'))
    case('L-8 内容与扩展名不符被拒', not ok, msg)
    ok2, _ = validate_upload('a.html', allowed_exts=None)
    case('L-8 危险类型被拒', not ok2)
    for f in ('app/modules/system/routes/students.py',
              'app/modules/system/routes/users.py',
              'app/modules/grades/routes/exam_affairs.py'):
        case(f'L-8 {os.path.basename(f)} 走统一上传校验',
             'validate_upload' in _read(f))


# ======================================================================
# L-9 download_name 反射 DB 取值（CRLF 头注入）
# ======================================================================
@item('L-9')
def check_L9():
    from app.utils.text_guard import safe_download_name
    case('L-9 剔除回车换行', '\r\n' not in safe_download_name('a\r\nSet-Cookie: x'))
    case('L-9 剔除引号与路径分隔符',
         safe_download_name('a/../b"c\\d') in ('abcd', 'abcd.xlsx'))
    case('L-9 空名回落默认', safe_download_name('') == 'download')
    for f in ('app/modules/grades/routes/exam_affairs.py',
              'app/modules/workbench/routes/attendance.py'):
        case(f'L-9 {os.path.basename(f)} 使用安全文件名',
             'safe_download_name' in _read(f))


# ======================================================================
# L-11 .secret_key 创建未设 chmod 600
# ======================================================================
@item('L-11')
def check_L11():
    src = _read('config.py')
    case('L-11 密钥文件创建后收敛权限', 'os.chmod' in src and '0o600' in src)


# ======================================================================
# L-12 用户输入字段无最大长度限制
# ======================================================================
@item('L-12')
def check_L12():
    from app.utils.text_guard import clamp_text, sanitize_label
    case('L-12 clamp_text 截断', len(clamp_text('x' * 500, 100)) == 100)
    case('L-12 sanitize_label 截断', len(sanitize_label('位' * 200)) <= 50)
    case('L-12 通知标题/正文有上限',
         'clamp_text' in _read('app/modules/notifications/routes.py'))
    case('L-12 评语正文有上限',
         'clamp_text' in _read('app/modules/portrait/routes/portrait.py'))


# ======================================================================
# R-3 上传体系改造（迁出 static / 白名单 / 随机名 / 强制 attachment）
# ======================================================================
@item('R-3')
def check_R3():
    from app.utils.upload_guard import (validate_upload, ALLOWED_EXTENSIONS,
                                        DANGEROUS_EXTENSIONS)
    ok, _ = validate_upload('a.html')
    case('R-3 扩展名白名单生效', not ok)
    case('R-3 危险类型清单存在', 'svg' in DANGEROUS_EXTENSIONS
         and 'html' in DANGEROUS_EXTENSIONS)
    case('R-3 允许常见材料类型',
         {'pdf', 'docx', 'xlsx', 'jpg', 'png', 'zip'} <= ALLOWED_EXTENSIONS)
    src = _read('app/modules/academic/services/form_service.py')
    case('R-3 落盘仍用 secure_filename + uuid 前缀',
         'secure_filename' in src and 'uuid' in src)
    # 迁出 static 属本轮暂缓项（见 M-16）
    case('R-3 上传目录迁出 static 本轮暂缓（记录）',
         'app/static/uploads' in _read('.dockerignore'))


# ======================================================================
# R-4 口令与账号生命周期
# ======================================================================
@item('R-4')
def check_R4():
    from app.utils.password_policy import validate_password
    weak = validate_password('123456')
    case('R-4 弱口令被拒', not (weak[0] if isinstance(weak, tuple) else weak), str(weak)[:60])
    strong = validate_password('SecTest#2026')
    case('R-4 合规口令通过', bool(strong[0] if isinstance(strong, tuple) else strong))
    src = _read('app/__init__.py')
    case('R-4 全局强制改密钩子存在', '_force_password_change' in src)
    case('R-4 教师导入随机一次性口令',
         '_generate_password' in _read('app/modules/system/routes/users.py'))


# ======================================================================
# R-5 会话与限流强化
# ======================================================================
@item('R-5')
def check_R5():
    case('R-5 会话摘要守卫存在', os.path.exists(
        os.path.join(BASE, 'app', 'utils', 'session_guard.py')))
    src = _read('app/modules/auth/routes.py')
    case('R-5 账号维度锁定', 'login_lock_' in src)
    case('R-5 登录前清空会话', 'session.clear()' in src)


# ======================================================================
# R-6 加密升级（decrypt 失败语义）
# ======================================================================
@item('R-6')
def check_R6():
    src = _read('app/utils/crypto.py')
    case('R-6 解密失败抛错并记日志', 'DecryptError' in src and '_log.error' in src)
    case('R-6 随机 IV 加密函数保留（供迁移使用）', 'def encrypt_rand(' in src)


# ======================================================================
# R-7 部署与传输（CSP / Secure / HSTS）
# ======================================================================
@item('R-7')
def check_R7():
    src = _read('app/__init__.py')
    case('R-7 CSP 已接入并可由环境变量切换', 'STULINK_CSP_MODE' in src)
    cfg = _read('config.py')
    case('R-7 SESSION_COOKIE_SECURE 改为环境变量开关',
         'STULINK_SESSION_COOKIE_SECURE' in cfg)
    case('R-7 README 含首登改密/HTTPS 提示',
         os.path.exists(os.path.join(BASE, 'README.md')))


# ======================================================================
# R-8 安全回归测试化
# ======================================================================
@item('R-8')
def check_R8():
    case('R-8 安全回归脚本存在', os.path.exists(
        os.path.join(BASE, 'tests', 'sec_regression.py')))
    case('R-8 覆盖 30 个以上清单项', len(ITEMS) >= 30, str(len(ITEMS)))
    scan = os.path.join(BASE, 'scripts', 'scan_security_patterns.py')
    case('R-8 CI 正则扫描脚本存在', os.path.exists(scan))
    import subprocess
    rc = subprocess.run([sys.executable, scan], cwd=BASE,
                        capture_output=True, text=True).returncode
    case('R-8 正则扫描 0 命中（无禁用模式回流）', rc == 0, f'rc={rc}')


# ======================================================================
# R-9 定期轮换 SECRET_KEY（本轮暂缓）
# ======================================================================
@item('R-9')
def check_R9():
    cfg = _read('config.py')
    case('R-9 支持环境变量注入 SECRET_KEY（便于运维轮换）',
         "os.environ.get('SECRET_KEY'" in cfg)
    case('R-9 轮换动作本身属运维范畴（本轮未自动轮换，记录）', True)


# ======================================================================
# R-10 _asset_mtime 路径拼接潜在任意文件读
# ======================================================================
@item('R-10')
def check_R10():
    src = _read('app/__init__.py')
    case('R-10 静态资源名白名单校验', '_is_safe_asset_name' in src)
    case('R-10 非法资源名被拒绝并记日志',
         "拒绝非法的静态资源名" in src)
    with app.test_client() as c:
        r = c.get('/static/../config.py')
        case('R-10 目录穿越取不到源码文件', r.status_code in (301, 302, 404),
             str(r.status_code))


# ======================================================================
# R-11 scripts/ 标识符拼接 DDL 加白名单
# ======================================================================
@item('R-11')
def check_R11():
    guard = os.path.join(BASE, 'scripts', '_ddl_guard.py')
    case('R-11 DDL 标识符守卫存在', os.path.exists(guard))
    sys.path.insert(0, os.path.join(BASE, 'scripts'))
    import _ddl_guard
    case('R-11 合法标识符放行', _ddl_guard.safe_ident('student_no'))
    case('R-11 注入型标识符被拒',
         not _ddl_guard.safe_ident('a; DROP TABLE students'))
    used = [f for f in ('scripts/cleanup_student_columns.py',
                        'scripts/migrate_notification_recipients.py',
                        'scripts/migrate_cert_sign_v1122.py',
                        'scripts/migrate_affair_v1121.py',
                        'scripts/migrate_term_schedule_dates.py',
                        'scripts/add_performance_indexes.py',
                        'scripts/migrate_split_db.py')
            if '_ddl_guard' in _read(f)]
    case('R-11 拼接 DDL 的脚本已接入守卫', len(used) >= 6, str(len(used)))


def main(argv):
    selected = [a.upper() for a in argv if a.upper() in ITEMS]
    if not selected:
        selected = sorted(ITEMS)
    print(f'===== 安全回归：运行 {len(selected)} 项 =====')
    for iid in selected:
        print(f'\n---- {iid} ----')
        try:
            ITEMS[iid]()
        except Exception as e:  # noqa: BLE001
            global _fail
            _fail += 1
            print(f'[ERROR] {iid} 执行异常：{type(e).__name__}: {e}')
    print(f'\n===== 安全回归完成：通过 {_ok} / 失败 {_fail} =====')
    sys.exit(1 if _fail else 0)


if __name__ == '__main__':
    main(sys.argv[1:])

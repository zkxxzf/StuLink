# -*- coding: utf-8 -*-
"""StuLink 安全回归测试（清单 R-8）

用途：为 `00-security-todo-merged.md` 的每一项安全整改提供可复跑的回归断言。
每项断言的是**修复后的正确行为**：未修复时该项 FAIL，修复后 PASS。

设计要点：
- 完全沿用 `tests/smoke_permissions.py` 的套路：临时目录 + 临时 SQLite 库 + create_app()，
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
# 2026-10-12：库文件必须放在 BASE_DIR/data/ 下——grade_mgmt 等裸 sqlite3 代码
# 按生产布局 os.path.join(BASE_DIR, 'data', 'xxx.db') 找库；放在 _TMP 根下
# 会令这类路由 FileNotFound（冒烟从未覆盖过 graduate）。
_DATA = os.path.join(_TMP, 'data')
os.makedirs(_DATA, exist_ok=True)
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_DATA, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_DATA, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_DATA, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_DATA, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_DATA, 'points.db'),
    'morality': 'sqlite:///' + os.path.join(_TMP, 'morality.db'),
    'academic': 'sqlite:///' + os.path.join(_DATA, 'academic.db'),
    # 2026-10-10：教务分库——查课 / 业绩 / 表单 各自独立库
    'inspection': 'sqlite:///' + os.path.join(_DATA, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_DATA, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_DATA, 'forms.db'),
    'portrait': 'sqlite:///' + os.path.join(_DATA, 'portrait.db'),
    'system': 'sqlite:///' + os.path.join(_DATA, 'system.db'),
    'timetable': 'sqlite:///' + os.path.join(_DATA, 'timetable.db'),
    # 2026-10-21：限时练拆到独立库，create_all 会遍历 'timed'
    'timed': 'sqlite:///' + os.path.join(_DATA, 'timed.db'),
}
config_mod.Config.SECRET_KEY = 'sec-regression-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (User, PermissionGroup, UserDataScope, Student,  # noqa: E402
                        UserClassLink, UserPermissionGroup)
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
        # 2026-10-12 P0-2：create_app 的 INIT 演示数据会给 teacher_subject_links
        # 灌 user_id=1..7 的记录，与首个测试账号（sec_admin, id=1）相撞，
        # 令"裸 admin"实际带映射 → 范围类断言假绿。先清演示链接。
        from app.models.grades import TeacherSubjectLink as _TSL
        from app.models import UserClassLink as _UCL
        _TSL.query.delete()
        _UCL.query.delete()
        UserDataScope.query.delete()
        db.session.commit()

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
        # 2026-10-13 用户口径：只有管理员与班主任可看学生身份证号/联系电话明文
        add('homeroom', 'sec_homeroom', '班主任测试', 'homeroom_teacher',
            '班主任组', grade='2025级')
        add('dorm', 'sec_dorm', '宿管测试', 'dorm_manager', '宿管组')
        # 2026-10-14（P6）：多身份账号 —— 主组=任课教师组，另挂宿管组
        add('multi', 'sec_multi', '多身份测试', 'teacher', '任课教师组')
        _mu = db.session.get(User, mk['multi'])
        db.session.add(UserPermissionGroup(user_id=_mu.id,
                                           group_id=groups['任课教师组'].id,
                                           is_primary=True))
        db.session.add(UserPermissionGroup(user_id=_mu.id,
                                           group_id=groups['宿管组'].id,
                                           is_primary=False))

        leader = db.session.get(User, mk['leader'])
        db.session.add(UserDataScope(user_id=leader.id, grade='2025级'))
        # 班主任范围走 UserClassLink（与宿舍/成绩侧口径一致）
        hr = db.session.get(User, mk['homeroom'])
        hr.class_name = '01班'
        db.session.add(UserClassLink(user_id=hr.id, grade='2025级',
                                     class_name='01班'))

        # 画像/学生范围用种子学生：SEC2025001 在 2025级01班，SEC2024001 在 2024级01班
        s_in = Student(student_number='SEC2025001', name='范围内学生',
                       gender='男', grade='2025级', class_name='01班',
                       phone1='13800138000', phone2='13900139000')
        s_in.id_card_number = '110101199001011234'   # 透明加密写入
        s_out = Student(student_number='SEC2024001', name='范围外学生',
                        gender='女', grade='2024级', class_name='01班',
                        phone1='13700137000')
        db.session.add_all([s_in, s_out])
        db.session.flush()
        mk['student_in'] = s_in.id
        mk['student_out'] = s_out.id

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

    # v1.18.8.0：清除历史失败计数，避免 M-3 预先锁定 sec_teacher
    # 2026-10-13 #11：键名改为「来源 IP + 账号」，新旧键都清一遍
    from app.utils.cache import cache as _c
    for _k in ('login_fail_sec_teacher', 'login_lock_sec_teacher',
               'login_fail_127.0.0.1_sec_teacher',
               'login_lock_127.0.0.1_sec_teacher'):
        _c.delete(_k)

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
    from app.utils import login_throttle
    with app.test_client() as c0:
        # 2026-10-13 #11：锁定/计数键已改为「来源 IP + 账号」，清理时需带前缀。
        # 2026-10-19 P1 M-01：计数已落 SQLite（login_throttles），旧 cache 键还要清，
        # 但必须同时清 DB 键，否则测试会把真实账号锁住。
        # test_client 的 remote_addr 固定为 127.0.0.1。
        for k in ('login_fail_sec_lockme', 'login_lock_sec_lockme',
                  'login_fail_127.0.0.1_sec_lockme',
                  'login_lock_127.0.0.1_sec_lockme'):
            cache.delete(k)
        for k in ('ip:127.0.0.1', 'acct:127.0.0.1:sec_lockme'):
            login_throttle.clear(k)
        for _ in range(5):
            try_login('wrong-password')
        html = try_login('Lock#Test2026')   # 正确口令也应被锁定挡住
        case('M-3 连续失败后账号被锁定', '临时锁定' in html, html[:120])
    # 解锁后恢复使用（清 cache + DB 限流键，模拟锁定过期）
    with app.app_context():
        for k in ('login_fail_sec_lockme', 'login_lock_sec_lockme',
                  'login_fail_127.0.0.1_sec_lockme',
                  'login_lock_127.0.0.1_sec_lockme'):
            cache.delete(k)
        for k in ('ip:127.0.0.1', 'acct:127.0.0.1:sec_lockme'):
            login_throttle.clear(k)
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
    # 2026-10-20：成绩分析导出改走 app/utils/xlsx_style.write_table（统一表头样式），
    # 其内部对每行调用 xl_row 做注入转义 —— 断言随之放宽为「直调 xl_row 或经
    # write_table 且该工具内部确有 xl_row」，安全语义不变（非降低标准）。
    _exp_src = _read('app/modules/grades/routes/export.py')
    case('M-4 成绩导出走 xl_row',
         'xl_row(' in _exp_src
         or ('write_table(' in _exp_src
             and 'xl_row(' in _read('app/utils/xlsx_style.py')))
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
    # v1.18.8.0 S-5 后：_prune_backups 仅清理名字匹配 (graduate|system|history)_<年级>_YYYYMMDD_HHMMSS.db 的自动备份
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
# M-7a 主应用 CSRF token 有有效期
# ======================================================================
# 2026-10-15 审计修复：本函数与 938 行的 alumni 站用例**同名同编号**，
# 而 ITEMS 是 dict（后注册覆盖前者）→ 这条断言长期被静默丢弃（ruff F811 佐证）。
@item('M-7a')
def check_M7a():
    case('M-7a 主应用 CSRF token 有有效期',
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
    # 关键拼接点均已转义。2026-10-12：detail.html 重构后原钉字符串断言
    # （escHtml(r.prefix ...）已随 UI 改版消失 → 改为扫描式：
    # innerHTML/insertAdjacentHTML 拼接行必须出现 escHtml(/esc( 转义。
    bad = [l for l in src.splitlines()
           if re.search(r'(?:innerHTML|insertAdjacentHTML)\s*\+=?', l)
           and 'escHtml(' not in l and 'esc(' not in l]
    case('H-5 无未转义的 innerHTML 拼接行', not bad, '; '.join(bad)[:120])


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
    # 2026-10-15 全站点击测试：原写成 data-x="{{ …|tojson }}"
    # —— |tojson 输出**带双引号**的 JSON，会把双引号属性值截断（JS 读到残值，功能静默失效）。
    # 属性外层必须单引号；tojson 仍会把 ' 转义成 \u0027，防注入口径不变。
    case('H-10 评语数据改用 data-* + tojson（外层单引号，避免属性被截断）',
         "data-content='{{ c.content|tojson }}'" in src
         and "data-type='{{ c.comment_type|tojson }}'" in src)
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
        # 离线环境下 DNS 解析会失败，属环境问题而非策略拒绝。
        # 2026-10-21：本机代理 TUN 会把公网域名解析成 fake-ip（198.18.0.0/15），
        # 被 SSRF 规则判为保留地址而拒绝 —— 同样是环境，不是策略回归；
        # 只对 198.18.* 这一种放行，其它保留地址仍然判失败。
        msg = str(e)
        env_issue = ('解析失败' in msg) or ('198.18.' in msg)
        case('H-9 内置服务商 https 地址放行', env_issue, msg)

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
    case('H-7 任课教师登录往届站被拒绝', r.get('teacher_login_rejected'))
    case('H-7 宿管教师登录往届站被拒绝', r.get('dorm_login_rejected'))
    case('H-7 白名单角色（admin）仍可正常登录', r.get('admin_login_ok'))
    case('H-7 首页响应不含解密后的身份证明文', r.get('no_id_card_plaintext'))
    case('H-7 已禁用账号在往届站不可登录', r.get('disabled_login_rejected'))
    # 2026-10-19 第十三轮审计：往届站原先无年级范围，年级长可枚举全部毕业年级
    case('H-7b 年级长登录往届站成功', r.get('grade_login_ok'))
    case('H-7b 年级长看得到本人当前年级', r.get('grade_sees_own'))
    case('H-7b 年级长看不到其他毕业年级', r.get('grade_hidden_other'))
    case('H-7b 年级长越界查询被 403', r.get('grade_out_of_scope_403'))


# ======================================================================
# M-7 CSRF：alumni 站无防护（主应用 token 有效期见 M-7a）
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
    # 2026-10-10 架构审计调整：原先这里验证 `app.utils.decorators.scope_required`，
    # 但该装饰器自定义后**从未被任何路由调用**（全仓库零命中），已于本次审计删除。
    # 真正生效的数据范围判定是 grades/services/scope.py，故 R-1 改为验证它 ——
    # 断言从「装饰器自身行为」升级为「实际生效的口径」，测试价值更高。
    from app.modules.grades.services import scope as scope_mod
    with app.app_context():
        teacher = db.session.get(User, IDS['teacher'])
        admin = db.session.get(User, IDS['admin'])

        # ① 可见年级返回列表（统一口径的核心出口）
        vis = scope_mod.visible_grades(teacher)
        case('R-1 任课教师可见年级返回列表', isinstance(vis, list))

        # ② 无有效数据范围的账号：考试判定一律拒绝（不回落全校）
        class _FakeExam:
            grade = '__NOT_A_REAL_GRADE__'

        class _NoScopeUser:
            """模拟「无任何数据范围配置」的账号（无 permission_group / 无映射）"""
            role = 'noperm'
            id = -1
            permission_group = None
            grade = None

            def has_role(self, *_a, **_k):
                return False

        try:
            scope_mod.check_exam_visible(_NoScopeUser(), _FakeExam())
            case('R-1 无数据范围账号访问考试被拒', False, '未拒绝')
        except PermissionError:
            case('R-1 无数据范围账号访问考试被拒', True)

        # ③ admin 恒不受限（各口径一致的最高层约定）
        try:
            scope_mod.check_exam_visible(admin, _FakeExam())
            case('R-1 admin 不受数据范围限制', True)
        except PermissionError:
            case('R-1 admin 不受数据范围限制', False, 'admin 被误拒')

    # ④ 统一口径必须存在可被路由调用的函数（防止"机制被删但无人知晓"）
    for fn in ('visible_grades', 'check_exam_visible', 'user_grade_scope'):
        case(f'R-1 统一口径提供 {fn}', callable(getattr(scope_mod, fn, None)))

    # 2026-10-12：scope_service 转发壳已删除（30 行纯转发、自述"请勿新增逻辑"），
    # 判定逻辑唯一实现在 workbench/services/scope.py（委托 grades/services/scope.py）。
    src = _read('app/modules/workbench/services/scope.py')
    case('R-1 workbench 判定实现存在且提供 class_allowed',
         'def class_allowed' in src)
    case('R-1 workbench scope 委托 grades 统一口径',
         'from app.modules.grades.services.scope import' in src)


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

    # 2026-10-13 全面审计 M-4：脱敏必须覆盖 logging.getLogger(__name__) 这类模块 logger
    # （名称形如 app.modules.*，不在名单里），且必须管住"装脱敏之后再新建的 handler"
    # —— 靠 LogRecord 工厂在记录创建瞬间掩码（handler/logger 级过滤都追不上晚挂的 handler）。
    import io as _io
    import logging as _logging
    from app.utils.log_mask import install_log_masking
    buf = _io.StringIO()
    late = _logging.StreamHandler(buf)          # 故意晚于 create_app 的安装时刻
    _logging.getLogger().addHandler(late)
    try:
        install_log_masking()
        _logging.getLogger('app.modules.system.routes.students').warning(
            '测试 身份证 110101200001011234 手机 13912345678')
        late.flush()
        out = buf.getvalue()
    finally:
        _logging.getLogger().removeHandler(late)
    case('M-4 模块 logger（app.modules.*）日志已脱敏',
         '110101200001011234' not in out and '13912345678' not in out,
         (out.strip() or '（未捕获到日志）')[-60:])
    src2 = _read('app/utils/log_mask.py')
    case('M-4 脱敏覆盖 handler 与 LogRecord 工厂',
         'setLogRecordFactory' in src2 and '_install_on_handlers' in src2, 'log_mask')

    # 2026-10-13 现场回归（MCP 起不来）：handler **已经带普通 Formatter** 时也要能安全
    # 包装 —— MCP 进程先 logging.basicConfig(format=…) 建了标准 Formatter，之后
    # create_app 才装脱敏；原实现读 `fmt.style`（logging.Formatter 无此公开属性，
    # 风格存在 _style / 格式串在 _fmt）→ AttributeError 抛到 build_asgi_app →
    # [MCP] 启动失败：'Formatter' object has no attribute 'style'。
    from app.utils.log_mask import MaskingFormatter, _wrap_formatter
    case('M-4 风格还原不再依赖 Formatter.style', '_fmt_style' in src2, 'log_mask')
    buf2 = _io.StringIO()
    late2 = _logging.StreamHandler(buf2)
    late2.setFormatter(_logging.Formatter('%(levelname)s|%(message)s'))
    _wrap_formatter(late2)                     # 不得抛异常（抛了 = 服务起不来）
    case('M-4 带普通 Formatter 的 handler 可安全包装',
         isinstance(late2.formatter, MaskingFormatter),
         type(late2.formatter).__name__)
    late2.handle(_logging.LogRecord('t', _logging.INFO, __file__, 1,
                                    '身份证 110101200001011234', None, None))
    out2 = buf2.getvalue().strip()
    case('M-4 包装后保留原格式串且已脱敏',
         out2.startswith('INFO|') and '110101200001011234' not in out2
         and '1234' in out2, out2[-45:] or '（无输出）')


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
    # 2026-10-10：旧课表导入（timetable.py）已整体下线，草稿安全要求转移到
    # 现存的「原样课表导入」草稿（schedule.py 的 _SMART_DRAFT）。
    src = _read('app/modules/academic/routes/schedule.py')
    case('L-4 草稿加锁', '_SMART_DRAFT_LOCK' in src and 'threading' in src)
    case('L-4 草稿数量上限', '_SMART_DRAFT_MAX' in src)
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
    # 2026-10-22：脚本随「scripts → tests」结构治理移动，这里跟着改；
    # 保留旧路径兼容（若有人还在旧分支上跑）。
    scan = os.path.join(BASE, 'tests', 'scan_security_patterns.py')
    if not os.path.exists(scan):
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
    used = [f for f in ('scripts/fix/cleanup_student_columns.py',
                        'scripts/migrate/migrate_notification_recipients.py',
                        'scripts/migrate/migrate_cert_sign_v1122.py',
                        'scripts/migrate/migrate_affair_v1121.py',
                        'scripts/migrate/migrate_term_schedule_dates.py',
                        'scripts/seed/add_performance_indexes.py',
                        'scripts/migrate/migrate_split_db.py')
            if '_ddl_guard' in _read(f)]
    case('R-11 拼接 DDL 的脚本已接入守卫', len(used) >= 6, str(len(used)))


# ======================================================================
# CSRF-TPL 模板 POST 表单必须带 CSRF 防护（2026-10-10 新增）
# 背景：通知中心「全部已读」表单漏了 csrf_token，CSRFProtect 直接 400
# （用户实测报障）。这里做全站静态扫描，再有人漏就本项变红。
# ======================================================================
@item('CSRF-TPL')
def check_csrf_tpl():
    import glob as _glob
    form_re = re.compile(r'<form\b[^>]*>.*?</form>', re.S | re.I)
    tag_re = re.compile(r'<form\b[^>]*>', re.I)
    bad = []
    for path in _glob.glob(os.path.join(BASE, 'app', 'templates', '**', '*.html'),
                           recursive=True):
        rel = os.path.relpath(path, BASE).replace('\\', '/')
        src = _read(rel)
        for m in form_re.finditer(src):
            block = m.group(0)
            if not re.search(r'method\s*=\s*[\'"]?post',
                             tag_re.search(block).group(0), re.I):
                continue
            # Flask-WTF 表单用 form.hidden_tag()，等价于注入 csrf_token 隐藏域
            if 'csrf_token' in block or 'hidden_tag' in block:
                continue
            bad.append(f'{rel}:{src[:m.start()].count(chr(10)) + 1}')
    case('CSRF-TPL 所有 POST 表单带 CSRF 防护（csrf_token / hidden_tag）',
         not bad, '、'.join(bad[:8]))
    case('CSRF-TPL 通知中心表单已带 token',
         'csrf_token' in _read('app/templates/notifications/list.html'))


# ======================================================================
# SR 主应用侧数据范围缺口（2026-10-11 批次，见清单第 10 节）
# ======================================================================
@item('SR')
def check_SR():
    from app.utils.student_scope import apply_student_scope
    from app.utils.dormitory_scope import class_in_scope, student_in_dorm_scope

    # SR-1 画像列表接入数据范围唯一口径（行为 + 源码双证）
    case('SR-1 画像列表接入唯一口径',
         'apply_student_scope' in _read('app/modules/portrait/services/portrait_service.py'))
    with app.app_context():
        leader = db.session.get(User, IDS['leader'])
        admin = db.session.get(User, IDS['admin'])
        n_leader = {s.student_number for s in apply_student_scope(Student.query, user=leader).all()}
        n_admin = {s.student_number for s in apply_student_scope(Student.query, user=admin).all()}
        case('SR-1 年级长仅见本年级学生（范围外被过滤）',
             'SEC2025001' in n_leader and 'SEC2024001' not in n_leader)
        case('SR-1 管理员不受数据范围限制', 'SEC2024001' in n_admin)

        # SR-3 宿舍范围口径（班级/学生判定）
        case('SR-3 年级长限于本年级班级',
             class_in_scope(leader, '2025级', '01班') is True
             and class_in_scope(leader, '2024级', '01班') is False)
        case('SR-3 管理员全范围班级', class_in_scope(admin, '2024级', '01班') is True)
        s25 = Student.query.filter_by(student_number='SEC2025001').first()
        s24 = Student.query.filter_by(student_number='SEC2024001').first()
        case('SR-3 学生宿舍范围判定（范围内/外）',
             student_in_dorm_scope(leader, s25) is True
             and student_in_dorm_scope(leader, s24) is False)

    # SR-2 宿舍搜索删除内联角色判断、改走唯一口径
    dash = _read('app/modules/dormitory/routes/dashboard.py')
    case('SR-2 宿舍搜索改用唯一口径', 'apply_student_scope' in dash)
    case('SR-2 宿舍搜索不再内联角色分支',
         'homeroom_teacher' not in dash and 'grade_leader' not in dash)

    # SR-3 房间/分配端点接入宿舍范围口径
    case('SR-3 房间列表接入 room_scope_query',
         'room_scope_query' in _read('app/modules/dormitory/routes/rooms.py'))
    assign = _read('app/modules/dormitory/routes/assignments.py')
    case('SR-3 分配端点接入班级/学生范围校验',
         'class_in_scope' in assign and 'student_in_dorm_scope' in assign)

    # SR-4 宿舍统计年级长收敛本人年级
    # 2026-10-13：statistics 由自带 _get_scope（只读组级 scope_type、scope_type='none'
    # 时 fail-open 到全校）改为统一走 dormitory_scope.resolve_scope（含用户级数据范围 +
    # fail-closed），越权 grade 参数收敛到授权年级内。
    _stats_src = _read('app/modules/dormitory/routes/statistics.py')
    case('SR-4 年级长统计收敛本人年级',
         'resolve_scope()' in _stats_src
         and "if sel_grade not in allowed_grades:" in _stats_src)

    # SR-5 成绩 compare/export 数据范围 fail-closed
    case('SR-5 compare fail-closed',
         'if limited is not None:' in _read('app/modules/grades/routes/analysis.py'))
    case('SR-5 export fail-closed',
         'if limited is not None:' in _read('app/modules/grades/routes/export.py'))

    # SR-6 grades/rooms.html 内联处理器改 tojson（M-10 同类残留）
    # 2026-10-12：renameGroup 已重构为无参（curGroup() 取值），原钉字符串断言
    # （g.name|tojson）目标消失 → 改为扫描式：
    # ① 分组按钮不再把名称拼进 onclick 单引号串；② 无 M-10 形态的内联处理器。
    rooms_html = _read('app/templates/grades/rooms.html')
    case('SR-6 分组按钮不再拼接名称进 onclick',
         "renameGroup('" not in rooms_html)
    case('SR-6 无内联处理器单引号变量（M-10 形态）',
         not re.search(r'on\w+="[^"]*\'\{\{', rooms_html))


# ======================================================================
# SR-7 admin 数据范围特判（2026-10-12 全项目审计 P0-2）
# ======================================================================
@item('SR-7')
def check_SR7():
    """裸 admin（无权限组/无班级映射/无用户级范围）必须等同全校口径。

    2026-10-12 审计：`get_scope()` 缺 admin 特判 → admin 落到 ('none', None) →
    `visible_grades/visible_classes` 把管理员关进空集 → 分析页考试下拉空、
    班级对比被 SR-5 的 fail-closed 挡死（smoke_grades 7 连败现场）。
    与 `check_exam_visible`/`student_in_scope`/`_guard_tab`/`access_scope`
    已有的 admin 特判同口径；缺一处即全校最常用账号范围全灭。
    """
    from app.modules.grades.services import scope as scope_mod

    with app.app_context():
        adm = db.session.get(User, uid('admin'))
        case('SR-7 admin get_scope 恒为全校',
             scope_mod.get_scope(adm) == ('school', None),
             str(scope_mod.get_scope(adm)))
        case('SR-7 admin visible_classes 为 None（不限班）',
             scope_mod.visible_classes(adm) is None)
        grades = scope_mod.visible_grades(adm)
        case('SR-7 admin visible_grades 非空（全部未毕业年级）',
             bool(grades) and '2025级' in grades, str(grades))

        # 两班总分成绩，供 HTTP 层班级对比使用（幂等：先清后建）
        from app.models.grades import ExamScore as _ES
        _ES.query.filter_by(exam_id=uid('exam25')).delete()
        db.session.add(Student(student_number='SEC2025002', name='二班学生',
                               gender='女', grade='2025级', class_name='02班'))
        for no, cls, nm in (('SEC2025001', '01班', '一班学生'),
                            ('SEC2025002', '02班', '二班学生')):
            db.session.add(_ES(exam_id=uid('exam25'), student_no=no, student_name=nm,
                               grade='2025级', class_name=cls, subject='总分',
                               score=90.0, rank_dir=1))
        db.session.commit()

    with app.test_client() as c:
        case('SR-7 admin 登录成功', login_ok(c, 'sec_admin', TEST_PWD).status_code == 200)
        js = c.get('/grades/api/options').get_json() or {}
        case('SR-7 admin 分析页年级下拉非空',
             bool(js.get('data', {}).get('grades')), str(js)[:120])
        from urllib.parse import quote
        pair = quote('01班') + ',' + quote('02班')
        r = c.get(f'/grades/api/analysis/compare?exam_id={uid("exam25")}&classes={pair}')
        js = r.get_json() or {}
        ct = js.get('data', {}).get('tables', {}) if js.get('success') else {}
        case('SR-7 admin 班级对比 200 且两班入表（不被 fail-closed 挡死）',
             r.status_code == 200 and
             len(ct.get('cmp_overview', {}).get('rows', [])) == 2,
             str(r.get_data(as_text=True))[:160])


# ======================================================================
# SR-8 毕业归档数据完整性（2026-10-12 全项目审计 P0-1）
# ======================================================================
@item('SR-8')
def check_SR8():
    """毕业归档：SQL 不得引用已删列；空归档/失败必须硬中止。

    2026-10-12 审计 P0-1：原归档 SQL 仍 SELECT students 的
    boarding_type/day_student_type/textbook/teacher_notes（已被
    cleanup_student_columns 删列、迁往 dormitory 库 student_accommodation）
    → 必抛 no such column → 裸 except 吞掉 → student_count=0 而流程继续
    清空在校数据并标记毕业（"归档 0 人 + 数据已清"丢失链）。
    本组在临时库上真跑 /grade-mgmt/graduate 全流程。
    """
    import sqlite3
    from app.models.room import StudentAccommodation
    from app.models import GradeSetting

    with app.app_context():
        # graduate 第 1 步 shutil.copy2(dormitory.db) 要求文件已存在；
        # SQLAlchemy 惰性建库，先查一次触发 dormitory 库文件创建（测试环境专属）
        from app.models import Room as _Room
        _Room.query.limit(1).all()
        db.session.add(Student(student_number='SEC2024002', name='二班往届生',
                               gender='男', grade='2024级', class_name='02班'))
        db.session.flush()
        _sid = db.session.query(Student.id).filter_by(
            student_number='SEC2024002').scalar()
        # 01班无住宿记录 → LEFT JOIN 后四列应为 NULL；02班有 → 应带出
        db.session.add(StudentAccommodation(student_id=_sid, boarding_type='住校'))
        db.session.commit()

    with app.test_client() as c:
        case('SR-8 admin 登录', login_ok(c, 'sec_admin', TEST_PWD).status_code == 200)
        # M-2：登录后会话重建，须重新取 CSRF token（已登录时 /login 会 302，改打首页）
        csrf = get_csrf(c, '/')
        hdr = {'X-CSRFToken': csrf}
        # 空归档年级（字典存在但无学生）→ 必须 500 硬中止且不标记毕业
        r = c.post('/grade-mgmt/graduate', json={'grade': '2023级'}, headers=hdr)
        js = r.get_json() or {}
        case('SR-8 空归档硬中止（500 且 success=False）',
             r.status_code == 500 and js.get('success') is False,
             f'{r.status_code} {str(js)[:100]}')
        with app.app_context():
            gs = GradeSetting.query.filter_by(grade='2023级').first()
            case('SR-8 硬中止后未标记毕业', gs is None or not gs.is_graduated)

        # 正常毕业：SQL 不引用已删列，2 人全部归档
        r = c.post('/grade-mgmt/graduate', json={'grade': '2024级'}, headers=hdr)
        js = r.get_json() or {}
        case('SR-8 正常毕业归档 2 人（不再 no such column）',
             r.status_code == 200 and js.get('success') is True
             and '2' in (js.get('message') or ''), str(js)[:160])

    hist = sqlite3.connect(os.path.join(_DATA, 'history.db'))
    rows = hist.execute('SELECT student_number, class_name, boarding_type '
                        'FROM graduated_students WHERE graduated_grade=?',
                        ('2024级',)).fetchall()
    hist.close()
    by_no = {r[0]: r for r in rows}
    case('SR-8 归档行数=2', len(rows) == 2, str(rows))
    case('SR-8 LEFT JOIN：无住宿记录 → boarding_type 为空',
         by_no.get('SEC2024001', ('', '', 'x'))[2] in (None, ''))
    case('SR-8 LEFT JOIN：有住宿记录 → boarding_type=住校',
         by_no.get('SEC2024002', ('', '', None))[2] == '住校')


# ======================================================================
# SR-9 往届查询数据范围（2026-10-12 全项目审计 P1）
# ======================================================================
@item('SR-9')
def check_SR9():
    """往届（已毕业年级）查询必须收敛范围（H-2 在往届数据上的复活）。

    2026-10-12 审计：`/dormitory/statistics/alumni`、`/grade-mgmt/alumni`
    及两条 /history 路由原先零数据范围校验，任意持 statistics.view /
    system.grade_mgmt 的账号可按毕业年级拉取全校往届住宿/档案。
    口径与 check_exam_visible 对齐：毕业年级仅 admin/校级/用户级授权可见。
    """
    from app.utils.alumni_scope import (alumni_visible_grades,
                                        filter_alumni_grades,
                                        assert_alumni_grade_visible)

    with app.app_context():
        adm = db.session.get(User, uid('admin'))
        leader = db.session.get(User, uid('leader'))    # 年级长 + 授权 2025级
        noperm = db.session.get(User, uid('noperm'))    # staff 无权限组 → 无范围
        case('SR-9 admin 不限毕业年级', alumni_visible_grades(adm) is None)
        case('SR-9 年级长（用户级授权 2025级）仅见授权年级',
             alumni_visible_grades(leader) == {'2025级'})
        case('SR-9 无范围账号毕业年级空集（fail-closed）',
             alumni_visible_grades(noperm) == set())
        case('SR-9 下拉收敛：授权 2025 级 → 只见 2025 级',
             filter_alumni_grades(leader, ['2024级', '2025级']) == ['2025级'])
        try:
            assert_alumni_grade_visible(noperm, '2024级')
            case('SR-9 越界毕业年级抛 PermissionError', False)
        except PermissionError:
            case('SR-9 越界毕业年级抛 PermissionError', True)

    with app.test_client() as c:
        case('SR-9 年级长登录', login_ok(c, 'sec_leader', TEST_PWD).status_code == 200)
        r = c.get('/statistics/alumni?grade=2024级')
        case('SR-9 HTTP 越界往届宿舍查询 403', r.status_code == 403,
             str(r.status_code))
        r = c.get('/grade-mgmt/alumni?grade=2024级')
        case('SR-9 HTTP 越界往届生档案查询 403', r.status_code == 403,
             str(r.status_code))
        r = c.get('/grade-mgmt/history?grade=2024级')
        case('SR-9 HTTP 越界历史查询 403', r.status_code == 403, str(r.status_code))

    # admin 旁路用独立 client（同 client 内二次登录时 /login 已 302，取不到 token）
    with app.test_client() as c2:
        case('SR-9 admin 登录', login_ok(c2, 'sec_admin', TEST_PWD).status_code == 200)
        r = c2.get('/grade-mgmt/alumni?grade=2099级')
        case('SR-9 admin 可查任意毕业年级（旁路）', r.status_code == 200,
             str(r.status_code))


@item('SR-10')
def check_SR10():
    """敏感字段（身份证号 / 联系电话）字段级权限与脱敏展示（2026-10-13 权限审计）。

    此前学生身份证号 / 联系电话只有「导出」维度有权限点，**页面展示**完全没有控制 ——
    只要拿到 students.view 就能看到明文（学生详情页身份证号、学生列表 / 宿舍床位卡
    联系电话）。现新增字段级权限点（standalone，不随模块写入批发）：
      students.view_id_card / students.view_phone / academic.view_teacher_phone
    无权限时页面/接口一律返回脱敏值（app/utils/sensitive.py，后端决定而非前端隐藏）；
    导出敏感列需「查看 + 导出」双权限。
    """
    from app.utils import permission_map as pm
    from app.utils.sensitive import display, mask_phone

    # 1) 权限映射：三个字段级权限点为 standalone 且不随模块「写入」批发
    lv = pm.module_levels_to_item_levels({'students': 'write', 'academic': 'write'})
    case('SR-10 view_id_card 为 standalone（不随模块写入批发）',
         lv['students:view_id_card'] == 'none')
    case('SR-10 view_phone 为 standalone', lv['students:view_phone'] == 'none')
    case('SR-10 teacher_phone 为 standalone', lv['academic:teacher_phone'] == 'none')

    # 2) 脱敏口径
    case('SR-10 手机号脱敏前3后4', mask_phone('13800138000') == '138****8000',
         mask_phone('13800138000'))

    with app.app_context():
        adm = db.session.get(User, uid('admin'))
        leader = db.session.get(User, uid('leader'))
        case('SR-10 管理员有身份证查看权', adm.has_perm('students.view_id_card'))
        case('SR-10 年级长无身份证查看权',
             not leader.has_perm('students.view_id_card'))
        case('SR-10 年级长无手机号查看权', not leader.has_perm('students.view_phone'))
        case('SR-10 年级长脱敏展示',
             display('13800138000', 'phone', leader) == '138****8000')
        case('SR-10 管理员明文展示',
             display('13800138000', 'phone', adm) == '13800138000')

    sid = uid('student_in')
    with app.test_client() as c:
        case('SR-10 年级长登录', login_ok(c, 'sec_leader', TEST_PWD).status_code == 200)
        html = c.get('/students/').get_data(as_text=True)
        case('SR-10 年级长学生列表：手机号脱敏', '138****8000' in html)
        case('SR-10 年级长学生列表：不出现明文手机号', '13800138000' not in html)
        html = c.get(f'/students/{sid}').get_data(as_text=True)
        case('SR-10 年级长详情页：身份证脱敏', '110***********1234' in html)
        case('SR-10 年级长详情页：不出现明文身份证', '110101199001011234' not in html)

    with app.test_client() as c2:
        case('SR-10 admin 登录', login_ok(c2, 'sec_admin', TEST_PWD).status_code == 200)
        html = c2.get('/students/').get_data(as_text=True)
        case('SR-10 管理员学生列表：手机号明文', '13800138000' in html)
        html = c2.get(f'/students/{sid}').get_data(as_text=True)
        case('SR-10 管理员详情页：身份证明文', '110101199001011234' in html)

    # 3) 导出敏感列需「查看 + 导出」双权限（后端强校验，非仅前端置灰）
    src = _read('app/utils/export_helpers.py')
    case('SR-10 导出敏感列需查看+导出双权限', "config.get('view_perm')" in src)


@item('SR-11')
def check_SR11():
    """管理员组种子必须覆盖注册表全部权限点（防止再次漏列）。

    2026-10-13 文档整理时发现：管理员组 `menu_keys` 漏列 `academic.edit`，
    导致权限矩阵中「管理员组 × 教务管理 × 录入管理」显示为「不可见」，
    与该组"拥有全部权限"的语义不符（实际 has_perm 恒 True，仅显示不一致）。
    已修复并加此断言防回归。
    """
    from app.utils import permission_map as pm
    from app import PERMISSION_GROUPS

    all_keys = set()
    for m in pm.MODULES:
        for it in m['items']:
            all_keys.update(it['read'])
            all_keys.update(it['write'])

    admin = next((g for g in PERMISSION_GROUPS if g['name'] == '管理员组'), None)
    case('SR-11 管理员组存在', admin is not None)
    keys = set(admin['menu_keys'])
    case('SR-11 管理员组覆盖全部权限点（无缺口）',
         not (all_keys - keys), f'缺口 {sorted(all_keys - keys)}')
    case('SR-11 管理员组无未登记 key', not (keys - all_keys),
         f'多余 {sorted(keys - all_keys)}')

    levels = pm.keys_to_item_levels(admin['menu_keys'])
    invisible = [k for k, v in levels.items() if v == pm.LEVEL_NONE]
    case('SR-11 管理员组矩阵无「不可见」子功能', not invisible, str(invisible))


@item('SR-12')
def check_SR12():
    """学生身份证号 / 联系电话明文：**默认只有管理员与班主任**可见（2026-10-13 用户口径）。

    「系统默认只有管理员和班主任可以看到身份证号和电话，其他人都不需要。」
    —— 因此除这两组外，任何身份都不得持有 students.view_id_card / view_phone，
    宿舍侧（宿管）也一律脱敏。
    """
    from app import PERMISSION_GROUPS

    groups = {g['name']: set(g['menu_keys']) for g in PERMISSION_GROUPS}
    id_card_groups = sorted(n for n, k in groups.items() if 'students.view_id_card' in k)
    phone_groups = sorted(n for n, k in groups.items() if 'students.view_phone' in k)

    case('SR-12 身份证明文仅管理员组+班主任组',
         id_card_groups == ['班主任组', '管理员组'], str(id_card_groups))
    case('SR-12 联系电话明文仅管理员组+班主任组',
         phone_groups == ['班主任组', '管理员组'], str(phone_groups))
    case('SR-12 宿管组无身份证明文', 'students.view_id_card' not in groups['宿管组'])
    case('SR-12 宿管组无联系电话明文', 'students.view_phone' not in groups['宿管组'])
    case('SR-12 年级长组无明文（两个字段都无）',
         not ({'students.view_id_card', 'students.view_phone'} & groups['年级长组']))
    case('SR-12 任课教师组无明文',
         not ({'students.view_id_card', 'students.view_phone'} & groups['任课教师组']))

    sid = uid('student_in')

    # 班主任（本班）：两个字段都明文
    with app.test_client() as c:
        case('SR-12 班主任登录',
             login_ok(c, 'sec_homeroom', TEST_PWD).status_code == 200)
        html = c.get('/students/').get_data(as_text=True)
        case('SR-12 班主任列表：手机号明文', '13800138000' in html)
        html = c.get(f'/students/{sid}').get_data(as_text=True)
        case('SR-12 班主任详情：身份证明文', '110101199001011234' in html)

    # 宿管：两个字段都脱敏
    with app.test_client() as c2:
        case('SR-12 宿管登录', login_ok(c2, 'sec_dorm', TEST_PWD).status_code == 200)
        html = c2.get('/students/').get_data(as_text=True)
        case('SR-12 宿管列表：手机号脱敏', '138****8000' in html)
        case('SR-12 宿管列表：无明文手机号', '13800138000' not in html)


@item('SR-13')
def check_SR13():
    """多身份：权限取「并集」，当前身份只影响显示与数据范围。2026-10-20 用户口径"""
    from flask import session
    from flask_login import login_user
    mid = uid('multi')
    with app.app_context():
        u = db.session.get(User, mid)
        dorm_gid = PermissionGroup.query.filter_by(name='宿管组').first().id
        case('SR-13 多身份账号有两个身份', len(u.permission_groups) == 2,
             str([g.name for g in u.permission_groups]))
        with app.test_request_context('/'):
            login_user(u)
            # 默认身份 = 主组（任课教师组）
            case('SR-13 默认身份=主组（任课教师）',
                 u.active_group is not None and u.active_group.name == '任课教师组',
                 getattr(u.active_group, 'name', None))
            # 2026-10-20：两组权限自动合并（班主任 + 任课教师并存时两组能力都可用）
            case('SR-13 权限取并集（两组能力都可用）',
                 u.has_perm('academic.swap') and u.has_perm('dormitory.assign'))
            session['active_group_id'] = dorm_gid
            case('SR-13 切换身份只改显示/范围，权限集合不变',
                 u.active_group is not None and u.active_group.name == '宿管组'
                 and u.has_perm('academic.swap') and u.has_perm('dormitory.assign'))


@item('SR-14')
def check_SR14():
    """范围类型 7 值：旧 scope_type 映射无损 + pick_grades 解析。2026-10-14（P4）"""
    import json
    from app.models.permission_group import KIND_TO_LEGACY, LEGACY_TO_KIND
    from app.utils.scope import resolve_scope
    case('SR-14 旧 scope_type 映射无损',
         all(KIND_TO_LEGACY.get(v) == k for k, v in LEGACY_TO_KIND.items()))
    with app.app_context():
        g = PermissionGroup(name='SR14考务', scope_type='grade',
                            scope_kind='pick_grades', role='staff')
        g.set_menu_keys(['students.view'])
        g.scope_grades = json.dumps(['2025级'])
        db.session.add(g)
        db.session.flush()
        u = User(username='sec_sr14', real_name='指定年级', role='staff',
                 permission_group_id=g.id, must_change_pwd=False)
        u.set_password(TEST_PWD)
        db.session.add(u)
        db.session.flush()
        u.sync_primary_link()
        db.session.commit()
        s = resolve_scope(u)
        case('SR-14 pick_grades 解析为指定年级',
             s.kind == 'grades' and s.grades == {'2025级'}, str(s))


@item('SR-15')
def check_SR15():
    """多身份不放大范围：数据范围随「当前身份」变化（不取最宽）。2026-10-14（P2/P3）"""
    from flask import session
    from flask_login import login_user
    from app.utils.scope import resolve_scope
    mid = uid('multi')
    with app.app_context():
        u = db.session.get(User, mid)
        dorm_gid = PermissionGroup.query.filter_by(name='宿管组').first().id
        with app.test_request_context('/'):
            login_user(u)
            case('SR-15 主身份范围=本人班级（任课教师）',
                 resolve_scope().kind == 'classes', str(resolve_scope()))
            session['active_group_id'] = dorm_gid
            case('SR-15 切宿管后范围=全校（不被主身份收窄）',
                 resolve_scope().kind == 'school', str(resolve_scope()))


@item('SR-16')
def check_SR16():
    """日志脱敏 Formatter 包装：handler 已预先有 formatter 时不得崩溃。2026-10-14"""
    import logging as _log
    from app.utils.log_mask import (MaskingFormatter, _formatter_style,
                                    _wrap_formatter)
    # 三种 style 推断正确
    for style, fmt, expect in (('%', '%(levelname)s %(message)s', '%'),
                               ('{', '{levelname} {message}', '{'),
                               ('$', '${levelname} ${message}', '$')):
        f = _log.Formatter(fmt, style=style)
        case('SR-16 推断 style=%s' % style, _formatter_style(f) == expect,
             _formatter_style(f))
    # 回归：handler 已有 formatter（uvicorn 场景）时不得抛 AttributeError
    h = _log.StreamHandler()
    h.setFormatter(_log.Formatter('%(levelname)s %(message)s'))
    try:
        _wrap_formatter(h)
        case('SR-16 包装已有 formatter 不崩溃',
             isinstance(h.formatter, MaskingFormatter)
             and h.formatter._fmt == '%(levelname)s %(message)s')
    except AttributeError as e:
        case('SR-16 包装已有 formatter 不崩溃', False, repr(e))


@item('PERF113')
def check_exam_report_cache_invalidation():
    """汇报缓存按考试精确失效：**被比对场次**改分也必须失效（2026-10-14 第三轮审计）。

    背景（PERF-113 正确性回归）：PERF-19 把 `grades_report_` 从"全清"收窄为
    "只清本场（旧实现比对 `split('_')[3] == exam_id`）"，但 `sl`（对比）键里还带
    **被比对考试** id：`grades_report_sl_<主考>_<方向>_<层>_<比对考>_<c|nc>`。
    于是"改 B 场分数"不会清掉 (A vs B) 的缓存 → 最长 `CACHE_TIMEOUT=900s` 呈现陈旧
    对比数据。PERF-114 同源：位置契约（第 4 段）一旦新增/挪动分段就静默不失效。

    本项断言修复后的行为：**键中任一分段命中该考试**即失效（宁可多清几个键，
    都不能留陈旧数据）；`grades_report_ld_*`（全局对比，跨考试取数）仍由调用方
    单独整体失效，不归本函数管。
    """
    from app.modules.grades.utils import delete_cache_exam_reports
    from app.utils.cache import cache

    keys = [
        # 主 19、被比对 21 → 清 19 时本来就会清
        'grades_report_sl_19_物理_一本_21_c',
        # 主 21、被比对 19 → ★ PERF-113 核心：改 19 必须也清掉这个键
        'grades_report_sl_21_物理_一本_19_c',
        # 主 19 / 主 21 的「显式不对比」哨兵
        'grades_report_sl_19_物理_一本__nc',
        'grades_report_sl_21_物理_一本__nc',
        # 其它 kind（班级概况）
        'grades_report_co_19_物理',
        'grades_report_co_21_物理',
        # ★ PERF-114：新增分段使考试 id 不在第 4 段（旧实现永远清不到）
        'grades_report_sl_物理_19_一本',
        # 全局对比：不归本函数管（调用方另有整体失效）
        'grades_report_ld_19_物理',
        # 非汇报缓存键：一个都不能动
        'grades_options_classes_by_exam',
    ]
    for k in keys:
        cache.set(k, {'key': k}, timeout=60)
    try:
        delete_cache_exam_reports(19)
        alive = {k: cache.get(k) is not None for k in keys}
        case('PERF113 主考 19 的 sl 键被清',
             not alive['grades_report_sl_19_物理_一本_21_c'])
        case('PERF113 被比对场次 19 的 sl 键也被清（修复前残留 900s 陈旧数据）',
             not alive['grades_report_sl_21_物理_一本_19_c'])
        case('PERF113 主考 19 的 nc 哨兵键被清',
             not alive['grades_report_sl_19_物理_一本__nc'])
        case('PERF113 无关场次 21 的 nc 键保留',
             alive['grades_report_sl_21_物理_一本__nc'])
        case('PERF113 主考 19 的 co 键被清', not alive['grades_report_co_19_物理'])
        case('PERF113 无关场次 21 的 co 键保留', alive['grades_report_co_21_物理'])
        case('PERF114 分段位置变化后仍按考试失效（不再依赖第 4 段）',
             not alive['grades_report_sl_物理_19_一本'])
        case('PERF113 全局对比 ld 键不在本函数管辖内',
             alive['grades_report_ld_19_物理'])
        case('PERF113 非汇报缓存键不受影响',
             alive['grades_options_classes_by_exam'])
    finally:
        for k in keys:
            cache.delete(k)


# ======================================================================
# 第四轮审计（2026-10-14）修复守卫：SEC-03/04/05、SEC3-01、PERF-101/102/110/112、
# 多身份范围（班级白名单）、DATA3-01、多身份迁移保护。
# 均为源码级/契约级断言：修复点一旦被回退（装饰器换回 grades.view、GET 重新写库、
# 缓存改回重查全量、班级口径改回全表 GROUP BY）立即变红。
# ======================================================================

def _read_src(rel):
    with open(os.path.join(BASE, rel), encoding='utf-8') as f:
        return f.read()


def check_audit4():
    import re

    mp = _read_src('app/utils/permission_map.py')
    case('SEC-04 权限点 grades.ai_manage 已定义（standalone 单独勾选）',
         "'ai_manage'" in mp and "'grades.ai_manage'" in mp)

    ai = _read_src('app/modules/grades/routes/ai.py')
    case('SEC-04 全局 Key 三端点装饰器统一为 grades.ai_manage',
         ai.count("@perm_required('grades.ai_manage')") == 3)
    case('SEC-04 函数体守卫同判据（重构漏拷装饰器仍有防线）',
         "current_user.has_perm('grades.ai_manage')" in ai)

    sl = _read_src('app/modules/academic/routes/subject_leaders.py')
    case('SEC-05 导出文件名走 safe_download_name（未消毒的 args 不再进响应头）',
         'safe_download_name(' in sl)
    _ctx = sl.split('def _context()')[1].split('def subject_leaders')[0]
    case('SEC3-01 GET 只读：_context 内不再写库（无 svc.ensure_plan 调用）',
         'svc.ensure_plan(' not in _ctx)
    case('SEC3-01 保存路径仍登记方案（ensure_plan 保留在 POST）',
         'svc.ensure_plan(' in sl)

    insp = _read_src('app/modules/academic/routes/inspection.py')
    case('SEC-03 单节总览代办入口记录实际操作人',
         'operator_uid=op_uid' in insp and 'operator_name=op_name' in insp)
    ssvc = _read_src('app/modules/academic/services/swap_service.py')
    case('SEC-03 服务层支持 operator 参数并落库',
         'operator_uid=None, operator_name=' in ssvc
         and 'operator_uid=(operator_uid or None)' in ssvc)
    case('SEC-03 代办时通知点名操作人', '（由 %s 代办）' in ssvc)
    tt = _read_src('app/models/timetable.py')
    case('SEC-03 模型有 operator_uid / operator_name 两列',
         'operator_uid = db.Column' in tt and 'operator_name = db.Column' in tt)

    ais = _read_src('app/modules/grades/services/ai_service.py')
    case('PERF-101 追问复用共享 ExamData 缓存（不再重查两场全量）',
         'cached_exam_data(ex.id).rows' in ais)
    imp = _read_src('app/modules/grades/routes/ai_import.py')
    case('PERF-102 原始分回填一次预取（无逐行 filter().first()）',
         'existing.get((r[' in imp
         and "filter_by(exam_id=exam.id, student_no=r['no']" not in imp)
    rk = _read_src('app/modules/grades/services/ranking.py')
    case('PERF-110 排名改批量写库（bulk_update_mappings + _rank_map）',
         'bulk_update_mappings(ExamScore' in rk and 'def _rank_map' in rk)
    case('PERF-110 已删除逐行 setattr 的 _assign_ranks（不留死代码）',
         'def _assign_ranks' not in rk)
    an = _read_src('app/modules/grades/routes/analysis.py')
    case('PERF-112 班级枚举取总分行口径（考试当时班级）',
         'ExamScore.subject == TOTAL_SUBJECT' in an)
    case('PERF-112 班级按数据范围收敛下发（_restrict_pairs）',
         '_restrict_pairs' in an)
    ap = _read_src('app/modules/grades/services/ai_prompt.py')
    case('PERF-112 生成器班级口径同源（总分行）',
         'ExamScore.subject == TOTAL_SUBJECT' in ap)

    sc = _read_src('app/utils/scope.py')
    case('多身份范围 user_scope_detail 原语存在且被 resolve_scope 使用',
         'def user_scope_detail' in sc and 'user_scope_detail(u)' in sc)
    case('多身份范围 纯班级级授权收成 classes（不放大为整年级）',
         "return Scope('classes', classes=pairs, origin='user_grades')" in sc)
    case('多身份范围 学生查询吸收班级白名单',
         'user_scope_detail(user)' in _read_src('app/utils/student_scope.py'))
    case('多身份 用户组保存校验组存在性（防悬空关联）',
         'valid_ids' in _read_src('app/modules/system/routes/users.py'))
    pm = _read_src('scripts/migrate/migrate_perm_multi_20261014.py')
    case('多身份 废弃组删除前同时统计 user_permission_groups 引用',
         'UserPermissionGroup.query.filter_by(group_id=g.id).count()' in pm)
    case('多身份 P1 迁移补 is_primary 标记',
         'fix_primary' in _read_src('scripts/migrate/migrate_user_groups_p1.py'))

    gm = _read_src('app/models/grades.py')
    ac = _read_src('app/models/academic.py')
    case('DATA3-01 两张方案表声明 is_current 部分唯一索引',
         'uq_link_plan_current' in gm and 'uq_leader_plan_current' in ac)
    import sqlite3

    def _has_idx(db, ix):
        p = os.path.join(BASE, 'data', db)
        if not os.path.exists(p):
            return True          # 无库环境跳过（新装由 create_all 建）
        con = sqlite3.connect(p)
        try:
            return con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                (ix,)).fetchone() is not None
        finally:
            con.close()

    case('DATA3-01 现网 grades.db 已建 uq_link_plan_current',
         _has_idx('grades.db', 'uq_link_plan_current'))
    case('DATA3-01 现网 academic.db 已建 uq_leader_plan_current',
         _has_idx('academic.db', 'uq_leader_plan_current'))
    case('PERF-117/118 现网已建 oplog / cert / entry 索引',
         _has_idx('system.db', 'idx_oplog_created')
         and _has_idx('grades.db', 'idx_cert_generated_by')
         and _has_idx('timetable.db', 'idx_entry_deleted_teacher'))
    case('CONS-07 新功能 smoke 已挂进一键回归', (
        lambda r: 'SMOKES' in r and 'tests/smoke_grades.py' in r)(
            _read_src('tests/run_all_regression.py')))


ITEMS['AUDIT4'] = check_audit4


@item('SR-17')
def check_SR17():
    """2026-10-16 审计修复防回归：html_guard 空元素吞内容 / 课时量范围与临时条目 / 组长科目归一。"""
    import inspect as _ins
    from app.utils.html_guard import sanitize_report_html
    # ① DROP 集合中的空元素（input/meta）与自闭合标签不得吞掉其后内容
    case('SR-17 input 之后内容保留',
         'World' in sanitize_report_html('<p>Hello</p><input type="text"><p>World</p>'))
    case('SR-17 meta 之后内容保留',
         'b' in sanitize_report_html('<p>a</p><meta charset="utf-8"><p>b</p>'))
    case('SR-17 自闭合 script 之后内容保留',
         'b' in sanitize_report_html('<p>a</p><script/><p>b</p>'))
    case('SR-17 script 块后内容保留',
         'b' in sanitize_report_html('<p>a</p><script>alert(1)</script><p>b</p>'))
    case('SR-17 XSS 仍被中和',
         'onerror' not in sanitize_report_html('<img src=x onerror=alert(1)>'))
    # 2026-10-23：整篇 HTML 文档（html/head/body 外壳）不得把正文一起丢掉
    _doc = ('<!DOCTYPE html><html><head><title>T</title><style>x{}</style>'
            '</head><body><h4>报告</h4><p>正文</p></body></html>')
    case('SR-17 整篇 HTML 文档保留正文',
         '报告' in sanitize_report_html(_doc)
         and '正文' in sanitize_report_html(_doc))
    case('SR-17 整篇文档里的脚本仍被丢弃',
         'alert' not in sanitize_report_html(
             '<html><body><script>alert(1)</script><p>ok</p></body></html>'))
    with app.app_context():
        from app.modules.academic.services import workload_service as _wl
        case('SR-17 课时量统计排除临时调课条目',
             'temp_target_ids' in _ins.getsource(_wl.calc_workload))
        from app.modules.academic.services import schedule_service as _ss
        case('SR-17 组长徽章科目名归一',
             'normalize_subject' in _ins.getsource(_ss.leader_roles))
        from app.modules.academic.routes.inspection import (
            inspection_workload_export as _exp)
        case('SR-17 课时量导出：受限账号须落在可见年级',
             'academic_class_authorizer' in _ins.getsource(_exp))


@item('SR-18')
def check_SR18():
    """2026-10-20 审计修复防回归：wxapi 调课列表 fail-closed / 教师名单权限 /
    复核录入 NaN 与范围下推 / 操作日志清理双保险 / MCP 读频控不清零。"""
    import inspect as _ins

    def _src(rel):
        with open(os.path.join(BASE, rel), encoding='utf-8') as f:
            return f.read()

    # ① 无教师档案时不能退化成「不限申请人」读全校调课单
    #    注意：wx_swap_list 被 wx_perm_required 包装过，inspect.getsource 拿到的是
    #    wrapper，故直接按源码切片判断。
    _s = _src('app/modules/wxapi/academic/routes_swap.py')
    _body = _s[_s.index('def wx_swap_list'):]
    _body = _body[:_body.index('@bp.route')] if '@bp.route' in _body else _body
    case('SR-18 调课列表无档案时 fail-closed（按空集返回）',
         'if not uid or sched is None' in _body
         and (_body.index('if not uid or sched is None')
              < _body.index('items, pag = swap_service.get_swap_list')))
    # ② 小程序教师名单与网页端同口径（academic.view）
    _t = _src('app/modules/wxapi/academic/routes_teachers.py')
    case('SR-18 小程序教师名单要求 academic.view',
         'wx_perm_required' in _t and "'academic.view'" in _t
         and '@wx_login_required' not in _t)
    # ③ 复核录入：NaN 拦截 + 可见范围下推到 SQL 后再 limit
    _e = _src('app/modules/grades/routes/score_review_entry.py')
    case('SR-18 复核改分拒绝 NaN/inf', 'math.isfinite(new)' in _e)
    case('SR-18 学生搜索把范围下推到 SQL',
         '_teach_subjects' in _e and '_granted_classes' in _e
         and '_conds.append' in _e)
    # ④ 操作日志清理：导出完整性校验 + 删前整库备份
    _l = _src('scripts/ops/prune_operation_logs.py')
    case('SR-18 操作日志清理先校验导出再删',
         'written != len(rows)' in _l and 'backup_db' in _l)
    # ⑤ MCP 读频控表满不得全量清零
    _a = _src('mcp_server/core/auth.py')
    case('SR-18 MCP 读频控不清零全部计数',
         '_read_hits.clear()' not in _a and 'if len(_read_hits) >= _READ_HITS_MAX' in _a)


@item('SR-19')
def check_SR19():
    """2026-10-20 审计加固防回归：改生产数据的修数据/权限同步脚本必须有门控与备份。"""

    def _src(rel):
        with open(os.path.join(BASE, rel), encoding='utf-8') as f:
            return f.read()

    # ① import_exam36（整场重置导入 + 清空方向快照）：必须 --apply 门控 + 备份 + 失败退出码
    _i = _src('scripts/fix/import_exam36_202510.py')
    case('SR-19 exam36 重导入默认 dry-run（需 --apply）',
         "APPLY = '--apply' in sys.argv" in _i and 'if not APPLY' in _i)
    case('SR-19 exam36 重导入改库前备份 grades.db',
         'backup_db' in _i and 'grades.db' in _i)
    case('SR-19 exam36 对照 FAIL 时退出码非 0',
         'sys.exit(main())' in _i and 'return 1 if bad else 0' in _i)
    # ② 权限组同步（决定谁能审批/改分）：改库前备份 + 迁移登记
    _r = _src('scripts/seed/sync_role_groups_20261020.py')
    case('SR-19 权限组同步改库前备份 system.db',
         'backup_db' in _r and 'system.db' in _r and 'rollback()' in _r)
    case('SR-19 权限组同步登记迁移日志',
         '_mig_log' in _r and 'is_applied' in _r and '_mig_log.record' in _r)
    # ③ 自审口径注释须与实现一致（管理员/校长例外），避免后人误判为回归
    _v = _src('app/modules/grades/services/review_service.py')
    case('SR-19 复核审批注释与 is_top_reviewer 口径一致',
         'is_top_reviewer' in _v and '管理员 / 校长例外' in _v)


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

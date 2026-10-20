# -*- coding: utf-8 -*-
"""往届查询站（alumni_app）安全回归——子进程脚本

alumni_app 是独立包，内部 `from config import Config` 指向 `alumni_app/config.py`，
与主应用同进程导入会产生包命名冲突，因此由 `tests/sec_regression.py` 以子进程调用，
本脚本把结果以 JSON 打印到标准输出供父进程断言。

运行：python tests/alumni_regression.py
"""
import json
import os
import sqlite3
import sys
import tempfile
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ALUMNI = os.path.join(ROOT, 'alumni_app')
sys.path.insert(0, ALUMNI)

_TMP = tempfile.mkdtemp(prefix='stulink_alumni_')
os.environ['DATA_DIR'] = _TMP

from werkzeug.security import generate_password_hash  # noqa: E402

TEST_PWD = 'SecTest#2026'
_db = os.path.join(_TMP, 'system.db')
conn = sqlite3.connect(_db)
# 2026-10-19：补 grade / permission_group_id 两列 —— 往届站现在按主站
# alumni_scope 口径做年级收敛（年级长只看本人当前年级）
conn.execute(
    'CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,'
    ' real_name TEXT, role TEXT, is_active INTEGER DEFAULT 1,'
    ' grade TEXT, permission_group_id INTEGER)')
_pwd = generate_password_hash(TEST_PWD)
conn.executemany(
    'INSERT INTO users (username, password_hash, real_name, role, is_active,'
    ' grade, permission_group_id) VALUES (?,?,?,?,?,?,?)',
    [('alumni_admin', _pwd, '往届管理员', 'admin', 1, '', None),
     ('alumni_teacher', _pwd, '任课教师', 'teacher', 1, '', None),
     ('alumni_dorm', _pwd, '宿管教师', 'dorm_manager', 1, '', None),
     ('alumni_grade', _pwd, '年级长', 'grade_leader', 1, '2024级', 2),
     ('alumni_disabled', _pwd, '已禁用管理员', 'admin', 0, '', None)])
conn.execute(
    'CREATE TABLE permission_groups (id INTEGER PRIMARY KEY, name TEXT,'
    ' scope_type TEXT, scope_kind TEXT, scope_grades TEXT, scope_classes TEXT)')
conn.execute(
    'INSERT INTO permission_groups (id, name, scope_type, scope_kind,'
    ' scope_grades, scope_classes) VALUES (2, ?, ?, ?, ?, ?)',
    ('年级长组', 'grade', 'self_grade', '', ''))
conn.commit()
conn.close()

# 往届库夹具：两个毕业年级各一条（用于验证年级收敛）
_hist = os.path.join(_TMP, 'history.db')
_hc = sqlite3.connect(_hist)
_hc.execute(
    'CREATE TABLE graduated_students (id INTEGER PRIMARY KEY, original_id INTEGER,'
    ' student_number TEXT, name TEXT, class_name TEXT, graduated_grade TEXT)')
_hc.executemany(
    'INSERT INTO graduated_students (original_id, student_number, name,'
    ' class_name, graduated_grade) VALUES (?,?,?,?,?)',
    [(1, '20240001', '张三', '01班', '2024级'),
     (2, '20250001', '李四', '01班', '2025级')])
_hc.commit()
_hc.close()

import config as _cfg  # noqa: E402
_cfg.Config.SECRET_KEY = 'alumni-regression-secret'

from app import create_app  # noqa: E402

application = create_app()
res = {}


def _csrf(c):
    html = c.get('/login').get_data(as_text=True)
    import re
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return html, (m.group(1) if m else '')


with application.test_client() as c:
    html, token = _csrf(c)
    res['login_page_200'] = '往届生查询系统' in html
    # M-7：登录表单必须带 CSRF token
    res['csrf_token_present'] = bool(token)

    # H-7：任课教师（不在白名单）用主站口令登录被拒。
    # 2026-10-19 第十三轮审计：拒绝时不再返回可区分的 403 文案（会暴露
    # "口令正确但角色不对"），改为登录页 + 统一口令错误文案 —— 断言同步
    # 改成"未建立会话（不是 302）+ 统一文案"。
    r = c.post('/login', data={'username': 'alumni_teacher', 'password': TEST_PWD,
                               'csrf_token': token}, follow_redirects=False)
    _body = r.get_data(as_text=True)
    res['teacher_login_rejected'] = (r.status_code != 302
                                     and '用户名或密码错误' in _body)

    # H-7：宿管教师同样被拒
    r = c.post('/login', data={'username': 'alumni_dorm', 'password': TEST_PWD,
                               'csrf_token': token}, follow_redirects=False)
    _body = r.get_data(as_text=True)
    res['dorm_login_rejected'] = (r.status_code != 302
                                  and '用户名或密码错误' in _body)

    # H-7 正向：白名单角色（admin）可正常登录
    r = c.post('/login', data={'username': 'alumni_admin', 'password': TEST_PWD,
                               'csrf_token': token}, follow_redirects=False)
    res['admin_login_ok'] = r.status_code == 302 and urlparse(
        r.headers.get('Location', '')).path == '/'

    # 登录后首页可达，且上下文中不含解密后的身份证明文
    r = c.get('/')
    body = r.get_data(as_text=True)
    res['index_ok'] = r.status_code == 200
    res['no_id_card_plaintext'] = 'id_card_decrypted' not in body

    c.get('/logout')

# H-7b（2026-10-19 第十三轮审计新增）：年级长只能看本人当前年级
with application.test_client() as c:
    _, token = _csrf(c)
    r = c.post('/login', data={'username': 'alumni_grade', 'password': TEST_PWD,
                               'csrf_token': token}, follow_redirects=False)
    res['grade_login_ok'] = r.status_code == 302
    body = c.get('/').get_data(as_text=True)
    res['grade_sees_own'] = '2024级' in body
    res['grade_hidden_other'] = '2025级' not in body
    r = c.get('/?grade=2025级', follow_redirects=False)
    res['grade_out_of_scope_403'] = r.status_code == 403
    c.get('/logout')

# M-7：缺 CSRF token 的跨站表单提交应被拒（400）
with application.test_client() as c:
    r = c.post('/login', data={'username': 'alumni_admin', 'password': TEST_PWD})
    res['csrf_enforced'] = r.status_code == 400

# H-7/M-2：主站已禁用的账号在本站不可登录
with application.test_client() as c:
    _, token = _csrf(c)
    r = c.post('/login', data={'username': 'alumni_disabled', 'password': TEST_PWD,
                               'csrf_token': token}, follow_redirects=False)
    res['disabled_login_rejected'] = r.status_code != 302

print('ALUMNI_RESULT_JSON=' + json.dumps(res))

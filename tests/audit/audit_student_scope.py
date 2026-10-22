
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 B5：验证学生范围收紧（任课教师只能看任教班）"""
import re
from app import create_app  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
ok = fail = 0


def case(name, cond, extra=''):
    global ok, fail
    extra = str(extra).encode('gbk', 'replace').decode('gbk')
    if cond:
        ok += 1
        print(f'[PASS] {name} {extra}')
    else:
        fail += 1
        print(f'[FAIL] {name} {extra}')


def login(c, username):
    with a.app_context():
        u = User.query.filter_by(username=username).first()
        uid, fp = u.id, fingerprint(u)
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp


with a.app_context():
    from app.models import Student
    s_own = Student.query.filter_by(grade='2024级', class_name='01班').first()
    s_other = Student.query.filter_by(grade='2024级', class_name='02班').first()
    print(f'   任教班样本 {s_own.id} {s_own.name}；非任教班样本 {s_other.id} {s_other.name}')

# licancan 任教 2024级 01班/04班
with a.test_client() as c:
    login(c, 'licancan')
    r = c.get(f'/students/{s_other.id}')
    case('任课教师读非任教班学生 → 被拒', r.status_code in (302, 403),
         f'HTTP {r.status_code}')
    r = c.get(f'/students/{s_own.id}')
    case('任课教师读任教班学生 → 放行', r.status_code == 200
         and s_own.name in r.get_data(as_text=True), f'HTTP {r.status_code}')

# 班主任仍只看本班
with a.test_client() as c:
    login(c, 'liuwen')
    r = c.get(f'/students/{s_other.id}')
    case('班主任读别班学生 → 被拒', r.status_code in (302, 403),
         f'HTTP {r.status_code}')
    r = c.get(f'/students/{s_own.id}')
    case('班主任读本班学生 → 放行', r.status_code == 200, f'HTTP {r.status_code}')

# 管理员不受影响
with a.test_client() as c:
    login(c, 'admin')
    r = c.get(f'/students/{s_other.id}')
    case('管理员读任意学生 → 放行', r.status_code == 200, f'HTTP {r.status_code}')

# 列表与导出同步收敛
with a.test_client() as c:
    login(c, 'licancan')
    html = c.get('/students/?grade=2024级&class_name=02班').get_data(as_text=True)
    case('任课教师列表按非任教班筛选 → 无数据', s_other.name not in html)

print(f'\n===== 范围收紧检查：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)
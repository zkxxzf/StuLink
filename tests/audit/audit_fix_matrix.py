# -*- coding: utf-8 -*-
"""审计修复回归：口令授权上限 / 手机号脱敏 / 批量清空防护 / 批量改范围

覆盖 2026-10-18 的 6 项高危修复：
1) 学生表单不再把完整手机号塞 hidden；无权限保存不会清空号码
2) 教师编辑页同上
3) 宿舍"保存分配"空数组必须 confirm=CLEAR，否则 400；清空前归档
4) 批量改住宿属性走 apply_student_scope
5) 批量调班逐行校验范围
6) 口令重置/编辑/新建：只有超管 admin 能碰其他管理员
"""
import io
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import re  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
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
    html = c.get('/grades/').get_data(as_text=True)
    m = re.search(r'csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


# ---------- 准备临时账号 ----------
with a.app_context():
    from app.models.student import Student
    for uname, role in (('TMP_AUDIT_T', 'teacher'), ('TMP_AUDIT_A', 'admin')):
        u = User.query.filter_by(username=uname).first()
        if u is None:
            nu = User(username=uname, real_name=f'审计临时{role}',
                      role=role, is_active=True)
            nu.set_password('Tmp@12345')
            db.session.add(nu)
    db.session.commit()
    tmp_t = User.query.filter_by(username='TMP_AUDIT_T').first()
    tmp_a = User.query.filter_by(username='TMP_AUDIT_A').first()
    t_id, a_id = tmp_t.id, tmp_a.id
    # 有手机号的样本学生
    # 2026-10-19：phone1 已改为加密 property，**不能用于 SQL 过滤**，
    # 查询要用内部列属性 _phone1_encrypted（密文列），读取仍走 property 拿到明文。
    sample = (Student.query.filter(Student._phone1_encrypted.isnot(None),
                                   Student._phone1_encrypted != '').first())
    s_id, s_phone = sample.id, sample.phone1
    s_grade, s_class = sample.grade, sample.class_name
    print(f'   样本学生 #{s_id} {s_grade}{s_class} 手机 {s_phone[:3]}****')

print('\n=== 1) 口令 / 账号编辑授权上限 ===')
with a.test_client() as c:
    csrf = login(c, 'admin')
    r = c.post(f'/users/{a_id}/reset-password', headers={'X-CSRFToken': csrf})
    case('超管 admin 可重置管理员口令', r.status_code in (200, 302), f'HTTP {r.status_code}')

with a.test_client() as c:
    csrf = login(c, 'zhangzhen')
    r = c.post(f'/users/{a_id}/reset-password', headers={'X-CSRFToken': csrf})
    followed = c.get('/users/').get_data(as_text=True)
    case('普通管理员重置管理员 → 被拒', '只有超管' in followed, f'HTTP {r.status_code}')
    r = c.post(f'/users/{t_id}/reset-password', headers={'X-CSRFToken': csrf})
    with a.app_context():
        from app.models.operation_log import OperationLog
        hit = (OperationLog.query.filter_by(action='更新', target_id=t_id)
               .order_by(OperationLog.id.desc()).first())
    case('普通管理员重置教师 → 放行', hit is not None, hit.detail if hit else '无日志')

with a.test_client() as c:
    csrf = login(c, 'zhangzhen')
    r = c.post(f'/users/{a_id}/edit',
               data={'username': 'TMP_AUDIT_A', 'real_name': '审计临时admin',
                     'role': 'admin', 'password': 'Hacked@123'},
               headers={'X-CSRFToken': csrf})
    with a.app_context():
        db.session.expire_all()
        target = db.session.get(User, a_id)
        still_admin = (target.role == 'admin')
        pwd_unchanged = not target.check_password('Hacked@123')
    case('普通管理员编辑管理员 → 角色与密码均未被改',
         still_admin and pwd_unchanged,
         f'HTTP {r.status_code} role={target.role} pwd_changed={not pwd_unchanged}')
    r = c.post('/users/create',
               data={'username': 'TMP_AUDIT_X', 'real_name': 'x', 'role': 'admin',
                     'password': 'Test@1234'},
               headers={'X-CSRFToken': csrf})
    body = r.get_data(as_text=True)
    with a.app_context():
        exists = User.query.filter_by(username='TMP_AUDIT_X').first()
    case('普通管理员新建 admin → 被拒', exists is None, f'HTTP {r.status_code}')

print('\n=== 2) 手机号不再写进 hidden（无权限时） ===')
with a.app_context():
    # 固定用 liuwen（班主任，有 students.edit、无 students.view_phone，
    # 本班 = 2024级01班），避免随机选到只读账号导致页面 302 假通过
    cand_name = 'liuwen'
    s2 = Student.query.filter_by(grade='2024级', class_name='01班').first()
    s_id, s_phone = s2.id, (s2.phone1 or '')
print(f'   样本账号：{cand_name}｜学生 #{s_id} 号码 {s_phone[:3]}***')

if cand_name:
    with a.test_client() as c:
        login(c, cand_name)
        html = c.get(f'/students/{s_id}/edit').get_data(as_text=True)
        case('编辑页不含完整手机号', s_phone not in html, s_phone[:3] + '***')
        case('编辑页含锁定标记', 'data-phone-locked' in html)
        # 保存（不传 phone）后号码不应被清空
        with a.app_context():
            from app.models.student import Student as S
            s0 = db.session.get(S, s_id)
            keep = {'name': s0.name, 'student_number': s0.student_number or '',
                    'enrollment_status': s0.enrollment_status or ''}
        csrf = ''
        m = re.search(r'csrfToken\s*=\s*"([^"]+)"', html)
        if m:
            csrf = m.group(1)
        # 2026-10-18：只把 phone 置空来验证"不会被清空"，其余字段一律回传原值，
        # 避免审计脚本把真实学生的姓名/学号/学籍状态改掉。
        c.post(f'/students/{s_id}/edit',
               data={**keep, 'phone1': '', 'phone2': ''},
               headers={'X-CSRFToken': csrf})
        with a.app_context():
            from app.models.student import Student as S
            db.session.expire_all()
            after = db.session.get(S, s_id).phone1
        case('无权限保存后号码未被清空', after == s_phone, f'{s_phone[:3]}***')

print('\n=== 3) 宿舍"保存分配"空数组防护 ===')
with a.test_client() as c:
    csrf = login(c, 'admin')
    r = c.post('/rooms/save-assignments', json={'assignments': []},
               headers={'X-CSRFToken': csrf})
    case('空数组无 confirm → 400', r.status_code == 400, f'HTTP {r.status_code}')

print('\n=== 4) 批量改住宿属性范围（源码层面断言） ===')
with a.app_context():
    src = io.open('app/modules/dormitory/routes/dashboard.py', encoding='utf-8').read()
case('批量改住宿已走 apply_student_scope', 'apply_student_scope' in src)
src2 = io.open('app/modules/system/routes/students.py', encoding='utf-8').read()
case('批量调班已加 _student_in_scope 校验', '_student_in_scope(student)' in src2)
src3 = io.open('app/modules/dormitory/routes/rooms.py', encoding='utf-8').read()
case('全校清空已加 confirm + archive',
     "!= 'CLEAR'" in src3 and 'archive(' in src3 and 'log_operation' in src3)

# ---------- 清理 ----------
with a.app_context():
    for uid in (t_id, a_id):
        u = db.session.get(User, uid)
        if u is not None:
            db.session.delete(u)
    db.session.commit()
print('\n   已清理临时账号')

print(f'\n===== 修复矩阵：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

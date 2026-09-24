# -*- coding: utf-8 -*-
"""权限体系 v1.9.2 端到端验证：使用临时目录数据库，不影响本地 data/

覆盖：新身份组种子、子功能三档矩阵（反推/保存）、管理员组保护（不可改/删）、
管理员账号保护（不可禁用/改角色）、用户级数据范围（教务员仅 2025 级）、
academic.edit 查看/写入拆分、身份自由新建。
运行：python scripts/smoke_permissions.py
"""
import os
import re
import sys
import tempfile
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_perm_verify_')

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
}
config_mod.Config.SECRET_KEY = 'perm-verify-secret'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User, PermissionGroup, UserDataScope  # noqa: E402
from app.models.grades import Exam  # noqa: E402
from app.modules.grades.services.scope import (  # noqa: E402
    visible_grades, check_exam_visible, user_grade_scope)
from app.utils.permission_map import (keys_to_item_levels,  # noqa: E402
                                      item_levels_to_keys)

app = create_app()
ok = fail = 0


def check(name, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'[PASS] {name}')
    else:
        fail += 1
        print(f'[FAIL] {name} {extra}')


def get_csrf(c, path='/login'):
    """取 CSRF token：优先表单隐藏域，其次 base.html 注入的 var csrfToken。

    注意（M-2）：登录后服务端会清空并重建会话，登录前取的 token 将失效，
    因此登录后的写操作必须重新取一次。
    """
    html = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


# ---------- 1. 新身份组与子功能三档映射 ----------
with app.app_context():
    names = {g.name for g in PermissionGroup.query.all()}
    check('7 个新身份组已创建', {'校长', '副校长', '教务主任', '教务员',
                                 '备课组长', '教研组长', '学生发展中心'} <= names)

    jwy_g = PermissionGroup.query.filter_by(name='教务员').first()
    lv = keys_to_item_levels(jwy_g.get_menu_keys())
    check('教务员矩阵：成绩查看=只读', lv.get('grades:view') == 'read', str(lv))
    check('教务员矩阵：成绩教学管理=写入', lv.get('grades:manage') == 'write')
    check('教务员矩阵：教务录入=写入', lv.get('academic:edit') == 'write')
    check('教务员矩阵：系统管理=不可见', lv.get('system:users') == 'none')

    xz_g = PermissionGroup.query.filter_by(name='校长').first()
    lv2 = keys_to_item_levels(xz_g.get_menu_keys())
    check('校长矩阵：全校只读且无系统管理', lv2.get('grades:view') == 'read'
          and lv2.get('grades:manage') == 'none'
          and lv2.get('academic:edit') == 'none'
          and lv2.get('system:users') == 'none', str(lv2))
    check('校长矩阵：宿舍分床位/宿舍分配均不可写', lv2.get('dormitory:assign') == 'none'
          and lv2.get('dormitory:beds') == 'none')

    check('子功能→keys：教学管理写入含 edit/import/settings',
          {'grades.edit', 'grades.import', 'grades.settings'} <=
          set(item_levels_to_keys({'grades:manage': 'write'})))
    check('子功能→keys：只读仅给查看 key',
          item_levels_to_keys({'grades:view': 'read'}) == ['grades.view'])
    check('子功能→keys：床位分配独立于宿舍分配',
          item_levels_to_keys({'dormitory:beds': 'write'}) == ['dormitory.beds'])

# ---------- 2. 用户与数据范围（教务员张三仅 2025 级） ----------
with app.app_context():
    jwy_g = PermissionGroup.query.filter_by(name='教务员').first()
    zs = User(username='jwy001', real_name='教务员张三', role='staff',
              permission_group_id=jwy_g.id, must_change_pwd=False)
    zs.set_password('pw123456')
    db.session.add(zs)
    xz_g = PermissionGroup.query.filter_by(name='校长').first()
    xz = User(username='xz001', real_name='校长测试', role='staff',
              permission_group_id=xz_g.id, must_change_pwd=False)
    xz.set_password('pw123456')
    db.session.add(xz)
    db.session.flush()
    db.session.add(UserDataScope(user_id=zs.id, grade='2025级'))
    # H-1 之后内置 admin 的初始口令是随机生成的（不再有 admin123），
    # 冒烟脚本改为自建一个管理员账号，避免依赖默认口令。
    adm = User(username='smokeadm', real_name='冒烟管理员', role='admin',
               must_change_pwd=False)
    adm.set_password('SmokeAdm#2026')
    db.session.add(adm)
    e25 = Exam(name='2025级期中', grade='2025级', exam_date=date(2026, 4, 1))
    e26 = Exam(name='2026级期中', grade='2026级', exam_date=date(2026, 4, 1))
    db.session.add_all([e25, e26])
    db.session.commit()
    zs_id, xz_id, e25_id, e26_id = zs.id, xz.id, e25.id, e26.id

with app.app_context():
    zs = db.session.get(User, zs_id)
    xz = db.session.get(User, xz_id)
    check('用户级范围解析', user_grade_scope(zs) == ['2025级'])
    check('visible_grades 仅授权年级', visible_grades(zs) == ['2025级'],
          str(visible_grades(zs)))
    check('功能权限随身份：教务员有 academic.edit', zs.has_perm('academic.edit'))
    check('功能权限随身份：教务员无 system.users', not zs.has_perm('system.users'))
    try:
        check_exam_visible(zs, db.session.get(Exam, e25_id))
        check('授权年级考试可见', True)
    except PermissionError:
        check('授权年级考试可见', False, '被误拒')
    try:
        check_exam_visible(zs, db.session.get(Exam, e26_id))
        check('非授权年级考试拦截', False, '未拦截！')
    except PermissionError:
        check('非授权年级考试拦截', True)
    check('校长无数据范围配置（回落组级）', user_grade_scope(xz) is None)
    check('校长只读：无 academic.edit', not xz.has_perm('academic.edit'))
    check('校长可见全校年级', len(visible_grades(xz)) >= 2, str(visible_grades(xz)))

# ---------- 3. 权限管理页面与 API ----------
with app.test_client() as c:
    csrf = get_csrf(c)
    r = c.post('/login', data={'csrf_token': csrf, 'username': 'smokeadm',
                               'password': 'SmokeAdm#2026'}, follow_redirects=True)
    check('admin 登录', r.status_code == 200)
    csrf = get_csrf(c, '/perm-groups/')   # M-2：登录后会话重建，需重新取 token

    r = c.get('/perm-groups/')
    html = r.get_data(as_text=True)
    check('权限页 200 且含子功能表头', r.status_code == 200
          and '功能权限：身份 × 子功能' in html and '数据范围：用户 × 年级' in html
          and '系统保留' in html)

    with app.app_context():
        xz_gid = PermissionGroup.query.filter_by(name='校长').first().id
    r = c.post('/perm-groups/save-perms', json={'items': [
        {'id': xz_gid, 'levels': {'students:view': 'read', 'dormitory:view': 'read',
                                  'grades:manage': 'write', 'points:view': 'read',
                                  'academic:view': 'read'}}]},
        headers={'X-CSRFToken': csrf})
    check('矩阵保存 API', r.status_code == 200 and r.get_json()['success'])
    with app.app_context():
        xz_g = PermissionGroup.query.get(xz_gid)
        check('保存后校长教学管理=写入', 'grades.edit' in xz_g.get_menu_keys())
        check('保存后校长宿舍分配=不可见',
              'dormitory.assign' not in xz_g.get_menu_keys())

    r = c.post('/perm-groups/data-scope/save',
               json={'scopes': {str(xz_id): ['2026级']}},
               headers={'X-CSRFToken': csrf})
    check('数据范围保存 API', r.status_code == 200 and r.get_json()['success'])
    with app.app_context():
        xz = db.session.get(User, xz_id)
        check('保存后用户范围=仅2026级', user_grade_scope(xz) == ['2026级'])

    r = c.post('/perm-groups/save', json={'name': '宿管组长',
                                          'scope_type': 'school',
                                          'description': '测试自定义身份'},
               headers={'X-CSRFToken': csrf})
    check('新建自定义身份', r.status_code == 200 and r.get_json()['success'])
    with app.app_context():
        check('自定义身份落库', PermissionGroup.query.filter_by(
            name='宿管组长').first() is not None)

    # ---- 子功能细分：班主任可床位分配、不可宿舍分配 ----
    with app.app_context():
        bz_gid = PermissionGroup.query.filter_by(name='班主任组').first().id
    r = c.post('/perm-groups/save-perms', json={'items': [
        {'id': bz_gid, 'levels': {'students:view': 'read',
                                  'dormitory:view': 'read',
                                  'dormitory:assign': 'none',
                                  'dormitory:beds': 'write'}}]},
        headers={'X-CSRFToken': csrf})
    with app.app_context():
        bz_g = PermissionGroup.query.get(bz_gid)
        ks = set(bz_g.get_menu_keys())
        check('细分：班主任可床位分配、不可宿舍分配',
              'dormitory.beds' in ks and 'dormitory.assign' not in ks,
              str(sorted(ks)))

    # ---- 管理员组保护（不可改 / 不可删） ----
    with app.app_context():
        adm_gid = PermissionGroup.query.filter_by(name='管理员组').first().id
    r = c.post('/perm-groups/save-perms', json={'items': [
        {'id': adm_gid, 'levels': {'students:view': 'none'}}]},
        headers={'X-CSRFToken': csrf})
    check('管理员组权限保存被跳过', r.status_code == 200
          and '跳过' in r.get_json()['message'], r.get_json()['message'])
    with app.app_context():
        adm_g = PermissionGroup.query.get(adm_gid)
        check('管理员组权限未被改动', 'students.view' in adm_g.get_menu_keys())
    r = c.post(f'/perm-groups/{adm_gid}/delete', headers={'X-CSRFToken': csrf})
    check('管理员组删除被拒绝', r.status_code == 400
          and '保留' in r.get_json()['message'])
    r = c.post('/perm-groups/save', json={'id': adm_gid, 'name': '改名尝试',
                                          'scope_type': 'school'},
               headers={'X-CSRFToken': csrf})
    check('管理员组修改被拒绝', r.status_code == 400)

    # ---- 管理员账号保护（不可禁用 / 不可改角色） ----
    with app.app_context():
        adm_uid = User.query.filter_by(username='admin').first().id
    r = c.post(f'/users/{adm_uid}/toggle', data={'csrf_token': csrf},
               follow_redirects=True)
    with app.app_context():
        check('管理员账号不可禁用', db.session.get(User, adm_uid).is_active)

    # ---- 教务写入拆分验证 ----
    c.get('/logout', follow_redirects=True)
    csrf = get_csrf(c)
    c.post('/login', data={'csrf_token': csrf, 'username': 'jwy001',
                           'password': 'pw123456'}, follow_redirects=True)
    r = c.get('/academic/teachers')
    check('教务员可访问教务页', r.status_code == 200, str(r.status_code))
    r = c.get('/academic/inspection')
    check('教务员可访问查课页', r.status_code == 200)
    c.get('/logout', follow_redirects=True)

    csrf = get_csrf(c)
    c.post('/login', data={'csrf_token': csrf, 'username': 'xz001',
                           'password': 'pw123456'}, follow_redirects=True)
    r = c.get('/academic/teachers')
    check('校长（只读）可查看教务页', r.status_code == 200, str(r.status_code))
    r = c.post('/academic/teachers/import/upload', data={})
    check('校长（只读）不可导入（非 200）', r.status_code in (400, 403),
          str(r.status_code))

print(f'\n===== 权限验证完成：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

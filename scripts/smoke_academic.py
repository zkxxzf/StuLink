# -*- coding: utf-8 -*-
"""教务模块 + 教师工作台端到端冒烟测试：使用临时目录数据库，不影响本地 data/

覆盖：页面可访问性、教师名单导入（模板/校验/预览/确认/二次导入幂等）、查课录入、
业绩录入与审核流程、教师工作台（登录/提交业绩/改手机号）、角色权限（403/200）。
运行：python scripts/smoke_academic.py
"""
import io
import os
import re
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_academic_smoke_')

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
config_mod.Config.SECRET_KEY = 'smoke-test-secret'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User, PermissionGroup  # noqa: E402
from app.models.academic import Teacher, TeacherAchievement, InspectionRecord  # noqa: E402
from app.modules.academic.routes import teachers as ac_teachers  # noqa: E402

app = create_app()
ok = 0
fail = 0


def check(name, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'[PASS] {name}')
    else:
        fail += 1
        print(f'[FAIL] {name} {extra}')


def get_csrf(c, url='/login'):
    r0 = c.get(url)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
    return m.group(1) if m else ''


def login(c, username, password):
    csrf = get_csrf(c)
    return c.post('/login', data={'csrf_token': csrf, 'username': username,
                                  'password': password}, follow_redirects=True), csrf


with app.test_client() as c:
    # ---- admin 登录 ----
    r, csrf = login(c, 'admin', 'admin123')
    check('admin 登录', r.status_code == 200 and '退出' in r.get_data(as_text=True))

    # ---- 1) 新页面可访问 ----
    for url in ('/', '/users/', '/academic/teachers', '/academic/timetable',
                '/academic/inspection', '/academic/achievements', '/workbench/'):
        r = c.get(url)
        check(f'页面 {url}', r.status_code == 200, f'status={r.status_code}')

    # ---- 2) 模板下载 ----
    r = c.get('/academic/teachers/template.xlsx')
    check('导入模板下载', r.status_code == 200 and r.data[:2] == b'PK')

    # ---- 3) 教师名单导入（含错误行与身份证校验） ----
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['姓名', '身份证号', '手机号', '学科', '备注'])
    ws.append(['张老师', '11010519491231002X', '13800000001', '语文', ''])
    ws.append(['李老师', '', '13800000002', '数学', ''])
    ws.append(['王老师', '123456', '', '英语', '身份证错误'])
    data1 = io.BytesIO()
    wb.save(data1)
    data1_bytes = data1.getvalue()
    r = c.post('/academic/teachers/import/upload',
               data={'csrf_token': csrf, 'file': (io.BytesIO(data1_bytes), '教师名单.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    html = r.get_data(as_text=True)
    check('导入预览页', r.status_code == 200 and '导入预览' in html)
    check('无效身份证行被拦截提示', '身份证号无效' in html)
    check('预览含新建账号输入框', 'un_0' in html)

    # ---- 4) 确认导入 ----
    token = list(ac_teachers._DRAFT.keys())[-1]
    r = c.post('/academic/teachers/import/confirm',
               data={'csrf_token': csrf, 'token': token}, follow_redirects=True)
    html = r.get_data(as_text=True)
    check('导入完成页', r.status_code == 200 and '导入完成' in html)
    check('结果页展示初始密码', '初始密码' in html)

    with app.app_context():
        ts = Teacher.query.all()
        check('教师落库 2 人（错误行跳过）', len(ts) == 2,
              str([t.name for t in ts]))
        check('教师编号唯一且 T 开头',
              all(t.teacher_uid.startswith('T') for t in ts)
              and len({t.teacher_uid for t in ts}) == 2)
        t1 = Teacher.query.filter_by(name='张老师').first()
        check('身份证加密存储（非明文）',
              t1 and t1.id_card_enc
              and '11010519491231002X' not in t1.id_card_enc)
        check('账号自动创建并关联', t1 and t1.user_id is not None)
        u1 = User.query.filter_by(real_name='张老师').first()
        check('登录账号=手机号', u1 is not None and u1.username == '13800000001')
        u2 = User.query.filter_by(real_name='李老师').first()
        check('无手机号用拼音/ts 账号', u2 is not None and u2.username != u1.username)

    # ---- 5) 二次导入同一文件：识别为更新、不重复建人 ----
    r = c.post('/academic/teachers/import/upload',
               data={'csrf_token': csrf, 'file': (io.BytesIO(data1_bytes), '教师名单.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    token2 = list(ac_teachers._DRAFT.keys())[-1]
    r = c.post('/academic/teachers/import/confirm',
               data={'csrf_token': csrf, 'token': token2}, follow_redirects=True)
    with app.app_context():
        check('二次导入不重复建人', Teacher.query.count() == 2,
              str(Teacher.query.count()))

    # ---- 6) 查课记录 ----
    with app.app_context():
        tuid = Teacher.query.filter_by(name='张老师').first().teacher_uid
    r = c.post('/academic/inspection/add',
               data={'csrf_token': csrf, 'inspect_date': '2026-09-16', 'period': 3,
                     'grade': '2025级', 'teacher_uid': tuid,
                     'class_name': '01班', 'result': 'normal'},
               follow_redirects=True)
    with app.app_context():
        check('查课记录落库', InspectionRecord.query.count() == 1)
    r = c.get('/academic/inspection')
    check('查课页含统计', r.status_code == 200
          and '本月查课次数' in r.get_data(as_text=True))

    # ---- 7) 教务录入业绩（直接生效） ----
    r = c.post('/academic/achievements/add',
               data={'csrf_token': csrf, 'teacher_uid': tuid, 'category': 'honor',
                     'title': '市级优秀教师', 'level': '市级'}, follow_redirects=True)
    with app.app_context():
        rec = TeacherAchievement.query.first()
        check('教务业绩直接生效', rec is not None and rec.status == 'approved')

    # ---- 8) 教师工作台：登录、查看、提交业绩、改手机号 ----
    with app.app_context():
        u = User.query.filter_by(real_name='张老师').first()
        u.set_password('pw123456')
        u.must_change_pwd = False
        db.session.commit()
    c.get('/logout', follow_redirects=True)
    r, csrf2 = login(c, '13800000001', 'pw123456')
    r = c.get('/workbench/')
    html = r.get_data(as_text=True)
    check('教师工作台可见本人信息', r.status_code == 200 and '张老师' in html)
    check('工作台显示我的业绩', '市级优秀教师' in html)

    r = c.post('/workbench/achievement/add',
               data={'csrf_token': csrf2, 'category': 'certificate',
                     'title': '普通话证书', 'level': '省级'}, follow_redirects=True)
    with app.app_context():
        check('教师提交业绩进入待审核',
              TeacherAchievement.query.filter_by(status='pending').count() == 1)

    r = c.post('/workbench/phone',
               data={'csrf_token': csrf2, 'new_phone': '13900000009',
                     'current_password': 'pw123456'}, follow_redirects=True)
    with app.app_context():
        u2 = User.query.filter_by(real_name='张老师').first()
        check('手机号修改同步登录名', u2.username == '13900000009', u2.username)
        check('教师名单手机号同步',
              Teacher.query.filter_by(name='张老师').first().phone == '13900000009')
    c.get('/logout', follow_redirects=True)

    # ---- 9) 权限：任课教师访问教务 403，工作台 200 ----
    with app.app_context():
        pg_teacher = PermissionGroup.query.filter_by(name='任课教师组').first()
        t2 = User(username='renke_x', real_name='任课测试', role='teacher',
                  permission_group_id=pg_teacher.id)
        t2.set_password('pw123456')
        t2.must_change_pwd = False
        db.session.add(t2)
        db.session.commit()
    r, _ = login(c, 'renke_x', 'pw123456')
    r = c.get('/academic/teachers')
    check('无权限角色访问教务（403）', r.status_code == 403, f'status={r.status_code}')
    r = c.get('/workbench/')
    check('无权限角色可访问工作台', r.status_code == 200)

print(f'\n===== 教务冒烟测试完成：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

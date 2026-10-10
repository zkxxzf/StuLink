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
    # 2026-10-10：教务分库——查课 / 业绩 / 表单 各自独立库
    'inspection': 'sqlite:///' + os.path.join(_TMP, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_TMP, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_TMP, 'forms.db'),
    # 2026-10-10：补 system 绑定 —— 启动建表会遍历 'system'，缺它会有 WARN 噪音
    'system': 'sqlite:///' + os.path.join(_TMP, 'system.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
    # 2026-10-10：教师工作台首页读 timetable.db（课表/调课），缺该 bind 会让
    # 教师身份访问 /workbench/ 直接 500（此前两条工作台断言失败的根因）
    'timetable': 'sqlite:///' + os.path.join(_TMP, 'timetable.db'),
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
    r = c.post('/login', data={'csrf_token': csrf, 'username': username,
                               'password': password}, follow_redirects=True)
    # M-2：登录时服务端会清空并重建会话，登录前取的 token 已失效 → 重新取
    m = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', r.get_data(as_text=True))
    return r, (m.group(1) if m else csrf)


# H-1：内置 admin 初始口令已改为随机生成，冒烟脚本自建管理员账号
with app.app_context():
    if not User.query.filter_by(username='smokeadm').first():
        _adm = User(username='smokeadm', real_name='冒烟管理员', role='admin',
                    must_change_pwd=False)
        _adm.set_password('SmokeAdm#2026')
        db.session.add(_adm)
        db.session.commit()

with app.test_client() as c:
    # ---- admin 登录 ----
    r, csrf = login(c, 'smokeadm', 'SmokeAdm#2026')
    check('admin 登录', r.status_code == 200 and '退出' in r.get_data(as_text=True))

    # ---- 1) 新页面可访问 ----
    for url in ('/', '/users/', '/academic/teachers',
                '/academic/inspection', '/academic/achievements', '/workbench/'):
        r = c.get(url)
        check(f'页面 {url}', r.status_code == 200, f'status={r.status_code}')

    # 2026-10-10：教务管理侧新增同款「任课教师映射」（复用成绩管理视图函数与源数据，
    # URL 与接口都在 /academic 前缀下，页面不跳转到成绩模块）
    r = c.get('/academic/')
    check('教务工作台含任课教师映射入口',
          r.status_code == 200 and '/academic/teacher-links' in r.get_data(as_text=True),
          f'status={r.status_code}')
    r = c.get('/academic/teacher-links')
    html = r.get_data(as_text=True)
    check('教务任课映射页可访问且接口归教务',
          r.status_code == 200 and 'tcApp' in html
          and '/academic/teacher-links/api' in html,
          f'status={r.status_code}')
    r = c.get('/academic/teacher-links/api')
    check('教务侧矩阵接口可用',
          r.status_code == 200 and 'data' in (r.get_json() or {}),
          f'status={r.status_code}')
    r = c.get('/academic/teacher-links/template.xlsx')
    check('教务侧导入模板可下载',
          r.status_code == 200 and r.data[:2] == b'PK', f'status={r.status_code}')
    r = c.get('/academic/teacher-links/import')
    html = r.get_data(as_text=True)
    check('教务侧导入页表单归教务',
          r.status_code == 200 and '/academic/teacher-links/import/upload' in html,
          f'status={r.status_code}')
    # 矩阵页整页不出现 /grades/ 链接：教务内的页面不把用户送回成绩模块
    r = c.get('/academic/teacher-links')
    check('教务映射页不跳成绩模块',
          r.status_code == 200 and '/grades/' not in r.get_data(as_text=True),
          f'status={r.status_code}')
    r = c.get('/grades/teachers')
    html = r.get_data(as_text=True)
    check('成绩管理侧任课映射不受影响',
          r.status_code == 200 and '/grades/api/teachers' in html,
          f'status={r.status_code}')
    # 下线「任课安排」时，同文件的路由（备课组长）必须不受影响
    r = c.get('/academic/leaders')
    check('备课组长页未受影响',
          r.status_code == 200 and '备课组长' in r.get_data(as_text=True),
          f'status={r.status_code}')
    # 2026-10-10：课表矩阵 CSS 从内联抽成静态文件（每页省 ~8KB 且可被浏览器缓存）。
    # 页面是否引用它由回归 OVERVIEW 项断言（本脚本账号对课表页权限路径不同）。
    r = c.get('/static/css/schedule_matrix.css')
    check('课表矩阵 CSS 静态文件可访问',
          r.status_code == 200 and b'.ovw-block' in r.data,
          f'status={r.status_code}')

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
    # 2026-10-10：课表卡改读 timetable.db（此前读旧表恒为空）
    check('工作台课表卡接入真实课表', '查看完整课表' in html
          and '课表导入功能将在后续版本开放' not in html)

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

    # ---- 8.1) 我的成绩：口径收紧到本人任教 + 越权 403（2026-10-10） ----
    with app.app_context():
        from datetime import date as _date

        from app.models.grades import Exam, ExamScore, TeacherSubjectLink
        u_chk = User.query.filter_by(real_name='张老师').first()
        link = TeacherSubjectLink(grade='2025级', class_name='01班', subject='数学',
                                  user_id=u_chk.id, active=True)
        exam = Exam(name='期中考试', exam_type='期中', grade='2025级',
                    exam_date=_date(2026, 10, 20), status='imported')
        db.session.add_all([link, exam])
        db.session.commit()
        db.session.add_all([
            ExamScore(exam_id=exam.id, student_no='2025001', student_name='甲同学',
                      grade='2025级', class_name='01班', subject='数学', score=90.0),
            ExamScore(exam_id=exam.id, student_no='2025002', student_name='乙同学',
                      grade='2025级', class_name='01班', subject='数学', score=55.0),
            ExamScore(exam_id=exam.id, student_no='2025003', student_name='丙同学',
                      grade='2025级', class_name='02班', subject='数学', score=40.0),
        ])
        db.session.commit()
        exam_id = exam.id

    r = c.get('/workbench/grades')
    html = r.get_data(as_text=True)
    check('我的成绩页只列任教班科', r.status_code == 200 and '我的成绩' in html
          and '2025级01班' in html and '年级名次' in html)
    r = c.get(f'/workbench/api/grade-detail?exam_id={exam_id}'
              '&class_name=01班&subject=数学')
    detail = r.get_json() or {}
    # 注：jsonify 默认把中文转义成 \uXXXX，必须用 get_json() 解析后再断言姓名
    check('任教班科可看学生明细', r.status_code == 200
          and any(row.get('student_name') == '甲同学' for row in detail.get('rows', [])),
          f'status={r.status_code}')
    r = c.get(f'/workbench/api/grade-detail?exam_id={exam_id}'
              '&class_name=02班&subject=数学')
    check('非任教班科明细 403', r.status_code == 403, f'status={r.status_code}')
    r = c.get('/workbench/api/grade-trend?class_name=01班&subject=数学&grade=2025级')
    check('任教班科走势可看', r.status_code == 200
          and '"points"' in r.get_data(as_text=True))

    # ---- 8.2) 我的课表：版式从教务端搬进工作台（2026-10-10） ----
    r = c.get('/workbench/my-schedule')
    html = r.get_data(as_text=True)
    check('工作台我的课表可访问', r.status_code == 200 and '我的课表' in html,
          f'status={r.status_code}')
    # my_mode 下不渲染教务专属入口（导出/学期管理），避免教师点了 403
    check('课表页不含教务专属入口', '学期管理' not in html and '导出' not in html)
    r = c.get('/workbench/')
    check('工作台侧栏含我的课表入口', '我的课表' in r.get_data(as_text=True))
    # 2026-10-10：入口可见性 = 路由权限 —— 任课教师无 grades.teachers，看不到入口也进不去
    r = c.get('/academic/')
    check('教务入口按权限隐藏（无 grades.teachers）',
          r.status_code == 200
          and '/academic/teacher-links' not in r.get_data(as_text=True),
          f'status={r.status_code}')
    r = c.get('/academic/teacher-links')
    check('无权限访问教务任课映射 403', r.status_code == 403, f'status={r.status_code}')
    c.get('/logout', follow_redirects=True)

    # ---- 9) 权限：无 academic 权限的身份访问教务 403，工作台 200 ----
    # 说明：原用例用「任课教师组」，但该组本身具备 academic.view（设计如此），
    # 断言应为 200；这里改用不含 academic.* 的「宿管组」来验证真正的越权拦截。
    with app.app_context():
        pg_dorm = PermissionGroup.query.filter_by(name='宿管组').first()
        t2 = User(username='suguan_x', real_name='宿管测试', role='dorm_manager',
                  permission_group_id=pg_dorm.id)
        t2.set_password('pw123456')
        t2.must_change_pwd = False
        db.session.add(t2)
        db.session.commit()
    r, _ = login(c, 'suguan_x', 'pw123456')
    r = c.get('/academic/teachers')
    check('无权限角色访问教务（403）', r.status_code == 403, f'status={r.status_code}')
    r = c.get('/workbench/')
    check('无权限角色可访问工作台', r.status_code == 200)

    # ---- 10) 已下线功能：教室课表 / 旧版课表 / 教务端我的课表 / 任课安排
    #          （2026-10-10 整功能删除，地址应 404；我的课表搬到教师工作台，
    #           教师-学科关系统一由「任课教师映射」维护） ----
    for url in ('/academic/timetable', '/academic/timetable/import',
                '/academic/schedule/rooms', '/academic/schedule/1/room',
                '/academic/my-schedule',
                '/academic/duty', '/academic/duty/export', '/academic/api/duty',
                '/academic/schedule/1/night-duty/auto'):
        r = c.get(url)
        check(f'已下线地址 404：{url}', r.status_code == 404, f'status={r.status_code}')

print(f'\n===== 教务冒烟测试完成：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

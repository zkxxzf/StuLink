# -*- coding: utf-8 -*-
"""成绩模块端到端冒烟测试：使用临时目录数据库，不影响本地 data/"""
import io
import os
import sys
import tempfile
import json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_TMP = tempfile.mkdtemp(prefix='stulink_grades_smoke_')

# 覆写数据目录：隔离测试
import config as config_mod
config_mod.BASE_DIR = _TMP
for k in ('SQLALCHEMY_DATABASE_URI', 'SQLALCHEMY_BINDS'):
    pass
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_TMP, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_TMP, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_TMP, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_TMP, 'points.db'),
    'academic': 'sqlite:///' + os.path.join(_TMP, 'academic.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
}
# config._get_secret_key 基于 BASE_DIR 读取密钥文件，临时目录下会自动生成
config_mod.Config.SECRET_KEY = 'smoke-test-secret'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Student, User, PermissionGroup  # noqa: E402
from app.models.grades import Exam, ExamScore, ExamBand, TeacherSubjectLink, SUBJECTS  # noqa: E402

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


with app.app_context():
    # ---- 造基础数据：学生（2025级 01班史政地 02班物化生） ----
    students = []
    for i in range(1, 13):
        if i <= 6:
            stu = Student(student_number=f'202500{i:02d}', name=f'历生{i:02d}',
                          gender='男', grade='2025级', class_name='01班',
                          subject_selection='史政地')
        else:
            stu = Student(student_number=f'202501{i - 6:02d}', name=f'物生{i:02d}',
                          gender='女', grade='2025级', class_name='02班',
                          subject_selection='物化生')
        students.append(stu)
        db.session.add(stu)
    db.session.commit()
    print('== 学生就绪 ==')

with app.test_client() as c:
    # ---- 登录 admin（先取 CSRF token） ----
    r0 = c.get('/login')
    import re
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
    csrf = m.group(1) if m else ''
    r = c.post('/login', data={'csrf_token': csrf, 'username': 'admin',
                               'password': 'admin123'}, follow_redirects=True)
    _html = r.get_data(as_text=True)
    check('admin 登录', r.status_code == 200 and '退出' in _html
          and '登录失败' not in _html, f'csrf={csrf!r} html={_html[:300]!r}')

    # ---- 建考试（名称=年级+日期自动由前端填充；后端接受任意 name） ----
    r = c.post('/grades/exams/new', data={
        'csrf_token': csrf, 'grade': '2025级', 'exam_date': '2026-01-15',
        'name': '2025级2026-01-15', 'exam_type': '期末'}, follow_redirects=True)
    _ce = r.get_data(as_text=True)
    check('新建考试', r.status_code == 200 and '考试已创建' in _ce,
          f'status={r.status_code} html={_ce[:200]!r}')

    with app.app_context():
        exam = Exam.query.first()
        check('考试落库', exam is not None and exam.grade == '2025级'
              and exam.name == '2025级2026-01-15')
        eid = exam.id

    # ---- 下载模板 ----
    r = c.get('/grades/template.xlsx')
    check('模板下载', r.status_code == 200 and r.data[:2] == b'PK')

    # ---- 构造成绩 Excel（模板 A 全列，01班史政地、02班物化生） ----
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['学号', '姓名', '年级', '班级', '总分', '语文', '数学', '外语',
               '物理', '历史', '化学', '生物', '政治', '地理'])
    # 01班 历史方向（物理/化学/生物 空）；总分=六科和（历史93+政治88+地理86+语外数）
    hist_rows = [
        ('20250001', '历生01', 612.5, [119.5, 125, 121, None, 93, None, None, 88, 86]),
        ('20250002', '历生02', 590.0, [110, 118, 115, None, 88, None, None, 92, 87]),
        ('20250003', '历生03', 571.5, [108, 110, 112, None, 85, None, None, 80, 76.5]),
        ('20250004', '历生04', 553.0, [102, 105, 108, None, 82, None, None, 78, 78]),
        ('20250005', '历生05', 531.0, [99, 100, 104, None, 78, None, None, 76, 74]),
        ('20250006', '历生06', 512.0, [95, 96, 100, None, 75, None, None, 72, 74]),
    ]
    phys_rows = [
        ('20250101', '物生01', 641.0, [118, 142, 121, 90, None, 88, 82, None, None]),
        ('20250102', '物生02', 610.5, [112, 130, 118, 84, None, 82, 76, None, None]),
        ('20250103', '物生03', 588.0, [108, 124, 115, 79, None, 78, 72, None, None]),
        ('20250104', '物生04', 566.5, [104, 116, 112, 74, None, 74, 68, None, None]),
        ('20250105', '物生05', 541.0, [100, 108, 108, 70, None, 70, 62, None, None]),
        ('20250106', '物生06', 522.0, [96, 102, 104, 68, None, 66, 60, None, None]),
    ]
    for no, name, total, vals in hist_rows + phys_rows:
        ws.append([no, name, '2025级', '01班' if no.startswith('202500') else '02班',
                   total] + vals)
    data_io = io.BytesIO()
    wb.save(data_io)
    data_io.seek(0)

    # ---- 上传解析 ----
    r = c.post(f'/grades/exams/{eid}/import/upload',
               data={'mode': 'A', 'csrf_token': csrf, 'file': (data_io, '期中成绩.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    html = r.get_data(as_text=True)
    check('上传解析报告', r.status_code == 200 and '可导入' in html and '确认导入' in html)

    # ---- 确认导入 ----
    with app.app_context():
        token = Exam.query.get(eid).import_token
    r = c.post(f'/grades/exams/{eid}/import/confirm', data={'csrf_token': csrf, 'token': token},
               follow_redirects=True)
    check('确认导入', r.status_code == 200 and '新增 12 人' in r.get_data(as_text=True))

    with app.app_context():
        n_total = ExamScore.query.filter_by(exam_id=eid, subject='总分').count()
        n_all = ExamScore.query.filter_by(exam_id=eid).count()
        check('成绩行数（12生×7行）', n_total == 12 and n_all == 12 * 7, f'{n_total}/{n_all}')
        top = ExamScore.query.filter_by(exam_id=eid, subject='总分') \
            .order_by(ExamScore.score.desc()).first()
        check('排名第1为最高分', top.student_no == '20250101' and top.rank_dir == 1,
              f'{top.student_no} rank={top.rank_dir}')
        # 历史方向最高
        htop = ExamScore.query.filter_by(exam_id=eid, subject='总分', direction='历史') \
            .order_by(ExamScore.rank_dir).first()
        check('历史方向第1', htop.student_no == '20250001' and htop.rank_dir == 1)
        check('考试状态 imported', Exam.query.get(eid).status == 'imported')

    # ---- 分析 API ----
    r = c.get(f'/grades/api/options')
    check('options API', r.status_code == 200 and r.get_json()['success'])
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    js = r.get_json()
    tables = js['data']['tables']
    check('年级分析 API 表', r.status_code == 200 and 'class_summary' in tables
          and len(tables['class_summary']['rows']) == 2)
    charts = js['data']['charts']
    check('年级分析 API 图（含箱线/堆叠/折线）',
          'box' in charts and 'seg_stack' in charts and 'trend_line' in charts)
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=01%E7%8F%AD')
    js = r.get_json()
    t2 = js['data']['tables']
    check('班级分析 API', 'class_meta' in t2 and 'student_detail' in t2
          and len(t2['student_detail']['rows']) == 6)
    r = c.get(f'/grades/api/analysis/subject?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6')
    js = r.get_json()
    check('学科分析 API（含热力）',
          'class_compare' in js['data']['tables'] and 'heatmap' in js['data']['charts'])

    # ---- 班级对比分析（第五 tab） ----
    from urllib.parse import quote
    pair = quote('01班') + ',' + quote('02班')
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}&classes={pair}')
    js = r.get_json()
    ct = js.get('data', {}).get('tables', {}) if js.get('success') else {}
    check('班级对比 API（两班）', r.status_code == 200 and 'cmp_overview' in ct
          and len(ct['cmp_overview']['rows']) == 2, str(r.get_data(as_text=True)[:200]))
    check('班级对比：各班核心指标含极差/分差',
          ct.get('cmp_overview', {}).get('rows') and
          'range' in ct['cmp_overview']['rows'][0] and 'diff' in ct['cmp_overview']['rows'][0])
    check('班级对比：科目/及格率/优秀率三张横表 + 图表规格',
          all(k in ct for k in ('cmp_subject', 'cmp_pass', 'cmp_good'))
          and bool(ct['cmp_subject'].get('chartHint')), str(list(ct.keys())))
    check('班级对比：堆叠分段表图表规格',
          bool(ct.get('cmp_score_seg', {}).get('chartHint'))
          and ct['cmp_score_seg']['chartHint']['type'] == 'stack')
    check('班级对比：五数概括 + 箱线图规格',
          'cmp_box' in ct and bool(ct['cmp_box'].get('chartHint'))
          and ct['cmp_box']['chartHint']['type'] == 'boxplot')
    # 01班只考史政地、02班只考物化生 → 科目对比表中未考班级该科应为空
    rows_s = ct.get('cmp_subject', {}).get('rows', [])
    hist_row = next((x for x in rows_s if x['subject'] == '历史'), None)
    check('班级对比：未考科目置空（历史仅01班有分）',
          hist_row is not None and hist_row['c0'] is not None and hist_row['c1'] is None,
          str(hist_row))
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}&classes={quote("01班")}')
    check('班级对比：不足两个班返回 400', r.status_code == 400)
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}'
              f'&classes={quote("01班")},{quote("不存在的班")}')
    check('班级对比：过滤不存在班级后 400', r.status_code == 400)
    r = c.get(f'/grades/export/compare?exam_id={eid}&classes={pair}')
    check('导出班级对比 Excel', r.status_code == 200 and r.data[:2] == b'PK')

    # ---- 分层模板应用 ----
    r = c.post('/grades/api/bands/apply', json={'exam_id': eid, 'template': '4',
                                                'both': True},
               headers={'X-CSRFToken': csrf})
    check('分层模板应用', r.status_code == 200 and r.get_json()['success'],
          f'status={r.status_code} body={r.get_data(as_text=True)[:300]}')
    with app.app_context():
        bands = ExamBand.query.filter_by(exam_id=eid, direction='物理') \
            .order_by(ExamBand.seq).all()
        check('分层落库4层', len(bands) == 4 and bands[-1].lower_value == 0)
        # 20% 比例 → 物理 6 人 → 第2名分数（ceil(6*0.2)=2 → 610.5）
        check('比例换算正确', abs(bands[0].lower_value - 610.5) < 0.01,
              f"lower={bands[0].lower_value}")
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    rows = r.get_json()['data']['tables']['class_summary']['rows']
    check('分层列进入汇总表',
          'l_0' in rows[0] and rows[0]['l_0'] is not None, str(rows[0]))
    # 分层分布校验：物理(02班)6 人，前20%线=610.5 → 优秀 2 人（641/610.5）；历史(01班)前20%=590 → 优秀 2 人
    r2 = {row['class_name']: row for row in rows}
    check('分层分布正确（优秀层=前20%）',
          r2['01班'].get('l_0') == '2' and r2['02班'].get('l_0') == '2',
          str({k: v.get('l_0') for k, v in r2.items()}))

    # ---- 分批补导（模式 A 只更新 02班 数学；历史方向不动） ----
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.append(['学号', '姓名', '年级', '班级', '数学'])
    ws2.append(['20250101', '物生01', '2025级', '02班', 145])
    io2 = io.BytesIO()
    wb2.save(io2)
    io2.seek(0)
    r = c.post(f'/grades/exams/{eid}/import/upload',
               data={'mode': 'A', 'csrf_token': csrf, 'file': (io2, '补数学.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    html = r.get_data(as_text=True)
    with app.app_context():
        d2 = json.loads(Exam.query.get(eid).import_draft)
        check('分批补导解析（差异预览：更新1条）',
              r.status_code == 200 and d2['diff']['updated_rows'] == 1,
              str(d2['diff']))
    with app.app_context():
        token = Exam.query.get(eid).import_token
    c.post(f'/grades/exams/{eid}/import/confirm', data={'csrf_token': csrf, 'token': token},
           follow_redirects=True)
    with app.app_context():
        m = ExamScore.query.filter_by(exam_id=eid, student_no='20250101',
                                      subject='数学').first()
        total = ExamScore.query.filter_by(exam_id=eid, student_no='20250101',
                                          subject='总分').first()
        check('覆盖生效+总分重算', m.score == 145
              and abs(total.score - (118 + 145 + 121 + 90 + 88 + 82)) < 0.01,
              f'math={m.score} total={total.score}')

    # ---- 教师映射 + 自动开户（含班主任列：正班/副班1 与班型设置同步） ----
    wb3 = openpyxl.Workbook()
    ws3 = wb3.active
    ws3.append(['年级', '班级', '选科', '正班', '副班1',
                '语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理'])
    ws3.append(['2025级', '01班', '史政地', '班正师', '班副师',
                '王老师', '李老师', '张老师', '', '', '', '赵老师', '钱老师', '孙老师'])
    ws3.append(['2025级', '02班', '物化生', '物正师', '物副师',
                '王老师', '李老师', '张老师', '赵老师', '周老师', '吴老师', '', '', ''])
    io3 = io.BytesIO()
    wb3.save(io3)
    io3.seek(0)
    r = c.post('/grades/teachers/import/upload',
               data={'csrf_token': csrf, 'file': (io3, '教师安排表.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    html = r.get_data(as_text=True)
    check('教师安排表解析预览（任课+班主任）', '自动开户' in html and '班主任' in html)
    with app.app_context():
        # 无账号教师 8 位：王/李/张/赵/钱/孙/周/吴 → 全部 to_create
        preview = len(TeacherSubjectLink.query.all())
        check('尚未写库', preview == 0)
    # 取暂存 token（进程内 _DRAFT）
    from app.modules.grades.routes import teachers as teachers_routes
    ttoken = list(teachers_routes._DRAFT.keys())[-1]
    check('教师导入暂存 token', bool(ttoken))
    r = c.post('/grades/teachers/import/confirm', data={'csrf_token': csrf, 'token': ttoken},
               follow_redirects=True)
    html = r.get_data(as_text=True)
    check('教师导入确认（含自动开户）', '导入完成' in html and '新开账号' in html,
          f'body={html[:200]!r}')
    with app.app_context():
        n_link = TeacherSubjectLink.query.filter_by(active=True).count()
        names_8 = ['王老师', '李老师', '张老师', '赵老师', '钱老师', '孙老师', '周老师', '吴老师']
        n_users = User.query.filter(User.real_name.in_(names_8)).count()
        check('映射绑定（12条班科）', n_link == 12, f'links={n_link}')
        check('任课教师开户去重（8 个）', n_users == 8, f'users={n_users}')
        # 班主任同步（与 class-profile/UserClassLink 同源）
        from app.models import UserClassLink as UCL
        heads = User.query.filter(User.real_name.in_(['班正师', '班副师', '物正师', '物副师'])).all()
        check('班主任自动开户（4 个）', len(heads) == 4, f'heads={len(heads)}')
        check('班主任角色与组正确',
              all(h.role == 'homeroom_teacher' and h.permission_group
                  and h.permission_group.name == '班主任组' for h in heads))
        ucl = UCL.query.all()
        check('UserClassLink 已同步（4 条）',
              len(ucl) == 4 and all(l.grade == '2025级' for l in ucl),
              str([(l.class_name) for l in ucl]))
        # 班主任可看本班全部成绩：登录验证
        bz = User.query.filter_by(real_name='班正师').first()
        bz.set_password('pw123456')
        db.session.commit()
        # 姓名锁定校验
        import re
        un = User.query.filter_by(real_name='王老师').first()
        check('新账号已绑定任课教师组', un.permission_group is not None
              and un.permission_group.role == 'teacher')
        pwd_user = User.query.filter_by(real_name='李老师').first()
        check('初始密码可登录（强制改密标记）', pwd_user.must_change_pwd is True)
        check('姓名锁定：用户名拼音或 ts 前缀', un.username.startswith('ts')
              or bool(re.match(r'^[a-z0-9]+$', un.username)), un.username)

    # ---- 教师分析 API（admin 全量） ----
    r = c.get(f'/grades/api/analysis/teacher?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6')
    js = r.get_json()
    check('教师分析 API', 'teacher_data' in js['data']['tables']
          and js['data']['tables']['teacher_data']['rows']
          and len(js['data']['tables']['teacher_data']['rows']) == 2)

    # ---- 方向筛选（B1） ----
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}&direction=%E5%8E%86%E5%8F%B2')  # 历史
    js = r.get_json()['data']
    rows = js['tables']['class_summary']['rows']
    subs = js['tables']['subject_total']['rows']
    check('方向筛选-历史：仅历史班', len(rows) == 1 and rows[0]['class_name'] == '01班'
          and rows[0]['count'] == 6, str(rows))
    check('方向筛选-历史：无物理科', all(x['subject'] != '物理' for x in subs))
    check('方向筛选-历史：均值口径', rows[0]['avg'] == 561.7, str(rows[0]['avg']))
    check('方向筛选-历史：箱线仅01班', js['charts']['box']['xAxis'] == ['01班'])
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}&direction=%E7%89%A9%E7%90%86')  # 物理
    js = r.get_json()['data']
    rows = js['tables']['class_summary']['rows']
    subs = js['tables']['subject_total']['rows']
    check('方向筛选-物理：仅物理班且含物理科',
          len(rows) == 1 and rows[0]['class_name'] == '02班'
          and any(x['subject'] == '物理' for x in subs)
          and all(x['subject'] != '历史' for x in subs))
    r = c.get(f'/grades/api/analysis/subject?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6'
              + '&direction=%E5%8E%86%E5%8F%B2')
    t1 = r.get_json()['data']['tables']['class_compare']['rows']
    check('方向筛选-学科：仅历史班数据', len(t1) == 1 and t1[0]['class_name'] == '01班',
          str(t1))
    # 分层表方向列（subject T3）在过滤时只输出该方向
    t3 = r.get_json()['data']['tables']['layer_score']['rows']
    check('方向筛选-学科：分层仅历史方向', t3 and all(x['direction'] == '历史' for x in t3))

    # ---- 导出 Excel ----
    r = c.get(f'/grades/export/grade?exam_id={eid}')
    check('导出年级分析 Excel', r.status_code == 200 and r.data[:2] == b'PK')

    # ---- 页面可访问 ----
    for url in ('/grades/', '/grades/exams', f'/grades/exams/{eid}',
                f'/grades/exams/{eid}/import', f'/grades/exams/{eid}/bands',
                '/grades/teachers', '/grades/teachers/import'):
        r = c.get(url)
        check(f'页面 {url}', r.status_code == 200, str(r.status_code))

    # ==================== 角色权限矩阵（9.2 后端强制） ====================
    from app.models import UserClassLink
    from app.models.user import User as U

    def login_as(username):
        c.get('/logout', follow_redirects=True)
        r0 = c.get('/login')
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
        c.post('/login', data={'csrf_token': m.group(1) if m else '',
                               'username': username, 'password': 'pw123456'},
               follow_redirects=True)

    def logout():
        c.get('/logout', follow_redirects=True)

    with app.app_context():
        grp = {g.name: g for g in PermissionGroup.query.all()}
        h = User(username='banzhu01', real_name='测试班主任', role='homeroom_teacher',
                 grade='2025级', class_name='01班', permission_group=grp['班主任组'])
        h.set_password('pw123456')
        db.session.add(h)
        db.session.flush()
        db.session.add(UserClassLink(user_id=h.id, grade='2025级', class_name='01班'))
        t = User(username='renke01', real_name='测试任课教师', role='teacher',
                 permission_group=grp['任课教师组'])
        t.set_password('pw123456')
        db.session.add(t)
        db.session.flush()
        gld = User(username='nianji01', real_name='测试年级长', role='grade_leader',
                   grade='2025级', permission_group=grp['年级长组'])
        gld.set_password('pw123456')
        db.session.add(gld)
        db.session.flush()
        # 复用已导入的 01班数学 映射指向测试任课教师
        link01 = (TeacherSubjectLink.query
                  .filter_by(grade='2025级', class_name='01班', subject='数学').first())
        link01.user_id = t.id
        db.session.commit()
        tid = t.id

    # 班主任：仅 class tab，且只本班
    login_as('banzhu01')
    r = c.get('/grades/api/options')
    tabs = r.get_json()['data']['tabs']
    check('班主任 tabs 仅班级', tabs == {'grade': False, 'class': True,
                                        'subject': False, 'teacher': False,
                                        'compare': False})
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=01%E7%8F%AD')
    check('班主任可看本班分析', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=02%E7%8F%AD')
    check('班主任不可看别班（403）', r.status_code == 403)
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    check('班主任不可看年级分析（403）', r.status_code == 403)
    _pair2 = quote('01班') + ',' + quote('02班')
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}&classes={_pair2}')
    check('班主任不可看班级对比（403）', r.status_code == 403)
    r = c.get(f'/grades/export/compare?exam_id={eid}&classes={_pair2}')
    check('班主任不可导出班级对比（403）', r.status_code == 403)
    r = c.get(f'/grades/export/grade?exam_id={eid}')
    check('班主任不可导出年级分析（403）', r.status_code == 403)
    logout()

    # 任课教师：仅 teacher tab（本人 01班数学）
    login_as('renke01')
    r = c.get('/grades/api/options')
    tabs = r.get_json()['data']['tabs']
    check('教师 tabs 仅任课教师', tabs == {'grade': False, 'class': False,
                                          'subject': False, 'teacher': True,
                                          'compare': False})
    r = c.get(f'/grades/api/analysis/teacher?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6')
    js = r.get_json()['data']['tables']['teacher_data']
    check('教师仅见本人数据', js['rows'] and js['rows'][0]['teacher'] == '测试任课教师'
          and js['rows'][0]['class_name'] == '01班', str(js['rows']))
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    check('教师不可看年级分析（403）', r.status_code == 403)
    logout()

    # 年级长：全部 tab（仅本年级考试）
    login_as('nianji01')
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    check('年级长可看年级分析', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/subject?exam_id={eid}&subject=%E8%AF%AD%E6%96%87')
    check('年级长可看学科分析', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/teacher?exam_id={eid}')
    check('年级长可看教师分析（本年级12条）',
          r.status_code == 200 and len(r.get_json()['data']['tables']['teacher_data']['rows']) == 12)
    logout()

    # ===== 导入产生的班主任：可查看本班全部科目成绩（需求） =====
    with app.app_context():
        bzu = User.query.filter_by(real_name='班正师').first()
        bz_name = bzu.username
    login_as(bz_name)
    r = c.get('/grades/api/options')
    tabs = r.get_json()['data']['tabs']
    check('导入班主任 tabs 仅班级', tabs == {'grade': False, 'class': True,
                                             'subject': False, 'teacher': False,
                                             'compare': False},
          str(tabs))
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=01%E7%8F%AD')
    check('导入班主任可看本班全部科目（200）', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=02%E7%8F%AD')
    check('导入班主任不可看别班（403）', r.status_code == 403)
    logout()

    # ===== 班主任换人同步：移除不在本次名单的旧班主任并降级 =====
    def login_admin():
        c.get('/logout', follow_redirects=True)
        r0 = c.get('/login')
        m2 = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
        c.post('/login', data={'csrf_token': m2.group(1) if m2 else '',
                               'username': 'admin', 'password': 'admin123'},
               follow_redirects=True)
        return m2.group(1) if m2 else ''

    acsrf = login_admin()
    wb4 = openpyxl.Workbook()
    ws4 = wb4.active
    ws4.append(['年级', '班级', '选科', '正班', '副班1',
                '语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理'])
    ws4.append(['2025级', '01班', '史政地', '班正师', '',
                '王老师', '李老师', '张老师', '', '', '', '赵老师', '钱老师', '孙老师'])
    io4 = io.BytesIO()
    wb4.save(io4)
    io4.seek(0)
    c.post('/grades/teachers/import/upload',
           data={'csrf_token': acsrf, 'file': (io4, '安排表v2.xlsx')},
           content_type='multipart/form-data', follow_redirects=True)
    from app.modules.grades.routes import teachers as teachers_routes
    t2 = list(teachers_routes._DRAFT.keys())[-1]
    c.post('/grades/teachers/import/confirm', data={'csrf_token': acsrf, 'token': t2},
           follow_redirects=True)
    with app.app_context():
        from app.models import UserClassLink as UCL
        bf = User.query.filter_by(real_name='班副师').first()
        bz2 = User.query.filter_by(real_name='班正师').first()
        wz = User.query.filter_by(real_name='物正师').first()
        check('换人同步：班副师关联已移除',
              UCL.query.filter_by(user_id=bf.id).count() == 0)
        check('换人同步：班副师降为任课教师',
              bf.role == 'teacher' and bf.permission_group.name == '任课教师组',
              f'{bf.role}/{bf.permission_group.name if bf.permission_group else None}')
        check('换人同步：正班保留、他班不受影响',
              UCL.query.filter_by(user_id=bz2.id, class_name='01班').count() == 1
              and UCL.query.filter_by(user_id=wz.id, class_name='02班').count() == 1)

    # ==================== AI 分析（mock 转发，不真外呼） ====================
    import json as _json
    from app.modules.grades.services import ai_service as ai_svc
    ai_calls = []

    def fake_call_llm(cfg, messages):
        ai_calls.append({'cfg': cfg, 'messages': messages})
        return True, '# AI 测试报告\n\n## 总体情况\n- 参考人数 12\n- 均分正常'

    ai_svc.call_llm = fake_call_llm

    # 1) 无 Key 时 analyze → 400
    r = c.post('/grades/ai/analyze', json={'exam_id': eid}, headers={'X-CSRFToken': csrf})
    check('AI：未配置 Key 返回 400', r.status_code == 400
          and 'API Key' in r.get_json()['message'], str(r.status_code))

    # 2) 个人 Key：加密存储 + 掩码不回显明文
    r = c.post('/grades/ai/key', json={'provider': 'deepseek',
                                       'api_key': 'sk-test-secret-12345678'},
               headers={'X-CSRFToken': csrf})
    check('AI：个人 Key 保存', r.status_code == 200 and r.get_json()['success'])
    r = c.get('/grades/ai/key')
    d = r.get_json()['data']
    check('AI：掩码回显不含明文', d.get('configured') and '****' in d.get('masked', '')
          and 'sk-test-secret' not in d.get('masked', ''), str(d))
    with app.app_context():
        from app.models.grades import AiKey as AiK
        row = AiK.query.first()
        check('AI：库中密文非明文', row is not None
              and 'sk-test-secret' not in (row.api_key_enc or ''))

    # 3) 发送范围预览（权限收敛）
    r = c.get(f'/grades/ai/scope?exam_id={eid}')
    s = r.get_json()['data']
    check('AI：admin 范围=全年级12人', s['scope_students'] == 12
          and s['scope_desc'] == '2025级全年级' and s['key_source'] == 'personal')

    # 4) analyze（admin，mock）：payload 全量、报告落库
    r = c.post('/grades/ai/analyze', json={'exam_id': eid}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('AI：admin 生成成功', r.status_code == 200 and d['success']
          and 'AI 测试报告' in d['data']['content'])
    rid_admin = d['data']['report_id']
    check('AI：报告落库', rid_admin is not None)
    last = ai_calls[-1]
    payload = _json.loads(last['messages'][1]['content'].split('\n\n')[-1])
    check('AI：admin payload 12 名学生', len(payload['exam']['students']) == 12)
    check('AI：无上一场考试时 prev 为空', payload['prev'] is None)

    # 5) 全局兜底 Key：admin 配置，教师无需个人 Key
    r = c.post('/grades/ai/global-key', json={'provider': 'deepseek',
                                              'api_key': 'sk-global-abcdef'},
               headers={'X-CSRFToken': csrf})
    check('AI：全局 Key 保存', r.status_code == 200)
    # 恢复 01班数学 映射给测试任课教师（换人同步用例把其覆盖回李老师）
    with app.app_context():
        lk = (TeacherSubjectLink.query.filter_by(grade='2025级', class_name='01班',
                                                 subject='数学').first())
        ru = User.query.filter_by(username='renke01').first()
        lk.user_id = ru.id
        db.session.commit()

    # 6) 任课教师：仅本人班科（01班·数学），payload 科目仅数学
    login_as('renke01')
    r = c.get(f'/grades/ai/scope?exam_id={eid}')
    s = r.get_json()['data']
    check('AI：教师范围=01班', s['scope_students'] == 6
          and s['scope_desc'] == '本人任课：01班' and s['key_source'] == 'global', str(s))
    r = c.post('/grades/ai/analyze', json={'exam_id': eid}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('AI：教师经全局 Key 生成', r.status_code == 200 and d['success'])
    rid_teacher = d['data']['report_id']
    last = ai_calls[-1]
    payload = _json.loads(last['messages'][1]['content'].split('\n\n')[-1])
    stu0 = payload['exam']['students'][0]
    check('AI：教师 payload 仅6人且科目仅数学', len(payload['exam']['students']) == 6
          and list(stu0.get('subjects', {}).keys()) == ['数学'], str(stu0))
    check('AI：教师发送的是原始成绩字段',
          'total' in stu0 and 'rank_dir' in stu0 and 'name' in stu0 and 'no' in stu0)
    logout()

    # 恢复测试班主任身份（换人同步用例将其随旧班主任一并移除/降级）
    with app.app_context():
        bz = User.query.filter_by(username='banzhu01').first()
        bz.role = 'homeroom_teacher'
        from app.models import PermissionGroup as PG
        from app.models import UserClassLink as UCL2
        bz.permission_group_id = PG.query.filter_by(name='班主任组').first().id
        bz.grade = '2025级'
        bz.class_name = '01班'
        if not UCL2.query.filter_by(user_id=bz.id, grade='2025级',
                                    class_name='01班').first():
            db.session.add(UCL2(user_id=bz.id, grade='2025级', class_name='01班'))
        db.session.commit()

    # 7) 班主任：仅本班
    login_as('banzhu01')
    r = c.get(f'/grades/ai/scope?exam_id={eid}')
    s = r.get_json()['data']
    check('AI：班主任范围=本班', s['scope_students'] == 6
          and s['scope_desc'] == '所辖班级：01班', str(s))
    logout()

    # 8) 报告历史权限：本人可见、admin 全量、跨用户 403
    login_as('renke01')
    r = c.get('/grades/ai/reports/list')
    rows = r.get_json()['data']
    check('AI：教师报告列表仅本人', len(rows) == 1 and rows[0]['id'] == rid_teacher)
    r = c.get(f'/grades/ai/reports/{rid_admin}')
    check('AI：教师不可看他人报告（403）', r.status_code == 403)
    logout()
    acsrf = login_admin()
    r = c.get('/grades/ai/reports/list')
    rows = r.get_json()['data']
    check('AI：admin 报告列表全量', len(rows) == 2)
    # 9) 删除
    r = c.post(f'/grades/ai/reports/{rid_teacher}/delete', headers={'X-CSRFToken': acsrf})
    check('AI：admin 可删他人报告', r.status_code == 200)
    with app.app_context():
        from app.models.grades import AiReport as AiR
        check('AI：删除生效', AiR.query.get(rid_teacher) is None)
    logout()

print(f'\n===== 冒烟测试完成：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

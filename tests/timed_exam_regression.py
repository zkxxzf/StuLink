# -*- coding: utf-8 -*-
"""限时练回归：独立表 / 考号匹配 / 缺考摒弃 / 名次区间 / 权限分层。

覆盖（2026-10-20 用户口径）：
1. 限时练写独立表，普通考试列表与 `exams` 表零影响（老代码零侵入）；
2. 导入优先按【考号】匹配 `students.zhixue_no`，学号兜底；
3. 「缺考」等文字 → 视为无成绩（不报错、不落库、页面不显示）；
4. 分析只做单科：各班平均分 + 各班落在名次区间内的人数；
5. 权限：普通任课教师可导入；改分 / 删除仅年级长（及管理员）。

运行：py -3.12 tests/timed_exam_regression.py   （退出码 0 = 全绿）
"""
import io
import os
import re
import shutil
import sys
import tempfile
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_timed_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
_DATA = os.path.join(_TMP, 'data')
os.makedirs(_DATA, exist_ok=True)
config_mod.Config.SQLALCHEMY_DATABASE_URI = ('sqlite:///' +
                                             os.path.join(_DATA, 'system.db'))
config_mod.Config.SQLALCHEMY_BINDS = {
    k: 'sqlite:///' + os.path.join(_DATA, f'{k}.db')
    for k in ('dormitory', 'history', 'grades', 'points', 'morality', 'academic',
              'inspection', 'achievement', 'forms', 'portrait', 'system',
              'timetable', 'timed')
}
config_mod.Config.SECRET_KEY = 'timed-regression-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')

import openpyxl  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PermissionGroup, Student, User  # noqa: E402
from app.models.grades import Exam, TeacherSubjectLink  # noqa: E402
from app.models.timed import TimedExam, TimedScore  # noqa: E402
from app.modules.grades.services import timed_import  # noqa: E402

app = create_app()
app.config['WTF_CSRF_ENABLED'] = False

_ok = 0
_fail = 0
PW = 'pw123456'
GRADE = '2025级'


def case(name, cond, extra=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f'[PASS] {name}')
    else:
        _fail += 1
        print(f'[FAIL] {name} {extra}')


def _xlsx(headers, rows):
    """构造导入用 xlsx（BytesIO）"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# 4 名学生：考号 8 位文本，两个班；分数 120 / 110 / 100 / 90
STUDENTS = (
    ('20250001', '张三', '59468662', '01班', 120),
    ('20250002', '李四', '59468663', '01班', 110),
    ('20250003', '王五', '59468664', '02班', 100),
    ('20250004', '赵六', None, '02班', 90),      # 无考号 → 走学号兜底
)


def _seed():
    with app.app_context():
        db.create_all()
        groups = {g.name: g for g in PermissionGroup.query.all()}
        admin = User.query.filter_by(username='admin').first()
        admin.set_password(PW)
        admin.must_change_pwd = False
        for no, name, zx, cls, _score in STUDENTS:
            db.session.add(Student(
                name=name, gender='男', student_number=no, zhixue_no=zx,
                grade=GRADE, class_name=cls, subject_selection='物化生'))
        teacher = User(username='t1', real_name='任课老师', role='teacher',
                       permission_group=groups['任课教师组'],
                       must_change_pwd=False)
        teacher.set_password(PW)
        leader = User(username='gl', real_name='年级长', role='grade_leader',
                      grade=GRADE, permission_group=groups['年级长组'],
                      must_change_pwd=False)
        leader.set_password(PW)
        db.session.add_all([teacher, leader])
        db.session.flush()
        db.session.add(TeacherSubjectLink(
            grade=GRADE, class_name='01班', subject='语文',
            user_id=teacher.id, active=True))
        db.session.commit()


def _login(client, username):
    client.get('/logout')
    r = client.post('/login', data={'username': username, 'password': PW},
                    follow_redirects=True)
    return r


def _upload(client, tid, rows, headers=None, subject='语文'):
    """上传模板文件（返回响应）"""
    buf = _xlsx(headers or ['考号', '学号', '姓名', '班级', '分数'], rows)
    return client.post(f'/grades/timed/{tid}/import/upload',
                       data={'subject': subject, 'file': (buf, 'scores.xlsx')},
                       content_type='multipart/form-data',
                       follow_redirects=True)


def _timed_id():
    with app.app_context():
        exam = TimedExam.query.order_by(TimedExam.id.desc()).first()
        return exam.id if exam else None


def test_parse_unit():
    """解析器单测：缺考文字、重复考号、越界分数"""
    rows = [['59468662', '张三', '01班', 120],
            ['59468663', '李四', '01班', '缺考'],
            ['59468662', '重复', '01班', 100],
            ['59468664', '王五', '02班', 999]]
    parsed = timed_import.parse_sheet(
        _xlsx(['考号', '姓名', '班级', '分数'], rows).read(), '语文', GRADE)
    case('解析：缺考文字按空值忽略（不报错）', parsed['stats']['absent_rows'] == 1,
         parsed['stats'])
    case('解析：重复考号被拦下', any('重复' in e['reason'] for e in parsed['errors']))
    case('解析：超满分被拦下', any('超出' in e['reason'] for e in parsed['errors']))
    case('解析：有效行 1 条', len(parsed['rows']) == 1, parsed['rows'])


def test_create_and_import(client):
    _login(client, 'admin')
    r = client.post('/grades/timed/new', data={
        'grade': GRADE, 'exam_date': '2026-10-20', 'name': '',
        'grade_semester': ''}, follow_redirects=True)
    case('新建限时练（名称自动生成）',
         r.status_code == 200 and '限时练已创建' in r.get_data(as_text=True))
    tid = _timed_id()
    case('限时练写独立表', tid is not None)
    with app.app_context():
        case('普通 exams 表零写入', Exam.query.count() == 0)
        exam = db.session.get(TimedExam, tid)
        want = f'{GRADE}20261020限时练No.1'
        case('新建即命名为「届+8位日期+限时练No.N」',
             exam is not None and exam.name == want, exam and exam.name)

    rows = [[s[2] or '', s[0], s[1], s[3], s[4]] for s in STUDENTS]
    rows[1][4] = '缺考'       # 李四缺考 → 不落库
    r = _upload(client, tid, rows)
    html = r.get_data(as_text=True)
    case('上传预检通过并进入报告页', r.status_code == 200 and '导入预览' in html,
         html[:200])

    with app.app_context():
        exam = db.session.get(TimedExam, tid)
        token = exam.import_token
    r = client.post(f'/grades/timed/{tid}/import/confirm',
                    data={'token': token}, follow_redirects=True)
    case('确认导入成功', r.status_code == 200 and '成绩已导入' in r.get_data(as_text=True))
    with app.app_context():
        scores = TimedScore.query.filter_by(exam_id=tid, subject='语文').all()
        case('按考号匹配 3 人（缺考 1 人不落库）', len(scores) == 3,
             [(s.student_no, s.score) for s in scores])
        by_no = {s.student_no: s for s in scores}
        case('无考号学生走学号兜底', '20250004' in by_no)
        case('考号快照落库', by_no['20250001'].zhixue_no == '59468662')
        case('名次 1,2,3 且同分口径正常',
             sorted(s.rank_grade for s in scores) == [1, 2, 3])
    return tid


def test_analysis(client, tid):
    r = client.get(f'/grades/timed/{tid}?tab=analysis')
    html = r.get_data(as_text=True)
    case('分析页 200（一次展开全部学科）', r.status_code == 200)
    case('分层按人数自适应（3 人 → 只到前10）',
         '前10' in html and '前550' not in html)
    case('分析页班均分：01班 120.0 / 02班 95.0',
         '120.0' in html and '95.0' in html)
    case('分析页每组左图右表（人数表 + 平均分表）',
         'data-chart=' in html and 'cntTable1' in html and 'avgTable1' in html)
    r2 = client.get(f'/grades/timed/{tid}?tab=students')
    html2 = r2.get_data(as_text=True)
    case('学生成绩（汇总表）200 且不出现「缺考」',
         r2.status_code == 200 and '缺考</' not in html2 and '>缺考<' not in html2)
    case('汇总表含 总分/年级名次/班级名次 + 每科 T 小字',
         all(k in html2 for k in ('总分', '年级名次', '班级名次'))
         and 'T ' in html2)
    r4 = client.get(f'/grades/timed/{tid}/export.xlsx')
    case('导出汇总 Excel 200',
         r4.status_code == 200 and 'spreadsheetml' in (r4.mimetype or ''),
         r4.mimetype)
    r3 = client.get('/grades/exams')
    case('普通考试列表 200 且不含本场限时练场次',
         r3.status_code == 200 and '2025级20261020限时练' not in
         r3.get_data(as_text=True))
    r4 = client.get('/grades/timed')
    case('限时练列表 200 且出现场次', r4.status_code == 200)


def test_permissions(client, tid):
    with app.app_context():
        admin_id = User.query.filter_by(username='admin').first().id
        teacher_id = User.query.filter_by(username='t1').first().id
        leader_id = User.query.filter_by(username='gl').first().id
        case('admin 具备管理权', _can_manage(admin_id))
        case('年级长具备管理权', _can_manage(leader_id))
        case('普通任课教师不具备管理权', not _can_manage(teacher_id))

    # 任课教师：可导入
    _login(client, 't1')
    rows = [[s[2] or '', s[0], s[1], s[3], s[4] + 1] for s in STUDENTS]
    r = _upload(client, tid, rows)
    case('任课教师可执行单科导入', r.status_code == 200 and '导入预览' in
         r.get_data(as_text=True))
    with app.app_context():
        exam = db.session.get(TimedExam, tid)
        token = exam.import_token
    client.post(f'/grades/timed/{tid}/import/confirm', data={'token': token})

    # 成绩单页已删除：改分 / 删分入口随之移除（改错请重导该科覆盖）
    r = client.post(f'/grades/timed/{tid}/score/1', data={'score': '88'})
    case('改分入口已移除（404）', r.status_code == 404, r.status_code)



def _can_manage(user_id):
    """在 app context 内判定 can_manage（避免跨请求污染）"""
    from app.modules.grades.services import timed_service
    user = db.session.get(User, user_id)
    return timed_service.can_manage(user)


def test_template_download(client):
    r = client.get('/grades/timed/template.xlsx')
    case('限时练导入模板可下载', r.status_code == 200
         and 'spreadsheet' in r.headers.get('Content-Type', ''))


def test_ai_import(client, tid):
    """AI 链路：analyze 出列映射 → apply 预览 → apply 落库（科目=数学）"""
    import json
    buf = _xlsx(['考生号', '学生姓名', '班级', '成绩'],
                [['59468662', '张三', '01班', 130],
                 ['59468663', '李四', '01班', 125]])
    r = client.post(f'/grades/timed/{tid}/ai/analyze',
                    data={'subject': '数学', 'file': (buf, 'm.xlsx')},
                    content_type='multipart/form-data')
    body = r.get_json() or {}
    case('AI analyze 自动识别出考号与分数列',
         body.get('success') and body.get('mapping', {}).get('zhixue') == 0
         and body.get('mapping', {}).get('score') == 3, body.get('mapping'))
    mapping = json.dumps(body.get('mapping') or {})
    buf2 = _xlsx(['考生号', '学生姓名', '班级', '成绩'],
                 [['59468662', '张三', '01班', 130],
                  ['59468663', '李四', '01班', 125]])
    r = client.post(f'/grades/timed/{tid}/ai/apply',
                    data={'subject': '数学', 'file': (buf2, 'm.xlsx'),
                          'mapping': mapping, 'dry_run': '1'})
    pv = (r.get_json() or {}).get('preview') or {}
    with app.app_context():
        saved = TimedScore.query.filter_by(exam_id=tid, subject='数学').count()
    case('AI 预览返回 2 行且未落库', pv.get('total_rows') == 2 and saved == 0,
         f'pv={pv.get("total_rows")} saved={saved}')
    buf3 = _xlsx(['考生号', '学生姓名', '班级', '成绩'],
                 [['59468662', '张三', '01班', 130],
                  ['59468663', '李四', '01班', 125]])
    r = client.post(f'/grades/timed/{tid}/ai/apply',
                    data={'subject': '数学', 'file': (buf3, 'm.xlsx'),
                          'mapping': mapping, 'dry_run': '0'})
    with app.app_context():
        saved = TimedScore.query.filter_by(exam_id=tid, subject='数学').count()
    case('AI 导入落库 2 人',
         (r.get_json() or {}).get('applied') == 2 and saved == 2,
         f'body={r.get_json()} saved={saved}')


def test_isolation(client, tid):
    """跨场次 / 跨年级 / 前导 0 学号 / 覆盖后名次重算"""
    # 前导 0 学号：主库存 020250009，文件写 20250009，应按数值兜底命中
    with app.app_context():
        db.session.add(Student(name='前导零', gender='男',
                               student_number='020250009', grade=GRADE,
                               class_name='01班', subject_selection='物化生'))
        db.session.commit()
    _login(client, 'admin')
    buf = _xlsx(['学号', '姓名', '班级', '分数'],
                [['20250009', '前导零', '01班', 77]])
    r = client.post(f'/grades/timed/{tid}/import/upload',
                    data={'subject': '地理', 'file': (buf, 'g.xlsx')},
                    content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        exam = db.session.get(TimedExam, tid)
        token = exam.import_token
    client.post(f'/grades/timed/{tid}/import/confirm', data={'token': token})
    with app.app_context():
        row = TimedScore.query.filter_by(exam_id=tid, subject='地理').first()
    case('前导 0 学号按数值兜底匹配', row is not None
         and row.student_no == '020250009', row and row.student_no)

    # 跨年级隔离：另建 2024 级场次
    client.post('/grades/timed/new', data={
        'grade': '2024级', 'exam_date': '2026-10-21', 'name': '隔离测试场'})
    with app.app_context():
        other = TimedExam.query.filter_by(grade='2024级').first()
        other_id = other.id
        case('新建后名称统一（页面已无手填入口）',
             other.name == '2024级20261021限时练No.1', other.name)

    # 跨年级：2025 级年级长看不到 2024 级场次
    _login(client, 'gl')
    r = client.get(f'/grades/timed/{other_id}')
    case('跨年级访问场次 403', r.status_code == 403, r.status_code)

    # 覆盖后名次重算：地理只有 1 人，再导入同科 2 人并反转分数
    _login(client, 'admin')
    buf2 = _xlsx(['学号', '姓名', '班级', '分数'],
                 [['20250009', '前导零', '01班', 60],
                  ['20250001', '张三', '01班', 90]])
    client.post(f'/grades/timed/{tid}/import/upload',
                data={'subject': '地理', 'file': (buf2, 'g2.xlsx')},
                content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        token = db.session.get(TimedExam, tid).import_token
    client.post(f'/grades/timed/{tid}/import/confirm', data={'token': token})
    with app.app_context():
        rows = {r.student_no: r.rank_grade for r in TimedScore.query.filter_by(
            exam_id=tid, subject='地理').all()}
    case('整科覆盖后名次重算（90 分第 1、60 分第 2）',
         rows.get('20250001') == 1 and rows.get('020250009') == 2, rows)


def test_all_subject_columns(client, tid):
    """2026-10-21 用户口径：两个列表页**固定展示 9 科 + 总分**，
    本场没导入的科留「—」—— 导入后要能一眼看出哪科还没导。"""
    subs = ['语文', '数学', '外语', '物理', '历史',
            '化学', '生物', '政治', '地理']
    _login(client, 'admin')
    with app.app_context():
        cls = (TimedScore.query.filter_by(exam_id=tid)
               .with_entities(TimedScore.class_name).first())[0]
    for tab in ('students', 'trends'):
        for name in ('', cls):
            url = '/grades/timed/%s?tab=%s' % (tid, tab)
            if name:
                url += '&class=%s' % name
            html = client.get(url).get_data(as_text=True)
            head = re.search(r'<thead.*?</thead>', html, re.S)
            cols = [c.strip() for c in re.findall(
                r'<th[^>]*>([^<]+)</th>', head.group(0))] if head else []
            miss = [s for s in subs if s not in cols]
            case('%s %s 九科全列 + 总分' % (tab, name or '全部'),
                 not miss and '总分' in cols, miss)


def test_class_norm():
    """2026-10-21 用户口径：智学网班级「高三01班」必须归一化成「01班」。

    否则任课映射（01班）、班型设置、历史成绩三边对不上 → 分析页
    「任课教师」整列空白、同班学生被拆成两行。
    """
    from app.modules.grades.services.timed_import import _norm_class
    case('班级归一化 高三01班 → 01班', _norm_class('高三01班') == '01班',
         _norm_class('高三01班'))
    case('班级归一化 1班 → 01班', _norm_class('1班') == '01班')
    case('班级归一化 高一（02）班 → 02班', _norm_class('高一（02）班') == '02班')
    case('班级归一化 不分班原样', _norm_class('不分班') == '不分班')
    from app.modules.grades.routes.timed_upload import _csv_to_xlsx
    parsed = timed_import.parse_sheet(
        _csv_to_xlsx(('学号,姓名,班级,分数\n20250001,甲,高三01班,88\n')
                     .encode('utf-8')), '语文', GRADE)
    case('导入解析后班级已归一化',
         parsed['rows'] and parsed['rows'][0]['class_name'] == '01班',
         parsed['rows'][0].get('class_name') if parsed['rows'] else None)


def test_match_smart():
    """2026-10-21 用户口径：考号对不上时按 姓名+班级 兜底；
    考号被别的学生复用（名字不符）时不能挂错人。"""
    from app.modules.grades.services import timed_match
    with app.app_context():
        stu = Student.query.filter_by(grade=GRADE).first()
        name, cls, no = stu.name, stu.class_name, stu.student_number
        zx = stu.zhixue_no
        other = (Student.query
                 .filter(Student.grade == GRADE, Student.student_number != no)
                 .first())
        other_no = other.student_number
    m, e, _ = timed_match.match_students(
        [{'line': 2, 'zhixue_no': '47642671', 'student_no': '',
          'name': name, 'class_name': cls, 'score': 88}], GRADE)
    case('考号对不上时按姓名+班级匹配',
         len(m) == 1 and m[0]['student_no'] == no
         and m[0].get('match_by') == '姓名+班级', (m, e))
    m2, _, _ = timed_match.match_students(
        [{'line': 2, 'zhixue_no': zx, 'student_no': '', 'name': name,
          'class_name': cls, 'score': 88}], GRADE)
    case('按库内考号直接匹配', len(m2) == 1 and m2[0]['student_no'] == no)
    m3, e3, _ = timed_match.match_students(
        [{'line': 2, 'zhixue_no': other_no, 'student_no': '',
          'name': '查无此人', 'class_name': '99班', 'score': 88}], GRADE)
    case('考号撞上别人时不误挂', not m3 and len(e3) == 1, (m3, e3))


def test_csv_upload():
    """2026-10-21：油猴脚本直接传 CSV（read_upload 里转 xlsx 再解析）"""
    from app.modules.grades.routes.timed_upload import _csv_to_xlsx
    raw = ('学号,姓名,班级,分数\n'
           '20250001,甲,01班,88\n').encode('utf-8-sig')
    parsed = timed_import.parse_sheet(_csv_to_xlsx(raw), '语文', GRADE)
    case('CSV 转 xlsx 后可解析出成绩', len(parsed['rows']) == 1,
         parsed.get('stats'))
    # Excel 另存的中文 CSV 常是 GB18030，也要认
    raw2 = ('学号,姓名,班级,分数\n'
            '20250001,甲,01班,88\n').encode('gb18030')
    parsed2 = timed_import.parse_sheet(_csv_to_xlsx(raw2), '语文', GRADE)
    case('GB18030 编码的 CSV 也认', len(parsed2['rows']) == 1)


def test_detail_paging():
    """2026-10-21 用户口径：逐场成绩按日期倒序、10 条一页"""
    from datetime import date, timedelta
    from types import SimpleNamespace as NS

    from app.modules.grades.services import timed_analysis as ta
    lines = [{'exam': NS(id=i, exam_date=date(2026, 1, 1) + timedelta(days=i))}
             for i in range(1, 24)]
    p1 = ta._paged_lines({'lines': lines}, None)
    case('逐场成绩倒序（新的在上）', p1['lines'][0]['exam'].id == 23,
         p1['lines'][0]['exam'].id)
    case('逐场成绩 10 条一页', len(p1['lines']) == 10 and p1['pages'] == 3,
         (len(p1['lines']), p1['pages']))
    p3 = ta._paged_lines({'lines': lines}, '3')
    case('末页为余数 3 条', len(p3['lines']) == 3 and p3['page'] == 3,
         (len(p3['lines']), p3['page']))
    case('页码越界收敛到末页',
         ta._paged_lines({'lines': lines}, 99)['page'] == 3)
    case('无成绩时不炸',
         ta._paged_lines({'lines': []}, 2)['pages'] == 1)


def test_default_template(client, tid):
    """固定用内置「限时练名次分层」模板（用户口径：不提供选模板 / 划线分层页）"""
    _login(client, 'gl')
    r = client.get(f'/grades/timed/{tid}?tab=analysis&subject=语文')
    html = r.get_data(as_text=True)
    case('分析页固定使用内置分层模板',
         r.status_code == 200 and '限时练名次分层' in html, r.status_code)
    case('页签只剩三个且无「划线分层」', '划线分层' not in html)


if __name__ == '__main__':
    _seed()
    with app.test_client() as c:
        test_parse_unit()
        _tid = test_create_and_import(c)
        test_template_download(c)
        test_analysis(c, _tid)
        test_permissions(c, _tid)
        test_ai_import(c, _tid)
        test_all_subject_columns(c, _tid)
        test_detail_paging()
        test_csv_upload()
        test_match_smart()
        test_class_norm()
        test_default_template(c, _tid)
        test_isolation(c, _tid)
    print(f'\n结果：PASS {_ok} / FAIL {_fail}')
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(0 if _fail == 0 else 1)

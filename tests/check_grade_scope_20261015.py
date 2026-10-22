# -*- coding: utf-8 -*-
"""成绩可见范围专项验证（2026-10-15 用户口径）：临时目录数据库，不影响本地 data/

用户口径（本轮落实）：
  · 任课教师＝任教 **(年级, 学科)**：该年级该科**全部班级**的逐生成绩都开放
    （"教高一化学 → 高一所有班的化学成绩都对她开着；教高一高二 → 两个年级都开"）；
  · 同时给该生的**总分年级名次**（`rank_dir` 总分行）——**只给名次，不给总分分数**；
  · 负向对照：非所教学科、他年级、逐生总分（成绩单 / 临界生）仍必须收口。

运行：py -3.12 tests/check_grade_scope_20261015.py
"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.dirname(_HERE)
_SCRIPTS = _os.path.join(_ROOT, 'scripts')
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys

# Windows 控制台是 GBK，输出 emoji/★ 会 UnicodeEncodeError（2026-10-21 补）
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:                                   # noqa: BLE001
    pass
import tempfile

BASE = _ROOT
sys.path.insert(0, BASE)

_TMP = tempfile.mkdtemp(prefix='stulink_scope_verify_')

import config as config_mod            # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_TMP, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_TMP, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_TMP, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_TMP, 'points.db'),
    'academic': 'sqlite:///' + os.path.join(_TMP, 'academic.db'),
    'inspection': 'sqlite:///' + os.path.join(_TMP, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_TMP, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_TMP, 'forms.db'),
    'system': 'sqlite:///' + os.path.join(_TMP, 'system.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
    'timetable': 'sqlite:///' + os.path.join(_TMP, 'timetable.db'),
    'morality': 'sqlite:///' + os.path.join(_TMP, 'morality.db'),
    'timed': 'sqlite:///' + os.path.join(_TMP, 'timed.db'),
}
config_mod.Config.SECRET_KEY = 'scope-verify-secret'

from datetime import date  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (Student, User, PermissionGroup,  # noqa: E402
                        UserClassLink, UserDataScope)
from app.models.academic import Teacher  # noqa: E402
from app.models.grades import (Exam, ExamBand, ExamScore,  # noqa: E402
                               TeacherSubjectLink, TOTAL_SUBJECT)

app = create_app()

OK = 0
FAIL = 0
FAILED = []


def expect(name, cond, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print(f'PASS  {name}')
    else:
        FAIL += 1
        FAILED.append(name)
        print(f'FAIL  {name} {extra}')


GRADE = '2025级'
TEACH_CLASS, OTHER_CLASS = '01班', '03班'
TEACH_SUBJECT, OTHER_SUBJECT = '数学', '化学'


def build_data():
    """三个班 × 每班 2 人，含所教科 + 非所教科 + 总分（带 rank_dir 名次）"""
    TeacherSubjectLink.query.delete()
    UserClassLink.query.delete()
    UserDataScope.query.delete()
    db.session.commit()

    groups = {g.name: g for g in PermissionGroup.query.all()}
    t = User(username='renke_sc', real_name='验证任课教师', role='teacher',
             permission_group=groups['任课教师组'], must_change_pwd=False)
    t.set_password('pw123456')
    db.session.add(t)
    db.session.flush()
    # 工作台「我的成绩」走 _teacher_or_none()（按 user_id 找教师名单），必须建名单行
    db.session.add(Teacher(teacher_uid='TSC01', name='验证任课教师', user_id=t.id))
    db.session.add(TeacherSubjectLink(grade=GRADE, class_name=TEACH_CLASS,
                                      subject=TEACH_SUBJECT, user_id=t.id,
                                      active=True))

    h = User(username='banzhu_sc', real_name='验证班主任', role='homeroom_teacher',
             grade=GRADE, class_name=TEACH_CLASS,
             permission_group=groups['班主任组'], must_change_pwd=False)
    h.set_password('pw123456')
    db.session.add(h)
    db.session.flush()
    db.session.add(Teacher(teacher_uid='TSC02', name='验证班主任', user_id=h.id))
    db.session.add(UserClassLink(user_id=h.id, grade=GRADE, class_name=TEACH_CLASS))

    exam = Exam(name='口径验证考', exam_type='月考', grade=GRADE,
                exam_date=date(2026, 10, 10), status='imported')
    db.session.add(exam)
    db.session.flush()

    rows, seq = [], 0
    for cls in ('01班', '02班', '03班'):
        for i in (1, 2):
            seq += 1
            no = f'2025{int(cls[:2]):02d}{i:02d}'
            nm = f'{cls[:-1]}生{i}'
            rows.append(Student(student_number=no, name=nm, gender='男',
                                grade=GRADE, class_name=cls, subject_selection='物化生'))
            rows.append(ExamScore(exam_id=exam.id, student_no=no, student_name=nm,
                                  grade=GRADE, class_name=cls, subject=TEACH_SUBJECT,
                                  score=90.0 - seq, rank_class=1, rank_dir=seq))
            rows.append(ExamScore(exam_id=exam.id, student_no=no, student_name=nm,
                                  grade=GRADE, class_name=cls, subject=OTHER_SUBJECT,
                                  score=80.0 - seq, rank_class=1, rank_dir=seq))
            rows.append(ExamScore(exam_id=exam.id, student_no=no, student_name=nm,
                                  grade=GRADE, class_name=cls, subject=TOTAL_SUBJECT,
                                  score=170.0 - seq * 2, rank_class=1, rank_dir=seq))
    db.session.add_all(rows)
    # 临界生的"对照组"需要一条总分划线，否则不收窄也返回空（控制项失效）
    db.session.add(ExamBand(exam_id=exam.id, subject=TOTAL_SUBJECT,
                            name='本科线', seq=1,
                            lower_mode='score', lower_value=160.0))
    db.session.commit()
    return t.id, h.id, exam.id


with app.app_context():
    TID, HID, EID = build_data()


def _scope(uid):
    from app.modules.grades.services import score_guard
    from app.models import User as U
    with app.app_context():
        return score_guard.resolve(db.session.get(U, uid))


# ---------- 1) 引擎层（纯逻辑，正反对照） ----------
sc = _scope(TID)
expect('R1 任课：任教班可进', sc.can_view_class(GRADE, TEACH_CLASS))
expect('R1 任课：同年级非任教班也可进（年级×学科）', sc.can_view_class(GRADE, OTHER_CLASS))
expect('R1 任课：任教班学科仅所教科', sc.class_subjects(GRADE, TEACH_CLASS) == {TEACH_SUBJECT})
expect('R1 任课：**他班同学科**＝所教科 🌟', sc.class_subjects(GRADE, OTHER_CLASS) == {TEACH_SUBJECT})
expect('R1 任课：他班可见年级 × 学科（班级清单不再收窄）',
       sc.visible_classes(GRADE) is None)
expect('R1 任课：跨年级拒绝', not sc.can_view_class('2024级', TEACH_CLASS))
expect('负向·任课总分**分数**仍不可见', not sc.class_total_visible(GRADE, OTHER_CLASS))
expect('R1 任课：总分**年级名次**可见 🌟', sc.total_rank_visible(GRADE, OTHER_CLASS))
expect('负向·逐生总分收口为空集（成绩单/临界生 fail-closed）',
       sc.total_visible_classes(GRADE) == set())

# ---------- 2) 工作台明细接口（Web 侧主出口） ----------
def detail(c, exam_id, cls, subject):
    return c.get(f'/workbench/api/grade-detail?exam_id={exam_id}'
                 f'&class_name={cls}&subject={subject}')


with app.test_client() as cli:
    r0 = cli.get('/login')
    import re
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
    csrf = m.group(1) if m else ''
    r = cli.post('/login', data={'csrf_token': csrf, 'username': 'renke_sc',
                                 'password': 'pw123456'}, follow_redirects=True)
    expect('任课教师登录', r.status_code == 200)

    page = cli.get('/workbench/grades')
    html = page.get_data(as_text=True)
    expect('我的成绩页 200', page.status_code == 200, f'status={page.status_code}')
    expect('我的成绩页含"本年级同科其他班"折叠行 🌟', '本年级同科其他班' in html)

    r = detail(cli, EID, TEACH_CLASS, TEACH_SUBJECT)
    mine = r.get_json() or {}
    expect('任教班明细可看', r.status_code == 200 and bool(mine.get('rows')))

    r = detail(cli, EID, OTHER_CLASS, TEACH_SUBJECT)
    other = r.get_json() or {}
    rows = other.get('rows') or []
    expect('🌟 他班同科明细可看（原 403 → 现 200）', r.status_code == 200, f'status={r.status_code}')
    expect('🌟 他班明细每行都带回总分年级名次 rank_dir',
           bool(rows) and all(row.get('rank_dir') for row in rows),
           f'样例={rows[:1]}')
    expect('负向·他班明细不含总分分数（无 total 字段）',
           bool(rows) and all('total' not in row for row in rows))
    expect('他班明细班数为实际参考数', len(rows) == 2, f'rows={len(rows)}')

    r = detail(cli, EID, OTHER_CLASS, OTHER_SUBJECT)
    expect('负向·他班非所教学科仍 403', r.status_code == 403, f'status={r.status_code}')

    # 成绩单（逐生总分/排名）对任课教师仍须 fail-closed
    r = cli.get(f'/grades/exams/{EID}')
    expect('负向·成绩单页对任课教师仍 403（总分口径不开放）',
           r.status_code == 403, f'status={r.status_code}')

    # 临界生（逐生总分名单）：服务层直接验证（本沙箱未划线，页面态断言会耦合文案）
    with app.app_context():
        from app.modules.grades.services.report_warning_service import near_line_report
        nl_all = near_line_report(EID, '', '', 10, 20)
        nl_none = near_line_report(EID, '', '', 10, 20, allowed_classes=set())
        expect('负向·临界生名单按班级收口（对照：不收窄时有数据）',
               bool(nl_all.get('rows')), f'对照 rows={len(nl_all.get("rows") or [])}')
        expect('负向·临界生名单对任课教师为空（fail-closed）',
               not (nl_none.get('rows') or []), f'rows={len(nl_none.get("rows") or [])}')

    cli.get('/logout', follow_redirects=True)

    # 班主任口径不受影响（旧口径回归）
    r0 = cli.get('/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
    csrf = m.group(1) if m else ''
    cli.post('/login', data={'csrf_token': csrf, 'username': 'banzhu_sc',
                             'password': 'pw123456'}, follow_redirects=True)
    r = detail(cli, EID, TEACH_CLASS, TOTAL_SUBJECT)
    head = r.get_json() or {}
    expect('回归·班主任本班总分明细仍可看',
           r.status_code == 200 and bool((head.get('rows') or [])),
           f'status={r.status_code}')
    r = detail(cli, EID, OTHER_CLASS, TOTAL_SUBJECT)
    expect('回归·班主任看他班总分仍 403', r.status_code == 403, f'status={r.status_code}')
    cli.get('/logout', follow_redirects=True)

# ---------- 3) MCP 逐生出口（与 Web 同一份口径） ----------
with app.app_context():
    from app.models import User as U
    from mcp_server.tools import _exam_views as mcp_scores

    user = db.session.get(U, TID)
    out = mcp_scores._class_score_detail(
        user, EID, OTHER_CLASS, TEACH_SUBJECT, 'full', 0)
    mrows = out.get('students') or []
    expect('MCP·他班同科逐生明细返回', bool(mrows))
    expect('MCP·🌟 每行给出总分年级名次 rank_g',
           all(row.get('rank_g') for row in mrows), f'样例={mrows[:1]}')
    expect('MCP·负向·不含总分（只给名次不给分数）',
           all('total' not in row for row in mrows))
    expect('MCP·负向·总分口径仍隐藏', out.get('total_visible') is False)
    expect('MCP·标记 total_rank_visible', out.get('total_rank_visible') is True)

    # 负向：他班非所教学科
    try:
        mcp_scores._class_score_detail(
            user, EID, OTHER_CLASS, OTHER_SUBJECT, 'full', 0)
        expect('MCP·负向·他班非所教科应被拒', False, '未抛异常')
    except PermissionError:
        expect('MCP·负向·他班非所教科被拒', True)

    # 负向：跨年级学生
    out2 = mcp_scores._student_scores(user, '20250301', 0, 'full')
    expect('MCP·本人可见学生仍可用（同级）', isinstance(out2, dict))

print()
print('===== 成绩可见范围专项验证完成：通过 %d / 失败 %d =====' % (OK, FAIL))
if FAILED:
    print('失败项：')
    for n in FAILED:
        print('  -', n)
sys.exit(1 if FAIL else 0)

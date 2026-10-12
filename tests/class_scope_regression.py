# -*- coding: utf-8 -*-
"""班级口径一致性回归（成绩"班级"以总分行为权威）

背景：
- exam_scores 一科一行，class_name 是**导入当时的快照**（模型注释："换班不影响历史"）；
- 选科分班后，科目行的班级快照会与总分行漂移（同一学生在科目行里可能已是新班）；
- 成绩分析口径 = "总分行的班级"：stats_service.ExamData.classes / totals_of()
  只认总分行（stats_service.py L99/L111/L118-120），科目行的漂移 class_name
  不得进入班级列表与统计。

【发现记录 · INFO 级，仅记录不改代码】
grades/routes/teachers.py 的班级下拉来源是 _grade_classes(grade)（L40-43），
它查的是 **system 库 students 表当前在籍口径**（Student.class_name 实时值），
与成绩分析的总分行快照口径不同源。本脚本对其只做"存在即可用"的 INFO 断言，
不在本脚本内改动该路由。

运行：py -3.12 tests/class_scope_regression.py  （退出码 0 = 全绿）
"""
import os
import shutil
import sys
import tempfile
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_class_scope_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
# 库文件必须放在 BASE_DIR/data/ 下——grade_mgmt 等裸 sqlite3 代码按此布局找库
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
    'inspection': 'sqlite:///' + os.path.join(_DATA, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_DATA, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_DATA, 'forms.db'),
    'portrait': 'sqlite:///' + os.path.join(_DATA, 'portrait.db'),
    'system': 'sqlite:///' + os.path.join(_DATA, 'system.db'),
    'timetable': 'sqlite:///' + os.path.join(_DATA, 'timetable.db'),
}
config_mod.Config.SECRET_KEY = 'class-scope-regression-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Student  # noqa: E402
from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT  # noqa: E402
from app.modules.grades.services import stats_service  # noqa: E402

app = create_app()

_ok = 0
_fail = 0


def case(name, cond, extra=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f'[PASS] {name}')
    else:
        _fail += 1
        print(f'[FAIL] {name} {extra}')


# 科目行的漂移班级（故意与总分行不同，模拟选科分班后的快照漂移）
_DRIFT = {'SEC-A': '05班', 'SEC-B': '06班'}
# 考试当时的班级（总分行权威口径）
_EXAM_CLASS = {'SEC-A': '01班', 'SEC-B': '02班'}


def _seed():
    """一场 2025级 考试 + 两名学生；总分行用考试当时班级，科目行班级故意漂移"""
    with app.app_context():
        db.create_all()
        exam = Exam(name='2025级期末统考', grade='2025级',
                    exam_date=date(2026, 1, 15), status='imported',
                    exam_type='期末', exam_kind='本校考')
        db.session.add(exam)
        db.session.flush()

        for no, name, cls in (('SEC-A', '张三', '01班'), ('SEC-B', '李四', '02班')):
            db.session.add(Student(name=name, gender='男', student_number=no,
                                   grade='2025级', class_name=cls,
                                   subject_selection='物化生'))
            # 总分行：班级 = 考试当时的班级（权威口径）
            db.session.add(ExamScore(
                exam_id=exam.id, student_no=no, student_name=name, grade='2025级',
                class_name=cls, subject=TOTAL_SUBJECT,
                score=600 if no == 'SEC-A' else 580, direction='物理',
                subject_selection='物化生', rank_dir=1 if no == 'SEC-A' else 2,
                rank_class=1))
            # 科目行：class_name 故意填成漂移班
            for subj, sc in (('语文', 120), ('数学', 130), ('外语', 125),
                             ('物理', 90), ('化学', 85), ('生物', 88)):
                db.session.add(ExamScore(
                    exam_id=exam.id, student_no=no, student_name=name, grade='2025级',
                    class_name=_DRIFT[no], subject=subj, score=sc,
                    direction='物理', subject_selection='物化生'))
        db.session.commit()
        return exam.id


def test_class_scope(exam_id):
    with app.app_context():
        data = stats_service.cached_exam_data(exam_id)

        # 1) 班级列表只含总分行口径
        case('data.classes 只包含总分行口径班级',
             set(data.classes) == set(_EXAM_CLASS.values()), f'实际={data.classes}')
        for no, drift in _DRIFT.items():
            case(f'漂移班级 {drift}（{no} 的科目行）不进入班级列表',
                 drift not in data.classes, f'实际={data.classes}')

        # 2) totals_of 与总分行一致
        for no, cls in _EXAM_CLASS.items():
            db_total = ExamScore.query.filter_by(
                exam_id=exam_id, subject=TOTAL_SUBJECT, class_name=cls).count()
            got = data.totals_of(class_name=cls)
            case(f'totals_of({cls}) 条数与总分行一致',
                 len(got) == db_total == 1,
                 f'totals={len(got)} db={db_total}')
        case("漂移班 totals_of('05班') 为空", data.totals_of(class_name='05班') == [])
        case('totals_of() 全量 = 2 条总分行',
             len(data.totals_of()) == 2, f'实际={len(data.totals_of())}')

        # 3) INFO：教师映射页班级下拉来源（当前在籍口径，仅记录不改代码）
        try:
            from app.modules.grades.routes.teachers import _grade_classes
            dropdown = _grade_classes('2025级')
        except Exception as _e:  # noqa: BLE001
            dropdown = None
            print(f'[INFO] 读取 grades/routes/teachers._grade_classes 失败：{_e}')
        print(f'[INFO] grades/routes/teachers.py 班级下拉来源 '
              f'_grade_classes("2025级") = {dropdown} '
              f'（口径=当前在籍 Student.class_name，与总分快照不同源；仅记录，未改代码）')
        case('INFO 班级下拉可用且含两名在籍学生所在班',
             isinstance(dropdown, list) and '01班' in dropdown
             and '02班' in dropdown, f'实际={dropdown}')


if __name__ == '__main__':
    _exam_id = _seed()
    test_class_scope(_exam_id)
    print(f'\n结果：PASS {_ok} / FAIL {_fail}')
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(0 if _fail == 0 else 1)

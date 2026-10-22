# -*- coding: utf-8 -*-
"""任课快照回归：成绩表「授课信息表」→ 每场考试自己的任课数据。

覆盖（2026-10-21 用户口径「每场考试要有自己的数据」）：
1. 授课信息表解析：一班一行 + 正班/副班1/副班2，跳过下方「1班|总分…」映射块；
2. 班主任三列合并成一行（exam_teacher_links 有唯一约束）；
3. 只写本场**实际考了**的 (班级, 科目)，不把上一阶段的科目列带进来；
4. 姓名 → user_id 反查（同名多账号不猜；合并的班主任不猜）；
5. 整场重写（source='excel'），不残留旧快照。

运行：py -3.12 tests/teacher_sheet_regression.py   （退出码 0 = 全绿）
"""
import io
import os
import shutil
import sys
import tempfile
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_tsheet_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
_DATA = os.path.join(_TMP, 'data')
os.makedirs(_DATA, exist_ok=True)
config_mod.Config.SQLALCHEMY_DATABASE_URI = (
    'sqlite:///' + os.path.join(_DATA, 'system.db'))
config_mod.Config.SQLALCHEMY_BINDS = {
    k: 'sqlite:///' + os.path.join(_DATA, f'{k}.db')
    for k in ('dormitory', 'history', 'grades', 'points', 'morality',
              'academic', 'inspection', 'achievement', 'forms', 'portrait',
              'system', 'timetable', 'timed')
}
config_mod.Config.SECRET_KEY = 'tsheet-regression-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')

import openpyxl  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.models.grades import Exam, ExamScore, ExamTeacherLink  # noqa: E402
from app.modules.grades.services import teacher_sheet_service as tsheet  # noqa: E402
from app.modules.grades.services import teacher_snapshot_service as tss  # noqa: E402

app = create_app()
_ok = 0
_fail = 0
TEACHERS = ['王班', '李副', '赵副', '张三', '李四', '王五', '赵六',
            '钱七', '孙八', '周九', '吴十', '郑一']
HEADER = ['年级', '班级', '密码', '选课', '总赋', '副班', '副班', '语文',
          '数学', '外语', '物理', '化赋', '生赋', '政赋', '历史', '地赋']
ROW1 = ['2025级', '01班', '12345', '全科', '王班', '李副', '赵副', '张三',
        '李四', '王五', '赵六', '钱七', '孙八', '周九', '吴十', '郑一']
ROW2 = ['2025级', '02班', '12345', '全科', '王班', '', '', '张三', '李四',
        '王五', '', '', '', '', '', '']


def case(name, cond, extra=''):
    """断言并计数。"""
    global _ok, _fail
    if cond:
        _ok += 1
        print('  [OK] %s' % name)
    else:
        _fail += 1
        print('  [FAIL] %s %s' % (name, extra))


def make_workbook():
    """构造含授课信息表 + 下方映射块的工作簿。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '全科纯分'
    ws.append(['学号', '姓名', '班级', '语文'])
    ws.append(['20250001', '测试生', '01班', 100])
    ws2 = wb.create_sheet('授课信息表')
    ws2.append(HEADER)
    ws2.append(ROW1)
    ws2.append(ROW2)
    ws2.append(['', '班级', '01班', '02班'])          # 映射块：必须被跳过
    ws2.append(['', '总赋', '某某', '某某'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def seed():
    """建考试 + 成绩（01班 9 科、02班 只 3 科）+ 教师账号。"""
    with app.app_context():
        db.create_all()
        for i, name in enumerate(TEACHERS):
            u = User(username='u%02d' % i, real_name=name, is_active=True,
                     role='teacher')
            u.set_password('pw123456')
            db.session.add(u)
        exam = Exam(name='2025级测试', grade='2025级',
                    exam_date=date(2026, 4, 1), status='imported')
        db.session.add(exam)
        db.session.flush()
        subs = {'01班': ['语文', '数学', '外语', '物理', '化学', '生物',
                         '政治', '历史', '地理'],
                '02班': ['语文', '数学', '外语']}
        for i, (cls, subjects) in enumerate(subs.items(), 1):
            for sub in subjects + ['总分']:
                db.session.add(ExamScore(
                    exam_id=exam.id, student_no='2025000%d' % i,
                    student_name='测试生', grade='2025级',
                    class_name=cls, subject=sub, score=90))
        db.session.commit()
        return exam.id


def check_parse():
    """① 解析 + ② 班主任合并 + ③ 实考过滤。"""
    print('\n[1] 授课信息表解析')
    info = tsheet.parse_stream(make_workbook())
    case('识别到授课信息表', info['sheet'] == '授课信息表', info['sheet'])
    case('01班任课 9 条', len([r for r in info['teachers']
                               if r['class_name'] == '01班']) == 9)
    case('空单元格不生成任课行（02班 3 条）',
         len([r for r in info['teachers']
              if r['class_name'] == '02班']) == 3)
    case('向下映射块未混入任课行',
         all(r['subject'] != '班级' for r in info['teachers']))
    case('班主任三列按序', info['heads']['01班'] == ['王班', '李副', '赵副'],
         info['heads']['01班'])
    rows = tsheet.to_snapshot_rows(info)
    case('班主任合并为一行',
         [r for r in rows if r['class_name'] == '01班'
          and r['subject'] == '班主任'][0]['teacher_name']
         == '王班、李副、赵副')
    return info


def check_snapshot(exam_id, info):
    """④ 姓名反查 + ⑤ 整场重写。"""
    print('\n[2] 写入快照')
    with app.app_context():
        exam = Exam.query.get(exam_id)
        n = tss.snapshot_from_sheet(exam, info)
        db.session.commit()
        links = {(r.class_name, r.subject): r
                 for r in ExamTeacherLink.query.filter_by(exam_id=exam_id)}
        case('02班只写实考 3 科 + 班主任',
             ('02班', '化学') not in links and ('02班', '语文') in links)
        case('01班化学写入', ('01班', '化学') in links)
        qian = User.query.filter_by(real_name='钱七').first()
        case('姓名反查到 user_id',
             links[('01班', '化学')].user_id == qian.id)
        head = links[('01班', '班主任')]
        case('合并班主任不猜 user_id', head.user_id is None)
        case('来源标记 excel', links[('01班', '化学')].source == 'excel')
        expect = 9 + 3 + 2
        case('总条数 = 实考科目 + 2 个班主任', n == expect, '%s != %s'
             % (n, expect))
        # 整场重写：先弄脏再跑一次
        links[('01班', '化学')].teacher_name = '旧值'
        links[('01班', '化学')].user_id = None
        db.session.commit()
        tss.snapshot_from_sheet(exam, info)
        db.session.commit()
        got = ExamTeacherLink.query.filter_by(
            exam_id=exam_id, class_name='01班', subject='化学').first()
        case('整场重写不残留旧值', got.teacher_name == '钱七')


def check_diff(info):
    """差异检测（核验脚本用）。"""
    print('\n[3] 差异检测')
    rows = tsheet.to_snapshot_rows(info)
    snap = {('01班', '语文'): '张三', ('01班', '化学'): '旧老师',
            ('01班', '音乐'): '多余'}
    diffs = tsheet.diff_snapshot(rows, snap)
    case('差异是四元组', all(len(d) == 4 for d in diffs))
    case('能报出「异」', ('01班', '化学', '旧老师', '钱七') in diffs)
    case('能报出「多」', ('01班', '音乐', '多余', '') in diffs)


def main():
    exam_id = seed()
    info = check_parse()
    check_snapshot(exam_id, info)
    check_diff(info)
    print('\n结果：%d 通过 / %d 失败' % (_ok, _fail))
    return 0 if _fail == 0 else 1


if __name__ == '__main__':
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)

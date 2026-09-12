# -*- coding: utf-8 -*-
"""向指定考试灌入演示成绩：走与页面一致的导入流程（上传 → 预检 → 确认 → 重算排名）

用法（在 StuLink/ 目录下执行）：
    python scripts/import_demo_scores.py                # 默认灌入最新一场考试
    python scripts/import_demo_scores.py 3              # 指定 exam_id
    python scripts/import_demo_scores.py 3 --seed 42    # 指定随机种子（默认 20260912）

说明：
    - 取主库「有选科组合」的学生，按其选科生成 6 科成绩；总分列留空，由系统按应考科累加。
    - Excel 刻意不含「年级」列：导入仅在存在该列时才校验年级，省略后不会因
      学生年级与考试年级不一致被拦截（成绩行仍会快照学生自身的真实年级）。
    - 幂等：按 (考试, 学号, 科目) 唯一键写入，重复执行为覆盖，不会产生重复行。
"""
import argparse
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import create_app                                    # noqa: E402
from app.models import Student                                # noqa: E402
from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT   # noqa: E402

ALL_SUBJECTS = ['语文', '数学', '外语', '物理', '历史', '化学', '生物', '政治', '地理']
# 各科分数分布（均值, 标准差, 下限, 上限）
DIST = {
    '语文': (108, 12, 60, 140),
    '数学': (95, 25, 20, 148),
    '外语': (100, 20, 30, 145),
    '物理': (70, 15, 25, 99),
    '历史': (72, 13, 30, 97),
    '化学': (68, 14, 25, 98),
    '生物': (68, 14, 25, 98),
    '政治': (70, 12, 30, 97),
    '地理': (69, 13, 28, 97),
}

app = create_app()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def build_workbook(students, rnd):
    """按学生选科生成成绩 Excel（不含年级列）"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '成绩'
    ws.append(['学号', '姓名', '班级'] + ALL_SUBJECTS)
    for s in students:
        sel = s.subject_selection or ''
        # 应考 6 科：语数外 + 方向科 + 两门再选
        take = ['语文', '数学', '外语']
        if len(sel) >= 3:
            take += [{'物': '物理', '史': '历史'}.get(sel[0], ''),
                     {'化': '化学', '生': '生物', '政': '政治', '地': '地理'}.get(sel[1], ''),
                     {'化': '化学', '生': '生物', '政': '政治', '地': '地理'}.get(sel[2], '')]
            take = [t for t in take if t]
        if len(take) < 4:      # 选科异常时退回语数外
            take = ['语文', '数学', '外语']
        row = [str(s.student_number), s.name, s.class_name]
        for sub in ALL_SUBJECTS:
            if sub not in take:
                row.append(None)
                continue
            mean, sd, lo, hi = DIST[sub]
            row.append(round(_clamp(rnd.gauss(mean, sd), lo, hi), 1))
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exam_id', nargs='?', type=int, default=None, help='考试 id，默认最新一场')
    ap.add_argument('--seed', type=int, default=20260912)
    ap.add_argument('--no-login-skip', action='store_true', help='保留参数占位')
    args = ap.parse_args()

    import random
    rnd = random.Random(args.seed)

    with app.app_context():
        exam = (Exam.query.get(args.exam_id) if args.exam_id
                else Exam.query.order_by(Exam.id.desc()).first())
        if not exam:
            print('未找到考试，请先创建考试')
            return 1
        eid, ename = exam.id, exam.name
        students = [s for s in Student.query.all()
                    if s.subject_selection and s.subject_selection != '不分班']
        print(f'目标考试：id={eid} 「{ename}」（年级 {exam.grade}，当前状态 {exam.status}）')
        print(f'主库学生：{len(students)} 人')
        if not students:
            print('主库没有带选科的学生，无法生成成绩')
            return 1

    buf = build_workbook(students, rnd)
    print('成绩 Excel 已生成（不含年级列，总分由系统累加）')

    with app.test_client() as c:
        r0 = c.get('/login')
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
        csrf = m.group(1)
        c.post('/login', data={'csrf_token': csrf, 'username': 'admin',
                               'password': 'admin123'}, follow_redirects=True)

        buf.seek(0)
        r = c.post(f'/grades/exams/{eid}/import/upload',
                   data={'mode': 'A', 'csrf_token': csrf, 'file': (buf, '演示成绩.xlsx')},
                   content_type='multipart/form-data', follow_redirects=True)
        if '确认导入' not in r.get_data(as_text=True):
            print('上传预检未通过，请检查页面提示')
            return 1
        with app.app_context():
            token = Exam.query.get(eid).import_token
        r = c.post(f'/grades/exams/{eid}/import/confirm',
                   data={'csrf_token': csrf, 'token': token}, follow_redirects=True)
        if '导入完成' not in r.get_data(as_text=True):
            print('确认导入失败')
            return 1

    with app.app_context():
        exam = Exam.query.get(eid)
        total_n = ExamScore.query.filter_by(exam_id=eid, subject=TOTAL_SUBJECT).count()
        rank_n = ExamScore.query.filter_by(exam_id=eid, subject=TOTAL_SUBJECT) \
            .filter(ExamScore.rank_dir.isnot(None)).count()
        subj_n = ExamScore.query.filter_by(exam_id=eid) \
            .filter(ExamScore.subject != TOTAL_SUBJECT).count()
        print(f'导入完成：参考学生 {total_n} 人（已排名 {rank_n}），单科成绩 {subj_n} 条，'
              f'状态={exam.status}')
    print(f'浏览器查看：http://localhost:5000/grades/exams/{eid}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

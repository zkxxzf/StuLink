# StuLink v1.19.49.52 2026-10-20
# 限时练学生匹配：考号优先、学号兜底；不做姓名猜测
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练导入的学生匹配与差异预览（不写库）。

匹配优先级（2026-10-20 用户口径）：
1. 文件【考号】→ ``students.zhixue_no``（智学网固定考号）；
2. 文件【学号】→ ``students.student_number``（含前导 0 的数值兜底）。
姓名 / 班级**不参与匹配**：同名同班在分班后极易误挂，宁可报错让老师核对。
"""
from app.extensions import db
from app.models import Student
from app.modules.grades.services.timed_import import _code


def _norm_student_no(value):
    """学号归一化（同普通导入：数值型去前导零差异）"""
    s = _code(value)
    return str(int(s)) if s.isdigit() else s


def _load_students(grade, zhixue, nos):
    """批量载入学生：{归一化考号: 学生} / {归一化学号: 学生}"""
    by_zhixue, by_no = {}, {}
    for i in range(0, len(zhixue), 500):
        chunk = list(zhixue)[i:i + 500]
        q = Student.query.filter(Student.zhixue_no.in_(chunk))
        if grade:
            q = q.filter(Student.grade == grade)
        for s in q.all():
            by_zhixue.setdefault(_code(s.zhixue_no), s)
    for i in range(0, len(nos), 500):
        chunk = list(nos)[i:i + 500]
        q = Student.query.filter(Student.student_number.in_(chunk))
        if grade:
            q = q.filter(Student.grade == grade)
        for s in q.all():
            by_no.setdefault(_norm_student_no(s.student_number), s)
        # 主库学号含前导 0（如 020250006）时字面量 IN 命中不到，按数值再查一次
        ints = [int(n) for n in chunk if n.isdigit() and int(n) > 0]
        if not ints:
            continue
        q2 = Student.query.filter(
            db.func.cast(Student.student_number, db.Integer).in_(ints))
        if grade:
            q2 = q2.filter(Student.grade == grade)
        for s in q2.all():
            by_no.setdefault(_norm_student_no(s.student_number), s)
    return by_zhixue, by_no


def match_students(rows, grade=''):
    """把解析行挂到主库学生上（考号 → 学号，两级）。

    返回 (matched_rows, errors, stats)；matched_rows 的 ``student_no``
    一律以主库原值为准，并补 ``student_name`` / ``class_name`` / ``zhixue_no``。
    """
    zhixue = {_code(r['zhixue_no']) for r in rows if r.get('zhixue_no')}
    nos = {_norm_student_no(r['student_no']) for r in rows if r.get('student_no')}
    by_zhixue, by_no = _load_students(grade, zhixue, nos)

    out, errors, seen = [], [], set()
    for r in rows:
        stu = (by_zhixue.get(_code(r.get('zhixue_no')))
               or by_no.get(_norm_student_no(r.get('student_no'))))
        if stu is None:
            errors.append({'line': r['line'],
                           'code': r.get('zhixue_no') or r.get('student_no'),
                           'reason': '考号/学号未匹配到本年级学生'})
            continue
        if stu.student_number in seen:
            errors.append({'line': r['line'], 'code': stu.student_number,
                           'reason': '匹配到同一学生，已跳过重复行'})
            continue
        seen.add(stu.student_number)
        out.append({'line': r['line'], 'student_no': stu.student_number,
                    'student_name': stu.name or r.get('name', ''),
                    'zhixue_no': _code(stu.zhixue_no) or r.get('zhixue_no', ''),
                    # 2026-10-21：班级**以主库学籍为准**。文件里的班级是导入方
                    # 自己填的，若当真，导入人可把成绩挂到任意班级 —— 而班级正是
                    # 成绩可见范围的判定依据（班主任按班授权）。
                    'class_name': stu.class_name or r.get('class_name') or '',
                    'score': r['score']})
    stats = {'matched': len(out), 'unmatched': len(errors)}
    return out, errors, stats


def preview_diff(exam_id, subject, rows):
    """预览差异：新增 / 改分 / 缺考删除（同科重导 = 整科覆盖）"""
    from app.models.timed import TimedScore
    existing = {r.student_no: r.score for r in TimedScore.query.filter_by(
        exam_id=int(exam_id), subject=subject).all()}
    new_ids = {r['student_no'] for r in rows}
    updated = sum(1 for r in rows if r['student_no'] in existing
                  and abs((existing[r['student_no']] or 0) - r['score']) > 1e-9)
    return {
        'new_students': len(new_ids - set(existing)),
        'updated_rows': updated,
        'deleted_rows': len(set(existing) - new_ids),
        'kept_rows': len(new_ids & set(existing)),
    }

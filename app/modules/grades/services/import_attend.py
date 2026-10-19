# -*- coding: utf-8 -*-
"""成绩导入：考务名单「不参加考试」学生的处理（2026-10-19）。

用户口径：考务安排里被移到「不参加考试」的学生，成绩表中即使有分数，
导入时也默认**不给他添加**；预览页可勾选「仍然添加」，是则一并导入。
本模块只做名单读取与行拆分，不写库。
"""


def non_attend_map(exam_id):
    """本场最新考务批次中「不参加考试」的学生：{学号: {name, class_name}}。

    无考务批次/无此标记时返回空 dict（导入行为与以前完全一致）。
    """
    from app.models.grades import ExamAffair, AffairStudent
    affair = (ExamAffair.query.filter_by(exam_id=exam_id)
              .order_by(ExamAffair.id.desc()).first())
    if affair is None:
        return {}
    rows = AffairStudent.query.filter_by(affair_id=affair.id,
                                         is_attend=False).all()
    return {r.student_no: {'name': r.name or '',
                           'class_name': r.class_name or ''}
            for r in rows if r.student_no}


def split_rows(rows, skip_map):
    """把成绩表行拆成（可导入行, 命中的不参加学生）。

    命中判定只看学号：parse_score_excel 已把学号归一到主库原值，
    与 affair_students.student_no 同源。返回的名单用于预览页提示。
    """
    kept, hit = [], []
    for row in rows:
        info = skip_map.get(row.get('no'))
        if info is None:
            kept.append(row)
            continue
        hit.append({'no': row['no'],
                    'name': row.get('name') or info['name'],
                    'class_name': row.get('class_name') or info['class_name']})
    return kept, hit

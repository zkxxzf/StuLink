# -*- coding: utf-8 -*-
"""把特控线 / 本科线写进 exam_bands。

口径与系统一致：seq=1 为特控线、seq=2 为本科线（最高层在前），
lower_mode='score'（直接给分数，不做比例换算），selection=''（按方向）。
写之前只清「方向级」旧线（selection=''），保留可能存在的组合级总分线。
"""
from app.extensions import db
from app.models.grades import ExamBand

BAND_SEQ = {'特控线': 1, '本科线': 2}


def replace_bands(exam_id, lines):
    """lines 形如 {方向: {科目: {'特控线': x, '本科线': y}}}，返回写入条数。"""
    ExamBand.query.filter_by(exam_id=exam_id, selection='').delete()
    db.session.flush()
    count = 0
    for direction, subjects in lines.items():
        for subject, values in subjects.items():
            for band, value in values.items():
                seq = BAND_SEQ.get(band)
                if seq is None or value is None:
                    continue
                db.session.add(ExamBand(
                    exam_id=exam_id, direction=direction, selection='',
                    subject=subject, seq=seq, name=band,
                    lower_mode='score', lower_value=float(value)))
                count += 1
    return count


def summarize(lines):
    """把 lines 压成便于打印的摘要文本。"""
    parts = []
    for direction, subjects in lines.items():
        total = subjects.get('总分') or {}
        label = direction or '全科'
        parts.append('%s 特控%s/本科%s' % (
            label, total.get('特控线', '-'), total.get('本科线', '-')))
    return '；'.join(parts)

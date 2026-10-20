# -*- coding: utf-8 -*-
"""把一场考试的成绩与划线写进 StuLink（脚本版导入，复用网页同一套服务）。"""
import json
import os
import tempfile
from datetime import datetime

from app.extensions import db
from app.models.grades import Exam
from app.modules.grades.services import (import_service, ranking,
                                         store_service, tab_service)
from app.modules.grades.services import (
    teacher_snapshot_service as tss)
from app.modules.grades.utils import term_of_date

from . import band_writer, line_reader, name_index, score_reader, workbook
from .specs import SOURCE_DIR, SPECS
GRADE = '2025级'


def _operator_id():
    """取库里最小 id 的用户作为操作人（与既有导入脚本同口径）。"""
    from app.models import User
    user = User.query.order_by(User.id).first()
    return user.id if user else None


def ensure_exam(spec):
    """按 年级+名称 取考试；不存在则新建。返回 (exam, created)。"""
    exam = Exam.query.filter_by(grade=GRADE, name=spec['name']).first()
    if exam:
        return exam, False
    exam = Exam(grade=GRADE, name=spec['name'], exam_date=spec['date'],
                exam_type=spec['type'], exam_kind=spec['kind'],
                term=term_of_date(spec['date']),
                operator_id=_operator_id())
    db.session.add(exam)
    db.session.flush()
    return exam, True


def _clear_direction(parsed):
    """高一上未分科：方向与选科快照一律留空（勿用分科后的选科反标）。"""
    for row in parsed['rows']:
        row['direction'] = ''
        row['subject_selection'] = ''


def _parse(exam, spec, records, with_raw):
    """把记录写成临时 xlsx 再交给 import_service 解析。"""
    fd, path = tempfile.mkstemp(suffix='.xlsx')
    os.close(fd)
    try:
        workbook.write_book(records, path, with_raw)
        with open(path, 'rb') as fh:
            parsed = import_service.parse_score_excel(fh, exam)
    finally:
        os.remove(path)
    if not spec['split']:
        _clear_direction(parsed)
    return parsed


def build_index():
    """构建主库索引与跨场「姓名+班级」提示（一次调用内复用，避免反复扫文件）。"""
    index = name_index.build(GRADE)
    return index, name_index.collect_hints(index, SPECS)


def prepare(exam, spec, index=None, hints=None):
    """读文件并解析，返回 (parsed, 未匹配列表, 是否含原始分)。"""
    path = os.path.join(SOURCE_DIR, spec['file'])
    records = score_reader.read_scores(path, spec['sheets'])
    if index is None:
        index, hints = build_index()
    records, bad = name_index.attach(records, index, hints)
    with_raw = score_reader.has_raw(path, spec['sheets'])
    return _parse(exam, spec, records, with_raw), bad, with_raw


def _write_scores(exam, spec, parsed, mode='B'):
    """落库 + 重排名 + 任课快照 + 清缓存，返回差异摘要。"""
    summary = store_service.apply_import(exam, parsed, mode=mode)
    ranking.recalc_exam(exam.id)
    n_tch = tss.snapshot_exam(exam, source='import')
    exam.status = 'imported'
    exam.import_info = json.dumps({
        'fname': spec['file'], 'mode': mode, 'summary': summary,
        'errors': len(parsed.get('errors') or []),
        'teacher_snapshot': n_tch,
        'operator': 'scripts/seed/import_2025_history.py',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }, ensure_ascii=False)
    db.session.commit()
    tab_service.clear_exam_cache(exam.id)
    summary['teacher_snapshot'] = n_tch
    return summary


def run_scores(spec, apply_, index=None, hints=None, mode='B'):
    """导入一场成绩，返回报告 dict。

    mode='B'：清空本场后重导（新建场次或需要换口径时用）；
    mode='A'：增量覆盖 —— 只改文件里出现的学生，保留库里多出来的
    （历史导入可能保留了缺考学生，重导不该把他们抹掉）。
    """
    exam, created = ensure_exam(spec)
    parsed, bad, with_raw = prepare(exam, spec, index, hints)
    report = {
        'exam': spec['name'], 'exam_id': exam.id, 'created': created,
        'rows': len(parsed['rows']), 'unmatched': bad,
        'errors': parsed.get('errors') or [],
        'with_raw': with_raw, 'applied': False,
    }
    if apply_:
        report['summary'] = _write_scores(exam, spec, parsed, mode)
        report['applied'] = True
    else:
        db.session.rollback()
    return report


def run_bands(spec, apply_):
    """导入一场的特控线 / 本科线，返回报告 dict。"""
    exam, created = ensure_exam(spec)
    lines = line_reader.read_lines(os.path.join(SOURCE_DIR, spec['file']))
    report = {'exam': spec['name'], 'exam_id': exam.id, 'created': created,
              'lines': lines, 'count': 0, 'applied': False}
    if apply_:
        report['count'] = band_writer.replace_bands(exam.id, lines)
        db.session.commit()
        tab_service.clear_exam_cache(exam.id)
        report['applied'] = True
    else:
        db.session.rollback()
    return report

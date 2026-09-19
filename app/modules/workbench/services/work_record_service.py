# 班主任工作记录服务：班会 / 家访 / 谈话记录的增删改查
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import date, datetime

from app.extensions import db
from app.models.academic import WorkRecord, RECORD_TYPES

_VALID_TYPES = {k for k, _ in RECORD_TYPES}


def _parse_date(value):
    """将字符串转为 date，失败返回 None"""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def add_record(teacher_uid, teacher_name, record_type, class_name, date_val,
               title, content='', student_no=None, student_name=None, follow_up=''):
    """新增工作记录，返回新建记录对象；参数非法返回 None"""
    if record_type not in _VALID_TYPES:
        return None
    if not title or not class_name:
        return None

    rec_date = _parse_date(date_val) if not isinstance(date_val, date) else date_val
    if not rec_date:
        return None

    rec = WorkRecord(
        teacher_uid=teacher_uid,
        teacher_name=teacher_name,
        record_type=record_type,
        class_name=class_name,
        date=rec_date,
        title=title.strip()[:100],
        content=(content or '').strip(),
        student_no=(student_no or '').strip() or None,
        student_name=(student_name or '').strip() or None,
        follow_up=(follow_up or '').strip(),
    )
    db.session.add(rec)
    db.session.commit()
    return rec


def get_records(teacher_uid, record_type=None, class_name=None, page=1, per_page=20):
    """获取记录列表（分页 + 筛选），返回 (pagination, records)"""
    q = WorkRecord.query.filter_by(teacher_uid=teacher_uid)
    if record_type and record_type in _VALID_TYPES:
        q = q.filter_by(record_type=record_type)
    if class_name:
        q = q.filter_by(class_name=class_name)
    q = q.order_by(WorkRecord.date.desc(), WorkRecord.created_at.desc())
    pag = q.paginate(page=page, per_page=per_page, error_out=False)
    return pag, pag.items


def update_record(record_id, teacher_uid, **kwargs):
    """编辑记录（验证归属），成功返回记录对象，失败返回 None"""
    rec = WorkRecord.query.filter_by(id=record_id, teacher_uid=teacher_uid).first()
    if not rec:
        return None

    if 'record_type' in kwargs and kwargs['record_type'] in _VALID_TYPES:
        rec.record_type = kwargs['record_type']
    if 'class_name' in kwargs and kwargs['class_name']:
        rec.class_name = kwargs['class_name']
    if 'date' in kwargs:
        d = _parse_date(kwargs['date']) if not isinstance(kwargs['date'], date) else kwargs['date']
        if d:
            rec.date = d
    if 'title' in kwargs and kwargs['title']:
        rec.title = kwargs['title'].strip()[:100]
    if 'content' in kwargs:
        rec.content = (kwargs['content'] or '').strip()
    if 'student_no' in kwargs:
        rec.student_no = (kwargs['student_no'] or '').strip() or None
    if 'student_name' in kwargs:
        rec.student_name = (kwargs['student_name'] or '').strip() or None
    if 'follow_up' in kwargs:
        rec.follow_up = (kwargs['follow_up'] or '').strip()

    rec.updated_at = datetime.now()
    db.session.commit()
    return rec


def delete_record(record_id, teacher_uid):
    """删除记录（验证归属），成功返回 True"""
    rec = WorkRecord.query.filter_by(id=record_id, teacher_uid=teacher_uid).first()
    if not rec:
        return False
    db.session.delete(rec)
    db.session.commit()
    return True


def get_record_detail(record_id):
    """获取单条记录详情"""
    return WorkRecord.query.get(record_id)

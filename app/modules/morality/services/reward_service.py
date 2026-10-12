# StuLink v1.19.47.22 2026-10-13
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""德育奖惩服务层

职责：列表查询（含可见范围过滤）、登记/编辑/删除、状态流转（确认/撤销）、
附件落盘与鉴权下载。

可见范围口径与成绩模块 `grades.services.scope` 保持一致：
  管理员 → 全校；用户级授权年级 → 限定年级；权限组 school/grade → 全校/本年级；
  class → 所带班级；无范围 → 什么都看不到。
学生信息与跨库：奖惩在 morality.db，学生在 system.db —— 跨库不能 join，
一律按「先取允许的年级/班级清单，再用 in_ 过滤」处理（与 academic 分库同口径）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import and_, or_

from app.extensions import db
from app.models.morality import (RewardPunishment, RewardPunishmentAttachment,
                                 KIND_PUNISH, STATUS_CONFIRMED, STATUS_DRAFT,
                                 STATUS_REVOKED, category_options,
                                 level_options)
from app.utils.upload_guard import ext_of, validate_upload

# 附件白名单：文档 + 图片 + PDF（考场记录多为扫描件/照片，证书多为图片或 PDF）
ALLOWED_EXTS = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt',
                'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']
MAX_MB = 10
MAX_PER_FILE = MAX_MB * 1024 * 1024
_PREVIEW_EXTS = {'pdf', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'}

# 常用后缀 → MIME（上传时就定好，下载/预览时直接用，避免每次猜类型）
_MIME = {
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'txt': 'text/plain',
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'bmp': 'image/bmp', 'webp': 'image/webp',
}


def mime_of(ext):
    return _MIME.get((ext or '').lower(), 'application/octet-stream')


# ==================== 可见范围 ====================

def scope_of(user):
    """返回 (scope_type, grade, classes)

    scope_type: 'school' / 'user_grades' / 'grade' / 'class' / 'none'
    """
    if getattr(user, 'role', '') == 'admin':
        return 'school', '', None
    from app.modules.grades.services.scope import user_grade_scope
    ug = user_grade_scope(user)
    if ug is not None:
        return 'user_grades', '', ug
    pg = getattr(user, 'permission_group', None)
    s = pg.scope_type if pg else 'none'
    grade = getattr(user, 'grade', '') or ''
    classes = None
    if s == 'class':
        from app.models import UserClassLink
        classes = [(l.grade, l.class_name)
                   for l in UserClassLink.query.filter_by(user_id=user.id).all()]
    return s, grade, classes


def apply_scope(q, user):
    """把可见范围条件加到查询上"""
    s, grade, classes = scope_of(user)
    if s == 'school':
        return q
    if s == 'user_grades':
        return q.filter(RewardPunishment.grade.in_(classes or []))
    if s == 'grade':
        return (q.filter(RewardPunishment.grade == grade) if grade
                else q.filter(RewardPunishment.id == -1))
    if s == 'class':
        if not classes:
            return q.filter(RewardPunishment.id == -1)
        return q.filter(or_(*[and_(RewardPunishment.grade == g,
                                   RewardPunishment.class_name == c)
                              for g, c in classes]))
    return q.filter(RewardPunishment.id == -1)      # none：什么都看不到


def can_see(user, rec):
    """单条记录是否可见（详情/附件鉴权用）"""
    if getattr(user, 'role', '') == 'admin':
        return True
    s, grade, classes = scope_of(user)
    if s == 'school':
        return True
    if s == 'user_grades':
        return rec.grade in (classes or [])
    if s == 'grade':
        return rec.grade == grade
    if s == 'class':
        return (rec.grade, rec.class_name) in (classes or [])
    return False


# ==================== 列表与统计 ====================

def build_query(f):
    q = RewardPunishment.query
    if f.get('kind'):
        q = q.filter(RewardPunishment.kind == f['kind'])
    if f.get('category'):
        q = q.filter(RewardPunishment.category == f['category'])
    if f.get('level'):
        q = q.filter(RewardPunishment.level == f['level'])
    if f.get('status'):
        q = q.filter(RewardPunishment.status == f['status'])
    if f.get('grade'):
        q = q.filter(RewardPunishment.grade == f['grade'])
    if f.get('class_name'):
        q = q.filter(RewardPunishment.class_name == f['class_name'])
    if f.get('kw'):
        kw = f'%{f["kw"]}%'
        q = q.filter(or_(RewardPunishment.student_name.like(kw),
                         RewardPunishment.student_no.like(kw),
                         RewardPunishment.title.like(kw)))
    if f.get('date_from'):
        q = q.filter(RewardPunishment.happen_date >= f['date_from'])
    if f.get('date_to'):
        q = q.filter(RewardPunishment.happen_date <= f['date_to'])
    return q


def list_records(f, user, page=1, per_page=30):
    q = apply_scope(build_query(f), user)
    q = q.order_by(RewardPunishment.happen_date.desc(),
                   RewardPunishment.id.desc())
    return q.paginate(page=page, per_page=per_page, error_out=False)


def stats(f, user):
    """概览计数：奖励 / 惩罚 / 待确认 / 已生效"""
    base = apply_scope(build_query(f), user)
    return {
        'total': base.count(),
        'reward': base.filter(RewardPunishment.kind == 'reward').count(),
        'punish': base.filter(RewardPunishment.kind == KIND_PUNISH).count(),
        'draft': base.filter(RewardPunishment.status == STATUS_DRAFT).count(),
        'confirmed': base.filter(
            RewardPunishment.status == STATUS_CONFIRMED).count(),
        'revoked': base.filter(RewardPunishment.status == STATUS_REVOKED).count(),
    }


# ==================== 学生查询（跨库，取主库 Student）====================

def search_students(kw, user, limit=20):
    """按学号/姓名查学生（限可见范围），返回 [{student_no, name, grade, class_name}]"""
    from app.models import Student
    kw = (kw or '').strip()
    if not kw:
        return []
    q = Student.query.filter(or_(Student.name.like(f'%{kw}%'),
                                 Student.student_number.like(f'%{kw}%')))
    # 可见范围：跨库不能 join，先取允许的 (grade, class) 清单再过滤
    s, grade, classes = scope_of(user)
    if s == 'grade' and grade:
        q = q.filter(Student.grade == grade)
    elif s == 'class':
        if not classes:
            return []
        q = q.filter(or_(*[and_(Student.grade == g, Student.class_name == c)
                           for g, c in classes]))
    elif s == 'user_grades':
        q = q.filter(Student.grade.in_(classes or []))
    elif s == 'none':
        return []
    rows = q.limit(limit).all()
    return [{'student_no': r.student_number, 'name': r.name,
             'grade': r.grade, 'class_name': r.class_name} for r in rows]


# ==================== 写操作 ====================

def _parse_date(v):
    v = (v or '').strip()
    if not v:
        return None
    try:
        return datetime.strptime(v, '%Y-%m-%d').date()
    except ValueError:
        return None


def _norm(rec, form):
    """把表单字段落到记录上"""
    rec.student_no = (form.get('student_no') or '').strip()
    rec.student_name = (form.get('student_name') or '').strip()
    rec.grade = (form.get('grade') or '').strip()
    rec.class_name = (form.get('class_name') or '').strip()
    rec.kind = (form.get('kind') or 'reward').strip()
    rec.category = (form.get('category') or '').strip()
    rec.level = (form.get('level') or '').strip()
    rec.title = (form.get('title') or '').strip()
    rec.description = (form.get('description') or '').strip() or None
    rec.happen_date = _parse_date(form.get('happen_date'))
    rec.source = (form.get('source') or '').strip() or None
    rec.award_name = (form.get('award_name') or '').strip() or None
    # 惩罚专属
    rec.related_exam = (form.get('related_exam') or '').strip() or None
    rec.subject = (form.get('subject') or '').strip() or None
    rec.effect = (form.get('effect') or '').strip() or None
    pm = (form.get('period_months') or '').strip()
    rec.period_months = int(pm) if pm.isdigit() and int(pm) > 0 else None
    rec.expire_date = _parse_date(form.get('expire_date'))
    if not rec.expire_date and rec.period_months and rec.happen_date:
        # 未手填到期日时，按「发生日 + 期限」推算，便于列表提示临期
        rec.expire_date = rec.happen_date + timedelta(days=30 * rec.period_months)
    rec.notify_parent = bool(form.get('notify_parent'))


def _validate(form):
    if not (form.get('student_no') or '').strip():
        return '请选择/填写学号'
    if not (form.get('title') or '').strip():
        return '请填写事由'
    kind = (form.get('kind') or '').strip()
    if kind not in ('reward', 'punish'):
        return '类型只能是奖励或惩罚'
    cat = (form.get('category') or '').strip()
    if cat and cat not in [k for k, _ in category_options(kind)]:
        return '类别与类型不匹配'
    lvl = (form.get('level') or '').strip()
    if lvl and lvl not in [k for k, _ in level_options(kind)]:
        return '级别与类型不匹配'
    return ''


def create(form, user, files=None):
    err = _validate(form)
    if err:
        return False, err, None
    rec = RewardPunishment()
    _norm(rec, form)
    rec.operator_id = getattr(user, 'id', None)
    rec.operator_name = (getattr(user, 'real_name', None)
                         or getattr(user, 'username', None))
    db.session.add(rec)
    db.session.commit()

    errors = []
    for f in (files or []):
        ok, msg, _att = save_attachment(rec, f, user,
                                        doc_type=(form.get('doc_type') or '').strip())
        if not ok:
            errors.append(f'{f.filename}：{msg}')
    return True, ('；'.join(errors) if errors else '已登记'), rec


def update(rec, form, user):
    err = _validate(form)
    if err:
        return False, err
    _norm(rec, form)
    db.session.commit()
    return True, '已保存'


def delete(rec):
    """删除记录（含落盘附件清理）"""
    atts = RewardPunishmentAttachment.query.filter_by(record_id=rec.id).all()
    for att in atts:
        root, stored = attachment_path(att)
        if root:
            try:
                os.remove(os.path.join(root, stored))
            except OSError:
                pass
        db.session.delete(att)
    db.session.delete(rec)
    db.session.commit()
    # 目录空了就清掉，避免留下空文件夹
    try:
        root = attachment_root(rec.id)
        if os.path.isdir(root) and not os.listdir(root):
            os.rmdir(root)
    except OSError:
        pass


def set_status(rec, status, user, reason=''):
    if status not in (STATUS_DRAFT, STATUS_CONFIRMED, STATUS_REVOKED):
        return False, '状态不合法'
    rec.status = status
    if status == STATUS_CONFIRMED:
        rec.confirmed_by = (getattr(user, 'real_name', None)
                            or getattr(user, 'username', None))
        rec.confirmed_at = datetime.now()
        rec.revoked_at = None
        rec.revoke_reason = None
    elif status == STATUS_REVOKED:
        rec.revoked_at = datetime.now()
        rec.revoke_reason = (reason or '').strip() or None
    else:
        rec.confirmed_by = None
        rec.confirmed_at = None
        rec.revoked_at = None
        rec.revoke_reason = None
    db.session.commit()
    return True, '状态已更新'


# ==================== 附件 ====================

def attachment_root(record_id):
    """附件目录（绝对路径）。目录名只由记录 id 拼成，天然不含用户输入。"""
    return os.path.abspath(os.path.join(current_app.static_folder, 'uploads',
                                        'morality', str(int(record_id))))


def save_attachment(rec, storage, user=None, doc_type=None):
    """保存一个上传文件 → (ok, message, attachment|None)

    校验顺序：扩展名白名单 / 危险类型 / magic 嗅探 → 落盘 → 大小限制
    （超限立即删除落盘文件，不留半截垃圾）。
    """
    if storage is None or not (storage.filename or '').strip():
        return False, '没有选择文件', None
    name = storage.filename.strip()
    ok, msg = validate_upload(name, allowed_exts=ALLOWED_EXTS, stream=storage.stream)
    if not ok:
        return False, msg, None

    ext = ext_of(name)
    root = attachment_root(rec.id)
    os.makedirs(root, exist_ok=True)
    stored = f'{uuid.uuid4().hex}.{ext}'
    path = os.path.join(root, stored)
    try:
        storage.save(path)
    except Exception as exc:                       # noqa: BLE001
        return False, f'文件保存失败：{exc}', None

    size = os.path.getsize(path)
    if size > MAX_PER_FILE:
        try:
            os.remove(path)
        except OSError:
            pass
        return False, (f'文件超过 {MAX_MB}MB'
                       f'（当前 {size / 1024 / 1024:.1f}MB）'), None

    att = RewardPunishmentAttachment(
        record_id=rec.id, file_name=name[:200], stored_name=stored, ext=ext,
        mime=mime_of(ext), size=size,
        uploaded_by=getattr(user, 'id', None),
        uploaded_name=(getattr(user, 'real_name', None)
                       or getattr(user, 'username', None)),
        doc_type=(doc_type or None))
    db.session.add(att)
    db.session.commit()
    return True, '上传成功', att


def delete_attachment(att):
    root, stored = attachment_path(att)
    if root:
        try:
            os.remove(os.path.join(root, stored))
        except OSError:
            pass
    db.session.delete(att)
    db.session.commit()


def attachment_path(att):
    """返回 (root, stored_name)；越界或文件缺失返回 (None, None)"""
    root = attachment_root(att.record_id)
    stored = os.path.basename(att.stored_name or '')
    if not stored:
        return None, None
    target = os.path.abspath(os.path.join(root, stored))
    if os.path.commonpath([root, target]) != root or not os.path.isfile(target):
        return None, None
    return root, stored


def is_previewable(ext):
    return (ext or '').lower() in _PREVIEW_EXTS

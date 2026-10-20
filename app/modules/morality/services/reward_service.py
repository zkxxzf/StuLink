# StuLink v1.19.49.47 2026-10-13
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
    P2：解析委托 app.utils.scope 唯一口径（行为与改造前一致）。
    """
    from app.utils.scope import resolve_scope
    s = resolve_scope(user)
    if s.origin in ('admin', 'school'):
        return 'school', '', None
    if s.origin == 'user_grades':
        # 2026-10-14 第四轮审计修复：用户级范围带班级白名单时精确到班（不再放大为整年级）
        if s.kind == 'classes':
            return 'class', '', set(s.classes)
        return 'user_grades', '', set(s.grades)
    if s.origin == 'grade':
        return 'grade', (getattr(user, 'grade', '') or ''), None
    if s.origin == 'class':
        return 'class', '', set(s.classes)
    return 'none', '', None


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


def _class_in_scope(user, grade, class_name):
    """(grade, class_name) 是否在德育可见范围（与 can_see 同口径，未配置 → False）"""
    if getattr(user, 'role', '') == 'admin':
        return True
    s, g, classes = scope_of(user)
    if s == 'school':
        return True
    if s == 'user_grades':
        return bool(grade) and grade in (classes or set())
    if s == 'grade':
        return bool(grade) and grade == g
    if s == 'class':
        return (grade or '', class_name or '') in (classes or set())
    return False


def _student_in_scope(user, student):
    """学生是否在德育可见范围（按学籍当前年级/班级）"""
    if student is None:
        return False
    return _class_in_scope(user, student.grade, student.class_name)


def _student_of(form):
    """按学号取学籍学生（不存在返回 None）"""
    from app.models import Student
    sn = (form.get('student_no') or '').strip()
    if not sn:
        return None
    return Student.query.filter_by(student_number=sn).first()


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
    # 2026-10-19 第十三轮审计：原实现把表单里的年级/班级直接落库，
    # 只校验「学号非空」→ 持 morality.edit 的班主任可替他年级学生登记奖惩
    # （写入后自己反而看不到，等于匿名登记处分）。现校验学籍 + 数据范围，
    # 年级/班级/姓名一律以学籍档案为准。
    student = _student_of(form)
    if student is None:
        return False, '学号不存在，请从学生列表中选择', None
    if not _student_in_scope(user, student):
        return False, '无权登记该学生的奖惩（不在你的数据范围内）', None
    rec = RewardPunishment()
    _norm(rec, form)
    rec.student_no = student.student_number
    rec.student_name = student.name
    rec.grade = student.grade
    rec.class_name = student.class_name
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
    # 2026-10-19 第十四轮：已生效的记录不允许直接改内容 —— 否则
    # 「确认后悄悄改标题/等级/日期」不会留下任何痕迹，破坏奖惩留档的可信度。
    # 需要更正请先撤销（撤销会留痕），再改/重申。
    if rec.status == STATUS_CONFIRMED:
        return False, '已生效的记录不能修改，请先撤销后再更正'
    err = _validate(form)
    if err:
        return False, err
    # 2026-10-19 第十三轮审计：编辑同样要校验目标范围（可把本班记录
    # 改成他年级）——有学籍的按学籍判定，无学籍的历史记录按表单班级判定。
    student = _student_of(form)
    if student is not None:
        if not _student_in_scope(user, student):
            return False, '无权修改为该学生的记录'
    elif not _class_in_scope(user, (form.get('grade') or '').strip(),
                             (form.get('class_name') or '').strip()):
        return False, '无权修改到该班级'
    _norm(rec, form)
    if student is not None:
        rec.student_no = student.student_number
        rec.student_name = student.name
        rec.grade = student.grade
        rec.class_name = student.class_name
    db.session.commit()
    return True, '已保存'


def delete(rec):
    """删除记录 → 先归档进回收站，再删库。

    2026-10-19 第十三轮审计：原实现直接 `os.remove` 附件 + 删行，误删后
    库与文件同时不可恢复（用户级规则：一切删除可恢复）。现在记录与附件
    元数据都进回收站；落盘文件**保留**，恢复后仍可下载。
    """
    atts = RewardPunishmentAttachment.query.filter_by(record_id=rec.id).all()
    from app.utils.trash import archive
    # 2026-10-20 第十七轮审计：归档失败即中止（fail-closed），原来返回值被丢弃 →
    # 归档抛错时记录仍被真删、回收站空白，界面却提示"已删除（可恢复）"。
    _recs = [archive('morality', 'reward_punishment', '奖惩记录', rec,
                     summary=f'{rec.student_name} {rec.title}')]
    if atts:
        _recs.append(archive('morality', 'reward_attachment', '奖惩附件', atts,
                            summary=f'奖惩记录 #{rec.id}：{len(atts)} 个附件'))
    if any(r is None for r in _recs):
        db.session.rollback()
        return False
    for att in atts:
        db.session.delete(att)
    db.session.delete(rec)
    db.session.commit()
    return True


def set_status(rec, status, user, reason=''):
    if status not in (STATUS_DRAFT, STATUS_CONFIRMED, STATUS_REVOKED):
        return False, '状态不合法'
    # 2026-10-19 第十四轮：状态机只允许「前进」——
    # 待确认 → 已生效 / 已撤销；已生效 → 已撤销；已撤销 → 已生效（误撤销可恢复）。
    # 不允许任何状态退回「待确认」（原来已生效/已撤销都能被"退回草稿"抹掉审核痕迹）。
    if status == STATUS_DRAFT and rec.status != STATUS_DRAFT:
        return False, '已生效/已撤销的记录不能退回「待确认」，如需更正请撤销后重新登记'
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
    # P1 M-19：状态流转（登记→确认/撤销）补审计
    try:
        from app.utils.helpers import log_operation
        log_operation(user, '状态变更', '德育奖惩', rec.id,
                      f"{getattr(rec, 'student_name', '') or rec.id} → {status}",
                      module='morality')
    except Exception:  # noqa: BLE001 审计失败不阻断业务
        pass
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

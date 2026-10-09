# StuLink v1.18.7.1 2026-09-30
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教师业绩库：标签 + 附件（2026-09-26）。

原来业绩库只有「类别 / 级别 / 状态」三个维度，教务实际要按用途检索
（竞赛辅导、论文发表、继续教育…），并且要能**看到证书原件**（扫描件照片、PDF）。
本模块提供：

- 标签：逗号分隔存储，解析/统计/常用预设；
- 附件：落到 `app/static/uploads/achievements/<achievement_id>/`，
  上传统一走 `app/utils/upload_guard.validate_upload`（白名单 + 危险类型 + magic 嗅探），
  落盘名用 uuid（杜绝路径穿越与同名覆盖），单文件默认 ≤ 10MB；
- 预览：图片与 PDF 可内联查看，其它类型强制下载（不内联渲染）。

下载/预览只允许两类人：有 `academic.view` 的教务端，或该条业绩的提交者本人。
"""
import os
import re
import uuid

from app.extensions import db
from app.models.academic import AchievementAttachment, TeacherAchievement
from app.utils.upload_guard import ext_of, validate_upload

# 常用标签（录入时下拉建议；仍可自由输入）
TAG_PRESETS = ['教学成果', '课题立项', '论文发表', '竞赛辅导', '荣誉表彰',
               '继续教育', '公开课', '指导学生', '校本课程', '教学能手',
               '班主任', '支教交流', '信息化教学']

# 业绩附件白名单：图片 + PDF + 常用文档（证书扫描件、红头文件、课题材料）
ALLOWED_EXTS = ['pdf', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp',
                'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'zip']
MAX_MB = 10
MAX_PER_FILE = MAX_MB * 1024 * 1024

_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'}
_MIME = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
         'gif': 'image/gif', 'bmp': 'image/bmp', 'webp': 'image/webp',
         'pdf': 'application/pdf'}


# ── 标签 ────────────────────────────────────────────────────────────────────

def parse_tags(raw):
    """「课题, 省级 数学」→ ['课题', '省级', '数学']（去重、限长、最多 8 个）"""
    out = []
    for part in re.split(r'[,，、;；|\s]+', str(raw or '')):
        tag = part.strip()[:12]
        if tag and tag not in out:
            out.append(tag)
    return out[:8]


def tags_of(rec):
    return parse_tags(getattr(rec, 'tags', '') or '')


def all_tags(limit=40):
    """库中已用标签及计数（标签云用），按次数降序。"""
    counter = {}
    for (raw,) in db.session.query(TeacherAchievement.tags).all():
        for tag in parse_tags(raw):
            counter[tag] = counter.get(tag, 0) + 1
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def set_tags(rec, raw):
    """写入标签（超长截断；空值置 None 便于筛选）"""
    tags = parse_tags(raw)
    rec.tags = ','.join(tags) if tags else None
    db.session.commit()
    return tags


# ── 附件 ────────────────────────────────────────────────────────────────────

def mime_of(ext):
    return _MIME.get((ext or '').lower(), 'application/octet-stream')


def is_previewable(ext):
    """图片与 PDF 可以内联预览；其它一律下载，避免浏览器渲染意外内容。"""
    e = (ext or '').lower()
    return e in _IMAGE_EXTS or e == 'pdf'


def is_image(ext):
    return (ext or '').lower() in _IMAGE_EXTS


def attachment_root(achievement_id):
    """附件目录（绝对路径）。目录名只由业绩 id 拼成，天然不含用户输入。"""
    from flask import current_app
    return os.path.abspath(os.path.join(current_app.static_folder, 'uploads',
                                        'achievements', str(int(achievement_id))))


def list_attachments(rec_or_id):
    aid = rec_or_id.id if hasattr(rec_or_id, 'id') else int(rec_or_id)
    return (AchievementAttachment.query.filter_by(achievement_id=aid)
            .order_by(AchievementAttachment.id).all())


def attachment_counts(ids):
    """{achievement_id: 附件数}（列表页显示徽章用，一次查询避免 N+1）"""
    ids = [i for i in (ids or [])]
    if not ids:
        return {}
    from sqlalchemy import func
    rows = (db.session.query(AchievementAttachment.achievement_id,
                             func.count(AchievementAttachment.id))
            .filter(AchievementAttachment.achievement_id.in_(ids))
            .group_by(AchievementAttachment.achievement_id).all())
    return {aid: n for aid, n in rows}


def save_attachment(rec, storage, user=None):
    """保存一个上传文件 → (ok, message, attachment|None)。

    校验顺序：白名单/危险类型/magic 嗅探（upload_guard）→ 落盘 → 大小限制
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
    except Exception as exc:  # noqa: BLE001  磁盘异常不该把 500 抛给用户
        return False, f'文件保存失败：{exc}', None

    size = os.path.getsize(path)
    if size > MAX_PER_FILE:
        try:
            os.remove(path)
        except OSError:
            pass
        return False, f'文件超过 {MAX_MB}MB（当前 {size / 1024 / 1024:.1f}MB）', None

    att = AchievementAttachment(
        achievement_id=rec.id, file_name=name[:200], stored_name=stored, ext=ext,
        mime=mime_of(ext), size=size,
        uploaded_by=getattr(user, 'id', None),
        uploaded_name=(getattr(user, 'real_name', None)
                       or getattr(user, 'username', None)))
    db.session.add(att)
    db.session.commit()
    return True, '上传成功', att


def delete_attachment(att):
    """删除附件记录与落盘文件（文件不存在也照样删记录）"""
    root = attachment_root(att.achievement_id)
    stored = os.path.basename(att.stored_name or '')
    target = os.path.abspath(os.path.join(root, stored))
    if stored and os.path.commonpath([root, target]) == root and os.path.isfile(target):
        try:
            os.remove(target)
        except OSError:
            pass
    db.session.delete(att)
    db.session.commit()


def attachment_path(att):
    """返回 (root, stored_name) 供下载路由使用；越界或文件缺失返回 (None, None)。"""
    root = attachment_root(att.achievement_id)
    stored = os.path.basename(att.stored_name or '')
    if not stored:
        return None, None
    target = os.path.abspath(os.path.join(root, stored))
    if os.path.commonpath([root, target]) != root or not os.path.isfile(target):
        return None, None
    return root, stored


def can_view(rec, att, user):
    """预览/下载权限：教务端(academic.view) 或 该条业绩的提交者本人。"""
    if user is None:
        return False
    try:
        if user.has_perm('academic.view'):
            return True
    except Exception:  # noqa: BLE001
        pass
    if rec is not None and rec.submitted_by and rec.submitted_by == getattr(user, 'id', None):
        return True
    return False


# ── 详情（供详情抽屉/模态使用） ──────────────────────────────────────────────

def detail_dict(rec, categories=None, levels=None, statuses=None):
    """业绩详情 JSON（含标签与附件清单）"""
    from app.models.academic import ACHIEVEMENT_CATEGORIES, ACHIEVEMENT_STATUS
    cat_map = dict(categories or ACHIEVEMENT_CATEGORIES)
    status_map = dict(statuses or ACHIEVEMENT_STATUS)
    return {
        'id': rec.id,
        'teacher_uid': rec.teacher_uid,
        'teacher_name': rec.teacher_name,
        'category': rec.category,
        'category_text': cat_map.get(rec.category, rec.category),
        'title': rec.title,
        'level': rec.level or '',
        'obtain_date': rec.obtain_date.strftime('%Y-%m-%d') if rec.obtain_date else '',
        'issuer': rec.issuer or '',
        'note': rec.note or '',
        'tags': tags_of(rec),
        'status': rec.status,
        'status_text': status_map.get(rec.status, rec.status),
        'review_note': rec.review_note or '',
        'reviewed_at': (rec.reviewed_at.strftime('%Y-%m-%d %H:%M')
                        if rec.reviewed_at else ''),
        'attachments': [dict(a.to_dict(), previewable=is_previewable(a.ext),
                             is_image=is_image(a.ext))
                        for a in list_attachments(rec)],
    }

# StuLink v1.18.8.0 2026-10-09
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

# 业绩附件白名单：2026-10-09 起统一只收 PDF（扫描件/通知书/课题材料一律转 PDF，
# 便于长期归档、预览一致、也避免 zip/doc 这类可执行风险）。
# 注：历史已上传的非 PDF 附件仍可正常预览/下载，只是新上传只接受 PDF。
ALLOWED_EXTS = ['pdf']
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


# ── 按类别的动态字段（课题编号/立项结题/期刊刊号/学时…）──────────────────────

def fields_of(category):
    """该类别要补的字段定义 [(key, label, type)]，type ∈ text/date/select:...。"""
    from app.models.academic import ACHIEVEMENT_FIELDS
    out = []
    for item in ACHIEVEMENT_FIELDS.get(category or '', []):
        if len(item) >= 3:
            out.append((item[0], item[1], item[2]))
        else:
            out.append((item[0], item[1], 'text'))
    return out


def fields_map():
    """{类别: 字段定义}（页面 JS 按类别动态渲染表单用）。"""
    from app.models.academic import ACHIEVEMENT_FIELDS
    return {k: fields_of(k) for k in ACHIEVEMENT_FIELDS}


def set_extra(rec, form):
    """把表单里 x_<key> 的动态字段写进 extra_json（只存有值的）。"""
    import json
    data = {}
    for key, _label, _t in fields_of(getattr(rec, 'category', '')):
        v = (form.get('x_' + key) or '').strip() if hasattr(form, 'get') else ''
        if v:
            data[key] = v[:100]
    rec.extra_json = json.dumps(data, ensure_ascii=False) if data else None
    return data


def extra_pairs(rec):
    """[(label, value)] 供详情/导出展示（只列有值的）。"""
    data = rec.extra() if hasattr(rec, 'extra') else {}
    out = []
    for key, label, _t in fields_of(getattr(rec, 'category', '')):
        v = str(data.get(key) or '').strip()
        if v:
            out.append((label, v))
    return out


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


def save_attachment(rec, storage, user=None, doc_type=None):
    """保存一个上传文件 → (ok, message, attachment|None)。

    doc_type：材料分类（立项通知书/中期材料/结题材料…），见 models.DOC_TYPES。
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
                       or getattr(user, 'username', None)),
        doc_type=(doc_type or None))
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
    from app.models.academic import (ACHIEVEMENT_CATEGORIES, ACHIEVEMENT_SOURCE,
                                     ACHIEVEMENT_STATUS)
    cat_map = dict(categories or ACHIEVEMENT_CATEGORIES)
    status_map = dict(statuses or ACHIEVEMENT_STATUS)
    source_map = dict(ACHIEVEMENT_SOURCE)
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
        # 2026-10-09：按类别的动态字段（课题编号/立项结题/期刊刊号/学时…）
        # extra = 原始 dict（编辑表单回填用）；extra_pairs = 展示用 [{'label','value'}]
        'extra': rec.extra() if hasattr(rec, 'extra') else {},
        'extra_pairs': [{'label': k, 'value': v} for k, v in extra_pairs(rec)],
        # 2026-10-10：来源追溯（表单收集审核通过后自动入账）
        'source_type': rec.source_type or '',
        'source_type_text': source_map.get(rec.source_type,
                                           '未标注' if not rec.source_type else rec.source_type),
        'source_label': rec.source_label or '',
        'source_id': rec.source_id,
        'source_round_id': rec.source_round_id,
        # 来源=表单收集时，附该次提交的答案与附件，供详情抽屉直接看原件
        'submission': submission_brief(rec),
    }


# ── 表单收集 → 业绩入账（2026-10-10） ──────────────────────────────────────────

def _answer_of(sub, question_id):
    """取该次提交里某一题的文本答案（文件题返回空串）。"""
    for a in getattr(sub, 'answers', None) or []:
        if a.question_id == question_id:
            return (a.answer_text or '').strip()
    return ''


def _answer_display(a):
    """答案展示文本：多选展开、文件显示原名、其余原样。"""
    import json as _json
    if a.file_path:
        return a.file_name or ''
    if a.answer_json:
        try:
            vals = _json.loads(a.answer_json)
            return '、'.join(str(v) for v in vals) if isinstance(vals, list) else str(vals)
        except (ValueError, TypeError):
            return a.answer_json or ''
    return a.answer_text or ''


def create_from_submission(sub, reviewer_id=None):
    """表单收集审核通过 → 生成一条「已通过」业绩（幂等）。

    规则全部来自发起收集时在模板上配好的映射：类别 / 级别 / 名称（模板标题或
    指定题目的答案）/ 标签。附件不复制，业绩详情通过 source_id 回看表单原件。

    返回 (rec, msg)：未开启计入、找不到教师等返回 (None, 原因)。
    """
    from datetime import datetime

    from app.models.academic import FormRound, FormTemplate, Teacher

    tpl = db.session.get(FormTemplate, sub.template_id)
    if not tpl or not tpl.to_achievement:
        return None, '该收集未开启计入业绩'

    exists = TeacherAchievement.query.filter_by(source_type='form',
                                                source_id=sub.id).first()
    if exists:
        return exists, '该提交已入账，跳过'

    # 教师归属：submitter_uid 归一后即 teacher_uid；历史数据兜底按 users.id 回查
    teacher = None
    if sub.submitter_uid:
        teacher = Teacher.query.filter_by(teacher_uid=sub.submitter_uid).first()
    if teacher is None and sub.submitter_id:
        teacher = Teacher.query.filter_by(user_id=sub.submitter_id).first()
    if teacher is None:
        return None, f'未找到提交人对应的教师：{sub.submitter_name or sub.submitter_id}'

    title = (tpl.title or '').strip()
    if tpl.ach_title_mode == 'question' and tpl.ach_title_question_id:
        title = _answer_of(sub, tpl.ach_title_question_id) or title
    if not title:
        return None, '业绩名称为空（模板标题与指定题目答案都为空）'

    rnd = db.session.get(FormRound, sub.round_id) if sub.round_id else None
    rec = TeacherAchievement(
        teacher_uid=teacher.teacher_uid,
        teacher_name=teacher.name,
        category=tpl.ach_category or 'other',
        title=title[:100],
        level=tpl.ach_level or None,
        obtain_date=(sub.submitted_at.date() if sub.submitted_at else None),
        tags=(','.join(parse_tags(tpl.ach_tags)) or None),
        status='approved',
        submitted_by=sub.submitter_id,
        reviewed_by=reviewer_id,
        reviewed_at=datetime.now(),
        source_type='form',
        source_id=sub.id,
        source_round_id=(rnd.id if rnd else None),
        source_label=((rnd.label() if rnd else None) or (tpl.title or None)),
    )
    db.session.add(rec)
    db.session.commit()
    return rec, 'ok'


def submission_brief(rec):
    """来源=表单收集时，回看该次提交的答案与附件（附件走 academic.form_file 鉴权）。

    不属于表单来源返回 None。文件不复制进业绩目录，这里只给带鉴权的访问链接。
    """
    if getattr(rec, 'source_type', None) != 'form' or not rec.source_id:
        return None
    from flask import url_for

    from app.models.academic import FormRound, FormSubmission, FormTemplate

    sub = db.session.get(FormSubmission, rec.source_id)
    if not sub:
        return None
    tpl = db.session.get(FormTemplate, sub.template_id)
    rnd = db.session.get(FormRound, sub.round_id) if sub.round_id else None
    qmap = {q.id: q for q in (tpl.questions if tpl else [])}

    items = []
    for a in sub.answers:
        q = qmap.get(a.question_id)
        files = []
        if a.file_path:
            files.append({
                'name': a.file_name or os.path.basename(a.file_path),
                'size_text': f'{(a.file_size or 0) / 1024:.0f} KB' if a.file_size else '',
                'url': url_for('academic.form_file', form_id=sub.template_id,
                               rel_path=a.file_path),
            })
        items.append({
            'title': (q.title if q else f'题目#{a.question_id}'),
            'type': (q.question_type if q else ''),
            'text': _answer_display(a),
            'files': files,
        })
    return {
        'template_title': (tpl.title if tpl else ''),
        'round_label': (rnd.label() if rnd else ''),
        'submitted_at': (sub.submitted_at.strftime('%Y-%m-%d %H:%M')
                         if sub.submitted_at else ''),
        'status': sub.status,
        'review_note': sub.review_note or '',
        'items': items,
        'list_url': url_for('academic.form_submissions',
                            form_id=sub.template_id),
    }

# StuLink v1.19.50.59 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序业绩接口：我的业绩（只读）。

与教师工作台同口径：按 teacher_uid 取本人业绩，登录即可访问；
不提供提交 / 修改 / 审核（需要 academic.edit，仍走网页端）。
"""
import os

from flask import g, request, send_file

from app.extensions import db
from app.models import TeacherAchievement
from app.models.academic import (
    ACHIEVEMENT_CATEGORIES,
    ACHIEVEMENT_LEVELS,
    ACHIEVEMENT_STATUS,
    AchievementAttachment,
)
from app.modules.academic.services import achievement_service as ach
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_login_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.modules.wxapi.academic.routes_schedule import _teacher_uid
from app.utils.text_guard import safe_download_name

LIMIT = 100
CATEGORY_TEXT = dict(ACHIEVEMENT_CATEGORIES)
LEVEL_TEXT = {v: v for v in ACHIEVEMENT_LEVELS}
STATUS_TEXT = dict(ACHIEVEMENT_STATUS)


@bp.route('/achievements/mine')
@wx_login_required
def wx_achievements_mine():
    """我的业绩（query: status=pending|approved|rejected）。"""
    uid = _teacher_uid(g.wx_user)
    if not uid:
        return wx_ok({'items': [], 'total': 0, 'stats': _empty_stats(),
                      'statuses': _status_options()})
    rows = (TeacherAchievement.query
            .filter(TeacherAchievement.teacher_uid == uid)
            .order_by(TeacherAchievement.created_at.desc())
            .limit(LIMIT).all())
    stats = _empty_stats()
    for r in rows:
        stats[r.status or 'pending'] = stats.get(r.status or 'pending', 0) + 1
        stats['total'] += 1
    status = (request.args.get('status') or '').strip()
    if status in STATUS_TEXT:
        rows = [r for r in rows if (r.status or 'pending') == status]
    files = _files_of([r.id for r in rows])
    return wx_ok({
        'items': [_row(r, files.get(r.id, [])) for r in rows],
        'total': len(rows),
        'stats': stats,
        'statuses': _status_options(),
    })


def _files_of(ids):
    """一次取出多条业绩的附件元数据（{achievement_id: [...]}）。"""
    if not ids:
        return {}
    out = {}
    rows = (AchievementAttachment.query
            .filter(AchievementAttachment.achievement_id.in_(ids))
            .order_by(AchievementAttachment.id).all())
    for att in rows:
        out.setdefault(att.achievement_id, []).append({
            'id': att.id,
            'file_name': att.file_name or '',
            'ext': att.ext or '',
            'size': att.size or 0,
            'mime': att.mime or '',
        })
    return out


@bp.route('/achievements/attachment/<int:att_id>')
@wx_login_required
def wx_achievement_attachment(att_id):
    """下载本人业绩附件（只允许本人，避免越权拿到别人的证书）。"""
    att = db.session.get(AchievementAttachment, att_id)
    if att is None:
        return wx_err('附件不存在', 404)
    rec = db.session.get(TeacherAchievement, att.achievement_id)
    uid = _teacher_uid(g.wx_user)
    # 本人（teacher_uid）或本人提交（旧数据可能没写 teacher_uid）
    # 2026-10-19 第十三轮审计：`rec.teacher_uid != uid` 在双方都为 None 时为 False
    # → 无教师档案的账号可下载 teacher_uid 为空的旧业绩附件（None 短路）。
    own = (uid is not None and rec is not None and rec.teacher_uid == uid)
    submitted = rec is not None and rec.submitted_by == g.wx_user.id
    if not (own or submitted):
        return wx_err('无权查看该附件', 403)
    root, stored = ach.attachment_path(att)
    if not root:
        return wx_err('附件文件缺失', 404)
    return send_file(os.path.join(root, stored), as_attachment=True,
                     download_name=safe_download_name(att.file_name or stored))


def _row(r, files):
    """业绩行：类别 / 名称 / 级别 / 时间 / 状态 / 审核意见 / 附件。"""
    return {
        'id': r.id,
        'category': r.category or '',
        'category_text': CATEGORY_TEXT.get(r.category, r.category or ''),
        'title': r.title or '',
        'level': r.level or '',
        'level_text': LEVEL_TEXT.get(r.level, r.level or ''),
        'obtain_date': r.obtain_date.strftime('%Y-%m-%d')
        if r.obtain_date else '',
        'issuer': r.issuer or '',
        'note': r.note or '',
        'tags': [t for t in (r.tags or '').split(',') if t],
        'status': r.status or 'pending',
        'status_text': STATUS_TEXT.get(r.status or 'pending', ''),
        'review_note': r.review_note or '',
        'reviewed_at': r.reviewed_at.strftime('%Y-%m-%d')
        if r.reviewed_at else '',
        'attachments': len(files),
        'files': files,
        'created_at': r.created_at.strftime('%Y-%m-%d')
        if r.created_at else '',
    }


def _empty_stats():
    """统计初始值。"""
    out = {'total': 0, 'pending': 0, 'approved': 0, 'rejected': 0}
    return out


def _status_options():
    """状态筛选项。"""
    return [{'key': k, 'label': v} for k, v in ACHIEVEMENT_STATUS.items()]

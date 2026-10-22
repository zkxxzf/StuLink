# StuLink v1.19.62.65 2026-10-22
# AI 报告分享链接：token 生成 / 解析 / 撤销 / 访问计数
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 报告分享链接的服务层（2026-10-22）。

设计口径（重要，改动前先读）：
  · AI 分析默认「**仅生成者本人可见**」（2026-10-18 用户最终口径，管理员也不例外）。
    分享是生成者**主动、可撤销**的一次授权，只放开"这一份报告 + 这个 token"，
    不新增任何"登录即可看全部"的开关。
  · 一个报告**同时最多一条有效链接**：再次分享复用同一条；过期后再分享会新建，
    并把旧的（已过期）标记撤销，避免同一份报告散出多个还能点开的地址。
  · token 用 `secrets.token_urlsafe` 随机生成（不可枚举），库里唯一；
    链接格式 `/s/<token>`，打开需要登录（未登录由 Flask-Login 带去登录页再跳回）。
  · 有效期：0 = 永久；上限 MAX_DAYS 天（防"永久链接"被随手发到校外群）。
"""
import secrets
from datetime import datetime, timedelta

from app.extensions import db
from app.models.grades import AiReport, AiReportShare

TOKEN_BYTES = 16                 # token_urlsafe(16) ≈ 22 字符
MAX_DAYS = 365                   # 有效期上限（天）
VIEW_KEEP = 20                   # 会话里去重的 token 记忆条数

# resolve() 的返回原因（页面据此给不同文案）
REASON_OK = 'ok'
REASON_MISSING = 'missing'
REASON_REVOKED = 'revoked'
REASON_EXPIRED = 'expired'
REASON_GONE = 'gone'


def _new_token():
    """生成库里唯一的分享 token（碰撞概率可忽略，仍做一次查重兜底）。"""
    while True:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        if not AiReportShare.query.filter_by(token=token).first():
            return token


def active_for(report_id):
    """该报告当前**唯一有效**的分享（未撤销且未过期）；没有则返回 None。"""
    now = datetime.now()
    rows = (AiReportShare.query
            .filter(AiReportShare.report_id == report_id,
                    AiReportShare.revoked_at.is_(None))
            .order_by(AiReportShare.id.desc()).all())
    for row in rows:
        if row.expires_at is None or row.expires_at > now:
            return row
    return None


def create_or_reuse(report, days=30, operator_id=None):
    """创建/复用分享链接，返回 (share, created)。

    days: 0/None = 永久；负数按 0；超过 MAX_DAYS 截断。
    已有有效链接时直接返回它（created=False），页面上不会出现两条链接。
    """
    cur = active_for(report.id)
    if cur is not None:
        return cur, False
    # 过期残留一律标记撤销：一个报告只保留一条"活"链接
    now = datetime.now()
    (AiReportShare.query
     .filter(AiReportShare.report_id == report.id,
             AiReportShare.revoked_at.is_(None))
     .update({'revoked_at': now}, synchronize_session=False))
    days = int(days or 0)
    if days < 0:
        days = 0
    if days > MAX_DAYS:
        days = MAX_DAYS
    share = AiReportShare(
        report_id=report.id,
        token=_new_token(),
        created_by=operator_id or report.user_id,
        expires_at=(now + timedelta(days=days)) if days else None,
        view_count=0,
    )
    db.session.add(share)
    db.session.commit()
    return share, True


def resolve(token):
    """按 token 解析，返回 (share, report, reason)。

    reason 见 REASON_*；只有 REASON_OK 时 report 非空。
    """
    token = (token or '').strip()
    if not token:
        return None, None, REASON_MISSING
    share = AiReportShare.query.filter_by(token=token).first()
    if share is None:
        return None, None, REASON_MISSING
    if share.revoked_at is not None:
        return share, None, REASON_REVOKED
    if share.expires_at is not None and share.expires_at <= datetime.now():
        return share, None, REASON_EXPIRED
    report = db.session.get(AiReport, share.report_id)
    if report is None:
        return share, None, REASON_GONE
    return share, report, REASON_OK


def mark_viewed(share, token):
    """访问计数 +1（同一会话对同一 token 只计一次，刷新不刷量）。"""
    from flask import session

    seen = session.get('_ai_share_seen') or []
    if token in seen:
        return False
    seen = (list(seen) + [token])[-VIEW_KEEP:]
    session['_ai_share_seen'] = seen
    share.view_count = (share.view_count or 0) + 1
    share.last_viewed_at = datetime.now()
    db.session.commit()
    return True


def revoke(share):
    """撤销分享（幂等：已撤销的再点一次不报错）。"""
    if share is None or share.revoked_at is not None:
        return False
    share.revoked_at = datetime.now()
    db.session.commit()
    return True


def purge_for_report(report_id):
    """报告被删除时清掉它的分享链接（避免残留行指向不存在的报告）。"""
    (AiReportShare.query
     .filter(AiReportShare.report_id == report_id)
     .delete(synchronize_session=False))


def share_path(token):
    """站内相对路径（前端拼 location.origin 得到可复制链接）。

    不用 _external=True：反向代理/内网部署时 host 可能不对，交给浏览器取当前源更准。
    """
    from flask import url_for
    return url_for('share.ai_report', token=token)


def share_state(share):
    """分享行 → 前端弹窗需要的字段（不泄露 token 之外的任何内容）。"""
    if share is None:
        return None
    now = datetime.now()
    expired = share.expires_at is not None and share.expires_at <= now
    return {
        'id': share.id,
        'report_id': share.report_id,
        'path': share_path(share.token),
        'token': share.token,
        'created_at': share.created_at.strftime('%Y-%m-%d %H:%M') if share.created_at else '',
        'expires_at': share.expires_at.strftime('%Y-%m-%d %H:%M') if share.expires_at else '',
        'permanent': share.expires_at is None,
        'expired': expired,
        'revoked': share.revoked_at is not None,
        'view_count': share.view_count or 0,
        'last_viewed_at': (share.last_viewed_at.strftime('%Y-%m-%d %H:%M')
                           if share.last_viewed_at else ''),
    }

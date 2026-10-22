# StuLink v1.19.62.65 2026-10-22
# 分享页：AI 报告只读查看（登录即可，token 授权）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""`/s/<token>` —— AI 报告只读分享页。

未登录访问：Flask-Login 会带去登录页（`next=/s/<token>`），登录成功后自动跳回，
不需要用户再点一次链接。

四种失效原因给不同文案（别让人以为"系统坏了"）：
  missing  链接不存在（复制不全/被改过）
  revoked  分享者撤销了
  expired  到期
  gone     报告已删除
"""
from flask import make_response, render_template
from flask_login import current_user, login_required

from app.extensions import db
from app.models import User
from app.models.grades import Exam
from app.modules.grades.services import ai_share_service as share_svc
from app.modules.share import bp
from app.utils.html_guard import sanitize_report_html
from app.utils.md_guard import normalize_report_html

# 失效原因 → (标题, 说明)
_BAD_REASON = {
    share_svc.REASON_MISSING: ('分享链接不存在',
                               '地址可能复制不完整或已被修改，请向分享者重新索取。'),
    share_svc.REASON_REVOKED: ('分享链接已失效',
                               '分享者已撤销这条链接。如仍需查看，请联系对方重新分享。'),
    share_svc.REASON_EXPIRED: ('分享链接已过期',
                               '分享时设置了有效期，到期后链接自动失效；请联系分享者重新分享。'),
    share_svc.REASON_GONE: ('报告已删除',
                            '这份 AI 报告已被删除，分享链接随之失效。'),
}


def _harden(resp, code=None):
    """私有内容不许被搜索引擎收录、不许中间层缓存。"""
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    if code is not None:
        resp.status_code = code
    return resp


@bp.route('/s/<token>')
@login_required
def ai_report(token):
    """打开分享链接：校验 token → 计数 → 渲染只读报告页。"""
    share, report, reason = share_svc.resolve(token)
    if reason != share_svc.REASON_OK:
        title, message = _BAD_REASON.get(reason, _BAD_REASON[share_svc.REASON_MISSING])
        return _harden(make_response(render_template(
            'share/invalid.html', title=title, message=message)), 410)

    share_svc.mark_viewed(share, token)
    exam = db.session.get(Exam, report.exam_id)
    owner = db.session.get(User, report.user_id)
    kind = report.kind or 'md'
    # 落库时已消毒过一次；这里再消毒一次是**兜底**（历史数据/旧版本写入路径不确定），
    # 白名单重建是幂等的，代价可忽略，但能保证"进页面的一定是安全片段"。
    # 2026-10-22：kind=html 但正文是 Markdown 的历史报告（用户截图里的 `# 标题`）—
    # 先按 Markdown 转 HTML，否则分享页也会把 `#`、`**` 原样显示出来。
    if kind == 'html':
        safe_html = sanitize_report_html(normalize_report_html(report.content or ''))
    else:
        safe_html = ''
    if exam is not None:
        try:
            exam_title = exam.display_name()
        except Exception:                       # noqa: BLE001  展示用，降级不影响内容
            exam_title = exam.name
    else:
        exam_title = '考试已删除'

    resp = make_response(render_template(
        'share/ai_report.html',
        report=report,
        share=share,
        exam=exam,
        exam_title=exam_title,
        owner_name=(owner.real_name or owner.username) if owner else '未知用户',
        kind=kind,
        safe_html=safe_html,
        is_owner=bool(current_user.is_authenticated and current_user.id == report.user_id),
    ))
    return _harden(resp)

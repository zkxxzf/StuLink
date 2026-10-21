# StuLink v1.19.61.64 2026-10-18
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序认证与引导接口：登录 / 当前用户 / 首页入口。

- POST /api/wx/login      账号密码换令牌（可指定身份组）
- GET  /api/wx/profile    当前用户概要
- GET  /api/wx/bootstrap  一次性拿到用户 + 身份组 + 可访问模块 + 服务端信息
"""
import logging

from flask import current_app, g, request

from app.extensions import db
from app.models import User
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import (
    client_ip,
    hit_limit,
    wx_login_required,
)
from app.modules.wxapi.core.response import CODE_TOO_MANY, wx_err, wx_ok
from app.modules.wxapi.core.serializers import group_options, user_brief
from app.modules.wxapi.core.tokens import issue_token

logger = logging.getLogger(__name__)

# 模块入口表：(key, 标题, 所需权限键或 None, 分包路径)
# 顺序即首页卡片顺序；permission=None 表示登录即可访问（如通知公告）。
# permission 也可以是元组 = 任一满足即可（课表：教务端 academic.view，
# 教师端 workbench.class_view —— 两个权限种子里教师组不一致，网页端同口径）。
MODULE_DEFS = (
    ('notice', '通知公告', None,
     '/package-notice/pages/index/index'),
    ('exam', '考务监考', None,
     '/package-exam/pages/index/index'),
    ('grades', '成绩查询', 'grades.student_query',
     '/package-grades/pages/index/index'),
    ('schedule', '课表', ('academic.view', 'workbench.class_view'),
     '/package-academic/pages/schedule/index'),
    ('dorm', '宿舍管理', 'dormitory.view',
     '/package-dorm/pages/index/index'),
)


def _has_any(user, perm):
    """权限判定：字符串＝单权限，元组＝任一满足。"""
    if not perm:
        return True
    if isinstance(perm, (tuple, list)):
        return any(user.has_perm(p) for p in perm)
    return user.has_perm(perm)


def _caps(user):
    """前端能力开关：决定页面里哪些入口可见（不代替接口鉴权）。"""
    return {
        # 班级 / 教师课表属于教务视角；教师只有「我的课表」
        'schedule_others': bool(user.has_perm('academic.view')),
        'grades_analysis': bool(user.has_perm('grades.student_query')),
        # 应用页按模块权限决定入口是否展示（接口仍各自鉴权）
        'points': bool(user.has_perm('points.view')),
        'morality': bool(user.has_perm('morality.view')),
        'portrait': bool(user.has_perm('portrait.view')),
        'attendance': bool(user.has_perm('workbench.attendance_view')),
        'dorm': bool(user.has_perm('dormitory.view')),
        # 教师自助：申请调课 / 提交成绩复核
        'swap': bool(user.has_perm('academic.swap')),
        'score_review': bool(user.has_perm('grades.view')),
        'admin': getattr(user, 'role', '') == 'admin',
    }


def _payload():
    """兼容 JSON / 表单两种提交方式。"""
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _resolve_group(user, group_id):
    """确定本次登录使用的身份组 id：指定且属于本人时用，否则回落主组。"""
    if group_id:
        try:
            gid = int(group_id)
        except (TypeError, ValueError):
            gid = None
        if gid and gid in {g['id'] for g in group_options(user)}:
            return gid
    pg = user.primary_group
    return pg.id if pg is not None else None


def _modules_of(user):
    """按权限生成可访问模块列表（首页据此渲染卡片）。"""
    out = []
    for key, title, perm, path in MODULE_DEFS:
        if not _has_any(user, perm):
            continue
        out.append({'key': key, 'title': title, 'path': path})
    return out


def _unread(user):
    """未读通知数；通知库异常不应影响首页加载。"""
    try:
        from app.modules.notifications.services import (
            notification_service as svc,
        )
        return svc.get_unread_count(user)
    except Exception as _e:  # noqa: BLE001 通知库异常降级为 0
        logger.warning('取未读通知数失败（降级为 0）: %s', _e)
        return 0


def _server_info():
    """服务端信息：环境标识（dev/prod）+ 学校名 + 服务器时间。"""
    from datetime import datetime
    return {
        'env': current_app.config.get('WX_ENV', 'prod'),
        'school_name': current_app.config.get('SCHOOL_NAME', ''),
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def login_payload(user, group_id=None, openid=None):
    """统一的登录返回：令牌 + 用户 + 身份组 + 模块 + 能力开关。"""
    ttl = int(current_app.config.get('WX_TOKEN_TTL', 43200))
    token, real_ttl = issue_token(user.id, group_id, ttl, openid,
                                  ver=user.wx_token_ver or 0)
    return {
        'token': token,
        'expires_in': real_ttl,
        'group_id': group_id,
        'openid': openid or '',
        'user': user_brief(user),
        'groups': group_options(user),
        'modules': _modules_of(user),
        'caps': _caps(user),
        'server': _server_info(),
    }


@bp.route('/login', methods=['POST'])
def wx_login():
    """账号密码登录，返回访问令牌。"""
    data = _payload()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return wx_err('请输入账号和密码')
    ip = client_ip()
    if not hit_limit('login_ip_%s' % ip, 30, 300):
        return wx_err('尝试过于频繁，请稍后再试', CODE_TOO_MANY)
    # 账号级键不能拼 IP：拼了以后换一个出口 IP 计数就归零，等于对同一
    # 账号无限次猜口令（2026-10-19 审计）。
    if not hit_limit('login_acc_%s' % username.lower(), 10, 600):
        return wx_err('错误次数过多，请 10 分钟后再试', CODE_TOO_MANY)
    user = User.query.filter_by(username=username).first()
    if user is None or not user.check_password(password):
        return wx_err('账号或密码不正确', 401)
    if not user.is_active:
        return wx_err('账号已停用', 403)
    gid = _resolve_group(user, data.get('group_id'))
    return wx_ok(login_payload(user, gid))


@bp.route('/profile')
@wx_login_required
def wx_profile():
    """当前登录用户概要。"""
    from flask import g
    return wx_ok(user_brief(g.wx_user))


@bp.route('/bootstrap')
@wx_login_required
def wx_bootstrap():
    """首页引导数据：用户 + 身份组 + 可访问模块 + 服务端信息。"""
    from flask import g
    user = g.wx_user
    return wx_ok({
        'user': user_brief(user),
        'groups': group_options(user),
        'modules': _modules_of(user),
        'caps': _caps(user),
        'server': _server_info(),
        'notice_unread': _unread(user),
    })


@bp.route('/logout', methods=['POST'])
@wx_login_required
def wx_logout():
    """登出：递增令牌版本，令当前及更早签发的令牌立即失效（P1 M-03）。"""
    user = g.wx_user
    user.wx_token_ver = int(user.wx_token_ver or 0) + 1
    db.session.commit()
    return wx_ok(message='已退出登录')

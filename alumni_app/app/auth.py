"""认证模块：复用主项目 system.db 的 users 表

H-7（代码层）：本站与主站共用 users 表与签名密钥，因此必须显式校验角色白名单，
否则任何一个在职主站账号（宿管、任课教师…）都能用主站口令登录本站读取往届生数据。
"""
import logging
import os
import sqlite3

from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user, current_user  # noqa: F401

login_manager = LoginManager()
login_manager.login_view = 'basic.login'
login_manager.login_message = '请先登录'

_log = logging.getLogger('alumni.auth')

# H-7：角色白名单（默认排除 teacher / dorm_manager / homeroom_teacher）。
# 如需放开更多角色，设置环境变量 ALUMNI_ALLOWED_ROLES（逗号分隔，如 admin,staff）。
ALUMNI_ALLOWED_ROLES = {
    r.strip()
    for r in os.environ.get(
        'ALUMNI_ALLOWED_ROLES', 'admin,school_viewer,staff,grade_leader').split(',')
    if r.strip()
}


def is_alumni_role_allowed(role):
    """角色是否允许登录往届查询站（H-7 唯一判定入口）"""
    return (role or '') in ALUMNI_ALLOWED_ROLES


class AlumniUser(UserMixin):
    def __init__(self, uid, username, real_name, role):
        self.id = uid
        self.username = username
        self.real_name = real_name
        self.role = role


@login_manager.user_loader
def load_user(user_id):
    from config import Config
    try:
        conn = sqlite3.connect(f'file:{Config.SYSTEM_DB}?mode=ro', uri=True)
        # H-7：加 is_active=1，主站禁用账号后本站会话同步失效
        row = conn.execute(
            'SELECT id, username, real_name, role FROM users WHERE id=? AND is_active=1',
            (int(user_id),)
        ).fetchone()
        conn.close()
        if row:
            # M-2 同类要求：角色不在白名单的账号，其既有会话不再有效
            if not is_alumni_role_allowed(row[3]):
                return None
            return AlumniUser(row[0], row[1], row[2], row[3])
    except Exception:
        # L-5：认证异常不得静默吞没（此前 `pass` 会让"库打不开"表现为"登录失败"）
        _log.exception('alumni load_user 异常 user_id=%s', user_id)
    return None


def init_auth(app):
    login_manager.init_app(app)

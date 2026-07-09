"""认证模块：复用主项目 system.db 的 users 表"""
import sqlite3
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user, current_user

login_manager = LoginManager()
login_manager.login_view = 'basic.login'
login_manager.login_message = '请先登录'


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
        row = conn.execute('SELECT id, username, real_name, role FROM users WHERE id=?', (int(user_id),)).fetchone()
        conn.close()
        if row:
            return AlumniUser(row[0], row[1], row[2], row[3])
    except Exception:
        pass
    return None


def init_auth(app):
    login_manager.init_app(app)

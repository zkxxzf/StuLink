# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _get_secret_key():
    """获取 SECRET_KEY：优先环境变量，否则从文件读取，最后自动生成并持久�?""
    env_key = os.environ.get('SECRET_KEY', '').strip()
    if env_key:
        return env_key
    key_file = os.path.join(BASE_DIR, 'data', '.secret_key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                return f.read().strip()
    except Exception:
        pass
    # 自动生成并持久化
    new_key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    try:
        with open(key_file, 'w') as f:
            f.write(new_key)
    except Exception:
        pass
    return new_key


class Config:
    SECRET_KEY = _get_secret_key()
    # 数据库路径：优先用环境变量，其次 data/dormitory.db（兼容现有部署）
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'data', 'system.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 最大上�?16MB
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'app', 'static', 'uploads')

    # Session 配置 - 兼容 Edge/Chrome 等各浏览�?
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False        # HTTP 环境必须�?False
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_PATH = '/'
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None           # CSRF token 不过�?
    WTF_CSRF_SSL_STRICT = False          # �?HTTPS 环境关闭严格检�?
    TEMPLATES_AUTO_RELOAD = False         # 生产环境关闭模板自动重载以提升性能
    
    # 缓存配置 - 使用简单内存缓�?
    CACHE_TYPE = 'simple'
    CACHE_DEFAULT_TIMEOUT = 300  # 5分钟缓存
    
    # SQLAlchemy 优化配置
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }

    # 多库绑定（模块独立数据库�?
    SQLALCHEMY_BINDS = {
        'dormitory': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'dormitory.db'),
        'history': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'history.db'),
    }

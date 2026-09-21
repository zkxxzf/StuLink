# StuLink v1.9.1 2026-09-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _get_secret_key():
    """获取 SECRET_KEY：优先环境变量，否则从文件读取，最后自动生成并持久化"""
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
    # 主库路径：优先用环境变量，其次 data/system.db（多库架构：主库 + dormitory/history 绑定库）
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'data', 'system.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 最大上传 16MB
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'app', 'static', 'uploads')

    # Session 配置 - 兼容 Edge/Chrome 等各浏览器
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = False        # HTTP 环境必须为 False
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_PATH = '/'
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None           # CSRF token 不过期
    WTF_CSRF_SSL_STRICT = False          # 非 HTTPS 环境关闭严格检查
    TEMPLATES_AUTO_RELOAD = False         # 生产环境关闭模板自动重载以提升性能
    
    # 缓存配置 - 使用简单内存缓存
    CACHE_TYPE = 'simple'
    CACHE_DEFAULT_TIMEOUT = 300  # 5分钟缓存
    
    # SQLAlchemy 优化配置
    # connect_args.timeout=15：SQLite  busy 等待 15s（多线程写入时避免立即报 database is locked）
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'connect_args': {'timeout': 15},
    }

    # SQLite WAL 开关：默认关闭。
    # 取舍说明：本项目 data 目录位于同步盘（路径含「00同步文件」），WAL 产生的
    # -wal/-shm 伴生文件可能被同步工具锁定或半同步，存在损坏数据库的真实风险。
    # 如需开启（写入并发高的部署环境）：设置环境变量 STULINK_ENABLE_WAL=1 后重启，
    # 并确认 data 目录不在同步范围内或同步工具已排除 *.db-wal / *.db-shm。
    SQLITE_ENABLE_WAL = os.environ.get('STULINK_ENABLE_WAL', '0').strip() == '1'

    # 学校名称：成绩证明等对外文书抬头
    # 优先级：环境变量 SCHOOL_NAME > 数据库 system_settings（系统设置页）> 化名占位
    # 注意：默认值必须为化名占位，真实校名只通过环境变量或系统设置页注入，避免写入公开仓库
    SCHOOL_NAME = os.environ.get('SCHOOL_NAME', '某某学校')

    # 多库绑定（模块独立数据库：一模块一库，故障互不影响）
    SQLALCHEMY_BINDS = {
        'dormitory': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'dormitory.db'),
        'history': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'history.db'),
        'grades': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'grades.db'),
        'points': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'points.db'),
        'academic': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'academic.db'),
        'portrait': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'portrait.db'),
        'system': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'system.db'),
        'timetable': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'timetable.db'),
    }



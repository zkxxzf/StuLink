# StuLink v1.19.49.37 2026-10-17
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
    # 自动生成加上持久化
    new_key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    try:
        with open(key_file, 'w') as f:
            f.write(new_key)
        # L-11：密钥文件权限收敛（Windows 下 chmod 支持有限，best-effort）
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
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
    # M-2：局域网 HTTP 下必须为 False（硬开会导致全站掉登录），
    # 生产启用 HTTPS 后设置环境变量 STULINK_SESSION_COOKIE_SECURE=1 即可打开。
    SESSION_COOKIE_SECURE = os.environ.get(
        'STULINK_SESSION_COOKIE_SECURE', '0').strip() == '1'
    # M-2：服务端会话过期（默认 8 小时），配合 session.permanent=True 生效
    PERMANENT_SESSION_LIFETIME = int(os.environ.get('STULINK_SESSION_LIFETIME', '28800'))
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_PATH = '/'
    WTF_CSRF_ENABLED = True
    # M-7：CSRF token 有效期（默认 8 小时；None=永不过期，泄露后长期可用）
    WTF_CSRF_TIME_LIMIT = int(os.environ.get('STULINK_CSRF_TIME_LIMIT', '28800'))
    WTF_CSRF_SSL_STRICT = False          # 非 HTTPS 环境关闭严格检查
    # 生产环境关闭模板自动重载（性能）；改版/迭代期间可临时用环境变量打开
    # STULINK_RELOAD_TEMPLATES=1 —— 这样改 .html 不必重启服务。重启会打断正在后台
    # 跑的 AI 分析等长任务（进度在进程内），迭代期频繁重启是"任务老中断"的主因。
    TEMPLATES_AUTO_RELOAD = os.environ.get(
        'STULINK_RELOAD_TEMPLATES', '0').strip() == '1'
    
    # 缓存配置（2026-10-14 性能审计 PERF-14：本项目**未接入 flask-caching**，
    # 下面两项目前没有被任何代码读取，改它们不会生效。真实的进程内缓存是
    # app/utils/cache.py 的 `cache = SimpleCache(default_timeout=300, maxsize=1024)`，
    # 另有若干处自持 SimpleCache。要调缓存时长/容量请改那里，不要只改这里。）
    CACHE_TYPE = 'simple'
    CACHE_DEFAULT_TIMEOUT = 300  # 5分钟缓存（现状为死配置，见上）
    
    # SQLAlchemy 优化配置
    # busy 等待：真正生效的是 app/__init__.py 连接事件里的 `PRAGMA busy_timeout=15000`（15s），
    # 它以 PRAGMA 覆盖本项的 connect_args.timeout。两处数字保持一致，避免"文档写 15s、
    # 实际 5s"的漂移（2026-10-10 实测发现并修正；2026-10-14 随 PERF-25 一并提到 15s）。
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
    # ⚠️ 两点生产实测结论（2026-10-10）：
    #   1) journal_mode=WAL 会写进库文件头、**持久生效**，本开关为 0 也不会被改回 delete。
    #      因此也可不开环境变量，直接对数据目录里的 *.db 执行 `PRAGMA journal_mode=WAL`。
    #   2) WAL 库**无法在只读挂载下打开**（读取也需要写 -shm，实测报
    #      "unable to open database file"）。alumni 容器以 :ro 挂载同一 data 目录，
    #      故 history.db 必须保持非 WAL。
    SQLITE_ENABLE_WAL = os.environ.get('STULINK_ENABLE_WAL', '0').strip() == '1'

    # 微信小程序接入（2026-10-18）：环境标识 dev/prod，小程序据此提示当前环境；
    # 令牌有效时长（秒，默认 12 小时）。均由环境变量注入，不写死在代码里。
    WX_ENV = os.environ.get('STULINK_WX_ENV', 'prod').strip().lower()
    WX_TOKEN_TTL = int(os.environ.get('STULINK_WX_TOKEN_TTL', '43200'))

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
        # 2026-10-13（v1.19.49.37）：德育奖惩独立成库 —— 与「积分」业务互不牵连，
        # 奖惩记录含附件与处分期限等长期档案信息，拆库后单库损坏不影响积分/成绩。
        'morality': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'morality.db'),
        'academic': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'academic.db'),
        # 2026-10-10：教务按「一个功能一个库」拆分（原 academic.db 一库混装四件事）——
        # 查课 / 教师业绩 / 表单收集各自独立成库，academic.db 只留师资基础资料。
        'inspection': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'inspection.db'),
        'achievement': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'achievement.db'),
        'forms': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'forms.db'),
        'portrait': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'portrait.db'),
        'system': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'system.db'),
        'timetable': 'sqlite:///' + os.path.join(BASE_DIR, 'data', 'timetable.db'),
    }



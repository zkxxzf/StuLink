# StuLink v1.19.49.41 2026-10-20
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


# H-07（2026-10-19）：SECRET_KEY 是"进程身份"——换钥不报错，只会把所有人
# 静默踢下线。显式指定的密钥文件读不到时必须**启动失败**，不能自动生成新钥。
_SYNC_MARKERS = ('同步', 'onedrive', 'dropbox', 'nutstore', '百度网盘', '坚果云')


def _looks_synced(path):
    """路径是否落在常见同步盘（本项目实际位于「00同步文件」下）。"""
    p = os.path.abspath(path).replace('\\', '/').lower()
    return any(m in p for m in _SYNC_MARKERS)


def _warn_secret_location(path, label):
    """密钥文件在 data/ 或同步盘时打印告警（不打印密钥内容）。"""
    under_data = os.path.abspath(path).startswith(
        os.path.join(BASE_DIR, 'data') + os.sep)
    if under_data or _looks_synced(path):
        print('[安全警告] %s 位于 data/ 或同步盘：%s；'
              '建议移出并设置 STULINK_SECRET_KEY_FILE。' % (label, path))


def _get_secret_key():
    """获取 SECRET_KEY：环境变量 > 显式文件 > data/.secret_key > 自动生成。

    2026-10-19 H-07：新增 STULINK_SECRET_KEY_FILE；显式指定但读不到时
    抛 RuntimeError（宁可起不来，也不要静默换钥把全员踢下线）。
    """
    env_key = os.environ.get('SECRET_KEY', '').strip()
    if env_key:
        return env_key

    explicit = os.environ.get('STULINK_SECRET_KEY_FILE', '').strip()
    if explicit:
        key_file = os.path.abspath(explicit)
        try:
            with open(key_file, 'r') as f:
                key = f.read().strip()
        except OSError as _e:
            raise RuntimeError(
                'STULINK_SECRET_KEY_FILE 指向的密钥文件无法读取：%s'
                % key_file) from _e
        if not key:
            raise RuntimeError(
                'STULINK_SECRET_KEY_FILE 指向的密钥文件为空：%s' % key_file)
        _warn_secret_location(key_file, 'SECRET_KEY 文件')
        return key

    key_file = os.path.join(BASE_DIR, 'data', '.secret_key')
    if os.path.exists(key_file):
        try:
            with open(key_file, 'r') as f:
                key = f.read().strip()
        except OSError as _e:
            raise RuntimeError(
                'SECRET_KEY 文件存在但无法读取：%s' % key_file) from _e
        if not key:
            raise RuntimeError('SECRET_KEY 文件为空：%s' % key_file)
        _warn_secret_location(key_file, 'SECRET_KEY 文件')
        return key

    # 首次运行：自动生成并持久化（data/ 下的旧口径，仅兼容存量部署）
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
    _warn_secret_location(key_file, 'SECRET_KEY 文件')
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
    # P1 M-02：绝对超时（滑动续期不刷新 login_at；默认 12 小时）
    SESSION_ABSOLUTE_TIMEOUT = int(os.environ.get(
        'STULINK_SESSION_ABSOLUTE_TIMEOUT', '43200'))
    SESSION_REFRESH_EACH_REQUEST = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_PATH = '/'
    WTF_CSRF_ENABLED = True
    # M-7：CSRF token 有效期（默认 8 小时；None=永不过期，泄露后长期可用）
    WTF_CSRF_TIME_LIMIT = int(os.environ.get('STULINK_CSRF_TIME_LIMIT', '28800'))
    WTF_CSRF_SSL_STRICT = False          # 非 HTTPS 环境关闭严格检查
    # P1 M-12：AI 外发默认匿名化（姓名/学号替换，分数/名次/班级保留）。
    # 关闭仅用于校内自查：AI_ANONYMIZE=0。
    AI_ANONYMIZE = os.environ.get(
        'AI_ANONYMIZE', '1').strip().lower() not in ('0', 'false', 'no', 'off')
    # P2：AI 每日配额（按用户；0=不限制）与单次请求超时（秒）
    AI_DAILY_QUOTA = int(os.environ.get('AI_DAILY_QUOTA', '50'))
    AI_REQUEST_TIMEOUT = int(os.environ.get('AI_REQUEST_TIMEOUT', '180'))
    # L-04：只有来自可信反代的请求才信 X-Real-IP / X-Forwarded-For。
    # 逗号分隔 IP；'*' 表示信任所有来源（仅当应用端口只绑回环时使用）。
    TRUSTED_PROXIES = tuple(
        p.strip() for p in os.environ.get('STULINK_TRUSTED_PROXIES', '').split(',')
        if p.strip())
    # L-01：小程序通知详情 GET 默认不写已读；兼容期可设 1 恢复旧行为。
    WX_NOTICE_DETAIL_AUTO_READ = os.environ.get(
        'WX_NOTICE_DETAIL_AUTO_READ', '0').strip().lower() in ('1', 'true', 'yes', 'on')

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
    # 微信小程序登录（服务端 code2session）；未配置时只能走账号密码。
    WX_MP_APPID = os.environ.get('WX_MP_APPID', '').strip()
    WX_MP_SECRET = os.environ.get('WX_MP_SECRET', '').strip()
    # 仅供本机联调：code=mock:<openid> 时不访问微信服务器。
    WX_MP_MOCK = os.environ.get('WX_MP_MOCK', '0') == '1'
    WX_QR_TTL = int(os.environ.get('STULINK_WX_QR_TTL', '120'))

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
        # 2026-10-13（v1.19.49.41）：德育奖惩独立成库 —— 与「积分」业务互不牵连，
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



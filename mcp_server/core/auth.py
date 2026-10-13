# -*- coding: utf-8 -*-
"""接入认证：账号密码（每请求校验）+ 可选静态令牌，失败统一 401/429。

认证模型（2026-10-11 起，完整设计见 docs/MCP服务-认证与授权设计.md）：
  · account 模式（默认）：每个 HTTP 请求都必须携带 StuLink 账号密码，校验通过后
    把账号 id 写入请求身份上下文（core/identity.py）；工具执行时按该身份做
    功能权限 + 数据范围校验（与网页端同一套体系，见 core/toolkit.py）。
  · token 模式（兼容旧客户端）：只校验静态访问令牌，执行身份固定为 bind_user。

凭据携带方式（任选其一）：
  · Authorization: Basic base64(账号:密码)
  · X-StuLink-User: <账号> + X-StuLink-Password: <密码>
可选服务令牌（require_token=true 时必需；account 模式下作为第二道准入）：
  · X-StuLink-Token: <token>，或 Authorization: Bearer <token>

拒绝策略（HTTP 层，未通过不进 MCP；工具层权限不足见 toolkit）：
  401 unauthorized        缺少账号密码
  401 invalid_credentials 账号或密码错误（不区分账号是否存在，防枚举）
  401 account_disabled    账号已停用
  401 invalid_token       服务令牌缺失/错误
  429 too_many_attempts   同一来源对该账号连续失败超限，带 Retry-After
                          （2026-10-13 起只锁"来源+账号"这一组合：账号本人从别的设备
                           登录不受影响；另对跨来源的失败累计做**递增延迟**兜底）

为什么自写中间件而不用 SDK 自带 auth：SDK 的 AuthSettings/TokenVerifier 围绕
OAuth 授权服务器设计（issuer_url、动态注册等），对"校验本系统账号密码"属过度
设计；自写行为完全可控、可读、可测，且与网页端共用 User.check_password。
"""
import base64
import binascii
import hashlib
import hmac
import json
import logging
import threading
import time

from .. import config
from . import bootstrap, db, identity

logger = logging.getLogger('stulink.mcp.auth')

# 凭据校验缓存：key = sha256(账号 \0 密码)。缓存命中仍会比对 password_hash 指纹
# 与启用状态，因此改密码 / 停用账号立即生效，无需等缓存过期；缓存只是省掉
# scrypt 校验（单次约 50-150ms）的开销。
_cred_cache = {}
_cred_lock = threading.Lock()
_CRED_CACHE_MAX = 256

# 认证失败计数（按「来源 + 账号」维度）：防密码爆破，同时**不会锁死真实账号**。
# 2026-10-13 全面审计 M-5：原实现按账号维度硬锁（默认 5 次失败锁 300 秒），而服务
# 常以 host=0.0.0.0 暴露在局域网 —— 任意设备都能用 5 次错误口令把某位老师的账号
# 锁死 300 秒（拒绝服务）。改成按来源维度后，攻击者只能锁住自己这条来源，该账号
# 从其他设备登录不受影响；单一来源的暴力破解仍会被挡下。
_fails = {}                     # (client, username) -> (count, locked_until)
_FAILS_MAX = 1024

# 跨来源的暴力破解兜底：同一账号在窗口内失败累计超过阈值后，**每次尝试附加递增
# 延迟**（不做硬锁，任何人都影响不了这个账号的正常使用）。窗口内成功登录即清零。
_account_fails = {}             # username -> (count, window_start)
_ACCOUNT_FAILS_MAX = 4096


def _cfg_int(key, default):
    try:
        value = config.load_config().get(key)
        return default if value is None else int(value)
    except (TypeError, ValueError):
        return default


def _cfg_float(key, default):
    try:
        value = config.load_config().get(key)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _cred_key(username, password):
    return hashlib.sha256(f'{username}\0{password}'.encode('utf-8')).hexdigest()


def _fail_key(username, client=None):
    """失败计数维度：「来源 + 账号」。

    client 为空（stdio 本地调用，见 run_stdio.py）时退化为账号维度 —— 单机本地
    调用不存在"别人锁你号"的问题。
    """
    return (client or '-', username)


def _lock_remaining(username, client=None):
    """该来源对该账号是否处于失败锁定中；返回剩余秒数（0 = 未锁定）。

    注意：锁的是 **(来源, 账号)** 这一组合，不是账号本身 —— 局域网里别人的
    错误尝试不会影响账号主人从自己设备登录（2026-10-13 全面审计 M-5）。
    """
    if _cfg_int('login_fail_limit', 5) <= 0:
        return 0
    now = time.time()
    with _cred_lock:
        record = _fails.get(_fail_key(username, client))
        if record and record[1] > now:
            return int(record[1] - now) + 1
    return 0


def _record_fail(username, client=None):
    limit = _cfg_int('login_fail_limit', 5)
    if limit <= 0:
        return
    lock_seconds = max(1, _cfg_int('login_lock_seconds', 300))
    now = time.time()
    key = _fail_key(username, client)
    window = max(30, _cfg_int('login_throttle_window', 300))
    with _cred_lock:
        if len(_fails) > _FAILS_MAX:
            _fails.clear()
        count, _until = _fails.get(key, (0, 0.0))
        count += 1
        if count >= limit:
            _fails[key] = (0, now + lock_seconds)
            logger.warning('MCP 认证失败次数超限：来源 %s 对账号 %s 锁定 %s 秒'
                           '（只锁该来源，账号本人不受影响）',
                           client or '-', username, lock_seconds)
        else:
            _fails[key] = (count, 0.0)
        # 跨来源累计（只用于附加延迟，不参与锁定判定）
        if len(_account_fails) > _ACCOUNT_FAILS_MAX:
            _account_fails.clear()
        acount, astart = _account_fails.get(username, (0, now))
        if now - astart > window:
            acount, astart = 0, now
        _account_fails[username] = (acount + 1, astart)


def _clear_fail(username, client=None):
    with _cred_lock:
        _fails.pop(_fail_key(username, client), None)
        _account_fails.pop(username, None)


def _throttle_delay(username):
    """跨来源失败累计后的附加延迟（秒）；未超阈值返回 0。

    只延迟、不锁定：即使被多个来源分布式尝试，账号本人也只是最多多等
    `login_throttle_max_delay` 秒，永远不会被拒绝。
    """
    limit = _cfg_int('login_fail_limit', 5)
    if limit <= 0:
        return 0.0
    threshold = max(2, _cfg_int('login_throttle_threshold', limit * 2))
    max_delay = max(0.0, _cfg_float('login_throttle_max_delay', 1.0))
    if max_delay <= 0:
        return 0.0
    window = max(30, _cfg_int('login_throttle_window', 300))
    now = time.time()
    with _cred_lock:
        count, start = _account_fails.get(username, (0, now))
        if now - start > window:
            return 0.0
        if count < threshold:
            return 0.0
    return min(max_delay, 0.1 * (count - threshold + 1))


def verify_credentials(username, password, client=None):
    """同步校验 StuLink 账号密码（必须在 app context 内调用）。

    `client` = 来源标识（HTTP 层传客户端 IP，stdio 传 None）。

    返回 (user_id, error, message)：error 为 None 表示通过。
    校验顺序：锁定检查（本来源） → 跨来源失败累计的附加延迟 → 账号存在/启用
    → 缓存命中（密码指纹未变） → 完整密码校验。
    """
    username = (username or '').strip()
    if not username or not password:
        return None, 'unauthorized', '缺少账号或密码'
    remain = _lock_remaining(username, client)
    if remain:
        return None, 'too_many_attempts', f'认证失败次数过多，请 {remain} 秒后重试'
    # 跨来源暴力破解兜底：只增加等待，不拒绝（账号本人永远进得来）
    delay = _throttle_delay(username)
    if delay:
        time.sleep(delay)

    from app.models.user import User
    user = User.query.filter_by(username=username).first()
    if user is None:
        _record_fail(username, client)
        return None, 'invalid_credentials', '账号或密码错误'
    if not user.is_active:
        return None, 'account_disabled', f'账号 {username} 已停用，请联系管理员'

    key = _cred_key(username, password)
    ttl = _cfg_int('credential_cache_ttl', 300)
    now = time.time()
    if ttl > 0:
        with _cred_lock:
            hit = _cred_cache.get(key)
        if (hit and now - hit['at'] < ttl and hit['user_id'] == user.id
                and hit['fingerprint'] == user.password_hash):
            _clear_fail(username, client)
            return user.id, None, None

    if not user.check_password(password):
        _record_fail(username, client)
        return None, 'invalid_credentials', '账号或密码错误'

    if ttl > 0:
        with _cred_lock:
            if len(_cred_cache) >= _CRED_CACHE_MAX:
                _cred_cache.clear()
            _cred_cache[key] = {'user_id': user.id,
                                'fingerprint': user.password_hash, 'at': now}
    _clear_fail(username, client)
    return user.id, None, None


def audit_auth_failure(username, error, message, client=None):
    """认证失败留痕：写系统操作日志（user 为空）+ MCP 服务端日志。

    必须在 app context 内调用（由 db.run 提供）。锁定期间的重复失败不再写库，
    避免攻击者刷日志。
    """
    logger.warning('MCP 认证失败：account=%s reason=%s client=%s',
                   username or '(空)', error, client or '-')
    if error == 'too_many_attempts':
        return
    try:
        from app.utils.helpers import log_operation
        log_operation(None, action='MCP认证失败', target_type='MCP接入', target_id=None,
                      detail={'account': username or '', 'reason': error,
                              'message': message, 'client': client or ''},
                      module='mcp', severity='WARNING')
    except Exception as exc:  # noqa: BLE001  审计失败不能影响拒绝响应
        logger.warning('MCP 认证失败审计写入失败：%s', exc)


def _verify_and_audit(username, password, client=None):
    """校验 + 失败审计，整体在 db 线程内执行（一次排队完成）。"""
    user_id, error, message = verify_credentials(username, password, client=client)
    if error:
        audit_auth_failure(username, error, message, client=client)
    return user_id, error, message


def _token_ok(provided, expected):
    if not provided or not expected:
        return False
    try:
        # 常量时间比较；非 ASCII 一律按错误令牌处理（compare_digest 会抛 TypeError）
        return hmac.compare_digest(provided, expected)
    except TypeError:
        return False


async def send_json(send, status, payload, extra_headers=()):
    """手动构造 JSON 响应（不经过 MCP/Starlette，确保拒绝响应带明确说明）。"""
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = [(b'content-type', b'application/json; charset=utf-8'),
               (b'content-length', str(len(body)).encode()),
               (b'cache-control', b'no-store')]
    headers.extend(extra_headers)
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': body})


class RequestHeaders:
    """ASGI scope 头部解析（HTTP 头按 latin-1 解码，与规范一致）。"""

    def __init__(self, scope):
        self._raw = {}
        for key, value in (scope.get('headers') or []):
            self._raw[key.decode('latin-1').lower()] = value.decode('latin-1')

    def get(self, name):
        return self._raw.get(name.lower(), '')

    def bearer_token(self):
        header = self.get('authorization')
        prefix = 'bearer '
        if header and header[:len(prefix)].lower() == prefix:
            return header[len(prefix):].strip()
        return ''

    def basic_credentials(self):
        header = self.get('authorization')
        prefix = 'basic '
        if not header or header[:len(prefix)].lower() != prefix:
            return None
        try:
            decoded = base64.b64decode(header[len(prefix):].strip(), validate=True
                                       ).decode('utf-8')
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
        if ':' not in decoded:
            return None
        username, _sep, password = decoded.partition(':')
        return username, password

    def account_credentials(self):
        """账号密码：Basic 优先，其次 X-StuLink-User / X-StuLink-Password。"""
        pair = self.basic_credentials()
        if pair:
            return pair
        username = self.get('x-stulink-user').strip()
        password = self.get('x-stulink-password')
        if username and password:
            return username, password
        return None

    def service_token(self):
        return self.get('x-stulink-token').strip() or self.bearer_token()


class AccessGuard:
    """接入认证中间件：校验凭据 → 写入请求身份 → 交给 MCP；失败直接 401/429。

    注意：lifespan / websocket 等非 HTTP 作用域必须放行，否则 uvicorn 启动握手
    会被拦下导致服务起不来。
    """

    def __init__(self, app, cfg):
        self.app = app
        self.mode = (cfg.get('auth_mode') or 'account').strip().lower()
        self.token = cfg.get('token') or ''
        self.require_token = bool(cfg.get('require_token'))

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return
        headers = RequestHeaders(scope)
        client = (scope.get('client') or ('', 0))[0]

        # 第一道（可选）：服务令牌，防止端口被随意扫描/连接
        if self.require_token and not _token_ok(headers.service_token(), self.token):
            await send_json(send, 401, {
                'error': 'invalid_token',
                'message': '缺少或错误的服务令牌（X-StuLink-Token，或 Authorization: Bearer <token>）'})
            return

        if self.mode == 'token':
            # 兼容模式：静态令牌 + 固定 bind_user 身份
            if not _token_ok(headers.service_token(), self.token):
                await send_json(send, 401, {
                    'error': 'invalid_token',
                    'message': '缺少或错误的访问令牌（Authorization: Bearer <token>）'})
                return
            handle = identity.set_current(bootstrap.default_user_id())
            try:
                await self.app(scope, receive, send)
            finally:
                identity.reset(handle)
            return

        # account 模式：每请求校验账号密码
        credentials = headers.account_credentials()
        if not credentials:
            logger.warning('MCP 接入被拒（无凭据）：client=%s path=%s',
                           client or '-', scope.get('path', ''))
            await send_json(send, 401, {
                'error': 'unauthorized',
                'message': '接入需要 StuLink 账号密码：Authorization: Basic base64(账号:密码)，'
                           '或请求头 X-StuLink-User + X-StuLink-Password'})
            return

        username, password = credentials
        user_id, error, message = await db.run_async(
            lambda: _verify_and_audit(username, password, client=client))
        if error == 'too_many_attempts':
            remain = _lock_remaining(username, client) or 1
            await send_json(send, 429, {'error': error, 'message': message},
                            extra_headers=[(b'retry-after', str(remain).encode())])
            return
        if error:
            await send_json(send, 401, {'error': error, 'message': message})
            return

        # 认证通过：身份写入请求上下文，交给 MCP（工具执行时按该身份校验权限与范围）
        handle = identity.set_current(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            identity.reset(handle)

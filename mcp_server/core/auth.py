# -*- coding: utf-8 -*-
"""Bearer 令牌鉴权：纯 ASGI 中间件，包在 MCP 的 Starlette 应用外层。

选自己写而不用 MCP SDK 自带 auth 的原因：SDK 的 AuthSettings/TokenVerifier
是围绕 OAuth 授权服务器设计的（issuer_url、动态注册等），对"单个静态令牌"
属于过度设计，且行为随 SDK 版本变化。自己写行为完全可控、可读、可测。
"""
import hmac
import json

_UNAUTHORIZED = json.dumps(
    {'error': 'unauthorized', 'message': '缺少或错误的访问令牌（Authorization: Bearer <token>）'},
    ensure_ascii=False).encode('utf-8')


class BearerAuth:
    """校验 Authorization: Bearer <token>；不合格一律 401。

    注意：lifespan / websocket 等非 HTTP 作用域必须放行，
    否则 uvicorn 的启动握手会被拦下导致服务起不来。
    """

    def __init__(self, app, token):
        self.app = app
        self.token = token or ''

    async def __call__(self, scope, receive, send):
        if scope.get('type') != 'http':
            await self.app(scope, receive, send)
            return
        if not self._valid(self._header(scope)):
            await send({'type': 'http.response.start', 'status': 401, 'headers': [
                (b'content-type', b'application/json; charset=utf-8'),
                (b'content-length', str(len(_UNAUTHORIZED)).encode()),
                (b'www-authenticate', b'Bearer'),
            ]})
            await send({'type': 'http.response.body', 'body': _UNAUTHORIZED})
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _header(scope):
        for key, value in scope.get('headers') or []:
            if key == b'authorization':
                return value.decode('latin-1')
        return ''

    def _valid(self, header):
        prefix = 'bearer '
        if not header or header[:len(prefix)].lower() != prefix:
            return False
        # 常量时间比较，避免逐字符比对泄露令牌前缀
        return bool(self.token) and hmac.compare_digest(header[len(prefix):].strip(), self.token)
# -*- coding: utf-8 -*-
"""接入认证端到端自测（account 模式）：401 / 429 拒绝策略 + 凭据通过后可正常调用工具。

由 `smoke_scores.py` 调用（共用同一套 PASS/FAIL 记录器，通过 record 回调回传结果）。

**不触碰任何真实账号**：每次运行**新建一个临时账号**（随机用户名 + 随机密码）用于验证认证，
结束后立即删除；若上次运行被强杀留下残留账号，本次启动时会先清理干净
（清理失败则兜底为"停用 + 重置为随机密码"，绝不会留下可用账号）。
除此之外不写入任何数据。
"""
import base64
import os
import secrets
import socket
import sys
import threading
import time

import uvicorn                                                       # noqa: E402
from mcp.client.session import ClientSession                         # noqa: E402
from mcp.client.streamable_http import (                             # noqa: E402
    create_mcp_http_client, streamable_http_client)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server import config                                    # noqa: E402
from mcp_server.core import bootstrap                            # noqa: E402
from mcp_server.run import build_asgi_app                        # noqa: E402

_TEMP_PREFIX = '__smoke_mcp_'      # 临时账号前缀：用于自愈清理残留


# ---------------- 临时账号（建 → 用 → 删） ----------------

def _cleanup_temp_accounts():
    """清理自测临时账号（含上次被强杀留下的残留）。返回 (删除数, 停用数)。"""
    from app.extensions import db
    from app.models.user import User
    removed = disabled = 0
    for user in User.query.filter(User.username.like(f'{_TEMP_PREFIX}%')).all():
        try:
            db.session.delete(user)
            db.session.commit()
            removed += 1
        except Exception:            # noqa: BLE001  有外键引用时删不掉 → 兜底停用+打乱密码
            db.session.rollback()
            try:
                user.is_active = False
                user.set_password(secrets.token_urlsafe(24))
                db.session.commit()
                disabled += 1
            except Exception:        # noqa: BLE001
                db.session.rollback()
    return removed, disabled


def _pick_perm_group_id():
    """借一个"有 grades.view 的非管理员"权限组，供临时账号使用。"""
    from app.models.user import User
    for user in (User.query.filter(User.is_active.is_(True), User.role != 'admin')
                 .limit(200).all()):
        if user.permission_group_id and user.has_perm('grades.view'):
            return user.permission_group_id
    return None


def _create_temp_account():
    """新建临时账号，返回 (username, password, can_view_grades)。"""
    from app.extensions import db
    from app.models.user import User
    username = f'{_TEMP_PREFIX}{secrets.token_hex(4)}'
    password = secrets.token_urlsafe(24)
    user = User(username=username, real_name='MCP自测临时账号', role='teacher',
                permission_group_id=_pick_perm_group_id(), is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return username, password, bool(user.has_perm('grades.view'))


def _delete_temp_account(username):
    """删除指定临时账号（失败则兜底停用）。"""
    from app.extensions import db
    from app.models.user import User
    user = User.query.filter_by(username=username).first()
    if user is None:
        return
    try:
        db.session.delete(user)
        db.session.commit()
    except Exception:                # noqa: BLE001
        db.session.rollback()
        try:
            user.is_active = False
            user.set_password(secrets.token_urlsafe(24))
            db.session.commit()
        except Exception:            # noqa: BLE001
            db.session.rollback()


# ---------------- HTTP / SDK 辅助 ----------------

def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _start_http(cfg):
    """起一个真实 HTTP 服务（uvicorn 线程），返回 (server, port)。"""
    port = _free_port()
    cfg = dict(cfg, host='127.0.0.1', port=port)   # 端口同时用于 DNS 重绑定白名单
    app, _server = build_asgi_app(cfg)
    http = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                         log_level='warning'))
    threading.Thread(target=http.run, daemon=True).start()
    for _ in range(200):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                return http, port
        except OSError:
            time.sleep(0.05)
    raise RuntimeError('HTTP 服务未在预期时间内就绪')


async def _raw_post(port, headers):
    """裸 HTTP 请求（不经 SDK 会话）：用于验证 401/429 这类连接层拒绝。"""
    client = create_mcp_http_client(headers=headers or None)
    try:
        return await client.post(
            f'http://127.0.0.1:{port}/mcp',
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                  'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                             'clientInfo': {'name': 'smoke', 'version': '1'}}},
            headers={'accept': 'application/json, text/event-stream'})
    finally:
        await client.aclose()


async def _sdk_session(port, headers, tool, args=None):
    """完整 SDK 会话：initialize + list_tools + 调用一次工具；返回 (工具数, 文本)。"""
    client = create_mcp_http_client(headers=headers)
    try:
        async with streamable_http_client(f'http://127.0.0.1:{port}/mcp',
                                          http_client=client) as transport:
            async with ClientSession(transport[0], transport[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool(tool, args or {})
                text = result.content[0].text if result.content else ''
                return len(tools.tools), text
    finally:
        await client.aclose()


def _status(record, name, status, expect):
    """断言 HTTP 状态码。"""
    if status == expect:
        record(name, [], '')
    else:
        record(name, [f'期望 HTTP {expect}，实际 {status}'], '')


# ---------------- 用例主体 ----------------

async def run_account_mode_cases(record, expected_tools, probe_tool='list_my_score_scope'):
    """account 模式端到端：401 / 429 拒绝策略 + 凭据通过后可正常调用工具。"""
    print('\n── 接入认证（account 模式） ──')
    base_cfg = dict(config.load_config())
    username = password = None
    can_call = False
    http_server = None
    try:
        with bootstrap.get_app().app_context():
            removed, disabled = _cleanup_temp_accounts()
            if removed or disabled:
                print(f'      （清理上次残留的临时账号：删除 {removed}、停用 {disabled}）')
            username, password, can_call = _create_temp_account()
            print(f'      （已新建临时账号 {username}，用于认证用例）')

        cfg = dict(base_cfg, auth_mode='account', require_token=False,
                   login_fail_limit=5, login_lock_seconds=30)
        config._cache = cfg
        http_server, port = _start_http(cfg)

        resp = await _raw_post(port, None)
        _status(record, '接入认证：无凭据被拒', resp.status_code, 401)
        if 'unauthorized' not in resp.text:
            record('接入认证：无凭据错误体', ['缺少 unauthorized 错误码'], resp.text[:200])

        resp = await _raw_post(port, {'x-stulink-user': username, 'x-stulink-password': 'x'})
        _status(record, '接入认证：错误密码被拒', resp.status_code, 401)

        basic = base64.b64encode(f'{username}:{password}'.encode()).decode()
        count, text = await _sdk_session(port, {'Authorization': f'Basic {basic}'}, probe_tool)
        if count == expected_tools and (not can_call or '"ok": true' in text):
            record('接入认证：正确凭据进入 MCP 并调用成绩工具', [], '')
            print(f'      （工具 {count} 个，{probe_tool} 调用{"成功" if can_call else "已到达工具层"}）')
        else:
            record('接入认证：正确凭据进入 MCP',
                   [f'工具数 {count}（期望 {expected_tools}）/ 返回 {text[:120]}'], text[:200])

        count2, _ = await _sdk_session(port, {'X-StuLink-User': username,
                                              'X-StuLink-Password': password}, probe_tool)
        if count2 == count:
            record('接入认证：X-StuLink-User/Password 头部方式', [], '')
        else:
            record('接入认证：头部方式', [f'工具数不一致 {count2} != {count}'], '')

        codes = []
        resp = None
        for _ in range(6):
            resp = await _raw_post(port, {'x-stulink-user': '__smoke_no_user__',
                                          'x-stulink-password': 'x'})
            codes.append(resp.status_code)
        if codes[-1] == 429 and resp is not None and resp.headers.get('retry-after'):
            record('接入认证：连续失败触发锁定 429', [], '')
            print(f'      （状态码 {codes}，retry-after={resp.headers.get("retry-after")}）')
        else:
            record('接入认证：连续失败锁定', [f'状态码序列 {codes}'], '')
    except Exception as exc:                                        # noqa: BLE001
        record('接入认证：端到端用例执行', [f'{type(exc).__name__}: {exc}'], '')
    finally:
        if http_server is not None:
            http_server.should_exit = True
        config._cache = base_cfg
        if username:
            try:
                with bootstrap.get_app().app_context():
                    _delete_temp_account(username)
                print(f'      （已删除临时账号 {username}）')
            except Exception as exc:                                # noqa: BLE001
                print(f'      警告：删除临时账号 {username} 失败：{exc}')

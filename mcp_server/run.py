# -*- coding: utf-8 -*-
"""StuLink 成绩分析 MCP 服务入口（**只读**）：HTTP(Streamable HTTP) 传输 + 接入认证。

服务范围：只暴露 12 个成绩分析只读工具，权限按三条规则细粒度裁决——
R1 任课教师（年级+班级同时命中，且仅所教学科）/ R2 班主任（本班）/
R3 年级长及以上（本年级，校级/管理员=全校）。无任何写入能力。
详见 docs/MCP服务-成绩分析权限设计.md。

启动方式（二选一）：
  · 双击 mcp_server\\start_mcp.bat（首次会自动创建虚拟环境并装依赖）
  · 命令行：mcp_server\\.venv\\Scripts\\python.exe mcp_server\\run.py

客户端接入（默认 account 模式）：URL = http://<监听地址>:<端口><path>，
请求头携带 StuLink 账号密码（任选其一）：
  · X-StuLink-User: <账号> + X-StuLink-Password: <密码>
  · Authorization: Basic base64(账号:密码)
（require_token=true 时另加 X-StuLink-Token；token 兼容模式见 config.json。）
认证设计见 docs/MCP服务-认证与授权设计.md。
"""
import logging
import os
import socket
import sys

# 把项目根目录加入 sys.path，保证以 "mcp_server.xxx" 形式导入包
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp.server.mcpserver import MCPServer                    # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from mcp_server import config                                 # noqa: E402
from mcp_server.core import auth, bootstrap                    # noqa: E402
from mcp_server.tools import register_all                      # noqa: E402

logger = logging.getLogger('stulink.mcp')

INSTRUCTIONS = (
    '本服务只提供 StuLink 教务系统的「成绩分析」只读能力，无任何写入操作。\n'
    '可见范围＝接入账号在网页端成绩模块的可见范围，细粒度规则：\n'
    '1. 任课教师：仅当同时任教某班级所在年级与该班级时，才能查看该班成绩，'
    '且只能看到自己在该年级所教学科的成绩；\n'
    '2. 班主任（班型设置）：可查看本班成绩；\n'
    '3. 年级长及以上（含校级/管理员）：可查看所在年级（校级/管理员为全校）成绩。\n'
    '使用约定：\n'
    '1. 先调用 list_my_score_scope 确认自己能看到哪些年级/班级/学科；\n'
    '2. 用 list_exams 取 exam_id、list_classes 核对班级名（如 "01班"）；\n'
    '3. 越权查询一律返回 {"ok": false, "error": "权限不足：…"}，不要绕过提示继续尝试；\n'
    '4. 任课教师查看班级时，"总分/班均分/排名"会显示为已隐藏（其只被授予单科）；\n'
    '5. 返回值为 JSON 文本，形如 {"ok": true, "data": ...} 或 {"ok": false, "error": "原因"}。'
)


def _lan_ip():
    """探测本机局域网 IP（UDP connect 只查路由表，不真正发包）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('10.255.255.255', 1))
        return sock.getsockname()[0]
    except Exception:  # noqa: BLE001  离线环境取不到就返回空
        return ''
    finally:
        sock.close()


def _allowed_hosts(cfg):
    """DNS 重绑定防护的 Host 白名单：本机 + 监听地址 + 局域网 IP（含端口形式）。"""
    if cfg.get('allowed_hosts'):
        return list(cfg['allowed_hosts'])
    port = int(cfg['port'])
    names = {'localhost', '127.0.0.1'}
    host = (cfg.get('host') or '').strip()
    if host and host not in ('0.0.0.0', '::', '::0'):
        names.add(host)
    lan = _lan_ip()
    if lan:
        names.add(lan)
    out = []
    for name in sorted(names):
        out.extend([name, f'{name}:{port}'])
    return out


def _auth_mode(cfg):
    """当前接入认证模式：account（账号密码，默认）| token（静态令牌，兼容）。"""
    return (cfg.get('auth_mode') or 'account').strip().lower()


def build_asgi_app(cfg):
    """组装 ASGI 应用：MCP(Streamable HTTP) → 接入认证中间件。"""
    if _auth_mode(cfg) == 'token':
        bootstrap.warm_up(cfg['bind_user'])      # 兼容模式：固定执行身份（启动即校验）
    else:
        bootstrap.prepare_app()                  # 账号密码模式：多用户，每请求认证
    server = MCPServer(
        name='stulink-scores',
        title='StuLink 成绩分析',
        instructions=INSTRUCTIONS,
        version='1.0.0',
    )
    loaded = register_all(server)
    logger.info('已注册工具模块：%s', '、'.join(label for _n, label in loaded))

    starlette_app = server.streamable_http_app(
        streamable_http_path=cfg['path'] or '/mcp',
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts(cfg),
        ),
        host=cfg['host'] or '127.0.0.1',
    )
    return auth.AccessGuard(starlette_app, cfg), server


def _mask_token(token):
    """启动横幅只显示令牌首尾片段，避免截图/日志泄露完整令牌。"""
    text = token or ''
    if len(text) <= 12:
        return '****'
    return f'{text[:6]}…{text[-4:]}'


def _client_example(cfg, url, mode):
    """客户端配置示例（按认证模式给出对应的 headers）。"""
    lines = ['  客户端配置示例（Streamable HTTP）：', '  {', f'    "url": "{url}",']
    if mode == 'account':
        lines.append('    "headers": {')
        if cfg.get('require_token'):
            lines.append('      "X-StuLink-User": "<你的 StuLink 账号>",')
            lines.append('      "X-StuLink-Password": "<你的账号密码>",')
            lines.append('      "X-StuLink-Token": "<服务令牌，见 config.json>"')
        else:
            lines.append('      "X-StuLink-User": "<你的 StuLink 账号>",')
            lines.append('      "X-StuLink-Password": "<你的账号密码>"')
        lines.append('    }')
    else:
        lines.append('    "headers": { "Authorization": "Bearer <服务令牌>" }')
    lines.append('  }')
    return lines


def _print_banner(cfg, me):
    """启动横幅：把客户端需要的信息一次印全，省得翻文档。"""
    mode = _auth_mode(cfg)
    host = cfg['host'] or '127.0.0.1'
    shown = _lan_ip() if host == '0.0.0.0' else host
    url = f"http://{shown}:{cfg['port']}{cfg['path'] or '/mcp'}"
    if mode == 'token':
        identity_line = (f"  执行身份 : {me['real_name']}（{me['username']}，{me['role']}）"
                         ' · 静态令牌模式')
    else:
        identity_line = '  执行身份 : 每个客户端用自己的 StuLink 账号密码（每请求认证）'
    lines = [
        '=' * 68,
        '  StuLink 成绩分析 MCP 服务已启动（只读）',
        '',
        f"  接入地址 : {url}",
        f"  认证模式 : {'账号密码（每请求校验）' if mode == 'account' else '静态访问令牌（兼容模式）'}",
        f"  服务令牌 : {_mask_token(cfg['token'])}（完整值见 mcp_server/config.json；"
        + ('必填' if mode == 'token' or cfg.get('require_token') else '可选') + '）',
        identity_line,
        '  工具集   : 成绩分析（12 个只读工具，无写入能力）',
        '  权限口径 : 任课教师=所教班级×所教学科 / 班主任=本班 / 年级长及以上=本年级',
        '',
    ]
    lines.extend(_client_example(cfg, url, mode))
    # 2026-10-13（全面审计 M-5）：局域网暴露时的准入自检 —— 只认账号密码，等于让
    # 同网段任何设备都能拿真实教师账号反复试探本端口，这里显式提醒一次。
    if (host in ('0.0.0.0', '::', '::0')
            and not (cfg.get('require_token') or mode == 'token')):
        lines.extend([
            '',
            '  【警告】服务已暴露到局域网，但没有开第二道准入（require_token=false）：',
            '         同网段任何设备都能拿账号口令试探本端口（认证有失败限速，但仍是单凭据）。',
            '         建议把 config.json 的 require_token 改为 true 并重启，'
            '客户端加 X-StuLink-Token 头。',
        ])
    lines.extend([
        '',
        f"  配置文件 : {config.CONFIG_PATH}",
        '  停止服务 : 在本窗口按 Ctrl+C',
        '=' * 68,
    ])
    try:
        print('\n'.join(lines), flush=True)
    except UnicodeEncodeError:  # 控制台编码异常时不阻塞启动
        print(f'StuLink MCP started: {url}', flush=True)


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
    cfg = config.load_config()
    try:
        app, _server = build_asgi_app(cfg)
        me = (bootstrap.warm_up(cfg['bind_user'])
              if _auth_mode(cfg) == 'token' else None)
    except Exception as exc:  # noqa: BLE001  启动期错误要打印清楚原因
        print(f'[MCP] 启动失败：{exc}', flush=True)
        return 1
    _print_banner(cfg, me)
    import uvicorn
    uvicorn.run(app, host=cfg['host'] or '127.0.0.1', port=int(cfg['port']),
                log_level='info')
    return 0


if __name__ == '__main__':
    sys.exit(main())
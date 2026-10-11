# -*- coding: utf-8 -*-
"""StuLink MCP 服务入口：HTTP(Streamable HTTP) 传输 + Bearer 令牌鉴权。

启动方式（二选一）：
  · 双击 mcp_server\\start_mcp.bat（首次会自动创建虚拟环境并装依赖）
  · 命令行：mcp_server\\.venv\\Scripts\\python.exe mcp_server\\run.py

客户端接入：URL = http://<监听地址>:<端口><path>，请求头带
            Authorization: Bearer <config.json 中的 token>
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
    '本服务提供 StuLink 教务管理系统的数据查询与操作能力，覆盖：基础信息（年级/班级/学生）、'
    '考试与成绩、统计分析（学科分析/分数段/上线率/班级对比）、考勤、课表、宿舍、学生画像。\n'
    '使用约定：\n'
    '1. 所有操作以服务端配置的账号身份执行，越界数据会返回"权限不足"；\n'
    '2. 先用 list_grades / list_classes / search_students 拿到准确的年级、班级、学号，'
    '再用 list_exams 拿 exam_id，避免凭空猜测参数；\n'
    '3. 班级名如 "01班"、年级如 "2025级"，务必与 list_classes 返回一致；\n'
    '4. 标有【写操作】的工具会真实修改数据，调用前请向用户确认；\n'
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


def build_asgi_app(cfg):
    """组装 ASGI 应用：MCP(Streamable HTTP) → Bearer 鉴权中间件。"""
    bootstrap.warm_up(cfg['bind_user'])
    server = MCPServer(
        name='stulink',
        title='StuLink 教务系统',
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
    return auth.BearerAuth(starlette_app, cfg['token']), server


def _print_banner(cfg, me):
    """启动横幅：把客户端需要的信息一次印全，省得翻文档。"""
    host = cfg['host'] or '127.0.0.1'
    shown = _lan_ip() if host == '0.0.0.0' else host
    url = f"http://{shown}:{cfg['port']}{cfg['path'] or '/mcp'}"
    lines = [
        '=' * 68,
        '  StuLink MCP 服务已启动',
        '',
        f"  接入地址 : {url}",
        f"  访问令牌 : {cfg['token']}",
        f"  执行身份 : {me['real_name']}（{me['username']}，{me['role']}）",
        f"  写操作   : {'允许' if cfg.get('allow_write') else '已禁用（allow_write=false）'}",
        '',
        '  客户端配置示例（Streamable HTTP）：',
        '  {',
        f'    "url": "{url}",',
        '    "headers": { "Authorization": "Bearer <上面的访问令牌>" }',
        '  }',
        '',
        f"  配置文件 : {config.CONFIG_PATH}",
        '  停止服务 : 在本窗口按 Ctrl+C',
        '=' * 68,
    ]
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
    except Exception as exc:  # noqa: BLE001  启动期错误要打印清楚原因
        print(f'[MCP] 启动失败：{exc}', flush=True)
        return 1
    me = bootstrap.warm_up(cfg['bind_user'])
    _print_banner(cfg, me)
    import uvicorn
    uvicorn.run(app, host=cfg['host'] or '127.0.0.1', port=int(cfg['port']),
                log_level='info')
    return 0


if __name__ == '__main__':
    sys.exit(main())
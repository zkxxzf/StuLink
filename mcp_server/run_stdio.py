# -*- coding: utf-8 -*-
"""StuLink 成绩分析 MCP 服务（stdio 传输入口）：供 CodeBuddy / Claude Desktop 等本地客户端直连。

与 run.py（HTTP）的区别：
  · 走标准输入输出管道，由客户端以子进程方式拉起，不占端口、不需要令牌；
  · 启动阶段的提示一律写 stderr，stdout 只留给 MCP 协议（避免污染协议流）；
  · 权限、数据范围与审计完全复用 core 层，与 HTTP 模式行为一致（同为只读成绩工具集）。

客户端配置（.codebuddy/mcp.json 或 Claude Desktop 等）：
  {
    "mcpServers": {
      "stulink": {
        "type": "stdio",
        "command": "<项目路径>\\\\mcp_server\\\\.venv\\\\Scripts\\\\python.exe",
        "args": ["<项目路径>\\\\mcp_server\\\\run_stdio.py"],
        "env": { "PYTHONUTF8": "1" }
      }
    }
  }
"""
import asyncio
import logging
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# stdio 协议按 UTF-8 传输；Windows 默认 cp936 会让中文 JSON 乱码或直接报错
for _stream in (sys.stdin, sys.stdout):
    try:
        _stream.reconfigure(encoding='utf-8')
    except Exception:  # noqa: BLE001  非标准流（被重定向）时跳过
        pass

_stdout = sys.stdout
sys.stdout = sys.stderr      # 初始化期间的一切输出走 stderr，绝不污染协议流
try:
    from mcp.server.mcpserver import MCPServer
    from mcp_server import config
    from mcp_server.core import auth, bootstrap, identity
    from mcp_server.run import INSTRUCTIONS
    from mcp_server.tools import register_all

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format='%(asctime)s %(levelname)s [%(name)s] %(message)s')

    cfg = config.load_config()
    mode = (cfg.get('auth_mode') or 'account').strip().lower()

    if mode == 'token':
        # 兼容模式：固定 bind_user 身份（本地子进程模型）
        me = bootstrap.warm_up(cfg['bind_user'])
        identity_note = f"{me['real_name']}（{me['username']}）· 静态令牌模式"
    else:
        # 账号密码模式：stdio 没有 HTTP 头，凭据由客户端通过环境变量注入，
        # 启动时校验一次并固化为进程级身份（否则拒绝启动 = 禁止未验证的随意调用）。
        username = (os.environ.get('STULINK_MCP_USER') or '').strip()
        password = os.environ.get('STULINK_MCP_PASSWORD') or ''
        if not username or not password:
            print('[MCP] stdio 模式需要账号密码：请在客户端配置的 env 中设置 '
                  'STULINK_MCP_USER 与 STULINK_MCP_PASSWORD', file=sys.stderr)
            sys.exit(2)
        app = bootstrap.get_app()
        with app.app_context():
            user_id, error, message = auth.verify_credentials(username, password)
            if error:
                auth.audit_auth_failure(username, error, message, client='stdio')
        if error:
            print(f'[MCP] stdio 身份认证失败：{message}', file=sys.stderr)
            sys.exit(3)
        identity.set_static(user_id)
        identity_note = f'{username}（启动时认证一次，进程级生效）'

    server = MCPServer(
        name='stulink',
        title='StuLink 教务系统',
        instructions=INSTRUCTIONS,
        version='1.0.0',
    )
    loaded = register_all(server)
    print(f"[MCP] stdio 模式已就绪：执行身份 {identity_note}，"
          f"工具集 {'、'.join(label for _n, label in loaded)}（只读）",
          file=sys.stderr, flush=True)
finally:
    sys.stdout = _stdout     # 关键：协议运行前必须恢复真实 stdout，否则响应会写进 stderr

asyncio.run(server.run_stdio_async())

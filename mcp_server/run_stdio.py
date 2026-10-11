# -*- coding: utf-8 -*-
"""StuLink MCP 服务（stdio 传输入口）：供 CodeBuddy / Claude Desktop 等本地客户端直连。

与 run.py（HTTP + Bearer 令牌）的区别：
  · 走标准输入输出管道，由客户端以子进程方式拉起，不占端口、不需要令牌；
  · 启动阶段的提示一律写 stderr，stdout 只留给 MCP 协议（避免污染协议流）；
  · 权限、数据范围、写开关与审计完全复用 core 层，与 HTTP 模式行为一致。

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
    from mcp_server.core import bootstrap
    from mcp_server.run import INSTRUCTIONS
    from mcp_server.tools import register_all

    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format='%(asctime)s %(levelname)s [%(name)s] %(message)s')

    cfg = config.load_config()
    bootstrap.warm_up(cfg['bind_user'])

    server = MCPServer(
        name='stulink',
        title='StuLink 教务系统',
        instructions=INSTRUCTIONS,
        version='1.0.0',
    )
    loaded = register_all(server)
    print(f"[MCP] stdio 模式已就绪：执行身份 {cfg['bind_user']}，"
          f"写操作 {'允许' if cfg.get('allow_write') else '已禁用'}，"
          f"工具模块 {'、'.join(label for _n, label in loaded)}",
          file=sys.stderr, flush=True)
finally:
    sys.stdout = _stdout     # 关键：协议运行前必须恢复真实 stdout，否则响应会写进 stderr

asyncio.run(server.run_stdio_async())

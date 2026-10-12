# StuLink MCP 服务
#
# 独立进程、独立虚拟环境（mcp_server/.venv）的 MCP（Model Context Protocol）服务，
# 通过 HTTP(Streamable HTTP) 对外提供 StuLink 教务系统的查询与操作工具。
#
# 设计要点（2026-10-11 建立）：
#   1. 不修改 app/ 下任何现有代码，只复用 create_app() 与 service 层；
#   2. 全部 ORM 操作在专用单线程 + 独立 app context 内串行执行
#      （SQLite 写操作本就整库串行，串行化可避免会话串用与会话中毒）；
#   3. 所有操作以一个配置指定的 StuLink 账号身份执行，
#      因此 has_perm / student_in_scope 等现有权限与数据范围逻辑原样生效；
#   4. HTTP 入口强制 Bearer 令牌鉴权 + DNS 重绑定防护。
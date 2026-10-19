# MCP 服务接入排查：容器部署下用「宿主机 IP」访问被 421 拦截

- **记录日期**：2026-10-12
- **部署形态**：NAS 容器部署 —— `deploy/docker-compose.yml` 中的 `mcp` 服务（镜像 `stulink-mcp:v1.0.0`，端口映射 `8765:8765`，环境变量 `STULINK_MCP_HOST=0.0.0.0`）
- **客户端形态**：桌面客户端的「自定义连接器」（HTTP 传输），URL `http://10.193.191.210:8765/mcp`，Headers 用 `Authorization: Basic base64(账号:密码)`

## 1. 现象

客户端保存后无法访问，但：

- `10.193.191.210:8765` 端口可连通（`Test-NetConnection` 通过）、主机在线；
- 客户端里填的账号密码、Basic 头格式均正确。

## 2. 定位：服务端返回 `421 Invalid Host header`

在本机用 curl 复现（把 MCP initialize 请求体存成 `mcp_probe.json` 后再发）：

```powershell
curl.exe -s -i -m 8 "http://10.193.191.210:8765/mcp" -X POST `
  -H "Authorization: Basic <base64(账号:密码)>" `
  -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" `
  -d "@C:\path\to\mcp_probe.json"
```

请求体内容（`mcp_probe.json`）：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}
```

响应：

```
HTTP/1.1 421 Misdirected Request
server: uvicorn
Invalid Host header
```

再把 **Host 头**换成白名单内的值重试（仅用于验证定位，客户端不用这样做）：

```powershell
curl.exe ... -H "Host: localhost:8765" ...
```

响应变为：

```
HTTP/1.1 200 OK
content-type: text/event-stream

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"capabilities":{...},"serverInfo":{"name":"stulink","title":"StuLink 教务系统","version":"1.0.0"}}}
```

**结论**：服务在跑、账号密码正确；唯一的问题是服务端 **Host 白名单不认识 `10.193.191.210`**。

## 3. 根因

`mcp_server/run.py` 启用了 DNS 重绑定防护：

```python
starlette_app = server.streamable_http_app(
    ...,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(cfg),
    ),
)
```

`_allowed_hosts()` 的自动白名单（`allowed_hosts` 留空时）：

- `localhost`、`127.0.0.1`
- `cfg.host`（值为 `0.0.0.0` 时**不加入**）
- `_lan_ip()`：本机局域网 IP

每个名字都会生成「不带端口」与「带端口」两种形式。

**MCP 跑在 NAS 的容器里**：`_lan_ip()` 探测到的是**容器自身的内网 IP**，不是宿主机的 `10.193.191.210`；`STULINK_MCP_HOST=0.0.0.0` 又不把自身加入白名单。
→ 因此一切**经宿主机 IP（或域名）进入**的请求都被 421 拒绝，只有容器本机 `127.0.0.1` 的请求能通过。

> 直接 `python mcp_server/run.py`（非容器）运行时，`_lan_ip()` 拿到的就是宿主机局域网 IP，不会出现这个问题 —— 这是**容器部署特有的坑**。

## 4. 修复方案

### 方案 A（推荐：服务端根治，所有客户端/其它电脑都能连）

在 NAS 上编辑 `/volume1/docker/stulink/docker-compose.yml`，给 `mcp` 服务的 `environment` 增加一行（IP 与 IP:端口 两种形式都写）：

```yaml
  mcp:
    environment:
      - TZ=Asia/Shanghai
      - DATABASE_URL=sqlite:////app/data/system.db
      - STULINK_MCP_HOST=0.0.0.0
      - STULINK_MCP_ALLOWED_HOSTS=10.193.191.210,10.193.191.210:8765
```

重建容器：

```bash
cd /volume1/docker/stulink
docker compose up -d mcp
```

（或在群晖 Container Manager 里对该项目点「重新构建」）

改完后客户端保持 `http://10.193.191.210:8765/mcp` 即可访问。
若还有其它访问入口（其它 IP、域名），逗号分隔一并加进 `STULINK_MCP_ALLOWED_HOSTS`。

### 方案 B（变通：只在客户端本机生效，无需动 NAS）

在客户端所在的电脑上（**管理员** PowerShell）加本机端口转发：

```powershell
netsh interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=8765 connectaddress=10.193.191.210 connectport=8765
```

然后把客户端 URL 改为：

```
http://127.0.0.1:8765/mcp
```

（Headers 不变。此时请求的 Host 头为 `127.0.0.1:8765`，落在服务端自动白名单内。）

注意：

- 转发表**持久保存**（重启电脑不丢），依赖系统服务 `iphlpsvc`；
- 查看现有规则：`netsh interface portproxy show all`；
- 卸载（管理员）：

  ```powershell
  netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=8765
  ```

  ⚠️ 若表中存在与本项目无关的条目（例如 `127.0.0.1:445 → ...`），**不要删除**；
- 局限：`127.0.0.1` 只在配置了转发的这台电脑上有效，其它机器仍需方案 A。

## 5. 备忘：account 模式下客户端凭据怎么填

`auth_mode=account`（默认）为每请求校验 StuLink 账号密码，**以下两种任选其一**：

| 方式 | Headers |
|---|---|
| 两个自定义头 | `X-StuLink-User: <账号>` + `X-StuLink-Password: <密码>` |
| Basic 单头（适合只能填一个 Header 的客户端） | `Authorization: Basic base64(<账号>:<密码>)` |

- 若 `require_token=true`（或 `auth_mode=token`），还需 `X-StuLink-Token: <服务令牌>`（或 `Authorization: Bearer <令牌>`），令牌见 `mcp_server/config.json`；
- MCP 服务启动横幅会按当前模式直接打印「客户端配置示例」，可直接照抄。

## 6. 相关代码位置

| 位置 | 作用 |
|---|---|
| `mcp_server/run.py::_allowed_hosts()` | Host 白名单自动生成逻辑 |
| `mcp_server/run.py::build_asgi_app()` | 组装 `TransportSecuritySettings`（DNS 重绑定防护） |
| `mcp_server/config.py` | `allowed_hosts` 配置项；`STULINK_MCP_*` 环境变量覆盖（list 类型按逗号分隔） |
| `mcp_server/core/auth.py` | 接入认证（Basic / 自定义头 / 服务令牌，错误码 401/429） |

---

## 7. 补充（2026-10-15）：本机直接运行、开着代理 TUN 时也会被 421

- **现象**：本机跑 `mcp_server\start_mcp.bat`（非容器），启动横幅打印的接入地址是
  `http://198.18.0.1:8766/mcp`；客户端（同一台机器）改用真实局域网地址
  `http://10.193.191.52:8766/mcp` 连接，服务端日志出现
  `WARNING [mcp.server.transport_security] Invalid Host header: 10.193.191.52:8766`
  → `421 Misdirected Request`，账号密码填法正确也进不来。
- **根因**：本机装着代理（Clash/Meta 之类）的 TUN 模式，默认路由落在它的虚拟网卡上，
  于是 `_lan_ip()` 的 UDP 路由探测拿到的是它的 **fake-ip 网关 `198.18.0.1`**
  （`198.18.0.0/15` = RFC 2544 保留段）。结果：横幅印错地址，Host 白名单只认
  `198.18.0.1`，**真实局域网 IP 反而被 421 拒绝**。（容器部署那一版根因不同，见第 3 节。）
  > 同一个坑还影响过别处：安全回归 H-9 里 `api.deepseek.com` 被解析成 `198.18.0.22`。
  > 凡是"探测本机 IP / 判断是否公网"的代码，都要把 `198.18.0.0/15` 排除掉。
- **修复（服务端，`mcp_server/run.py`）**：
  - 新增 `_local_ipv4s()`：路由探测 + `gethostbyname_ex(gethostname())` 汇总本机地址，
    剔除环回 / 链路本地 / 多播 / 未指定，以及 `198.18.0.0/15` fake-ip 段；
  - `_allowed_hosts()` 把**全部网卡地址 + 主机名**都加入白名单（各含带端口形式），
    不再只取单个 IP；
  - `_lan_ip()` 按内网优先级 `192.168 → 10 → 172.16-31` 挑选印刷地址，避免选到虚拟网卡。
- **验证（2026-10-15，本机）**：

  | 项 | 结果 |
  |---|---|
  | 横幅地址 | `http://10.193.191.52:8766/mcp`（不再是 `198.18.0.1`） |
  | `_validate_host('10.193.191.52:8766')` / `'127.0.0.1:8766'` / `'sakaay-com:8766'` | True / True / True |
  | 负向对照 `'evil.example.com'` / `'198.18.0.1:8766'` | False / False |
  | 局域网 IP 直连（带 Host 头、无凭据） | `401 unauthorized`（不再是 421） |
  | `mcp_server\.venv\Scripts\python.exe mcp_server\smoke_scores.py` | 通过 76 / 失败 0 |

- **若仍连不上**：① 同机客户端直接用 `http://127.0.0.1:8766/mcp`，跨设备用横幅给出的局域网地址；
  ② 跨设备还要确认服务端防火墙放行 8766；③ 访问入口不是自动白名单里的 IP／域名时，
  在 config.json 的 `allowed_hosts`（或环境变量 `STULINK_MCP_ALLOWED_HOSTS`，逗号分隔）里补上。
- **关于启动横幅打印的「服务令牌」**：只要 config.json 里 `token` 有值就会打印（首尾片段 + `（可选）`），
  **不代表开了令牌模式**；`auth_mode=account` 且 `require_token=false` 时客户端带不带
  `X-StuLink-Token` 都被忽略。只有 `auth_mode=token` 或 `require_token=true` 时它才是必需的。

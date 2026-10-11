# StuLink MCP 服务：认证与授权设计（2026-10-11）

本文是 `mcp_server/` 接入认证与授权的权威说明。核心目标三条：

1. **接入必须身份验证**：每个调用方都要用 StuLink 账号密码登录，未经验证一律拒绝；
2. **权限与平台用户体系完全一致**：认证身份直接复用系统既有的
   `User.has_perm`、数据范围函数与分析维度矩阵——不另造一套权限；
3. **拒绝策略明确**：认证失败/权限不足时返回什么、记什么日志，都有固定约定。

---

## 一、认证模型（Authentication）

### 1.1 两种模式（`config.json` 的 `auth_mode`）

| 模式 | 说明 | 适用 |
|---|---|---|
| `account`（**默认**） | **每个 HTTP 请求**携带 StuLink 账号密码，逐请求校验；执行身份 = 该账号 | 推荐：多人/多客户端共用同一 MCP 服务 |
| `token`（兼容） | 只校验静态访问令牌 `token`，执行身份固定为 `bind_user` | 旧客户端平滑过渡、单人本机调试 |

stdio 模式没有 HTTP 头，凭据由客户端通过环境变量注入，**启动时校验一次**并固化为
进程级身份（未配置或校验失败直接拒绝启动）。

### 1.2 凭据携带方式（二选一）

```http
Authorization: Basic base64(账号:密码)
```
或
```http
X-StuLink-User: <账号>
X-StuLink-Password: <密码>
```

可选第二道准入（`require_token=true` 时必需）：

```http
X-StuLink-Token: <config.json 里的 token>      # 也接受 Authorization: Bearer <token>
```

### 1.3 校验流程与缓存

```
请求 → [可选]服务令牌 → 账号密码非空? → 失败锁定检查? → 账号存在且启用?
     → 凭据缓存命中（password_hash 指纹未变）? → 命中→放行
     → 未命中 → werkzeug scrypt 校验（约 50–150ms）→ 通过则写入缓存
```

- **凭据缓存**（`credential_cache_ttl`，默认 300 秒）：缓存键是
  `sha256(账号\0密码)`，命中后仍会比对当前 `password_hash` 指纹与启用状态，
  因此**改密码、停用账号立即生效**，缓存只省掉 scrypt 计算。
- **失败锁定**（`login_fail_limit` 默认 5，`login_lock_seconds` 默认 300）：
  按账号维度计数；锁定期间不再查库、直接 429，也不再写审计（防止刷日志）。
  按账号而非按 IP，是因为本服务通常部署在本机/局域网共享出口，按 IP 会误伤
  同一台机器上的其他客户端。
- 校验在 MCP 的专用 DB 线程（`core/db.py`）执行，不阻塞事件循环。

### 1.4 身份如何传给工具（关键实现）

MCP SDK（mcp 2.3.0）的 StreamableHTTP 链路会把「写消息那一刻」的 contextvars
快照并在分发时还原（`mcp/shared/_context_streams.py` + `jsonrpc_dispatcher._spawn`），
这是 SDK 为 ASGI 认证中间件预留的官方路径。因此：

```
认证中间件（core/auth.py::AccessGuard，ASGI 层）
    → identity.set_current(user_id)            # contextvars
    → MCP SDK 分发（现代/legacy 路径都会保留该 context）
    → 工具函数（async，同 task）
    → toolkit._dispatch 在 async 侧读出 user_id → 显式传参进 DB 线程
    → bootstrap.load_user(user_id) → guards 校验
```

注意：`loop.run_in_executor` **不复制** contextvars，所以身份绝不依赖它跨线程
传播，而是显式传参（`core/toolkit.py`）。

---

## 二、授权模型（Authorization）

执行身份确定后，工具执行前统一过三道关，**与网页端同一套体系**：

| 层 | 实现 | 与网页端对应 |
|---|---|---|
| ① 功能权限 | `guards.perm(user, key)` = `User.has_perm(key)`；每个工具的 `toolkit.db_call(..., perm=...)` 声明权限点 | 网页端 `@perm_required(同名权限点)` |
| ② 数据范围 | `student_in_scope / class_allowed / visible_class_names / apply_student_scope` | 网页端同名函数（成绩/工作台/学生模块） |
| ③ 分析维度 | `guards.tab_scope(user, tab)`（grade/class/subject/teacher/compare） | 网页端 `_guard_tab` 的角色矩阵 |

铁律：**AI 通过 MCP 能看到的范围 = 该账号在网页端能看到的范围**。新增工具时
必须同时声明 `perm=` 并选用与网页端对应页面相同的范围函数（详见
`聊天总结/20261011_批次总结_MCP二轮审计整改_v1.19.5.0.md` 的两次口径修正）。

---

## 三、拒绝策略（明确约定）

### 3.1 接入层（HTTP，直接拒绝，不进入 MCP）

| 场景 | 状态码 | 响应体 `error` | 说明 |
|---|---|---|---|
| 缺少账号密码 | 401 | `unauthorized` | 提示两种携带方式 |
| 账号或密码错误 / 账号不存在 | 401 | `invalid_credentials` | 统一文案「账号或密码错误」，不区分账号是否存在（防枚举） |
| 账号已停用 | 401 | `account_disabled` | 明确提示联系管理员 |
| 服务令牌缺失/错误 | 401 | `invalid_token` | 仅 `require_token=true` 或 token 模式时出现 |
| 连续失败超限 | 429 | `too_many_attempts` | 带 `Retry-After: <秒>` 头 |
| Host 头不在白名单 | 400/403 | （SDK 内置） | DNS 重绑定防护，`allowed_hosts` 配置 |

响应体统一为 `{"error": "...", "message": "中文说明"}`，`Content-Type: application/json`。

### 3.2 授权层（MCP 工具返回，模型可见）

权限不足与数据越界**不抛协议错误**，而是返回可读 JSON，便于模型向用户解释：

```json
{"ok": false, "error": "权限不足：当前账号没有对应权限或数据范围，可在系统的权限管理中调整"}
```

内部异常返回 `{"ok": false, "error": "执行失败：<异常类名>；详细原因已记录在 MCP 服务端日志"}`（不泄露堆栈）。

### 3.3 审计

- **每次工具调用**：写系统操作日志（`module='mcp'`，含工具名、参数摘要、执行账号、成功/失败）。只读调用是否记录由 `audit_reads` 决定，写操作始终记录。
- **认证失败**：写操作日志 `action='MCP认证失败'`（`user_id` 为空，记录账号名、失败原因、来源 IP），同时写 MCP 服务端日志。锁定期间的重复失败不重复入库。
- 审计失败静默（只记服务端日志），不影响拒绝响应。

---

## 四、客户端接入

### 4.1 HTTP（推荐）

```json
{
  "mcpServers": {
    "stulink": {
      "type": "streamableHttp",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "X-StuLink-User": "<你的 StuLink 账号>",
        "X-StuLink-Password": "<你的账号密码>"
      }
    }
  }
}
```

（`require_token=true` 时再加 `"X-StuLink-Token": "<config.json 里的 token>"`。）

建议为 MCP 单独建一个**权限最小的账号**（例如只读所需的查看权限），
而不是用 `admin`——这是"权限与网页端一致"最直接的收益。

### 4.2 stdio（本地子进程）

```json
{
  "mcpServers": {
    "stulink": {
      "type": "stdio",
      "command": "<项目路径>\\mcp_server\\.venv\\Scripts\\python.exe",
      "args": ["<项目路径>\\mcp_server\\run_stdio.py"],
      "env": {
        "PYTHONUTF8": "1",
        "STULINK_MCP_USER": "<你的 StuLink 账号>",
        "STULINK_MCP_PASSWORD": "<你的账号密码>"
      }
    }
  }
}
```

未设置这两个环境变量时 stdio 进程**拒绝启动**（退出码 2）；认证失败退出码 3。

---

## 五、服务端配置（`mcp_server/config.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `auth_mode` | `account` | `account`=账号密码每请求校验；`token`=静态令牌+固定身份 |
| `require_token` | `false` | account 模式下是否额外要求 `X-StuLink-Token`（双重准入） |
| `token` | 自动生成 | 静态令牌（token 模式必需；account 模式下由 `require_token` 决定用途） |
| `bind_user` | `admin` | **token 模式**的执行账号；account 模式不生效 |
| `credential_cache_ttl` | 300 | 账号密码校验缓存秒数（0=每次完整校验） |
| `login_fail_limit` | 5 | 连续失败锁定阈值（0=不锁定） |
| `login_lock_seconds` | 300 | 锁定时长（秒） |
| `allow_write` | `true` | 写工具总开关 |
| `max_rows` / `exam_cache_ttl` / `allowed_hosts` / `host` / `port` / `path` / `audit_reads` | 见 config.json | 非认证项，语义不变 |

所有键均可用环境变量覆盖（`STULINK_MCP_<大写键名>`），便于不落盘部署。

---

## 六、实现结构

| 文件 | 职责 |
|---|---|
| `core/auth.py` | 凭据解析（Basic / 自定义头 / 令牌）、校验缓存与失败锁定、`AccessGuard` 中间件、统一拒绝响应、认证失败审计 |
| `core/identity.py` | 请求级身份 contextvar + 进程级静态身份（stdio），工具执行时读取 |
| `core/toolkit.py` | 身份解析（请求级→进程级）并显式传入 DB 线程；`perm` 校验；异常兜底 |
| `core/bootstrap.py` | app 单例；`load_user(uid)`（每请求）；`warm_up(bind_user)`（token 模式） |
| `run.py` | 组装 ASGI（`AccessGuard` 包 MCP）+ 启动横幅（按模式给出客户端示例） |
| `run_stdio.py` | stdio 模式启动认证（env 账号密码，失败拒绝启动） |
| `smoke_mcp.py` | 40 项自测（含 account 模式端到端认证用例） |

---

## 七、安全边界与已知限制

1. **密码明文在客户端配置文件里**（与旧令牌同级）。风险由文件权限与"专用低权限账号"缓解；不支持 OAuth 是刻意取舍（本地/局域网单服务，OAuth 动态注册属过度设计）。
2. **未做会话绑定**：每个请求独立认证，同一 MCP session 可被不同账号的请求复用（各自按自身身份执行，不产生越权；但共享了传输层会话）。
3. **锁定按键隔离**：锁定按账号维度；攻击者遍历大量用户名时不会触发单账号锁定（本地服务场景可接受）。
4. **内存态**：凭据缓存与失败计数为进程内状态，重启即清空。
5. **写开关仍独立**：`allow_write=false` 时，即使认证通过的账号也不能调用写工具。
6. 传输层建议继续保持 `127.0.0.1` 绑定；若改为 `0.0.0.0`，请同时开启
   `require_token=true` 并为账号设置强密码。

---

## 八、验证

- 端到端自测：`mcp_server\.venv\Scripts\python.exe mcp_server\smoke_mcp.py`
  → **通过 40 / 失败 0**，其中认证相关 6 项：无凭据 401、错密码 401、正确凭据进入
  MCP 并调用工具、`X-StuLink-User/Password` 方式、越权工具按账号权限被拒、
  连续失败锁定 429（带 Retry-After）。
- stdio：无凭据拒绝启动（rc=2）、错密码拒绝启动（rc=3）、正确凭据就绪。
- 修改本机制后必须**重启 MCP 进程**生效。

## 九、迁移与回滚

- **从旧版（只有令牌）升级**：`config.json` 已是 `auth_mode=account`，客户端配置
  需从 `Authorization: Bearer <token>` 改为账号密码头（见第四节）。未更新配置的
  客户端会收到 401（响应体含配置提示）。
- **临时回滚**：把 `config.json` 的 `auth_mode` 改成 `token` 并重启，即恢复旧行为
  （静态令牌 + `bind_user` 身份），无需改代码。

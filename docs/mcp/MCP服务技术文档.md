# StuLink MCP 服务技术文档

> 面向开发与运维：说明 MCP 服务的定位、架构、工具清单、依赖、启动方式（本地 / Docker）、
> 配置、与主应用的边界、运维与验证。
> 认证与授权的完整设计另见 [MCP 服务认证与授权设计](MCP服务-认证与授权设计.md)；
> 客户端侧安装与排查见 [AI 工具安装与配置指南](../规范与说明/AI工具安装与配置指南.md)。

---

## 一、定位与设计原则

MCP（Model Context Protocol）服务把 StuLink 教务系统的**成绩数据**，以标准协议暴露给
AI 客户端（IDE 插件、桌面应用、豆包等）。**本服务只做成绩分析、且只读**：
12 个查询工具，不含任何写入能力。它**不是主应用的一部分**，而是一个
**独立进程 / 独立容器**，与主应用解耦：

| 原则 | 含义 |
|---|---|
| **进程独立** | 单独启动（uvicorn 承载 Streamable HTTP），主应用完全不 import `mcp_server`，双方零引用 |
| **共库不共进程** | 直接读取主应用的 SQLite 数据文件（`data/*.db`），不复制数据、不建第二套存储 |
| **权限同源** | 认证身份直接复用 `User.has_perm` 与数据范围函数，**AI 能看到的范围 = 该账号网页端能看到的范围** |
| **细粒度授权** | 成绩可见范围由 `app/modules/grades/services/score_guard.py`（`core/score_guard.py` 薄转发）按三条规则裁决：任课教师=**任教年级×学科**（该年级该科全部班）/ 班主任=本班 / 年级长及以上=本年级 |
| **只读** | 工具集无任何写入路径（`allow_write` 对本服务无实际作用），权限范围仅限读取成绩 |
| **默认不暴露** | 默认只听 `127.0.0.1`；容器 / 局域网部署时显式改为 `0.0.0.0` 并加令牌 |

---

## 二、整体架构

```
AI 客户端（IDE 插件 / 桌面应用 / 豆包）
        │  Streamable HTTP（默认 http://<host>:8765/mcp，携带 StuLink 账号密码）
        ▼
┌───────────────────────────────────────────────┐
│  MCP 服务进程（mcp_server/run.py → uvicorn）     │
│                                                 │
│  AccessGuard（ASGI 认证中间件）  ← core/auth.py  │
│        │ 通过：写入 contextvars 身份             │
│        ▼                                        │
│  MCPServer（mcp 2.x SDK）                        │
│        │  工具分发（12 个 · 全部只读）            │
│        ▼                                        │
│  core/toolkit.py（功能权限点 + 异常兜底）        │
│        ▼                                        │
│  core/score_guard.py（成绩可见范围：三条规则）   │
│        ▼                                        │
│  core/db.py（单线程串行 + 独立 app context）     │
│        ▼                                        │
│  Flask app（复用主应用 create_app）+ SQLAlchemy  │
└───────────────────────────────────────────────┘
        │  直连
        ▼
   data/*.db（与主应用容器共享同一份数据目录）
```

- **传输**：默认 Streamable HTTP（`/mcp`）；另提供 stdio（`run_stdio.py`）供本地子进程型客户端。
- **认证**：默认 `account` 模式，**每个请求**校验 StuLink 账号密码；`token` 为兼容模式。
- **数据库访问**：所有 ORM 操作在**单线程 + 独立 app context**内串行执行（`core/db.py`），
  规避 SQLite 写锁与 Flask-SQLAlchemy 会话跨 context 串用问题。

---

## 三、工具清单（2 模块 · 12 个 · 全部只读）

| 模块 | 工具 |
|---|---|
| 成绩查询（只读） | `list_my_score_scope`、`list_exams`、`list_classes`、`get_exam_overview`、`get_class_score_summary`、`get_class_score_detail`、`get_student_scores`、`get_student_score_trend` |
| 成绩分析（只读） | `get_subject_analysis`、`get_exam_score_segments`、`get_layer_online_stats`、`compare_classes` |

- 每个工具在 `toolkit.db_call(..., perm='grades.view')` 声明权限点，与网页端同名；
- **没有任何写工具**：服务不提供成绩录入 / 改分 / 重算等写入路径；
- 返回值统一为 JSON 文本：`{"ok": true, "data": ...}` 或 `{"ok": false, "error": "原因"}`。

### 3.1 两个通用参数：`kind` 与 `view`（2026-10-21）

| 参数 | 取值 | 说明 |
|---|---|---|
| `kind` | `exam`（默认）/ `timed` | 同一批工具查**普通考试**或**限时练**（独立库 `timed.db`）；不另开工具，避免 `tools/list` 变大 |
| `view` | `brief`（默认）/ `full` | `brief` 只给够用的字段、明细走列式 `{cols, rows}`；`full` 才给各科均分 / 分层明细 / 年级横向 |

另有 `top`（只看前 N 名）。两者都是**省额度**设计：改造后 12 个工具各调一次
约 4340 token（改造前约 18270），详见
[响应体积与额度优化](MCP服务-响应体积与额度优化.md)。

> 细粒度授权规则（任课教师=**任教年级×所教学科**（该年级该科全部班可见，含他班）
> + 每名学生的**总分年级名次**（只给名次、不给总分分数）/ 班主任=本班 / 年级长及以上=本年级）、
> 判定矩阵与各工具的范围过滤细则，见
> [MCP 服务成绩分析权限设计](MCP服务-成绩分析权限设计.md)。

---

## 四、依赖与运行环境

依赖清单见 [mcp_server/requirements.txt](../../mcp_server/requirements.txt)，由三部分组成：

| 组成 | 内容 | 说明 |
|---|---|---|
| 主应用依赖 | `-r ../requirements.txt` | MCP 复用主应用代码，直接引用同一份清单，避免两处漂移 |
| MCP SDK | `mcp>=2.3.0,<3.0.0` | **v2 线**：`FastMCP` 已更名 `MCPServer`（`mcp.server.mcpserver`）；自带 starlette/pydantic/httpx/anyio 等 |
| ASGI 服务器 | `uvicorn>=0.30,<1.0` | `run.py` 用它承载 Streamable HTTP |

- **运行时要 **Python 3.11+**（与主应用一致；镜像用 `python:3.11-slim`）。
- **隔离环境**：本地请用 `mcp_server/.venv`，不要装进全局 Python。
- 主应用侧**不需要**安装 `mcp`/`uvicorn`（`requirements.txt` 不含它们）。

---

## 五、启动方式

### 5.1 本地（venv + HTTP）

```bat
:: 首次：建隔离环境并装依赖
python -m venv mcp_server/.venv
mcp_server\.venv\Scripts\python.exe -m pip install -r mcp_server/requirements.txt

:: 每次启动（默认监听 127.0.0.1:8765，端点 /mcp）
mcp_server\.venv\Scripts\python.exe mcp_server\run.py
```

启动后控制台打印接入地址、认证模式、服务令牌（脱敏）与客户端配置示例。
需被局域网访问时，改 `config.json` 的 `host=0.0.0.0` 或设环境变量 `STULINK_MCP_HOST=0.0.0.0`。

### 5.2 本地（stdio）

由客户端拉起子进程，无需手动启动服务：

```json
{
  "mcpServers": {
    "stulink": {
      "type": "stdio",
      "command": "<项目路径>\\mcp_server\\.venv\\Scripts\\python.exe",
      "args": ["<项目路径>\\mcp_server\\run_stdio.py"],
      "env": { "PYTHONUTF8": "1",
               "STULINK_MCP_USER": "<账号>", "STULINK_MCP_PASSWORD": "<密码>" }
    }
  }
}
```

未提供账号密码拒绝启动（退出码 2），认证失败退出码 3。

### 5.3 Docker（独立容器，推荐用于 NAS / 服务器）

镜像由 `Dockerfile.mcp` 构建（构建上下文为仓库根目录）：

```bash
docker build -f Dockerfile.mcp -t stulink-mcp:v1.0.0 .
```

`deploy/docker-compose.yml` 已内置 `mcp` 服务（与 `stulink`、`alumni` 并列）：

```yaml
  mcp:
    image: stulink-mcp:v1.0.0
    container_name: stulink-mcp
    pull_policy: never
    depends_on:
      stulink:
        condition: service_healthy
    ports:
      - "8765:8765"
    volumes:
      - ./stulink-data:/app/data          # 与主应用共享同一数据目录
    restart: unless-stopped
    environment:
      - TZ=Asia/Shanghai
      - DATABASE_URL=sqlite:////app/data/system.db
      - STULINK_MCP_HOST=0.0.0.0          # 容器内必须监听 0.0.0.0
```

要点：

1. **数据卷**：必须挂载主应用的 `stulink-data → /app/data`（MCP 直接读库）。
   采用**读写**挂载而非 `:ro`——SQLite 开启 WAL 后，只读挂载会导致
   `unable to open database file`（见 `config.py` 的 WAL 说明）。
2. **监听地址**：镜像内置 `ENV STULINK_MCP_HOST=0.0.0.0`，否则端口无法被外部访问。
3. **令牌持久化**：容器内 `mcp_server/config.json` 随重启重建会重新生成令牌。
   需要固定令牌时，用环境变量注入（不落盘）：
   `- STULINK_MCP_REQUIRE_TOKEN=true` 与 `- STULINK_MCP_TOKEN=<随机令牌>`。
4. **只读**：服务本身不含写工具，无需再设写开关；需要完整留痕时可加
   `- STULINK_MCP_AUDIT_READS=true`（只读调用也写审计）。
5. **离线环境**：若 NAS 无法构建镜像，可在有网机器 `docker build` 后
   `docker save` / `docker load` 导入；或走整包部署脚本（见第七节）。

---

## 六、配置项

配置来源优先级：**环境变量 `STULINK_MCP_<大写键名>` > `mcp_server/config.json` > 内置默认值**。
首次启动自动生成 `config.json`（含随机令牌与逐键中文说明）。数据安全相关键：

| 键 | 默认 | 说明 | 容器部署建议 |
|---|---|---|---|
| `host` / `port` / `path` | `127.0.0.1` / `8765` / `/mcp` | 监听与端点 | `host` 走环境变量设 `0.0.0.0` |
| `auth_mode` | `account` | account=每请求校验账号密码（推荐）；token=静态令牌+固定身份 | 保持 `account` |
| `require_token` | `false` | account 下是否额外要求 `X-StuLink-Token` | 暴露到局域网/公网时设 `true` |
| `token` | 自动生成 | 静态令牌 | 用 `STULINK_MCP_TOKEN` 注入固定值 |
| `bind_user` | `admin` | 仅 token 模式的执行账号 | account 模式不生效 |
| `allow_write` | `true` | 写工具总开关（本服务无写工具，实际不生效） | 可忽略 |
| `audit_reads` | `false` | 只读调用是否也写审计 | 需完整留痕时设 `true` |
| `max_rows` | `200` | 单次返回最大行数 | 保持默认 |
| `pretty_json` | `false` | JSON 是否缩进（仅排查用，会让结果多付三到五成 token） | 保持默认 |
| `max_response_chars` | `24000` | 单次字符预算，超出给"结果过大 + 该加什么参数" | 保持默认 |
| `timed_cache_ttl` | `300` | 限时练派生缓存（名次/T 分）在 MCP 侧的失效秒数 | 保持默认 |
| `exam_cache_ttl` | `300` | 成绩分析缓存秒数（0=用主程序默认 900） | 保持默认 |
| `credential_cache_ttl` | `300` | 账号密码校验缓存秒数 | 保持默认 |
| `login_fail_limit` / `login_lock_seconds` | `5` / `300` | 连续失败锁定阈值 / 时长 | 保持默认 |
| `allowed_hosts` | `[]` | Host 白名单（DNS 重绑定防护），留空自动放行本机与监听地址 | 跨机访问时按需填写 |

> 安全提示：`config.json` 含访问令牌，已在 `.gitignore` 与 `.dockerignore` 中排除，**不得入库、不得打进镜像**。

---

## 七、部署与运维

### 7.1 与主应用容器共享数据的两种部署形态

| 形态 | 适用 | 做法 |
|---|---|---|
| **同机多容器（Compose）** | NAS / 服务器 | 三个服务 `stulink` / `alumni` / `mcp` 共享 `./stulink-data` 卷；`mcp` 依赖 `stulink` 健康后启动 |
| **独立主机** | MCP 单独一台机器 | 需把主应用 `data/` 目录以共享盘（NFS/SMB）挂给 MCP，或改 `DATABASE_URL` / `SQLALCHEMY_BINDS` 指向远程库（本项目当前以同机为主） |

### 7.2 NAS 部署（绿联 UGOS）

- 主应用沿用既有轻量更新链路：整包脚本 `deploy/nas_update_v*.sh` 或增量 `deploy/hot_update.ps1`
  （`docker cp` 进容器 + 重启）。详见 [部署文档](../规范与说明/部署文档.md)。
- **MCP 容器为新增服务**，需**先有 `stulink-mcp:v1.0.0` 镜像**再用 compose 起容器；
  仅 `docker cp` 代码不会带来 `mcp`/`uvicorn` 依赖（依赖在镜像层，不在可写层）。
- 若当前 NAS 账号权限不足（无法 `docker build` / 无镜像导入权限），
  **先不部署 MCP 容器，仅完成主应用更新**；待权限具备后补 `docker build`/`docker load` + `docker compose up -d mcp`。

### 7.3 升级与回滚

- **仅代码变更**（工具逻辑）：重新构建镜像 `docker build -f Dockerfile.mcp …`，`docker compose up -d mcp`。
- **依赖变更**：必须重建镜像（`mcp_server/requirements.txt` 或 `requirements.txt` 变化）。
- **回滚**：保留上一版镜像 tag（如 `stulink-mcp:v0.9.0`），改 compose 的 `image:` 后 `up -d mcp`。
- **配置变更**：改环境变量或 `config.json` 后**重启 MCP 进程**生效。

### 7.4 日志与审计

- 服务端日志：stdout（uvicorn + `stulink.mcp` logger）；容器内可用 `docker logs stulink-mcp` 查看。
- 业务审计：工具调用写入主应用系统操作日志（`module='mcp'`）；认证失败同样留痕
  （详见认证设计文档第三节）。

---

## 八、验证（L0–L6）

| 层级 | 验证 | 命令 / 操作 | 通过标准 |
|---|---|---|---|
| L0 进程 | 服务运行 | 看启动横幅 / `docker ps` | 横幅完整、容器 Up |
| L1 连通 | 端口可连 | `Test-NetConnection <host> -Port 8765` | TcpTestSucceeded=True |
| L2 协议 | MCP 握手 | 客户端连接 / `curl` 发 initialize | 非 4xx/5xx |
| L3 认证 | 凭据有效 | 真实账号密码连接 | 不再 401 |
| L4 工具 | 工具清单 | 客户端查看工具列表 | 12 项（全只读） |
| L5 取数 | 真实数据 | 问只读问题（如“查 2025级01班最近一次考试”） | 返回真实数据 |
| L6 边界 | 权限正确 | 受限账号越权提问 | 明确返回“权限不足” |

端到端自测（本地 venv）：

```bat
mcp_server\.venv\Scripts\python.exe mcp_server\smoke_scores.py
:: 期望输出：通过 69 / 失败 0
::   = 12 个只读工具 + 三条权限规则矩阵 + 真实账号端到端 + account 认证（401/429）
```

容器内自测：

```bash
docker exec stulink-mcp python mcp_server/smoke_scores.py
```

---

## 九、实现结构

| 路径 | 职责 |
|---|---|
| `Dockerfile.mcp` | MCP 服务镜像（Python 3.11-slim + 依赖 + 代码，暴露 8765） |
| `mcp_server/requirements.txt` | MCP 依赖清单（主应用依赖 + mcp SDK + uvicorn） |
| `mcp_server/run.py` | HTTP(Streamable) 入口：组装 ASGI、启动 uvicorn、打印启动横幅 |
| `mcp_server/run_stdio.py` | stdio 入口：env 账号密码认证，失败拒绝启动 |
| `mcp_server/config.py` | 配置加载（`config.json` + `STULINK_MCP_*` 环境变量覆盖） |
| `mcp_server/core/` | `auth`(认证中间件) · `identity`(请求身份) · `toolkit`(工具分发与鉴权) · `bootstrap`(app 引导) · `db`(串行 DB) · **`score_guard`(成绩权限引擎)** · `guards`(权限点/取对象) · `examdata` · `respond` · `audit` |
| `mcp_server/tools/` | `scores`(成绩查询 8 个) + `score_analysis`(成绩分析 4 个) + `_score_common`(共享常量与 helper)，全部只读 |
| `mcp_server/smoke_scores.py` | 62 项端到端自测（工具集 + 权限矩阵 + 真实账号 + 接入认证） |
| `mcp_server/smoke_auth.py` | account 模式接入认证端到端用例（被 smoke_scores 调用） |
| `deploy/docker-compose.yml` | `mcp` 服务定义（与 `stulink`/`alumni` 并列） |

---

## 十、相关文档

- [MCP 服务认证与授权设计](MCP服务-认证与授权设计.md)：认证模型、授权三层、拒绝策略、审计
- [MCP 服务成绩分析权限设计](MCP服务-成绩分析权限设计.md)：成绩分析专用服务的三条授权规则与工具清单
- [AI 工具安装与配置指南](../规范与说明/AI工具安装与配置指南.md)：客户端接入（HTTP/stdio）、L0–L6 验证、排障
- [部署文档](../规范与说明/部署文档.md)：主应用 Docker / NAS / 云服务器部署与升级

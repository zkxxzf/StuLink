# StuLink MCP 服务：认证与授权设计（2026-10-11）

本文是 `mcp_server/` 接入认证与授权的权威说明。核心目标三条：

1. **接入必须身份验证**：每个调用方都要用 StuLink 账号密码登录，未经验证一律拒绝；
2. **权限与平台用户体系完全一致**：认证身份直接复用系统既有的
   `User.has_perm`、数据范围函数与分析维度矩阵——不另造一套权限；
3. **拒绝策略明确**：认证失败/权限不足时返回什么、记什么日志，都有固定约定。

> **2026-10-12 收敛说明**：主仓库的 `mcp_server/` 已收敛为**成绩分析专用、且只读**
> （12 个成绩工具，细粒度授权见 [成绩分析权限设计](MCP服务-成绩分析权限设计.md)）。
> 本文的认证机制、拒绝策略、审计与配置项**完全继续适用**；
> 但第六节"实现结构"里的工具清单记录的是 `wr_mcp` 独立分支上更完整的**写工具**版本，
> 主仓库当前**不含**那些写工具，`allow_write` 在主仓库实际不生效。

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
  按 **「来源 + 账号」** 维度计数（2026-10-13 全面审计 M-5 起）；锁定期间不再查库、
  直接 429，也不再写审计（防止刷日志）。
  - 为什么不按账号锁：服务常以 `host=0.0.0.0` 暴露在局域网，若按账号锁，任何人用
    5 次错误口令就能把某位老师的账号锁死 300 秒（拒绝服务）。按来源锁之后，攻击者
    只能锁住自己这条来源，账号主人从自己设备登录不受影响。
  - 为什么不单纯按 IP 锁：本机 / 局域网可能共享出口 IP，纯按 IP 会误伤同一台机器
    上的其他客户端 —— 所以键是「来源 + 账号」这一组合，而不是来源本身。
  - 跨来源兜底（`login_throttle_threshold` 默认 10、`login_throttle_window` 默认
    300 秒、`login_throttle_max_delay` 默认 1 秒）：同一账号在窗口内失败累计超过阈值
    后，每次尝试**附加递增延迟**（上限 1 秒），只延迟、不拒绝 —— 分布式爆破会被拖慢，
    而账号本人永远进得来。
  - stdio 本地调用（`run_stdio.py`）不带来源，等价于按账号维度计数（单机无他人锁号问题）。
- **单来源总闸**（`login_source_fail_limit` 默认 20，`login_source_window` 默认 300 秒）：
  同一来源在窗口内的失败**总数**（不分账号）达到阈值后，该来源整段窗口内一律 429 ——
  挡住"一个来源换着账号扫口令"；只影响这个来源，与账号无关，窗口过后自动恢复。
- **第二道准入（部署现状 = 关）**：本项目实机 `require_token=false`（2026-10-13
  用户口径：**不开令牌**，只留账号密码一道准入）。此时客户端多带的
  `X-StuLink-Token` 会被直接忽略（无害）。
  要启用：把该键改 `true` 并重启服务，客户端加 `X-StuLink-Token: <config.json 里的 token>`
  （或 `Authorization: Bearer <token>`）—— 相关端到端用例在 `smoke_auth.py` 里已有
  （无令牌 401 / 带令牌通过 / 错令牌 401），启用前先跑一遍自测。
  令牌只在 `config.json` 与启动横幅（首尾片段）出现，不进日志、不进文档。
  局域网暴露且未开第二道准入时，服务启动横幅会打印一行安全提示（含两种收紧方式）。
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
| ④ **写入范围**（2026-10-11） | `swap_entry_scope / swap_review_scope / exam_write_scope / student_write_scope` | 网页端写入路由的 `swap_entry_authorizer` / `can_review` / `assert_exam_visible` |

铁律：**AI 通过 MCP 能看到的范围 = 该账号在网页端能看到的范围；能写的范围 = 网页端能写的范围**。新增工具时
必须同时声明 `perm=` 并选用与网页端对应页面相同的范围函数（详见
`聊天总结/20261011_批次总结_MCP二轮审计整改_v1.19.5.0.md` 的两次口径修正）。

`guards.perm()` 的 `key` 支持传元组（命中任一即通过），用于"多级审批"这类同一入口
对应不同权限点的场景（如调课审批：一级 `academic.swap_approve`、终审 `academic.timetable`）。

---

## 二之二、写工具清单与权限（2026-10-11 新增）

写工具统一经 `toolkit.db_write()` 执行，受 `allow_write` 总开关约束，且**在 `perm`
之外必过写入范围守卫**（§二 ④）。全部写操作都写系统操作日志（`module` 为对应模块）。

### 调课（tools/swap.py）

| 工具 | 权限点 | 额外范围校验 | 说明 |
|---|---|---|---|
| `list_my_swaps` / `list_swap_entries` / `get_swap_available_slots` / `get_swap_detail` | `academic.swap` | 条目按 `swap_entry_scope` 过滤 | 只读 |
| `list_pending_swaps` | `academic.swap_approve` 或 `academic.timetable` | `swap_reviewable` + 审批级/年级收窄 | 只读 |
| `submit_swap_request` | `academic.swap` | `swap_entry_scope`（本人任教/负责班级） | 仅生成 `pending`，**不直接改课表** |
| `submit_bulk_swap` | **`academic.swap_bulk`** | 逐条 `swap_entry_scope`，任一条越权整批拒绝 | 统一调课（普通教师不具备） |
| `cancel_swap_request` | `academic.swap` | 申请人本人，带 `batch_id` 整批撤销 | 仅 `pending` 可撤 |
| `review_swap` | `academic.swap_approve` 或 `academic.timetable` | `swap_review_scope`（轮到本级 + 本年级） | 审批链逐级推进；驳回必填理由 |
| `execute_swap` | `academic.timetable` | 仅 `approved`；批次整批执行 | **真正改课表** |
| `revoke_swap_execution` | `academic.timetable` | 仅 `executed`；批次整批回退 | 回退课表，记录回到「已通过」 |

### 查课（tools/inspection.py）

| 工具 | 权限点 | 额外范围校验 | 说明 |
|---|---|---|---|
| `add_inspection_record` | `academic.edit` | 年级+班级+教师三重 | 单条录入 |
| `batch_add_inspection_records` | `academic.edit` | 同上，无效行跳过 | 返回 skipped 计数 |
| `update_inspection_record` / `delete_inspection_record` | `academic.edit` | 记录须在可见范围内 | 只改传入字段 |
| `mark_inspection_cells` | `academic.edit` | 同上，越权格跳过 | 同格覆盖；`result` 空=撤销 |
| `notify_inspection_summary` | `academic.edit` | — | 推送给课表管理员 |

> 配套改动：查课原本**没有 service 层**（写逻辑 100% 内联在 `inspection.py`），
> 2026-10-11 抽出 `inspection_service.py`，5 条网页端路由改为调用它，MCP 与网页端
> 共用同一套校验（年级→班级→教师三重，缺一即越权）。

### 表单收集（tools/forms.py）

| 工具 | 权限点 | 说明 |
|---|---|---|
| `create_form` / `update_form` | `academic.edit` | 建/改模板（仅草稿可改；改题目会整体重建） |
| `set_form_status` | `academic.edit` | publish / close / delete（仅草稿可删） |
| `start_form_round` / `manage_form_round` | `academic.edit` | 轮次：发起 / 结束 / 重开 / 调整时间窗 |
| `review_form_submission` | `academic.edit` | 审核；**通过后自动计入教师业绩库**（幂等） |
| `submit_form` | **`academic.view`** | 填写端提交（与网页端一致，不是 edit） |
| `remind_form_submitters` | `academic.edit` | 催交；已有未读催交通知者自动跳过 |

> ⚠️ 权限点**必须以路由装饰器为准**：`permission_map.py` 里的 `academic.forms` /
> `academic.forms_view` 是死键，表单路由从未使用。

### 业绩库（tools/achievements.py）

| 工具 | 权限点 | 说明 |
|---|---|---|
| `add_achievement` | `academic.edit` | 教务录入（直接 approved） |
| `review_achievement` | `academic.edit` | 审核；驳回必填理由 |
| `update_achievement` | `academic.edit` | 编辑（只改传入字段） |
| `delete_achievement` | `academic.edit` | 删除，附件一并清理 |
| `set_achievement_tags` | `academic.edit` | 标签（逗号/空格分隔，去重） |

> 配套改动：业绩主流程原内联在路由，2026-10-11 抽出 `add/review/edit/delete_achievement`
> 到 `achievement_service.py`。同时修正了网页端 `achievements_edit` 中"只能改自己"的
> **死代码**（原判定因装饰器已强制 `academic.edit` 而恒为真，分支永远走不到）。
> 刻意未开放附件上传（MCP 无文件通道）。

### 学期课表（tools/schedule_write.py，第 3 批）

权限统一 `academic.timetable`。

| 工具 | 额外范围校验 | 说明 |
|---|---|---|
| `create_term` / `update_term` / `activate_term` / `set_current_term` / `archive_term` / `delete_term` / `update_term_dates` / `copy_term` | 无（**校级操作**） | 学期是校级概念，网页端也无年级范围校验，MCP 保持一致 |
| `add_schedule_entry` / `update_schedule_entry` / `delete_schedule_entry` | `schedule_class_scope`（`academic_class_is_visible`） | 课表条目 CRUD；原班级与目标班级都须在范围内 |
| `save_schedule_periods` / `reset_schedule_periods` / `manage_global_periods` | 无（校级操作） | 节次与全局作息 |

> ⚠️ **必须自己补的一关：归档学期只读**。服务层 `add_entry/edit_entry/delete_entry/
> save_periods` 都不检查学期状态，该限制只在网页端路由层（`_readonly_guard`）。
> MCP 直接调服务层，故用 `guards.schedule_writable(ts)` 补上——已用归档学期实测被拒。
>
> ⚠️ **两套范围口径不可混用**：调课走 `swap_entry_authorizer`（"本人任教的课"无条件
> 放行，更宽）；直接改课表走 `academic_class_is_visible`（纯班级范围）。MCP 分别用
> `swap_entry_scope` 与 `schedule_class_scope`，避免越权缝隙。
>
> **刻意未开放**：smart-import（原样课表导入，需上传 Excel，MCP 无文件通道）；
> 旧 `timetable.py` 的路由是死代码（引用了不存在的模型），未基于它实现。

### 宿舍（tools/dormitory_write.py，第 2 批）

| 工具 | 权限点 | 额外范围校验 | 说明 |
|---|---|---|---|
| `assign_bed` / `unassign_bed` / `move_bed` / `swap_beds` | `dormitory.beds` | `student_in_dorm_scope`（学生须在宿舍管理范围） | 含性别、跨班、毕业年级校验，原子 UPDATE 防并发 |
| `clear_class_beds` | `dormitory.beds` | `class_in_scope` | 清空本班床位 |
| `create_room` / `update_room` / `delete_room` / `assign_room_class` | `dormitory.manage` | `class_in_scope`（网页端本身无校验，MCP 补上） | 建/改/删/分配班级；有人入住不可改性别容量、不可删 |

> 宿舍口径（`app/utils/dormitory_scope.py`）**不等价于**成绩口径，写工具必须用前者。

### 德育积分（tools/points.py，第 2 批）

| 工具 | 权限点 | 说明 |
|---|---|---|
| `add_point_record` / `update_point_record` / `delete_point_record` | `points.edit` | 分值整数、非 0、\|值\|≤100；事由必填 |
| `import_point_records` | `points.import` | 复用网页端 `sanitize_rows`；越权行**跳过**（非整批拒绝）；补了网页端缺失的整批 rollback |

### 学生（tools/students.py，第 2 批）

| 工具 | 权限点 | 说明 |
|---|---|---|
| `update_student` | `students.edit` | 只改 phone1/phone2/选科/学籍/备注；**年级班级不可改** |
| `transfer_student` | `students.transfer` | 调班；**先写变迁日志再提交业务**，日志失败即中止 |

> 范围校验复刻网页端**内联角色判定**（homeroom_teacher 限本班、grade_leader 限本年级、
> 其余拒绝、admin 放行），而非读侧的 `apply_student_scope`——以"与网页端一致"为准。
> 刻意未开放删除（会解绑床位与住宿档案，且网页端用 `students.import` 权限点，易误授）。

### 通知与备课组长（tools/notifications.py、tools/duty.py，第 2 批）

| 工具 | 权限点 | 说明 |
|---|---|---|
| `publish_notification` | `workbench.notifications` 或 `system.settings` | **复刻 4 条路由层限制**（见下） |
| `delete_my_notification` | 同上 | 只能删自己发布的（管理员除外） |
| `save_subject_leader` / `delete_subject_leader` | `academic.edit` | 按 学年+学期+年级+学科 幂等 |

> ⚠️ `create_notification` 服务层**没有任何限制**，以下 4 条全在路由层，MCP 必须自行复刻：
> ① `all`/`role` 群发需 `system.settings`；② 链接只允许 http/https（防存储型 XSS）；
> ③ 长度 clamp（标题 100 / 正文 5000 / 链接 500）；④ 写权限闸。

### 成绩（tools/scores.py）

| 工具 | 权限点 | 额外范围校验 | 说明 |
|---|---|---|---|
| `update_score` | `grades.edit` | `exam_write_scope`（考试年级） | 改分后联动总分行；`score` 留空=按缺考删除 |
| `import_scores` | `grades.import` | `exam_write_scope` + 逐条学号范围 | 整批事务（全成全败），自动重算排名 |
| `recalc_exam_ranks` | `grades.edit` | `exam_write_scope` | 重算班排/方向排/进退步并失效分析缓存 |

**刻意未开放**：删除整场考试（网页端需账号+密码二次确认）、删除全部成绩（模式 B
虽可清空重导，但要求显式传 `mode='B'`）。

### 写工具的三条硬约束

1. **申请 ≠ 执行**：调课必须先审批、再执行，AI 无法一步改课表；执行错了可用
   `revoke_swap_execution` 回退。
2. **整批原子**：统一调课与成绩录入都是"任一条不合格则整批拒绝/回滚"，杜绝部分写入。
3. **后置动作齐全**：改分刷新总分行、考试状态置位、失效分析缓存——与网页端一致，
   否则分析页会读到 900 秒旧缓存。

---

## 二之三、第二轮审计结论与修复（2026-10-12）

对 MCP 全部 **96 个工具（64 写 / 32 读）** 做了独立复核，方法：AST 静态扫描所有
`db_write/db_call` 的 `perm` 声明 + 逐工具与网页端路由/服务层比对 + 运行验证。

### 基线（已核实无误）

- **权限声明零遗漏**：64 个写工具、32 个读工具**全部**声明了 `perm`。
  （`toolkit._dispatch` 的 `perm` 默认为 `None`，漏写即"登录即可写"，故此项为红线。）
- swap / inspection / points / students / achievements / duty / schedule_write 的
  范围守卫与网页端同源或 1:1 复刻，未发现 MCP 更宽松。
- analytics / exams / basic / attendance / dormitory 读工具的数据范围收敛与网页端一致。
- `scores.import_scores` 整批事务真正全成全败（service 不 commit，MCP 单次提交）。

### 发现并修复的 6 处问题

| # | 严重度 | 问题 | 修复 |
|---|---|---|---|
| 1 | **高** | `update_room` 只校验房间**原**归属范围，改**新** grade/class 时零校验 → 可绕过 `assign_room_class` 的守卫；且「已分班但空」的房间可改容量（网页端禁止）、改容量不调 `_adjust_beds` → 容量与床位行不一致 | 补新归属范围校验；改为「已分班 **或** 已入住」禁止改性别/容量（对齐 `rooms.py:167`）；新增 `_adjust_beds` 同步床位行 |
| 2 | **中** | `import_scores` 未校验「学生年级 = 考试年级」→ 可把别年级学生成绩写进本场考试（网页端导入解析器有此校验） | 逐条校验 `student.grade == exam.grade`，不一致整批拒绝 |
| 3 | **中** | `submit_form` 硬编码 `submitter_type='teacher'` 且不传 grade/class/subject → 学生账号可提交「仅面向教师」的表单；定向表单因缺字段被误判 | 按角色判定 `submitter_type`，补全 grade/class/subject（对齐 `forms.py:769-778`） |
| 4 | **低** | `get_student_accommodation` 用 `students.view`（网页端住宿页要 `dormitory.view`）+ 成绩口径 `student_in_scope` | 权限改 `dormitory.view`；范围改 `guards.student_list_scope`（= `apply_student_scope`，与网页端同源） |
| 5 | **中** | `clear_class_beds` 缺毕业年级校验、缺床位变迁留痕（网页端两者都有） | 补 `grade in graduated → 拒绝`；逐床写 `record_assignment('clear', ...)` |
| 6 | **低** | `publish_notification` 缺 `workbench.notifications_view` 闸（网页端是「装饰器 + `_can_publish`」两道） | `_can_publish` 补该权限校验 |

另加一处稳健性改进：`toolkit._execute` 异常路径**显式 `rollback`**，不再只依赖
app context 退出时回收会话。

### 顺带修复：网页端表单提交 500（非 MCP，但同批发现）

审计中发现 `app/modules/academic/routes/forms.py` 的 `form_submit` 用了
`current_user.name`，而 `User` 模型**没有 `name` 字段**（只有 `real_name`）→
`AttributeError` → 提交表单必 500。**该 bug 从未被回归测试覆盖**：原有用例
（FORM2ACH）直接调 `form_service.submit_form`，绕过了 HTTP 路由。

- 修复：`current_user.name` → `current_user.real_name`（带 `or username` 兜底）。
- 补测：新增 `FORMHTTP` 回归项走**真实路由**，断言不 500 且提交人姓名正确。
- 验证：该项 2/2 通过；全量回归 **302/302**（原 300 + 新增 2）。

### 刻意未改（与网页端口径一致，改了反而不一致）

- `execute_swap` 批次逐条 commit → 可能部分执行：网页端 `swap_execute` 同样逐条，
  且返回 `executed/failed` 可重试，属设计使然。
- `create_form(publish=true)` 两次内部 commit → 发布失败残留草稿：网页端同为两步。
- `points.import_point_records` 的 rollback：service 是「先全 add、最后 commit」，
  提交前异常时 rollback 有效，提交成功后无异常，无需"整批回滚"能力。

### 验证

- 静态扫描：写工具 64 / 读工具 32，`perm` 缺失 **0**。
- 修复运行验证 **4/4**：教师（仅 `students.view`）查住宿被拒；跨年级学生导入被整批拒绝；
  已分班房间改容量被拒；admin 正常路径未被误伤。
- 回归：MCP 自测 **40/40**、网页端回归 **300/300**。

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
| `login_fail_limit` | 5 | 同一来源对同一账号连续失败锁定阈值（0=不锁定）；**只锁来源、不锁账号** |
| `login_lock_seconds` | 300 | 锁定时长（秒） |
| `login_throttle_threshold` | 10 | 跨来源失败累计阈值：超过后每次尝试附加递增延迟（只延迟不拒绝） |
| `login_throttle_window` | 300 | 跨来源失败累计统计窗口（秒） |
| `login_throttle_max_delay` | 1.0 | 附加延迟上限（秒）；0=关闭 |
| `login_source_fail_limit` | 20 | 单来源失败总数上限（跨账号，超限该来源一律 429）；0=不限制 |
| `login_source_window` | 300 | 单来源失败总数的统计窗口（秒） |
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
| `tools/swap.py` | 2026-10-11 新增：调课读/写工具（申请、统一调课、审批、执行、撤销执行） |
| `tools/scores.py` | 2026-10-11 新增：成绩写工具（改分、批量录入、重算排名） |
| `tools/inspection.py` | 2026-10-11 新增：查课写工具（录入、批量、标记、通知） |
| `tools/forms.py` | 2026-10-11 新增：表单写工具（模板、轮次、审核、填写、催交） |
| `tools/achievements.py` | 2026-10-11 新增：业绩库写工具（录入、审核、编辑、删除、标签） |
| `tools/dormitory_write.py` | 2026-10-11 第 2 批：宿舍写工具（房间管理、床位分配/搬移/互换） |
| `tools/points.py` | 2026-10-11 第 2 批：德育积分写工具（录入、编辑、删除、批量导入） |
| `tools/students.py` | 2026-10-11 第 2 批：学生写工具（编辑、调班） |
| `tools/notifications.py` | 2026-10-11 第 2 批：通知写工具（发布、删除） |
| `tools/duty.py` | 2026-10-11 第 2 批：备课组长写工具（保存、删除） |
| `tools/schedule_write.py` | 2026-10-11 第 3 批：学期课表写工具（学期管理、条目 CRUD、节次配置） |
| `core/bootstrap.py` | app 单例；`load_user(uid)`（每请求）；`warm_up(bind_user)`（token 模式） |
| `run.py` | 组装 ASGI（`AccessGuard` 包 MCP）+ 启动横幅（按模式给出客户端示例） |
| `run_stdio.py` | stdio 模式启动认证（env 账号密码，失败拒绝启动） |
| `smoke_scores.py` + `smoke_auth.py` | 62 项自测（成绩权限矩阵 + account 模式端到端认证用例） |

---

## 七、安全边界与已知限制

1. **密码明文在客户端配置文件里**（与旧令牌同级）。风险由文件权限与"专用低权限账号"缓解；不支持 OAuth 是刻意取舍（本地/局域网单服务，OAuth 动态注册属过度设计）。
2. **未做会话绑定**：每个请求独立认证，同一 MCP session 可被不同账号的请求复用（各自按自身身份执行，不产生越权；但共享了传输层会话）。
3. **锁定按键隔离**：锁定按账号维度；攻击者遍历大量用户名时不会触发单账号锁定（本地服务场景可接受）。
4. **内存态**：凭据缓存与失败计数为进程内状态，重启即清空。
5. **写开关仍独立**：`allow_write=false` 时，即使认证通过的账号也不能调用写工具。
6. 传输层建议继续保持 `127.0.0.1` 绑定；若改为 `0.0.0.0`，请同时开启
   `require_token=true` 并为账号设置强密码。
7. **写工具放大了账号权限的后果**（2026-10-11）：AI 可代用户执行改分、调课、审批等
   写操作，一旦账号被盗或提示词被注入，影响直接落在生产数据上。缓解措施：
   - 给 AI 用的账号遵循**最小权限**（如只给 `grades.view` + `academic.swap`，不给
     `academic.timetable` / `grades.import`）；
   - `allow_write=false` 可一键关闭全部写工具（读不受影响）；
   - 全部写操作均写入系统操作日志（`module` 为 grades / academic），可事后追溯；
   - 高风险动作（删除整场考试）刻意未开放给 MCP。

---

## 八、验证

- 端到端自测：`mcp_server\.venv\Scripts\python.exe mcp_server\smoke_scores.py`
  → **通过 69 / 失败 0**（主仓库当前工具集 = 12 个只读成绩工具）。其中认证相关用例在
  `mcp_server/smoke_auth.py`：无凭据 401、错密码 401、正确凭据进入 MCP 并调用工具、
  `X-StuLink-User/Password` 方式、连续失败锁定 429（带 Retry-After）。
- 网页端回归：`py -3.12 tests/academic_regression.py` → **300 / 300**（第 1 批改动了
  查课/业绩/表单的共有代码，必须回归）。
- 写工具专项验证（2026-10-11，临时脚本验证后已删除）：
  - 第 0 批（调课+成绩）：受限账号 7 个写工具全部「权限不足」，admin 全部过权限层
    → 12/12；真实写入（录入落库、改分联动总分、非法分数被拒、调课不改课表、撤销）
    → 11/11；测试数据已按 id 精确删除并恢复排名。
  - 第 1 批（查课+表单+业绩）：19 个新工具全部注册；受限账号（仅 `academic.view`）
    13 个写工具全部「权限不足」，admin 7 个全部过权限层 → **21/21**。
  - 第 2 批（宿舍+德育+学生+通知+备课组长）：19 个新工具全部注册；受限账号
    16 个写工具被拒，admin 12 个全部过权限层；德育用越界学号验证范围拦截生效
    （`20250001` → 「不在你的德育管理范围内」）。验证中误删的 1 条积分记录已恢复。
  - 第 3 批（学期课表）：14 个新工具全部注册；受限账号 14 个写工具被拒，admin 8 个
    全部过权限层；**归档学期只读守卫实测生效**（写节次/加条目均返回「历史（已归档）
    学期课表为只读」）。真实写入端到端 9/9：建学期 → 加条目 → 冲突检测拦截 →
    改条目（落库校验）→ 存节次 → 删条目 → 删学期，并校验学期与条目已从库中完全移除。
- stdio：无凭据拒绝启动（rc=2）、错密码拒绝启动（rc=3）、正确凭据就绪。
- 修改本机制后必须**重启 MCP 进程**生效。

## 九、迁移与回滚

- **从旧版（只有令牌）升级**：`config.json` 已是 `auth_mode=account`，客户端配置
  需从 `Authorization: Bearer <token>` 改为账号密码头（见第四节）。未更新配置的
  客户端会收到 401（响应体含配置提示）。
- **临时回滚**：把 `config.json` 的 `auth_mode` 改成 `token` 并重启，即恢复旧行为
  （静态令牌 + `bind_user` 身份），无需改代码。

# StuLink MCP 服务（成绩分析）· 权限设计

> 面向开发与运维：说明本 MCP 服务的定位、三条授权规则的形式化定义、判定矩阵、
> 工具清单与范围过滤细则、启动接入与验证方式。
> 服务总览、依赖、部署与运维见 [MCP 服务技术文档](MCP服务技术文档.md)；
> 接入认证与客户端配置见 [AI 工具安装与配置指南](AI工具安装与配置指南.md)。

---

## 一、定位

**本 MCP 服务已收敛为成绩分析专用、且只读**：只暴露 12 个成绩查询工具，
不含任何写入能力。

| 项 | 内容 |
|---|---|
| 入口 | `mcp_server/run.py`（HTTP）／`mcp_server/start_mcp.bat`；另有 stdio 入口 `run_stdio.py` |
| 工具 | 12 个，**全部只读**（无写工具，`allow_write` 实际不生效） |
| 端口 / 端点 | 由 `config.json` 的 `port` / `path` 决定（内置默认 `8765` / `/mcp`） |
| 认证 | `core/auth.py`：每请求校验 StuLink 账号密码（`auth_mode=account`） |
| 配置 | `mcp_server/config.json`（令牌、白名单、行数上限、缓存 TTL） |
| 权限 | `core/score_guard.py`：三条规则细粒度裁决，**仅授予读取成绩的权限** |

---

## 二、三条授权规则

需求原文拆解为可判定的形式（`G` 年级、`C` 班级、`S` 学科）：

| 编号 | 角色 | 授予范围 | 判定条件 |
|---|---|---|---|
| **R1** | 任课教师 | **年级 × 学科**（2026-10-15） | `TeacherSubjectLink(active)` 中存在 `(G, S)`：<br>① 可查看 **G 年级全部班级** 的 **S 科** 成绩（不再限本人的那几个班）；<br>② 并附带每名学生的**总分年级名次**（`rank_dir` 总分行）——**只给名次、不给总分分数**；<br>③ 其它学科、总分分数、逐生总分名单仍不开放 |
| **R2** | 班主任 | 本班 × 全部学科 | `UserClassLink` 中存在 `(G, C)`；一班最多 3 名班主任，无主副之分 |
| **R3** | 年级长及以上 | 本年级 × 全部班级 × 全部学科 | `role='grade_leader'` 且 `User.grade=G`；校级（权限组 `scope_type='school'`）与管理员为**全校** |
| **R4** | 显式授权（补充） | 年级 / 年级+班级白名单 | 权限管理页"用户级数据范围"`UserDataScope`；该表**有记录时按网页端口径优先且收窄** |

**取并集**：R1/R2/R3 可叠加（既任课又当班主任则两类范围都可见）。
**默认拒绝**：不属于以上任何来源 → 范围为空集，一律返回"权限不足"，**不回落全校**。

> R4 是网页端既有能力（管理员显式授予某账号可见某年级），保留以维持
> "AI 可见范围 ＝ 网页端该账号可见范围"这一项目铁律；若不需要，可不配置该表。

---

## 三、权限解析（`score_guard.resolve(user)`）

按顺序解析，命中即返回：

1. `role == 'admin'` → **全校**；
2. 权限组 `scope_type == 'school'` → **全校**；
3. `UserDataScope` 有记录 → 仅按授权年级/班级（某年级若存在"指定班级"记录，
   该年级按班级白名单处理，与网页端 `get_user_scope_classes` 一致）；
4. 否则取并集：
   - R3：`role=='grade_leader'` → `User.grade`；权限组 `scope_type=='grade'` → `User.grade`；
   - R2：`UserClassLink` 全部记录；`role=='homeroom_teacher'` 另按 `User.grade/class_name` 兜底；
   - R1：`TeacherSubjectLink(user_id, active=True)` 的 `(G, S)` **二元组**（年级 × 学科）。

解析结果是 `ScoreScope` 值对象，提供四类判定：

| 方法 | 语义 |
|---|---|
| `visible_grades()` | 可见年级集合；`None` = 全校 |
| `can_view_grade(G)` | 是否可访问该年级 |
| `grade_subjects(G)` | 该年级可见学科；`None` = 全部学科，空集 = 无授权 |
| `visible_classes(G)` | 该年级可见班级；`None` = 该年级全部班级（**任课教师任教年级也是 None**，学科另行收窄） |
| `can_view_class(G, C)` | 是否可访问该班成绩（任课教师＝任教年级内的任意班） |
| `class_subjects(G, C)` | 班内可见学科；`None` = 全部学科；任课教师＝所教科（与他班同解） |
| `can_view_subject(G, C, S)` | 是否可访问该班该科成绩 |
| `class_total_visible(G, C)` | 是否可见该班"**总分口径**"（总分、班均分、排名、分层） |
| `total_rank_visible(G, C)` | 是否可见该班学生的**总分年级名次**（任课教师＝✅，但总分分数仍不给） |
| `total_visible_classes(G, 班级清单?)` | **逐生总分数据的唯一收口**：`None` = 全部班级（年级级授权）；集合 = 仅这些班；任课教师＝空集 |

### 判定矩阵（`smoke_scores.py` 逐条实测）

| 场景 | 任课教师(R1) | 班主任(R2) | 年级长(R3) | 管理员/校级 |
|---|---|---|---|---|
| 本年级 · 本班 | ✅ | ✅ | ✅ | ✅ |
| 本年级 · 他班（**限所教科**） | ✅（2026-10-15） | ❌ | ✅ | ✅ |
| 他年级 | ❌ | ❌ | ❌ | ✅ |
| 本班 · 所教学科 | ✅ | ✅ | ✅ | ✅ |
| 他班 · 所教学科 | ✅（2026-10-15） | ❌ | ✅ | ✅ |
| 本班 · 非所教学科 | ❌ | ✅ | ✅ | ✅ |
| 本班总分**分数** | ❌ | ✅ | ✅ | ✅ |
| 本班学生**总分年级名次**（rank_dir） | ✅（仅名次） | ✅ | ✅ | ✅ |
| 逐生总分名单（成绩单 / 临界生） | ❌（空集 fail-closed） | ✅ 本班 | ✅ | ✅ |

> "总分"不属于任何单科，因此**不对任课教师开放**：其班级结果中 `avg/std/max/min/layers`
> 置空并附 `total_hidden: true` 与说明；学生成绩中 `score/rank_*` 被移除。
> 这是 R1"仅授予读取成绩（所教学科）"的严格落地。
>
> **2026-10-15 修正（考试级概览必须按班判定）**：`get_exam_overview` 原先用
> `grade_subjects(G)` 把"学科可见范围"压成**年级级单值**——于是"班主任 + 化学任课"的账号
> （如 `zhouyihang`：2025级07班班主任 + 03/07/10班化学）会被显示成
> 「可见学科＝化学、总分口径已隐藏」，与同一账号在班级级
> （`get_class_score_summary`）本就能看到的"本班全科 + 本班总分"自相矛盾。
> 现改为**逐班判定 → 再聚合**：
> · 学科：逐班 `class_subjects()` 取并集，任一班全科则该场即"全部学科"，并附
>   `class_scope` 逐班明细（防 AI 把 A 班的可见学科套到 B 班）；
> · 总分：只统计 `class_total_visible` 为真的班级；部分授权时给
>   `total_classes` / `total_hidden_classes` / `total_note`，**一个班都没有时才整体隐藏**
>   （纯任课教师口径不变，仍为隐藏）。
> 塑形代码只有一份：`app/modules/grades/services/score_overview.py`，Web 端 AI
> （`ai_tools._t_exam_overview`）与 MCP（`tools/scores.py::_exam_overview`）共用。

> **2026-10-15 下午·用户裁决（两层口径，本节最重要）**：用户原话——
> 「MCP 在分析班级成绩的时候应该加上年级成绩分析这个维度……**年级成绩分析的结果是可以
> 公开的**。不能公开的，**仅仅只有学生个人的成绩**（除非是他们自己班的）」。
> 于是权限从"一刀切按班级收窄"改为**两层**：
>
> | 层 | 内容 | 可见性 |
> |---|---|---|
> | **聚合层** | 年级各班汇总（人数/总分均分/标准差/最高/最低/分层计数/各科均分/**班级位次**）、年级基准、年级学科分析、分数段分布、分层上线率、班级对比 | **公开**：能看该考试的账号都能看**全年级**（班主任/任课教师不再只看自己那几个班） |
> | **逐生层** | 学生个人成绩、班级逐生明细、逐生总分、临界生名单、成绩单 | **收敛**：任课教师＝**所教年级 × 所教学科**（该年级**全部班**都可下钻，2026-10-15）+ **总分年级名次**（只名次不给分数）；班主任＝本班全科；他科 / 逐生总分一律拒绝 |
>
> · 聚合塑形唯一实现：`app/modules/grades/services/grade_context.py`
>   （Web 端 AI `ai_tools.py` 与 MCP `tools/scores.py`/`score_analysis.py` 共用）；
>   班级级总分聚合**不再隐藏**（旧的"总分口径不对任课教师开放"只保留在逐生层）。
> · 每个班级行都带 `detail_visible`（该班能否下钻逐生）与 `class_rank`（位次），
>   `get_class_score_detail` 另附 `grade_context`（本班位次 + 年级基准 + 各班横向），
>   保证 AI"分析某班"时手里有参照系，不会只报一个孤立数字。
> · 原因：此前 MCP 把 `get_class_score_summary` 按 `visible_classes` 收窄，而网页端
>   「年级分析」页早已把全年级各班摆给班主任/任课教师（`tab_service.grade_tab` 不过滤）
>   —— 两边不一致，AI 拿不到年级对照，属铁律（AI 可见范围 = 网页端可见范围）违反。

---

## 四、工具清单（12 个 · 全部只读）

| 工具 | 作用 | 范围过滤 |
|---|---|---|
| `list_my_score_scope` | 查看自己的可见年级/班级/学科与授权原因 | 仅返回范围描述，不含成绩 |
| `list_exams` | 可见考试清单 | 按 `visible_grades()` 收窄 |
| `list_classes` | 可见班级清单（口径＝**考试当时的班级**，非当前花名册；剔除"不分班/已转出/离校"） | 按 `can_view_class` 过滤，附可见学科与是否可见总分；可传 `exam_id` 只看某场，否则汇总可见考试内出现过的班级 |
| `get_exam_overview` | 考试概览（人数、总分均分/最高/最低、班级数、导入情况） | 仅统计可见班级；**学科与总分都按班判定后聚合**（`class_subjects` / `class_total_visible`）：任一班给了全部学科即按"全部学科"展示并附 `class_scope` 逐班明细；总分只统计已授权班级，部分隐藏时给 `total_classes` / `total_hidden_classes` |
| `get_class_score_summary` | **年级各班**成绩汇总（人数/总分均分/标准差/最高/最低/分层/各科均分/班级位次）＋ 年级基准 | **聚合公开**：返回本场考试全部班级（不再按 `visible_classes` 收窄）；每行 `detail_visible` 指示能否下钻逐生；`class_name` 可留空 |
| `get_class_score_detail` | 某场某班的**逐生成绩明细**（每生各科分数；班主任及以上含总分与排名；任课教师＝所教科 + 每生的**总分年级名次** `rank_dir`）＋ `grade_context` 年级对照 | 逐生收敛：`class_name` 必填且校验 `can_view_class`（任课教师可传任教年级的**任意班**）；科目按 `class_subjects`；总分按 `class_total_visible`（不给则回落给名次）；可传 `subject` 只看单科 |
| `get_student_scores` | 学生成绩（或历次考试清单） | 学生须在可见班级内（任课教师＝任教年级任意班）；科目按 `class_subjects`；总分按 `class_total_visible`，否则只给 `rank_dir`（总分年级名次） |
| `get_student_score_trend` | 学生单科历次走势 | 同上（指定科目须在可见学科内） |
| `get_subject_analysis` | 年级学科分析（各科人数/均分/及格率/优秀率） | **聚合公开**：返回全部学科（不再按 `grade_subjects` 收窄） |
| `get_exam_score_segments` | 分数段/名次段（各段人数与合计） | **聚合公开**：全年级与指定班级都放开（班级只需在本场考试内存在） |
| `get_layer_online_stats` | 分层上线统计（各层上线人数/上线率） | **聚合公开**：任何能看该考试的账号都可查（不再仅限年级长及以上） |
| `compare_classes` | 班级对比（人数/总分均分/各科均分/位次）＋ 年级基准 | **聚合公开**：本场考试内的任意班都可对比；仅"本场不存在的班"会列入 `unknown_classes` |

**功能权限层**：所有工具都以 `toolkit.db_call(..., perm='grades.view')` 注册，
即同时要求"网页端成绩查看权限点"与"数据范围"。三层缺一不可（权限点 / 数据范围 / 维度口径）。

---

## 五、与网页端口径的对照

| 维度 | 网页端 | 本服务 | 说明 |
|---|---|---|---|
| 管理员 / 校级 | 全校 | 全校 | 一致 |
| 年级长 | `User.grade` 本年级 | 同 | 一致 |
| 班主任 | `UserClassLink` 本班 | 同 | 一致 |
| 任课教师 · 年级 | 任教年级（`TeacherSubjectLink.grade`） | 同 | 一致 |
| 任课教师 · 班级 | 网页**逐生**视图：工作台明细 / 成绩复核可选本人**任教年级的全部班**（2026-10-15 二次裁决）；**聚合**视图（年级分析）不过滤 | 同（两层口径，见第三节 2026-10-15 下午裁决 + 晚上"年级×学科"裁决） | 聚合公开、逐生按"年级×学科"收敛 |
| 任课教师 · 学科 | 教师维度**逐生**分析只展示本人映射的 (班级, 学科)；年级学科总表不过滤 | 同 | 一致 |
| 用户级数据范围 | 优先且收窄 | 同 | 一致 |
| 无有效范围 | 空集（不回落全校） | 同 | 一致 |

### 5.1 班级口径：**成绩跟"考试当时"，学生归属跟"当前花名册"**

这是两件不同的事，别混：

| 用途 | 口径 | 数据来源 | 理由 |
|---|---|---|---|
| 班级清单（`list_classes`）、班级成绩聚合（`get_class_score_summary`、`compare_classes`、分数段） | **考试当时的班级** | `exam_score.class_name` | 花名册的班级随时会变（转班 / 转出 / 不分班），而成绩分析跟的是考试当时所在的班 |
| 学生成绩可见性（`get_student_scores`、`get_student_score_trend`） | **学生当前班级** | `student.class_name`（`Student` 名册） | 学生可能换班主任；"谁能看这个学生的成绩"按**当前归属**判定 |

- 非行政班（`不分班` / `已转出` / `离校`，班级字典中的特殊项）**不参与成绩统计**，一律剔除
  （当前成绩表里实际只有 `01班`~`10班`，此过滤为防御性保留）。
- 学生**转出/归档**后，其历史成绩进入历史成绩归档，原班主任/任课教师在本服务中查不到，
  属预期行为（与网页端 `student_in_scope` 一致），不额外处理。

---

## 六、启动与接入

```bat
:: 启动（默认监听 127.0.0.1:8765，端点 /mcp）
mcp_server\.venv\Scripts\python.exe mcp_server\run.py
:: 或双击 mcp_server\start_mcp.bat
```

监听地址 / 端口 / 端点由 `config.json` 决定，也可用环境变量覆盖：
`STULINK_MCP_HOST`、`STULINK_MCP_PORT`、`STULINK_MCP_PATH`。

客户端配置（凭据头，URL 端口按实际填写）：

```json
{
  "mcpServers": {
    "stulink": {
      "url": "http://<监听地址>:8765/mcp",
      "headers": {
        "X-StuLink-User": "<StuLink 账号>",
        "X-StuLink-Password": "<账号密码>"
      }
    }
  }
}
```

---

## 七、验证

```bat
mcp_server\.venv\Scripts\python.exe mcp_server\smoke_scores.py
:: 期望：通过 82 / 失败 0（2026-10-15 两层口径调整后）
```

覆盖内容：

1. 工具集注册：恰好 12 个，且不含任何写入类工具；班级清单 `list_classes` 取自考试成绩
   （考试当时的班级），不把"不分班/已转出/离校"当班级；
2. **权限引擎矩阵**（纯逻辑）：R1/R2/R3/R0 与"无范围"逐条断言；
3. **解析结果**：年级长身份 → 本年级整年级；无范围身份 → 空集（不回落全校）；
4. **真实账号端到端**（2026-10-15 两层口径）：
   - 管理员：全校可见，各工具正常取数；
   - 班主任：本班逐生可查（含总分）、**他班汇总可查（聚合公开）**、年级学科分析/分数段可查、
     全年级各班汇总带 `grade_baseline` 与 `class_rank`、他班逐生/他班学生仍被拒；
   - 任课教师：**任教年级全部班 × 所教科的逐生明细可查（2026-10-15 R1 年级×学科）**、
     班级汇总含全部学科与总分聚合（聚合公开）、**全年级各班 + 年级基准 + 位次**、
     逐生明细附 `grade_context` 且仍只含所教学科、**每行附 `rank_dir`（总分年级名次，
     无 `total_score`）**；他科与他班非所教科仍被拒、逐生总分名单（成绩单/临界生）仍收口；
     年级级分数段与分层统计可查、含他班的对比也可用；
   - 无 `grades.view` 的账号：功能权限层拒绝。
5. **接入认证端到端**（`smoke_auth.py`，account 模式）：无凭据 401、错误密码 401、
   正确凭据可进入并调用工具（工具数 11）、`X-StuLink-User/Password` 头部方式、
   连续失败触发 429 锁定。

---

## 八、实现结构与边界

| 路径 | 职责 |
|---|---|
| `mcp_server/core/score_guard.py` | 权限引擎：`ScoreScope` + `resolve()` + 三个 `require_*` 校验（薄转发到 app，见下） |
| `app/modules/grades/services/score_guard.py` | 权限引擎**唯一实现**（Web 端 AI 与 MCP 共用） |
| `app/modules/grades/services/grade_context.py` | **年级维度聚合塑形**：各班汇总 + 年级基准 + 班级位次 + 可下钻标记（两层口径的"聚合层"唯一实现） |
| `app/modules/grades/services/score_overview.py` | 考试级概览塑形（考试概览工具唯一实现） |
| `mcp_server/tools/scores.py` | 成绩查询工具（8 个：考试/班级/学生）与范围过滤细则 |
| `mcp_server/tools/score_analysis.py` | 成绩分析工具（4 个：学科/分数段/分层/对比） |
| `mcp_server/tools/_score_common.py` | 共享常量与响应塑形 helper（考试/范围校验） |
| `mcp_server/tools/__init__.py` | 工具注册总入口（`scores` + `score_analysis`） |
| `mcp_server/run.py` / `run_stdio.py` | 服务入口（HTTP / stdio；认证 + 启动横幅） |
| `mcp_server/smoke_scores.py` | 88 项自测（权限矩阵 + 年级×学科 + 总分名次 + 真实账号端到端） |
| `mcp_server/smoke_auth.py` | 接入认证端到端用例（401 / 429） |
| `mcp_server/start_mcp.bat` | 一键启动 |

已知取舍与边界：

1. **只读**：本服务不提供任何写入路径；成绩的录入/修改/重算仍走网页端。
2. **历史任课快照**：本服务按**当前** `TeacherSubjectLink` 判定任课范围（与网页端
   成绩模块的可见性判定一致）；历史场次的任课归属另由 `ExamTeacherLink` 快照承载，
   用于"教师维度分析"，不用于授权。
3. **学籍维度**：学生可见性按**学生当前班级**判定（成绩是班级口径数据），
   与网页端成绩模块一致；历史考试中的"考试当时班级"由 `ExamScore` 快照承载。
4. **总分口径（2026-10-15 已分层，晚间再判一次名次）**：
   - **聚合层不再隐藏**：班均分/最高/最低/分层人数/位次属"年级成绩分析结果"，公开
     （用户裁决：不公开的只有学生个人成绩）；
   - **逐生层的总分分数仍收敛**：学生本人的总分（`total_score`）按 `ScoreScope.class_total_visible`
     判定（任课教师只看所教学科）；临界生名单、成绩单同理；
   - **但总分"名次"单独放开**（2026-10-15 晚间用户裁决："还有一个现在公开的是这个学生的
     年级的名次……是只暴露名次，不暴露分数"）：任课教师拿到的是 `rank_dir`（该生总分在
     本年级 / 本选科方向内的名次），由 `ScoreScope.total_rank_visible` 判定；
   - **收口必须是 `total_visible_classes()`**：R1 放宽成"年级 × 学科"后，`visible_classes(G)`
     对该年级也返回 `None`，沿用 `visible_classes() is None → 不限班` 的旧写法会把
     逐生总分（成绩单 / 临界生）连带放开，属越权。
   - 若要收紧聚合层，改 `grade_context.class_rows` 的调用侧（`total_visible` 标记已随行下发）。
5. **审计**：与通用服务一致，调用经 `core/audit.py` 写入系统操作日志（`module='mcp'`）；
   只读是否留痕由 `audit_reads` 控制。

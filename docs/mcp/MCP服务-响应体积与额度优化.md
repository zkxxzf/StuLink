# MCP 服务：响应体积与额度优化（2026-10-21）

> 用户反馈："用了一次 MCP，7 天额度全没了"。本批次先用探针**量化**，
> 再按数据改，改完复测。结论：**同样 12 个工具各调一次，token 从约
> 18270 降到约 4340（-76%）**，且功能不减（多了限时练）。

---

## 一、改造前的实测（探针 `_probe_mcp_payload.py`）

| 工具 | 字符 | ≈token |
|---|---:|---:|
| `get_class_score_detail` | 24674 | 6669 |
| `get_class_score_summary` | 13351 | 3470 |
| `get_exam_score_segments` | 7380 | 1896 |
| `list_exams` | 5592 | 1511 |
| `compare_classes` | 4388 | 1138 |
| `get_layer_online_stats` | 2830 | 788 |
| `get_subject_analysis` | 2174 | 563 |
| `get_student_score_trend` | 1922 | 512 |
| `list_classes` | 1907 | 546 |
| `get_exam_overview` | 1406 | 414 |
| `get_student_scores` | 1185 | 314 |
| **合计（+ 系统提示 327）** | — | **18270** |

钱主要花在三处：**缩进空白**（`indent=2`）、**`"key": null` 占位**、
**每行重复键名**（逐生明细 50 行 × 9 科的键名要付 450 次）。

---

## 二、四项措施（都在 `core/respond.py` 与 `core/toolkit.py`）

| 措施 | 做法 | 收益 |
|---|---|---|
| 紧凑序列化 | `separators=(',', ':')`；`pretty_json` 才缩进 | 三到五成 |
| 剔除空值 | `drop_none()` 递归删 `None` 键 | 明细场景约一成 |
| 列式明细 | `table_view()` 输出 `{cols, rows}` | 明细再省三到四成 |
| 预算兜底 | 超 `max_response_chars` 返回"结果过大 + 该加什么参数" | 防一次性吃光 |

再加两个**默认少给**的开关（各工具通用参数）：

- `view`：`brief`（默认，精简）/ `full`（全字段）；
- `top`：只看前 N 名（0=全部）；`class_name` / `subject` 尽量给。

### 改造后实测

| 口径 | ≈token | 说明 |
|---|---:|---|
| view=brief（默认）全部 12 个工具 | **4340** | 最坏单项 detail 1189 |
| view=full 全部 12 个工具 | 9044 | 仍比改造前省一半（紧凑 + 去空） |
| INSTRUCTIONS 系统提示 | 242 | 原 327 |
| tools/list schema（常驻） | 1936 | 原 1417（新增 kind/view 参数所致） |

> schema 涨了 519 token，但它是**每会话一次**；换来的是每次调用省
> 5000+ token。这也是刻意**不新增工具**的原因：工具越多，schema 越贵。

---

## 三、`kind=timed`：限时练走同一批工具

限时练是独立库 `timed.db`，**没有新增工具**，靠 `kind` 复用：

| 工具 | 限时练下的语义 |
|---|---|
| `list_exams` / `list_classes` / `get_exam_overview` | 场次清单 / 本场班级 / 科目与人数 |
| `get_class_score_summary` | 汇总表（学生 × 各科 + 现算总分 + 名次） |
| `get_class_score_detail` | 某班某科逐生（分数 + 该科年级名次） |
| `get_student_scores` / `..._trend` | 该生本场各科 / 某科跨场次 `[日期, T分]` |
| `get_subject_analysis` | **某科的班级分析**：去差均分 + 名次区间人数 |
| `compare_classes` | 按某科比各班（需 `subject`） |
| `get_exam_score_segments` / `get_layer_online_stats` | 不支持（限时练无分数段/分数线），返回提示 |

与网页端的口径差异（**必须按这个理解，别套普通考试**）：

1. 限时练**不存总分行**，总分＝各科现算；缺考不落库，天然不计入分母；
2. 名次是**该科全年级名次** `rank_grade`，不是总分名次；
3. 分层是**名次区间模板**的累积门槛（层「前N」＝名次 ≤ N 的人数），
   **没有分数线**；去差均分按班型名额剔除该科末尾 N 人；
4. 无选科方向 / 组合，也没有划线——所以不给分数段与上线率。

权限＝`grades.view` + 年级在可见范围内（复用网页端
`timed_service.visible_grades_of(user)`，**必须显式传 user**：MCP 进程
没有 `current_user`）。字段口径全部复用网页端 `timed_analysis`，
不另写一份聚合。

---

## 四、给客户端（AI）的省额度约定

写在 `INSTRUCTIONS` 里，每次会话都会发给模型：

1. 先 `list_my_score_scope` 确认能看什么，再取数；
2. 能用 `class_name` / `subject` / `top` 缩小的，就别取全量；
3. `view=full` 只在确实需要各科均分 / 分层明细 / 年级横向时用；
4. 越权返回 `{"ok": false, "error": "权限不足：…"}`，不要绕过重试。

---

## 五、相关配置（`mcp_server/config.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `pretty_json` | `false` | 缩进输出（仅人肉排查用），会让结果多付三到五成 |
| `max_response_chars` | `24000` | 单次字符预算；0=不限制 |
| `timed_cache_ttl` | `300` | 限时练派生缓存（名次/T 分）在 MCP 侧的失效秒数 |
| `max_rows` | `200` | 行数上限（超出截断并提示） |

---

## 六、复测入口

```bat
mcp_server\.venv\Scripts\python.exe mcp_server\smoke_scores.py
mcp_server\.venv\Scripts\python.exe tests/_archive/qa_20261021/_probe_mcp_payload.py
mcp_server\.venv\Scripts\python.exe tests/_archive/qa_20261021/_probe_schema.py
```

- `smoke_scores.py`：105 项全绿（含限时练 12 项、省额度断言 2 项）；
- `_probe_mcp_payload.py`：brief / full / timed 三组体积对照；
- `_probe_schema.py`：拆解每个工具的 description 与参数各占多少字符。

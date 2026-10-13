# StuLink 项目约定（供 AI 协作参考）

## 教师-学科关系：以「任课教师映射」为唯一依据（2026-10-10 起）

- **唯一数据源**：`TeacherSubjectLink`（成绩管理模块维护），决定"谁教哪个班哪一科"、
  谁能看哪些成绩、教师维度的教学成绩归属。
- **页面（同一批视图函数、同一份数据）**：
  - 教务管理：`/academic/teacher-links`（端点 `academic.teacher_links_*`）
  - 成绩管理：`/grades/teachers`（端点 `grades.teachers_*`，批量导入入口 `grades.teachers_import_page`）
  - 权限 key：`grades.teachers`
- **已下线（不要再引用或恢复）**：任课安排 `/academic/duty`、`/academic/duty/export`、
  `/academic/api/duty` 与模板 `academic/duty_table.html`，以及 `duty_service.py` 里
  按课表实时聚合的 `build_class_duty` / `build_teacher_duty` / `build_subject_duty` /
  `export_duty_workbook`。旧地址一律 404。
- **核查纪律**：修改或核查任何涉及"班级 × 学科 × 教师"的逻辑、报表、导出、界面时，
  只读任课教师映射（`TeacherSubjectLink`），**不要再参考或依赖任课安排**（包括它的
  课表聚合口径、导出格式与旧文档描述）。
- **不受影响**：备课组长 `/academic/leaders`、晚自习值班 `/academic/schedule/<sid>/night-duty`、
  学期课表 `/academic/schedule/*`。
- 背景与版本记录见 `docs/教务管理设计手册.md`（第 3、4、7 节）。

## 沟通总结纪律（2026-10-10 起，用户明确要求）

- **每完成一个「大问题」就要写一次总结**，不要等用户提醒；用户也会到 `docs/聊天总结/` 目录回查沟通过的事情。
- 位置与命名：`docs/聊天总结/YYYYMMDD_批次总结_<主题>_v<版本>.md`（版本号用当时已完成/发布的版本）。
  （2026-10-13 从项目根目录 `聊天总结/` 移到 `docs/聊天总结/`；`.gitignore` 的 `聊天总结/` 规则按目录名任意层级匹配，因此移入 `docs/` 后**仍不会被提交**，`scripts/bump_version.py` 的 `SKIP_DIRS` 也按目录名跳过，两边都不需要改。）
- 内容固定七段：批次做了什么（按主题）／修复的 Bug／关键口径约定／验证记录／发布与部署／遗留与下一批／运维备忘。
- 严禁写入敏感信息：**不要写 NAS 地址、账号（手机号）、口令、密钥**，用 `<NAS>` 之类占位符。

## 成绩模块「班级」口径（2026-10-10 定案，务必遵守）

- `exam_scores.class_name` **两处语义不同**：
  - **总分行（subject='总分'）= 考试当时班级** ← **权威口径**
  - 科目行 = 导入时的**当前班级**，选科分班后会与"考试当时"不一致
- 因此：**任何按班级聚合的分析、筛选、展示，都必须用总分行口径**（`subject='总分'`）。
  已按此实现：考试概览「按班级查看」、班级胶囊、成绩单班级列。
- **历史班级的唯一真值是学校提供的原始成绩表（Excel）**；学生名册的班级是"现在"的班，不能用于历史考试。
- 已按原始表修正 17 场 2024 级考试（19,521 行），复核与原始表 100% 一致；2025 级（考试 18–20）缺原始表未修。

## 学籍徽标：**已按用户要求移除**（2026-10-10）

- 用户决定：**历次考试成绩单不再显示学生的当前学籍情况**（不标「已转出 / 已休学」等），
  目的是简化数据结构、便于长期维护。相关代码已全部删除（`off_school_badge`、`status_badge`、
  `st_map` 学籍查询、两个成绩单模板里的徽标渲染、`Student` 的引入）。
- **不要再重新加回**这类「用当前学籍反标历史考试」的功能。
- 历史考试名单仍然完整保留（考试是自包含数据包）；学籍状态只在「学生管理」等
  现行数据页面维护，不进入考试成绩呈现。

## 部署方式选择（2026-10-10）

| 场景 | 用什么 |
|---|---|
| 只改代码/模板、**无**数据库迁移 | `deploy/hot_update.ps1`（SSH 增量热更新，省打包） |
| 版本含**数据库迁移**、或新增依赖、一次改上百文件 | `deploy/nas_update_<版本>.sh`（停容器后按序跑迁移） |
- ⚠️ 改动 **Python 代码或模板**后必须**重启容器**才生效（模板有进程内缓存）。
- ⚠️ 容器 `/app` **不是**宿主机挂载目录（只有 `stulink-data → /app/data`），
  所以 `scp` 到宿主机不生效，必须再 `docker cp` 进容器。
- ⚠️ 一次性容器跑迁移时**不能**把新代码挂到 `/app`（会盖掉镜像内依赖，如 pptx 在容器可写层）；
  正确做法是先把停下的容器 `docker commit` 成临时镜像再跑。

## 考务/考场口径（2026-10-11 定案）

- **容量属于「每场考务」，不存基础数据**：同一房间不同年级/不同考试容量可能不同。
  `affair_room_libs` 只存 位置/备注；容量写在 `affair_rooms.capacity`。
  新建考务批次时**默认沿用该年级上一次**的考场安排与容量（`_inherit_rooms_from_prev`）。
- **考场维护**（`/grades/rooms`，基础配置）：`exam_room_groups` + `exam_room_group_map`
  （房间与分组是**多对多**）。默认三分组「高一/高二/高三考场」；房间默认「未分组」。
  分组只影响归集与后续建场筛选，**不影响任何历史场次考场**（每场考务的考场彼此独立）。
- **历史考务名单从成绩反推**（`exam_scores` 总分行：学号/姓名/考试当时班级/选科），
  不要用「当前学籍」同步历史场次。

## 历年任课老师真值来源（2026-10-11）

- 原始表任课表位置：**202409 = 「教师任课表」sheet**；**其余 = 「自定义参数」sheet**。
  「自定义参数」里有一块**可编辑的转置块**（列为班级、行为 班主任/副班/语文…，权威）与一块自动转置块。
- 分成 **历史选科 / 物理选科 两个文件**的考试，两文件的任课表**可能互相打架**；
  核对时**按班级方向取对应文件**（史* 看历史文件、物* 看物理文件）。
- **历史方向（史*）没有物理课/物理成绩**，不要给该方向班级配物理老师（原表若填了属笔误）。
- `exam_teacher_links.source='excel'` 表示来自旧成绩 Excel；`backfill` 为按当前映射推测，可信度低。

## 回归与自检（2026-10-13 起，改代码前后都跑）

**一键跑全部**（3 项自检 + 4 套回归，末行给汇总，失败退出码 1）：

```powershell
py -3.12 scripts\run_all_regression.py            # 自检 + 4 套回归
py -3.12 scripts\run_all_regression.py --with-mcp # 额外跑 MCP 端到端自测（需 mcp_server\.venv）
py -3.12 scripts\run_all_regression.py --quick    # 只跑 3 项自检
```

- 单项也可单独跑：`tests/academic_regression.py`（教务，含 `item` 名可只跑一项，如
  `py -3.12 tests/academic_regression.py AUDITP2`）、`tests/sec_regression.py`、
  `tests/dormitory_regression.py`、`tests/class_scope_regression.py`。
- 自检：`scripts/scan_security_patterns.py`（六类安全红线，必须 0 命中）、
  `scripts/check_columns.py`（模型列 vs 现网列）、`scripts/check_indexes.py`
  （模型声明索引 vs 现网；`--fix` 可补，改库前会先备份）。
- MCP 服务改动另跑：`mcp_server\.venv\Scripts\python.exe mcp_server\smoke_scores.py`。
- `tests/_archive/` 是**一次性历史脚本**（按日期 `qa_YYYYMMDD/` 归档，被若干批次总结当验证证据引用），
  **不属于回归**，改代码不必管它；新增一次性脚本也请放这里，别堆在 `tests/` 顶层。
- 当前基线（2026-10-13 全面审计后）：教务 505、安全 287、宿舍 31、班级范围 8，
  **合计 831 项全绿**；MCP 自测 76 / 0。

### MCP 服务的准入现状（动 mcp_server 前必读）

- 实机 `mcp_server/config.json`：`host=0.0.0.0`、`auth_mode=account`、**`require_token=false`**
  —— 客户端只需账号密码（`X-StuLink-User/Password` 或 Basic）。
  **2026-10-13 用户口径：第二道准入（令牌）不开**（开过一次，当天按要求关了）：
  能力保留，改 `require_token=true` + 重启即启用；若要更严也可把 `host` 收回 `127.0.0.1`。
  多带 `X-StuLink-Token` 头在 `false` 时会被直接忽略（无害），所以客户端加了也不用撤。
- 令牌只在 `config.json` 与启动横幅（首尾片段）出现：**不要回显、不要写进文档/日志/提交**。
  该文件**从未进过 git**（本地与 `origin` 均无；`.gitignore` 已含它），它的**任何副本/备份/归档
  也不许提交**（`.gitignore` 已封堵 `**/backup_mcp_server*/`、`tests/**/config.json`、
  `**/config.json.bak*`）；`scripts/scan_security_patterns.py` 第 7 项会按令牌**字面值**
  全仓搜一遍并拦截（含 `backup*` 目录，命令 `py -3.12 scripts/scan_security_patterns.py` 必须 0 命中）。
- 认证限速三层：同来源+同账号连错 5 次 → 只锁该来源（**不锁账号**）；同来源失败总数 20 次
  → 挡该来源；跨来源累计 → 只加延迟不拒绝。改这块别把"锁来源"退回"锁账号"（那会被人拿错口令锁死老师账号）。
- lint（本仓库不装进依赖，按需 `py -3.12 -m pip install ruff`）：
  `py -3.12 -m ruff check app mcp_server scripts tests`，规则见根目录 `ruff.toml`
  （只开"真错"级：语法错误 E9、未定义名 F82、重复定义 F811）。

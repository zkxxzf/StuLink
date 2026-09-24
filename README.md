# StuLink - 智联校园学生管理系统

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/flask-3.x-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Version](https://img.shields.io/badge/version-1.18.2.1-orange.svg)](https://gitee.com/zkxxzf/StuLink)

面向中学的综合学生管理平台，采用双站点 + 七模块 + 多库故障隔离架构：

- **在校生系统（主应用 :5000）**：宿舍自动分配 · 合班管理 · 床位管理 · 学生信息管理 · 批量导入/调班 · 二维权限（功能三档 × 数据范围）· 成绩管理与四大分析（ECharts）· 班级横向对比 · AI 分析（BYOK）· 德育管理 · 教务（教师名单/课表/查课/调课/表单/业绩）· 教师工作台（班级概览/工作记录/考勤/积分/画像）· 通知中心
- **往届生查询（独立应用 :5001）**：毕业生数据快照查询 · 学习经历变迁 · 宿舍历史

## 功能模块（v1.18.2.1）

| 模块 | 站点 | 状态 | 说明 |
|------|------|------|------|
| 📚 系统管理 | 主应用 | ✅ 已完成 | 学生管理 · 用户/账号管理 · 教师名单同步 · 字典管理 · 班型设置 · 权限组 · 年级毕业归档 · 审计日志 |
| 🏠 宿舍管理 | 主应用 | ✅ 已完成 | 宿舍列表 · 可视化拖拽分配 · 床位管理 · 自动分配 V20260805 · 合班自动识别 · 统计报表 · 宿舍数据导入 · 床位操作并发安全 |
| 📊 成绩管理 | 主应用 | ✅ 已完成 | 考试登记 · 成绩导入（分批覆盖）· 划线分层 · **五大分析**（年级/班级/学科/任课教师/**班级对比**，ECharts 可视化）· 双上线/进退步/预警 · Excel/PDF 导出 · 成绩证明快照（AES 加密）· AI 分析（个人 Key/公共 Key，数据按权限收敛）· 考务批次（考场编排/座号标签/PDF 三视图）· 个人成绩查询 |
| 🎓 教务管理 | 主应用 | ✅ 已完成 | 教师名单（唯一 UID + 身份证 AES 密文 + 手机号/学科）· 课本管理 · 查课记录 · 教师业绩 · 课表档案（多学期）· 调课（个人/统一）· 表单收集（多题型/文件/审核）· 交付物汇总 |
| 👨‍🏫 教师工作台 | 主应用 | ✅ 已完成 | 班级概览 · 学生信息（年级+班级联动筛选）· 成绩分析 · 积分概览 · 工作记录（班会/家访/谈话+附件）· 考勤记录 · 个人工作台首页 |
| 🧑‍🎨 学生画像 | 主应用 | ✅ 已完成 | 德智体美劳五维雷达 · 成长轨迹 · 多维数据聚合（成绩/积分/考勤/住宿/工作记录/事件）· 评语管理 |
| ⭐ 德育管理 | 主应用 | ✅ 已完成 | 学生加减分记录 · 规则模板 · 批量导入 · 汇总排名 · 独立数据库（原"积分管理"v1.18.1.0 更名） |
| 🔔 通知中心 | 主应用 | ✅ 已完成 | 站内通知 · 收件人筛选（年级/班级/角色/教师名单）· 已读回执 · 撤回 |
| 🔍 往届查询 | 独立应用 | ✅ 已完成 | 毕业生基本信息查询 · 宿舍分配快照 · 学习经历变迁时间线 |

## 宿舍自动分配算法（V20260805 · v1.8.0 迭代爬山版）

平滑动态贪心 + 全局压力等级制 + 迭代爬山选优：

- **压力等级 L=6/7/8**：根据所选房间总数与总人数自动判定宽松/紧张模式，无需人工调参
- **宽松优先**：优先 L=7（8 人间只住 7 人）；房间不足时自动升档 L=8 极限重试，保证分配成功
- **动态前瞻**：分配时检查后续容量，压力均匀分散，班级连续切段（S 型序列化，偶数层正向/奇数层反向）
- **合班被动触发**：班级收尾剩 1~3 人时与下一班（同性别+同年级+同班型）合并，两班不分主次
- **合班调整优化**：自动拆分链式合班，减少合班宿舍数量（如 16 间 → 13 间）
- **合班自动识别**：手动选择多班（如 `01班+02班`）即自动识别为合班宿舍，全站自动显示
- **床位填充优先级（v1.7.2）**：小容量房间优先住满；独享宿舍满后才允许合班；合班按份额分配
- **迭代爬山（v1.8.0）**：初稿（严格分级贪心）+ 迭代爬山 + 全局评分选优；7 项权重前端可调；连续 20 轮未刷新最优自动收敛；满 8 惩罚消除"8 人满 vs 6 人"并存

## 项目架构

```
双站点部署：
  :5000 — StuLink   在校生管理系统（读写）
  :5001 — Alumni    往届生查询系统（只读 history.db）

八库分离（故障隔离，快照字段关联，不建跨库外键）：
  data/system.db     基础库（用户/权限组/字典/学生/班级档案/年级/操作日志）
  data/dormitory.db  宿舍库（房间/床位/分配历史）
  data/history.db    历史库（毕业生快照 + 学习经历变迁 + 归档日志）
  data/grades.db     成绩库（考试/成绩/分层/任课映射/考务批次/AI Key 与报告/证明快照）
  data/points.db     德育库（加减分记录/规则模板/汇总）
  data/academic.db   教务库（教师名单/课表/查课/调课/业绩/表单/工作记录/考勤）
  data/portrait.db   画像库（多维聚合/评语/事件）
  data/timetable.db  课表库（跨学期课表条目）
```

- **后端**：Python 3.11+ / Flask 3.x / Flask-Login / Flask-SQLAlchemy 2.x（多库 binds）/ Waitress / openpyxl / reportlab / python-pptx
- **前端**：Bootstrap 5 / jQuery / Jinja2 / ECharts（成绩可视化与班级对比）/ Chart.js（画像）
- **安全（v1.18.2.1）**：
  - **身份**：管理员首启随机口令 + 强制改密 · 一次性初始口令展示 · 会话口令摘要（改密即踢）· IP + 账号双维限流
  - **数据**：AES-256-GCM 身份证与 AI Key 加密 · CSRF 8h 时效 · 二维权限（功能三档 × 数据范围）· 白名单式教师候选过滤
  - **传输**：`url_guard` 出站白名单 + DNS 解析 + 禁 302 跟随 · CSP（Report-Only 起步，可切 enforce）· HTTPS Cookie Secure
  - **输入**：`upload_guard` 4 层（MIME / magic-bytes / 扩展名白名单 / 危险类型）· `text_guard` 考务短标签清洗 · Excel 公式注入防护（`xl_row` 转义）
  - **运维**：`err_safe` 异常脱敏 · `log_mask` 日志身份证/手机号掩码 · `scan_security_patterns.py` CI 正则扫描 · `_ddl_guard` 迁移白名单 · `sec_regression.py` 1274 行 244 断言
- **部署**：支持 Docker Compose（独立容器）/ 阿里云 ECS / 绿联 NAS / Windows 本地；采用**轻量更新**（不 rebuild 镜像，直接 `docker cp` + `docker restart`）

## 快速开始

```bash
# 本地开发 — 主应用（:5000）
pip install -r requirements.txt
python run.py --dev

# 本地开发 — 往届查询（:5001）
cd alumni_app && python run.py

# Docker 部署（两个容器）
docker build -t stulink:v1.18.2.1 .
docker build -t stulink-alumni:v1.0.0 ./alumni_app
docker-compose up -d
```

### 首次启动与安全基线（H-1 / R-7，必读）

- **默认管理员口令已不再是 `admin/admin123`**：首次启动在系统库里创建 `admin` 时，
  会生成**随机初始口令**并只在控制台打印一次，且该账号 `must_change_pwd=True`，
  登录后会被强制跳转到改密页，未改密前无法访问任何功能页。
  若控制台输出已滚动过去，请删除 `data/system.db` 中的 admin 记录后重启，
  或由其他管理员在「用户管理」里为其重置口令。
- **存量部署**：若 `admin` 仍是历史默认口令，启动时会打印显著告警，请立即修改。
- 批量导入的教师账号不再使用「手机号即密码」，改为一次性随机口令（页面一次性展示）。
- **密码复杂度**：`password_policy` 强制 ≥8 位 + 含字母与数字 + 非弱口令黑名单 + 不等于用户名/姓名/手机号。
- **升级即全站掉登录**（session_guard 引入会话口令摘要，旧会话缺 `_pwd_fp` 视为无效）。
- 生产启用 HTTPS 后，建议设置环境变量：
  - `STULINK_SESSION_COOKIE_SECURE=1`（会话 cookie 加 `Secure`）
  - `STULINK_CSP_MODE=enforce`（CSP 由只上报切换为强制拦截，需先确认无违规）
  - `STULINK_SESSION_LIFETIME=28800`、`STULINK_CSRF_TIME_LIMIT=28800`（8 小时）
- 提交前自检：`python tests/sec_regression.py`（安全回归）与
  `python scripts/scan_security_patterns.py`（禁用模式扫描）。

> 💡 生产部署包与版本记录托管在 [Gitee](https://gitee.com/zkxxzf/StuLink)（主仓库，私密）与 GitHub Releases（镜像，历史）

## 目录结构

```
StuLink/
├── app/                       # 主应用
│   ├── modules/
│   │   ├── auth/              # 认证（登录、改密、会话守卫）
│   │   ├── welcome/           # 欢迎页与状态徽标
│   │   ├── system/            # 系统管理（学生/用户/字典/权限/年级/班级档案/日志）
│   │   ├── dormitory/         # 宿舍管理（房间/床位/自动分配 v8/统计/图表）
│   │   ├── grades/            # 成绩管理（导入/分层/五大分析/考务/证明/AI）
│   │   ├── academic/          # 教务（教师名单/课表/查课/调课/业绩/表单）
│   │   ├── workbench/         # 教师工作台（班级概览/records/attendance/画像入口）
│   │   ├── portrait/          # 学生画像（多维聚合/评语/事件）
│   │   ├── points/            # 德育管理（原积分，加减分记录/规则模板）
│   │   └── notifications/     # 通知中心（收件人筛选/已读/撤回）
│   ├── models/                # 数据模型（八库 binds）
│   ├── forms/                 # WTForms
│   ├── templates/             # Jinja2 模板
│   ├── services/              # 业务逻辑（跨模块共享）
│   └── utils/                 # 工具：crypto/cache/decorators/helpers +
│                              #   8 大安全 utils（url_guard/upload_guard/session_guard/
│                              #   password_policy/student_scope/text_guard/err_safe/log_mask）
├── alumni_app/                # 往届生查询（独立 Flask 应用 :5001）
│   ├── app/
│   │   ├── routes/            # basic.py / dormitory.py
│   │   └── templates/         # 登录 + 查询页
│   └── run.py                 # 生产默认（--dev 显式开 debug）
├── tests/                     # 安全回归测试（sec_regression.py 1274 行 244 断言）
├── scripts/                   # 初始化 / 迁移 / 版本 bump / 安全扫描 / 冒烟测试
│   ├── bump_version.py        # 四段版本号自动 bump 工具（zk/sakay/minor/major/set）
│   ├── sync_users_to_teachers.py  # users ↔ academic.teachers 一次性回填
│   ├── scan_security_patterns.py  # 禁用模式正则扫描
│   └── smoke_grades.py        # 98 断言端到端冒烟
├── data/                      # 8 个 SQLite + 密钥 + 备份
├── docs/                      # 设计手册 / 使用手册 / 测试报告
├── Dockerfile                 # 主应用镜像
└── docker-compose.yml         # 双容器编排
```

## 版本管理规范

**四段式** `主.次.三.四`（自 v1.18.0.0 起）：

- **第三段**：主线维护者 `zkxxzf` 每次改动 +1
- **第四段**：协助者 `sakay`（飒龘）每次改动 +1
- **里程碑**：主版本或次版本变动时打 tag；协助者分支合并到 master 后**立即删除支线**

统一由 `python -m scripts.bump_version <mode>` 执行（支持 `show | zk | sakay | minor | major | set X.Y.Z.W`）。

## 文档

- 综合设计：[设计文档](docs/设计文档.md) · [用户文档](docs/用户文档.md) · [部署文档](docs/部署文档.md) · [协作指南](docs/协作指南.md)
- 模块设计手册：
  - [宿舍管理](docs/宿舍管理设计手册.md) + [宿舍分配算法](docs/宿舍分配算法手册.md)
  - [成绩管理](docs/成绩管理设计手册.md) + [成绩分析算法](docs/成绩分析算法手册.md)
  - [系统管理](docs/系统管理设计手册.md)
  - [教务管理](docs/教务管理设计手册.md)
  - [教师工作台](docs/教师工作台设计手册.md)
  - [学生画像](docs/学生画像设计手册.md)
  - [德育管理](docs/德育管理设计手册.md)（原积分管理）
- 专项设计与审计：
  - [历史记录与日志系统设计](docs/历史记录与日志系统设计.md)
  - [个人成绩查询与成绩证明](docs/个人成绩查询与成绩证明功能计划书.md)
  - [安全审计报告](docs/安全审计报告_20260807.md) · [安全整改清单](00-security-todo-merged.md)
  - [测试报告 v1.7.1](docs/测试报告_20260808.md) · [成绩模块测试与修复](docs/成绩管理模块测试与修复总结报告.md)

## 技术里程碑（近三期）

| 版本 | 日期 | 亮点 |
|---|---|---|
| **v1.18.2.1** | 2026-09-24 | sakay 大规模安全加固：8 utils + 60 处权限收敛 + 1274 行 sec_regression；管理员首启随机口令 + 强制改密；SSRF 白名单 + 302 阻断；upload_guard 4 层校验；CSP Report-Only 起步 |
| **v1.18.2.0** | 2026-09-24 | users ↔ academic.teachers 双向同步（admin 不属教师，白名单式过滤）；一次性回填 94 条教师档案 |
| **v1.18.1.0** | 2026-09-24 | 积分管理 → 德育管理（用户可见文本层，`points.*` 技术标识保留不动）|
| **v1.18.0.0** | 2026-09-23 | 主仓库迁移至 Gitee（HTTPS + 私人令牌 + Windows 凭据管理器）；sakay 分支 PR#2 成绩分析班级对比 + workbench 年级/班级联动下拉；4 段版本号规范落地；`bump_version.py` 工具 |
| v1.17.0 | 2026-09-21 | 教务管理 + 教师工作台 + 学生画像 + 通知中心（PR#5 合并）+ 安全审查两轮整改 + 考试/考务管理合并 |

## 许可证

[Apache License 2.0](LICENSE) — 允许商用，保留版权署名

Copyright (c) 2026 zkxxzf

## 致谢

感谢 [@飒龘 / Sakaay](https://gitee.com/sakay) 深度协作：
- **v1.18.2.1**：安全加固大版（46 项清单闭环 · 8 utils · 1274 行回归测试）
- **v1.18.0.0**：成绩分析班级对比 · workbench 联动筛选 · 演示数据脚本增强 · PR 协作流程
- **v1.17.0**：教务 / 工作台 / 画像 / 通知四大模块 + 两轮安全整改
- **v1.13.x**：成绩汇报区 · 成绩证明快照加密 · 全局性能优化
- 早期：协作指南共建 · 成绩模块测试与修复 · 前端资源本地化

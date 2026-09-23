# StuLink - 智联校园学生管理系统

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/flask-3.x-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Version](https://img.shields.io/badge/version-1.9.2-orange.svg)](https://github.com/zkxxzf/stulink)

面向中学的综合学生管理平台，采用双站点架构：

- **在校生系统（主应用 :5000）**：宿舍自动分配 · 合班管理 · 床位管理 · 学生信息管理 · 批量导入/调班 · 多角色权限 · 统计报表 · 成绩管理与分析（ECharts）· AI 分析（BYOK）· 德育管理
- **往届生查询（独立应用 :5001）**：毕业生数据快照查询 · 学习经历变迁 · 宿舍历史

## 功能模块

| 模块 | 站点 | 状态 | 说明 |
|------|------|------|------|
| 📚 系统管理 | 主应用 | ✅ 已完成 | 学生管理 · 教师管理 · 字典管理 · 班型设置 · 权限组 · 年级毕业归档 |
| 🏠 宿舍管理 | 主应用 | ✅ 已完成 | 宿舍列表 · 可视化拖拽分配 · 床位管理 · 自动分配 V20260805 · 合班自动识别 · 统计报表 · 宿舍数据导入 · 床位操作并发安全 |
| 🔍 往届查询 | 独立应用 | ✅ 已完成 | 毕业生基本信息查询 · 宿舍分配快照 · 学习经历变迁时间线 |
| ⭐ 德育管理 | 主应用 | ✅ 基础版已完成（持续完善） | 学生加减分记录 · 学生积分汇总排名 · 范围权限（全校/年级/班级）· 独立数据库 |
| 📊 成绩管理 | 主应用 | ✅ 已完成（持续完善中） | 考试登记 · 成绩导入（分批覆盖）· 划线分层 · 四大分析（年级/班级/学科/任课教师，ECharts 可视化）· Excel/PDF 导出 · AI 分析（个人 Key/公共 Key，数据按权限收敛） |

## 宿舍自动分配算法（V20260805）

平滑动态贪心 + 全局压力等级制：

- **压力等级 L=6/7/8**：根据所选房间总数与总人数自动判定宽松/紧张模式，无需人工调参
- **宽松优先**：优先 L=7（8 人间只住 7 人）；房间不足时自动升档 L=8 极限重试，保证分配成功
- **动态前瞻**：分配时检查后续容量，压力均匀分散，班级连续切段（S 型序列化，偶数层正向/奇数层反向）
- **合班被动触发**：班级收尾剩 1~3 人时与下一班（同性别+同年级+同班型）合并，两班不分主次
- **合班调整优化**：自动拆分链式合班，减少合班宿舍数量（如 16 间 → 13 间）
- **合班自动识别**：手动选择多班（如 `01班+02班`）即自动识别为合班宿舍，全站（列表/详情/可视化/统计）自动显示，无需手动标记
- **床位填充优先级（v1.7.2）**：小容量房间（6 人间）优先住满；班级收尾时先填满独享宿舍空床，独享都满才允许合班；分床位时合班宿舍严格按份额分配
- **迭代爬山算法（v1.8.0 里程碑）**：初稿（严格分级贪心）+ 迭代爬山 + 全局评分选优；7 项权重（宽松/超6/满8/合班/班级分散/小房/空房）前端可调；连续 20 轮未刷新最优自动收敛；默认 1000 轮上限；满8 惩罚消除"8人满 vs 6人"并存

## 项目架构

```
双站点部署：
  :5000 — StuLink 在校生管理系统（读写）
  :5001 — Alumni  往届生查询系统（只读 history.db）

共享数据：
  data/system.db     基础库（用户/学生/字典/权限）
  data/dormitory.db  宿舍库（房间/床位）
  data/history.db    历史库（毕业生快照 + 变迁日志 + 分配历史）
  data/grades.db     成绩库（考试/成绩/分层/任课映射/AI Key 与报告）
  data/points.db     积分库（学生加减分记录）
  data/backups/      毕业备份
```

- **后端**: Python 3.11+ / Flask 3.x / Flask-Login / SQLAlchemy / Waitress
- **前端**: Bootstrap 5 / jQuery / Jinja2 / ECharts（成绩可视化）
- **安全**: AES-256 身份证与 AI Key 加密 / CSRF 防护 / 登录频率限制 / 审计日志
- **部署**: 支持 Docker Compose（独立容器）/ 阿里云 ECS / 绿联 NAS / Windows 本地

## 快速开始

```bash
# 本地开发 — 主应用（:5000）
pip install -r requirements.txt
python run.py --dev

# 本地开发 — 往届查询（:5001）
cd alumni_app && python run.py

# Docker 部署（两个容器）
docker build -t stulink:v1.18.0.0 .
docker build -t stulink-alumni:v1.0.0 ./alumni_app
docker-compose up -d
```

默认管理员：`admin` / `admin123`（首次登录请立即修改）

> 💡 生产部署推荐直接使用发布包：镜像托管于 [GitHub Releases](https://github.com/zkxxzf/StuLink/releases) 附件（详见 [部署文档](docs/部署文档.md)）

## 目录结构

```
StuLink/
├── app/                    # 主应用
│   ├── modules/
│   │   ├── auth/           # 认证
│   │   ├── welcome/        # 欢迎页
│   │   ├── system/         # 系统管理（学生/教师/字典/权限/年级）
│   │   ├── dormitory/      # 宿舍管理（房间/床位/分配/统计）
│   │   ├── points/         # 德育管理（占位）
│   │   └── grades/         # 成绩管理（导入/分层/四大分析/AI）
│   ├── models/             # 数据模型
│   ├── templates/          # Jinja2 模板
│   ├── services/           # 业务逻辑
│   └── utils/              # 工具（加密/缓存/装饰器）
├── alumni_app/             # 往届生查询系统（独立 Flask 应用）
│   ├── app/
│   │   ├── routes/         # basic.py / dormitory.py
│   │   └── templates/      # 登录 + 查询页
│   └── run.py
├── data/                   # 数据库 + 密钥
├── docs/                   # 设计手册 / 使用手册 / 测试报告
├── scripts/                # 初始化/迁移脚本
├── deploy/                 # 生产部署包
├── Dockerfile              # 主应用镜像
└── docker-compose.yml      # 双容器编排
```

## 文档

- [设计文档](docs/设计文档.md)
- [成绩管理设计手册](docs/成绩管理设计手册.md)（模块手册 ｜ [算法手册](docs/成绩分析算法手册.md)）
- 模块设计手册：[宿舍](docs/宿舍管理设计手册.md) ｜ [积分](docs/德育管理设计手册.md) ｜ [教务](docs/教务管理设计手册.md) ｜ [教师工作台](docs/教师工作台设计手册.md) ｜ [学生画像](docs/学生画像设计手册.md) ｜ [系统管理](docs/系统管理设计手册.md)
- [用户手册](docs/用户文档.md)（含宿舍自动分配 4 步向导与成绩管理完整操作）
- [部署文档](docs/部署文档.md)（Docker / 阿里云 / NAS）
- [宿舍分配算法手册](docs/宿舍分配算法手册.md)
- [安全审计报告](docs/安全审计报告_20260807.md)
- [测试报告 v1.7.1](docs/测试报告_20260808.md)
- [历史记录与日志设计](docs/历史记录与日志系统设计.md)

## 许可证

[Apache License 2.0](LICENSE) — 允许商用，保留版权署名

Copyright (c) 2026 zkxxzf

## 致谢

感谢 [@Sakaay](https://github.com/sa-da-sa-da) 参与协作开发（协作指南共建 · 成绩模块测试与修复 · 前端资源本地化）

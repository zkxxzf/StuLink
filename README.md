# StuLink - 智联校园学生管理系统

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/flask-3.x-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Version](https://img.shields.io/badge/version-1.5.0-orange.svg)](https://github.com/zkxxzf/stulink)

面向中学的综合学生管理平台，采用双站点架构：

- **在校生系统（主应用 :5000）**：宿舍分配 · 床位管理 · 学生信息管理 · 批量导入/调班 · 多角色权限 · 统计报表
- **往届生查询（独立应用 :5001）**：毕业生数据快照查询 · 学习经历变迁 · 宿舍历史

## 功能模块

| 模块 | 站点 | 状态 | 说明 |
|------|------|------|------|
| 📚 系统管理 | 主应用 | ✅ 已完成 | 学生管理 · 教师管理 · 字典管理 · 班型设置 · 权限组 · 年级毕业归档 |
| 🏠 宿舍管理 | 主应用 | ✅ 已完成 | 宿舍列表 · 可视化拖拽分配 · 床位管理 · 自动分配 V4 · 统计报表 · 宿舍数据导入 |
| 🔍 往届查询 | 独立应用 | ✅ 已完成 | 毕业生基本信息查询 · 宿舍分配快照 · 学习经历变迁时间线 |
| ⭐ 积分管理 | 主应用 | 🚧 开发中 | 学生积分记录与奖惩管理 |
| 📊 成绩管理 | 主应用 | 🚧 开发中 | 成绩录入、排名分析与报表导出 |

## 项目架构

```
双站点部署：
  :5000 — StuLink 在校生管理系统（读写）
  :5001 — Alumni  往届生查询系统（只读 history.db）

共享数据：
  data/system.db     基础库（用户/学生/字典/权限）
  data/dormitory.db  宿舍库（房间/床位）
  data/history.db    历史库（毕业生快照 + 变迁日志 + 分配历史）
  data/backups/      毕业备份
```

- **后端**: Python 3.11+ / Flask 3.x / Flask-Login / SQLAlchemy / Waitress
- **前端**: Bootstrap 5 / jQuery / Jinja2
- **安全**: AES-256 身份证加密 / CSRF 防护 / 登录频率限制 / 审计日志
- **部署**: 支持 Docker Compose（独立容器）/ 阿里云 ECS / 绿联 NAS / Windows 本地

## 快速开始

```bash
# 本地开发 — 主应用（:5000）
pip install -r requirements.txt
python run.py --dev

# 本地开发 — 往届查询（:5001）
cd alumni_app && python run.py

# Docker 部署（两个容器）
docker build -t stulink:v1.5 .
docker build -t stulink-alumni:v1.0 ./alumni_app
docker-compose up -d
```

默认管理员：`admin` / `admin123`（首次登录请立即修改）

## 目录结构

```
StuLink/
├── app/                    # 主应用
│   ├── modules/
│   │   ├── auth/           # 认证
│   │   ├── welcome/        # 欢迎页
│   │   ├── system/         # 系统管理（学生/教师/字典/权限/年级）
│   │   ├── dormitory/      # 宿舍管理（房间/床位/分配/统计）
│   │   ├── points/         # 积分管理（占位）
│   │   └── grades/         # 成绩管理（占位）
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
├── docs/                   # 设计/测试/用户文档
├── scripts/                # 初始化/迁移脚本
├── deploy/                 # 生产部署包
├── Dockerfile              # 主应用镜像
└── docker-compose.yml      # 双容器编排
```

## 文档

- [设计文档](docs/设计文档.md)
- [用户手册](docs/用户文档.md)
- [部署文档](docs/部署文档.md)（Docker / 阿里云 / NAS）
- [测试文档](docs/测试文档.md)
- [数据库分库规格说明](docs/模块化重构规格说明.md)
- [历史记录与日志设计](docs/历史记录与日志系统设计.md)
- [宿舍分配算法规范](docs/宿舍分配算法设计规范.md)

## 许可证

[Apache License 2.0](LICENSE) — 允许商用，保留版权署名

Copyright (c) 2026 zkxxzf

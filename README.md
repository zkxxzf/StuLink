# StuLink - 智联校园学生管理系统

[![License](https://img.shields.io/badge/license-CC%20BY--NC%204.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![Flask](https://img.shields.io/badge/flask-3.x-lightgrey.svg)](https://flask.palletsprojects.com/)
[![Version](https://img.shields.io/badge/version-1.4.6-orange.svg)](https://github.com/zkxxzf/stulink)

面向中学的综合学生管理平台。支持宿舍分配、床位管理、学生信息管理、多角色权限控制、数据统计与 Excel 导出，采用模块化架构设计，为积分管理、成绩管理等后续功能预留扩展接口。

## 功能模块

| 模块 | 状态 | 说明 |
|------|------|------|
| 🏠 宿舍管理 | ✅ 已完成 | 宿舍分配 · 床位管理 · 可视化拖拽 · 自动分配算法 V4 · 统计报表 · Excel 导出 |
| ⭐ 积分管理 | 🚧 开发中 | 学生积分记录与奖惩管理 |
| 📊 成绩管理 | 🚧 开发中 | 成绩录入、排名分析与报表导出 |

## 技术架构

```
Flask 3.x + SQLAlchemy + SQLite 多文件分库
├── system.db       基础库（用户/学生/字典/权限）
├── dormitory.db    宿舍库（房间/床位）
└── history.db      历史库（分配变更流水）
```

- **后端**: Python 3.11+ / Flask 3.x / SQLAlchemy 2.x / Waitress
- **前端**: Bootstrap 5 / jQuery / Jinja2 模板
- **安全**: AES-256 身份证加密 / CSRF 防护 / 登录频率限制 / 审计日志
- **部署**: Docker Compose 一键部署 / 代码数据分离 / 绿联 NAS 兼容

## 快速开始

```bash
# 本地开发
pip install -r requirements.txt
python run.py --dev

# Docker 部署
docker build -t stulink:v1 .
docker-compose up -d
```

默认管理员：`admin` / `admin123`（首次登录请立即修改）

## 项目结构

```
StuLink/
├── app/
│   ├── modules/       # 蓝图路由（5 模块 + auth + welcome）
│   ├── models/        # 数据模型（11 表）
│   ├── templates/     # Jinja2 模板
│   ├── services/      # 业务逻辑
│   └── utils/         # 工具（加密/缓存/装饰器）
├── data/              # 数据库 + 密钥（不纳入 Git）
├── docs/              # 设计/测试/用户文档
├── scripts/           # 初始化/迁移脚本
└── docker-compose.yml
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

[CC BY-NC 4.0](LICENSE) — 署名-非商业性使用 4.0 国际

Copyright (c) 2026 zkxxzf

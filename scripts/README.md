# scripts 目录（改数据 / 导数据 / 部署 / 迁移 / 同步）

本目录只放**会改变数据或环境**的脚本。

- 校验、断言、冒烟、回归 → `tests/`
- 生成代码/配置、解析 schema、调试 MCP 客户端 → `tools/`
- 一次性 `_tmp`、截图、调试脚本 → `tests/_local/`（不入库）

## 目录结构

```
scripts/
├── migrate/   数据库结构迁移（一次性、幂等、需 _mig_log 登记）
├── seed/      初始/演示数据：导入、模板、绑定关系、权限组
├── fix/       历史数据修补：改名、补字段、清理脏数据、重算名次
├── ops/       运维：清缓存、日志/备份/回收站清理、密钥迁出
├── zhixue/    智学网抓取与导入工具链
├── _db_backup.py / _ddl_guard.py / _mig_log.py
│              迁移脚本共用的内部库（**不要移动、不要改名**）
├── bump_version.py   版本号自增（发布流程）
├── init_db.py        建库建表
└── sync_zhixue_roster.py  智学网名单 → 学生档案
```

## 常用命令

```bash
# 迁移
py -3.12 scripts/migrate/migrate_score_review_20261015.py
# 演示/导入
py -3.12 scripts/seed/seed_full_demo.py
# 修数据
py -3.12 scripts/fix/recalc_exam_ranks_20261019.py
# 运维
py -3.12 scripts/ops/prune_trash.py
# 智学网名单
py -3.12 scripts/sync_zhixue_roster.py "名单.xls" --dry-run
# 版本号
python -m scripts.bump_version sakay
```

校验与回归请改用：

```bash
py -3.12 tests/run_all_regression.py
```

## 分类规则（新增脚本照此放置）

| 脚本目标 | 放到 | 例子 |
|---|---|---|
| 建表/加列/改结构 | `migrate/` | `migrate/migrate_swap_batch_20261010.py` |
| 造数据/导入/初始化 | `seed/` | `seed/seed_full_demo.py` |
| 修补历史数据 | `fix/` | `fix/fix_duplicate_rooms.py` |
| 清理/备份/部署运维 | `ops/` | `ops/prune_plaintext_backups.py` |
| 同步外部业务 | 对应业务目录 | `zhixue/import_to_stulink.py` |
| 校验/断言/冒烟/回归 | **`tests/`** | `tests/smoke_grades.py` |

## 路径自愈（重要）

子目录脚本顶部统一插入：

```python
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
```

它同时把**项目根**（能 `import app`）和 **scripts/**（能 `import _mig_log`）
加进 `sys.path`。新增子目录脚本请保留这段，否则会 `ImportError`。

## 历史文档说明

`docs/审计报告/`、`docs/版本历史/` 里的历史记录保留了当时的真实路径，
以本索引为准；活文档（`CODEBUDDY.md`、`README.md`、`docs/规范与说明/`）
已同步为当前路径。

# scripts 目录索引（2026-10-18 分类整理）

以前 135 个脚本平铺在一个目录里，现在按用途分到 5 个子目录；**根目录只留高频入口**。

## 目录结构

```
scripts/
├── 根目录（15 个高频入口，路径未变，文档/习惯命令照旧可用）
├── migrate/    数据库迁移（一次性，幂等，需在 _mig_log 登记）
├── seed/       演示/初始数据：造数据、导入模板、绑定关系、权限组初始化
├── fix/        历史数据修补：改名、补字段、清理脏数据、重置口令
├── smoke/      低频冒烟测试（高频的三个仍在根目录）
├── verify/     核对类：范围核验、名次重算、性能基准、权限快照
└── zhixue/     智学网抓取与导入（独立工具链，未改动）
```

## 根目录（常用，路径不变）

| 脚本 | 用途 |
|---|---|
| `run_all_regression.py` | 一键跑全套回归 |
| `scan_security_patterns.py` | 六类安全红线扫描（必须 0 命中） |
| `check_columns.py` / `check_indexes.py` | 模型列 / 索引 与现网核对 |
| `check_frontend_compat.py` | 模板内联脚本与手写 JS 兼容检查 |
| `migration_status.py` | 迁移登记状态（`--pending` 看未落地项） |
| `bump_version.py` | 版本号自增 |
| `init_db.py` | 建库建表 |
| `smoke_grades.py` / `smoke_permissions.py` / `smoke_academic.py` | 高频冒烟 |
| `make_pptx_clean_template.py` | 汇报模板生成 |
| `_mig_log.py` / `_db_backup.py` / `_ddl_guard.py` | 迁移脚本共用的内部库（**不要移动**） |

## 分类规则（新增脚本照此放置）

| 前缀 | 放到 | 例子 |
|---|---|---|
| `migrate_*` | `migrate/` | `migrate/migrate_swap_batch_20261010.py` |
| `seed_*` / `import_*` / `apply_*` / `setup_*` / `demo_*` / `add_*` | `seed/` | `seed/seed_full_demo.py` |
| `fix_*` / `backfill_*` / `normalize_*` / `update_*` / `cleanup_*` | `fix/` | `fix/fix_duplicate_rooms.py` |
| `smoke_*`（低频） | `smoke/` | `smoke/smoke_invig.py` |
| `verify_*` / `recalc_*` / `perf_*` / `perm_snapshot` | `verify/` | `verify/recalc_exam_ranks.py` |

## 运行方式（移动后路径变了）

```bash
# 迁移
py -3.12 scripts/migrate/migrate_score_review_20261015.py
# 演示数据
py -3.12 scripts/seed/seed_full_demo.py
# 核对
py -3.12 scripts/verify/recalc_exam_ranks.py
```

## 关于路径自愈（重要）

子目录脚本顶部统一插入了 8 行：

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

它同时把**项目根**（能 `import app`）和 **scripts/ 目录**（能 `import _mig_log`）加进 `sys.path`。
**新增子目录脚本请保留这段**，否则会 `ImportError`。

## 历史文档说明

`docs/审计报告/` 里的历史报告保留了当时的真实路径（如 `scripts/migrate_xxx.py`），
现在这些文件在对应子目录下 —— **以本索引为准**；`CODEBUDDY.md`、`README.md`、`deploy/*.sh`
等活文档已同步更新。

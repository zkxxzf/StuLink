# tests 目录（校验 / 断言 / 冒烟 / 回归）

这里放**只验证、不改业务数据**的脚本。会改数据的脚本在 `scripts/`。

## 一键入口

```bash
py -3.12 tests/run_all_regression.py             # 自检 + 回归 + 冒烟
py -3.12 tests/run_all_regression.py --quick     # 只跑静态自检
py -3.12 tests/run_all_regression.py --with-mcp  # 额外跑 MCP 自测
```

## 目录结构

```
tests/
├── run_all_regression.py          一键回归总入口
├── check_columns.py               模型列 vs 现网库
├── check_indexes.py               模型索引 vs 现网库
├── check_frontend_compat.py       手写 JS 语法兼容
├── check_migrations.py            迁移清单/执行器/登记表对账
├── check_xml_parsers.py           XML 解析库红线
├── check_grade_scope_20261015.py  成绩可见范围专项验证
├── scan_security_patterns.py      六类安全红线扫描
├── migration_status.py            迁移登记状态（只读）
├── perf_bench.py                  性能压测 / P95 基线
├── perm_snapshot.py               权限表只读快照
├── dump_all_routes.py             全站路由快照（供审计 diff）
├── *_regression.py                各模块回归（教务/安全/宿舍/…）
├── smoke/                         低频端到端冒烟
├── audit/                         安全与健康审计（多数只读）
├── _local/                        临时脚本/截图（**不入库**）
└── _archive/                      历史 QA 产物（**不入库**）
```

## 命名与放置

| 类型 | 命名 | 例子 |
|---|---|---|
| 回归 | `<模块>_regression.py` | `academic_regression.py` |
| 静态自检 | `check_<对象>.py` | `check_indexes.py` |
| 冒烟 | `smoke_<模块>.py` | `smoke/smoke_invig.py` |
| 审计 | `audit_<主题>.py` | `audit/audit_authz_matrix.py` |
| 基准/快照 | `<用途>.py` | `perf_bench.py` |

## 铁律

1. 断言失败必须用非 0 退出码（`sys.exit(1)`），否则一键回归判定不到。
2. 涉及数据库的测试用临时目录 + 临时 SQLite，**不得写 `data/`**。
3. 一次性脚本一律放 `_local/`，不要留在 tests 根目录。
4. 不要在 `tests/` 放迁移、备份、同步业务脚本 —— 那些属于 `scripts/`。

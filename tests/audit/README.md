# tests/audit —— 安全与健康审计脚本

2026-10-18 从项目根目录迁入（此前误放根目录，已纠正）。这些脚本**不是单元测试**，
是"对运行中的代码库做体检"的审计工具：多数只读，少数会**故意发起写请求**验证越权拦截。

## 怎么跑

```bash
# 任意目录都行（脚本自己 chdir 到项目根 + 把根加进 sys.path）
py -3.12 tests/audit/audit_config_headers.py
```

## 脚本清单

| 脚本 | 测什么 | 是否写数据 |
|---|---|---|
| `audit_config_headers.py` | DEBUG / SECRET_KEY / cookie 属性 / CSP / 点击劫持 / 版本泄露 | 只读 |
| `audit_role_survey.py` | 角色分布、样本账号、教师-班级-科目关系摸底 | 只读 |
| `audit_endpoint_inventory.py` | 列出所有带 `<int:id>` 的读写路由（越权测试目标） | 只读 |
| `audit_authz_matrix.py` | **多角色越权矩阵**：班主任跨班、任课跨班、只读角色写操作、删改别班学生 | **会发写请求**（断言被拒，正常运行不改数据） |
| `audit_teaching_scope_data.py` | 任课映射数据核对（UserClassLink vs TeacherSubjectLink，判断范围是否精确到班） | 只读 |
| `audit_student_scope.py` | 学生范围收紧后的回归断言（任教班放行 / 非任教班拦截） | 只读 |
| `audit_xss_sqli.py` | 反射型 XSS 与 SQL 注入实测（6 个搜索页 × 多组 payload） | 只读 |
| `audit_data_integrity.py` | 跨库孤儿数据、重复分数、同床重复、状态非法值 | 只读 |
| `audit_perf_sql.py` | 主要页面的耗时 + SQL 条数（找 N+1） | 只读 |
| `audit_nplus1.py` | 用户列表 N+1 优化后的断言（SQL < 20 且页面完整） | 只读 |
| `audit_affair_sql_profile.py` | 考务详情页 SQL 模板 TOP10（定位重复查询） | 只读 |
| `audit_backup_deps.py` | 备份文件 `integrity_check` + 实际安装版本对照 | 只读 |
| `audit_perm_matrix.py` | 权限组合诊断：谁能编辑学生/教师，却看不到手机号（脱敏是否真的生效） | 只读 |
| `audit_phone_page.py` | 单个账号打开编辑页的真实响应（是否 200、是否含 `data-phone-locked`、是否泄露完整号码） | 只读 |
| `audit_fix_matrix.py` | **6 项高危修复的验证矩阵**：口令授权上限、手机号脱敏、批量清空防护、批量改范围 | 建临时账号（会自动清理） |
| `audit_student_delete_archive.py` | 删学籍 → 自动归档 → 回收站恢复 → 学籍回库 | 建临时学生（会自动清理） |
| `audit_timetable_scope.py` | 课表范围口径（按老师所在年级）+ 字典删除归档 | 建临时字典项（会自动清理） |
| `audit_affair_bundle_restore.py` | 整批考务删除 → 快照 → 恢复，验证 7 张表**按原 id** 回来、外键不断链 | 建临时批次（会自动清理） |
| `audit_exam_bundle_restore.py` | 整场考试删除 → 快照 → 恢复，验证 11 张表按原 id 回来 | 建临时考试（会自动清理） |
| `_cleanup_tmp.py` | 清掉所有 `TMP*` 残留（账号/学生/字典/归档/批次） | **会删数据**，只删 TMP 前缀 |
| `audit_scope_diag.py` / `audit_scope_diag2.py` | 诊断用：权限矩阵、可见年级、500 堆栈 | 建临时字典项（会自动清理） |

## 注意

- 这些脚本连的是**真实库** `data/*.db`，默认不要在生产时段跑 `audit_authz_matrix.py`
  （它会 POST 到 `/students/<id>/edit` 等接口验证被拒；若权限校验有洞，**可能真的改数据**）。
- 审计结论以"当次运行输出"为准，不是固定断言；需要回归断言的用 `tests/sec_regression.py`
  和 `tests/smoke_*.py`。
- 新增审计脚本请放本目录，命名 `audit_<主题>.py`，并在上表补一行。

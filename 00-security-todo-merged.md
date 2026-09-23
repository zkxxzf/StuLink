# StuLink 安全审查 · 合并整改清单（03-文件合并版）

> **合并来源**：`01-ds-todo.md`（走查详版，9 高 / 9 中 / 8 低 + 红线）、`02-qw-todo.md`（P0–P3 优先级版）、`03-h3_todo.md`（按风险分级版）。
> **本文件是长期记忆**：三者的并集，所有独有项均保留，重复项合并为一条并标注多来源；2026-09-23 已逐项对照三份源文件复核补齐遗漏。
> **审查日期**：2026-09-23。**代码基线**：StuLink v1.17.0（行号为当时快照，改动后会漂移，以符号名/函数名为准）。
> **约定**：`[ ]` 未处理 / `[x]` 已修复；完成一项改 `[x]` 并补「YYYY-MM-DD 完成（改动点）」；需作废用 ~~删除线~~ 并写原因。
> **审查方式**：只读走查（鉴权与会话 / SQL / 文件路径 / 前端渲染 / 上传与导出 / 双站点）＋ 脚本扫描全部 390 路由装饰器覆盖 ＋ 关键点人工复核 ＋ Windows 文件系统大小写实测。
> **对照基线**：本仓库**没有** `tests/sec_test.py`（属「局域网答题」项目）；对照 `docs/安全审计报告_20260807.md`（v1.7.0）与 `scripts/smoke_permissions.py` / `smoke_grades.py` / `smoke_academic.py`。
> **说明**：需求提及的 `main.py`/`core`/`exepack`/题库更新/自动升级/MD5 校验在本仓库不存在（自动升级属另一项目），已据实映射到等价环节；证书防伪用 HMAC-SHA256 非 MD5。

---

## 0. 部署口径（决定风险成立与否）

| 形态 | 入口 | 说明 |
|---|---|---|
| **A. 局域网生产（主应用）** | `run.py` → waitress `0.0.0.0:5000`，HTTP | 默认形态；`SESSION_COOKIE_SECURE=False`；材料在 `app/static/uploads/` |
| **B. 往届查询站** | `alumni_app/run.py` → **`debug=True` `0.0.0.0:5001`** | 共用 `data/system.db` 用户表、`data/.secret_key`、`data/.encryption_key` |
| **C. Docker** | `Dockerfile` / `docker-compose.yml` | 镜像 `COPY app/`；`alumni` 以 `:ro` 挂载同一 `stulink-data` |

**两条铁律**：① 修鉴权时**后端校验为准**，菜单/按钮隐藏只是提示；② **同类校验不要写两份实现**（模块内联 `if role==` 与 `scope_service` 并存正是漂移来源）。

---

## 1. 高危（10 项）

### [ ] H-1. 默认口令 + 强制改密从未实现 + 教师默认密码=手机号（=登录名）
- **来源**：01-H-1、02-H1/H2/H3、03-H1/H2
- **位置**：`app/__init__.py:375-379`（`admin/admin123`、`must_change_pwd=False`，启动即重建）；`app/models/user.py:21`（字段）；`must_change_pwd` 全库 15 处命中**全为写入方**，无 `before_request` 读取；`app/modules/system/routes/users.py:277-280`（导入教师 `set_password(phone)`）；`app/forms/user_forms.py:24`、`app/forms/auth_forms.py:15-18`（口令仅 `min=6`）。
- **触发**：局域网任意人 `/login`；管理员未改 → `admin/admin123` 直登；教师账号 → 手机号/手机号（半公开）。
- **影响**：系统完全接管；批量教师账号稳定登录。`smoke_grades.py:321` 表明项目自身只把该字段当"标记"。
- **修复**：① 首启随机口令（`secrets` 生成）仅控制台输出一次，或强制 `must_change_pwd=True`；② 全局 `before_request`：`must_change_pwd` 为真时除改密/登出外 302 `/change-password`；③ 教师导入复用已有 `_generate_password()` 生成随机一次性口令；④ 口令策略 ≥8 位 + 弱口令黑名单；⑤ 部署文档 / README / Dockerfile 增加「首次登录改密」硬提示。
  ```python
  @app.before_request
  def _force_password_change():
      from flask_login import current_user
      if not current_user.is_authenticated or not current_user.must_change_pwd:
          return
      allowed = {'auth.change_password', 'auth.logout', 'static'}
      if request.endpoint in allowed:
          return
      return redirect(url_for('auth.change_password'))
  ```
- **验证**：`admin/admin123` 登录后 `GET /students/` 期望 302 到改密页（当前 200）；新建 `must_change_pwd=True` 账号应无法自由访问；导入手机号后用「手机号/同手机号」应无法登录。

### [ ] H-2. 一批敏感读/导出接口只有 `@login_required`，无权限与数据范围校验
- **来源**：01-H-2、03-H3、03-M5（统计回落）
- **位置**（逐条复核）：`students.py:19-25`（`/students/export`）、`:303-371`（`/students/search`）、`:45-47`（`/students/`，`role='teacher'` 不命中任何过滤分支）；`dormitory/routes/dashboard.py:274-281`（`/dormitory/export`）、`:181-192`（`/dormitory/search`）、`rooms.py:1259-1271`（`/report`、`/report/export`）；`statistics.py:209`（`statistics.py:31-32` 无权限组回落 `SCOPE_SCHOOL`）；`rooms.py:22,104,280,521,860,1063,1101,1473`。
- **触发**：任意登录账号（宿管、任课教师、无权限组 `staff`）直接访问上述 URL，或 `/students/export?columns=name&columns=grade&columns=class_name`。
- **影响**：全校学生名册（学号/姓名/年级/班级/选科/民族）与全校住宿分布外泄；身份证/手机等列仍受 `export_helpers.py:140-144` 独立权限保护。
- **修复**：逐个补 `@perm_required('students.view'|'students.export'|'dormitory.view'|'dormitory.assign')`；内联角色过滤统一替换为 `scope_service`；`_get_scope()` 无权限组改为拒绝而非 `SCHOOL`。
- **验证**：任课教师 `GET /students/export?columns=name` 期望 403（当前 200+xlsx）；无权限组账号 `GET /statistics/` 期望 403。

### [ ] H-3. 学生画像模块全链路无数据范围校验（可读且可写范围外学生）
- **来源**：01-H-3、03-H4
- **位置**：`portrait/routes/portrait.py:16-92,147-154,205-213,216-255`（仅 `perm_required`，不校验学号归属）；`portrait_service.py:323-334,375-391` 按传入 `student_no` 落库；授权 `app/__init__.py:150,170`（班主任/年级长有 `portrait.view/edit`）。
- **触发**：把 URL 学号换成任意学生，或 POST `/portrait/comments` 带 `student_no`。
- **影响**：读取全校画像/评语/事件/积分/宿舍/考勤（心理行为敏感数据），并可**写**范围外评语/事件。
- **修复**：所有画像入口前置 `student_in_scope(student_no)`（复用 `workbench/services/scope.py:57-95`）；写接口额外校验写权限范围。
- **验证**：年级长访问 `/portrait/<他年级学号>` 与 POST `/portrait/comments` 期望 403。

### [ ] H-4. 成绩模块考试/分层/考务路由缺少考试年级范围校验
- **来源**：01-H-4、03-H5
- **位置**：`grades/routes/exams.py:158-188`（`exam_detail`、`exam_scores_page`）、`:295-336`（`exam_delete` 仅密码二次确认、不校验年级）；`bands.py:115-385`（6 条路由 `check_exam_visible`/`_grade_options` 0 命中）；`exam_affairs.py:133-150` 等（33 条仅 `:111` 一处年级校验，其余 `abort(403)` 为子资源归属）。
- **触发**：2025 级年级长（持 `grades.edit`，`app/__init__.py:147`）访问 `/exams/<2024级考试id>`、`/exams/<id>/delete`、`/bands/save`、`/affairs/<aid>`。
- **影响**：跨年级查看/删除成绩、改分档线、操作考务编排。
- **修复**：入口统一 `scope_service.check_exam_visible(current_user, exam)`（`analysis.py:163`、`report.py:28`、`pivot.py:64`、`ai.py:274/310/414`、`export.py:82` 已示范）；考务按 `affair.grade` 校验。
- **验证**：教务员张三 `GET /grades/exams/<非授权年级考试id>` 期望 403（`smoke_permissions.py:120-129` 已断言服务层，搬到 HTTP 层）。

### [ ] H-5. 考务页面存储型 DOM XSS
- **来源**：01-H-5
- **位置**：`app/templates/grades/affairs/detail.html:455-456,478-484,555-561,613-618`（全 `innerHTML` 拼接，文件内**无任何转义函数**）；数据源 `exam_affairs.py:575-578`（`location`/`note`/`prefix` 仅 `.strip()` 入库）、`:636`；学生姓名/学号经 Excel 导入可控。
- **触发**：有 `grades.edit` 的账号（或能导入名单者）写入 `<img src=x onerror=...>`，任何打开该考务批次的会话即执行。
- **影响**：会话内任意 JS（无 CSP；`base.html:463-469` 全局注入 `X-CSRFToken` → 可受害者身份调全部 JSON 接口）。
- **修复**：拼接改 `textContent`/`escHtml`（`grades.js:316-319` 已有）；服务端对 `location/note/prefix` 做字符白名单；补 CSP。
- **验证**：写入 `<img src=x onerror=alert(1)>` 后刷新；或 grep 该模板确认 0 处转义。

### [ ] H-6. 通知人员多选存储型 XSS（`real_name`/`username` 未转义）
- **来源**：01-H-6
- **位置**：`notifications/list.html:395-401`（`u.name`/`u.uid` 拼 `innerHTML`）、`:440`（`onclick="removeUser('<uid>')"` 单引号串未转义）；源 `notifications/routes.py:241-263`；写入源 `users.py:68-70,277`。
- **触发**：有 `system.users` 权限者创建/导入姓名含 HTML 的用户 → 通知发布者检索到即执行（`:249` 要求 `_can_publish()`，命中面有限）。
- **影响**：跨用户存储型 XSS，控制发布者会话。
- **修复**：统一 `escHtml`；`:440` 改事件委托 + `dataset.uid`。
- **验证**：创建姓名 `<img src=x onerror=alert(1)>` 的用户后在发布页搜索。

### [ ] H-7. 往届查询站（:5001）：生产 debug + 认证不校验角色 + 共享密钥/用户表
- **来源**：01-H-7、02-H4、03-H8
- **位置**：`alumni_app/run.py:32`（`debug=True, host='0.0.0.0'`，Werkzeug 调试器 → PIN 爆破即 RCE）；`alumni_app/app/auth.py:18-29`、`alumni_app/app/routes/basic.py:19-27`（只验口令、**不校验 role/权限**，直读主库 `users`）；`alumni_app/config.py:27,50`（与主应用同 `data/.secret_key`；`:53` `alumni_session`）；`alumni_app/app/utils/crypto.py` 复用同 `data/.encryption_key`；`basic.py:102-108` 解密出完整身份证号（模板 `basic_search.html:69` 只渲染掩码）；`alumni_app/Dockerfile:13`（容器 `CMD` 直跑该调试入口）。
- **触发**：任意在职主站账号（含宿管、任课教师）用自己口令登录 `:5001`；同域 cookie 注入可复用签名会话（flask-login cookie 只签名不加密）。
- **影响**：往届生姓名/学号/班级/毕业学校/变迁记录外泄；debug 叠加则 RCE。
- **修复**：① `debug=False` + waitress，Docker `CMD` 同步改 gunicorn/waitress；② 登录加角色/独立权限白名单；③ 独立 `SECRET_KEY`（仅共享 `ENCRYPTION_KEY`）；④ 身份证只作掩码用途、不在上下文保留明文。
- **验证**：任课教师账号 POST `:5001/login` 期望 403（当前进入 `/`）；`grep -n "debug=True" alumni_app/run.py` 应为空；访问 `:5001` 触发异常不应返回调试页。

### [ ] H-8. `/static/uploads` 直链守卫可被大小写绕过（Windows），叠加附件类型不限 + 上传仅按扩展名
- **来源**：01-H-8、03-H6/H7、02-L1
- **位置**：`app/__init__.py:495-498`（`request.path.startswith('/static/uploads')`）；落盘 `form_service.py:428-434`；类型校验 `form_service.py:417-421`（**仅当配置了 `file_types` 才比对**，否则任意后缀）；`app/models/academic.py:233`（字段由创建者自由填写）；`points/routes.py:330-333`（仅按扩展名）；`config.py:41`（UPLOAD_FOLDER 在 static 下）。
- **触发**：Windows 下 `GET /static/Uploads/forms/<fid>/<sid>/<filename>`。实测：本机 FS 大小写不敏感（`os.path.isfile('.../Uploads/a.txt')` 对真实 `uploads/a.txt` 返回 `True`），Werkzeug 静态前缀大小写敏感、`<path:filename>` 原样透传 → 守卫放行、`send_from_directory` 命中。
- **影响**：任何拿到 URL 的人（含未登录）可下载学生材料，违背 `app/__init__.py:488-494`（PR#5 M4）；上传 `.html`/`.svg` 构成**未登录可触发的存储型 XSS**；落盘危险类型。
- **修复**：① 守卫改 `os.path.normcase(request.path).startswith('/static/uploads')`（并考虑合并斜杠）；② 按 TODO 把上传迁出 `static`（如 `instance/uploads`）；③ 上传扩展名白名单 + 危险类型拒绝 + magic/MIME 校验 + 下载强制 `attachment`。
- **验证**：`curl -i ".../static/uploads/forms/1/1/foo.html"` → 404；`curl -i ".../static/Uploads/forms/1/1/foo.html"` → 200（漏洞确认）；无类型限制表单题传 `x.html` 应被拒。

### [ ] H-9. BYOK 自定义 `base_url` 可控 → SSRF + API Key/成绩数据外发
- **来源**：01-H-9、02-H5
- **位置**：`grades/routes/ai.py:60-64`（仅校验 `http(s)://` 前缀与长度）；`grades/services/ai_service.py:222-241`（`base + '/chat/completions'` + `Authorization: Bearer <key>` + `urlopen`）；`ai_providers.py:100-105`（用户填写优先）；全局 Key 回落 `ai_service.py:204-211`。
- **触发**：持 `grades.view` 的用户保存 provider=custom + 任意 `base_url`（内网/环回），点"测试连接"或任一次分析；无个人 Key 时会把**全局 Key** 发往攻击者服务器。
- **影响**：内网探测（错误码/耗时可回显）+ 密钥与成绩数据外泄。
- **修复**：`base_url` 域名白名单（自定义需管理员审批）；解析后按 IP 段拦私网/环回/链路本地/云元数据（含 DNS 解析后校验，防 rebinding）；出站走受控代理并记录目标域名。
- **验证**：保存 `base_url=http://127.0.0.1:5000` 后点"测试连接"，本机 5000 应收到带 `Authorization` 的 `/chat/completions`；`http://169.254.169.254` 应被拒。

### [ ] H-10. 画像评语注入 JS 上下文（存储型 XSS）
- **来源**：03-H9
- **位置**：`app/templates/portrait/detail.html:189`（`onclick="editComment({{ c.id }}, \`{{ c.content|e }}\`, '{{ c.comment_type }}', '{{ c.term }}')"`）；`comment_type` 后端未白名单（`portrait/routes/portrait.py:76/102`）。
- **触发**：评语含反引号/`${...}`（`|e` 不转义这些）或 `comment_type/term` 含单引号（`&#39;` 交 JS 引擎前还原为 `'` 打断字符串）。
- **影响**：拥有 `portrait.edit` 写入、拥有 `portrait.view` 点击「编辑」即执行，窃取会话/CSRF token 并调全部 JSON 接口。
- **修复**：改 `{{ c.content|tojson }}` / `{{ c.comment_type|tojson }}`；`comment_type` 枚举白名单。
- **验证**：评语写 `${alert(document.cookie)}`，有 view 权限者点「编辑」→ 修复后不弹窗。

---

## 2. 中危（16 项）

### [ ] M-1. 审计日志对全体登录用户开放
- **来源**：01-M-1、03-M11
- **位置**：`system/routes/operation_logs.py:11-13`（仅 `login_required`）、`:50`（`User.query.all()`）、`:72-85`；同类 `dashboard.py:14-17`。
- **触发/影响**：任意登录账号访问 `/operation-logs/` → 全校操作轨迹（谁改了哪个学生/成绩/用户、导出行为、登录时间）泄露。
- **修复**：加 `@perm_required('system.users')` 或新增 `system.logs`；`User.query.all()` 收敛为日志中出现的用户。
- **验证**：任课教师 `GET /operation-logs/` 期望 403。

### [ ] M-2. 会话与认证强化缺失（会话固定 / 改密不停旧会话 / 无过期 / load_user 不查 is_active / Cookie Secure / 无 Session Protection）
- **来源**：01-M-2、03-M2/M3/M4、02-M2
- **位置**：`auth/routes.py:55`（`login_user` 前无 `session.clear()`）、`:70-76`（登出不清 session）、`:79-93`（改密不失效既有会话）；`extensions.py:8-13`（无 `session_protection`、无 `REMEMBER_COOKIE_*`）；`user.py:65-72`（`load_user` **不检查 `is_active`**）；`users.py:143-159`（禁用不踢会话）；`config.py:45`（`SESSION_COOKIE_SECURE=False`）；`config.py` 无 `PERMANENT_SESSION_LIFETIME`。
- **触发/影响**：权限撤销（禁用/改密/离职）不生效；HTTP 下 cookie 可被同网段嗅探；会话固定需同域 cookie 注入配合；无服务端空闲超时长期有效。
- **修复**：① 登录前 `session.clear()`；② `SESSION_PROTECTION='strong'` + 用户表 `session_version`（改密/禁用自增，`load_user` 比对）；③ `load_user` 校验 `is_active`；④ 生产 HTTPS 后开 `SESSION_COOKIE_SECURE=True` + HSTS；⑤ 设 `PERMANENT_SESSION_LIFETIME`（如 8h）并 `session.permanent=True`；⑥ 定期轮换 `SECRET_KEY` 使旧会话失效。
- **验证**：A 设备登录、B 设备改密后 A 继续访问期望 302；禁用后原会话期望失效；长时间空闲后请求应重新登录；HTTPS 下 `Set-Cookie` 含 `Secure`。

### [ ] M-3. 登录限流仅按 IP、驻留内存、无账号锁定
- **来源**：01-M-3、03-M1、02-L2
- **位置**：`auth/routes.py:14-16,41-49,63-65`（`login_attempts_{client_ip}` + `SimpleCache`）；`config.py` `CACHE_TYPE='simple'`。
- **触发/影响**：多 IP 分布式爆破、进程重启清零；配合 H-1 可稳定命中。
- **修复**：增加账号维度计数与阶梯延迟/临时锁定；失败/锁定入审计并可告警；限流状态持久化（Redis 等共享存储）；admin 等高价值账号额外保护（延迟/验证码）。
- **验证**：同一账号两个来源各试 9 次，第 10 次期望被锁。

### [ ] M-4. Excel/CSV 公式注入防护未全覆盖
- **来源**：01-M-4
- **位置**：工具已有 `export_helpers.py:14-43`（`xl_safe`/`xl_row`）；**未使用**：`export_helpers.py:161-171`、`:331-359`、`grades/routes/export.py:60-61`、`exam_affairs.py:898-930,1024-1059`、`dormitory/statistics.py:673-675`、`rooms.py:1438-1470`、`dashboard.py:375-381`；打包清单 CSV 未转义（`form_summary_service.py:914-923`）。
- **触发/影响**：姓名/班级/事由以 `=`/`+`/`-`/`@` 开头 → 导出文件在 Excel/WPS 打开即求值（DDE/外链）。
- **修复**：所有写单元格路径统一走 `xl_row`/`xl_safe`；CSV 同处理。
- **验证**：导入姓名 `=1+1` 后导出，修复后单元格应为文本。

### [ ] M-5. 身份证加密使用确定性 IV；解密失败静默当明文返回
- **来源**：01-M-5、03-M8、02-M1
- **位置**：`crypto.py:50-57`（`iv = sha256(plaintext)[:16]`）、`:83-85,98-100`（失败返回密文原文）；索引 `student.py:65`。
- **触发/影响**：密文等同弱哈希（身份证低熵可离线字典比对）；密钥轮换/数据异常时密文以"明文"形式流入页面与导出。
- **修复**：随机 IV（`encrypt_rand` 已有）+ 独立等值索引列（`HMAC-SHA256(key, 明文)`，即盲索引）；解密失败抛错并记日志（语义改为报错 + 审计）；**迁移需配套回填脚本**（旧确定性密文 → 随机 IV 密文，并回填盲索引列）。
- **验证**：同一身份证 `encrypt()` 两次，密文相同即确认；篡改密文后 `decrypt` 应报错；仅持库无密钥时无法用 `sha256(猜测)[:16] == iv` 离线校验。

### [ ] M-6. 备份、临时文件与缓存清理异常
- **来源**：01-M-6
- **位置**：`system/routes/grade_mgmt.py:69-85`（毕业备份 `system.db`/`dormitory.db`/`history.db` 到 `data/backups/`，**无保留策略/无清理**，好在无下载路由）；`academic/routes/form_summary.py:133-146`（`after_this_request` best-effort 删临时包，中断即残留）；`students.py:28-42,933-936`（`_import_errors` 仅被下载时惰性清理）。
- **触发/影响**：明文整库副本长期累积并随同步盘扩散（system.db 含口令哈希、身份证密文）；`stulink_pkg_*` 临时目录残留；磁盘缓慢增长。
- **修复**：备份保留窗口 + 受控目录 + 权限收敛；启动时清扫过期临时包；`_import_errors` 增加请求级清理阈值。
- **验证**：观察 `data/backups/` 增长；`ls $env:TEMP | findstr stulink_pkg_`。

### [ ] M-7. CSRF：alumni 站无防护；主应用 token 永不过期
- **来源**：01-M-7、02-M4、03-S2
- **位置**：`alumni_app/app/__init__.py:7-25`（未初始化 `CSRFProtect`，唯一 POST 为 `/login`）；`alumni_app/config.py:49-56`；主应用 `config.py:48-50`（`WTF_CSRF_TIME_LIMIT=None`、`WTF_CSRF_SSL_STRICT=False`）；主应用 `csrf.exempt` 0 命中（已确认）。
- **触发/影响**：alumni 登录 CSRF（危害低）；主应用 token 泄露后长期可用。
- **修复**：alumni 启用 `CSRFProtect` + 登录表单加 token；主应用设置 `WTF_CSRF_TIME_LIMIT`（如 8h）。
- **验证**：对 `:5001/login` 提交无 token 的跨站表单，观察是否登录成功；过期旧 token 提交应被拒。

### [ ] M-8. Docker 镜像把用户上传材料打包进镜像
- **来源**：01-M-8
- **位置**：`Dockerfile:15`（`COPY app/ ./app/`）+ `.dockerignore`（排除 `data`、`logs`，**未排除** `app/static/uploads`）。
- **触发/影响**：构建时若目录已有学生材料 → 材料进入镜像层，镜像分发即泄露。
- **修复**：`.dockerignore` 加 `app/static/uploads`；配合 H-8 把上传迁出 `app/`。
- **验证**：`docker run --rm <img> ls /app/app/static/uploads`。

### [ ] M-9. 无 CSP 头 + `X-XSS-Protection` 已废弃
- **来源**：01-M-9、03-S1、02-L3
- **位置**：`app/__init__.py:519-526`。
- **触发/影响**：H-5/H-6/H-8/H-10 任一 XSS 成功执行时无第二道防线。
- **修复**：增加 `Content-Security-Policy`（至少 `default-src 'self'`，脚本用 nonce/hash，按需放宽内联样式）；移除 `X-XSS-Protection`。
- **验证**：`curl -I` 检查响应头含 CSP。

### [ ] M-10. 内联事件处理器 JS 上下文 XSS（单引号未转义）
- **来源**：03-M6
- **位置**：`system/students/list.html:169`、`dormitory/assignments/manage.html:201,205,206`、`grades/affairs/list.html:59`、`system/users/list.html:82`、`system/dictionary/list.html:69`、`academic/schedule_manage.html:111,117,124,146`。
- **触发/影响**：`{{ x|e }}` 的 `&#39;` 在交 JS 引擎前还原为 `'` 打断字符串，姓名/名称含 `'`（如 O'Brien）即注入；部分处完全未转义（`bd.student.name`）。
- **修复**：全部改 `|tojson`；`confirm` 文本用 `|tojson`。
- **验证**：姓名改 `x');alert(1);//` 触发按钮/拖拽 → 修复后不弹窗。

### [ ] M-11. 通知 `link_url` 无协议白名单（javascript: XSS）
- **来源**：03-M7
- **位置**：`notifications/routes.py:110`（仅 `strip()`）；模板 `notifications/list.html:123`、`detail.html:87`（`href="{{ n.link_url }}"`）。
- **触发**：有发布权限者将 `link_url` 设为 `javascript:alert(document.cookie)`，他人点击即执行。
- **修复**：仅允许 `http://`/`https://`，拒绝 `javascript:`/`data:`。
- **验证**：发布 `javascript:alert(1)` 链接，他人点击 → 修复后拒绝保存。

### [ ] M-12. 堆栈回溯 / 内部异常文本经响应回传前端
- **来源**：03-M9/M10
- **位置**：`dormitory/services/room_assignment_v8.py:480-485`（`traceback.format_exc()` 进 `logs`）+ `rooms.py:1026-1060`（`jsonify(result)` 回传含堆栈 `logs`）；`rooms.py:361,1599`、`class_profile.py:168`、`form_summary.py:150,171,191,218,249`、`points/routes.py:337`、`users.py:300`、`students.py:911,1278`、`grade_mgmt.py:339,439` 等多处 `str(e)`。
- **触发/影响**：攻击者构造异常从响应获绝对路径/源码行/SQL；异常文本泄露文件路径/表结构辅助攻击。
- **修复**：生产环境剥离 `[TRACE]` 堆栈仅留摘要；向用户返回友好文案，详细异常仅记服务端日志（结构化、不含敏感字段）。
- **验证**：触发自动分配异常，响应 `logs` 不应含完整堆栈；触发异常操作响应不应含 SQL/路径。

### [ ] M-13. 开放重定向校验过宽
- **来源**：01-L-1、02-M3
- **位置**：`auth/routes.py:19-30`（放行 `/\evil.com`；`netloc == ''` 放行 `javascript:`/`data:`）。
- **触发/影响**：登录 `next` 参数可被用于钓鱼外跳；`javascript:`/`data:` 协议可触发。
- **修复**：改用 `flask.helpers.safe_join`，仅允许 `scheme==''` 且 `netloc==''` 的相对路径、拒绝 `\` 开头；`/login?next=//evil.com` 登录后不应外跳。
- **验证**：`/login?next=/\外部域` 登录后不得跳转外域。

### [ ] M-14. 成绩证明公开核验端点无鉴权暴露快照
- **来源**：02-M5
- **位置**：`grades/routes/student_query.py:65-86`（`/cert/<code>`）。
- **触发/影响**：未登录访问有效 code，页面可能含成绩明文（真/伪/已作废 + 摘要本应走登录后 `cert_print`）。
- **修复**：`/cert/<code>` 仅返回真/伪/已作废 + 摘要哈希；完整明细走登录后 `cert_print`。
- **验证**：未登录访问有效 code，页面不含成绩明文。

### [ ] M-15. 密码复杂度过低 + 无弱口令黑名单
- **来源**：03-M13、02-L4、01-H-1④
- **位置**：`auth_forms.py`、`user_forms.py`（仅 `min=6`）。
- **触发/影响**：弱口令易被爆破，叠加 H-1/H-3 限流缺失放大。
- **修复**：≥8 位 + 复杂度策略 + 弱口令黑名单；禁止用姓名/手机号做密码。
- **验证**：设 `123456` 应被拒。

### [ ] M-16. 上传目录置于 static 下的设计缺陷
- **来源**：03-M12、01-H-8/R-3
- **位置**：`config.py:41` + `app/__init__.py:493-494`（已有 TODO）；`app/__init__.py:488-498` 直链守卫。
- **触发/影响**：即便修复 H-8 大小写，把用户内容托管在 Web 静态目录仍是脆弱设计（任一未来误配置即暴露）。
- **修复**：整体迁到 `instance/uploads` 等静态目录之外，下载统一走带鉴权路由，并同步迁移 `FormAnswer.file_path`。
- **验证**：`uploads` 不在 `static_folder` 下。

---

## 3. 低危（12 项）

### [ ] L-1. 导出日志来源 IP 可伪造
- **来源**：01-L-2
- **位置**：`export_helpers.py:203,394`（`ip_address=args.get('ip_address','')`）。
- **修复**：统一取 `request.remote_addr`。
- **验证**：导出日志 IP 应为真实客户端地址。

### [ ] L-2. 日志落盘与脱敏
- **来源**：01-L-3
- **位置**：`bands.py:26-46`（`logs/bands.log`，已被 `.gitignore:56-57` 忽略）；`helpers.py:283,321` 异常日志可能含业务上下文。
- **修复**：脱敏 + 目录权限收敛；关键安全事件告警。

### [ ] L-3. PDF 生成的类 HTML 解析
- **来源**：01-L-4
- **位置**：`affair_pdf.py:150-175`（学生姓名/班级进 reportlab `Paragraph`，会解释 `<img src=...>`）。
- **修复**：`escape()` 后再构造。
- **验证**：导入姓名含标签的名单，生成的 PDF 不应内嵌图片/外链。

### [ ] L-4. 打包阈值与导入草稿并发
- **来源**：01-L-5
- **位置**：`form_summary.py:133-146`（>500MB 才落盘，大包内存打包有 OOM 风险）；`timetable.py:21-29`（进程内 dict 无锁）。
- **修复**：统一阈值/落盘策略 + 加锁。

### [ ] L-5. 异常静默吞没，缺结构化安全日志（主应用 + alumni）
- **来源**：01-L-6、02-L5
- **位置**：`alumni_app/app/routes/basic.py:29-30`、`alumni_app/app/auth.py:27-28`（`except Exception: pass`）；主应用同类静默吞没（如 `app/modules/academic/routes/form_summary.py:142-143` 的 `except Exception: pass`）。
- **修复**：记日志（结构化、不含敏感字段），认证异常不得静默；关键安全事件告警。

### [ ] L-6. 依赖未锁版本
- **来源**：01-L-7
- **位置**：`requirements.txt:1-10`（全部 `>=`）。
- **修复**：锁定版本 + CI 加 `pip-audit`。

### [ ] L-7. 新用户初始密码明文回显
- **来源**：01-L-8
- **位置**：`users.py:82-83`（flash 明文口令 → 进响应 HTML 与签名 cookie）；`users.py:162-171` 重置后明文被丢弃属可用性缺陷。
- **修复**：一次性展示区 + 首登强制改密（关联 H-1）。

### [ ] L-8. 导入 Excel 仅校验扩展名（无 Content-Type/Magic/Macro 校验）
- **来源**：03-L1（注：上传扩展名见 H-8；此处指导入解析）
- **位置**：`students.py:621`、`users.py:223`、`exam_affairs.py:243,504` 等 `endswith(('.xlsx','.xls'))`。
- **修复**：加 Content-Type/Magic 校验并限制解析规模（`openpyxl` 不执行宏，无 RCE，风险低）。

### [ ] L-9. `download_name` 反射 DB 取值到 Content-Disposition（潜在 CRLF 头注入）
- **来源**：03-L2
- **位置**：`exam_affairs.py:907`、`export.py:146`、`workbench/routes/attendance.py:143`。
- **修复**：显式清洗文件名（防 CRLF 头注入）。

### [ ] L-10. 前端 `escapeAttr` 未转义 `&` 与 `'`
- **来源**：03-L3
- **位置**：`app/static/js/academic_forms.js:293-295`。
- **修复**：补全转义 `&` 与 `'`（自 XSS，影响有限）。

### [ ] L-11. `.secret_key` 创建未设 `chmod 600`
- **来源**：03-L4
- **位置**：`config.py:22-28`。
- **修复**：创建后 `os.chmod(0o600, ...)`（多用户机文件权限）。

### [ ] L-12. 用户输入字段无最大长度限制
- **来源**：03-L5
- **位置**：姓名/备注/通知/评语等 `request.form.get(...).strip()` 多处。
- **修复**：加长度上限（功能性/存储风险，低安全危）。

---

## 4. 建议优化（R/S 合并）

- [ ] **R-1. 统一数据范围装饰器**：新增 `@scope_required`（内部走 `scope_service`/`student_in_scope`），维护"路由 → 权限 + 范围"清单，杜绝 H-2/H-3/H-4 类遗漏。（01-R-1）
- [ ] **R-2. 前端渲染规约**：抽公共 `escHtml`（`grades.js` 已有）；CR 禁止新增裸 `innerHTML`；模板禁止新增 `|safe`（现状 `base.html:441`、`_cert_body.html:60` 均不可注入，保持）。（01-R-2）
- [ ] **R-3. 上传体系改造**：迁出 `static` + 扩展名白名单 + 随机名 + 强制 `attachment` + 单用户配额；同步迁移 `FormAnswer.file_path`（关联 H-8/M-16）。（01-R-3、03-M12）
- [ ] **R-4. 口令与账号生命周期**：落地强制改密（H-1）、口令策略（≥8 + 黑名单）；重置后一次性展示（关联 L-7）。（01-R-4）
- [ ] **R-5. 会话与限流强化**：见 M-2/M-3；admin 等高价值账号增加额外保护。（01-R-5）
- [ ] **R-6. 加密升级**：见 M-5；`decrypt` 失败语义改为报错 + 审计。（01-R-6）
- [ ] **R-7. 部署与传输**：生产 HTTPS + `SESSION_COOKIE_SECURE=True` + HSTS + CSP（关联 M-2/M-9/H-7）。（01-R-7）
- [ ] **R-8. 安全回归测试化**：新增 `tests/sec_regression.py`（沿用 `smoke_permissions.py:17-31` 临时库套路），把 H-1~H-10、M-1 写成 403/404/302 断言，提交前必跑；CI 加正则扫描（禁 `text(f"...")`、`execute(f"SELECT ...{request"`、模板新增 `|safe`/裸 `innerHTML`）。（01-R-8、03-S6）
- [ ] **R-9. 定期轮换 SECRET_KEY** 并使旧会话失效（关联 M-2）。（03-S3）
- [ ] **R-10. `_asset_mtime` 路径拼接潜在任意文件读**：保持仅限已知静态资源，勿改为接收请求参数。（03-S4）
- [ ] **R-11. `scripts/` 标识符拼接 DDL 加白名单**：表/列名来自 CLI 参数或硬编码，非 Web 暴露，纵深防御。（03-S5）

---

## 5. 回归红线（改动后必跑）

1. **SQL**：新增 raw SQL 必须绑定参数；`ORDER BY`/`LIMIT`/表名不得来自请求（本轮扫描：0 违规，勿回退）。
2. **文件**：`send_file`/`send_from_directory`/`open()` 的路径不得含 `request.*`；附件下载保持 `forms.py:481-500` 的结构校验 + `basename` + `commonpath`。
3. **CSRF**：不得新增 `csrf.exempt`；新增 POST/JSON 接口必须能在带 `X-CSRFToken` 的 fetch 下工作。
4. **模板**：不得新增 `|safe`/`Markup(用户输入)`/`render_template_string`；JS 拼接用户数据必须先 `esc`；新增导出必须走 `xl_safe`/`xl_row`（M-4）。
5. **权限**：新增路由必须带 `@perm_required` + 明确数据范围；`@login_required` 单独使用需在 PR 说明理由。
6. **上传**：新增上传必须 `secure_filename` + 白名单扩展名 + 不落 `static/`（H-8）。
7. **会话/认证**：改密/禁用必须使旧会话失效（M-2）；新增 `next` 跳转必须 `safe_join`（M-13）。

---

## 6. 本轮确认"无问题"（勿回退）

| 项 | 结论 | 证据 |
|---|---|---|
| SQL 注入 | 无：raw SQL 全部参数化（`helpers.py:303`、`grade_mgmt.py` 18 处、`dormitory/statistics.py` 6 处、`students.py:195/209`、alumni 若干）；`ORDER BY/LIMIT/表名` 均常量；导出列白名单 + `getattr` 不进 SQL | 扫描 + 人工复核 |
| 路径穿越 / 任意文件读写 | 无：`os.path.join` 无请求参数；无 `?path=`/`?file=`；无 `render_template` 变量名（无 SSTI）；唯一 `file.save` 路径受控 | — |
| 命令执行 / 反序列化 | 无：`eval/exec/pickle/subprocess/os.system` 0 命中 | — |
| ZIP slip / XML 解析 | 无：`openpyxl` 全内存解析；`zipfile` 仅生成，条目名 `_sanitize` + 去重 | — |
| CSRF（主应用） | 有效：全局 `CSRFProtect`，`csrf.exempt` 0 命中；`base.html:463-469` 统一注入 token | 见 M-7 两点弱化 |
| 密码哈希 | 安全：`werkzeug.generate_password_hash`（PBKDF2-SHA256，默认盐+迭代）（`user.py:25-29`） | — |
| 上传落盘命名 | 安全：`secure_filename` + uuid 前缀防覆盖（`form_service.py:424-434`） | 问题在访问控制（H-8） |
| 硬编码敏感信息 | 无：`SCHOOL_NAME` 默认化名占位；密钥均环境变量/文件 | — |
| 证书防伪 | HMAC-SHA256（`cert_sign.py:65-73`）+ 公开核验限流（`student_query.py:71-80`） | — |
| 整体确认项（02 对照） | SQL 注入 / 越权(`perm_required`/`role_required` + `scope.py` 年级/班级数据范围 + `check_exam_visible`/`student_in_scope` + AI 报告按 `user_id` 隔离 + 材料包需 `academic.edit`) / 存储型 XSS(Jinja2 默认转义+`nl2br`先转义+前端统一`esc`+AI Markdown 白名单) / 路径穿越(`/static/uploads` 封禁+`form_file` `basename`+`commonpath`) / 密钥管理(`.gitignore` 已排除 `data/`、`.secret_key`、`.encryption_key`、`app/static/uploads/`、`*.log`、`*.bak-*`) | — |

---

## 7. 与旧审计（docs/安全审计报告_20260807.md）的差异

- 旧报告结论"访问控制 ✅ 通过"仅对 v1.7.0 成立；v1.7.0→v1.17.0 新增的考务/画像/通知/导出路由未同步权限与范围校验，形成 H-2/H-3/H-4。
- 旧报告"`must_change_pwd` 已预留强制改密能力"至今仍是死代码（H-1）。
- 旧报告 3 项提示（`SESSION_COOKIE_SECURE=False`、`WTF_CSRF_TIME_LIMIT=None`、备份策略）全部仍在（M-2/M-7/M-6）。
- 旧报告未覆盖：双站点认证边界（H-7）、上传目录直链绕过（H-8）、BYOK SSRF（H-9）、前端 DOM XSS（H-5/H-6/H-10/M-10/M-11）。

---

## 8. 处置顺序总览（合并 01 修复路线 + 02 P0–P3 + 03 修复路线）

| 优先级 | 项 | 一句话 |
|---|---|---|
| **P0 立即（阻断接管/泄露）** | H-1, H-7, H-8, H-9, H-10 | 默认/手机号弱口令 + 强制改密缺失 / alumni debug+RCE / uploads 直链绕过+XSS / BYOK SSRF / 画像评语 XSS |
| **P1 尽快（凭据/传输/越权收敛）** | H-2, H-3, H-4, H-5, H-6, M-1, M-2, M-5, M-14 | 敏感读导出越权 / 画像 IDOR / 成绩跨年级 / 考务+通知 DOM XSS / 日志全员可读 / 会话强化 / 身份证确定性 IV / 成绩证明核验端点 |
| **P2 计划内（纵深防御）** | M-3, M-4, M-6, M-7, M-9, M-10, M-11, M-12, M-13, M-15, M-16 | 限流/公式注入/备份清理/CSRF时效/CSP/堆栈回传/link_url XSS/内联XSS/开放重定向/口令策略/上传迁出 |
| **P3 低危与优化** | L-1~L-12, R-1~R-11 | 日志IP伪造/脱敏/PDF/并发/静默/依赖锁/口令回显/导入校验/头注入/转义/文件权限/长度 + 统一范围装饰器/渲染规约/上传改造/生命周期/加密/部署/回归测试化 |

> 完成一项即在对应条目标记 `[x]` 并补日期与改动点；禁止删除历史结论，作废用删除线。

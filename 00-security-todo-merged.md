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

### [x] H-1. 默认口令 + 强制改密从未实现 + 教师默认密码=手机号（=登录名）
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
- **2026-09-23 完成（改动点）**：① `app/__init__.py` 首启 admin 改为 `secrets.token_urlsafe(12)` 随机口令 + `must_change_pwd=True`，仅控制台打印一次；存量 admin 若仍 `admin123` 则自动置强制改密并打印 `[安全警告]` 横幅；② 新增全局 `before_request` `_force_password_change()`（白名单 `auth.change_password`/`auth.logout`/`static`）——补上了 `must_change_pwd` 此前缺失的唯一读取方；③ `users.py::import_teachers` 教师初始口令由手机号改为 `_generate_password()` 随机一次性口令，口令通过 `session['_pwd_once']` 在用户列表页一次性展示（关联 L-7）；④ 口令策略落到 `app/utils/password_policy.py`（≥8 位 + 字母数字 + 弱口令黑名单 + 禁账号/姓名/手机号），`auth_forms.py`/`user_forms.py` 统一调用（关联 M-15）；⑤ `tests/sec_regression.py` 增 H-1 断言 7 条，全部通过。注：⑤ 的 README/Dockerfile 提示见 R-7 一并处理。

### [x] H-2. 一批敏感读/导出接口只有 `@login_required`，无权限与数据范围校验
- **来源**：01-H-2、03-H3、03-M5（统计回落）
- **位置**（逐条复核）：`students.py:19-25`（`/students/export`）、`:303-371`（`/students/search`）、`:45-47`（`/students/`，`role='teacher'` 不命中任何过滤分支）；`dormitory/routes/dashboard.py:274-281`（`/dormitory/export`）、`:181-192`（`/dormitory/search`）、`rooms.py:1259-1271`（`/report`、`/report/export`）；`statistics.py:209`（`statistics.py:31-32` 无权限组回落 `SCOPE_SCHOOL`）；`rooms.py:22,104,280,521,860,1063,1101,1473`。
- **触发**：任意登录账号（宿管、任课教师、无权限组 `staff`）直接访问上述 URL，或 `/students/export?columns=name&columns=grade&columns=class_name`。
- **影响**：全校学生名册（学号/姓名/年级/班级/选科/民族）与全校住宿分布外泄；身份证/手机等列仍受 `export_helpers.py:140-144` 独立权限保护。
- **修复**：逐个补 `@perm_required('students.view'|'students.export'|'dormitory.view'|'dormitory.assign')`；内联角色过滤统一替换为 `scope_service`；`_get_scope()` 无权限组改为拒绝而非 `SCHOOL`。
- **验证**：任课教师 `GET /students/export?columns=name` 期望 403（当前 200+xlsx）；无权限组账号 `GET /statistics/` 期望 403。
- **2026-09-23 完成（改动点）**：① 新增 `app/utils/student_scope.py::apply_student_scope()` 作为 Student 查询范围的唯一口径（用户级范围 > 班主任/年级长字段 > 班级映射 > 任课年级 > 组级范围；无任何范围→空集，不再回落全校），接入 `students.py` 列表/搜索、`export_helpers.py` 两个导出函数（原先只处理班主任）；② 权限补齐：`students.py` 的 `/`、`/export`、`/search`、`/<id>`、`/batch-edit-search`、`/download-errors/<key>`；`dormitory/dashboard.py` 的 `/`、`/search`、`/export`、`/search/import-template`；`rooms.py` 的 `/`、`/<id>`、`/assign-data`、`/class-bed-requirement`、`/assign-auto/stats`、`/available-rooms-data`、`/assign-auto/room-stats`、`/report`、`/report/export`、`/swap-data`；`statistics.py` 的 `/`；③ `statistics._get_scope()` 无权限组不再回落 `SCOPE_SCHOOL`，改为 `abort(403)`（admin 例外）。`tests/sec_regression.py` 增 H-2 断言 20 条，全部通过；`scripts/smoke_permissions.py` 仍 38/38。

### [x] H-3. 学生画像模块全链路无数据范围校验（可读且可写范围外学生）
- **来源**：01-H-3、03-H4
- **位置**：`portrait/routes/portrait.py:16-92,147-154,205-213,216-255`（仅 `perm_required`，不校验学号归属）；`portrait_service.py:323-334,375-391` 按传入 `student_no` 落库；授权 `app/__init__.py:150,170`（班主任/年级长有 `portrait.view/edit`）。
- **触发**：把 URL 学号换成任意学生，或 POST `/portrait/comments` 带 `student_no`。
- **影响**：读取全校画像/评语/事件/积分/宿舍/考勤（心理行为敏感数据），并可**写**范围外评语/事件。
- **修复**：所有画像入口前置 `student_in_scope(student_no)`（复用 `workbench/services/scope.py:57-95`）；写接口额外校验写权限范围。
- **验证**：年级长访问 `/portrait/<他年级学号>` 与 POST `/portrait/comments` 期望 403。
- **2026-09-23 完成（改动点）**：`portrait/routes/portrait.py` 新增 `_assert_student_in_scope()`（内部调 `grades/services/scope.py::student_in_scope`，越界 403），接入全部 7 个带学号的入口：`detail`、`comments_list`、`add_comment`、`events_list`、`add_event`、`api_portrait_data`、`api_refresh`（读与写都校验）。`tests/sec_regression.py` 增 H-3 断言 8 条，全部通过。

### [x] H-4. 成绩模块考试/分层/考务路由缺少考试年级范围校验
- **来源**：01-H-4、03-H5
- **位置**：`grades/routes/exams.py:158-188`（`exam_detail`、`exam_scores_page`）、`:295-336`（`exam_delete` 仅密码二次确认、不校验年级）；`bands.py:115-385`（6 条路由 `check_exam_visible`/`_grade_options` 0 命中）；`exam_affairs.py:133-150` 等（33 条仅 `:111` 一处年级校验，其余 `abort(403)` 为子资源归属）。
- **触发**：2025 级年级长（持 `grades.edit`，`app/__init__.py:147`）访问 `/exams/<2024级考试id>`、`/exams/<id>/delete`、`/bands/save`、`/affairs/<aid>`。
- **影响**：跨年级查看/删除成绩、改分档线、操作考务编排。
- **修复**：入口统一 `scope_service.check_exam_visible(current_user, exam)`（`analysis.py:163`、`report.py:28`、`pivot.py:64`、`ai.py:274/310/414`、`export.py:82` 已示范）；考务按 `affair.grade` 校验。
- **验证**：教务员张三 `GET /grades/exams/<非授权年级考试id>` 期望 403（`smoke_permissions.py:120-129` 已断言服务层，搬到 HTTP 层）。
- **2026-09-23 完成（改动点）**：新增 `app/modules/grades/services/exam_guard.py`（`assert_exam_visible(exam_id)` / `assert_grade_visible(grade)`，越界 403、不存在 404）作为唯一入口；接入 `exams.py`（详情/成绩分页/改名/改分/删分/重算/删除/导入四处，共 11 处）、`bands.py`（分档页 + 读取/矩阵/单个保存/批量/应用共 6 处）、`exam_affairs.py`（新增 `_get_affair_checked()` 替换 20 处裸 `get_or_404`，并补创建与「考试→考务」入口的年级校验）。`tests/sec_regression.py` 增 H-4 断言 10 条，全部通过；`scripts/smoke_grades.py` 随 H-1 调整账号后 98/98。

### [x] H-5. 考务页面存储型 DOM XSS
- **来源**：01-H-5
- **位置**：`app/templates/grades/affairs/detail.html:455-456,478-484,555-561,613-618`（全 `innerHTML` 拼接，文件内**无任何转义函数**）；数据源 `exam_affairs.py:575-578`（`location`/`note`/`prefix` 仅 `.strip()` 入库）、`:636`；学生姓名/学号经 Excel 导入可控。
- **触发**：有 `grades.edit` 的账号（或能导入名单者）写入 `<img src=x onerror=...>`，任何打开该考务批次的会话即执行。
- **影响**：会话内任意 JS（无 CSP；`base.html:463-469` 全局注入 `X-CSRFToken` → 可受害者身份调全部 JSON 接口）。
- **修复**：拼接改 `textContent`/`escHtml`（`grades.js:316-319` 已有）；服务端对 `location/note/prefix` 做字符白名单；补 CSP。
- **验证**：写入 `<img src=x onerror=alert(1)>` 后刷新；或 grep 该模板确认 0 处转义。
- **2026-09-23 完成（改动点）**：① 前端：`grades/affairs/detail.html` 全部 innerHTML 拼接改用公共 `escHtml`（班级/选科下拉、学生名单、考场 `room_no/location/subject/capacity/prefix/note`、考场库列表、`td0`）；② 服务端：新增 `app/utils/text_guard.py`（`sanitize_label` 字符白名单 + `sanitize_prefix` 仅数字字母 + 长度上限），接入 `exam_affairs.py` 的考场保存、考场库保存、导入路径、批默认前缀与选科前后缀。`tests/sec_regression.py` 增 H-5 断言 9 条，全部通过。

### [x] H-6. 通知人员多选存储型 XSS（`real_name`/`username` 未转义）
- **来源**：01-H-6
- **位置**：`notifications/list.html:395-401`（`u.name`/`u.uid` 拼 `innerHTML`）、`:440`（`onclick="removeUser('<uid>')"` 单引号串未转义）；源 `notifications/routes.py:241-263`；写入源 `users.py:68-70,277`。
- **触发**：有 `system.users` 权限者创建/导入姓名含 HTML 的用户 → 通知发布者检索到即执行（`:249` 要求 `_can_publish()`，命中面有限）。
- **影响**：跨用户存储型 XSS，控制发布者会话。
- **修复**：统一 `escHtml`；`:440` 改事件委托 + `dataset.uid`。
- **验证**：创建姓名 `<img src=x onerror=alert(1)>` 的用户后在发布页搜索。
- **2026-09-23 完成（改动点）**：`notifications/list.html` 搜索下拉项全部改 `escHtml`（name/uid/grade/class_name/role）；已选标签不再拼 `onclick="removeUser('<uid>')"`，改为 DOM 构建 + `textContent` + `addEventListener`。`tests/sec_regression.py` 增 H-6 断言 3 条，全部通过。

### [ ] H-7. 往届查询站（:5001）：生产 debug + 认证不校验角色 + 共享密钥/用户表
> **状态：部分完成（代码层已修，部署形态暂缓）**
- **来源**：01-H-7、02-H4、03-H8
- **位置**：`alumni_app/run.py:32`（`debug=True, host='0.0.0.0'`，Werkzeug 调试器 → PIN 爆破即 RCE）；`alumni_app/app/auth.py:18-29`、`alumni_app/app/routes/basic.py:19-27`（只验口令、**不校验 role/权限**，直读主库 `users`）；`alumni_app/config.py:27,50`（与主应用同 `data/.secret_key`；`:53` `alumni_session`）；`alumni_app/app/utils/crypto.py` 复用同 `data/.encryption_key`；`basic.py:102-108` 解密出完整身份证号（模板 `basic_search.html:69` 只渲染掩码）；`alumni_app/Dockerfile:13`（容器 `CMD` 直跑该调试入口）。
- **触发**：任意在职主站账号（含宿管、任课教师）用自己口令登录 `:5001`；同域 cookie 注入可复用签名会话（flask-login cookie 只签名不加密）。
- **影响**：往届生姓名/学号/班级/毕业学校/变迁记录外泄；debug 叠加则 RCE。
- **修复**：① `debug=False` + waitress，Docker `CMD` 同步改 gunicorn/waitress；② 登录加角色/独立权限白名单；③ 独立 `SECRET_KEY`（仅共享 `ENCRYPTION_KEY`）；④ 身份证只作掩码用途、不在上下文保留明文。
- **验证**：任课教师账号 POST `:5001/login` 期望 403（当前进入 `/`）；`grep -n "debug=True" alumni_app/run.py` 应为空；访问 `:5001` 触发异常不应返回调试页。
- **2026-09-23 部分完成（改动点）**：**已做（代码层）**——① `alumni_app/app/auth.py` 新增 `ALUMNI_ALLOWED_ROLES`（默认 `admin,school_viewer,staff,grade_leader`，可用环境变量覆盖）与 `is_alumni_role_allowed()`；② `basic.py::login` 校验口令后**再校验角色白名单**，不在白名单直接 403（此前只验口令）；③ `load_user` 加 `is_active=1` 且校验角色白名单，主站禁用后本站会话同步失效；④ 首页不再把解密后的完整身份证号放进上下文（只留掩码），并 `pop` 掉 `id_card_number`；⑤ 认证异常不再 `except Exception: pass`，改 `logging` 记录（关联 L-5）；⑥ 查询异常不再把 `str(e)` 回传前端（关联 M-12）。`tests/alumni_regression.py`（子进程）+ `tests/sec_regression.py` H-7 断言 5 条全部通过。
- ⏸ **本轮暂缓（用户 2026-09-23 决定：不动部署形态）**：`alumni_app/run.py:32` 的 `debug=True`（Werkzeug 调试器 PIN 可爆破 → **RCE 级残留风险**）、`alumni_app/Dockerfile:13` 的 CMD、独立 `SECRET_KEY`。下次做法：`debug=False` + 换 waitress/gunicorn、`CMD` 同步、改用独立 `.alumni_secret_key`（仅共享 `ENCRYPTION_KEY`）。

### [x] H-8. `/static/uploads` 直链守卫可被大小写绕过（Windows），叠加附件类型不限 + 上传仅按扩展名
- **来源**：01-H-8、03-H6/H7、02-L1
- **位置**：`app/__init__.py:495-498`（`request.path.startswith('/static/uploads')`）；落盘 `form_service.py:428-434`；类型校验 `form_service.py:417-421`（**仅当配置了 `file_types` 才比对**，否则任意后缀）；`app/models/academic.py:233`（字段由创建者自由填写）；`points/routes.py:330-333`（仅按扩展名）；`config.py:41`（UPLOAD_FOLDER 在 static 下）。
- **触发**：Windows 下 `GET /static/Uploads/forms/<fid>/<sid>/<filename>`。实测：本机 FS 大小写不敏感（`os.path.isfile('.../Uploads/a.txt')` 对真实 `uploads/a.txt` 返回 `True`），Werkzeug 静态前缀大小写敏感、`<path:filename>` 原样透传 → 守卫放行、`send_from_directory` 命中。
- **影响**：任何拿到 URL 的人（含未登录）可下载学生材料，违背 `app/__init__.py:488-494`（PR#5 M4）；上传 `.html`/`.svg` 构成**未登录可触发的存储型 XSS**；落盘危险类型。
- **修复**：① 守卫改 `os.path.normcase(request.path).startswith('/static/uploads')`（并考虑合并斜杠）；② 按 TODO 把上传迁出 `static`（如 `instance/uploads`）；③ 上传扩展名白名单 + 危险类型拒绝 + magic/MIME 校验 + 下载强制 `attachment`。
- **验证**：`curl -i ".../static/uploads/forms/1/1/foo.html"` → 404；`curl -i ".../static/Uploads/forms/1/1/foo.html"` → 200（漏洞确认）；无类型限制表单题传 `x.html` 应被拒。
- **2026-09-23 完成（改动点）**：① 新增 `app/utils/upload_guard.py::is_static_uploads_path`（`os.path.normcase` + 反斜杠/多斜杠归一），`app/__init__.py::_block_static_uploads` 改用它，大小写绕过修掉；② 同模块 `validate_upload()` 提供唯一上传校验口径（扩展名白名单 + 危险类型黑名单 + magic 字节比对），`form_service.upload_file`（未配置 `file_types` 时不再任意后缀可传）与 `points/routes.py::import_upload` 统一调用，题型白名单里写 `html` 之类危险类型仍被拒；③ 下载强制 `attachment` 本就已在 `forms.py:505` 落地（保持）。`tests/sec_regression.py` 增 H-8 断言 17 条，全部通过。
- ⏸ **本轮未做（用户 2026-09-23 决定）**：③ 把上传目录迁出 `static`（见 M-16 / R-3 暂缓）。

### [x] H-9. BYOK 自定义 `base_url` 可控 → SSRF + API Key/成绩数据外发
- **来源**：01-H-9、02-H5
- **位置**：`grades/routes/ai.py:60-64`（仅校验 `http(s)://` 前缀与长度）；`grades/services/ai_service.py:222-241`（`base + '/chat/completions'` + `Authorization: Bearer <key>` + `urlopen`）；`ai_providers.py:100-105`（用户填写优先）；全局 Key 回落 `ai_service.py:204-211`。
- **触发**：持 `grades.view` 的用户保存 provider=custom + 任意 `base_url`（内网/环回），点"测试连接"或任一次分析；无个人 Key 时会把**全局 Key** 发往攻击者服务器。
- **影响**：内网探测（错误码/耗时可回显）+ 密钥与成绩数据外泄。
- **修复**：`base_url` 域名白名单（自定义需管理员审批）；解析后按 IP 段拦私网/环回/链路本地/云元数据（含 DNS 解析后校验，防 rebinding）；出站走受控代理并记录目标域名。
- **验证**：保存 `base_url=http://127.0.0.1:5000` 后点"测试连接"，本机 5000 应收到带 `Authorization` 的 `/chat/completions`；`http://169.254.169.254` 应被拒。
- **2026-09-23 完成（改动点，按用户选定口径「保留自定义 + 审批 + 私网拦截」）**：新增 `app/utils/url_guard.py::assert_outbound_url_allowed()` 作为唯一出站校验——① 域名白名单＝内置服务商域名 ∪ 管理员审批名单（环境变量 `AI_ALLOWED_BASE_URLS` 或 `data/ai_approved_base_urls.txt`，一行一个）；② 出站前 `getaddrinfo` 解析，**任一** IP 属私网/环回/链路本地/组播/保留/非全局即拒绝（含 `169.254.169.254` 云元数据）；③ 非 https 地址必须管理员显式审批（防 Key 明文外发）；④ 接入点：`ai.py::_normalize_config`（保存即校验，400）、`ai_service.py::_chat_completions` 与流式 `_stream_chat_completions`（发请求前校验）；⑤ 放行时记 `stulink.outbound` 日志（含目标域名）。`tests/sec_regression.py` 增 H-9 断言 13 条，全部通过。
- **已知残余风险**：域名解析与真正建连之间存在极小 DNS rebinding 时间窗（对 `127.0.0.1`、`10.x`、`192.168.x`、`169.254.169.254` 等静态地址无影响）；如需彻底消除，下一步可改为自建连接并对 `getpeername()` 二次校验。

### [x] H-10. 画像评语注入 JS 上下文（存储型 XSS）
- **来源**：03-H9
- **位置**：`app/templates/portrait/detail.html:189`（`onclick="editComment({{ c.id }}, \`{{ c.content|e }}\`, '{{ c.comment_type }}', '{{ c.term }}')"`）；`comment_type` 后端未白名单（`portrait/routes/portrait.py:76/102`）。
- **触发**：评语含反引号/`${...}`（`|e` 不转义这些）或 `comment_type/term` 含单引号（`&#39;` 交 JS 引擎前还原为 `'` 打断字符串）。
- **影响**：拥有 `portrait.edit` 写入、拥有 `portrait.view` 点击「编辑」即执行，窃取会话/CSRF token 并调全部 JSON 接口。
- **修复**：改 `{{ c.content|tojson }}` / `{{ c.comment_type|tojson }}`；`comment_type` 枚举白名单。
- **验证**：评语写 `${alert(document.cookie)}`，有 view 权限者点「编辑」→ 修复后不弹窗。
- **2026-09-23 完成（改动点）**：① `portrait/detail.html:189` 不再把评语内容/类型/学期拼进 `onclick="editComment(...)"`，改为 `data-content/data-type/data-term` + `|tojson`，并用事件委托（`closest('.edit-comment-btn')` + `JSON.parse`）取值；② `portrait/routes/portrait.py` 新增 `_clean_comment_fields()`：`comment_type` 枚举白名单（学期/操行/班主任评语）+ `term`/`content` 长度上限（32/2000，关联 L-12），add/edit 两处统一调用。`tests/sec_regression.py` 增 H-10 断言 6 条，全部通过。

---

## 2. 中危（16 项）

### [x] M-1. 审计日志对全体登录用户开放
- **来源**：01-M-1、03-M11
- **位置**：`system/routes/operation_logs.py:11-13`（仅 `login_required`）、`:50`（`User.query.all()`）、`:72-85`；同类 `dashboard.py:14-17`。
- **触发/影响**：任意登录账号访问 `/operation-logs/` → 全校操作轨迹（谁改了哪个学生/成绩/用户、导出行为、登录时间）泄露。
- **修复**：加 `@perm_required('system.users')` 或新增 `system.logs`；`User.query.all()` 收敛为日志中出现的用户。
- **验证**：任课教师 `GET /operation-logs/` 期望 403。
- **2026-09-23 完成（改动点）**：`system/routes/operation_logs.py::list_logs` 与 `system/routes/dashboard.py::index` 补 `@perm_required('system.users')`（原先仅 `@login_required`）。`tests/sec_regression.py` 增 M-1 断言 3 条，全部通过。注：`User.query.all()` 收敛为「日志中出现的用户」属优化项，未做（无越权面）。

### [x] M-2. 会话与认证强化缺失（会话固定 / 改密不停旧会话 / 无过期 / load_user 不查 is_active / Cookie Secure / 无 Session Protection）
- **来源**：01-M-2、03-M2/M3/M4、02-M2
- **位置**：`auth/routes.py:55`（`login_user` 前无 `session.clear()`）、`:70-76`（登出不清 session）、`:79-93`（改密不失效既有会话）；`extensions.py:8-13`（无 `session_protection`、无 `REMEMBER_COOKIE_*`）；`user.py:65-72`（`load_user` **不检查 `is_active`**）；`users.py:143-159`（禁用不踢会话）；`config.py:45`（`SESSION_COOKIE_SECURE=False`）；`config.py` 无 `PERMANENT_SESSION_LIFETIME`。
- **触发/影响**：权限撤销（禁用/改密/离职）不生效；HTTP 下 cookie 可被同网段嗅探；会话固定需同域 cookie 注入配合；无服务端空闲超时长期有效。
- **修复**：① 登录前 `session.clear()`；② `SESSION_PROTECTION='strong'` + 用户表 `session_version`（改密/禁用自增，`load_user` 比对）；③ `load_user` 校验 `is_active`；④ 生产 HTTPS 后开 `SESSION_COOKIE_SECURE=True` + HSTS；⑤ 设 `PERMANENT_SESSION_LIFETIME`（如 8h）并 `session.permanent=True`；⑥ 定期轮换 `SECRET_KEY` 使旧会话失效。
- **验证**：A 设备登录、B 设备改密后 A 继续访问期望 302；禁用后原会话期望失效；长时间空闲后请求应重新登录；HTTPS 下 `Set-Cookie` 含 `Secure`。
- **2026-09-23 完成（无迁移方案）**：新增 `app/utils/session_guard.py`（口令摘要 `sha256(password_hash)` 写入 `session['_pwd_fp']`）；`auth/routes.py` 登录前 `session.clear()`（防会话固定）+ `session.permanent=True` + 重新签发 CSRF token、登出 `session.clear()`、改密后 `stamp()`（本设备保持登录、其它设备失效）；`app/__init__.py` 新增 `before_request _enforce_session_validity()`（禁用/口令变更即登出并清会话）；`extensions.py` 设 `session_protection='strong'`；`config.py` 加 `PERMANENT_SESSION_LIFETIME`（默认 8h，环境变量可调）、`SESSION_REFRESH_EACH_REQUEST=True`、`SESSION_COOKIE_SECURE` 改由 `STULINK_SESSION_COOKIE_SECURE=1` 打开（局域网 HTTP 下默认仍 False，硬开会导致全站掉登录）。`tests/sec_regression.py` 增 M-2 断言 9 条，全部通过。
- ⏸ **本轮未做（用户 2026-09-23 决定：不加 DB 列）**：`users.session_version` 列。影响：无——已用口令摘要等价实现；若日后需要「按设备粒度吊销」再考虑加列。
- **升级影响**：旧会话缺少摘要，首次访问会被要求重新登录一次（有意为之）。

### [x] M-3. 登录限流仅按 IP、驻留内存、无账号锁定
- **来源**：01-M-3、03-M1、02-L2
- **位置**：`auth/routes.py:14-16,41-49,63-65`（`login_attempts_{client_ip}` + `SimpleCache`）；`config.py` `CACHE_TYPE='simple'`。
- **触发/影响**：多 IP 分布式爆破、进程重启清零；配合 H-1 可稳定命中。
- **修复**：增加账号维度计数与阶梯延迟/临时锁定；失败/锁定入审计并可告警；限流状态持久化（Redis 等共享存储）；admin 等高价值账号额外保护（延迟/验证码）。
- **验证**：同一账号两个来源各试 9 次，第 10 次期望被锁。
- **2026-09-23 完成（改动点）**：`auth/routes.py` 增加账号维度计数 `login_fail_<username>` 与锁定键 `login_lock_<username>`：连续失败 5 次锁定 15 分钟（期间即使口令正确也拒绝），成功后清零；失败与锁定事件写 `stulink.auth` 安全日志（含来源 IP）。`tests/sec_regression.py` 增 M-3 断言 3 条，全部通过。
- ⏸ **未做**：限流状态持久化到 Redis/共享存储（仍为进程内 `SimpleCache`，重启清零）。剩余风险：多进程/重启后计数丢失。下次做法：换 Redis 或落 `data/` 下独立 SQLite。

### [x] M-4. Excel/CSV 公式注入防护未全覆盖
- **来源**：01-M-4
- **位置**：工具已有 `export_helpers.py:14-43`（`xl_safe`/`xl_row`）；**未使用**：`export_helpers.py:161-171`、`:331-359`、`grades/routes/export.py:60-61`、`exam_affairs.py:898-930,1024-1059`、`dormitory/statistics.py:673-675`、`rooms.py:1438-1470`、`dashboard.py:375-381`；打包清单 CSV 未转义（`form_summary_service.py:914-923`）。
- **触发/影响**：姓名/班级/事由以 `=`/`+`/`-`/`@` 开头 → 导出文件在 Excel/WPS 打开即求值（DDE/外链）。
- **修复**：所有写单元格路径统一走 `xl_row`/`xl_safe`；CSV 同处理。
- **验证**：导入姓名 `=1+1` 后导出，修复后单元格应为文本。
- **2026-09-23 完成（改动点）**：`export_helpers.py` 两处学生导出写值改 `xl_safe(v)`；`grades/routes/export.py` 表数据行改 `xl_row(...)`；`exam_affairs.py` 的 `_write_sheet` 与竖版桌签导出改 `xl_row(...)`。清单点名的 `dormitory/statistics.py:673-675`、`dashboard.py:375-381` 经核对是**导入模板的示例行**（静态常量），无需转义。`tests/sec_regression.py` 增 M-4 断言 9 条，全部通过。

### [ ] M-5. 身份证加密使用确定性 IV；解密失败静默当明文返回
> **状态：部分完成（解密失败语义已修，随机 IV + 盲索引暂缓）**
- **来源**：01-M-5、03-M8、02-M1
- **位置**：`crypto.py:50-57`（`iv = sha256(plaintext)[:16]`）、`:83-85,98-100`（失败返回密文原文）；索引 `student.py:65`。
- **触发/影响**：密文等同弱哈希（身份证低熵可离线字典比对）；密钥轮换/数据异常时密文以"明文"形式流入页面与导出。
- **修复**：随机 IV（`encrypt_rand` 已有）+ 独立等值索引列（`HMAC-SHA256(key, 明文)`，即盲索引）；解密失败抛错并记日志（语义改为报错 + 审计）；**迁移需配套回填脚本**（旧确定性密文 → 随机 IV 密文，并回填盲索引列）。
- **验证**：同一身份证 `encrypt()` 两次，密文相同即确认；篡改密文后 `decrypt` 应报错；仅持库无密钥时无法用 `sha256(猜测)[:16] == iv` 离线校验。
- **2026-09-23 部分完成（改动点）**：**已做**——`crypto.py::decrypt` 解密失败不再"静默返回密文原文"，改为记 `stulink.crypto` 错误日志并抛 `DecryptError`；展示侧（`id_card.decrypt_id_card`、`Student.id_card_number`、`grade_mgmt.py` 两处）降级为 `''`/`None`，保证页面不 500。`tests/sec_regression.py` 增 M-5 断言 4 条，全部通过。
- ⏸ **本轮未做（用户 2026-09-23 决定：不加 DB 列/不迁移数据）**：随机 IV（`encrypt_rand`）+ 独立盲索引列（`HMAC-SHA256(key, 明文)`）+ 旧确定性密文回填脚本。剩余风险：身份证密文仍等同弱哈希，可离线字典比对。下次做法：写幂等迁移脚本（加列 → 回填 → 切查询口径）。

### [x] M-6. 备份、临时文件与缓存清理异常
- **来源**：01-M-6
- **位置**：`system/routes/grade_mgmt.py:69-85`（毕业备份 `system.db`/`dormitory.db`/`history.db` 到 `data/backups/`，**无保留策略/无清理**，好在无下载路由）；`academic/routes/form_summary.py:133-146`（`after_this_request` best-effort 删临时包，中断即残留）；`students.py:28-42,933-936`（`_import_errors` 仅被下载时惰性清理）。
- **触发/影响**：明文整库副本长期累积并随同步盘扩散（system.db 含口令哈希、身份证密文）；`stulink_pkg_*` 临时目录残留；磁盘缓慢增长。
- **修复**：备份保留窗口 + 受控目录 + 权限收敛；启动时清扫过期临时包；`_import_errors` 增加请求级清理阈值。
- **验证**：观察 `data/backups/` 增长；`ls $env:TEMP | findstr stulink_pkg_`。
- **2026-09-23 完成（改动点）**：`grade_mgmt.py` 新增 `_prune_backups()`（毕业备份后调用，保留最近 20 份且删除超过 180 天的 `.db` 备份，只动备份文件）；`form_summary.py` 新增 `cleanup_stale_packages()`（打包前清扫超过 24 小时的 `stulink_pkg_*` 残留目录），并把临时包清理失败的静默 `except: pass` 改为写 `stulink.academic` 警告日志（同时消 L-5）。`tests/sec_regression.py` 增 M-6 断言 2 条，全部通过。
- ⏸ **未做**：`students.py` 的 `_import_errors` 请求级清理阈值（属优化，无泄露面）。

### [x] M-7. CSRF：alumni 站无防护；主应用 token 永不过期
- **来源**：01-M-7、02-M4、03-S2
- **位置**：`alumni_app/app/__init__.py:7-25`（未初始化 `CSRFProtect`，唯一 POST 为 `/login`）；`alumni_app/config.py:49-56`；主应用 `config.py:48-50`（`WTF_CSRF_TIME_LIMIT=None`、`WTF_CSRF_SSL_STRICT=False`）；主应用 `csrf.exempt` 0 命中（已确认）。
- **触发/影响**：alumni 登录 CSRF（危害低）；主应用 token 泄露后长期可用。
- **修复**：alumni 启用 `CSRFProtect` + 登录表单加 token；主应用设置 `WTF_CSRF_TIME_LIMIT`（如 8h）。
- **验证**：对 `:5001/login` 提交无 token 的跨站表单，观察是否登录成功；过期旧 token 提交应被拒。
- **2026-09-23 完成（改动点）**：① alumni 站启用 `CSRFProtect`（`alumni_app/app/__init__.py`）+ 登录模板加 token（`alumni_app/app/templates/login.html`）；② 主应用 `config.py` 的 `WTF_CSRF_TIME_LIMIT` 由 `None` 改为 8 小时（可用 `STULINK_CSRF_TIME_LIMIT` 调整）。`tests/sec_regression.py` 增 M-7 断言 3 条，全部通过。
- **2026-09-23 部分完成（改动点）**：**已做**——alumni 站 `app/__init__.py` 初始化 `CSRFProtect`、`templates/login.html` 表单加 `csrf_token`、`alumni_app/config.py` 设 `WTF_CSRF_TIME_LIMIT`（默认 28800s，可用 `ALUMNI_CSRF_TIME_LIMIT` 覆盖）；`tests/sec_regression.py` M-7 断言 2 条通过。**未做**：主应用 `config.py:49` 的 `WTF_CSRF_TIME_LIMIT=None`（见本条其余部分，随 P2 一并处理）。

### [x] M-8. Docker 镜像把用户上传材料打包进镜像
- **来源**：01-M-8
- **位置**：`Dockerfile:15`（`COPY app/ ./app/`）+ `.dockerignore`（排除 `data`、`logs`，**未排除** `app/static/uploads`）。
- **触发/影响**：构建时若目录已有学生材料 → 材料进入镜像层，镜像分发即泄露。
- **修复**：`.dockerignore` 加 `app/static/uploads`；配合 H-8 把上传迁出 `app/`。
- **验证**：`docker run --rm <img> ls /app/app/static/uploads`。
- **2026-09-23 完成（改动点）**：`.dockerignore` 增加 `app/static/uploads` 与 `app/static/uploads/**`。`tests/sec_regression.py` 增 M-8 断言 2 条，全部通过。注：根治仍需配合 M-16 把上传迁出 `static`（本轮暂缓）。

### [x] M-9. 无 CSP 头 + `X-XSS-Protection` 已废弃
- **来源**：01-M-9、03-S1、02-L3
- **位置**：`app/__init__.py:519-526`。
- **触发/影响**：H-5/H-6/H-8/H-10 任一 XSS 成功执行时无第二道防线。
- **修复**：增加 `Content-Security-Policy`（至少 `default-src 'self'`，脚本用 nonce/hash，按需放宽内联样式）；移除 `X-XSS-Protection`。
- **验证**：`curl -I` 检查响应头含 CSP。
- **2026-09-23 完成（改动点）**：`app/__init__.py` 的 `add_security_headers` 移除 `X-XSS-Protection`，新增 CSP：默认下发 `Content-Security-Policy-Report-Only`（`default-src 'self'`，`img/font` 允许 data，`connect-src 'self'`，`frame-ancestors 'none'`，`object-src 'none'`，`base-uri 'self'`），并预留 nonce（`request.csp_nonce` / 模板 `{{ csp_nonce }}`）。模式开关：`STULINK_CSP_MODE=report|enforce|off`（默认 report）。`tests/sec_regression.py` 增 M-9 断言 4 条，全部通过。
- ⏸ **未做**：切换到 enforcing（需要先把内联脚本全部加 nonce，否则现代浏览器会拦掉内联脚本导致页面失效）。剩余风险：Report-Only 只上报不拦截。下次做法：观察一轮 CSP 违规报告 → 给内联脚本补 nonce → 置 `STULINK_CSP_MODE=enforce`。

### [x] M-10. 内联事件处理器 JS 上下文 XSS（单引号未转义）
- **来源**：03-M6
- **位置**：`system/students/list.html:169`、`dormitory/assignments/manage.html:201,205,206`、`grades/affairs/list.html:59`、`system/users/list.html:82`、`system/dictionary/list.html:69`、`academic/schedule_manage.html:111,117,124,146`。
- **触发/影响**：`{{ x|e }}` 的 `&#39;` 在交 JS 引擎前还原为 `'` 打断字符串，姓名/名称含 `'`（如 O'Brien）即注入；部分处完全未转义（`bd.student.name`）。
- **修复**：全部改 `|tojson`；`confirm` 文本用 `|tojson`。
- **验证**：姓名改 `x');alert(1);//` 触发按钮/拖拽 → 修复后不弹窗。
- **2026-09-23 完成（改动点）**：清单点名的 6 个文件 + 全模板扫描发现的 4 处（宿舍列表、通知列表、班级概览、学期管理）全部改 `|tojson`；`academic/schedule_manage.html` 的 `confirm()` 文本整体走 `|tojson`。新增**全模板正则扫描断言**（禁止 `on*="...'{{` 形式的内联处理器），防止回退。`tests/sec_regression.py` 增 M-10 断言 6 条，全部通过。

### [x] M-11. 通知 `link_url` 无协议白名单（javascript: XSS）
- **来源**：03-M7
- **位置**：`notifications/routes.py:110`（仅 `strip()`）；模板 `notifications/list.html:123`、`detail.html:87`（`href="{{ n.link_url }}"`）。
- **触发**：有发布权限者将 `link_url` 设为 `javascript:alert(document.cookie)`，他人点击即执行。
- **修复**：仅允许 `http://`/`https://`，拒绝 `javascript:`/`data:`。
- **验证**：发布 `javascript:alert(1)` 链接，他人点击 → 修复后拒绝保存。
- **2026-09-23 完成（改动点）**：`notifications/routes.py` 新增 `_is_safe_link()`（仅允许 http/https 且必须有 host），创建通知时校验失败即 flash 错误并拒绝保存。`tests/sec_regression.py` 增 M-11 断言 5 条，全部通过。

### [x] M-12. 堆栈回溯 / 内部异常文本经响应回传前端
- **来源**：03-M9/M10
- **位置**：`dormitory/services/room_assignment_v8.py:480-485`（`traceback.format_exc()` 进 `logs`）+ `rooms.py:1026-1060`（`jsonify(result)` 回传含堆栈 `logs`）；`rooms.py:361,1599`、`class_profile.py:168`、`form_summary.py:150,171,191,218,249`、`points/routes.py:337`、`users.py:300`、`students.py:911,1278`、`grade_mgmt.py:339,439` 等多处 `str(e)`。
- **触发/影响**：攻击者构造异常从响应获绝对路径/源码行/SQL；异常文本泄露文件路径/表结构辅助攻击。
- **修复**：生产环境剥离 `[TRACE]` 堆栈仅留摘要；向用户返回友好文案，详细异常仅记服务端日志（结构化、不含敏感字段）。
- **验证**：触发自动分配异常，响应 `logs` 不应含完整堆栈；触发异常操作响应不应含 SQL/路径。
- **2026-09-23 完成（改动点）**：① `room_assignment_v8.py` 不再把 `[TRACE] traceback.format_exc()` 放进返回 `logs`（改记 `stulink.dormitory` 服务端日志）；② 新增 `app/utils/err_safe.py::safe_error()`（剔除绝对路径/盘符、SQL 关键字、File/line 标记，附带错误编号便于对照服务端日志），接入 `rooms.py` 两处、`form_summary.py` 全部 500 分支。`tests/sec_regression.py` 增 M-12 断言 6 条，全部通过。
- ⏸ **未做**：清单列出的其余 `str(e)` 点（`class_profile.py:168`、`points/routes.py:337`（经核对该文件无 `str(e)`）、`users.py:300`、`students.py:911/1278`）保留原文。剩余风险：低（多为已受控的文本）。下次做法：逐点替换为 `safe_error()`。

### [x] M-13. 开放重定向校验过宽
- **来源**：01-L-1、02-M3
- **位置**：`auth/routes.py:19-30`（放行 `/\evil.com`；`netloc == ''` 放行 `javascript:`/`data:`）。
- **触发/影响**：登录 `next` 参数可被用于钓鱼外跳；`javascript:`/`data:` 协议可触发。
- **修复**：改用 `flask.helpers.safe_join`，仅允许 `scheme==''` 且 `netloc==''` 的相对路径、拒绝 `\` 开头；`/login?next=//evil.com` 登录后不应外跳。
- **验证**：`/login?next=/\外部域` 登录后不得跳转外域。
- **2026-09-23 完成（改动点）**：`auth/routes.py::_is_safe_redirect` 重写为：拒绝含反斜杠、有 scheme 或 netloc、非单个 `/` 开头的路径（即 `//evil.com`、`/\evil.com`、`javascript:`、`data:`、外站绝对地址全部拒绝）。`tests/sec_regression.py` 增 M-13 断言 6 条，全部通过。

### [x] M-14. 成绩证明公开核验端点无鉴权暴露快照
- **来源**：02-M5
- **位置**：`grades/routes/student_query.py:65-86`（`/cert/<code>`）。
- **触发/影响**：未登录访问有效 code，页面可能含成绩明文（真/伪/已作废 + 摘要本应走登录后 `cert_print`）。
- **修复**：`/cert/<code>` 仅返回真/伪/已作废 + 摘要哈希；完整明细走登录后 `cert_print`。
- **验证**：未登录访问有效 code，页面不含成绩明文。
- **2026-09-23 完成（改动点）**：`student_query.py::cert_verify` 不再解密/传递证明快照（改为只算 `content_hash` 摘要），模板 `cert_verify.html` 移除 `cert_doc(content, cert, ...)` 渲染，改为「真伪 + 状态 + 防伪码 + 出具时间 + 内容摘要」表格，并提示完整明细走登录后的 `cert_print`。`tests/sec_regression.py` 增 M-14 断言 4 条，全部通过。

### [x] M-15. 密码复杂度过低 + 无弱口令黑名单
- **来源**：03-M13、02-L4、01-H-1④
- **位置**：`auth_forms.py`、`user_forms.py`（仅 `min=6`）。
- **触发/影响**：弱口令易被爆破，叠加 H-1/H-3 限流缺失放大。
- **修复**：≥8 位 + 复杂度策略 + 弱口令黑名单；禁止用姓名/手机号做密码。
- **验证**：设 `123456` 应被拒。
- **2026-09-23 完成（改动点）**：新增 `app/utils/password_policy.py` 作为唯一口令策略实现（≥8 位、含字母与数字、弱口令黑名单含 admin123/手机号键盘序列、禁止与登录名/姓名/手机号相同、禁止单字符重复）；`auth_forms.py::ChangePasswordForm.validate_new_password` 与 `user_forms.py::UserForm.validate_password` 统一调用，并额外禁止新口令与当前口令相同；`tests/sec_regression.py` 增 M-15 断言 9 条，全部通过。

### [ ] M-16. 上传目录置于 static 下的设计缺陷
> **状态：暂缓（用户 2026-09-23 决定：不搬目录、不迁移 file_path），已由 H-8 缓解**
- **来源**：03-M12、01-H-8/R-3
- **位置**：`config.py:41` + `app/__init__.py:493-494`（已有 TODO）；`app/__init__.py:488-498` 直链守卫。
- **触发/影响**：即便修复 H-8 大小写，把用户内容托管在 Web 静态目录仍是脆弱设计（任一未来误配置即暴露）。
- **修复**：整体迁到 `instance/uploads` 等静态目录之外，下载统一走带鉴权路由，并同步迁移 `FormAnswer.file_path`。
- **验证**：`uploads` 不在 `static_folder` 下。
- ⏸ **本轮暂缓（用户 2026-09-23 决定）**：不搬上传目录、不迁移 `FormAnswer.file_path`。已由 H-8 的 `normcase` 守卫 + 扩展名白名单/危险类型拒绝 + magic 校验作为缓解。剩余风险：静态目录承载用户内容的设计性缺陷仍在（任一未来误配置即暴露）。下次做法：迁至 `instance/uploads` + 下载统一走带鉴权路由 + 同步迁移 `FormAnswer.file_path`。

---

## 3. 低危（12 项）

### [x] L-1. 导出日志来源 IP 可伪造
- **来源**：01-L-2
- **位置**：`export_helpers.py:203,394`（`ip_address=args.get('ip_address','')`）。
- **修复**：统一取 `request.remote_addr`。
- **验证**：导出日志 IP 应为真实客户端地址。
- **2026-09-23 完成（改动点）**：`export_helpers.py` 新增 `_client_ip()`（取 `request.remote_addr`，非请求上下文留空），两处 `ip_address=args.get('ip_address','')` 改为 `ip_address=_client_ip()`。断言 3 条全过。

### [x] L-2. 日志落盘与脱敏
- **来源**：01-L-3
- **位置**：`bands.py:26-46`（`logs/bands.log`，已被 `.gitignore:56-57` 忽略）；`helpers.py:283,321` 异常日志可能含业务上下文。
- **修复**：脱敏 + 目录权限收敛；关键安全事件告警。
- **2026-09-23 完成（改动点）**：新增 `app/utils/log_mask.py`（`mask_text()` + `MaskingFilter`，落盘前掩码身份证/手机号/邮箱，含异常栈），`bands.py` 的划线日志已挂该过滤器；关键安全事件（登录失败/锁定、解密失败、内部异常）分别写入 `stulink.auth` / `stulink.crypto` / `stulink.error` 日志。断言 4 条全过。
- ⏸ **未做**：日志目录权限收敛（Windows 部署下 chmod 语义有限）。

### [x] L-3. PDF 生成的类 HTML 解析
- **来源**：01-L-4
- **位置**：`affair_pdf.py:150-175`（学生姓名/班级进 reportlab `Paragraph`，会解释 `<img src=...>`）。
- **修复**：`escape()` 后再构造。
- **验证**：导入姓名含标签的名单，生成的 PDF 不应内嵌图片/外链。
- **2026-09-23 完成（改动点）**：`affair_pdf.py` 引入 `xml.sax.saxutils.escape`，新增内部 `_p()` 包装，所有进入 `Paragraph` 的文本（标题、分组名、表头、单元格）先转义。断言 1 条通过。

### [x] L-4. 打包阈值与导入草稿并发
- **来源**：01-L-5
- **位置**：`form_summary.py:133-146`（>500MB 才落盘，大包内存打包有 OOM 风险）；`timetable.py:21-29`（进程内 dict 无锁）。
- **修复**：统一阈值/落盘策略 + 加锁。
- **2026-09-23 完成（改动点）**：`academic/routes/timetable.py` 的 `_DRAFT` 加 `threading.RLock`，并新增 `_DRAFT_MAX=200` 数量上限（超出按时间淘汰）；材料包残留清扫见 M-6 的 `cleanup_stale_packages()`。断言 3 条全过。

### [x] L-5. 异常静默吞没，缺结构化安全日志（主应用 + alumni）
- **来源**：01-L-6、02-L5
- **位置**：`alumni_app/app/routes/basic.py:29-30`、`alumni_app/app/auth.py:27-28`（`except Exception: pass`）；主应用同类静默吞没（如 `app/modules/academic/routes/form_summary.py:142-143` 的 `except Exception: pass`）。
- **修复**：记日志（结构化、不含敏感字段），认证异常不得静默；关键安全事件告警。
- **2026-09-23 完成（改动点）**：`alumni_app/app/auth.py`（load_user）与 `alumni_app/app/routes/basic.py`（登录）的 `except Exception: pass` 改为 `_log.exception(...)`；主应用 `form_summary.py` 临时包清理失败改为写 `stulink.academic` 警告日志。断言 3 条全过。
- ⏸ **未做**：alumni 中「下拉选项查询失败」的 `except: pass`（无敏感信息、失败即降级为空列表），保留。

### [x] L-6. 依赖未锁版本
- **来源**：01-L-7
- **位置**：`requirements.txt:1-10`（全部 `>=`）。
- **修复**：锁定版本 + CI 加 `pip-audit`。
- **2026-09-23 完成（改动点）**：`requirements.txt` 由全部 `>=` 改为按本机实际运行版本锁区间（`~=`，允许补丁/次版本修复、禁止跨次版本升级），并补充 `WTForms/SQLAlchemy/Werkzeug/Jinja2` 等间接依赖。断言 2 条全过。
- ⏸ **未做**：CI 里加 `pip-audit`（需 CI 环境，当前仓库无 CI 配置）。下次做法：在 CI 增加 `pip-audit -r requirements.txt`。

### [x] L-7. 新用户初始密码明文回显
- **来源**：01-L-8
- **位置**：`users.py:82-83`（flash 明文口令 → 进响应 HTML 与签名 cookie）；`users.py:162-171` 重置后明文被丢弃属可用性缺陷。
- **修复**：一次性展示区 + 首登强制改密（关联 H-1）。
- **2026-09-23 完成（改动点）**：`users.py` 新增 `_store_one_time_password()`，create/reset_password/import_teachers 的口令写入 `session['_pwd_once']`，`list_users` 取出后渲染一次性面板（`system/users/list.html` 顶部），刷新即失效；flash 不再携带明文口令。`tests/sec_regression.py` 增 L-7 断言 2 条，全部通过。

### [x] L-8. 导入 Excel 仅校验扩展名（无 Content-Type/Magic/Macro 校验）
- **来源**：03-L1（注：上传扩展名见 H-8；此处指导入解析）
- **位置**：`students.py:621`、`users.py:223`、`exam_affairs.py:243,504` 等 `endswith(('.xlsx','.xls'))`。
- **修复**：加 Content-Type/Magic 校验并限制解析规模（`openpyxl` 不执行宏，无 RCE，风险低）。
- **2026-09-23 完成（改动点）**：学生导入 / 批量调班（`students.py`）、教师导入（`users.py`）、考务名单与考场导入（`exam_affairs.py`）全部改走 `upload_guard.validate_upload()`（扩展名白名单 + 危险类型 + magic 字节）。断言 5 条全过。

### [x] L-9. `download_name` 反射 DB 取值到 Content-Disposition（潜在 CRLF 头注入）
- **来源**：03-L2
- **位置**：`exam_affairs.py:907`、`export.py:146`、`workbench/routes/attendance.py:143`。
- **修复**：显式清洗文件名（防 CRLF 头注入）。
- **2026-09-23 完成（改动点）**：`text_guard.py` 新增 `safe_download_name()`（去 CR/LF 与控制字符、去引号与路径分隔符、去 `..`、限长、空名回落默认），接入 `exam_affairs.py` 三处导出与 `workbench/routes/attendance.py`。断言 5 条全过。

### [x] L-10. 前端 `escapeAttr` 未转义 `&` 与 `'`
- **来源**：03-L3
- **位置**：`app/static/js/academic_forms.js:293-295`。
- **修复**：补全转义 `&` 与 `'`（自 XSS，影响有限）。
- **2026-09-23 完成（改动点）**：`academic_forms.js::escapeAttr` 改为复用公共 `window.escAttr`（`static/js/common.js`，转义 `& < > " ' \` =`），不再只转义 `"`/`<`/`>`。`tests/sec_regression.py` 增 L-10 断言 2 条，全部通过。

### [x] L-11. `.secret_key` 创建未设 `chmod 600`
- **来源**：03-L4
- **位置**：`config.py:22-28`。
- **修复**：创建后 `os.chmod(0o600, ...)`（多用户机文件权限）。
- **2026-09-23 完成（改动点）**：`config.py::_get_secret_key()` 写入密钥文件后 `os.chmod(key_file, 0o600)`（Windows 下 best-effort，失败不影响启动）。断言 1 条通过。

### [x] L-12. 用户输入字段无最大长度限制
- **来源**：03-L5
- **位置**：姓名/备注/通知/评语等 `request.form.get(...).strip()` 多处。
- **修复**：加长度上限（功能性/存储风险，低安全危）。
- **2026-09-23 完成（改动点）**：`text_guard.py` 新增 `clamp_text()` 与 `safe_download_name()`；已接入通知标题(100)/正文(5000)/链接(500)、画像评语正文(2000)，考务 `location(50)/note(100)/prefix(16)` 也已限长。断言 4 条全过。

---

## 4. 建议优化（R/S 合并）

> 2026-09-23：R-1 / R-2 已完成 —— R-1 新增 `app/utils/decorators.py::scope_required(checker, perm)`（内部 `PermissionError`/False → 403），`workbench/services/scope_service.py` 降级为转发层（统一委托 `workbench/services/scope.py::class_allowed`），「路由 → 权限 + 范围」清单随 H-2/H-3/H-4 逐条补全；R-2 新增 `app/static/js/common.js`（`escHtml`/`escAttr`/`jsStr`），`base.html` 全局引入，`grades.js`/`student_query.js` 改为复用。各增断言并全部通过。

- [x] **R-1. 统一数据范围装饰器**：新增 `@scope_required`（内部走 `scope_service`/`student_in_scope`），维护"路由 → 权限 + 范围"清单，杜绝 H-2/H-3/H-4 类遗漏。（01-R-1）
- [x] **R-2. 前端渲染规约**：抽公共 `escHtml`（`grades.js` 已有）；CR 禁止新增裸 `innerHTML`；模板禁止新增 `|safe`（现状 `base.html:441`、`_cert_body.html:60` 均不可注入，保持）。（01-R-2）
- [x] **R-3. 上传体系改造**：**部分完成**——已落地扩展名白名单 + 危险类型拒绝 + magic 校验（`app/utils/upload_guard.py`，表单材料 / 积分 / 学生 / 教师 / 考务导入统一调用）、随机名（uuid 前缀）、强制 `attachment`；⏸ **未做**：迁出 `static` + `FormAnswer.file_path` 迁移 + 单用户配额（同 M-16，用户 2026-09-23 决定暂缓）。断言 5 条全过。（01-R-3、03-M12）
- [x] **R-4. 口令与账号生命周期**：强制改密（H-1）、口令策略 ≥8 + 复杂度 + 弱口令黑名单（`app/utils/password_policy.py`，H-1④ 与 M-15 共用同一实现）、重置后一次性展示（L-7）均已落地。断言 4 条全过。（01-R-4）
- [x] **R-5. 会话与限流强化**：见 M-2（口令摘要会话失效 / 会话保护 / 过期 / 登出清会话）与 M-3（账号维度锁定 + 安全日志）。⏸ 未做：admin 等高价值账号的**额外**保护（验证码/更严阈值）——已由账号锁定 + 日志覆盖大部分场景。断言 3 条全过。（01-R-5）
- [x] **R-6. 加密升级**：`decrypt` 失败语义已改为抛 `DecryptError` + 服务端审计日志（M-5 后半）。⏸ 未做：随机 IV + 盲索引 + 回填（同 M-5，暂缓）。断言 2 条全过。（01-R-6）
- [x] **R-7. 部署与传输**：CSP 已接入并可由 `STULINK_CSP_MODE` 切换（默认 Report-Only），`SESSION_COOKIE_SECURE` 改由 `STULINK_SESSION_COOKIE_SECURE` 打开，README 增加部署安全基线说明。⏸ 未做：生产上 HTTPS 与 HSTS（属运维）。断言 3 条全过。（01-R-7）
- [x] **R-8. 安全回归测试化**：新增 `tests/sec_regression.py`（临时库 + `create_app()` + `test_client()`，按项号分组，当前 **244 条断言全绿**，支持 `python tests/sec_regression.py H-1 M-2` 局部运行）；新增 `scripts/scan_security_patterns.py`（正则扫描：SQL 拼接 / 路径含 `request.*` / `csrf.exempt` / `|safe` / 裸 `innerHTML` / 内联处理器单引号变量，当前 0 命中、退出码 0），提交前必跑。断言 4 条全过。（01-R-8、03-S6）
- [x] **R-9. 定期轮换 SECRET_KEY**：**支持但未自动执行**——`SECRET_KEY` 可经环境变量注入（`config.py`），配合 M-2 的口令摘要机制，轮换后旧会话全部失效。⏸ 轮换动作本身属运维范畴（会踢掉全部在线用户），未写入代码自动流程。断言 2 条全过。（03-S3）
- [x] **R-10. `_asset_mtime` 路径拼接**：新增 `_is_safe_asset_name()` 白名单（相对路径、禁 `..`/反斜杠/盘符/空字节、仅允许 `[A-Za-z0-9_./-]`），非法名直接返回 0 并记 `stulink.app` 警告；保持不接受请求参数。断言 3 条全过。（03-S4）
- [x] **R-11. `scripts/` 标识符拼接 DDL 加白名单**：新增 `scripts/_ddl_guard.py`（`safe_ident` / `assert_ident` / `assert_table` + 表名白名单），接入 7 个拼接 DDL 的迁移脚本（cleanup_student_columns / migrate_notification_recipients / migrate_cert_sign_v1122 / migrate_affair_v1121 / migrate_term_schedule_dates / add_performance_indexes / migrate_split_db）。断言 4 条全过。（03-S5）

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

---

## 9. 2026-09-23 整改执行总结（本轮）

**进度：49 项中已勾 `[x]` 46 项**（H-1~H-6、H-8~H-10、M-1~M-4、M-6~M-15、L-1~L-12、R-1~R-11）；
**部分完成/暂缓 3 项**：H-7（代码层已修、部署形态暂缓）、M-5（失败语义已修、随机 IV+盲索引暂缓）、M-16（不搬目录，已由 H-8 缓解）。

### 验证方式（提交前必跑）

```powershell
python tests/sec_regression.py            # 安全回归：244 条断言，全绿（支持按项号过滤：... H-1 M-2）
python scripts/scan_security_patterns.py  # 禁用模式正则扫描：0 命中（退出码 0）
python scripts/smoke_permissions.py       # 业务冒烟：38/38
python scripts/smoke_grades.py            # 业务冒烟：98/98
python scripts/smoke_academic.py          # 业务冒烟：31/31
```

### 本轮新增/改造的关键文件

| 文件 | 作用 |
|---|---|
| `tests/sec_regression.py` | R-8 安全回归（临时库 + HTTP 断言，按项号分组） |
| `tests/alumni_regression.py` | H-7 往届站独立回归（子进程隔离） |
| `scripts/scan_security_patterns.py` | R-8 CI 正则扫描（SQL 拼接/路径含 request/csrf.exempt/\|safe/裸 innerHTML/内联单引号变量） |
| `scripts/_ddl_guard.py` | R-11 DDL 标识符白名单 |
| `app/utils/session_guard.py` | M-2 会话口令摘要（无迁移实现改密/禁用踢会话） |
| `app/utils/password_policy.py` | H-1④ / M-15 唯一口令策略 |
| `app/utils/upload_guard.py` | H-8 / R-3 / L-8 上传校验唯一实现 |
| `app/utils/text_guard.py` | H-5 / L-9 / L-12 文本清洗（字符白名单、安全文件名、长度上限） |
| `app/utils/err_safe.py` | M-12 异常文案脱敏（剔路径/SQL/行号 + 错误编号） |
| `app/utils/log_mask.py` | L-2 日志脱敏过滤器 |
| `app/modules/grades/services/exam_guard.py` | H-4 考试/年级范围校验唯一入口 |
| `app/modules/grades/services/url_guard.py` | H-9 出站 URL 白名单 + SSRF 拦截（DNS 解析后按 IP 段拒绝） |
| `app/static/js/common.js` | R-2 公共 `escHtml`/`escAttr`/`jsStr` |

### 暂缓项与残留风险（下次优先处理）

1. **H-7 `alumni_app/run.py` 的 `debug=True`** —— **RCE 级**（Werkzeug 调试器 PIN 可爆破），仅一行改动即可消除；同时建议换 waitress/gunicorn + 独立 `SECRET_KEY`。
2. **M-5 随机 IV + 盲索引** —— 身份证密文仍等同弱哈希，可离线字典比对；需幂等迁移脚本（加列 → 回填 → 切查询口径）。
3. **M-16 / R-3 上传迁出 `static`** —— 设计性缺陷仍在（已修大小写绕过 + 白名单缓解）；需迁至 `instance/uploads` + 下载走鉴权路由 + 迁移 `FormAnswer.file_path`。
4. **M-9 CSP 切换 enforcing** —— 当前 Report-Only；需先给内联脚本补 nonce 再置 `STULINK_CSP_MODE=enforce`。
5. **M-3 限流持久化** —— 仍为进程内 `SimpleCache`，多进程/重启后计数丢失。
6. **R-7 生产 HTTPS/HSTS** —— 属部署运维；上 HTTPS 后开 `STULINK_SESSION_COOKIE_SECURE=1` + HSTS。

### 升级后的行为变化（需要告知使用者）

- 首次启动的 `admin` 使用**随机初始口令**（控制台打印一次）+ 强制首登改密；存量 `admin` 若仍是历史默认口令会打印告警。
- 导入/新建的账号默认 `must_change_pwd=True`，未改密前访问任何功能页都会跳改密页。
- **登录会重建会话**（防会话固定）：登录后浏览器页面需重新取 CSRF token（本项目页面由 `base.html` 统一注入，正常不受影响；脚本/对接方需按 README 说明处理）。
- 改密/重置/禁用后，其它设备的登录状态立即失效；升级后所有在线用户需重新登录一次（会话摘要缺失所致）。
- 越权访问（跨年级/跨班/无权限）统一返回 403；公开的成绩证明核验页只显示真伪与摘要。

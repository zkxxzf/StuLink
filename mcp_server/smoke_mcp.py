# -*- coding: utf-8 -*-
"""StuLink MCP 服务回归自测：注册全部工具并逐个调用，验证端到端可用。

用法（在项目根目录执行）：
    mcp_server\\.venv\\Scripts\\python.exe mcp_server\\smoke_mcp.py

覆盖：工具注册与 schema 生成、只读工具真实取数、权限/参数错误的中文提示、
      写工具开关、写路径的入参校验（用不存在的学号触发，不写任何真实数据）、
      接入认证端到端（account 模式的 401/429 拒绝策略、账号密码通过、权限仍受控）。

运行前提：mcp_server/config.json 存在（首次启动服务会自动生成）。
"""
import asyncio
import base64
import json
import os
import socket
import sys
import threading
import time

import uvicorn                                                       # noqa: E402
from mcp.client.session import ClientSession                         # noqa: E402
from mcp.client.streamable_http import (                             # noqa: E402
    create_mcp_http_client, streamable_http_client)

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server import config                                        # noqa: E402
from mcp_server.core import bootstrap, db as core_db                   # noqa: E402
from mcp_server.run import build_asgi_app                             # noqa: E402

PASS, FAIL = [], []


def check(name, text, expect_ok=True, must_contain=None):
    """检查工具返回：ok 标志 + 必须包含的文本。"""
    got_ok = '"ok": true' in text
    problems = []
    if got_ok != expect_ok:
        problems.append(f'期望 ok={expect_ok}，实际 {got_ok}')
    for token in (must_contain or []):
        if token not in text:
            problems.append(f'缺少内容：{token}')
    if problems:
        FAIL.append((name, '; '.join(problems), text[:300]))
        print(f'FAIL  {name}')
        print('      问题:', '; '.join(problems))
        print('      返回:', text[:300].replace('\n', ' '))
    else:
        PASS.append(name)
        print(f'PASS  {name}')


def _pick_restricted_username():
    """挑一个"角色受限"的账号：班主任/任课教师 + 权限组班级范围 + 无用户级授权。

    这类账号在网页端只能看本人班级/任课范围，用于验证 MCP 侧的权限点与角色矩阵。
    """
    from app.models.user import User
    from app.modules.grades.services import scope as scope_service
    cands = (User.query.filter(User.is_active.is_(True),
                               User.role.in_(['homeroom_teacher', 'teacher']))
             .order_by(User.id).limit(20).all())
    for u in cands:
        if scope_service.user_grade_scope(u) is not None:
            continue
        scope_type, _g = scope_service.get_scope(u)
        if scope_type == 'class':
            return u.username
    return None


async def main():
    cfg = config.load_config()
    cfg['bind_user'] = 'admin'
    cfg['allow_write'] = False          # 自测不写真实数据
    cfg['auth_mode'] = 'token'          # 第一段直接测工具层（token 模式固定身份）；
    #                                     account 模式的接入认证在文件末尾单独端到端验证
    _app, server = build_asgi_app(cfg)

    tools = await server.list_tools()
    names = sorted(t.name for t in tools)
    print(f'已注册工具 {len(names)} 个：{", ".join(names)}\n')
    if names:
        PASS.append('工具注册')
    else:
        FAIL.append(('工具注册', '未注册任何工具', ''))

    async def call(name, args):
        result = await server.call_tool(name, args)
        if hasattr(result, 'content') and result.content:
            return result.content[0].text
        return str(result)

    # ── 基础信息 ────────────────────────────────────────────────
    check('list_grades', await call('list_grades', {}), must_contain=['grade'])
    check('list_classes', await call('list_classes', {'grade': '2025级'}))
    check('search_students', await call('search_students', {'keyword': '张', 'limit': 3}))
    check('get_system_overview', await call('get_system_overview', {}))
    check('list_exams', await call('list_exams', {'limit': 3}))

    # ── 考试与统计分析 ─────────────────────────────────────────
    exams_text = await call('list_exams', {'limit': 1})
    try:
        exams = (json.loads(exams_text).get('data') or {}).get('exams') or []
    except Exception:
        exams = []
    if exams:
        exam_id = exams[0]['id']
        check('get_exam_overview',
              await call('get_exam_overview', {'exam_id': exam_id}),
              must_contain=['student_count'])
        check('get_class_score_summary',
              await call('get_class_score_summary', {'exam_id': exam_id}),
              must_contain=['classes'])
        check('get_subject_analysis', await call('get_subject_analysis', {'exam_id': exam_id}))
        check('get_exam_score_segments',
              await call('get_exam_score_segments', {'exam_id': exam_id}))
        layer_text = await call('get_layer_online_stats', {'exam_id': exam_id})
        if '"ok": true' in layer_text and 'layers' in layer_text:
            PASS.append('get_layer_online_stats')
            print('PASS  get_layer_online_stats')
        elif '"ok": false' in layer_text and '未配置分层线' in layer_text:
            PASS.append('get_layer_online_stats')
            print('PASS  get_layer_online_stats（本场未配置分层线，返回中文提示）')
        else:
            FAIL.append(('get_layer_online_stats', '返回既非成功也非预期提示', layer_text[:200]))
            print('FAIL  get_layer_online_stats')
        check('compare_classes 班级不足时提示',
              await call('compare_classes', {'exam_id': exam_id, 'class_names': '01班'}),
              expect_ok=False, must_contain=['不足'])
    else:
        print('注意：库中没有考试数据，跳过成绩类工具检查')

    # ── 宿舍 / 课表 ────────────────────────────────────────────
    check('get_dorm_stats', await call('get_dorm_stats', {}))
    check('list_dorm_buildings', await call('list_dorm_buildings', {}))
    check('list_dorm_rooms', await call('list_dorm_rooms', {'per_page': 3}))
    check('list_schedules', await call('list_schedules', {}))

    # ── 学生维度 ───────────────────────────────────────────────
    students_text = await call('search_students', {'limit': 1})
    try:
        stu = (json.loads(students_text).get('data') or {}).get('students') or []
    except Exception:
        stu = []
    if stu:
        no, grade, cls = stu[0]['student_number'], stu[0]['grade'], stu[0]['class_name']
        check('get_student', await call('get_student', {'student_no': no}),
              must_contain=['student_number'])
        check('get_student_scores(考试清单)',
              await call('get_student_scores', {'student_no': no, 'exam_id': 0}))
        check('get_student_accommodation',
              await call('get_student_accommodation', {'student_no': no}))
        check('get_student_portrait', await call('get_student_portrait', {'student_no': no}))
        check('list_portrait_comments', await call('list_portrait_comments', {'student_no': no}))
        check('list_portrait_events', await call('list_portrait_events', {'student_no': no}))
        check('get_student_attendance', await call('get_student_attendance', {'student_no': no}))
        check('get_attendance_stats',
              await call('get_attendance_stats', {'class_name': cls, 'grade': grade}))
        check('get_attendance_records',
              await call('get_attendance_records', {'class_name': cls, 'grade': grade}))
        no_grade_text = await call('get_attendance_records', {'class_name': cls})
        if '"ok": true' in no_grade_text:
            PASS.append('get_attendance_records(省略 grade 按可见范围确定年级)')
            print('PASS  get_attendance_records(省略 grade 自动反查年级)')
        else:
            check('get_attendance_records(省略 grade 多义时明确提示)',
                  no_grade_text, expect_ok=False, must_contain=['请同时指定 grade'])
    else:
        print('注意：库中没有学生数据，跳过学生类工具检查')

    # ── 数据范围与功能权限（需要库中存在受限角色账号） ────────────
    restricted = None
    try:
        restricted = core_db.run(_pick_restricted_username)
    except Exception as exc:                       # noqa: BLE001
        print(f'注意：受限角色用例准备失败（{type(exc).__name__}），已跳过')
    if restricted:
        bootstrap.warm_up(restricted)
        exams_text_r = await call('list_exams', {'limit': 1})
        exam_id_r = None
        try:
            rows_r = (json.loads(exams_text_r).get('data') or {}).get('exams') or []
            exam_id_r = rows_r[0]['id'] if rows_r else None
        except Exception:                          # noqa: BLE001
            exam_id_r = None
        if exam_id_r:
            check('受限账号：学科分析被角色矩阵拒绝',
                  await call('get_subject_analysis', {'exam_id': exam_id_r}),
                  expect_ok=False, must_contain=['无权'])
            check('受限账号：班级对比被角色矩阵拒绝',
                  await call('compare_classes', {'exam_id': exam_id_r,
                                                 'class_names': '01班,02班'}),
                  expect_ok=False, must_contain=['无权'])
        else:
            print('注意：受限账号看不到任何考试，跳过分析维度用例')
        can_view_dorm = core_db.run(
            lambda: bool(bootstrap.load_bound_user().has_perm('statistics.view')))
        dorm_text = await call('get_dorm_stats', {})
        if can_view_dorm:
            check('受限账号：宿舍统计（该账号有 statistics.view，应允许）', dorm_text)
        else:
            check('受限账号：宿舍统计被权限点拒绝', dorm_text,
                  expect_ok=False, must_contain=['权限'])
        bootstrap.warm_up('admin')
        check('切回 admin：宿舍统计恢复可用', await call('get_dorm_stats', {}),
              must_contain=['total_rooms'])
    else:
        print('注意：未找到"班主任/任课教师 + 班级范围"的受限账号，跳过受限角色用例')

    # ── 安全与容错 ─────────────────────────────────────────────
    check('写工具被禁用时拒绝',
          await call('record_attendance',
                     {'records': [{'student_no': 'x', 'attend_date': '2026-10-11'}]}),
          expect_ok=False, must_contain=['写操作已被禁用'])
    check('非法学号返回中文提示',
          await call('get_student_scores', {'student_no': '不存在的学号', 'exam_id': 0}),
          expect_ok=False, must_contain=['学号不存在'])

    # 写路径入参校验：打开开关后用不存在的学号触发，确保在写库前就被拦下
    cfg['allow_write'] = True
    config._cache = cfg
    check('写路径在写库前拦截非法学号',
          await call('record_attendance',
                     {'records': [{'student_no': '不存在的学号', 'attend_date': '2026-10-11'}]}),
          expect_ok=False, must_contain=['学号不存在'])
    # 写路径校验日期格式：用真实学号 + 非法日期，应在写库前被拦下
    if stu:
        check('写路径校验日期格式',
              await call('add_portrait_event',
                         {'student_no': stu[0]['student_number'], 'title': '自测',
                          'event_date': '不是日期'}),
              expect_ok=False, must_contain=['格式'])
    cfg['allow_write'] = False
    config._cache = cfg

    # ── 接入认证（account 模式：账号密码 + 拒绝策略，端到端） ──────
    await _account_mode_cases()

    print(f'\n===== MCP 自测完成：通过 {len(PASS)} / 失败 {len(FAIL)} =====')
    for name, problem, _snippet in FAIL:
        print(f'  失败 {name}: {problem}')
    return 1 if FAIL else 0


_SMOKE_PWD = 'SmokePwd!2345'


def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _start_http(cfg):
    """起一个真实 HTTP 服务（uvicorn 线程），返回 (server, port, cfg)。"""
    port = _free_port()
    cfg = dict(cfg, host='127.0.0.1', port=port)   # 端口同时用于 DNS 重绑定白名单
    app, _server = build_asgi_app(cfg)
    http = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                         log_level='warning'))
    threading.Thread(target=http.run, daemon=True).start()
    for _ in range(200):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                return http, port, cfg
        except OSError:
            time.sleep(0.05)
    raise RuntimeError('HTTP 服务未在预期时间内就绪')


async def _raw_post(port, headers):
    """裸 HTTP 请求（不经 SDK 会话）：用于验证 401/429 这类连接层拒绝。"""
    client = create_mcp_http_client(headers=headers or None)
    try:
        return await client.post(
            f'http://127.0.0.1:{port}/mcp',
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                  'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                             'clientInfo': {'name': 'smoke', 'version': '1'}}},
            headers={'accept': 'application/json, text/event-stream'})
    finally:
        await client.aclose()


async def _sdk_session(port, headers, tool='list_grades', args=None):
    """完整 SDK 会话：initialize + list_tools + 调用一次工具；返回 (工具数, 文本)。"""
    client = create_mcp_http_client(headers=headers)
    try:
        async with streamable_http_client(f'http://127.0.0.1:{port}/mcp',
                                          http_client=client) as transport:
            async with ClientSession(transport[0], transport[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool(tool, args or {})
                text = result.content[0].text if result.content else ''
                return len(tools.tools), text
    finally:
        await client.aclose()


def _check_status(name, status, expect):
    if status == expect:
        PASS.append(name)
        print(f'PASS  {name}（HTTP {status}）')
    else:
        FAIL.append((name, f'期望 HTTP {expect}，实际 {status}', ''))
        print(f'FAIL  {name}（期望 {expect}，实际 {status}）')


def _pick_access_probe_user():
    """挑一个"角色受限 + 班级范围"的账号（会被临时改密码用于认证测试）。"""
    from app.models.user import User
    from app.modules.grades.services import scope as scope_service
    for user in (User.query.filter(User.is_active.is_(True),
                                   User.role.in_(['homeroom_teacher', 'teacher']))
                 .order_by(User.id).limit(30).all()):
        if scope_service.user_grade_scope(user) is not None:
            continue
        if scope_service.get_scope(user)[0] == 'class':
            return user
    return None


async def _account_mode_cases():
    """account 模式端到端：401 / 429 拒绝策略 + 认证通过后的权限控制。"""
    print('\n── 接入认证（account 模式） ──')
    base_cfg = dict(config.load_config())
    username, old_hash, http_server = None, None, None
    try:
        from app.extensions import db
        with bootstrap.get_app().app_context():
            target = _pick_access_probe_user()
            if target is None:
                print('注意：未找到受限账号，跳过接入认证用例')
                return
            username, old_hash = target.username, target.password_hash
            target.set_password(_SMOKE_PWD)
            db.session.commit()
            perms = {k: target.has_perm(k) for k in
                     ('students.view', 'statistics.view')}

        cfg = dict(base_cfg, auth_mode='account', require_token=False,
                   login_fail_limit=5, login_lock_seconds=30)
        config._cache = cfg
        http_server, port, _ = _start_http(cfg)

        resp = await _raw_post(port, None)
        _check_status('接入认证：无凭据被拒', resp.status_code, 401)
        if 'unauthorized' not in resp.text:
            FAIL.append(('接入认证：无凭据错误体', '缺少 unauthorized 错误码', resp.text[:200]))

        resp = await _raw_post(port, {'x-stulink-user': username, 'x-stulink-password': 'x'})
        _check_status('接入认证：错误密码被拒', resp.status_code, 401)

        basic = base64.b64encode(f'{username}:{_SMOKE_PWD}'.encode()).decode()
        count, text = await _sdk_session(port, {'Authorization': f'Basic {basic}'})
        if count > 0 and '"ok": true' in text:
            PASS.append('接入认证：正确凭据进入 MCP 并调用工具')
            print(f'PASS  接入认证：正确凭据进入 MCP（工具 {count} 个，list_grades 成功）')
        else:
            FAIL.append(('接入认证：正确凭据进入 MCP', '会话或工具调用失败', text[:200]))
            print('FAIL  接入认证：正确凭据进入 MCP')

        count2, _ = await _sdk_session(port, {'X-StuLink-User': username,
                                              'X-StuLink-Password': _SMOKE_PWD})
        if count2 == count:
            PASS.append('接入认证：X-StuLink-User/Password 头部方式')
            print('PASS  接入认证：X-StuLink-User/Password 头部方式')
        else:
            FAIL.append(('接入认证：头部方式', f'工具数不一致 {count2} != {count}', ''))

        if not perms.get('statistics.view'):
            _, dorm_text = await _sdk_session(
                port, {'X-StuLink-User': username, 'X-StuLink-Password': _SMOKE_PWD},
                tool='get_dorm_stats')
            if '"ok": false' in dorm_text and '权限' in dorm_text:
                PASS.append('接入认证：越权工具按账号权限被拒')
                print('PASS  接入认证：越权工具按账号权限被拒')
            else:
                FAIL.append(('接入认证：越权工具应被拒', '未返回权限不足', dorm_text[:200]))
                print('FAIL  接入认证：越权工具应被拒')

        codes = []
        resp = None
        for _ in range(6):
            resp = await _raw_post(port, {'x-stulink-user': '__smoke_no_user__',
                                          'x-stulink-password': 'x'})
            codes.append(resp.status_code)
        if codes[-1] == 429 and resp is not None and resp.headers.get('retry-after'):
            PASS.append('接入认证：连续失败触发锁定 429')
            print(f'PASS  接入认证：连续失败触发锁定（{codes}，'
                  f'retry-after={resp.headers.get("retry-after")}）')
        else:
            FAIL.append(('接入认证：连续失败锁定', f'状态码序列 {codes}', ''))
            print(f'FAIL  接入认证：连续失败锁定（{codes}）')
    except Exception as exc:                                        # noqa: BLE001
        FAIL.append(('接入认证：端到端用例执行', f'{type(exc).__name__}: {exc}', ''))
        print(f'FAIL  接入认证：端到端用例执行异常 {type(exc).__name__}: {exc}')
    finally:
        if http_server is not None:
            http_server.should_exit = True
        config._cache = base_cfg
        if username and old_hash:
            try:
                from app.extensions import db
                from app.models.user import User
                with bootstrap.get_app().app_context():
                    user = User.query.filter_by(username=username).first()
                    if user and user.password_hash != old_hash:
                        user.password_hash = old_hash
                        db.session.commit()
                        print(f'      （已恢复 {username} 的原密码哈希）')
            except Exception as exc:                                # noqa: BLE001
                print(f'      警告：恢复 {username} 密码哈希失败：{exc}')


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
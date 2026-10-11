# -*- coding: utf-8 -*-
"""StuLink MCP 服务回归自测：注册全部工具并逐个调用，验证端到端可用。

用法（在项目根目录执行）：
    mcp_server\\.venv\\Scripts\\python.exe mcp_server\\smoke_mcp.py

覆盖：工具注册与 schema 生成、只读工具真实取数、权限/参数错误的中文提示、
      写工具开关、写路径的入参校验（用不存在的学号触发，不写任何真实数据）。

运行前提：mcp_server/config.json 存在（首次启动服务会自动生成）。
"""
import asyncio
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server import config                                        # noqa: E402
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


async def main():
    cfg = config.load_config()
    cfg['bind_user'] = 'admin'
    cfg['allow_write'] = False          # 自测不写真实数据
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
    else:
        print('注意：库中没有学生数据，跳过学生类工具检查')

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

    print(f'\n===== MCP 自测完成：通过 {len(PASS)} / 失败 {len(FAIL)} =====')
    for name, problem, _snippet in FAIL:
        print(f'  失败 {name}: {problem}')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
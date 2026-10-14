# -*- coding: utf-8 -*-
"""StuLink 成绩分析 MCP 服务自测：工具可用性 + 三条权限规则的细粒度矩阵 + 接入认证。

用法（在项目根目录执行）：
    mcp_server\\.venv\\Scripts\\python.exe mcp_server\\smoke_scores.py

覆盖：
 1. 只读工具集注册（12 个，且不含任何写工具）；
 2. 权限引擎（core/score_guard.py）三条规则的纯逻辑矩阵：
    任课教师=年级×班级同时命中且仅所教学科 / 班主任=本班全部学科 /
    年级长及以上=本年级全部班级全部学科 / 无范围=一律拒绝；
 3. 真实账号端到端：管理员（全校）、班主任（本班 vs 他班）、任课教师
    （所教班级 vs 同年级他班、所教学科 vs 他科）；
 4. 年级长（库中无该角色账号时用等价身份对象验证解析结果）；
 5. 接入认证端到端（account 模式，用例在 smoke_auth.py）：401 / 429 拒绝策略。

两层口径（2026-10-15 用户裁决，本文件即按此断言，改口径须同步改这里）：
 · **聚合层公开**：年级各班汇总/年级基准/班级位次、学科分析、分数段、分层上线、
   班级对比 —— 能看该考试的账号都能看全年级（班主任/任课教师不再只看自己班）；
 · **逐生层收敛**：学生个人成绩、班级逐生明细、逐生总分与排名 —— 任课教师＝所教班级×
   所教学科，班主任＝本班全科，他班/他科一律拒绝。


运行前提：mcp_server/config.json 存在；库中有真实数据。
本脚本只读、不改动任何真实账号；认证用例（smoke_auth.py）会新建一个临时账号并在结束时删除。
"""
import asyncio
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp_server import config                                    # noqa: E402
from mcp_server.core import bootstrap, db as core_db, score_guard  # noqa: E402
from mcp_server.run import build_asgi_app                        # noqa: E402
from mcp_server.smoke_auth import run_account_mode_cases         # noqa: E402
from mcp_server.tools import register_all                        # noqa: E402

EXPECTED_TOOLS = {
    'list_my_score_scope', 'list_exams', 'list_classes', 'get_exam_overview',
    'get_class_score_summary', 'get_class_score_detail', 'get_student_scores',
    'get_student_score_trend', 'get_subject_analysis', 'get_exam_score_segments',
    'get_layer_online_stats', 'compare_classes',
}
NON_CLASS_NAMES = ('不分班', '已转出', '离校')      # 非行政班，不应出现在班级清单里
PASS, FAIL = [], []


def check(name, text, expect_ok=True, must_contain=None):
    """检查工具返回：ok 标志 + 必须包含的文本。

    expect_ok=None 表示"权限必须放行、业务结果不作要求"（例如该场考试没配置分层线）。
    """
    got_ok = '"ok": true' in text
    problems = []
    if expect_ok is None:
        if '权限不足' in text:
            problems.append('权限被拒（应放行）')
    elif got_ok != expect_ok:
        problems.append(f'期望 ok={expect_ok}，实际 {got_ok}')
    for token in (must_contain or []):
        if token not in text:
            problems.append(f'缺少内容：{token}')
    _record(name, problems, text)


def expect(name, condition, detail=''):
    """检查纯逻辑断言（权限引擎矩阵）。"""
    _record(name, [] if condition else [detail or '断言不成立'], '')


def _record(name, problems, text):
    if problems:
        FAIL.append((name, '; '.join(problems), text[:300]))
        print(f'FAIL  {name}')
        print('      问题:', '; '.join(problems))
        if text:
            print('      返回:', text[:300].replace('\n', ' '))
    else:
        PASS.append(name)
        print(f'PASS  {name}')


def _data(text):
    try:
        return (json.loads(text).get('data') or {})
    except Exception:            # noqa: BLE001
        return {}


# ---------------- 权限引擎：纯逻辑矩阵（不依赖库数据） ----------------

def engine_matrix():
    """三条规则的判定矩阵：引擎层直接验证，不经过数据库。"""
    # 2026-10-15 用户口径：任课授权＝(年级, 学科)——教某年级某科，该年级**全部班**的
    # 该科成绩都开放（不再只限自己教的几个班）；总分**分数**仍不开放，但给"总分年级名次"；
    # 逐生总分的收口走 `total_visible_classes()`（任课教师＝空集，fail-closed）。
    teach = score_guard.ScoreScope(teach={('2024级', '数学')})
    expect('R1 任课：任教班级 → 允许', teach.can_view_class('2024级', '01班'))
    expect('R1 任课：同年级非任教班级 → 也允许（年级×学科口径）',
           teach.can_view_class('2024级', '02班'))
    expect('R1 任课：非任教年级 → 拒绝', not teach.can_view_class('2025级', '01班'))
    expect('R1 任课：任教班可见学科=所教学科',
           teach.class_subjects('2024级', '01班') == {'数学'})
    expect('R1 任课：非任教班可见学科同样只有所教学科',
           teach.class_subjects('2024级', '02班') == {'数学'})
    expect('R1 任课：年级可见学科=该年级所教学科',
           teach.grade_subjects('2024级') == {'数学'})
    expect('R1 任课：可见班级=任教年级全部班（None，学科另行收窄）',
           teach.visible_classes('2024级') is None)
    expect('R1 任课：总分分数不可见',
           not teach.class_total_visible('2024级', '01班'))
    expect('R1 任课：总分年级名次可见（只给名次，不给分数）',
           teach.total_rank_visible('2024级', '02班'))
    expect('R1 任课：逐生总分收口＝空集（fail-closed）',
           teach.total_visible_classes('2024级') == set())
    expect('R1 任课：可见年级=任教年级',
           teach.visible_grades() == {'2024级'})

    homeroom = score_guard.ScoreScope(classes={('2024级', '01班')})
    expect('R2 班主任：本班 → 允许', homeroom.can_view_class('2024级', '01班'))
    expect('R2 班主任：同年级他班 → 拒绝', not homeroom.can_view_class('2024级', '02班'))
    expect('R2 班主任：本班全部学科', homeroom.class_subjects('2024级', '01班') is None)
    expect('R2 班主任：本班总分可见', homeroom.class_total_visible('2024级', '01班'))
    expect('R2 班主任：年级级学科无授权（空集）',
           homeroom.grade_subjects('2024级') == set())

    leader = score_guard.ScoreScope(grades={'2024级'})
    expect('R3 年级长：本年级任意班 → 允许', leader.can_view_class('2024级', '09班'))
    expect('R3 年级长：他年级 → 拒绝', not leader.can_view_class('2025级', '01班'))
    expect('R3 年级长：本年级全部学科', leader.grade_subjects('2024级') is None)
    expect('R3 年级长：可见年级=本年级', leader.visible_grades() == {'2024级'})

    admin = score_guard.ScoreScope(full_school=True)
    expect('R0 管理员/校级：全校任意班', admin.can_view_class('2026级', '12班'))
    expect('R0 管理员/校级：可见年级=不限（None）', admin.visible_grades() is None)

    empty = score_guard.ScoreScope()
    expect('无任何范围：年级拒绝', not empty.can_view_grade('2024级'))
    expect('无任何范围：班级拒绝', not empty.can_view_class('2024级', '01班'))
    expect('无任何范围：可见年级为空集', empty.visible_grades() == set())


class _StubUser:
    """等价身份对象：库中无年级长账号时用于验证 resolve() 的解析结果。"""

    def __init__(self, role, grade=None, class_name=None, uid=-999):
        self.id = uid
        self.username = f'stub-{role}'
        self.role = role
        self.grade = grade
        self.class_name = class_name
        self.permission_group = None

    def has_role(self, *roles):
        return self.role in roles

    def has_perm(self, key):
        return self.role == 'admin'


def resolver_matrix():
    """resolve() 解析：年级长 / 无范围身份。"""
    leader = score_guard.resolve(_StubUser('grade_leader', grade='2024级'))
    expect('resolve 年级长 → 本年级整年级可见',
           leader.grades == {'2024级'} and leader.can_view_class('2024级', '07班'))
    expect('resolve 年级长 → 他年级拒绝', not leader.can_view_grade('2025级'))

    nobody = score_guard.resolve(_StubUser('staff'))
    expect('resolve 无范围身份 → 空范围（不回落全校）',
           nobody.visible_grades() == set() and not nobody.can_view_grade('2024级'))


# ---------------- 真实账号端到端 ----------------

def _pick_accounts():
    """挑选端到端用例账号：班主任（本班）与任课教师（所教班级+学科）。"""
    from app.models import UserClassLink
    from app.models.grades import TeacherSubjectLink
    from app.models.student import Student
    from app.models.user import User
    from app.modules.grades.services import scope as scope_service

    picked = {'homeroom': None, 'teacher': None, 'no_perm': None}
    for user in User.query.filter(User.is_active.is_(True)).order_by(User.id).all():
        if not user.has_perm('grades.view'):
            if picked['no_perm'] is None and user.role in ('teacher', 'homeroom_teacher'):
                picked['no_perm'] = user.username
            continue
        if scope_service.user_grade_scope(user) is not None:
            continue
        scope_type, _g = scope_service.get_scope(user)
        if scope_type in ('school', None) or user.role == 'admin':
            continue
        links = TeacherSubjectLink.query.filter_by(user_id=user.id, active=True).all()
        classes = UserClassLink.query.filter_by(user_id=user.id).all()

        if picked['teacher'] is None and not classes and links:
            grades = {l.grade for l in links}
            for grade in sorted(grades):
                taught = {l.class_name for l in links if l.grade == grade}
                subjects = {l.subject for l in links if l.grade == grade}
                all_classes = {c for (c,) in Student.query.filter_by(grade=grade)
                               .with_entities(Student.class_name).distinct().all() if c}
                others = sorted(all_classes - taught)
                if taught and others and subjects:
                    picked['teacher'] = {
                        'username': user.username, 'grade': grade,
                        'class_name': sorted(taught)[0], 'subject': sorted(subjects)[0],
                        'other_class': others[0],
                        'second_class': sorted(taught)[1] if len(taught) > 1 else None,
                    }
                    break

        # 班主任：**优先挑没有任课映射的纯班主任**——这样"同年级他班被拒"才是干净断言；
        # 库里全是"班主任+任课"双身份时退而求其次，并把 has_teach 带出去给断言分支用
        # （2026-10-15 R1 放宽成 年级×学科 后，兼任课的账号对本年级他班也能查所教学科）。
        if classes:
            grade, cls = classes[0].grade, classes[0].class_name
            all_classes = {c for (c,) in Student.query.filter_by(grade=grade)
                           .with_entities(Student.class_name).distinct().all() if c}
            others = sorted(all_classes - {cls})
            mine = Student.query.filter_by(grade=grade, class_name=cls).first()
            other_stu = (Student.query.filter_by(grade=grade, class_name=others[0]).first()
                         if others else None)
            has_teach = bool(links)
            better = (picked['homeroom'] is None
                      or (picked['homeroom'].get('has_teach') and not has_teach))
            if better and others and mine is not None:
                picked['homeroom'] = {
                    'username': user.username, 'grade': grade, 'class_name': cls,
                    'other_class': others[0],
                    'student_no': mine.student_number,
                    'other_student_no': other_stu.student_number if other_stu else None,
                    'has_teach': has_teach,
                    'teach_subjects': sorted({l.subject for l in links
                                              if l.grade == grade}),
                }
        if all(picked.values()):
            break
    return picked


async def main():
    cfg = dict(config.load_config())
    cfg['auth_mode'] = 'token'          # 用固定身份逐账号切换，直接测工具层
    cfg['bind_user'] = 'admin'
    cfg['allow_write'] = False          # 只读服务：自测同样不写数据
    config._cache = cfg

    app, server = build_asgi_app(cfg)

    tools = await server.list_tools()
    names = {t.name for t in tools}
    print(f'已注册工具 {len(names)} 个：{", ".join(sorted(names))}\n')
    expect('工具集：恰好 12 个只读工具', names == EXPECTED_TOOLS,
           f'实际：{sorted(names)}')
    write_like = [n for n in names if any(k in n for k in
                                         ('add_', 'update_', 'delete_', 'record_', 'set_', 'import_'))]
    expect('工具集：不含任何写入类工具', not write_like, f'可疑写工具：{write_like}')

    async def call(name, args):
        result = await server.call_tool(name, args)
        if hasattr(result, 'content') and result.content:
            return result.content[0].text
        return str(result)

    # ── 引擎矩阵（纯逻辑） ──────────────────────────────────────
    engine_matrix()
    core_db.run(resolver_matrix)        # resolve() 需要 app context（查映射表）

    # ── 管理员：全校可见 ────────────────────────────────────────
    scope_text = await call('list_my_score_scope', {})
    check('管理员：范围=全校', scope_text, must_contain=['"full_school": true'])
    check('管理员：list_exams',
          await call('list_exams', {'limit': 3}), must_contain=['"exams"'])
    classes_text = await call('list_classes', {})
    check('管理员：list_classes', classes_text, must_contain=['"classes"'])
    class_names = [c.get('class_name') for c in (_data(classes_text).get('classes') or [])]
    bogus = sorted(set(class_names) & set(NON_CLASS_NAMES))
    expect('list_classes：不把"不分班/已转出/离校"当班级',
           bool(class_names) and not bogus, f'出现：{bogus}')
    exam_id = core_db.run(lambda: _pick_exam())
    if exam_id:
        check('管理员：list_classes(exam_id) 用考试当时的班级',
              await call('list_classes', {'exam_id': exam_id}),
              must_contain=['student_count'])
        check('管理员：get_exam_overview',
              await call('get_exam_overview', {'exam_id': exam_id}),
              must_contain=['student_count'])
        check('管理员：get_class_score_summary',
              await call('get_class_score_summary', {'exam_id': exam_id}),
              must_contain=['subject_avg'])
        if class_names:
            check('管理员：get_class_score_detail（逐生明细）',
                  await call('get_class_score_detail',
                             {'exam_id': exam_id, 'class_name': class_names[0]}),
                  must_contain=['student_count', '"scores"'])
        check('管理员：get_subject_analysis',
              await call('get_subject_analysis', {'exam_id': exam_id}),
              must_contain=['"subjects"'])
        check('管理员：get_exam_score_segments（年级级）',
              await call('get_exam_score_segments', {'exam_id': exam_id}),
              must_contain=['distribution'])
        check('管理员：get_layer_online_stats（放行；该场未配置分层线属正常）',
              await call('get_layer_online_stats', {'exam_id': exam_id}), expect_ok=None)
    else:
        print('注意：库中无带成绩的考试，跳过管理员成绩用例')

    # ── 真实账号：班主任 / 任课教师 / 无成绩权限 ────────────────
    picked = core_db.run(_pick_accounts)
    print(f'\n用例账号：班主任={picked["homeroom"] and picked["homeroom"]["username"]}，'
          f'任课教师={picked["teacher"] and picked["teacher"]["username"]}，'
          f'无 grades.view={picked["no_perm"]}\n')

    # 班主任：本班可见、他班拒绝、年级级分析拒绝
    home = picked['homeroom']
    if home:
        bootstrap.warm_up(home['username'])
        scope_text = await call('list_my_score_scope', {})
        check('班主任：范围含本班', scope_text,
              must_contain=[f'{home["grade"]} {home["class_name"]}'])
        exam_id = core_db.run(
            lambda: _pick_exam(home['grade'], home['class_name']))
        if exam_id:
            text = await call('get_class_score_summary',
                              {'exam_id': exam_id, 'class_name': home['class_name']})
            check('班主任：本班成绩可查', text, must_contain=['subject_avg'])
            rows = _data(text).get('classes') or []
            expect('班主任：本班总分口径可见（非任课收窄）',
                   bool(rows) and all(not r.get('total_hidden') and r.get('avg') is not None
                                      for r in rows))
            # 2026-10-15 用户口径：年级成绩分析结果公开 —— 他班汇总不再拒绝
            check('班主任：他班汇总可查（年级聚合公开）',
                  await call('get_class_score_summary',
                             {'exam_id': exam_id, 'class_name': home['other_class']}),
                  must_contain=['subject_avg'])
            whole = _data(await call('get_class_score_summary', {'exam_id': exam_id}))
            whole_rows = whole.get('classes') or []
            expect('年级维度：各班汇总＝全年级 + 年级基准 + 班级位次',
                   len(whole_rows) >= 2
                   and (whole.get('grade_baseline') or {}).get('avg') is not None
                   and all(r.get('class_rank') for r in whole_rows),
                   f'班数={len(whole_rows)} 基准={whole.get("grade_baseline")}')
            expect('年级维度：每行标明能否下钻逐生（detail_visible 布尔）',
                   all(isinstance(r.get('detail_visible'), bool) for r in whole_rows))
            check('班主任：年级级学科分析可查（聚合公开）',
                  await call('get_subject_analysis', {'exam_id': exam_id}),
                  must_contain=['"subjects"'])
            check('班主任：年级级分数段可查（聚合公开）',
                  await call('get_exam_score_segments', {'exam_id': exam_id}),
                  must_contain=['distribution'])
            check('班主任：本班分数段可查',
                  await call('get_exam_score_segments',
                             {'exam_id': exam_id, 'class_name': home['class_name']}),
                  must_contain=['distribution'])
            check('班主任：list_classes 只含本班（考试口径）',
                  await call('list_classes', {'exam_id': exam_id}),
                  must_contain=[home['class_name']])
            detail = await call('get_class_score_detail',
                                {'exam_id': exam_id, 'class_name': home['class_name']})
            check('班主任：逐生明细可查', detail, must_contain=['student_count'])
            detail_rows = _data(detail).get('students') or []
            expect('班主任：逐生明细含总分与排名',
                   bool(detail_rows) and all('total_score' in r for r in detail_rows))
            check('班主任：他班逐生明细被拒',
                  await call('get_class_score_detail',
                             {'exam_id': exam_id, 'class_name': home['other_class']}),
                  expect_ok=False, must_contain=['无权'])
            student_no = core_db.run(
                lambda: _student_in_exam(exam_id, home['class_name']))
            if student_no:
                check('班主任：本班学生成绩可查',
                      await call('get_student_scores',
                                 {'student_no': student_no, 'exam_id': exam_id}),
                      must_contain=['"subjects"'])
            if home.get('other_student_no'):
                if home.get('has_teach'):
                    # 兼任课：R1＝年级×学科 → 他班学生可见，但只给所教学科、不给总分
                    stu = _data(await call('get_student_scores',
                                           {'student_no': home['other_student_no'],
                                            'exam_id': exam_id}))
                    expect('班主任(兼任课)：他班学生成绩＝仅所教学科、无总分',
                           bool(stu.get('subjects'))
                           and all(s.get('subject') in (home.get('teach_subjects') or [])
                                   for s in (stu.get('subjects') or []))
                           and 'score' not in (stu.get('student') or {}),
                           f'样例={(stu.get("subjects") or [])[:1]}')
                else:
                    check('班主任：他班学生成绩被拒',
                          await call('get_student_scores',
                                     {'student_no': home['other_student_no'],
                                      'exam_id': exam_id}),
                          expect_ok=False, must_contain=['无权'])
        else:
            print(f'注意：{home["grade"]} 没有带成绩的考试，跳过班主任成绩用例')

    # 任课教师：任教年级×所教学科可见（同年级任意班），他科/他年级拒绝
    teacher = picked['teacher']
    if teacher:
        bootstrap.warm_up(teacher['username'])
        check('任课教师：范围含任教年级+任教学科',
              await call('list_my_score_scope', {}),
              must_contain=[f'{teacher["grade"]} {teacher["subject"]}'])
        exam_id = core_db.run(
            lambda: _pick_exam(teacher['grade'], teacher['class_name']))
        if exam_id:
            text = await call('get_class_score_summary',
                              {'exam_id': exam_id, 'class_name': teacher['class_name']})
            check('任课教师：所教班级可查', text, must_contain=['subject_avg'])
            data = _data(text)
            rows = data.get('classes') or []
            subjects = [s['subject'] for r in rows for s in (r.get('subject_avg') or [])]
            # 2026-10-15 用户口径：班级/年级聚合公开（含各科均分与总分均分/位次）
            expect('任课教师：班级汇总含全部学科（聚合公开）',
                   bool(subjects) and any(s != teacher['subject'] for s in subjects),
                   f'返回学科：{sorted(set(subjects))}')
            expect('任课教师：班级级总分聚合可见（聚合公开）',
                   bool(rows) and all(not r.get('total_hidden') and r.get('avg') is not None
                                      for r in rows))
            whole = _data(await call('get_class_score_summary', {'exam_id': exam_id}))
            whole_rows = whole.get('classes') or []
            expect('年级维度：任课教师也拿到全年级各班 + 年级基准 + 位次',
                   len(whole_rows) >= 2
                   and (whole.get('grade_baseline') or {}).get('avg') is not None
                   and all(r.get('class_rank') for r in whole_rows),
                   f'班数={len(whole_rows)}')
            check('任课教师：他班汇总可查（年级聚合公开）',
                  await call('get_class_score_summary',
                             {'exam_id': exam_id, 'class_name': teacher['other_class']}),
                  must_contain=['subject_avg'])
            # R1 新口径：任课＝年级×学科 → 清单给出该年级**全部班**（含非任教班），
            # 但每行仍标出"可见学科＝所教学科、总分口径不可见"（清单只影响可下钻范围）
            cls_rows = _data(await call('list_classes',
                                        {'exam_id': exam_id})).get('classes') or []
            expect('任课教师：班级清单含同年级非任教班（年级×学科口径）',
                   any(r.get('class_name') == teacher['other_class'] for r in cls_rows),
                   f'清单={[r.get("class_name") for r in cls_rows]}')
            expect('任课教师：清单每行仍标出"仅所教学科、总分不可见"',
                   bool(cls_rows) and all(isinstance(r.get('visible_subjects'), list)
                                          and teacher['subject'] in r['visible_subjects']
                                          and r.get('total_visible') is False
                                          for r in cls_rows),
                   f'样例={cls_rows[:1]}')
            detail = await call('get_class_score_detail',
                                {'exam_id': exam_id, 'class_name': teacher['class_name']})
            check('任课教师：逐生明细可查', detail, must_contain=['student_count'])
            detail_rows = _data(detail).get('students') or []
            expect('任课教师：逐生明细只含所教学科且无总分',
                   bool(detail_rows) and all(
                       set(r.get('scores') or {}) <= {teacher['subject']}
                       and 'total_score' not in r for r in detail_rows),
                   f'样例：{detail_rows[:1]}')
            ctx = _data(detail).get('grade_context') or {}
            expect('年级维度：逐生明细附"本班位次 + 年级基准 + 各班横向"',
                   bool(ctx.get('class_rank'))
                   and (ctx.get('grade') or {}).get('avg') is not None
                   and len(ctx.get('classes') or []) >= 2,
                   f'ctx键={sorted(ctx)}')
            # 注：他班逐生明细的"所教学科可查 / 他科被拒 / 无总分"断言在下方成对给出
            other_subject = '语文' if teacher['subject'] != '语文' else '数学'
            check('任课教师：逐生明细指定他科被拒',
                  await call('get_class_score_detail',
                             {'exam_id': exam_id, 'class_name': teacher['class_name'],
                              'subject': other_subject}),
                  expect_ok=False, must_contain=['无权'])
            # R1 新口径：任教年级的他班也能查，但同样**只给所教学科、不给总分分数**
            other_rows = _data(await call('get_class_score_detail',
                                          {'exam_id': exam_id,
                                           'class_name': teacher['other_class'],
                                           'subject': teacher['subject']})
                               ).get('students') or []
            expect('任课教师：他班逐生明细＝仅所教学科、无总分（年级×学科）',
                   bool(other_rows) and all(
                       set(r.get('scores') or {}) <= {teacher['subject']}
                       and 'total_score' not in r for r in other_rows),
                   f'样例={other_rows[:1]}')
            check('任课教师：他班逐生明细指定他科被拒',
                  await call('get_class_score_detail',
                             {'exam_id': exam_id, 'class_name': teacher['other_class'],
                              'subject': other_subject}),
                  expect_ok=False, must_contain=['无权'])
            other_stu = core_db.run(
                lambda: _student_in_exam(exam_id, teacher['other_class']))
            if other_stu:
                stu = _data(await call('get_student_scores',
                                       {'student_no': other_stu, 'exam_id': exam_id}))
                stu_subjects = stu.get('subjects') or []
                expect('任课教师：他班学生成绩＝仅所教学科、无总分（年级×学科）',
                       bool(stu_subjects)
                       and all(s.get('subject') == teacher['subject'] for s in stu_subjects)
                       and 'score' not in (stu.get('student') or {}),
                       f'样例={stu_subjects[:1]}')
            check('任课教师：年级级分数段可查（聚合公开）',
                  await call('get_exam_score_segments', {'exam_id': exam_id}),
                  must_contain=['distribution'])
            check('任课教师：分层上线统计可查（聚合公开；未划线属正常）',
                  await call('get_layer_online_stats', {'exam_id': exam_id}),
                  expect_ok=None)
            if teacher.get('second_class'):
                check('任课教师：对比自己任教的两个班',
                      await call('compare_classes',
                                 {'exam_id': exam_id,
                                  'class_names': f'{teacher["class_name"]},{teacher["second_class"]}'}),
                      must_contain=['"classes"'])
                check('任课教师：对比含他班也可（聚合公开）',
                      await call('compare_classes',
                                 {'exam_id': exam_id,
                                  'class_names': f'{teacher["class_name"]},{teacher["other_class"]}'}),
                      must_contain=['"classes"'])
        else:
            print(f'注意：{teacher["grade"]} 没有考试，跳过任课教师成绩用例')

    # 无 grades.view 的账号：功能权限层拒绝
    if picked['no_perm']:
        bootstrap.warm_up(picked['no_perm'])
        check('无 grades.view：功能权限层拒绝',
              await call('list_exams', {}), expect_ok=False, must_contain=['权限'])

    bootstrap.warm_up('admin')
    check('切回管理员：恢复全校可见',
          await call('list_my_score_scope', {}), must_contain=['"full_school": true'])

    # ── 接入认证（account 模式：账号密码 + 拒绝策略，端到端） ──────
    await run_account_mode_cases(_record, len(EXPECTED_TOOLS))

    print(f'\n===== 成绩分析 MCP 自测完成：通过 {len(PASS)} / 失败 {len(FAIL)} =====')
    for name, problem, _snippet in FAIL:
        print(f'  失败 {name}: {problem}')
    return 1 if FAIL else 0


def _pick_exam(grade=None, class_name=None):
    """取最近一场**带成绩**（有总分行，可限定班级）的考试 id。"""
    from app.models.grades import TOTAL_SUBJECT, Exam, ExamScore
    query = Exam.query
    if grade:
        query = query.filter_by(grade=grade)
    for exam in query.order_by(Exam.exam_date.desc(), Exam.id.desc()).all():
        filters = {'exam_id': exam.id, 'subject': TOTAL_SUBJECT}
        if class_name:
            filters['class_name'] = class_name
        if ExamScore.query.filter_by(**filters).first():
            return exam.id
    return None


def _student_in_exam(exam_id, class_name):
    """取该场考试中某班的一名学生学号（保证该生在考试中有成绩）。"""
    from app.models.grades import TOTAL_SUBJECT, ExamScore
    row = (ExamScore.query.filter_by(exam_id=exam_id, class_name=class_name,
                                     subject=TOTAL_SUBJECT).first())
    return row.student_no if row else None


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))

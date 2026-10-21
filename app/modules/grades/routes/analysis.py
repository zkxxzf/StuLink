# StuLink v1.19.50.55 2026-10-10
# 成绩分析：主页（四 tab）+ options/analysis API + AI 预留
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import hashlib

from flask import (render_template, request, jsonify, abort, current_app,
                   flash, redirect, url_for)
# 2026-10-13：redirect / url_for 随 index_legacy 一并移除（/grades/ 已改由 dashboard 承担）
# 2026-10-16：汇总打印版又需要 flash/redirect/url_for（无 exam 参数时引路），故重新引入
from flask_login import login_required, current_user
from app.models.grades import Exam, ExamScore, TeacherSubjectLink
from app.models import Student
from app.modules.grades import bp
from app.modules.grades.services import tab_service, scope as scope_service
from app.modules.grades.services import compare_service
from app.modules.grades.services import near_line_view
from app.modules.grades.utils import numeric_classes
from app.utils.decorators import perm_required
from app.utils.cache import cache


# 2026-10-13：原 /grades/ → 历次考试的重定向（index_legacy）已移出，
# 该地址现由 routes/dashboard.py 承担「首页仪表盘」（用户口径：成绩模块默认落地页＝仪表盘）。


@bp.route('/exams/<int:exam_id>/near-line')
@login_required
@perm_required('grades.view')
def exam_near_line(exam_id):
    """临界生对比（v1.19.0）

    口径：总分线**线上 above 分内**（危险：可能掉下来）/ **线下 below 分内**（希望：可能冲上去）
    默认 10 / 20 分，可在页面上调；复用 report_warning_service.near_line_report 的计算。
    cls（可选）：按班级筛选，与成绩单「按班级查看」同名同口径（总分行＝考试当时班级）。
    本页在“考试上下文”内（侧栏会切成该场考试的悬浮流程栏）。

    2026-10-15 用户口径（数据范围）：名单是**逐生总分/各科明细**，必须按账号范围收敛——
    班主任只见本班、年级长及以上见整年级（口径与 MCP/AI 同一份 `score_guard`；
    侧栏原本就写着"数据范围由后端 scope 过滤"，此前漏在本页落地）。
    另外：临界生是"总分划线上下"的概念，**任课班级（仅单科授权）不含总分口径**，
    与其他页面一致不进名单（否则等于用临界生名单绕过总分口径）。
    """
    # 参数解析 + 数据范围收敛统一在 services/near_line_view，页面与导出同一份
    # （导出见 routes/near_line_export.py，别各写一套 scope）。
    ctx = near_line_view.load(exam_id, request.args)
    return render_template('grades/near_line.html', **ctx, title='临界生对比')


@bp.route('/analysis')
@login_required
@perm_required('grades.view')
def index():
    # v1.19.0 考试上下文：?exam=<id> 让本页进入“考试态”（侧栏切换到本场流程，并默认选中该场）
    raw = (request.args.get('exam') or '').strip()
    initial_exam_id = int(raw) if raw.isdigit() else None
    # v1.19.0 三分栏深链：侧栏「班级分析 / 学科分析」等直接落到对应页签（?tab=class|subject|…）
    tab = (request.args.get('tab') or '').strip()
    if tab not in ('grade', 'class', 'subject', 'teacher', 'compare'):
        tab = ''
    return render_template('grades/index.html', initial_exam_id=initial_exam_id,
                           initial_tab=tab)


@bp.route('/alumni')
@login_required
@perm_required('grades.view')
def alumni():
    """往届成绩查询（二期，预留占位）"""
    return render_template('grades/alumni.html')


# ==================== 筛选选项 ====================

# PERF-06/12：「每场考试的班级」原先对 exam_scores（数十万行）做无 WHERE 的
# DISTINCT 全表扫描 + 临时 B-tree 去重，且每次打开分析页都跑一次（首屏第一个请求，
# 300~800ms）。改为 ①GROUP BY 走 (exam_id, class_name) 索引前缀（免排序）
# ②结果按进程缓存 300s（班级枚举低频变化，成绩导入时由 invalidate_exam_cache 清除）。
_CLASSES_BY_EXAM_KEY = 'grades_options_classes_by_exam'
_CLASSES_BY_EXAM_TTL = 300


def _classes_by_exam():
    """每场考试的班级（考试当时班级口径），带进程内缓存"""
    from app.utils.cache import cache
    cached_value = cache.get(_CLASSES_BY_EXAM_KEY)
    if cached_value is not None:
        return cached_value
    out = {}
    try:
        from app.models.grades import ExamScore, ExamTeacherLink, TOTAL_SUBJECT
        from app.extensions import db
        tmp = {}
        # 2026-10-14 第四轮审计修复（PERF-112 / CONS-05）：班级口径必须取**总分行**
        # （subject='总分'＝考试当时班级，全库唯一权威口径）——原实现按全部行
        # （含科目行）GROUP BY，选科分班后的科目行班级会与考试当时不一致。
        for eid, cn in (db.session.query(ExamScore.exam_id, ExamScore.class_name)
                        .filter(ExamScore.subject == TOTAL_SUBJECT)
                        .group_by(ExamScore.exam_id, ExamScore.class_name).all()):
            tmp.setdefault(eid, set()).add(cn)
        try:
            for eid, cn in (db.session.query(ExamTeacherLink.exam_id,
                                             ExamTeacherLink.class_name)
                            .group_by(ExamTeacherLink.exam_id,
                                      ExamTeacherLink.class_name).all()):
                tmp.setdefault(eid, set()).add(cn)
        except Exception as _e:  # noqa: BLE001
            # 旧库无任课快照表时只靠学生名册；留 debug 便于排查（2026-10-11 审计补）
            current_app.logger.debug('任课快照表不可用，按学生名册取班级: %s', _e)
        out = {str(k): numeric_classes(list(v)) for k, v in tmp.items()}
    except Exception as e:
        current_app.logger.warning(f'按考试取班级失败: {e}')
        out = {}
    cache.set(_CLASSES_BY_EXAM_KEY, out, timeout=_CLASSES_BY_EXAM_TTL)
    return out


_COMBOS_BY_EXAM_KEY = 'grades_options_combos_by_exam'
_COMBOS_BY_EXAM_TTL = 300


def _combos_by_exam():
    """每场考试出现过的选科组合（总分行的 subject_selection 快照），带进程内缓存

    2026-10-16 第七轮审计修复（P-M4）：原实现挂在 /api/options 内**逐场**一条
    DISTINCT 查询（可见 20-40 场 → 每次打开分析页 20-40 条跨行扫描），且不随
    grades_options_ 前缀失效。改为单条 GROUP BY 一次取回 + 300s 进程缓存
    （与班级枚举同一 TTL / 同一失效前缀）。
    """
    from app.utils.cache import cache
    cached_value = cache.get(_COMBOS_BY_EXAM_KEY)
    if cached_value is not None:
        return cached_value
    out = {}
    try:
        from app.models.grades import ExamScore, TOTAL_SUBJECT
        from app.extensions import db
        rows = (db.session.query(ExamScore.exam_id, ExamScore.subject_selection)
                .filter(ExamScore.subject == TOTAL_SUBJECT)
                .group_by(ExamScore.exam_id, ExamScore.subject_selection).all())
        tmp = {}
        for eid, sel in rows:
            sel = (sel or '').strip()
            if sel:
                tmp.setdefault(str(eid), set()).add(sel)
        out = {k: sorted(v) for k, v in tmp.items()}
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning(f'按考试取选科组合失败: {e}')
        out = {}
    cache.set(_COMBOS_BY_EXAM_KEY, out, timeout=_COMBOS_BY_EXAM_TTL)
    return out


@bp.route('/print-summary')
@login_required
@perm_required('grades.view')
def print_summary_page():
    """汇总打印版（2026-10-16 用户要求）：A4 网格，每科目一张「班级 × 指标」表。

    数据口径与分析页完全同源（同一份 ExamData / 同一套分层线），只是排版成
    可直接打印/存 PDF 的网格；不需要"重算"，也没有额外写库。
    """
    raw = (request.args.get('exam') or request.args.get('exam_id') or '').strip()
    exam = _get_exam(int(raw)) if raw.isdigit() else None
    if exam is None:
        flash('请从成绩分析页进入（需带 ?exam=<考试 id>）', 'warning')
        return redirect(url_for('grades.index'))
    from app.modules.grades.services import stats_service as _stats
    from datetime import datetime
    # 2026-10-16 第七轮审计修复（P-M3）：该页原为**零缓存**逐请求重算（≈90-100 万次
    # 内层遍历 + 逐班 User 单查）。结果只由 (exam_id, prev_id) 决定 → 挂 900s 缓存；
    # 键名 grades_report_ps_*，被 delete_cache_exam_reports 的「键段匹配考试」覆盖
    # （本场或上一场改分都会失效），与汇报区共用同一套失效契约。
    _prev = _stats.cached_exam_data(exam.id).prev_exam
    _ps_key = f'grades_report_ps_{exam.id}_{_prev.id if _prev is not None else 0}'
    from app.modules.grades.routes.report import _cached
    ps = _cached(_ps_key, lambda: _stats.print_summary(exam.id))
    return render_template('grades/print_summary.html', ps=ps, exam_id=exam.id,
                           now=datetime.now().strftime('%Y-%m-%d %H:%M'))


@bp.route('/api/options')
@login_required
@perm_required('grades.view')
def api_options():
    """顶部筛选器选项（按用户范围过滤）"""
    scope_type, grade = scope_service.get_scope(current_user)
    if current_user.role == 'admin':
        scope_type = 'school'
    grades = scope_service.visible_grades(current_user)
    # 用户可见考试（未毕业年级 + 范围过滤）
    exams = Exam.query.order_by(Exam.exam_date.desc(), Exam.id.desc()).all()
    exams = [e for e in exams if e.grade in grades]
    # v1.13.1 汇报区：标注哪些考试已有总分划线（下拉里提示「未划线」，避免选到无法汇报的考试）
    from app.models.grades import ExamBand, TOTAL_SUBJECT
    banded_ids = {r[0] for r in ExamBand.query.with_entities(ExamBand.exam_id)
                  .filter(ExamBand.subject == TOTAL_SUBJECT).distinct().all()}
    by_grade = {}
    for e in exams:
        by_grade.setdefault(e.grade, []).append({
            'id': e.id, 'name': e.name, 'date': e.exam_date.strftime('%Y-%m-%d'),
            'type': e.exam_type or '', 'status': e.status, 'banded': e.id in banded_ids,
        })
    # v1.19.50.55 每场考试自己的班级：**班级筛选仅限本场考试**
    # 来源 = 该场考试的学生名册（exam_scores.class_name）∪ 任课教师表（exam_teacher_links）
    # 不能用主库当前班级：考试是自包含的独立单元，其参与者是整个年级、且班级为考试当时
    #
    # 2026-10-14 第四轮审计修复（PERF-112 / CONS-05）：此前 classes_by_exam 与
    # classes_by_grade 对**所有角色**原样下发（教师/班主任也能拿到全部考试的全部班级）。
    # 现在统一按数据范围收敛：校级不限；年级范围按本年级；班级范围（班主任/任课）
    # 只给所辖/任教班级；无范围（none）不给任何班级（fail-closed）。
    _is_school = (current_user.role == 'admin' or scope_type == 'school')
    _restrict_pairs = None
    if not _is_school and scope_type != 'grade':
        _restrict_pairs = set(scope_service.visible_classes(current_user) or [])
        _restrict_pairs |= {(l.grade, l.class_name)
                            for l in scope_service.teacher_links(current_user)
                            if l.class_name}
    # 班级（年级下的数字教学班 + 锁定范围）——仅作“未选考试时”的兜底
    classes_by_grade = {}
    for g in grades:
        st_rows = Student.query.filter_by(grade=g).with_entities(Student.class_name).distinct().all()
        cs = numeric_classes([r[0] for r in st_rows])
        if _restrict_pairs is not None:
            cs = [c for c in cs if (g, c) in _restrict_pairs]
        classes_by_grade[g] = cs
    # 每场考试的班级（同样按范围收敛；缓存本体仍是全量，仅下发时过滤）
    classes_by_exam = {}
    _all_by_exam = _classes_by_exam()
    _exam_grade = {str(e.id): e.grade for e in exams}
    for _eid, _cs in _all_by_exam.items():
        _g = _exam_grade.get(_eid)
        if _g is None:
            continue                       # 不可见考试直接不发
        if _is_school or (_g == grade and scope_type == 'grade'):
            classes_by_exam[_eid] = _cs
        elif _restrict_pairs:
            _keep = [c for c in _cs if (_g, c) in _restrict_pairs]
            if _keep:
                classes_by_exam[_eid] = _keep
    # tab 可见性（恒返回 5 键，前端据此显隐）
    tabs = {'grade': False, 'class': False, 'subject': False, 'teacher': False,
            'compare': False}
    if current_user.role == 'admin' or scope_type == 'school':
        tabs = {'grade': True, 'class': True, 'subject': True, 'teacher': True,
                'compare': True}
    elif scope_type == 'grade':
        tabs = {'grade': True, 'class': True, 'subject': True, 'teacher': True,
                'compare': True}
    else:
        # 2026-10-15 用户口径（与 _guard_tab 保持同一份判定，别让"前端显示 / 后端放行"漂移）：
        # 班主任 = 年级分析 + 本班分析；任课教师 = 年级分析 + 学科分析 + 本人。
        if current_user.has_role('homeroom_teacher'):
            tabs.update({'grade': True, 'class': True})
        if current_user.has_role('teacher'):
            tabs.update({'grade': True, 'subject': True, 'teacher': True})
    # 班主任/教师锁定范围
    locked = {'grade': '', 'classes': []}
    if scope_type == 'grade':
        locked['grade'] = grade or ''
    if current_user.has_role('homeroom_teacher'):
        links = scope_service.visible_classes(current_user) or []
        locked['classes'] = [{'grade': g, 'class_name': c} for g, c in links]
        locked['grade'] = links[0][0] if links else ''
    teacher_combos = []
    if current_user.has_role('teacher'):
        for l in scope_service.teacher_links(current_user):
            teacher_combos.append({'grade': l.grade, 'class_name': l.class_name,
                                   'subject': l.subject})
        if teacher_combos:
            locked['grade'] = teacher_combos[0]['grade']
    subjects = []
    cat = None
    from app.models import DictCategory
    cat = DictCategory.query.filter_by(code='exam_subject').first()
    if cat:
        subjects = [i.value for i in cat.items.filter_by(is_active=True)
                    .order_by('sort_order').all()]
    # 2026-10-16 组合维度：每场考试出现过的选科组合（物化生/物化地/物化政/史地政…）
    # 供分析页「组合」下拉使用；没有选科快照的考试为空数组（下拉自动隐藏）。
    # 第七轮审计（P-M4）：改为一次 GROUP BY + 进程缓存（原为逐场 DISTINCT 查询）。
    _all_combos = _combos_by_exam()
    combos_by_exam = {k: v for k, v in _all_combos.items() if k in classes_by_exam}
    return jsonify(success=True, data={
        'grades': grades,
        'exams': by_grade,
        'classes': classes_by_grade,
        'classes_by_exam': classes_by_exam,
        'combos_by_exam': combos_by_exam,
        'subjects': subjects,
        'tabs': tabs,
        'locked': locked,
        'teacher_combos': teacher_combos,
        'scope': scope_type,
        'role': current_user.role,
    })


# ==================== 分析 API ====================

TAB_BY_ROLE = {
    'admin': {'grade', 'class', 'subject', 'teacher', 'compare'},
    'grade_leader': {'grade', 'class', 'subject', 'teacher', 'compare'},
    'homeroom_teacher': {'class'},
    'teacher': {'teacher'},
}


def _guard_tab(tab):
    """按角色/范围限制可访问的分析 tab（9.2 可见矩阵的后端强制）

    说明：除以 role 判定外，必须同时认可「全校范围」权限组（校级领导），
    否则 school_viewer 等角色会被 TAB_BY_ROLE 漏掉而全部 403。
    compare（班级对比）与年级分析同口径：班主任/任课教师仅限本班范围，不开放跨班对比。
    """
    if current_user.role == 'admin':
        allowed = {'grade', 'class', 'subject', 'teacher', 'compare'}
    else:
        scope_type, _grade = scope_service.get_scope(current_user)
        if scope_type == 'school':                 # 校级领导：与管理员同范围
            allowed = {'grade', 'class', 'subject', 'teacher', 'compare'}
        elif scope_service.has_user_scope(current_user):
            # v1.9.2 用户级数据范围（如教务员按年级）：数据已限授权年级，tab 全开
            allowed = {'grade', 'class', 'subject', 'teacher', 'compare'}
        elif current_user.role == 'grade_leader' or scope_type == 'grade':
            allowed = {'grade', 'class', 'subject', 'teacher', 'compare'}
        else:
            # 2026-10-15 用户口径：班主任 / 任课教师不再只给"本班 / 本人"这一档 ——
            #   · 班主任：本班分析 + **所在年级的成绩分析**（看本班在年级中的位置）；
            #   · 任课教师：本人（教师维度）+ 学科分析 + **所在年级的成绩分析**。
            # 多身份取并集（一个人可能同时是班主任和任课教师）。
            # 注意：放开的是"页签可见性"，数据明细仍由 scope 收敛，不放开跨班逐生明细。
            allowed = set()
            if current_user.has_role('homeroom_teacher'):
                allowed |= {'grade', 'class'}
            if current_user.has_role('teacher'):
                allowed |= {'grade', 'subject', 'teacher'}
    if tab not in allowed:
        abort(403)


def _get_exam(exam_id):
    exam = Exam.query.get(exam_id)
    if not exam:
        abort(404)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    return exam


# 分析数据只在「导入 / 重算 / 删除考试 / 保存划线」时变化，而这些入口都会执行
# delete_cache_prefix(f'grades_tab_{exam_id}_')，因此缓存可以放长。
# 原先 60 秒就过期，来回切换 tab 几次又要整场重算，是"切换很慢"的直接原因。
CACHE_TIMEOUT = 900


def _denom():
    """分母口径参数（2026-10-17）：ref=参考数（成绩>0）/ enrolled=班级实际人数。

    非法值静默回落 ref —— 与前端默认一致，避免旧前端/手工 URL 把统计打空。
    """
    d = (request.args.get('denom') or '').strip()
    return d if d in ('ref', 'enrolled') else 'ref'


def _fetch(key, builder):
    data = cache.get(key)
    if data is None:
        data = builder()
        cache.set(key, data, timeout=CACHE_TIMEOUT)
    return data


@bp.route('/api/analysis/grade')
@login_required
@perm_required('grades.view')
def api_grade_tab():
    _guard_tab('grade')
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    selection = (request.args.get('selection') or '').strip()
    exam = _get_exam(exam_id)
    denom = _denom()
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_grade_{direction}_{selection}_{denom}',
        lambda: tab_service.grade_tab(exam_id, direction, selection, denom)))


@bp.route('/api/analysis/class')
@login_required
@perm_required('grades.view')
def api_class_tab():
    _guard_tab('class')
    exam_id = request.args.get('exam_id', type=int)
    class_name = request.args.get('class_name', '').strip()
    direction = (request.args.get('direction') or '').strip()
    selection = (request.args.get('selection') or '').strip()
    exam = _get_exam(exam_id)
    # 2026-10-15 审计修复（P0）：班级 tab 的表格含**逐生全科 + 总分/班排/进退步**，
    # 闸门必须是「该班总分口径可见」（class_total_visible）—— 此前只校验
    # homeroom_teacher，用户级数据范围账号（按班授权）与「班主任+任课」看**他班**
    # 都能整班读逐生总分，与 score_guard/成绩单口径相矛盾。
    from app.modules.grades.services import score_guard as _sg
    _sc = _sg.resolve(current_user)
    if not _sc.class_total_visible(exam.grade, class_name):
        abort(403)
    denom = _denom()
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_class_{class_name}_{direction}_{selection}_{denom}',
        lambda: tab_service.class_tab(exam_id, class_name, direction, selection, denom)))


@bp.route('/api/analysis/subject')
@login_required
@perm_required('grades.view')
def api_subject_tab():
    _guard_tab('subject')
    exam_id = request.args.get('exam_id', type=int)
    subject = request.args.get('subject', '').strip()
    direction = (request.args.get('direction') or '').strip()
    selection = (request.args.get('selection') or '').strip()
    exam = _get_exam(exam_id)
    denom = _denom()
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_subject_{subject}_{direction}_{selection}_{denom}',
        lambda: tab_service.subject_tab(exam_id, subject, direction, selection, denom)))


@bp.route('/api/analysis/compare')
@login_required
@perm_required('grades.view')
def api_compare_tab():
    """班级对比：多班横向对比（班级名单用逗号分隔，顺序=对比展示顺序）"""
    _guard_tab('compare')
    exam_id = request.args.get('exam_id', type=int)
    direction = (request.args.get('direction') or '').strip()
    raw = (request.args.get('classes') or '').strip()
    exam = _get_exam(exam_id)
    classes = [c.strip() for c in raw.split(',') if c.strip()]

    # 班级可见性过滤：受限用户（visible_classes 返回列表）对未授权班级静默剔除；
    # None = 全校/年级范围不限班。2026-10-11 审计修复：原 `if limited:` 在返回空列表时
    # 为假 → 不过滤（fail-open）→ 失败方向应保持闭锁，改用 is not None。
    limited = scope_service.visible_classes(current_user)
    if limited is not None:
        allowed = {c for g, c in limited if g == exam.grade}
        classes = [c for c in classes if c in allowed]
    if len(classes) < 2:
        return jsonify(success=False, message='请至少选择 2 个班级进行对比'), 400
    # 上限保护：最多 20 个班，避免缓存键与结果集失控
    classes = classes[:20]
    # 班级名单可能很长 → 用摘要做缓存键（前缀仍为 grades_tab_{id}_，命中现有失效钩子）
    digest = hashlib.md5(','.join(classes).encode('utf-8')).hexdigest()[:10]
    denom = _denom()
    payload = _fetch(f'grades_tab_{exam_id}_compare_{digest}_{direction}_{denom}',
                     lambda: compare_service.compare_tab(exam_id, classes, direction,
                                                         denom))
    # 传入的班级在本场可能全部无效（如已停考班级）→ 与前端「至少 2 个班」校验同一口径
    if len(((payload.get('meta') or {}).get('selected')) or []) < 2:
        return jsonify(success=False, message='有效班级不足 2 个，请重新选择'), 400
    return jsonify(success=True, data=payload)


@bp.route('/api/analysis/teacher')
@login_required
@perm_required('grades.view')
def api_teacher_tab():
    _guard_tab('teacher')
    exam_id = request.args.get('exam_id', type=int)
    subject = request.args.get('subject', '').strip() or None
    exam = _get_exam(exam_id)
    denom = _denom()
    scope_type, _ = scope_service.get_scope(current_user)
    # 修复：数据构建移入 _fetch 的构建函数——原实现在缓存命中时也会先全量计算一遍，
    # 缓存既不省时、未命中时相当于计算两遍
    def _build():
        if current_user.has_role('teacher'):
            # 任课教师：仅本人映射
            links = scope_service.teacher_links(current_user)
            if not links:
                return tab_service._empty(exam)
            return tab_service.teacher_tab(exam_id, subject=subject, links=links,
                                           grade=exam.grade, denom=denom)
        if (current_user.role == 'admin' or scope_type == 'school'
                or scope_service.has_user_scope(current_user)):
            # 管理员 / 校级领导 / 用户级数据范围：本年级全体任课教师
            return tab_service.teacher_tab(exam_id, subject=subject,
                                           links=None, grade=exam.grade, denom=denom)
        if scope_type == 'grade':
            # 年级长：本场考试年级（管理年级可多选，考试已校验在可见范围内）
            links = TeacherSubjectLink.query.filter_by(
                grade=exam.grade, active=True).all()
            return tab_service.teacher_tab(exam_id, subject=subject, links=links,
                                           grade=exam.grade, denom=denom)
        abort(403)
    cache_key = f'grades_tab_{exam_id}_teacher_{subject or "*"}_{current_user.id}_{denom}'
    return jsonify(success=True, data=_fetch(cache_key, _build))

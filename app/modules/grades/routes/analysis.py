# StuLink v1.18.9.2 2026-10-10
# 成绩分析：主页（四 tab）+ options/analysis API + AI 预留
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import hashlib

from flask import render_template, request, redirect, url_for, jsonify, abort
from flask_login import login_required, current_user
from app.models.grades import Exam, TeacherSubjectLink
from app.models import Student
from app.modules.grades import bp
from app.modules.grades.services import tab_service, scope as scope_service
from app.modules.grades.services import compare_service
from app.modules.grades.utils import numeric_classes
from app.utils.decorators import perm_required
from app.utils.cache import cache


@bp.route('/')
@login_required
@perm_required('grades.view')
def index_legacy():
    """v1.19.0 成绩模块落地页改为「历次考试列表」（用户口径：进入模块先看考试列表）

    原 /grades/ 是成绩分析页；现改为重定向，旧书签/旧链接仍可用。
    """
    return redirect(url_for('grades.exams_list'))


@bp.route('/exams/<int:exam_id>/near-line')
@login_required
@perm_required('grades.view')
def exam_near_line(exam_id):
    """临界生对比（v1.19.0）

    口径：总分线**线上 above 分内**（危险：可能掉下来）/ **线下 below 分内**（希望：可能冲上去）
    默认 10 / 20 分，可在页面上调；复用 report_warning_service.near_line_report 的计算。
    本页在“考试上下文”内（侧栏会切成该场考试的悬浮流程栏）。
    """
    from app.modules.grades.services.exam_guard import assert_exam_visible
    from app.modules.grades.services import (report_warning_service as rws,
                                             report_service as rs)
    from app.models.grades import ExamBand, TOTAL_SUBJECT
    exam = assert_exam_visible(exam_id)

    cfg_max = rs.NEAR_LINE_MAX
    direction = (request.args.get('direction') or '').strip()
    layer = (request.args.get('layer') or '').strip()
    above = request.args.get('above', default=rs.NEAR_LINE_ABOVE, type=int)
    below = request.args.get('below', default=rs.NEAR_LINE_BELOW, type=int)
    above = max(0, min(above or 0, cfg_max))
    below = max(0, min(below or 0, cfg_max))

    # 可选层级（本场总分划线）——不依赖缓存服务，直接查分数线表
    lq = ExamBand.query.filter_by(exam_id=exam_id, subject=TOTAL_SUBJECT)
    if direction:
        lq = lq.filter_by(direction=direction)
    layers = []
    for row in lq.order_by(ExamBand.seq).all():
        if row.name and row.name not in layers:
            layers.append(row.name)

    data = rws.near_line_report(exam_id, direction, layer, above, below)
    err = data.get('error') if isinstance(data, dict) else '计算失败'
    by_class = []
    if not err:
        agg = {}
        for r in data['rows']:
            b = agg.setdefault(r['class_name'], {'class_name': r['class_name'],
                                                 'headteacher': r.get('headteacher') or '',
                                                 'above': 0, 'below': 0})
            b['above' if r['status'] == '线上' else 'below'] += 1
            if not b['headteacher'] and r.get('headteacher'):
                b['headteacher'] = r['headteacher']
        by_class = sorted(agg.values(),
                          key=lambda x: (-(x['above'] + x['below']), x['class_name']))
    return render_template('grades/near_line.html', exam=exam, data=data, error=err,
                           layers=layers, direction=direction, layer=layer,
                           above=above, below=below, cfg_max=cfg_max,
                           by_class=by_class,
                           title='临界生对比')


@bp.route('/analysis')
@login_required
@perm_required('grades.view')
def index():
    # v1.19.0 考试上下文：?exam=<id> 让本页进入“考试态”（侧栏切换到本场流程，并默认选中该场）
    raw = (request.args.get('exam') or '').strip()
    initial_exam_id = int(raw) if raw.isdigit() else None
    return render_template('grades/index.html', initial_exam_id=initial_exam_id)


@bp.route('/history')
@login_required
@perm_required('grades.view')
def history():
    """毕业生（往届学生）成绩查询（二期，预留占位）"""
    return render_template('grades/history.html')


@bp.route('/alumni')
@login_required
@perm_required('grades.view')
def alumni():
    """往届成绩查询（二期，预留占位）"""
    return render_template('grades/alumni.html')


# ==================== 筛选选项 ====================

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
    # 班级（年级下的数字教学班 + 锁定范围）——仅作“未选考试时”的兑底
    classes_by_grade = {}
    for g in grades:
        st_rows = Student.query.filter_by(grade=g).with_entities(Student.class_name).distinct().all()
        classes_by_grade[g] = numeric_classes([r[0] for r in st_rows])
    # v1.18.9.2 每场考试自己的班级：**班级筛选仅限本场考试**
    # 来源 = 该场考试的学生名册（exam_scores.class_name）∪ 任课教师表（exam_teacher_links）
    # 不能用主库当前班级：考试是自包含的独立单元，其参与者是整个年级、且班级为考试当时
    classes_by_exam = {}
    try:
        from app.models.grades import ExamScore, ExamTeacherLink
        from app.extensions import db
        tmp = {}
        for eid, cn in (db.session.query(ExamScore.exam_id, ExamScore.class_name)
                        .distinct().all()):
            tmp.setdefault(eid, set()).add(cn)
        try:
            for eid, cn in (db.session.query(ExamTeacherLink.exam_id,
                                              ExamTeacherLink.class_name)
                            .distinct().all()):
                tmp.setdefault(eid, set()).add(cn)
        except Exception:
            pass        # 旧库无任课快照表时只靠学生名册
        classes_by_exam = {str(k): numeric_classes(list(v)) for k, v in tmp.items()}
    except Exception as e:
        current_app.logger.warning(f'按考试取班级失败: {e}')
        classes_by_exam = {}
    # tab 可见性（恒返回 5 键，前端据此显隐）
    tabs = {'grade': False, 'class': False, 'subject': False, 'teacher': False,
            'compare': False}
    if current_user.role == 'admin' or scope_type == 'school':
        tabs = {'grade': True, 'class': True, 'subject': True, 'teacher': True,
                'compare': True}
    elif scope_type == 'grade':
        tabs = {'grade': True, 'class': True, 'subject': True, 'teacher': True,
                'compare': True}
    elif current_user.has_role('homeroom_teacher'):
        tabs = {'grade': False, 'class': True, 'subject': False, 'teacher': False,
                'compare': False}
    elif current_user.has_role('teacher'):
        tabs = {'grade': False, 'class': False, 'subject': False, 'teacher': True,
                'compare': False}
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
    return jsonify(success=True, data={
        'grades': grades,
        'exams': by_grade,
        'classes': classes_by_grade,
        'classes_by_exam': classes_by_exam,
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
        elif current_user.has_role('homeroom_teacher'):
            allowed = {'class'}
        elif current_user.has_role('teacher'):
            allowed = {'teacher'}
        else:
            allowed = set()
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
    exam = _get_exam(exam_id)
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_grade_{direction}',
        lambda: tab_service.grade_tab(exam_id, direction)))


@bp.route('/api/analysis/class')
@login_required
@perm_required('grades.view')
def api_class_tab():
    _guard_tab('class')
    exam_id = request.args.get('exam_id', type=int)
    class_name = request.args.get('class_name', '').strip()
    exam = _get_exam(exam_id)
    scope_type, _ = scope_service.get_scope(current_user)
    if current_user.has_role('homeroom_teacher'):
        allowed = {(g, c) for g, c in (scope_service.visible_classes(current_user) or [])}
        if (exam.grade, class_name) not in allowed:
            abort(403)
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_class_{class_name}',
        lambda: tab_service.class_tab(exam_id, class_name)))


@bp.route('/api/analysis/subject')
@login_required
@perm_required('grades.view')
def api_subject_tab():
    _guard_tab('subject')
    exam_id = request.args.get('exam_id', type=int)
    subject = request.args.get('subject', '').strip()
    direction = (request.args.get('direction') or '').strip()
    exam = _get_exam(exam_id)
    return jsonify(success=True, data=_fetch(
        f'grades_tab_{exam_id}_subject_{subject}_{direction}',
        lambda: tab_service.subject_tab(exam_id, subject, direction)))


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

    # 班级可见性过滤：班主任/受限用户对未授权班级静默剔除，其余用户直接放行
    limited = scope_service.visible_classes(current_user)
    if limited:
        allowed = {c for g, c in limited if g == exam.grade}
        classes = [c for c in classes if c in allowed]
    if len(classes) < 2:
        return jsonify(success=False, message='请至少选择 2 个班级进行对比'), 400
    # 上限保护：最多 20 个班，避免缓存键与结果集失控
    classes = classes[:20]
    # 班级名单可能很长 → 用摘要做缓存键（前缀仍为 grades_tab_{id}_，命中现有失效钩子）
    digest = hashlib.md5(','.join(classes).encode('utf-8')).hexdigest()[:10]
    payload = _fetch(f'grades_tab_{exam_id}_compare_{digest}_{direction}',
                     lambda: compare_service.compare_tab(exam_id, classes, direction))
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
                                           grade=exam.grade)
        if (current_user.role == 'admin' or scope_type == 'school'
                or scope_service.has_user_scope(current_user)):
            # 管理员 / 校级领导 / 用户级数据范围：本年级全体任课教师
            return tab_service.teacher_tab(exam_id, subject=subject,
                                           links=None, grade=exam.grade)
        if scope_type == 'grade':
            # 年级长：本年级
            grade = scope_service.get_scope(current_user)[1]
            links = TeacherSubjectLink.query.filter_by(grade=grade, active=True).all()
            return tab_service.teacher_tab(exam_id, subject=subject, links=links,
                                           grade=grade)
        abort(403)
    cache_key = f'grades_tab_{exam_id}_teacher_{subject or "*"}_{current_user.id}'
    return jsonify(success=True, data=_fetch(cache_key, _build))

# StuLink v1.19.68.64 2026-10-20
# 限时练：列表 / 新建 / 详情四页签 / 删除
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练页面路由（与普通考试零耦合）。

页面结构（用户口径 2026-10-20）：
- 列表：与「历次考试」并列的第二个 tab；
- 详情只有 3 个页签：**成绩单 / 数据导入 / 成绩总览分析**（2026-10-20 用户口径：
  去掉「划线分层」页签）；
  **没有考务导入页面**，也不进入普通考试的四步流程；
- 分层模板在分析页顶部选「限时练专属名次区间模板」，不提供按科目分数线配置页；
- 导入相关路由见 ``timed_import_routes.py``（单科追加、整科覆盖）。
"""
from datetime import date

from flask import (abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required

from app.extensions import db
from app.models.grades import (DEFAULT_FULL_MARKS, GRADE_SEMESTERS,
                               SPRING_FESTIVAL, SUBJECTS)
from app.models.timed import TimedExam
from app.modules.grades import bp
from app.modules.grades.services import timed_analysis, timed_service
from app.modules.grades.utils import term_of_date
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 2026-10-20 用户口径（第三版）：删除「成绩单」页，学生成绩=汇总表
TABS = ('students', 'trends', 'import', 'analysis')
DEFAULT_TAB = 'students'


def _safe_date(value):
    """表单日期：非法/空 → 今天"""
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date.today()


def _semester_from_form(value):
    """年级学期：只认白名单，其它当「自动」"""
    v = (value or '').strip()
    return v if v in GRADE_SEMESTERS else ''


def _auto_name(grade, exam_date):
    """名称留空时的默认名：年级 + 8 位日期 + 限时练"""
    return f'{grade}{exam_date.strftime("%Y%m%d")}限时练'


def _form_ctx(**kw):
    """新建限时练表单上下文（与普通考试新建页同一套字段口径）"""
    ctx = {'grade_options': timed_service.visible_grades_of(),
           'default_date': date.today().isoformat(),
           'semester_options': GRADE_SEMESTERS,
           'spring_festivals': SPRING_FESTIVAL}
    ctx.update(kw)
    return ctx


# ==================== 列表 / 新建 / 删除 ====================

@bp.route('/timed')
@login_required
@perm_required('grades.view')
def timed_list():
    """限时练列表（侧栏独立入口；默认只看本学期）"""
    grade = (request.args.get('grade') or '').strip()
    if grade:
        timed_service.assert_grade_readable(grade)
    q = timed_service.query_visible()
    if grade:
        q = q.filter(TimedExam.grade == grade)
    exams = q.order_by(TimedExam.exam_date.desc(), TimedExam.id.desc()).all()

    # 学期筛选（2026-10-20 用户口径）：按**自然学期**分档，一个档位里
    # 高一/高二/高三三个年级的场次一起展示；过了春节自动算下学期。
    # 参数：缺省或空串 = 本学期；all = 全部；2025-2026下 = 指定自然学期。
    raw_sem = (request.args.get('sem') or '').strip()
    cur_sem = timed_service.current_natural_semester()
    sem_keys = sorted({e.natural_semester() for e in exams
                       if e.natural_semester()}, reverse=True)
    sem_opts = [(k, timed_service.natural_semester_label(k))
                for k in sem_keys if k != cur_sem]
    if raw_sem in ('', 'cur'):
        sem = ''
        exams = [e for e in exams if e.natural_semester() == cur_sem]
    elif raw_sem == 'all':
        sem = 'all'
    elif raw_sem in sem_keys or raw_sem == cur_sem:
        sem = raw_sem
        exams = [e for e in exams if e.natural_semester() == sem]
    else:
        # 旧链接（曾经按「高二上」这类年级学期筛选）不认识就退回全部，
        # 不能静默给个空列表让人以为没数据
        sem = 'all'

    rows = []
    for e in exams:
        counts = timed_service.student_counts(e.id)
        rows.append({'e': e, 'subjects': sorted(counts),
                     'total': timed_service.student_total(e.id)})
    return render_template('grades/timed/list.html', rows=rows, grade=grade,
                           sem=sem, sem_opts=sem_opts,
                           cur_sem_label=timed_service.natural_semester_label(
                               cur_sem),
                           is_cur_sem=(sem == '' or sem == cur_sem),
                           grade_options=timed_service.visible_grades_of(),
                           can_manage=timed_service.can_manage(),
                           can_create=current_user.has_perm('grades.edit'))


@bp.route('/timed/new', methods=['GET', 'POST'])
@login_required
@perm_required('grades.edit')
def timed_new():
    """新建限时练（类型在此锁定，创建后不可转成普通考试）"""
    if request.method == 'POST':
        grade = (request.form.get('grade') or '').strip()
        exam_date = _safe_date(request.form.get('exam_date'))
        # 名称由系统统一生成（2026-10-21 用户口径）：页面不再提供手填框，
        # 这里的 name 只是临时值，创建后立刻由 renumber_grade 重排成
        # `2025级20260905限时练No.17`
        name = (request.form.get('name') or '').strip() or _auto_name(
            grade, exam_date)
        grade_semester = _semester_from_form(request.form.get('grade_semester'))
        if not grade:
            flash('请选择年级', 'danger')
            return render_template('grades/timed/form.html', **_form_ctx(
                grade=grade, exam_date=exam_date.isoformat(), name=name))
        timed_service.assert_grade_writable(grade)
        dup = TimedExam.query.filter_by(grade=grade, name=name).first()
        if dup:
            flash(f'同年级已存在同名限时练「{name}」', 'danger')
            return render_template('grades/timed/form.html', **_form_ctx(
                grade=grade, exam_date=exam_date.isoformat(), name=''))
        exam = TimedExam(grade=grade, name=name, exam_date=exam_date,
                         term=term_of_date(exam_date),
                         grade_semester=grade_semester,
                         remark=(request.form.get('remark') or '').strip(),
                         operator_id=current_user.id)
        exam.template_id = timed_service.ensure_builtin_templates().id
        db.session.add(exam)
        db.session.flush()
        # 与导入入口同口径（2026-10-21）：新建后立刻按届内日期重排成
        # `2025级20260905限时练No.17`，未导入的场次也不会停在无序号的名字上
        timed_service.renumber_grade(grade)
        db.session.commit()
        log_operation(current_user, '新建', '限时练', exam.id,
                      exam.name, module='grades')
        flash('限时练已创建，先导入一科成绩', 'success')
        return redirect(url_for('grades.timed_detail', tid=exam.id,
                                tab='import'))
    return render_template('grades/timed/form.html', **_form_ctx(
        grade='', exam_date=date.today().isoformat(), name=''))


@bp.route('/timed/<int:tid>/delete', methods=['POST'])
@login_required
@perm_required('grades.view')
def timed_delete(tid):
    """删除整场限时练（仅年级长 / 管理员）"""
    exam = timed_service.assert_visible(tid)
    if not timed_service.can_manage():
        abort(403)
    name = f'{exam.grade} {exam.name}'
    timed_service.delete_scores_of(exam.id)
    db.session.delete(exam)
    db.session.commit()
    timed_analysis.invalidate(exam.id)     # 派生缓存（名次 / T 分 / 趋势）作废
    log_operation(current_user, '删除', '限时练', tid, name, module='grades')
    flash('限时练已删除', 'success')
    return redirect(url_for('grades.timed_list'))


# ==================== 详情（四个页签） ====================

def _detail_ctx(exam, tab, subject, class_name):
    """按页签装配上下文（只查当前页需要的数据）"""
    subjects = timed_service.subjects_of(exam.id)
    subject = subject or (subjects[0] if subjects else '')
    if subject and subject not in SUBJECTS:
        subject = subjects[0] if subjects else ''
    tpl = timed_service.current_template(exam)
    ctx = {'exam': exam, 'tab': tab, 'subjects': subjects,
           'subject': subject, 'class_name': class_name,
           'can_manage': timed_service.can_manage(),
           'subject_options': list(SUBJECTS),
           'report': timed_service.report_of(exam),
           'full_marks': DEFAULT_FULL_MARKS.get(subject, 100)}
    if tab == 'import':
        return ctx
    if not subject:
        ctx['empty_subjects'] = True
        # 2026-10-20 审计修复：空场次必须把各页签要用的键都填上 ——
        # `_trends.html` 第 5 行直接读 `trends.rows`（在学生成绩 / 分析页，
        # 模板先判 empty_subjects 才没炸），否则空场次打开「学生分析」500。
        ctx.setdefault('trends', {
            'rows': [], 'total': 0, 'page': 1, 'pages': 1,
            'class_name': class_name, 'subjects': []})
        ctx.setdefault('blocks', [])
        return ctx
    ctx['classes'] = timed_analysis.classes_of(exam.id, user=current_user)
    if tab == 'students':
        # 汇总表：各科分数 + 总分 + 年级/班级名次 + 总分 T 分
        # 每页条数：?per=20/50/100/200，缺省 50（前端会读「我的设置」里的本机偏好）
        try:
            per = int(request.args.get('per') or 50)
        except (TypeError, ValueError):
            per = 50
        if per not in (20, 50, 100, 200):
            per = 50
        ctx['summary'] = timed_analysis.summary_matrix(
            exam, class_name,
            q=(request.args.get('q') or '').strip(),
            page=request.args.get('page') or 1, per_page=per,
            user=current_user)
    elif tab == 'trends':
        # 学生分析：每人每科 T 分轨迹（分页 + SVG 迷你折线）
        ctx['trends'] = timed_analysis.student_trends(
            exam, class_name, page=request.args.get('page') or 1,
            user=current_user)
    else:
        # 分析页一次给出全部学科（每科左图右表；按可见范围收口）
        ctx['blocks'] = timed_analysis.subject_blocks(exam, tpl, user=current_user)
    return ctx


@bp.route('/timed/<int:tid>')
@login_required
@perm_required('grades.view')
def timed_detail(tid):
    """限时练详情：成绩单 / 数据导入 / 单科分析（模板在分析页顶部选）"""
    exam = timed_service.assert_visible(tid)
    tab = (request.args.get('tab') or DEFAULT_TAB).strip()
    if tab not in TABS:
        tab = DEFAULT_TAB
    # 2026-10-21：导入页是写入口，与导入路由同门槛（grades.import）；
    # 只读账号直接落到学生成绩，不再渲染一个提交必 403 的表单。
    if tab == 'import' and not current_user.has_perm('grades.import'):
        flash('没有成绩导入权限', 'warning')
        return redirect(url_for('grades.timed_detail', tid=exam.id))
    subject = (request.args.get('subject') or '').strip()
    class_name = (request.args.get('class') or '').strip()
    ctx = _detail_ctx(exam, tab, subject, class_name)
    # 2026-10-21：学生成绩 / 学生分析 切班、翻页走局部刷新（只回 #timedPane），
    # 不再整页重载；其余页签照常整页渲染。
    if tab in ('students', 'trends') and request.args.get('partial') == '1':
        return render_template('grades/timed/_pane.html', **ctx)
    return render_template('grades/timed/detail.html', **ctx)


@bp.route('/timed/<int:tid>/student/<student_no>')
@login_required
@perm_required('grades.view')
def timed_student_detail(tid, student_no):
    """学生分析详情：该生**本年级所有限时练场次**的成绩 + T 分走势"""
    exam = timed_service.assert_visible(tid)
    try:
        data = timed_analysis.student_detail(exam, (student_no or '').strip(),
                                             request.args.get('page'),
                                             user=current_user)
    except PermissionError as e:
        # 越权（他班学生）不报 500，退回成绩总览并提示
        flash(str(e), 'warning')
        return redirect(url_for('grades.timed_detail', tid=exam.id, tab='students'))
    return render_template('grades/timed/student_detail.html',
                           exam=exam, **data)

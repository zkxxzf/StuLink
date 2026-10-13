# StuLink v1.19.47.29 2026-10-10
# 成绩管理：考试管理 + 成绩导入向导路由
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import json
import uuid
from datetime import date, datetime

import openpyxl
from sqlalchemy import func
from flask import (Blueprint, render_template, request, jsonify, flash,
                   redirect, url_for, send_file, current_app, abort)
from flask_login import login_required, current_user
from app.extensions import db
from app.models.grades import (Exam, ExamScore, SUBJECTS, TOTAL_SUBJECT,
                              ExamAffair, AffairRoom, AffairStudent, ScoreChangeLog,
                              ExamTeacherLink)
from app.modules.grades import bp
from app.modules.grades.services import import_service, store_service, ranking, tab_service
from app.modules.grades.services.exam_guard import assert_exam_visible, assert_grade_visible
from app.modules.grades.services.import_service import ParseError
from app.modules.grades.utils import term_of_date, invalidate_exam_cache
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.text_guard import safe_download_name

EXAM_STATUS_LABEL = {'draft': '未导入', 'imported': '已导入', 'dirty': '待重算'}


def _grade_options():
    from app.modules.grades.services.scope import visible_grades
    return visible_grades(current_user)


def _dict_values(code, fallback=()):
    """字典可见项（无字典/为空时兑底用 fallback），供两维下拉使用"""
    from app.models import DictCategory
    cat = DictCategory.query.filter_by(code=code).first()
    vals = ([i.value for i in cat.items.filter_by(is_active=True)
             .order_by('sort_order').all()] if cat else [])
    return vals or list(fallback)


def _exam_date_from(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date.today()


# ==================== 考试列表 / 新建 ====================

@bp.route('/exams')
@login_required
@perm_required('grades.edit')
def exams_list():
    # 2026-10-12 安全审计 H-3：原先 ?grade= 直接 filter_by 信任 URL 参数，
    # 2025 级年级长传 ?grade=2024级 即可看到 2024 级全部考试的
    # 名称/日期/导入状态/孤儿批次。现统一「未传 grade 时按可见年级收敛，
    # 显式传 grade 时先 assert_grade_visible」。
    grade = request.args.get('grade', '')
    if grade:
        assert_grade_visible(grade)
        q = Exam.query.filter_by(grade=grade)
    else:
        vg = _grade_options()
        q = Exam.query.filter(Exam.grade.in_(vg)) if vg else Exam.query.filter(False)
    exams = q.order_by(Exam.exam_date.desc(), Exam.id.desc()).all()
    counts = {}
    # 修复：人数统计改 GROUP BY 聚合（原先把全部总分行载入内存逐条计数）
    if exams:
        rows_cnt = (db.session.query(ExamScore.exam_id, func.count(ExamScore.id))
                    .filter(ExamScore.exam_id.in_([e.id for e in exams]),
                            ExamScore.subject == TOTAL_SUBJECT)
                    .group_by(ExamScore.exam_id).all())
        for eid, cnt in rows_cnt:
            counts[eid] = cnt
    # v1.19.47.29：孤儿考务批次兜底（exam_id 为空，或指向已删除的考试）
    # 这类批次从考试列表无法进入，以前只能直连 /grades/affairs 才能看到，现在底部统一列出
    # 2026-10-12 安全审计 H-3：孤儿批次同样按可见年级收敛（未传 grade 时），
    # 否则跨年级用户仍能从此区读到不该看的年级的批次元数据。
    live_exam_ids = {e.id for e in Exam.query.with_entities(Exam.id).all()}
    _vis_grade = grade if grade else set(_grade_options())
    orphan_affairs = []
    for a in ExamAffair.query.order_by(ExamAffair.id.desc()).all():
        if a.exam_id is None or a.exam_id not in live_exam_ids:
            if _vis_grade and a.grade not in _vis_grade:
                continue
            orphan_affairs.append({
                'a': a,
                'students': AffairStudent.query.filter_by(affair_id=a.id).count(),
                'rooms': AffairRoom.query.filter_by(affair_id=a.id).count(),
                'exam_gone': a.exam_id is not None,
            })
    steps_map = _list_steps_map(exams)
    return render_template('grades/exam_list.html', exams=exams, counts=counts,
                           grade=grade, grade_options=_grade_options(),
                           orphan_affairs=orphan_affairs,
                           steps_map=steps_map,
                           status_label=EXAM_STATUS_LABEL)


def _list_steps_map(exams):
    """v1.19.0 列表页四步进度（建考试 / 考务 / 导入 / 划线）

    批量两条聚合查询算出，避免逐行 N+1；口径与考试详情页 _four_step_status 一致。
    返回 {exam_id: (s1, s2, s3, s4)}（s1 恒为 True）
    """
    ids = [e.id for e in exams]
    affair_ids, band_ids = set(), set()
    if ids:
        try:
            affair_ids = {r[0] for r in db.session.query(ExamAffair.exam_id)
                          .filter(ExamAffair.exam_id.in_(ids)).distinct().all()}
            band_ids = {r[0] for r in db.session.query(ExamBand.exam_id)
                        .filter(ExamBand.exam_id.in_(ids)).distinct().all()}
        except Exception:                  # 进度只是展示信息，失败不影响列表
            affair_ids, band_ids = set(), set()
    return {e.id: (True, e.id in affair_ids, e.status == 'imported',
                   e.id in band_ids) for e in exams}


def _exam_form_ctx(**kw):
    """新建考试表单的公共上下文（年级 / 性质 / 阶段）"""
    from app.models.grades import EXAM_KINDS, EXAM_STAGES
    ctx = {
        'grade_options': _grade_options(),
        'kind_options': _dict_values('exam_kind', EXAM_KINDS),
        'stage_options': _dict_values('exam_type', EXAM_STAGES),
        'default_date': date.today().isoformat(),
    }
    ctx.update(kw)
    return ctx


@bp.route('/exams/new', methods=['GET', 'POST'])
@login_required
@perm_required('grades.edit')
def exam_new():
    if request.method == 'POST':
        grade = (request.form.get('grade') or '').strip()
        exam_date = _exam_date_from(request.form.get('exam_date'))
        name = (request.form.get('name') or '').strip()
        exam_kind = (request.form.get('exam_kind') or '').strip()
        exam_type = (request.form.get('exam_type') or '').strip()
        # v1.19.47.29：不再有「分数口径」表单字段（口径在导入时按文件确定），
        # 新考试 score_mode 一律留空（未指定），展示为「按导入确定」
        if not name:
            # 名称留空 → 默认「年级 + 日期」（两维度已单独存列，不塞进名称）
            name = f'{grade}{exam_date.isoformat()}'
        if not grade or not name:
            flash('请选择年级并填写考试名称', 'danger')
            return render_template('grades/exam_form.html', **_exam_form_ctx(
                grade=grade, exam_date=exam_date.isoformat(), name=name,
                exam_kind=exam_kind, exam_type=exam_type))
        # 2026-10-12 P1：目标年级可见性校验（防止为未授权年级创建考试后导入成绩，
        # 构成跨年级写入；与 H-4 的 assert_grade_visible 同一入口）
        assert_grade_visible(grade)
        dup = Exam.query.filter_by(grade=grade, name=name).first()
        if dup:
            # 修复：同名考试由“仅警告仍创建”改为阻断——避免成绩分散到两场同名考试混淆统计
            flash(f'同年级已存在同名考试「{name}」（{dup.exam_date}），请修改考试名称',
                  'danger')
            return render_template('grades/exam_form.html', **_exam_form_ctx(
                grade=grade, exam_date=exam_date.isoformat(), name='',
                exam_kind=exam_kind, exam_type=exam_type))
        exam = Exam(grade=grade, name=name, exam_date=exam_date,
                    exam_kind=exam_kind, exam_type=exam_type or '其他',
                    term=term_of_date(exam_date),
                    operator_id=current_user.id)
        db.session.add(exam)
        db.session.commit()
        log_operation(current_user, '新建', '考试', exam.id,
                      f'{exam.title_with_dimensions()}（{name}）', module='grades')
        flash('考试已创建（%s）' % exam.title_with_dimensions(), 'success')
        return redirect(url_for('grades.exam_detail', exam_id=exam.id))
    # GET：性质/阶段默认取字典第一项
    from app.models.grades import EXAM_KINDS, EXAM_STAGES
    kinds = _dict_values('exam_kind', EXAM_KINDS)
    stages = _dict_values('exam_type', EXAM_STAGES)
    return render_template('grades/exam_form.html', **_exam_form_ctx(
        grade='', exam_date=date.today().isoformat(), name='',
        exam_kind=(kinds[0] if kinds else ''),
        exam_type=('月考' if '月考' in stages else (stages[0] if stages else ''))))


# ==================== 考试详情 / 成绩浏览 / 修正 / 重算 / 删除 ====================

def _exam_classes(exam_id):
    """本场考试出现过的班级（按班级名排序），供成绩单「按班级查看」筛选。

    取**总分行**的 class_name —— 它是「考试当时」的班级快照，与考试自包含设计
    （v1.18.7.0：考试自带学生班级）以及班排/方向排的班级口径一致。
    注意：科目行的 class_name 是**导入时的当前班级**（选科分班之后），对高一上等
    「分班之前」的考试与总分行不一致（生产实测考试 1-5、18-20 约 90% 学生如此，
    经调班记录 reassign 佐证），故本模块一律不用科目行判定班级。
    """
    rows = (db.session.query(ExamScore.class_name)
            .filter(ExamScore.exam_id == exam_id,
                    ExamScore.subject == TOTAL_SUBJECT,
                    ExamScore.class_name.isnot(None),
                    ExamScore.class_name != '')
            .distinct().all())
    return sorted({r[0] for r in rows})


# v1.19.47.29 成绩单表头可排序列 → 总分行（每生一行）字段名；
# 各科成绩用 's:科目名' 前缀（见 _exam_page_rows），不在此表内。
_SORT_COLS = {
    'no': 'student_no', 'name': 'student_name', 'exam_no': 'exam_no',
    'cls': 'class_name', 'dir': 'direction', 'sel': 'subject_selection',
    'total': 'score', 'rank_dir': 'rank_dir', 'rank_class': 'rank_class',
    'move': 'move_rank',
}


def _sort_args(args):
    """解析 ?sort= / ?order=（白名单校验，非法一律回默认次序）"""
    key = (args.get('sort') or '').strip()
    order = 'asc' if (args.get('order') or '').strip().lower() == 'asc' else 'desc'
    if key.startswith('s:'):
        if not key[2:].strip() or key[2:].strip() == TOTAL_SUBJECT:
            key = ''
    elif key not in _SORT_COLS:
        key = ''
    return (key, order) if key else ('', 'desc')


def _exam_page_rows(exam_id, page, size, class_name=None, direction=None,
                    sort_key='', order='desc'):
    """服务端分页：仅查询当前页的学生成绩，避免整场 8000 人一次性载入。

    返回 (students, page, total_pages, total)：
      - 先用「总分行」做分页元数据（每生 1 行，远小于全部科目行），
        按「方向 → 总分名次」排序并 LIMIT/OFFSET 取出本页学号；
      - 再仅查本页学号的全部科目成绩行（约 50×7=350 行）；
      - 学籍状态一次性 IN 查询（1 次，而非逐批 10 次）。

    class_name（v1.19.0）：按班级查看成绩单，只统计/展示该班学生（None=全部）。
    direction（v1.19.47.29）：按方向查看（物理/历史），取**总分行**上的方向快照（None=全部）。
    sort_key / order（v1.19.47.29）：表头排序。sort_key 取 _SORT_COLS 的键，
        或 's:科目名' 表示按该科成绩排；order 为 'asc'/'desc'（空 sort_key = 默认次序）。
        排序必须在**服务端**做：页面只加载当前页，前端排序只会排到本页 50 人。
        排序状态只存在于 URL 参数里（v1.19.47.29 确认），服务端不保存"当前排序"，
        因此**不会影响其他用户**；不传参数时回到「方向 → 总分名次」默认榜。
    """
    q = ExamScore.query.filter_by(exam_id=exam_id, subject=TOTAL_SUBJECT)
    if class_name:
        q = q.filter(ExamScore.class_name == class_name)
    if direction:
        q = q.filter(ExamScore.direction == direction)
    total = q.count()
    if total == 0:
        return [], 1, 0, 0
    size = max(1, min(int(size or 50), 500))   # 单页上限 500，避免「全部」拖垮
    page = max(1, int(page or 1))
    total_pages = (total + size - 1) // size
    if page > total_pages:
        page = total_pages

    # ---- 排序键解析（只认白名单/本场科目，避免拼接注入） ----
    order_col = None
    if sort_key.startswith('s:'):
        subject = sort_key[2:].strip()
        if subject and subject != TOTAL_SUBJECT:
            sub = (db.session.query(ExamScore.student_no.label('sno'),
                                    ExamScore.score.label('s_score'))
                   .filter(ExamScore.exam_id == exam_id, ExamScore.subject == subject)
                   .subquery())
            q = q.outerjoin(sub, sub.c.sno == ExamScore.student_no)
            order_col = sub.c.s_score
    elif sort_key in _SORT_COLS:
        order_col = getattr(ExamScore, _SORT_COLS[sort_key])

    if order_col is not None:
        # 空值（缺考/未考）恒排最后：SQLite 里 NULL 最小，DESC 天然最后，ASC 用 case 兜底；
        # 末尾再按学号稳定次序，保证翻页不跳行/不重复
        seqq = [db.case((order_col.is_(None), 1), else_=0),
                order_col.asc() if order == 'asc' else order_col.desc(),
                ExamScore.student_no]
        meta = q.order_by(*seqq).limit(size).offset((page - 1) * size).all()
    else:
        # 默认次序（v1.19.47.29）：按**总分名次**排列 —— 先分方向（历史在前、物理在后），
        # 方向内按方向排（= 该方向的总分名次）升序，缺考（无方向排）排在本方向最后，
        # 末尾按学号定序保证翻页稳定。注意：不按班级分组（老师要看的是名次榜）。
        dir_seq = db.case((ExamScore.direction == '历史', 0),
                          (ExamScore.direction == '物理', 1), else_=2)
        meta = (q.order_by(dir_seq, db.func.coalesce(ExamScore.rank_dir, 99999),
                           ExamScore.student_no)
                .limit(size).offset((page - 1) * size).all())
    nos = [r.student_no for r in meta]
    if not nos:
        return [], page, total_pages, total
    rows = (ExamScore.query.filter_by(exam_id=exam_id)
            .filter(ExamScore.student_no.in_(nos))
            .order_by(ExamScore.subject == TOTAL_SUBJECT, ExamScore.subject,
                      ExamScore.rank_dir).all())
    data = {}
    for r in rows:
        d = data.setdefault(r.student_no, {
            'no': r.student_no, 'name': r.student_name, 'class_name': r.class_name,
            'direction': r.direction, 'selection': r.subject_selection,
            'exam_no': r.exam_no,                    # v1.19.0 考号
            'total': None, 'total_sid': None, 'rank': None, 'move': None, 'subjects': {},
        })
        if r.subject == TOTAL_SUBJECT:
            d['total'] = r.score
            d['total_raw'] = r.raw_score            # v1.19.0 总分原始分（可选）
            d['total_sid'] = r.id
            d['rank'] = r.rank_dir
            d['rank_class'] = r.rank_class
            d['move'] = r.move_rank
            # v1.19.0 身份字段以「总分行」为准：它是**考试当时**的班级/方向/选科快照，
            # 与 _exam_classes() / _exam_page_rows() 的筛选口径、以及班排/方向排一致。
            # （科目行的 class_name 是导入时的当前班级，高一上等「分班之前」的考试会与
            #   考试快照不一致——生产实测约 90% 学生命中，经调班记录 reassign 佐证）
            d['class_name'] = r.class_name or d['class_name']
            d['direction'] = r.direction or d['direction']
            d['selection'] = r.subject_selection or d['selection']
            if r.exam_no:
                d['exam_no'] = r.exam_no
        else:
            d['subjects'][r.subject] = {
                'id': r.id, 'score': r.score, 'raw': r.raw_score,   # v1.19.0 原始分
                'rank': r.rank_dir,
                'rank_class': r.rank_class,
            }
    for d in data.values():
        d['unselected'] = unselected_subjects(d.get('selection'), d.get('direction'))
    # 严格按分页顺序（nos）输出，保证翻页稳定
    students = [data[n] for n in nos if n in data]
    return students, page, total_pages, total


# 选科简称 → 科目名（判断“未选”还是“缺考”用）
_SUBJ_SHORT = {'物理': '物', '化学': '化', '生物': '生',
               '政治': '政', '历史': '史', '地理': '地'}


def unselected_subjects(selection, direction):
    """返回该生“**未选**”的科目集合（与“缺考”区分）。

    用户口径（2026-10-10）：选科为「物化政」的学生，历史/生物/地理应显示「未选」，
    只有所选科目（含语数外）确实没分数时才显示「缺」。
    高一全科 / 不分科 / 选科为空 → 不存在“未选”概念，全部按缺考处理。
    方向科（物理或历史）总是被选的，按 direction 兼容一下。
    """
    sel = (selection or '').strip()
    if not sel or sel in ('全科', '不分科'):
        return set()
    out = set()
    for subj, ch in _SUBJ_SHORT.items():
        if ch in sel or subj == (direction or '').strip():
            continue
        out.add(subj)
    return out


def _four_step_status(exam, user=None):
    """四段式进度（建立考试 → 考务安排 → 成绩导入 → 成绩分析）

    每一步给出：是否完成、可点进去的地址、“下一步”提示。
    - 建立考试：能打开本页即已完成
    - 考务安排：该考试已生成考务批次（含名单/考场）
    - 成绩导入：考试状态 = imported
    - 成绩分析：已划过分层线（划线是考试自带的 exam_bands，与模板无关）
    """
    from app.models.grades import ExamBand
    affair = ExamAffair.query.filter_by(exam_id=exam.id).first()
    imported = (exam.status == 'imported')
    banded = ExamBand.query.filter_by(exam_id=exam.id).count() > 0
    # v1.19.47.29：第④步落点与**侧栏「成绩分析」标题同口径**（base.html 同一套判断）——
    #   领导（管理员 / 校级·年级权限组）→「总览（全年级对比）」global_compare_page；
    #   其他人 →「班级分析」grades.index?tab=class。
    # 此前④一律跳 grades.index（老的五页签分析页），与侧栏不一致（用户 2026-10-13 反馈
    # 「点击以后跳转的还是老页面」）；URL 必须带本场 exam，否则全局页会选错年级/考试。
    _pg = getattr(user, 'permission_group', None) if user is not None else None
    is_lead = (user is not None and (
        getattr(user, 'role', '') == 'admin'
        or (_pg is not None
            and getattr(_pg, 'scope_type', '') in ('school', 'grade'))))
    if is_lead:
        analysis_url = url_for('grades.global_compare_page', exam=exam.id)
    else:
        analysis_url = url_for('grades.index', exam=exam.id, tab='class')
    steps = [
        {'key': 'exam', 'label': '建立考试', 'done': True,
         'url': url_for('grades.exam_detail', exam_id=exam.id),
         'hint': exam.title_with_dimensions()},
        {'key': 'affair', 'label': '考务安排', 'done': bool(affair),
         'url': url_for('grades.exam_affair_go', exam_id=exam.id),
         'hint': (affair.name if affair else '未创建考务批次')},
        {'key': 'import', 'label': '成绩导入', 'done': imported,
         'url': url_for('grades.exam_import', exam_id=exam.id),
         'hint': (EXAM_STATUS_LABEL.get(exam.status, exam.status) if imported
                  else '尚未导入成绩')},
        {'key': 'analysis', 'label': '成绩分析', 'done': banded,
         'url': analysis_url,
         'hint': ('已划线，分析可用' if banded else '未划线，分层类指标不可用')},
    ]
    nxt = next((s for s in steps if not s['done']), None)
    return steps, nxt


@bp.route('/exams/<int:exam_id>')
@login_required
@perm_required('grades.edit')
def exam_detail(exam_id):
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    page = _safe_int(request.args.get('page'), 1)
    size = _safe_int(request.args.get('size'), 50)
    # v1.19.0：成绩单支持按班级查看（cls=班级名，空/非法=全部）
    # v1.19.47.29：再支持按方向查看（dir=物理/历史）；两者互斥，传 cls 时忽略 dir
    classes = _exam_classes(exam_id)
    cur_class = (request.args.get('cls') or '').strip()
    if cur_class not in classes:
        cur_class = ''
    cur_dir = (request.args.get('dir') or '').strip()
    if cur_dir not in ('物理', '历史'):
        cur_dir = ''
    if cur_class:
        cur_dir = ''
    cur_sort, cur_order = _sort_args(request.args)      # v1.19.47.29 表头排序
    students, page, total_pages, total = _exam_page_rows(
        exam_id, page, size, class_name=cur_class or None, direction=cur_dir or None,
        sort_key=cur_sort, order=cur_order)
    info = None
    if exam.import_info:
        try:
            info = json.loads(exam.import_info)
        except (json.JSONDecodeError, TypeError):
            info = None
    steps, next_step = _four_step_status(exam, user=current_user)
    # 2026-10-12 数据可信度审计（§3.3）：分批导入（模式 A：未出现的科目列保留）
    # 中途，总分＝已导入科目合计，与导完后的总分**不可比**。后端早就有
    # stats_service.ExamData.partial_import() 能算出"仍有 X 科未导入"，
    # 但页面从未消费 → 老师只能靠自己记得"还有两科没导"。
    # 这里在成绩单页顶部给出明确提示。
    partial = None
    if exam.status == 'imported':
        try:
            from app.modules.grades.services.stats_service import ExamData
            partial = ExamData(exam.id).partial_import()
        except Exception as _e:  # noqa: BLE001  提示信息不应影响页面渲染
            current_app.logger.debug('partial_import 计算失败 exam_id=%s: %s', exam_id, _e)
            partial = None
    # 2026-10-13：考试信息编辑弹窗（名称/年级/日期/性质/阶段）。
    # has_scores 决定年级能否改 —— 已导入成绩后年级锁定（成绩行绑的是考试当时的班级与学号）。
    from app.models.grades import EXAM_KINDS, EXAM_STAGES
    has_scores = db.session.query(ExamScore.id).filter(
        ExamScore.exam_id == exam_id).first() is not None
    return render_template('grades/exam_detail.html', exam=exam, students=students,
                           subjects=SUBJECTS, status_label=EXAM_STATUS_LABEL,
                           import_info=info, steps=steps, next_step=next_step,
                           partial_import=partial,
                           grade_options=_grade_options(),
                           kind_options=_dict_values('exam_kind', EXAM_KINDS),
                           stage_options=_dict_values('exam_type', EXAM_STAGES),
                           has_scores=has_scores,
                           full_marks=exam.full_marks(),
                           can_edit_scores=current_user.has_perm('grades.score_edit'),
                           classes=classes, cur_class=cur_class, cur_dir=cur_dir,
                           cur_sort=cur_sort, cur_order=cur_order,
                           page=page, total_pages=total_pages, total=total, size=size)


@bp.route('/exams/<int:exam_id>/scores')
@login_required
@perm_required('grades.edit')
def exam_scores_page(exam_id):
    """成绩单分页片段：仅返回 <tr> 行，供前端 AJAX 翻页时局部替换 tbody。"""
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    page = _safe_int(request.args.get('page'), 1)
    size = _safe_int(request.args.get('size'), 50)
    cur_class = (request.args.get('cls') or '').strip()   # v1.19.0 按班级查看
    cur_dir = (request.args.get('dir') or '').strip()     # v1.19.15.0 按方向查看
    if cur_dir not in ('物理', '历史'):
        cur_dir = ''
    cur_sort, cur_order = _sort_args(request.args)        # v1.19.47.29 表头排序
    students, _page, _total_pages, _total = _exam_page_rows(
        exam_id, page, size, class_name=cur_class or None, direction=cur_dir or None,
        sort_key=cur_sort, order=cur_order)
    return render_template('grades/exam_detail_rows.html',
                           students=students, subjects=SUBJECTS,
                           can_edit_scores=current_user.has_perm('grades.score_edit'))


def _safe_int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


@bp.route('/exams/<int:exam_id>/update', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_update(exam_id):
    """修改考试基本信息：名称 / 年级 / 考试日期 / 性质 / 阶段。

    2026-10-13（用户口径）：考试详情页原先只能改名（浏览器 prompt），
    日期/类型也应可改；但**已导入成绩后年级不可改** —— 成绩行存的是
    考试当时的班级与学号，改年级会让历史成绩挂到别的年级。
    """
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    name = (request.form.get('name') or '').strip()
    if not name:
        return jsonify(success=False, message='考试名称不能为空'), 400
    if len(name) > 100:
        return jsonify(success=False, message='考试名称过长（不超过 100 字）'), 400

    grade = (request.form.get('grade') or '').strip() or exam.grade
    # 日期：解析失败要报错，不能静默回落到今天（编辑场景静默改期最危险）
    raw_date = (request.form.get('exam_date') or '').strip()
    if raw_date:
        try:
            exam_date = date.fromisoformat(raw_date)
        except ValueError:
            return jsonify(success=False, message='考试日期格式不正确'), 400
    else:
        exam_date = exam.exam_date
    # 性质/阶段：表单未给值（字段被禁用等）时保留原值，避免误清空
    exam_kind = (request.form.get('exam_kind') or '').strip() or (exam.exam_kind or '')
    exam_type = (request.form.get('exam_type') or '').strip() or (exam.exam_type or '其他')

    has_scores = db.session.query(ExamScore.id).filter(
        ExamScore.exam_id == exam_id).first() is not None
    if grade != exam.grade:
        if has_scores:
            return jsonify(success=False,
                           message='本场已导入成绩，年级不可修改（会影响历史成绩归属）'), 400
        assert_grade_visible(grade)       # 目标年级也必须在当前用户可见范围内

    dup = Exam.query.filter(Exam.grade == grade, Exam.name == name,
                            Exam.id != exam.id).first()
    if dup:
        return jsonify(success=False,
                       message=f'同年级已存在同名考试「{name}」（{dup.exam_date}），请换一个名称'), 400

    changes = []
    if name != exam.name:
        changes.append(f'名称「{exam.name}」→「{name}」')
    if grade != exam.grade:
        changes.append(f'年级 {exam.grade}→{grade}')
    if exam_date != exam.exam_date:
        changes.append(f'日期 {exam.exam_date}→{exam_date}')
    if exam_kind != (exam.exam_kind or ''):
        changes.append(f'性质「{exam.exam_kind or "—"}」→「{exam_kind or "—"}」')
    if exam_type != (exam.exam_type or ''):
        changes.append(f'阶段「{exam.exam_type or "—"}」→「{exam_type or "—"}」')
    if not changes:
        return jsonify(success=True, message='未做任何修改')

    exam.name = name
    exam.grade = grade
    exam.exam_date = exam_date
    exam.exam_kind = exam_kind
    exam.exam_type = exam_type
    exam.term = term_of_date(exam_date)   # 学年随日期自动重算（不单独编辑）
    db.session.commit()
    invalidate_exam_cache(exam_id)
    log_operation(current_user, '修改', '考试', exam_id, '；'.join(changes), module='grades')
    return jsonify(success=True, message='考试信息已更新：' + '；'.join(changes))


def _recalc_after_edit(exam_id):
    """改分后立即重算整场排名并把状态复位（不再留「待重算」）。返回重算行数。"""
    n = ranking.recalc_exam(exam_id)
    exam = Exam.query.get(exam_id)
    if exam is not None:
        exam.status = 'imported'
    return n


def _verify_edit_password():
    """改分二次确认：核对当前登录账号的密码。返回错误文案；通过返回 None。"""
    pwd = request.form.get('password') or ''
    if not pwd:
        return '请输入当前登录账号的密码以确认本次修改'
    if not current_user.check_password(pwd):
        return '密码不正确，本次修改未生效'
    return None


def _score_edit_reply(exam_id, success, message):
    """改分接口返回：AJAX → JSON（前端好提示）；非 AJAX → flash + 跳回成绩单"""
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(success=success, message=message)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('grades.exam_detail', exam_id=exam_id))


def _log_score_change(exam, row, action, old_score, new_score, old_raw, new_raw):
    """写一条「分数修改记录」（改分/标记缺考都留痕，供全校查询）"""
    db.session.add(ScoreChangeLog(
        exam_id=exam.id, exam_name=exam.name, score_id=row.id,
        student_no=row.student_no, student_name=row.student_name,
        class_name=row.class_name, subject=row.subject, action=action,
        old_score=old_score, new_score=new_score,
        old_raw=old_raw, new_raw=new_raw,
        operator_id=current_user.id,
        operator_name=current_user.real_name or current_user.username))


@bp.route('/exams/<int:exam_id>/score/<int:score_id>', methods=['POST'])
@login_required
@perm_required('grades.edit')
@perm_required('grades.score_edit')
def score_update(exam_id, score_id):
    """单科改分（v1.19.47.29）

    · 赋分（score）与原始分（raw_score）都可改，提交哪个改哪个；
    · 两者都提交为空 = 该科缺考（删除该科）；
    · 需要输入**当前登录账号的密码**二次确认；
    · 每次修改写「分数修改记录」（全校可查）；
    · 改完自动重算整场排名。
    """
    exam = assert_exam_visible(exam_id)          # H-4
    row = ExamScore.query.filter_by(id=score_id, exam_id=exam_id).first_or_404()
    # 总分由各科自动汇总，禁止直接改（避免总分与单科不一致）
    if row.subject == TOTAL_SUBJECT:
        return _score_edit_reply(exam_id, False,
                                 '总分由各科成绩自动汇总，不能直接修改，请修改对应的单科成绩')
    pwd_err = _verify_edit_password()
    if pwd_err:
        return _score_edit_reply(exam_id, False, pwd_err)

    limit = exam.full_marks().get(row.subject, 100)
    changed = {}
    try:
        for field, label in (('score', '赋分'), ('raw_score', '原始分')):
            if field not in request.form:
                continue
            raw = (request.form.get(field) or '').strip()
            if raw == '':
                changed[field] = None
                continue
            val = float(raw)
            if val < 0 or val > limit + 0.5:
                return _score_edit_reply(exam_id, False, f'{label} {val} 超出范围(0~{limit})')
            changed[field] = val
    except ValueError:
        return _score_edit_reply(exam_id, False, '分数格式不正确')

    old_score, old_raw = row.score, row.raw_score

    # 赋分与原始分都清空 → 视为缺考，删除该科
    if changed.get('score', 1) is None and changed.get('raw_score', 1) is None:
        _log_score_change(exam, row, 'delete', old_score, None, old_raw, None)
        no = row.student_no
        db.session.delete(row)
        store_service.refresh_student_total(exam, no)
        n = _recalc_after_edit(exam_id)
        db.session.commit()
        invalidate_exam_cache(exam_id)
        tab_service.clear_exam_cache(exam_id)
        # 2026-10-12 数据可信度审计（§3.8）：标记缺考分支原先只写 ScoreChangeLog、
        # **未写 log_operation**，与下面的 update 分支不对称，业务审计里缺一条。
        log_operation(current_user, '标记缺考', '成绩', exam_id,
                      f'{no} {row.subject}（原 {old_score}）', module='grades')
        return _score_edit_reply(exam_id, True,
                                 f'该科已标记缺考，排名已自动重算（{n} 条），并已记入分数修改记录')

    for field, val in changed.items():
        setattr(row, field, val)
    store_service.refresh_student_total(exam, row.student_no)   # 总分 = 各科之和
    n = _recalc_after_edit(exam_id)
    _log_score_change(exam, row, 'update', old_score, row.score, old_raw, row.raw_score)
    db.session.commit()
    invalidate_exam_cache(exam_id)
    tab_service.clear_exam_cache(exam_id)
    log_operation(current_user, '修改', '成绩', exam_id,
                  f'{row.student_name or row.student_no} {row.subject} 改分（排名已重算）',
                  module='grades')
    return _score_edit_reply(exam_id, True,
                             f'成绩已修改，排名已自动重算（{n} 条），并已记入分数修改记录')


@bp.route('/exams/<int:exam_id>/score/<int:score_id>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
@perm_required('grades.score_edit')
def score_delete(exam_id, score_id):
    exam = assert_exam_visible(exam_id)          # H-4
    row = ExamScore.query.filter_by(id=score_id, exam_id=exam_id).first_or_404()
    # 总分不可单独删除（它随各科自动汇总）
    if row.subject == TOTAL_SUBJECT:
        return _score_edit_reply(exam_id, False,
                                 '总分由各科成绩自动汇总，不能直接操作，请修改对应的单科成绩')
    pwd_err = _verify_edit_password()
    if pwd_err:
        return _score_edit_reply(exam_id, False, pwd_err)
    _log_score_change(exam, row, 'delete', row.score, None, row.raw_score, None)
    no = row.student_no
    db.session.delete(row)
    store_service.refresh_student_total(exam, no)
    n = _recalc_after_edit(exam_id)
    db.session.commit()
    invalidate_exam_cache(exam_id)
    tab_service.clear_exam_cache(exam_id)
    return _score_edit_reply(exam_id, True,
                             f'该科已标记缺考，排名已自动重算（{n} 条），并已记入分数修改记录')


@bp.route('/score-changes')
@login_required
@perm_required('grades.edit')
def score_changes():
    """分数修改记录（v1.19.47.29）：**只看某一场考试**

    该记录属于单场考试的数据，入口只有一处：成绩单页右上角「分数修改记录」按钮
    （带 ?exam=<id>）。不再提供跨考试视图，也不在左侧栏出现；缺 ?exam= 时回考试列表。

    2026-10-12 安全审计 H-1：原先只有 @login_required，既无功能权限也无**数据范围**
    校验，任意已登录用户（含权限极窄账号）可借 ?exam=<id> 读取任意考试、任意年级的
    改分流水（学号/姓名/班级/科目/改分前后分值/操作人）。现补：
      · @perm_required('grades.edit') —— 与改分/重算同档（读这份流水等于能看全部分数变动）；
      · assert_exam_visible —— 越界 403、考试不存在 404（原为静默跳回列表）。
    """
    raw = (request.args.get('exam') or '').strip()
    exam = assert_exam_visible(int(raw)) if raw.isdigit() else None
    if exam is None:
        flash('请从「考试详情 → 分数修改记录」进入', 'warning')
        return redirect(url_for('grades.exams_list'))
    logs = (ScoreChangeLog.query.filter_by(exam_id=exam.id)
            .order_by(ScoreChangeLog.operated_at.desc(), ScoreChangeLog.id.desc())
            .limit(3000).all())
    return render_template('grades/score_changes.html', exam=exam, logs=logs)


@bp.route('/exams/<int:exam_id>/recalc', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_recalc(exam_id):
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    n = ranking.recalc_exam(exam_id)
    exam.status = 'imported'
    db.session.commit()
    # 修复：清除分析页真实使用的 grades_tab_ 缓存（原 grades_analysis_ 前缀无人写入，属无效清理）
    tab_service.clear_exam_cache(exam_id)
    log_operation(current_user, '重算', '考试', exam_id, f'{exam.name} 排名重算({n}行)', module='grades')
    flash(f'排名已重新计算（{n} 条成绩）', 'success')
    return redirect(url_for('grades.exam_detail', exam_id=exam_id))


@bp.route('/exams/<int:exam_id>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_delete(exam_id):
    """删除考试：需当前账号+密码二次确认；级联清理成绩/分层/AI报告/缓存"""
    from app.models.grades import ExamBand, AiReport
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    is_json = request.is_json
    if is_json:
        data = request.get_json(silent=True) or {}
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''
    else:
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

    def _fail(msg, code=400):
        if is_json:
            return jsonify(success=False, message=msg), code
        flash(msg, 'danger')
        return redirect(url_for('grades.exam_detail', exam_id=exam_id))

    if not username or not password:
        return _fail('请输入账号和密码后再删除')
    if username != current_user.username:
        return _fail('账号与当前登录账号不一致')
    if not current_user.check_password(password):
        return _fail('密码不正确')

    exam_name = f'{exam.grade} {exam.name}'
    # 2026-10-12 安全/质量审计 P0-1：原先漏删两张关联表 → 孤儿行永久残留：
    #   · score_change_logs（改分留痕，含学生姓名/班级快照）——表注释写"只增不改不删"，
    #     与删考试行为矛盾，删完考试后流水还挂在已不存在的 exam_id 上；
    #   · exam_teacher_links（任课快照）——links_of() 的惰性回填会反复命中空 exam。
    # 另外整段包 try/rollback：原实现中间任一步抛异常（如 DB 锁）会变 500 页面，
    # 且 session 处于脏状态。
    try:
        n_scores = ExamScore.query.filter_by(exam_id=exam_id).delete()
        n_bands = ExamBand.query.filter_by(exam_id=exam_id).delete()
        n_ai = AiReport.query.filter_by(exam_id=exam_id).delete()
        n_logs = ScoreChangeLog.query.filter_by(exam_id=exam_id).delete()
        n_links = ExamTeacherLink.query.filter_by(exam_id=exam_id).delete()
        # v1.19.47.29：级联删除本考试的考务批次（含其考场与名单），避免产生孤儿批次
        # 旧逻辑只删考试，导致 affair 的 exam_id 指向已删除考试，从考试列表无法再进入
        from app.modules.grades.routes.exam_affairs import delete_affair_cascade
        from app.models.grades import ExamAffair
        n_affair = 0
        for aff in ExamAffair.query.filter_by(exam_id=exam_id).all():
            delete_affair_cascade(aff.id)
            n_affair += 1
        db.session.delete(exam)
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error('删除考试失败 exam_id=%s: %s', exam_id, e, exc_info=True)
        return _fail(f'删除失败：{str(e)[:120]}', 500)
    invalidate_exam_cache(exam_id)
    tab_service.clear_exam_cache(exam_id)
    log_operation(current_user, '删除', '考试', exam_id,
                  f'{exam_name}（成绩{n_scores}条/分层{n_bands}条/AI报告{n_ai}条'
                  f'/改分留痕{n_logs}条/任课快照{n_links}条'
                  + (f'/考务批次{n_affair}个' if n_affair else '') + '）', module='grades')
    if is_json:
        msg = f'已删除《{exam_name}》：成绩 {n_scores} 条、分层 {n_bands} 条'
        if n_affair:
            msg += f'、关联考务批次 {n_affair} 个'
        return jsonify(success=True, message=msg)
    flash(f'考试「{exam_name}」及全部成绩已删除', 'success')
    return redirect(url_for('grades.exams_list'))


# ==================== 成绩导入向导 ====================

@bp.route('/exams/<int:exam_id>/import', methods=['GET'])
@login_required
@perm_required('grades.import')
def exam_import(exam_id):
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    return render_template('grades/import_wizard.html', exam=exam,
                           step='upload', mode='A', status_label=EXAM_STATUS_LABEL)


@bp.route('/exams/<int:exam_id>/import/upload', methods=['POST'])
@login_required
@perm_required('grades.import')
def exam_import_upload(exam_id):
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    mode = request.form.get('mode', 'A')
    remove_missing = request.form.get('remove_missing') == '1'
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要上传的 Excel 文件', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    # 2026-10-13 全面审计 M-6：统一上传校验（白名单 + 危险类型 + magic 嗅探），
    # 此前本入口直接读流交给 openpyxl —— 不校验扩展名，畸形/压缩炸弹文件可耗尽内存
    from app.utils.upload_guard import validate_upload
    ok, msg = validate_upload(file.filename, allowed_exts=['xlsx', 'xls'],
                              stream=file.stream)
    if not ok:
        flash(f'{msg}（当前文件：{file.filename}）', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    fname = file.filename
    try:
        stream = io.BytesIO(file.read())
        parsed = import_service.parse_score_excel(stream, exam)
    except ParseError as e:
        flash(str(e), 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    except Exception as e:
        current_app.logger.error(f'成绩解析异常: {e}', exc_info=True)
        flash('文件解析失败，请确认是标准模板格式（可下载模板参考）', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))

    diff = _preview_diff(exam, parsed, mode)
    token = str(uuid.uuid4())
    exam.import_draft = json.dumps({
        'fname': fname, 'mode': mode, 'remove_missing': remove_missing,
        'headers': parsed['headers'], 'rows': parsed['rows'], 'errors': parsed['errors'],
        'stats': parsed['stats'], 'template': parsed['template'],
        'diff': diff, 'created_at': datetime.now().isoformat(),
    }, ensure_ascii=False, default=str)
    exam.import_token = token
    db.session.commit()
    return render_template('grades/import_wizard.html', exam=exam, step='report',
                           draft=json.loads(exam.import_draft), token=token, mode=mode,
                           status_label=EXAM_STATUS_LABEL)


def _preview_diff(exam, parsed, mode):
    """与库内现有成绩对比的差异预览
    修复1：原先每行×每科各发一次 DB 查询（千人年级≈9000 次），改为一次性载入本场已有成绩比对
    修复2：更新计数仅统计分值真正变化的行（原先行存在即计数，数字虚高）
    """
    existing = {}
    nos = set()
    for r in ExamScore.query.filter_by(exam_id=exam.id).all():
        existing[(r.student_no, r.subject)] = r.score
        nos.add(r.student_no)
    new_nos = set()
    update_cnt = 0
    del_cnt = 0
    for r in parsed['rows']:
        no = r['no']
        if no not in nos:
            new_nos.add(no)
        for sub, score in r['subjects'].items():
            key = (no, sub)
            if score is None:
                if key in existing:
                    del_cnt += 1
            elif key in existing and existing[key] != score:
                update_cnt += 1
    file_nos = {r['no'] for r in parsed['rows']}
    unseen = len(nos - file_nos) if mode == 'A' else 0
    return {
        'new_students': len(new_nos),
        'updated_rows': update_cnt,
        'deleted_rows': del_cnt,
        'unseen': unseen,
    }


@bp.route('/exams/<int:exam_id>/import/confirm', methods=['POST'])
@login_required
@perm_required('grades.import')
def exam_import_confirm(exam_id):
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    token = request.form.get('token', '')
    if not exam.import_draft or not exam.import_token or exam.import_token != token:
        flash('导入批次已失效，请重新上传', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    try:
        draft = json.loads(exam.import_draft)
    except (json.JSONDecodeError, TypeError):
        flash('导入批次数据异常，请重新上传', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    parsed = {'headers': draft['headers'], 'rows': draft['rows']}
    mode = draft.get('mode', 'A')
    remove_missing = draft.get('remove_missing', False)
    try:
        summary = store_service.apply_import(exam, parsed, mode=mode,
                                             remove_missing=remove_missing)
        ranking.recalc_exam(exam_id)
        # v1.19.47.29 任课快照：把本次导入时的任课教师映射定格到本场考试（教师维度分析读它，
        # 以后教师调整/重新分班都不会把历史成绩归到新教师名下）
        from app.modules.grades.services import teacher_snapshot_service as tss
        n_tch = tss.snapshot_exam(exam, source='import')
        exam.status = 'imported'
        exam.import_info = json.dumps({
            'fname': draft.get('fname', ''), 'mode': mode,
            'summary': summary, 'errors': len(draft.get('errors', [])),
            'teacher_snapshot': n_tch,
            'operator': current_user.real_name,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }, ensure_ascii=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'成绩导入失败: {e}', exc_info=True)
        flash(f'导入失败，数据已回滚：{e}', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    # 修复：按本场考试清除分析页 grades_tab_ 缓存（原前缀无人写入，等于没清）
    tab_service.clear_exam_cache(exam_id)
    log_operation(current_user, '导入', '考试', exam_id,
                  f'{exam.name} {mode}模式 差异{json.dumps(summary, ensure_ascii=False)}',
                  module='grades')
    flash(f'导入完成：新增 {summary["new_students"]} 人，更新 {summary["updated_rows"]} 条，'
          f'删除 {summary["deleted_rows"]} 条，未出现保留 {summary["unseen"]} 人'
          + (f'；已记下本场任课快照 {n_tch} 条' if n_tch else ''), 'success')
    return redirect(url_for('grades.exam_detail', exam_id=exam_id))


@bp.route('/exams/<int:exam_id>/import/errors')
@login_required
@perm_required('grades.import')
def exam_import_errors(exam_id):
    """导出最近一次预检的错误明细 xlsx"""
    exam = assert_exam_visible(exam_id)   # H-4：校验考试所属年级范围
    if not exam.import_draft:
        abort(404)
    try:
        draft = json.loads(exam.import_draft)
        errors = draft.get('errors', [])
    except Exception:
        abort(404)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '错误明细'
    ws.append(['行号', '学号', '原因'])
    for e in errors:
        ws.append([e.get('line'), e.get('no'), e.get('reason')])
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=safe_download_name(
                         f'导入错误_{exam.name}') + f'_{datetime.now():%Y%m%d_%H%M%S}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ==================== 模板下载 ====================

@bp.route('/template.xlsx')
@login_required
@perm_required('grades.import')
def template_download():
    wb = openpyxl.Workbook()
    # Sheet1 模板A
    ws = wb.active
    ws.title = '模板A(全年级全科)'
    ws.append(['学号', '姓名', '年级', '班级', '总分',
               '语文', '数学', '外语', '物理', '历史', '化学', '生物', '政治', '地理'])
    ws.append(['20250001', '张三', '2025级', '01班', 560, 110, 108, 95, 85, None, 76, None, 82, 80])
    for col, width in zip('ABCDEFGHIJKLMN', [10, 10, 10, 8, 8, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7]):
        ws.column_dimensions[col].width = width
    # Sheet2 模板B
    ws2 = wb.create_sheet('模板B(分批次分科)')
    ws2.append(['学号', '姓名', '年级', '班级', '语文', '数学', '外语'])
    ws2.append(['20250001', '张三', '2025级', '01班', 110, 108, 95])
    for col, width in zip('ABCDEFG', [10, 10, 10, 8, 7, 7, 7]):
        ws2.column_dimensions[col].width = width
    # Sheet3 填表说明
    ws3 = wb.create_sheet('填表说明')
    tips = [
        ['成绩导入模板说明'],
        [''],
        ['1. 表头必须保留（第一行），列顺序不限；缺考科目留空单元格；0 分填 0。'],
        ['2. 学号必填，以学号匹配学生；姓名/班级辅助展示，班级仅用于分批范围说明。'],
        ['3. “年级”列可选：填写时须与考试年级一致（如 2025级），不一致的行会被拦截。'],
        ['4. “总分”列可选：外部总分优先采用（兼容含听力的总分）；不填则由系统按应考 6 科累加。'],
        ['5. 科目列可只保留本次要导入的科目（分批导入），未出现的科目列不会被改动。'],
        ['6. 历史方向班级不考“物理/化学/生物”，对应单元格留空即可；也可以直接粘贴参考成绩表整表，多余列自动忽略。'],
        ['7. 学号匹配不到、分数超范围的行会列入错误报告，可下载错误明细修改后重传。'],
    ]
    for row in tips:
        ws3.append(row)
    ws3.column_dimensions['A'].width = 110
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=f'成绩导入模板_{date.today():%Y%m%d}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

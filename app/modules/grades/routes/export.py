# StuLink v1.19.68.64 2026-10-10
# 成绩分析导出：四大模块表格 → Excel（openpyxl，与页面所见一致）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
from datetime import datetime

import openpyxl
from flask import send_file, abort, request, current_app
from flask_login import login_required, current_user
from app.models.grades import Exam
from app.modules.grades import bp
from app.modules.grades.services import tab_service, scope as scope_service
from app.modules.grades.services import compare_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.xlsx_style import write_table   # 表头样式 + xl_row 注入防护

_TAB_LABEL = {'grade': '年级分析', 'class': '班级分析', 'subject': '学科分析',
              'teacher': '任课教师分析', 'compare': '班级对比分析'}
# 注：tab 可见性统一由 analysis._guard_tab 判定（含 scope_type=school 校级领导档），
# 此处不再维护第二份 _TAB_BY_ROLE —— 2026-10-12 审计 L-1：两份字典口径不一致
# 曾导致校级领导能看不能导。

def _build_workbook(tab_label, exam_name, tables):
    wb = openpyxl.Workbook()
    ws0 = wb.active
    ws0.title = '说明'
    notes = [
        [f'{tab_label}导出'],
        ['考试', exam_name or ''],
        ['导出时间', datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
        ['导出人', current_user.real_name],
        ['口径', '参考数=有成绩的参考学生（不分学籍状态）；排名=方向内排名；分层列随考试配置'],
    ]
    for row in notes:
        ws0.append(row)
    ws0.column_dimensions['A'].width = 12
    ws0.column_dimensions['B'].width = 60

    for tkey, table in tables.items():
        ws = wb.create_sheet(title=(table.get('title') or tkey)[:31])
        # 表头样式与 M-4 公式注入防护都在 write_table 里（与一键导出同一份）
        write_table(ws, table)
    return wb


def _teacher_payload(exam, subject, denom):
    """教师分析取数（/export/teacher 与一键导出共用同一份权限口径）"""
    scope_type, _ = scope_service.get_scope(current_user)
    if current_user.has_role('teacher'):
        links = scope_service.teacher_links(current_user)
        return tab_service.teacher_tab(exam.id, subject=subject or None,
                                       links=links, grade=exam.grade,
                                       denom=denom)
    if (current_user.role == 'admin'
            or scope_service.has_user_scope(current_user)):
        return tab_service.teacher_tab(exam.id, subject=subject or None,
                                       links=None, grade=exam.grade,
                                       denom=denom)
    if scope_type == 'grade':
        from app.models.grades import TeacherSubjectLink
        # 2026-10-19：管理年级可多选，取本场考试年级（已校验可见）
        links = TeacherSubjectLink.query.filter_by(
            grade=exam.grade, active=True).all()
        return tab_service.teacher_tab(exam.id, subject=subject or None,
                                       links=links, grade=exam.grade,
                                       denom=denom)
    abort(403)


@bp.route('/export/<tab>')
@login_required
@perm_required('grades.view')
def export_tab(tab):
    """导出模块表格 xlsx（复用各 tab 组装数据，与页面所见一致）"""
    if tab not in _TAB_LABEL:
        abort(404)
    # 2026-10-12 安全审计 L-1：原先用 _TAB_BY_ROLE + has_user_scope 判定，
    # 漏了「权限组 scope_type=school」这一档（校级领导 / school_viewer）——
    # 他们能打开分析页（analysis._guard_tab 有 school 分支）却无法导出 Excel。
    # 现直接复用 analysis._guard_tab，保证「能看就能导」。
    from app.modules.grades.routes.analysis import _guard_tab
    _guard_tab(tab)
    exam_id = request.args.get('exam_id', type=int)
    exam = Exam.query.get_or_404(exam_id)
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    class_name = request.args.get('class_name', '').strip()
    subject = request.args.get('subject', '').strip()
    direction = (request.args.get('direction') or '').strip()
    selection = (request.args.get('selection') or '').strip()   # 2026-10-16 组合维度
    # 2026-10-17 统计口径：导出必须与页面所见一致（ref=参考数 / enrolled=在籍人数）
    denom = (request.args.get('denom') or '').strip()
    denom = denom if denom in ('ref', 'enrolled') else 'ref'
    # 班级对比：classes=01班,02班…（顺序=对比展示顺序）
    raw_classes = (request.args.get('classes') or '').strip()
    compare_classes = [c.strip() for c in raw_classes.split(',') if c.strip()]
    # 2026-10-15 用户口径：班主任 / 任课教师也能看「所在年级的成绩分析」，
    # 导出与本文件的既有设计口径「能看就能导」保持一致（_guard_tab 已放行 grade/subject）。
    # 导出**班级明细**（tab=class）含逐生全科 + 总分/班排 → 与 API 端点同闸门：
    # 「该班总分口径可见」。2026-10-15 审计修复（P0）：此前只校验 homeroom_teacher，
    # 用户级按班授权账号与「班主任+任课」看**他班**都能导出整班逐生总分。
    if tab == 'class':
        from app.modules.grades.services import score_guard as _sg
        if not _sg.resolve(current_user).class_total_visible(exam.grade, class_name):
            abort(403)
    try:
        if tab == 'grade':
            payload = tab_service.grade_tab(exam_id, direction, selection, denom)
        elif tab == 'class':
            payload = tab_service.class_tab(exam_id, class_name, direction, selection, denom)
        elif tab == 'subject':
            payload = tab_service.subject_tab(exam_id, subject, direction, selection, denom)
        elif tab == 'compare':
            # 多班对比：班级可见性过滤与 API 端点同口径
            # 2026-10-11 审计修复：`if limited:` 空列表时 fail-open → 改 is not None（fail-closed）
            limited = scope_service.visible_classes(current_user)
            if limited is not None:
                allowed = {c for g, c in limited if g == exam.grade}
                compare_classes = [c for c in compare_classes if c in allowed]
            if len(compare_classes) < 2:
                abort(400, description='请至少选择 2 个班级进行对比')
            payload = compare_service.compare_tab(exam_id, compare_classes[:20],
                                                  direction, denom)
        else:
            # 教师分析：与 analysis API 同权限
            payload = _teacher_payload(exam, subject, denom)
        tables = payload.get('tables') or {}
        if not tables:
            tables = {'__empty': {'title': '提示', 'columns': [{'key': 'msg', 'label': '说明',
                                                                'type': 'text'}],
                                  'rows': [{'msg': '暂无数据（考试未导入成绩或无有效参考学生）'}]}}
        wb = _build_workbook(_TAB_LABEL[tab], exam.name, tables)
    except Exception as e:
        current_app.logger.error(f'导出 {tab} 失败: {e}', exc_info=True)
        abort(500)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    fname = f'{_TAB_LABEL[tab]}_{exam.grade}_{exam.exam_date:%Y%m%d}_{datetime.now():%H%M%S}.xlsx'
    log_operation(current_user, '导出', '成绩分析', exam_id,
                  f'{_TAB_LABEL[tab]} {exam.name}', module='grades')
    return send_file(out, as_attachment=True, download_name=fname,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# StuLink v1.19.58.64 2026-10-20
# 成绩分析「一键导出」：一场考试的全部模块 → 一个 xlsx
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩分析一键导出（路由层：参数 + 权限 + 下载；取数在 services）。

与 `/grades/export/<tab>` 的区别：那个只导当前页签，要逐页签点五次；
这里把「能看的模块」一次写进同一个工作簿，适合直接发给领导或存档。

模块与权限（"能看就能导"，与页面同一份 `_guard_tab`）：
- 年级分析：grade
- 学科分析：subject；9 科的「各班对比」合并为一张（多一列科目）
- 任课教师：teacher，取数复用 `export._teacher_payload`
- 班级分析：只导**当前选中班**，且该班总分口径可见（逐生明细不跨班）
- 班级对比：只在已选 ≥2 个班且都在可见范围内时导出

无权限的模块静默跳过并在「说明」页列出 —— 一个模块 403 不该让整份导出失败。
"""
import io
from datetime import datetime

from flask import abort, current_app, request, send_file
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from app.models.grades import Exam
from app.modules.grades import bp
from app.modules.grades.routes.analysis import _guard_tab
from app.modules.grades.routes.export import _teacher_payload
from app.modules.grades.services import analysis_export as svc
from app.modules.grades.services import scope as scope_service
from app.modules.grades.services import score_guard
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


def _params():
    """读取与页面同名的筛选参数（缺省即"全部"）"""
    denom = (request.args.get('denom') or '').strip()
    return {
        'direction': (request.args.get('direction') or '').strip(),
        'selection': (request.args.get('selection') or '').strip(),
        'class_name': (request.args.get('class_name') or '').strip(),
        'classes': [c.strip() for c in
                    (request.args.get('classes') or '').split(',')
                    if c.strip()],
        'denom': denom if denom in ('ref', 'enrolled') else 'ref',
    }


def _can(tab):
    """与页面同一份可见性判定；403 视为"无权限"而不是整单失败"""
    try:
        _guard_tab(tab)
        return True
    except HTTPException as exc:
        if exc.code == 403:
            return False
        raise


def _class_part(exam, p):
    """只导当前选中班；逐生明细不跨班（与 /export/class 同一道闸门）"""
    name = p['class_name']
    if not name:
        return []
    scope = score_guard.resolve(current_user)
    if not scope.class_total_visible(exam.grade, name):
        return []
    return svc.class_sheets(exam.id, name, p)


def _compare_part(exam, p):
    classes = list(p['classes'])
    limited = scope_service.visible_classes(current_user)
    if limited is not None:
        allowed = {c for g, c in limited if g == exam.grade}
        classes = [c for c in classes if c in allowed]
    if len(classes) < 2:
        return []
    return svc.compare_sheets(exam.id, classes[:20], p)


def _collect(exam, p):
    """按模块收集 (表名, 表)；无权限/无选择记入 skipped（说明页可见）"""
    sheets, skipped = [], []
    sections = (
        ('grade', '年级分析', lambda: svc.grade_sheets(exam.id, p)),
        ('subject', '学科分析', lambda: svc.subject_sheets(exam.id, p)),
        # 教师表不按学科收窄：全量导出给的是「全科全教师」口径
        ('teacher', '任课教师',
         lambda: svc.sheets_of(_teacher_payload(exam, '', p['denom']))),
        ('class', '班级分析', lambda: _class_part(exam, p)),
        ('compare', '班级对比', lambda: _compare_part(exam, p)),
    )
    for tab, label, build in sections:
        if not _can(tab):
            skipped.append(label)
            continue
        part = build()
        if part:
            sheets.extend(part)
        elif tab in ('class', 'compare'):
            skipped.append(label)
    return sheets, skipped


@bp.route('/export/full')
@login_required
@perm_required('grades.view')
def export_full():
    """一键导出：一场考试的全部模块 → 一个 xlsx"""
    exam = Exam.query.get_or_404(request.args.get('exam_id', type=int))
    try:
        scope_service.check_exam_visible(current_user, exam)
    except PermissionError:
        abort(403)
    p = _params()
    try:
        sheets, skipped = _collect(exam, p)
        wb = svc.build_workbook(exam, p, sheets, skipped, current_user)
    except Exception as exc:                       # noqa: BLE001
        current_app.logger.error('一键导出成绩分析失败: %s', exc, exc_info=True)
        abort(500)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    fname = ('成绩分析_%s_%s_%s.xlsx'
             % (exam.grade, exam.exam_date.strftime('%Y%m%d'),
                datetime.now().strftime('%H%M%S')))
    log_operation(current_user, '导出', '成绩分析', exam.id,
                  '一键导出 %s' % exam.name, module='grades')
    return send_file(out, as_attachment=True, download_name=fname,
                     mimetype=_XLSX_MIME)

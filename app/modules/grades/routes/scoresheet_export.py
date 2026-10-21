# -*- coding: utf-8 -*-
"""成绩单导出（2026-10-21）：把页面当前筛选/排序的学生成绩导成 Excel。

数据范围与成绩单页面**同源**：直接复用 `routes/exams.py::_exam_page_rows`
（`all_rows=True` 取全部匹配行）与 `_scoresheet_scope` 的班级收敛 ——
导出不能成为绕过「逐生总分收口」的旁路，因此这里不重写任何 scope 判定，
只把页面参数原样透传（cls / dir / sort / order）。
"""
import io

from flask import request, send_file
from flask_login import current_user, login_required

from app.models.grades import SUBJECTS
from app.modules.grades import bp
from app.modules.grades.routes.exams import (_exam_page_rows, _scoresheet_scope,
                                             _sort_args, assert_exam_visible)
from app.modules.grades.services import scoresheet_xlsx, xlsx_kit
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


def _filters(classes):
    """解析页面当前筛选参数（与 exam_detail 同一套白名单校验）。

    返回 (班级, 方向, 排序键, 排序方向)；班级与方向互斥，越界值一律回空。
    """
    cur_class = (request.args.get('cls') or '').strip()
    if cur_class not in classes:
        cur_class = ''
    cur_dir = (request.args.get('dir') or '').strip()
    if cur_dir not in ('物理', '历史') or cur_class:
        cur_dir = ''
    sort_key, order = _sort_args(request.args)
    return cur_class, cur_dir, sort_key, order


@bp.route('/exams/<int:exam_id>/scores/export')
@login_required
@perm_required('grades.view')
def exam_scores_export(exam_id):
    """按当前筛选导出成绩单（不分页，导出全部匹配学生）。"""
    exam = assert_exam_visible(exam_id)      # H-4：校验考试所属年级范围
    allowed_classes, classes = _scoresheet_scope(exam)
    cur_class, cur_dir, sort_key, order = _filters(classes)
    students, _page, _pages, total = _exam_page_rows(
        exam_id, 1, 0, class_name=cur_class or None,
        direction=cur_dir or None, sort_key=sort_key, order=order,
        allowed_classes=allowed_classes, all_rows=True)
    meta = {'cls': cur_class, 'dir': cur_dir, 'sort_key': sort_key,
            'order': order, 'total': total}
    buf = io.BytesIO()
    scoresheet_xlsx.build(exam, students, SUBJECTS, meta).save(buf)
    buf.seek(0)
    log_operation(current_user, '导出', '成绩单', exam_id,
                  f"{exam.name} 班级={cur_class or '全部'} "
                  f"方向={cur_dir or '全部'} 排序={sort_key or '默认'} "
                  f'人数={total}', module='grades')
    scope_name = cur_class or cur_dir or '全部'
    fname = (f'成绩单_{xlsx_kit.safe_fname(exam.name)}_'
             f'{xlsx_kit.safe_fname(scope_name)}.xlsx')
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype=_XLSX_MIME)

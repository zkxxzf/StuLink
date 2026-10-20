# -*- coding: utf-8 -*-
"""临界生名单 Excel 导出（2026-10-19）。

数据范围与页面**同源**（`near_line_view.load`）：班主任只见本班、纯任课教师
不含总分口径 → 空名单。导出绝不能成为绕过「逐生总分收口」的旁路，因此这里
不重写 scope，只调用页面同一个加载函数。
"""
import io

from flask import flash, redirect, request, send_file, url_for
from flask_login import current_user, login_required

from app.modules.grades import bp
from app.modules.grades.services import (near_line_view, near_line_xlsx,
                                         xlsx_kit)
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')
_FILTER_KEYS = ('direction', 'layer', 'above', 'below', 'cls')


@bp.route('/exams/<int:exam_id>/near-line/export')
@login_required
@perm_required('grades.view')
def near_line_export(exam_id):
    """按页面当前筛选导出临界生名单（明细 + 班级对比）。"""
    ctx = near_line_view.load(exam_id, request.args)
    if ctx['error']:
        flash(ctx['error'], 'warning')
        keep = {k: request.args.get(k) for k in _FILTER_KEYS
                if request.args.get(k)}
        return redirect(url_for('grades.exam_near_line', exam_id=exam_id,
                                **keep))
    data = ctx['data']
    buf = io.BytesIO()
    near_line_xlsx.build(ctx).save(buf)
    buf.seek(0)
    log_operation(current_user, '导出', '临界生名单', exam_id,
                  f"{data['exam']['name']} {data['direction']} "
                  f"{data['layer']} 线上{data['above']}/线下{data['below']} "
                  f"班级={data['class_name'] or '全部'}", module='grades')
    fname = (f"临界生_{xlsx_kit.safe_fname(data['exam']['name'])}_"
             f"{xlsx_kit.safe_fname(data['direction'])}_"
             f"{xlsx_kit.safe_fname(data['layer'])}.xlsx")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype=_XLSX_MIME)

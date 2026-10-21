# StuLink v1.19.58.64 2026-10-20
# 限时练：模板文件导入（上传预检 → 确认落库）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练模板文件导入（追加式单科：一次一科，同科重导 = 整科覆盖）。

流程：上传 → 解析 → 学生匹配 → 差异预览（写 import_draft + 一次性 token）
→ 确认（整科覆盖 + 重算名次）。AI 导入见 ``timed_ai_routes.py``。
"""
import json
import uuid
from datetime import datetime

from flask import flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.modules.grades import bp
from app.modules.grades.routes.timed_upload import (read_upload,
                                                    subject_from_form)
from app.modules.grades.services import (timed_analysis, timed_import,
                                         timed_match, timed_service)
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation


@bp.route('/timed/<int:tid>/import/upload', methods=['POST'])
@login_required
@perm_required('grades.import')
def timed_import_upload(tid):
    """模板文件导入预检：解析 → 匹配 → 差异预览（不写成绩）"""
    exam = timed_service.assert_visible(tid)
    timed_service.assert_grade_writable(exam.grade)
    try:
        subject = subject_from_form()
        timed_service.assert_subject_importable(exam.grade, subject)
        raw = read_upload()
        parsed = timed_import.parse_sheet(raw, subject, exam.grade)
        rows, errors, mstats = timed_match.match_students(
            parsed['rows'], exam.grade)
    except timed_import.TimedParseError as e:
        flash(str(e), 'danger')
        return redirect(url_for('grades.timed_detail', tid=exam.id,
                                tab='import'))
    draft = {'source': 'template', 'subject': subject, 'rows': rows,
             'errors': parsed['errors'] + errors,
             'stats': {**parsed['stats'], **mstats},
             'diff': timed_match.preview_diff(exam.id, subject, rows),
             'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    exam.import_draft = json.dumps(draft, ensure_ascii=False)
    exam.import_token = str(uuid.uuid4())
    db.session.commit()
    return redirect(url_for('grades.timed_detail', tid=exam.id, tab='import',
                            step='report'))


@bp.route('/timed/<int:tid>/import/confirm', methods=['POST'])
@login_required
@perm_required('grades.import')
def timed_import_confirm(tid):
    """确认导入：整科覆盖 + 重算名次（一次性 token，防串批）"""
    exam = timed_service.assert_visible(tid)
    timed_service.assert_grade_writable(exam.grade)
    token = (request.form.get('token') or '').strip()
    draft = timed_service.report_of(exam)
    if not draft or not token or token != (exam.import_token or ''):
        flash('导入批次已失效，请重新上传文件', 'danger')
        return redirect(url_for('grades.timed_detail', tid=exam.id,
                                tab='import'))
    if not draft.get('rows'):
        # 整科覆盖前必须保证本次至少匹配到 1 人，否则会把该科清空
        flash('本次没有匹配到任何学生，已取消导入', 'danger')
        return redirect(url_for('grades.timed_detail', tid=exam.id,
                                tab='import'))
    subject = draft['subject']
    # 草稿可能是别人（有权限的账号）留下的：确认前再校验一次本账号能否导入该科
    timed_service.assert_subject_importable(exam.grade, subject)
    try:
        timed_service.delete_scores_of(exam.id, subject)
        db.session.flush()
        timed_analysis.save_subject(exam, subject, draft['rows'])
        exam.status = 'imported'
        exam.import_draft = None
        exam.import_token = None
        # 与脚本导入同口径：届内按日期重排成「2026级20260918限时练No.N」
        timed_service.renumber_grade(exam.grade)
        db.session.commit()
    except Exception as e:                              # noqa: BLE001
        db.session.rollback()
        flash(f'导入失败：{e}', 'danger')
        return redirect(url_for('grades.timed_detail', tid=exam.id,
                                tab='import'))
    # 缓存必须在 commit 之后清（save_subject 里清会被并发请求用旧数据重建）
    timed_analysis.invalidate(exam.id)
    removed = (draft.get('diff') or {}).get('deleted_rows') or 0
    log_operation(current_user, '导入', '限时练成绩', exam.id,
                  f'{exam.grade} {exam.name} · {subject} '
                  f'{len(draft["rows"])} 人（替换原有 {removed} 条）',
                  module='grades')
    flash(f'{subject} 成绩已导入（{len(draft["rows"])} 人'
          + (f'，替换原有 {removed} 条' if removed else '') + '）', 'success')
    # 'scoresheet' 页签本批已删除（静默回落到学生成绩），直接给汇总表页签
    return redirect(url_for('grades.timed_detail', tid=exam.id,
                            tab='students'))

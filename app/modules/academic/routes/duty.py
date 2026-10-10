# StuLink v1.18.9.1 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""备课组长（2026-09-25）。

`/academic/leaders`：备课组长名单（按学科分组维护，含职责与备课组范围），
落库在 academic.db.subject_leaders。

已下线（2026-10-10）：
- 任课安排（原 `/academic/duty`）：教师-学科关系以「任课教师映射」
  （TeacherSubjectLink）为唯一依据；
- 晚自习值班（原 `/academic/night-duty`、`/academic/schedule/<sid>/night-duty`）：
  晚自习在「全校总课表」里本就按节次列出并带值班教师，独立页面重复，整功能删除。
"""
from datetime import datetime

from flask import (render_template, request, redirect, url_for, flash,
                   send_file)
from flask_login import login_required, current_user

from app.models.academic import DUTY_TEMPLATES, SUBJECT_ORDER
from app.modules.academic import bp
from app.modules.academic.services import duty_service as svc
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')

# ── 备课组长名单 ───────────────────────────────────────────────────────────

def _leader_filters():
    return {
        'school_year': (request.args.get('school_year') or '').strip(),
        'term': (request.args.get('term') or '').strip(),
        'subject': (request.args.get('subject') or '').strip(),
        'grade': (request.args.get('grade') or '').strip(),
        'keyword': (request.args.get('q') or '').strip(),
    }


@bp.route('/leaders')
@login_required
@perm_required('academic.view')
def subject_leaders():
    """备课组长名单：按学科分组展示，可维护（需 academic.edit）。"""
    f = _leader_filters()
    years = svc.leader_years()
    school_year = f['school_year'] or (years[0] if years else '')
    leaders = svc.list_leaders(school_year=school_year or None,
                               term=f['term'] or None,
                               subject=f['subject'] or None,
                               grade=f['grade'] or None,
                               keyword=f['keyword'] or None)
    return render_template('academic/subject_leaders.html',
                           leaders=leaders, groups=svc.group_leaders(leaders),
                           years=years, school_year=school_year,
                           subjects=SUBJECT_ORDER, duty_templates=DUTY_TEMPLATES,
                           grades=_grade_options(),
                           f=f, can_edit=current_user.has_perm('academic.edit'))


def _grade_options():
    """年级下拉候选：班级档案里的年级 + 高一/高二/高三兜底。"""
    grades = []
    try:
        from app.models import ClassProfile
        grades = sorted({cp.grade for cp in ClassProfile.query.all() if cp.grade})
    except Exception:  # noqa: BLE001
        pass
    for g in ('高一', '高二', '高三'):
        if g not in grades:
            grades.append(g)
    return grades


@bp.route('/leaders/save', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_save():
    """新增/更新备课组长（同学年×学期×年级×学科重复保存即更新）"""
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data = data or {}
    try:
        leader, created = svc.save_leader(data, operator_id=current_user.id)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.subject_leaders',
                                school_year=(data.get('school_year') or '').strip()))
    log_operation(current_user, '创建' if created else '更新', '备课组长', leader.id,
                  f'{leader.school_year} {leader.grade or "全校"}{leader.subject}'
                  f' {leader.leader_name}', module='academic')
    flash(f'备课组长「{leader.leader_name}」（{leader.grade or "全校"}{leader.subject}）'
          f'已{"新增" if created else "更新"}', 'success')
    return redirect(url_for('academic.subject_leaders',
                            school_year=leader.school_year,
                            subject=leader.subject or None))


@bp.route('/leaders/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_delete():
    """删除备课组长记录"""
    lid = request.form.get('id', type=int)
    if not lid and request.is_json:
        lid = (request.get_json(silent=True) or {}).get('id')
    info = svc.delete_leader(lid)
    if not info:
        flash('记录不存在或已删除', 'warning')
    else:
        log_operation(current_user, '删除', '备课组长', lid, info, module='academic')
        flash(f'已删除：{info}', 'success')
    return redirect(url_for('academic.subject_leaders',
                            school_year=request.form.get('school_year') or None))


@bp.route('/leaders/export')
@login_required
@perm_required('academic.view')
def subject_leader_export():
    """导出备课组长名单 Excel"""
    f = _leader_filters()
    years = svc.leader_years()
    school_year = f['school_year'] or (years[0] if years else '')
    leaders = svc.list_leaders(school_year=school_year or None,
                               term=f['term'] or None,
                               subject=f['subject'] or None,
                               grade=f['grade'] or None,
                               keyword=f['keyword'] or None)
    buf = svc.export_leaders_workbook(leaders, school_year=school_year)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(buf, as_attachment=True,
                     download_name=f'备课组长名单_{school_year or ""}_{stamp}.xlsx',
                     mimetype=_XLSX_MIME)

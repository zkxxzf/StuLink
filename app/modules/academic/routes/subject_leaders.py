# StuLink v1.19.48.1 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科组长（2026-09-25 建，2026-10-14 由「备课组长」升级）。

`/academic/leaders`：学科组长名单，一页两类 ——

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校；
- 两类组长都必须是本学科教师（任课教师映射为唯一依据，课表兜底）；
- 体育 / 音乐 / 美术 / 信息技术 / 通用技术 / 心理 合并展示为「综合组」。

落库在 academic.db.subject_leaders。

已下线（2026-10-10）：
- 任课安排（原 `/academic/duty`）：教师-学科关系以「任课教师映射」
  （TeacherSubjectLink）为唯一依据；
- 晚自习值班（原 `/academic/night-duty`、`/academic/schedule/<sid>/night-duty`）：
  晚自习在「全校总课表」里本就按节次列出并带值班教师，独立页面重复，整功能删除。
"""
import logging
from datetime import datetime

from flask import (jsonify, render_template, request, redirect, url_for, flash,
                   send_file)
from flask_login import login_required, current_user

from app.models.academic import (COMPREHENSIVE_GROUP, LEADER_CATEGORIES,
                                 LEADER_GRADES, SUBJECT_ORDER)
from app.modules.academic import bp
from app.modules.academic.services import subject_leader_service as svc
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
logger = logging.getLogger(__name__)

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')

# ── 学科组长名单 ───────────────────────────────────────────────────────────


def _leader_filters():
    return {
        'school_year': (request.args.get('school_year') or '').strip(),
        'term': (request.args.get('term') or '').strip(),
        'subject': (request.args.get('subject') or '').strip(),
        'grade': (request.args.get('grade') or '').strip(),
        'category': (request.args.get('category') or '').strip(),
        'keyword': (request.args.get('q') or '').strip(),
    }


def _subject_options():
    """学科下拉：常规学科 + 综合组（音体美信通心的合并项）。"""
    return list(SUBJECT_ORDER) + [COMPREHENSIVE_GROUP]


def _collect_leaders(f):
    years = svc.leader_years()
    school_year = f['school_year'] or (years[0] if years else '')
    leaders = svc.list_leaders(school_year=school_year or None,
                               term=f['term'] or None,
                               subject=f['subject'] or None,
                               grade=f['grade'] or None,
                               keyword=f['keyword'] or None,
                               category=f['category'] or None)
    return years, school_year, leaders


@bp.route('/leaders')
@login_required
@perm_required('academic.view')
def subject_leaders():
    """学科组长名单：教研组长（不分年级）＋ 备课组长（年级 × 学科）"""
    f = _leader_filters()
    years, school_year, leaders = _collect_leaders(f)
    cats = [f['category']] if f['category'] in LEADER_CATEGORIES else list(LEADER_CATEGORIES)
    return render_template('academic/subject_leaders.html',
                           leaders=leaders,
                           grouped=svc.group_leaders_by_category(leaders, cats),
                           years=years, school_year=school_year,
                           subjects=_subject_options(),
                           categories=list(LEADER_CATEGORIES),
                           duty_templates={c: svc.duty_templates(c) for c in LEADER_CATEGORIES},
                           grades=_grade_options(),
                           f=f, can_edit=current_user.has_perm('academic.edit'))


def _grade_options():
    """年级下拉候选：备课组长固定高中三个年级，班级档案里的年级兜底。"""
    grades = list(LEADER_GRADES)
    try:
        from app.models import ClassProfile
        for g in sorted({cp.grade for cp in ClassProfile.query.all() if cp.grade}):
            if g not in grades:
                grades.append(g)
    except Exception as _e:  # noqa: BLE001
        logger.warning('subject_leaders::_grade_options 异常（已降级处理）: %s', _e)
    return grades


@bp.route('/leaders/candidates')
@login_required
@perm_required('academic.view')
def subject_leader_candidates():
    """本学科候选组长 JSON（下拉只给本学科教师，兼作前端提示依据）。"""
    subject = (request.args.get('subject') or '').strip()
    keyword = (request.args.get('q') or '').strip()
    return jsonify(success=True, subject=subject,
                   data=svc.teacher_candidates(subject, keyword=keyword or None))


@bp.route('/leaders/save', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_save():
    """新增/更新学科组长（同学年×学期×年级×学科×类别重复保存即更新）"""
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    data = data or {}
    try:
        leader, created, known = svc.save_leader(data, operator_id=current_user.id)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('academic.subject_leaders',
                                school_year=(data.get('school_year') or '').strip()))
    if not known:
        flash(f'教师档案里没有「{leader.leader_name}」（已按手填姓名保存）。'
              f'如需自动带出教师编号，请先在「教师名单」补建该教师并维护任课学科', 'warning')
    log_operation(current_user, '创建' if created else '更新', '学科组长', leader.id,
                  f'{leader.school_year} {leader.grade or "全校"}{leader.subject}'
                  f' {leader.category} {leader.leader_name}', module='academic')
    flash(f'{leader.category}「{leader.leader_name}」（{leader.grade or "全校"}'
          f'{leader.subject}）已{"新增" if created else "更新"}', 'success')
    return redirect(url_for('academic.subject_leaders',
                            school_year=leader.school_year,
                            subject=leader.subject or None,
                            category=leader.category or None))


@bp.route('/leaders/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_delete():
    """删除学科组长记录"""
    lid = request.form.get('id', type=int)
    if not lid and request.is_json:
        lid = (request.get_json(silent=True) or {}).get('id')
    info = svc.delete_leader(lid)
    if not info:
        flash('记录不存在或已删除', 'warning')
    else:
        log_operation(current_user, '删除', '学科组长', lid, info, module='academic')
        flash(f'已删除：{info}', 'success')
    return redirect(url_for('academic.subject_leaders',
                            school_year=request.form.get('school_year') or None))


@bp.route('/leaders/export')
@login_required
@perm_required('academic.view')
def subject_leader_export():
    """导出学科组长名单 Excel（备课组长 + 教研组长）"""
    f = _leader_filters()
    years, school_year, leaders = _collect_leaders(f)
    buf = svc.export_leaders_workbook(leaders, school_year=school_year)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(buf, as_attachment=True,
                     download_name=f'学科组长名单_{school_year or ""}_{stamp}.xlsx',
                     mimetype=_XLSX_MIME)

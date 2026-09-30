# StuLink v1.18.7.0 2026-09-30
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教务 · 教师业绩库：录入 / 列表筛选分页 / 审核（含审核意见）/ 统计 / 导出。

2026-09-26 增强：
- 列表由「limit 300 无分页」改为分页，并补年度筛选；
- 新增统计（总量/待审/已通过/已驳回 + 类别与级别分布）；
- 审核支持填写审核意见（驳回必填，教师在工作台可见）；
- 新增 Excel 导出（遵循当前筛选条件）。
"""
import io
from datetime import date, datetime

from flask import (render_template, request, redirect, url_for, flash, abort,
                   send_file, send_from_directory, jsonify)
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.academic import (AchievementAttachment, TeacherAchievement, Teacher,
                                 ACHIEVEMENT_CATEGORIES, ACHIEVEMENT_LEVELS,
                                 ACHIEVEMENT_STATUS)
from app.modules.academic import bp
from app.modules.academic.services import achievement_service as ach_svc
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_CATEGORY_KEYS = {k for k, _ in ACHIEVEMENT_CATEGORIES}
_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


def _filters():
    return {
        'teacher_uid': (request.args.get('teacher_uid') or '').strip(),
        'category': (request.args.get('category') or '').strip(),
        'status': (request.args.get('status') or '').strip(),
        'year': (request.args.get('year') or '').strip(),
        'tag': (request.args.get('tag') or '').strip()[:12],      # 标签筛选
    }


def _achievement_query(f):
    """列表与导出共用同一套筛选，保证"导出即所见"。"""
    q = TeacherAchievement.query
    if f.get('teacher_uid'):
        q = q.filter_by(teacher_uid=f['teacher_uid'])
    if f.get('category') in _CATEGORY_KEYS:
        q = q.filter_by(category=f['category'])
    if f.get('status') in ACHIEVEMENT_STATUS:
        q = q.filter_by(status=f['status'])
    if (f.get('year') or '').isdigit():
        q = q.filter(func.strftime('%Y', TeacherAchievement.obtain_date) == f['year'])
    if f.get('tag'):
        # 标签以逗号分隔存储，这里用 LIKE 包住分隔符做整词匹配
        # （避免「数学」命中「数学竞赛辅导」这类长标签的部分匹配歧义）
        tag = f['tag']
        q = q.filter(db.or_(
            TeacherAchievement.tags == tag,
            TeacherAchievement.tags.like(f'{tag},%'),
            TeacherAchievement.tags.like(f'%,{tag}'),
            TeacherAchievement.tags.like(f'%,{tag},%'),
        ))
    return q


@bp.route('/achievements')
@login_required
@perm_required('academic.view')
def achievements_page():
    """教师业绩库：筛选 + 分页 + 统计概览。"""
    f = _filters()
    page = request.args.get('page', 1, type=int)
    pagination = (_achievement_query(f)
                  .order_by(TeacherAchievement.obtain_date.desc(),
                            TeacherAchievement.id.desc())
                  .paginate(page=page, per_page=30, error_out=False))
    items = pagination.items

    # 全量统计（不受筛选影响，用于看清整体结构）
    stats = {
        'total': TeacherAchievement.query.count(),
        'pending': TeacherAchievement.query.filter_by(status='pending').count(),
        'approved': TeacherAchievement.query.filter_by(status='approved').count(),
        'rejected': TeacherAchievement.query.filter_by(status='rejected').count(),
    }
    cat_map = dict(ACHIEVEMENT_CATEGORIES)
    cat_rows = (db.session.query(TeacherAchievement.category, func.count())
                .group_by(TeacherAchievement.category).all())
    cat_dist = sorted([{'label': cat_map.get(k, k or '未分类'), 'count': n}
                       for k, n in cat_rows], key=lambda x: -x['count'])
    level_rows = (db.session.query(TeacherAchievement.level, func.count())
                  .group_by(TeacherAchievement.level).all())
    level_dist = sorted([{'label': k or '未填级别', 'count': n}
                         for k, n in level_rows], key=lambda x: -x['count'])[:8]
    year_rows = db.session.query(
        func.strftime('%Y', TeacherAchievement.obtain_date)).distinct().all()
    years = sorted({r[0] for r in year_rows if r[0]}, reverse=True)

    teachers = Teacher.query.filter_by(status='active').order_by(
        Teacher.teacher_uid).all()
    return render_template('academic/achievements.html',
                           items=items, pagination=pagination, teachers=teachers,
                           categories=ACHIEVEMENT_CATEGORIES,
                           levels=ACHIEVEMENT_LEVELS,
                           status_map=ACHIEVEMENT_STATUS,
                           stats=stats, cat_dist=cat_dist, level_dist=level_dist,
                           years=years,
                           all_tags=ach_svc.all_tags(),
                           tag_presets=ach_svc.TAG_PRESETS,
                           att_counts=ach_svc.attachment_counts([a.id for a in items]),
                           tags_of=ach_svc.tags_of,
                           f_teacher=f['teacher_uid'], f_category=f['category'],
                           f_status=f['status'], f_year=f['year'], f_tag=f['tag'],
                           can_edit=current_user.has_perm('academic.edit'),
                           today=date.today().isoformat())


@bp.route('/achievements/export')
@login_required
@perm_required('academic.view')
def achievements_export():
    """导出教师业绩 Excel（遵循当前筛选条件）"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    from app.utils.export_helpers import xl_safe

    f = _filters()
    records = (_achievement_query(f)
               .order_by(TeacherAchievement.obtain_date.desc(),
                         TeacherAchievement.id.desc()).all())
    if not records:
        flash('当前筛选条件下没有可导出的业绩记录', 'warning')
        return redirect(url_for('academic.achievements_page'))

    cat_map = dict(ACHIEVEMENT_CATEGORIES)
    wb = Workbook()
    ws = wb.active
    ws.title = '教师业绩'
    headers = ['序号', '教师', '教师编号', '类别', '业绩名称', '级别',
               '取得时间', '颁发单位', '标签', '状态', '审核意见', '备注', '附件数']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        c.alignment = Alignment(horizontal='center', vertical='center')
    counts = ach_svc.attachment_counts([r.id for r in records])
    for i, r in enumerate(records, 1):
        values = [
            i, r.teacher_name or '', r.teacher_uid or '',
            cat_map.get(r.category, r.category or ''),
            r.title or '', r.level or '',
            r.obtain_date.strftime('%Y-%m-%d') if r.obtain_date else '',
            r.issuer or '',
            '、'.join(ach_svc.tags_of(r)),
            ACHIEVEMENT_STATUS.get(r.status, r.status or ''),
            r.review_note or '', r.note or '',
            counts.get(r.id, 0),
        ]
        for ci, v in enumerate(values, 1):
            cell = ws.cell(row=i + 1, column=ci,
                           value=xl_safe(v) if isinstance(v, str) else v)
            cell.alignment = Alignment(horizontal='center', vertical='center',
                                       wrap_text=(ci in (5, 9, 11, 12)))
    for ci, w in enumerate([6, 12, 14, 8, 30, 10, 12, 24, 18, 10, 24, 24, 8], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_operation(current_user, '导出', '教师业绩', None, f'{len(records)} 条',
                  module='academic')
    return send_file(buf, as_attachment=True,
                     download_name=f'教师业绩_{stamp}.xlsx',
                     mimetype=_XLSX_MIME)


def _parse_date(value):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return None


@bp.route('/achievements/add', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievements_add():
    """教务录入业绩（直接生效）"""
    back = redirect(url_for('academic.achievements_page'))
    teacher_uid = (request.form.get('teacher_uid') or '').strip()
    category = (request.form.get('category') or '').strip()
    title = (request.form.get('title') or '').strip()
    t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    if not t:
        flash('请选择教师', 'danger')
        return back
    if category not in _CATEGORY_KEYS:
        flash('请选择有效的业绩类别', 'danger')
        return back
    if not title:
        flash('请填写业绩名称', 'danger')
        return back

    rec = TeacherAchievement(
        teacher_uid=t.teacher_uid, teacher_name=t.name,
        category=category, title=title,
        level=(request.form.get('level') or '').strip() or None,
        obtain_date=_parse_date(request.form.get('obtain_date')),
        issuer=(request.form.get('issuer') or '').strip() or None,
        note=(request.form.get('note') or '').strip() or None,
        tags=(','.join(ach_svc.parse_tags(request.form.get('tags'))) or None),
        status='approved', submitted_by=current_user.id,
    )
    db.session.add(rec)
    db.session.commit()
    log_operation(current_user, '新增', '教师业绩', rec.id,
                  f'{t.name} {title}', module='academic')
    flash(f'已录入 {t.name} 的业绩：{title}', 'success')
    return back


@bp.route('/achievements/<int:aid>/review', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievements_review(aid):
    """审核教师工作台提交的业绩（支持填写审核意见；驳回必填）"""
    rec = db.session.get(TeacherAchievement, aid)
    if not rec:
        abort(404)
    action = (request.form.get('action') or '').strip()
    note = (request.form.get('review_note') or '').strip()
    if action not in ('approve', 'reject'):
        flash('无效的审核操作', 'danger')
        return redirect(url_for('academic.achievements_page'))
    if action == 'reject' and not note:
        flash('驳回时请填写审核意见，教师可在工作台看到原因', 'danger')
        return redirect(url_for('academic.achievements_page',
                                status='pending'))
    rec.status = 'approved' if action == 'approve' else 'rejected'
    rec.review_note = note[:200] or None
    rec.reviewed_by = current_user.id
    rec.reviewed_at = datetime.now()
    db.session.commit()
    log_operation(current_user, '审核', '教师业绩', rec.id,
                  f'{rec.teacher_name} {rec.title} → {ACHIEVEMENT_STATUS[rec.status]}'
                  + (f'（{note[:40]}）' if note else ''),
                  module='academic')
    flash(f'已{ACHIEVEMENT_STATUS[rec.status]}：{rec.title}', 'success')
    return redirect(url_for('academic.achievements_page',
                            status=(request.form.get('back_status') or None)))


@bp.route('/achievements/<int:aid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievements_delete(aid):
    rec = db.session.get(TeacherAchievement, aid)
    if not rec:
        abort(404)
    # 一并清掉附件（记录 + 落盘文件），否则会留下孤儿文件
    for att in ach_svc.list_attachments(rec):
        ach_svc.delete_attachment(att)
    db.session.delete(rec)
    db.session.commit()
    log_operation(current_user, '删除', '教师业绩', aid,
                  f'{rec.teacher_name} {rec.title}', module='academic')
    flash('业绩记录已删除', 'success')
    return redirect(url_for('academic.achievements_page'))


# ══════════════════════════════════════════════════════════════════════════════
# 标签与附件（2026-09-26）：分类分标签 / 详情查看 / 附件（图片、PDF、文档）
# 附件走带鉴权路由（/static/uploads 直链已全局封禁），图片与 PDF 可内联预览。
# ══════════════════════════════════════════════════════════════════════════════

def _ach_or_404(aid):
    rec = db.session.get(TeacherAchievement, aid)
    if not rec:
        abort(404)
    return rec


@bp.route('/achievements/<int:aid>/detail')
@login_required
@perm_required('academic.view')
def achievement_detail(aid):
    """业绩详情 JSON：完整字段 + 标签 + 附件清单（详情抽屉用，不刷新页面）"""
    rec = _ach_or_404(aid)
    data = ach_svc.detail_dict(rec)
    data['can_edit'] = current_user.has_perm('academic.edit')
    data['tag_presets'] = ach_svc.TAG_PRESETS
    return jsonify({'success': True, 'data': data})


@bp.route('/achievements/<int:aid>/tags', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievement_set_tags(aid):
    """保存标签（逗号/空格/顿号分隔，自动去重限长）"""
    rec = _ach_or_404(aid)
    raw = (request.form.get('tags') or (request.get_json(silent=True) or {}).get('tags')
           or '')
    tags = ach_svc.set_tags(rec, raw)
    log_operation(current_user, '更新', '教师业绩', rec.id,
                  f'{rec.teacher_name} {rec.title} 标签：{"、".join(tags) or "（清空）"}',
                  module='academic')
    return jsonify({'success': True, 'tags': tags, 'message': '标签已保存'})


@bp.route('/achievements/<int:aid>/attachment', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievement_attachment_upload(aid):
    """上传附件（支持一次多选；类型白名单 + magic 校验，单文件 ≤10MB）"""
    rec = _ach_or_404(aid)
    files = request.files.getlist('files') or request.files.getlist('file')
    files = [f for f in files if f and (f.filename or '').strip()]
    if not files:
        return jsonify({'success': False, 'message': '没有选择文件'}), 400

    uploaded, errors = 0, []
    for f in files:
        ok, msg, _att = ach_svc.save_attachment(rec, f, current_user)
        if ok:
            uploaded += 1
        else:
            errors.append(f'{f.filename}：{msg}')
    if uploaded:
        log_operation(current_user, '上传', '业绩附件', rec.id,
                      f'{rec.teacher_name} {rec.title} 上传 {uploaded} 个附件',
                      module='academic')
    return jsonify({
        'success': uploaded > 0, 'uploaded': uploaded, 'errors': errors,
        'message': (f'已上传 {uploaded} 个附件' if uploaded else '上传失败')
                   + (f'；{len(errors)} 个被拒绝' if errors else ''),
        'attachments': [dict(a.to_dict(), previewable=ach_svc.is_previewable(a.ext),
                             is_image=ach_svc.is_image(a.ext))
                        for a in ach_svc.list_attachments(rec)],
    })


@bp.route('/achievements/attachment/<int:fid>/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def achievement_attachment_delete(fid):
    """删除附件（记录 + 落盘文件）"""
    att = db.session.get(AchievementAttachment, fid)
    if not att:
        abort(404)
    name = att.file_name
    ach_svc.delete_attachment(att)
    log_operation(current_user, '删除', '业绩附件', fid, name or '', module='academic')
    return jsonify({'success': True, 'message': '附件已删除'})


@bp.route('/achievements/attachment/<int:fid>/view')
@login_required
# v1.18.7.0 审核（S2）：本路由有意不用 @perm_required('academic.view')——
# 教师工作台“我的业绩”需允许无 academic.view 权限的提交者本人预览自己上传的附件；
# 权限由 ach_svc.can_view(rec, att, current_user) 收敛（academic.view 持有者 OR 提交者本人 OR 审核人）。
def achievement_file_view(fid):
    """附件预览：图片/PDF 内联打开，其它类型强制下载（避免浏览器渲染意外内容）。

    权限：有 academic.view 的教务端，或该条业绩的提交者本人。
    """
    att = db.session.get(AchievementAttachment, fid)
    if not att:
        abort(404)
    rec = db.session.get(TeacherAchievement, att.achievement_id)
    if not ach_svc.can_view(rec, att, current_user):
        abort(403)
    root, stored = ach_svc.attachment_path(att)
    if not root:
        abort(404)
    if not ach_svc.is_previewable(att.ext):
        return send_from_directory(root, stored, as_attachment=True,
                                   download_name=att.file_name or stored)
    return send_from_directory(root, stored, as_attachment=False,
                               download_name=att.file_name or stored,
                               mimetype=att.mime or 'application/octet-stream')


@bp.route('/achievements/attachment/<int:fid>/download')
@login_required
# v1.18.7.0 审核（S2）：同 achievement_file_view，权限由 ach_svc.can_view 收敛，
# 豁免 @perm_required 是为保留“提交者本人下载自己附件”路径（与工作台协作）。
def achievement_file_download(fid):
    """附件下载（与预览同一套权限）"""
    att = db.session.get(AchievementAttachment, fid)
    if not att:
        abort(404)
    rec = db.session.get(TeacherAchievement, att.achievement_id)
    if not ach_svc.can_view(rec, att, current_user):
        abort(403)
    root, stored = ach_svc.attachment_path(att)
    if not root:
        abort(404)
    return send_from_directory(root, stored, as_attachment=True,
                               download_name=att.file_name or stored)

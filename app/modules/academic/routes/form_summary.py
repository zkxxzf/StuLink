# StuLink v1.18.0 2026-09-23
# 教务 · 表单收集「汇总侧」：汇总看板 / 汇总表格 / 未交名单 / 材料清单 / 材料包下载 / 催交
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""表单收集之后的能力路由（只读汇总 + 打包 + 催交）。

权限：沿用同模块管理端已有 key `academic.edit`（forms.py 中 form_submissions/
form_export 亦用此 key），管理员经 has_perm 自动放行；不新增 permission_map key。
"""
from flask import (render_template, request, abort, send_file, jsonify,
                   after_this_request)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.academic import FormTemplate
from app.modules.academic import bp
from app.modules.academic.services import form_summary_service as svc
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

import os

_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _json(success, message='', data=None):
    return jsonify({'success': success, 'message': message, 'data': data or {}})


def _filters(args=None):
    args = args or request.args
    return {
        'status': (args.get('status') or '').strip() or None,
        'grade': (args.get('grade') or '').strip() or None,
        'class_name': (args.get('class_name') or '').strip() or None,
        'keyword': (args.get('keyword') or '').strip() or None,
        'question_id': (args.get('question_id') or '').strip() or None,
        'uid': (args.get('uid') or '').strip() or None,
        'include_rejected': (args.get('include_rejected') or '') in ('1', 'true', 'on'),
        'approved_only': (args.get('approved_only') or '') in ('1', 'true', 'on'),
    }


def _tpl_or_404(form_id):
    tpl = db.session.get(FormTemplate, form_id)
    if not tpl:
        abort(404)
    return tpl


# ── 页面路由 ──────────────────────────────────────────────────

@bp.route('/forms/<int:form_id>/summary')
@login_required
@perm_required('academic.edit')
def form_summary_page(form_id):
    """汇总看板"""
    tpl = _tpl_or_404(form_id)
    stats = svc.get_submission_stats(form_id)
    scope_options = svc.get_scope_options(form_id)
    return render_template('academic/form_summary.html',
                           tpl=tpl, stats=stats, scope_options=scope_options,
                           rounds=svc.get_remind_rounds(form_id))


@bp.route('/forms/<int:form_id>/table')
@login_required
@perm_required('academic.edit')
def form_table_page(form_id):
    """汇总表格页（数据经 /api/.../table-data 异步渲染）"""
    tpl = _tpl_or_404(form_id)
    questions = svc._load_questions(form_id)
    scope_options = svc.get_scope_options(form_id)
    return render_template('academic/form_table.html',
                           tpl=tpl, questions=questions, scope_options=scope_options)


@bp.route('/forms/<int:form_id>/missing')
@login_required
@perm_required('academic.edit')
def form_missing_page(form_id):
    """未提交名单页"""
    tpl = _tpl_or_404(form_id)
    stats = svc.get_submission_stats(form_id)
    scope_options = svc.get_scope_options(form_id)
    return render_template('academic/form_missing.html',
                           tpl=tpl, stats=stats, scope_options=scope_options,
                           rounds=svc.get_remind_rounds(form_id))


@bp.route('/forms/<int:form_id>/files')
@login_required
@perm_required('academic.edit')
def form_files_page(form_id):
    """材料清单页"""
    tpl = _tpl_or_404(form_id)
    questions = [q for q in svc._load_questions(form_id) if q.question_type == 'file']
    scope_options = svc.get_scope_options(form_id)
    return render_template('academic/form_files.html',
                           tpl=tpl, questions=questions, scope_options=scope_options)


@bp.route('/forms/<int:form_id>/package')
@login_required
@perm_required('academic.edit')
def form_package_page(form_id):
    """材料包下载页"""
    tpl = _tpl_or_404(form_id)
    questions = [q for q in svc._load_questions(form_id) if q.question_type == 'file']
    scope_options = svc.get_scope_options(form_id)
    return render_template('academic/form_package.html',
                           tpl=tpl, questions=questions, scope_options=scope_options)


@bp.route('/forms/<int:form_id>/package/download')
@login_required
@perm_required('academic.edit')
def form_package_download(form_id):
    """执行打包并 send_file"""
    tpl = _tpl_or_404(form_id)
    f = _filters()
    structure = (request.args.get('structure') or 'class_student').strip()
    if structure not in ('class_student', 'question_class', 'flat'):
        structure = 'class_student'
    scope = (request.args.get('scope') or 'all').strip()
    try:
        buffer_or_path, dname, stats, is_disk = svc.build_material_package(
            form_id, scope=scope, grade=f['grade'], class_name=f['class_name'],
            question_id=f['question_id'], submitter_uid=f['uid'],
            structure=structure, approved_only=f['approved_only'])
        log_operation(current_user, '导出', '材料包', form_id,
                      f'打包下载：{tpl.title}（{stats["file_count"]}个文件）',
                      module='academic')
        if is_disk:
            @after_this_request
            def _cleanup(resp):
                try:
                    d = os.path.dirname(buffer_or_path)
                    if os.path.exists(buffer_or_path):
                        os.remove(buffer_or_path)
                    if os.path.isdir(d) and 'stulink_pkg_' in d:
                        os.rmdir(d)
                except Exception:
                    pass
                return resp
            return send_file(buffer_or_path, as_attachment=True,
                             download_name=dname, mimetype='application/zip')
        return send_file(buffer_or_path, as_attachment=True,
                         download_name=dname, mimetype='application/zip')
    except ValueError as e:
        return _json(False, str(e)), 400
    except Exception as e:
        db.session.rollback()
        return _json(False, f'打包失败：{e}'), 500


@bp.route('/forms/<int:form_id>/export/summary')
@login_required
@perm_required('academic.edit')
def form_export_summary(form_id):
    """导出多 Sheet 汇总 Excel"""
    tpl = _tpl_or_404(form_id)
    f = _filters()
    try:
        out, dname = svc.export_summary_excel(
            form_id, status=f['status'], grade=f['grade'], class_name=f['class_name'],
            keyword=f['keyword'], include_rejected=f['include_rejected'])
        log_operation(current_user, '导出', '表单汇总', form_id,
                      f'导出汇总 Excel：{tpl.title}', module='academic')
        return send_file(out, as_attachment=True, download_name=dname, mimetype=_XLSX_MIME)
    except ValueError as e:
        return _json(False, str(e)), 400
    except Exception as e:
        db.session.rollback()
        return _json(False, f'导出失败：{e}'), 500


@bp.route('/forms/<int:form_id>/export/missing')
@login_required
@perm_required('academic.edit')
def form_export_missing(form_id):
    """导出未提交名单 Excel"""
    tpl = _tpl_or_404(form_id)
    f = _filters()
    try:
        out, dname = svc.export_missing_list_excel(form_id, grade=f['grade'],
                                                   class_name=f['class_name'])
        log_operation(current_user, '导出', '未交名单', form_id,
                      f'导出未交名单：{tpl.title}', module='academic')
        return send_file(out, as_attachment=True, download_name=dname, mimetype=_XLSX_MIME)
    except ValueError as e:
        return _json(False, str(e)), 400
    except Exception as e:
        db.session.rollback()
        return _json(False, f'导出失败：{e}'), 500


@bp.route('/forms/<int:form_id>/remind', methods=['POST'])
@login_required
@perm_required('academic.edit')
def form_remind(form_id):
    """催交（body: uids[] 或 all_missing=true）"""
    tpl = _tpl_or_404(form_id)
    payload = request.get_json(silent=True) or request.form
    uids = payload.get('uids') or None
    all_missing = bool(payload.get('all_missing')) or (payload.get('all_missing') == 'true')
    grade = (payload.get('grade') or '').strip() or None
    class_name = (payload.get('class_name') or '').strip() or None
    try:
        success, message, data = svc.remind_submitters(
            form_id, uids=uids, all_missing=all_missing, operator=current_user,
            grade=grade, class_name=class_name)
        if success:
            log_operation(current_user, '催交', '表单', form_id,
                          f'催交：{tpl.title}（{data.get("notified",0)}人）',
                          module='academic')
        return _json(success, message, data)
    except ValueError as e:
        return _json(False, str(e)), 400
    except Exception as e:
        db.session.rollback()
        return _json(False, f'催交失败：{e}'), 500


@bp.route('/forms/<int:form_id>/submission/<int:sid>/files')
@login_required
@perm_required('academic.edit')
def form_submission_files(form_id, sid):
    """单个提交的材料清单（弹窗用，返回 JSON）"""
    _tpl_or_404(form_id)
    inv = svc.get_file_inventory(form_id, submitter_uid=None)
    files = [i for i in inv['items'] if i['submission_id'] == sid]
    for f in files:
        f.pop('stored_path', None)
    return _json(True, '', {'submission_id': sid, 'files': files, 'total': len(files)})


# ── JSON API 路由（图表/表格异步加载） ────────────────────────

@bp.route('/api/forms/<int:form_id>/stats')
@login_required
@perm_required('academic.edit')
def api_form_stats(form_id):
    _tpl_or_404(form_id)
    try:
        stats = svc.get_submission_stats(form_id)
        stats['trend'] = svc.get_submission_trend(form_id)
        return _json(True, '', stats)
    except Exception as e:
        return _json(False, str(e)), 500


@bp.route('/api/forms/<int:form_id>/class-breakdown')
@login_required
@perm_required('academic.edit')
def api_form_class_breakdown(form_id):
    _tpl_or_404(form_id)
    try:
        rows = svc.get_class_breakdown(form_id)
        totals = svc.class_breakdown_totals(rows)
        return _json(True, '', {'rows': rows, **totals})
    except Exception as e:
        return _json(False, str(e)), 500


@bp.route('/api/forms/<int:form_id>/question-stats')
@login_required
@perm_required('academic.edit')
def api_form_question_stats(form_id):
    _tpl_or_404(form_id)
    try:
        matrix = svc.build_summary_matrix(form_id, include_rejected=True)
        return _json(True, '', {'question_stats': matrix['question_stats']})
    except Exception as e:
        return _json(False, str(e)), 500


@bp.route('/api/forms/<int:form_id>/table-data')
@login_required
@perm_required('academic.edit')
def api_form_table_data(form_id):
    """汇总表格数据（内存分页）"""
    _tpl_or_404(form_id)
    f = _filters()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    try:
        matrix = svc.build_summary_matrix(
            form_id, status=f['status'], grade=f['grade'], class_name=f['class_name'],
            keyword=f['keyword'], include_rejected=f['include_rejected'])
        rows = matrix['rows']
        page_rows, pagination = svc._paginate(rows, page, per_page)
        # 重新编号
        for i, r in enumerate(page_rows, (page - 1) * per_page + 1):
            r['index'] = i
        return _json(True, '', {'columns': matrix['columns'], 'rows': page_rows,
                                'question_stats': matrix['question_stats'],
                                'pagination': pagination})
    except Exception as e:
        db.session.rollback()
        return _json(False, str(e)), 500


@bp.route('/api/forms/<int:form_id>/missing-data')
@login_required
@perm_required('academic.edit')
def api_form_missing_data(form_id):
    _tpl_or_404(form_id)
    f = _filters()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 200, type=int)
    try:
        items, pagination = svc.get_missing_submitters(
            form_id, grade=f['grade'], class_name=f['class_name'],
            page=page, per_page=per_page)
        # 按班级分组
        groups = {}
        for it in items:
            key = f"{it.get('grade','')}|{it.get('class_name','')}"
            groups.setdefault(key, {'grade': it.get('grade', ''),
                                    'class_name': it.get('class_name', ''),
                                    'members': []})
            groups[key]['members'].append(it)
        return _json(True, '', {'items': items, 'pagination': pagination,
                                'groups': list(groups.values())})
    except Exception as e:
        return _json(False, str(e)), 500


@bp.route('/api/forms/<int:form_id>/files-data')
@login_required
@perm_required('academic.edit')
def api_form_files_data(form_id):
    _tpl_or_404(form_id)
    f = _filters()
    only_missing = (request.args.get('only_missing') or '') in ('1', 'true', 'on')
    try:
        inv = svc.get_file_inventory(form_id, grade=f['grade'], class_name=f['class_name'],
                                     question_id=f['question_id'], submitter_uid=f['uid'],
                                     approved_only=f['approved_only'])
        items = inv['items']
        if only_missing:
            items = [i for i in items if not i['exists']]
        for i in items:
            i.pop('stored_path', None)
        return _json(True, '', {'items': items, 'total': inv['total'],
                                'total_size': inv['total_size'],
                                'total_size_text': inv['total_size_text'],
                                'missing_count': inv['missing_count']})
    except Exception as e:
        return _json(False, str(e)), 500


@bp.route('/api/forms/<int:form_id>/package-preview')
@login_required
@perm_required('academic.edit')
def api_form_package_preview(form_id):
    _tpl_or_404(form_id)
    f = _filters()
    structure = (request.args.get('structure') or 'class_student').strip()
    try:
        preview = svc.get_package_preview(form_id, grade=f['grade'], class_name=f['class_name'],
                                          question_id=f['question_id'], submitter_uid=f['uid'],
                                          structure=structure, approved_only=f['approved_only'])
        return _json(True, '', preview)
    except Exception as e:
        return _json(False, str(e)), 500

# StuLink v1.19.50.63 2026-10-10
# 教务 · 教师名单：列表 / Excel 导入（模板-预览-确认-密码清单）/ 编辑
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import re
import time
import uuid
from datetime import datetime

from flask import (render_template, request, redirect, url_for,
                   flash, send_file, abort)
from flask_login import login_required, current_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import User
from app.models.academic import Teacher
from app.modules.academic import bp
from app.modules.academic.services import teacher_import_service
from app.modules.academic.services import teacher_sync
from app.utils import id_card as id_card_util
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

_PHONE_RE = re.compile(r'^1\d{10}$')

# 导入草稿（内存级，服务重启后自动失效，与成绩模块教师导入同模式）
_DRAFT = {}
_DRAFT_TTL = 1800

# 2026-10-14：本页有**两处入口** —— 教务管理「教师」(/academic/teachers) 与
# 系统管理「教师管理 → 教师名单」(/users/teachers，见 app/modules/system/routes/users.py
# 的 add_url_rule)。同一份视图函数、同一份数据，端点按当前蓝图自适应：
# 页面内链接 / 表单 action / 重定向都留在用户所在模块（不跨模块跳，与
# app/modules/grades/routes/teachers.py 的双端模式一致）。
_USERS_ENDPOINTS = {
    'teachers_page': 'users.teachers_page',
    'teachers_export': 'users.teachers_export',
    'teachers_template': 'users.teachers_template',
    # 2026-10-21：教师导入只保留教务入口，users 蓝图下已不再挂载导入路由。
    'teacher_edit': 'users.teacher_edit',
}


def _ep(name):
    """端点名自适应：返回当前模块下该功能的端点全名（模板里以 url_for(ep('x')) 使用）。"""
    if request.blueprint == 'users':
        return _USERS_ENDPOINTS[name]
    return f'academic.{name}'


def _log_module():
    """操作日志的模块名按当前入口记（系统管理侧 system / 教务侧 academic），
    便于「操作日志」页按模块筛选时归属正确。"""
    return 'system' if request.blueprint == 'users' else 'academic'


def _purge_expired_drafts():
    now = time.time()
    for key in [k for k, v in _DRAFT.items() if now - v.get('_ts', 0) > _DRAFT_TTL]:
        _DRAFT.pop(key, None)


def _teacher_filters():
    """教师名单筛选条件（列表与导出共用，保证"导出即所见"）"""
    return {
        'kw': (request.args.get('kw') or '').strip(),
        'status': (request.args.get('status') or '').strip(),
        'subject': (request.args.get('subject') or '').strip(),
    }


def _teacher_query(filters):
    # 2026-10-20：离职/退休已归档教师只允许在「系统管理 → 教师管理」查阅，
    # 教务名单/导出默认排除（archive_kind 为空才是未归档）。
    q = Teacher.query.filter(Teacher.archive_kind.is_(None))
    kw = filters.get('kw')
    if kw:
        like = f'%{kw}%'
        conds = [Teacher.name.like(like), Teacher.teacher_uid.like(like),
                 Teacher.subject.like(like)]
        # 2026-10-19：手机号已加密，不能再 LIKE。两条路补回来：
        #   ① 输入完整号码 → 盲索引精确匹配（覆盖 138-0000-0000 这类格式差异）
        #   ② 输入片段     → 教师是百人级，解密后内存过滤出 id 再并入
        if any(ch.isdigit() for ch in kw):
            _idx = Teacher.phone_index(kw)
            if _idx:
                conds.append(Teacher.phone_idx == _idx)
            extra = [t.id for t in Teacher.query.all() if kw in (t.phone or '')]
            if extra:
                conds.append(Teacher.id.in_(extra))
        q = q.filter(or_(*conds))
    if filters.get('status') in ('active', 'left'):
        q = q.filter_by(status=filters['status'])
    if filters.get('subject'):
        q = q.filter_by(subject=filters['subject'])
    return q


def _subject_options():
    rows = (db.session.query(Teacher.subject)
            .filter(Teacher.archive_kind.is_(None)).distinct().all())
    return sorted({r[0] for r in rows if r[0]})


@bp.route('/teachers')
@login_required
@perm_required('academic.view')
def teachers_page():
    """教师名单（教务基础数据）：关键词 + 状态 + 学科筛选，分页展示。"""
    from app.models.grades import TeacherSubjectLink

    filters = _teacher_filters()
    page = request.args.get('page', 1, type=int)
    pagination = (_teacher_query(filters).order_by(Teacher.teacher_uid)
                  .paginate(page=page, per_page=30, error_out=False))
    teachers = pagination.items

    user_ids = [t.user_id for t in teachers if t.user_id]
    users = ({u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()}
             if user_ids else {})
    counts = {}
    if user_ids:
        counts = dict(TeacherSubjectLink.query.with_entities(
            TeacherSubjectLink.user_id, func.count())
            .filter(TeacherSubjectLink.user_id.in_(user_ids),
                    TeacherSubjectLink.active.is_(True))
            .group_by(TeacherSubjectLink.user_id).all())

    rows = []
    for t in teachers:
        rows.append({
            't': t,
            'account': users.get(t.user_id) if t.user_id else None,
            'lesson_count': counts.get(t.user_id, 0),
            'id_masked': id_card_util.masked_from_cipher(t.id_card_enc),
        })
    return render_template('academic/teachers.html', rows=rows, pagination=pagination,
                           subjects=_subject_options(), ep=_ep, **filters)


@bp.route('/teachers/export')
@login_required
@perm_required('academic.view')
def teachers_export():
    """导出教师名单 Excel（遵循当前筛选条件：关键词/状态/学科）"""
    from datetime import datetime

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    from app.utils.export_helpers import xl_safe
    from app.utils.sensitive import display as _sensitive_display

    filters = _teacher_filters()
    teachers = _teacher_query(filters).order_by(Teacher.teacher_uid).all()
    user_ids = [t.user_id for t in teachers if t.user_id]
    users = ({u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()}
             if user_ids else {})

    wb = Workbook()
    ws = wb.active
    ws.title = '教师名单'
    headers = ['序号', '教师编号', '姓名', '学科', '手机号', '身份证号(掩码)',
               '登录账号', '状态', '备注']
    # 2026-10-13 字段级脱敏：无 academic.view_teacher_phone 时导出脱敏手机号
    _phone_plain = current_user.has_perm('academic.view_teacher_phone')
    headers[4] = '手机号' if _phone_plain else '手机号(掩码)'
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
        c.alignment = Alignment(horizontal='center', vertical='center')
    for i, t in enumerate(teachers, 1):
        account = users.get(t.user_id) if t.user_id else None
        values = [
            i, t.teacher_uid, t.name, t.subject or '',
            (t.phone or '') if _phone_plain else _sensitive_display(t.phone, 'teacher_phone'),
            id_card_util.masked_from_cipher(t.id_card_enc) or '',
            account.username if account else '',
            '在职' if t.status == 'active' else '离职', t.note or '',
        ]
        for ci, v in enumerate(values, 1):
            cell = ws.cell(row=i + 1, column=ci,
                           value=xl_safe(v) if isinstance(v, str) else v)
            cell.alignment = Alignment(horizontal='center', vertical='center',
                                       wrap_text=(ci == 9))
    for ci, w in enumerate([6, 14, 12, 10, 14, 22, 14, 8, 30], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_operation(current_user, '导出', '教师名单', None, f'{len(teachers)} 条',
                  module=_log_module())
    return send_file(buf, as_attachment=True,
                     download_name=f'教师名单_{stamp}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument'
                              '.spreadsheetml.sheet')


@bp.route('/teachers/template.xlsx')
@login_required
@perm_required('academic.view')
def teachers_template():
    """下载教师名单导入模板"""
    buf = teacher_import_service.build_template()
    return send_file(buf, as_attachment=True,
                     download_name='教师名单导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument'
                              '.spreadsheetml.sheet')


@bp.route('/teachers/import/upload', methods=['POST'])
@login_required
@perm_required('academic.edit')
def teachers_import_upload():
    """上传教师名单 → 解析并生成导入预览"""
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择教师名单 Excel 文件', 'danger')
        return redirect(url_for(_ep('teachers_page')))
    # 2026-10-13 全面审计 M-6：统一上传校验（白名单 + 危险类型 + magic 嗅探）
    from app.utils.upload_guard import validate_upload
    ok, msg = validate_upload(file.filename, allowed_exts=['xlsx', 'xls'],
                              stream=file.stream)
    if not ok:
        flash(f'{msg}（当前文件：{file.filename}）', 'danger')
        return redirect(url_for(_ep('teachers_page')))
    try:
        rows = teacher_import_service.parse_excel(io.BytesIO(file.read()))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for(_ep('teachers_page')))
    except Exception:
        flash('文件解析失败：请使用下载的标准模板填写后重试', 'danger')
        return redirect(url_for(_ep('teachers_page')))

    plan = teacher_import_service.build_plan(rows)
    token = uuid.uuid4().hex
    _purge_expired_drafts()
    _DRAFT[token] = {'plan': plan, 'fname': file.filename, '_ts': time.time()}
    return render_template('academic/teacher_import_preview.html',
                           token=token, plan=plan, fname=file.filename, ep=_ep)


@bp.route('/teachers/import/confirm', methods=['POST'])
@login_required
@perm_required('academic.edit')
def teachers_import_confirm():
    """确认导入：落库 + 自动建号"""
    token = (request.form.get('token') or '').strip()
    _purge_expired_drafts()
    draft = _DRAFT.get(token)
    if not draft:
        flash('导入批次已失效，请重新上传文件', 'danger')
        return redirect(url_for(_ep('teachers_page')))

    plan = draft['plan']
    overrides = {}
    for it in plan['items']:
        if (it.get('account') or {}).get('status') == 'to_create':
            val = (request.form.get(f'un_{it["idx"]}') or '').strip()
            if val:
                overrides[str(it['idx'])] = val
    try:
        result = teacher_import_service.apply_plan(plan['items'], overrides)
    except IntegrityError:
        db.session.rollback()
        flash('导入失败：身份证号或用户名与现有数据冲突，请检查后重新导入', 'danger')
        return redirect(url_for(_ep('teachers_page')))
    except Exception as e:
        db.session.rollback()
        flash(f'导入失败：{e}', 'danger')
        return redirect(url_for(_ep('teachers_page')))

    draft['result'] = result
    draft['_ts'] = time.time()
    log_operation(current_user, '导入', '教师名单', None,
                  f'新增 {result["created_teachers"]}、更新 {result["updated_teachers"]}、'
                  f'新建账号 {len(result["accounts"])}', module=_log_module())
    return render_template('academic/teacher_import_result.html',
                           token=token, result=result, plan=plan, ep=_ep)


@bp.route('/teachers/import/passwords/<token>.csv')
@login_required
@perm_required('academic.edit')
def teachers_import_passwords(token):
    """下载本次导入的初始账号密码清单"""
    _purge_expired_drafts()
    draft = _DRAFT.get(token)
    if not draft or not draft.get('result'):
        flash('密码清单已失效，请在教师管理页重置相应账号密码', 'warning')
        return redirect(url_for(_ep('teachers_page')))
    data = teacher_import_service.accounts_csv(draft['result']['accounts'])
    # 2026-10-18 审计修复（高危）：下载**明文初始口令**此前没有任何审计留痕 ——
    # 谁在什么时候导走了哪些账号的口令，事后完全无法追溯；且这是 GET，
    # after_request 的兜底网只拦写方法，正好覆盖不到。
    log_operation(current_user, '导出', '教师初始密码', None,
                  f'下载明文口令清单（{len(draft["result"]["accounts"])} 个账号）',
                  module=_log_module(), severity='WARNING')
    return send_file(io.BytesIO(data), as_attachment=True,
                     download_name='教师初始账号密码.csv', mimetype='text/csv')


@bp.route('/teachers/<int:tid>/edit', methods=['GET', 'POST'])
@login_required
@perm_required('academic.edit')
def teacher_edit(tid):
    """编辑教师：身份证一经录入仅管理员可更新（本页即管理入口）"""
    t = db.session.get(Teacher, tid)
    if not t:
        abort(404)

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        # 2026-10-18 审计修复（P1）：无 academic.view_teacher_phone 时模板不再回传号码，
        # 这里也不能把空值写回去（会把手机号清空）；直接用库中原值，禁止改号。
        if current_user.has_perm('academic.view_teacher_phone'):
            phone = (request.form.get('phone') or '').strip()
        else:
            phone = (t.phone or '').strip()
        note = (request.form.get('note') or '').strip()
        status = request.form.get('status') or 'active'
        id_card_raw = id_card_util.normalize(request.form.get('id_card'))

        back = redirect(url_for(_ep('teacher_edit'), tid=tid))
        if not name:
            flash('姓名不能为空', 'danger')
            return back
        if phone and not _PHONE_RE.match(phone):
            flash('手机号格式不正确（11 位，1 开头）', 'danger')
            return back
        if id_card_raw:
            if not id_card_util.validate(id_card_raw):
                flash('身份证号无效（需为 18 位有效号码，含校验码）', 'danger')
                return back
            # H-06：身份证改盲索引比对；密文随机 IV 不再可比较。
            new_idx = id_card_util.id_card_index(id_card_raw)
            old_idx = t.id_card_idx or ''
            if new_idx != old_idx:
                if t.id_card_enc:
                    flash('身份证号已录入，如需更正请确认无误后重试', 'warning')
                exist = Teacher.query.filter(
                    Teacher.id_card_filter(id_card_raw),
                    Teacher.id != t.id).first()
                if exist:
                    flash(f'该身份证号已属于教师「{exist.name}」，无法重复使用', 'danger')
                    return back
                t.set_id_card(id_card_raw)

        changes = []
        if name != t.name:
            changes.append(f'姓名 {t.name}→{name}')
            t.name = name
        if phone != (t.phone or ''):
            changes.append(f'手机号 {(t.phone or "空")}→{(phone or "空")}')
            t.phone = phone or None
        if subject != (t.subject or ''):
            t.subject = subject or None
        if note != (t.note or ''):
            t.note = note or None
        if status in ('active', 'left') and status != t.status:
            t.status = status
            changes.append('状态变更')
        # 2026-10-20：教育事业统计属性（教基4149/4153/4156）—— 首存才参与统计，
        # 清空即回到「未填」，不做默认值（避免把"没填"当成"无/否"报进基表）。
        for label, field, allowed in (
                ('性别', 'gender', ('男', '女')),
                ('人员类别', 'staff_type',
                 ('专任教师', '行政人员', '教辅人员', '工勤人员', '其他')),
                ('学历', 'education',
                 ('博士研究生', '硕士研究生', '本科', '专科', '高中阶段', '高中阶段以下')),
                ('职称', 'title',
                 ('正高级', '副高级', '中级', '助理级', '员级', '未定职级'))):
            val = (request.form.get(field) or '').strip()
            if val and val not in allowed:
                continue                                # 非法值忽略（防手改表单）
            if val != (getattr(t, field) or ''):
                setattr(t, field, val or None)
                changes.append(label)
        eth = (request.form.get('ethnicity') or '').strip()[:20]
        if eth != (t.ethnicity or ''):
            t.ethnicity = eth or None
            changes.append('民族')
        # 2026-10-20 用户口径（隐私）：教育统计只需年龄 → 出生日期为唯一必填项，
        # 身份证选填。清空即回到「未填」。
        bd_raw = (request.form.get('birth_date') or '').strip()
        bd_val = None
        if bd_raw:
            try:
                bd_val = datetime.strptime(bd_raw, '%Y-%m-%d').date()
            except ValueError:
                bd_val = None
        if bd_val != t.birth_date:
            t.birth_date = bd_val
            changes.append('出生日期')
        for label, field in (('在编', 'is_official'), ('外籍', 'is_foreign')):
            raw = (request.form.get(field) or '').strip()
            val = True if raw == '1' else (False if raw == '0' else None)
            if val is not getattr(t, field):
                setattr(t, field, val)
                changes.append(label)
        # v1.19.50.63：教师档案变更 → 同步回写到关联的 users 账号（admin 自动 skip）
        back_changes = teacher_sync.sync_from_teacher(t, renamed_from=None)
        if back_changes:
            changes.extend(back_changes)
        db.session.commit()
        log_operation(current_user, '更新', '教师', t.id,
                      f'{t.name}：' + ('；'.join(changes) or '无字段变化'),
                      module=_log_module())
        flash(f'教师 {t.name} 已更新', 'success')
        return redirect(url_for(_ep('teachers_page')))

    return render_template('academic/teacher_edit.html', t=t, ep=_ep,
                           id_masked=id_card_util.masked_from_cipher(t.id_card_enc))

# StuLink v1.19.49.45 2026-10-10
# 任课教师映射：矩阵维护（按年级）+ 教师安排表批量导入 + 自动开户
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import json
import time
import uuid
from datetime import date

import openpyxl
# 2026-10-10：批量按 (grade, class_name) 组合查询需要 tuple_ 构造复合列比对
from sqlalchemy import tuple_
from flask import render_template, request, jsonify, abort, redirect, url_for, flash, \
    send_file
from flask_login import login_required, current_user
from app.extensions import db
from app.models import User, Student, PermissionGroup, UserClassLink
from app.models.grades import (COMPREHENSIVE_SUBJECTS, LINK_SUBJECTS, SUBJECTS,
                               TeacherLinkPlanRow, TeacherSubjectLink)
from app.modules.grades import bp
from app.modules.grades.services import teacher_import, user_account
from app.modules.grades.services import link_plan_service as link_plan
from app.modules.grades.services.exam_guard import assert_grade_visible
from app.modules.grades.services.scope import visible_grades, _all_active_grades
from app.modules.grades.utils import numeric_classes
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 教师导入预览暂存（进程内，token 关联；服务重启后需重传）
_DRAFT = {}
# 修复：增加草稿过期回收——原先进程内暂存无上限增长且永不过期
_DRAFT_TTL = 2 * 3600  # 草稿保留时长（秒），超时未确认自动失效


def _purge_expired_drafts():
    """清理过期的教师导入草稿，防止 _DRAFT 无上限增长"""
    now = time.time()
    expired = [t for t, d in _DRAFT.items() if now - d.get('_ts', 0) > _DRAFT_TTL]
    for t in expired:
        _DRAFT.pop(t, None)


def _grade_classes(grade):
    """该年级的数字教学班（01班~10班…；过滤 不分班/已转出 等）"""
    rows = Student.query.filter_by(grade=grade).with_entities(Student.class_name).distinct().all()
    return numeric_classes([r[0] for r in rows])


def _teacher_roles_filter():
    """v1.19.49.45：教师候选的合法角色集合（明确排除 admin）"""
    return ('teacher', 'homeroom_teacher', 'grade_leader',
            'dorm_manager', 'school_viewer', 'staff')


def _teacher_candidates():
    """v1.19.49.45：所有启用且属于教师类角色的账号 id 集合"""
    rows = db.session.query(User.id).filter(
        User.role.in_(_teacher_roles_filter()),
        User.is_active.is_(True)
    ).all()
    return {r[0] for r in rows}


def _matrix_users(grade):
    """下拉候选教师：启用 + 教师类角色（明确排除 admin）。

    v1.19.49.45：历史逻辑“已绑定 user_id 即可入选”会导致 admin 错配后一直显示在下拉里，
    现改为仅按角色过滤；已绑定但非教师类的行依旧可在矩阵中显示历史名字（由 _build_matrix 侧），
    但下拉中不再提供 admin 作为新选择。
    v1.19.49.45：候选**按老师名字首字母排序**（拼音 A→Z；无姓名时用账号拼音兜底）。"""
    valid = _teacher_candidates()
    if not valid:
        return []
    rows = User.query.filter(User.id.in_(valid)).all()
    out = [{'id': u.id, 'name': u.real_name, 'username': u.username} for u in rows]
    out.sort(key=lambda x: _name_sort_key(x['name'], x['username']))
    return out


def _name_sort_key(name, username):
    """姓名 → 拼音排序键：(首字母, 全拼, 姓名)。

    v1.19.49.45：用 pypinyin 把「付冰洋」排到 f、把「何楠」排到 h；
    没有姓名（real_name 为空）的账号退化为用账号（pinyin）排序，仍保证稳定有序。"""
    py = user_account.pinyin_of(name) if name else None
    py = (py or '').lower() or (username or '').lower() or (name or '')
    return (py[:1], py, name or '')


# 2026-10-10：教务管理侧复用同一批视图函数（见 app/modules/academic/routes/teacher_links.py），
# 端点按当前蓝图自适应 —— 成绩管理里用 grades.*，教务管理里用 academic.teacher_links_*，
# 这样页面内链接、表单与跳转都留在用户所在的模块，源数据仍是同一张 TeacherSubjectLink。
_ACADEMIC_ENDPOINTS = {
    'index': 'academic.academic_home',          # 返回按钮：教务里回教务工作台
    'teachers_page': 'academic.teacher_links_page',
    'teachers_get': 'academic.teacher_links_api',
    'teachers_save': 'academic.teacher_links_save_api',
    'teachers_import_page': 'academic.teacher_links_import_page',
    'teachers_import_upload': 'academic.teacher_links_import_upload',
    'teachers_import_confirm': 'academic.teacher_links_import_confirm',
    'teacher_template_download': 'academic.teacher_links_template',
    # 方案（历史记录）2026-10-14
    'teachers_plans': 'academic.teacher_links_plans',
    'teachers_plans_create': 'academic.teacher_links_plans_create',
    'teachers_plans_use': 'academic.teacher_links_plans_use',
    'teachers_plans_rename': 'academic.teacher_links_plans_rename',
    'teachers_plans_delete': 'academic.teacher_links_plans_delete',
}


def _ep(name):
    """端点名自适应：返回当前模块下该功能的端点全名（模板里以 url_for(ep('x')) 使用）。"""
    if request.blueprint == 'academic':
        return _ACADEMIC_ENDPOINTS[name]
    return f'grades.{name}'


# ==================== 矩阵维护 ====================

@bp.route('/teachers')
@login_required
@perm_required('grades.teachers')
def teachers_page():
    # 2026-10-20 第十七轮审计：年级下拉按数据范围收敛（原 _all_active_grades
    # 会向年级长展示全部年级，且可借 URL 参数直接看外年级映射）。
    grades = visible_grades(current_user)
    grade = request.args.get('grade', '')
    if grade not in grades:
        grade = grades[0] if grades else ''
    # 2026-10-14 方案：页面顶部显示「当前方案」，右上角「历史记录」管理全部方案。
    # ensure 幂等：存量库（迁移已建「初始方案」）直接返回；空库首次进入自动建。
    plan = link_plan.ensure_current_plan(operator_id=current_user.id)
    return render_template('grades/teachers.html', grade_options=grades, grade=grade,
                           ep=_ep, plan=plan,
                           plan_count=link_plan.row_count(plan.id) if plan else 0)


@bp.route('/api/teachers')
@login_required
@perm_required('grades.teachers')
def teachers_get():
    grade = request.args.get('grade', '').strip()
    active = visible_grades(current_user)
    if grade not in active:
        # 防御：年级缺失或参数异常时回退到第一个可见年级（前端下拉固定有值）
        grade = active[0] if active else ''
    if not grade:
        return jsonify(success=False, message='年级无效'), 400
    # 2026-10-20 第十七轮审计：读也要过范围（否则年级长可借 ?grade= 看外年级映射）
    try:
        assert_grade_visible(grade)
    except PermissionError:
        return jsonify(success=False, message='该年级不在你的数据范围内'), 403
    links = TeacherSubjectLink.query.filter_by(grade=grade, active=True).all()
    # 单元格带姓名：候选下拉只列「启用 + 教师类角色」，历史方案里已停用/已删账号
    # 的绑定仍要能显示出来（否则前端会误显示为「未指定」）
    names = link_plan.user_names({l.user_id for l in links})
    cells = {}
    for l in links:
        cells[f'{l.class_name}|{l.subject}'] = {'user_id': l.user_id,
                                               'user_name': names.get(l.user_id, '')}
    return jsonify(success=True, data={
        'grade': grade,
        'classes': _grade_classes(grade),
        # 2026-10-15 用户口径「教师映射扩容，增加综合组」：科目列 = 成绩科目 9 科 +
        # 综合组 6 科（体育/音乐/美术/信息技术/通用技术/心理，各占一列）
        'subjects': LINK_SUBJECTS,
        # 页面按 文化科目 / 综合组 两段渲染（与学科组长页同形）
        'subject_groups': [
            {'name': '', 'subjects': list(SUBJECTS)},
            {'name': '综合组', 'subjects': list(COMPREHENSIVE_SUBJECTS)},
        ],
        'cells': cells,
        'users': _matrix_users(grade),
    })


def _json_body():
    """请求体 → dict（v1.19.49.45 兼容两种提交方式）。

    背景：前端历史上用 `$.post(url, JSON.stringify({...}))`，jQuery 会以
    `application/x-www-form-urlencoded` 发送**JSON 文本**，而 Flask 的
    `request.get_json(silent=True)` 只认 `application/json` → 取到 None →
    `cells` 为空 → 保存**静默失效**（页面还提示"已保存"）。
    这里先按标准 JSON 解析，失败再兜底解析原始请求体里的 JSON 文本。
    """
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    raw = (request.get_data(as_text=True) or '').strip()
    if not raw.startswith('{'):
        # 2026-10-14：CSRF 校验会先把 body 当表单解析，此时 get_data() 拿不到原文，
        # JSON 文本会原样成为表单字段名 —— 从 request.form 的键里捞回来，
        # 否则这条兜底会「返回成功但一格都没保存」（静默失效）。
        raw = next((k.strip() for k in request.form if k.strip().startswith('{')), '')
    if raw.startswith('{'):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


@bp.route('/api/teachers/save', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_save():
    data = _json_body()
    grade = (data.get('grade') or '').strip()
    cells = data.get('cells') or {}    # {'01班|语文': user_id 或 ''}
    active = visible_grades(current_user)
    if grade not in active:
        # 防御：年级缺失或参数异常时回退到第一个可见年级（前端下拉固定有值）
        grade = active[0] if active else ''
    if not grade:
        return jsonify(success=False, message='年级无效'), 400
    # 2026-10-20 第十七轮审计：写任课映射必须过年级范围（与小程序同一口径）；
    # 原实现只认权限点，年级长可写外年级映射 → 反过来提升自己的成绩可见范围。
    try:
        assert_grade_visible(grade)
    except PermissionError:
        return jsonify(success=False, message='该年级不在你的数据范围内'), 403
    # 校验 user_id 存在
    user_ids = {int(v) for v in cells.values() if v}
    if user_ids:
        valid = {u.id for u in User.query.filter(User.id.in_(user_ids)).all()}
        if not user_ids <= valid:
            return jsonify(success=False, message='存在无效的教师账号'), 400
    changes = {'bound': 0, 'released': 0, 'changed': 0}
    # 2026-10-10 架构审计修复：原实现每格（班级×学科，可达上百）都查一次
    # TeacherSubjectLink（N+1）。改为**先按年级一次性取出**该年级全部映射建索引，
    # 循环内只做内存查找；新增的 link 也同步写入索引，保证同一格不会重复 insert。
    # 判定与计数逻辑完全不变。
    _link_index = {}
    for _l in TeacherSubjectLink.query.filter_by(grade=grade).all():
        _link_index[(_l.class_name, _l.subject)] = _l

    for cls_sub, user_id in cells.items():
        try:
            class_name, subject = cls_sub.split('|', 1)
        except ValueError:
            continue
        # 2026-10-15：白名单放宽到 LINK_SUBJECTS（含综合组 6 科），否则「体育」等键被静默丢弃
        if subject not in LINK_SUBJECTS or class_name not in _grade_classes(grade):
            continue
        link = _link_index.get((class_name, subject))
        if user_id:
            if link is None:
                new_link = TeacherSubjectLink(grade=grade, class_name=class_name,
                                              subject=subject, user_id=int(user_id),
                                              active=True, created_by=current_user.id)
                db.session.add(new_link)
                _link_index[(class_name, subject)] = new_link
                changes['bound'] += 1
            elif not link.active or link.user_id != int(user_id):
                link.user_id = int(user_id)
                link.active = True
                changes['changed'] += 1
        else:
            if link and link.active:
                link.active = False
                changes['released'] += 1
    db.session.commit()
    # 第七轮审计（P-M8）：教师映射变更 → 失效教师相关分析缓存（否则最长 900s 旧教师）
    from app.modules.grades.utils import invalidate_teacher_cache
    invalidate_teacher_cache()
    # 2026-10-14 方案：主表变更后把「当前方案」的快照同步刷新（快照不落后于主表）
    plan = link_plan.sync_current(operator_id=current_user.id)
    log_operation(current_user, '维护', '教师映射', None,
                  f'{grade} 矩阵保存：{json.dumps(changes, ensure_ascii=False)}'
                  + (f'；当前方案「{plan.name}」' if plan else ''),
                  module='grades')
    return jsonify(success=True, message='已保存', changes=changes,
                   plan=(plan.to_dict() if plan else None))


# ==================== 方案（历史记录，2026-10-14） ====================
# 用户口径：每学期任课都会变，要有记录、可新增方案、可切回历史方案。
# 方案 = 整套任课映射的留档；「使用」＝把该方案物化回当前生效映射（全站即时生效）。

@bp.route('/teachers/plans')
@login_required
@perm_required('grades.teachers')
def teachers_plans():
    """方案清单（「历史记录」弹窗的数据源）。"""
    return jsonify(success=True, data={'items': link_plan.list_items()})


@bp.route('/teachers/plans/create', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_plans_create():
    """新建方案（可沿用当前映射 / 空白 / 复制某历史方案，可选立即使用）。"""
    data = _json_body()
    try:
        plan = link_plan.create_plan(
            name=data.get('name') or '',
            school_year=data.get('school_year') or '',
            term=data.get('term') or '',
            note=data.get('note') or '',
            copy_from=data.get('copy_from') or 'current',
            use_now=bool(data.get('use_now', True)),
            operator_id=current_user.id)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    log_operation(current_user, '新建', '任课映射方案', None,
                  f'方案「{plan.name}」（{plan.school_year or "未标学年"} '
                  f'{plan.term or "整学年"}，'
                  f'{"已切换为当前" if plan.is_current else "未启用"}）',
                  module='grades')
    return jsonify(success=True, data={'id': plan.id, 'is_current': plan.is_current},
                   message=f'已新建方案「{plan.name}」'
                           + ('，并切换为当前任课映射' if plan.is_current else ''))


@bp.route('/teachers/plans/use', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_plans_use():
    """使用某方案：切换为当前生效任课映射（物化到 teacher_subject_links）。"""
    data = _json_body()
    # 2026-10-20 第十七轮审计：方案物化=整表写任课映射，需限定在可见年级内，
    # 否则年级长可用任意 plan_id 把外年级映射一次性改成别的快照。
    try:
        _pid = int(data.get('plan_id') or 0)
    except (TypeError, ValueError):
        return jsonify(success=False, message='参数不正确'), 400
    _plan_grades = [r[0] for r in (
        TeacherLinkPlanRow.query.filter_by(plan_id=_pid)
        .with_entities(TeacherLinkPlanRow.grade).distinct().all())]
    for _g in _plan_grades:
        try:
            assert_grade_visible(_g)
        except PermissionError:
            return jsonify(success=False,
                           message=f'方案覆盖 {_g}，不在你的数据范围内'), 403
    try:
        plan, stats = link_plan.use_plan(data.get('plan_id'),
                                         operator_id=current_user.id)
    except (ValueError, TypeError) as e:
        return jsonify(success=False, message=str(e) or '参数不正确'), 400
    # 第七轮审计（P-M8）：方案「使用」= 物化回主表 → 教师列全变，必须失效
    from app.modules.grades.utils import invalidate_teacher_cache
    invalidate_teacher_cache()
    log_operation(current_user, '使用', '任课映射方案', None,
                  f'切换为方案「{plan.name}」'
                  + (f'：{json.dumps(stats, ensure_ascii=False)}' if stats else '（本就当前）'),
                  module='grades')
    msg = f'已切换为「{plan.name}」'
    if stats:
        msg += (f'：新增 {stats["added"]} / 恢复 {stats["restored"]} / '
                f'改派 {stats["changed"]} / 失效 {stats["released"]} 格')
    return jsonify(success=True, message=msg, data={'id': plan.id, 'stats': stats})


@bp.route('/teachers/plans/rename', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_plans_rename():
    """重命名方案 / 改说明。"""
    data = _json_body()
    try:
        plan = link_plan.rename_plan(data.get('plan_id'), name=data.get('name') or '',
                                     note=data.get('note'),
                                     operator_id=current_user.id)
    except (ValueError, TypeError) as e:
        return jsonify(success=False, message=str(e) or '参数不正确'), 400
    log_operation(current_user, '重命名', '任课映射方案', None, f'方案改为「{plan.name}」',
                  module='grades')
    return jsonify(success=True, message='已保存', data=plan.to_dict())


@bp.route('/teachers/plans/delete', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_plans_delete():
    """删除方案（当前生效方案不可删）。"""
    data = _json_body()
    try:
        plan = link_plan.delete_plan(data.get('plan_id'), operator_id=current_user.id)
    except (ValueError, TypeError) as e:
        return jsonify(success=False, message=str(e) or '参数不正确'), 400
    log_operation(current_user, '删除', '任课映射方案', None,
                  f'方案「{plan.name}」（{plan.school_year or "未标学年"} '
                  f'{plan.term or "整学年"}）', module='grades')
    return jsonify(success=True, message=f'已删除方案「{plan.name}」')


# ==================== 批量导入（教师安排表） ====================

@bp.route('/teachers/import')
@login_required
@perm_required('grades.teachers')
def teachers_import_page():
    return render_template('grades/teacher_import.html', ep=_ep)


@bp.route('/teachers/import/upload', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_import_upload():
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择教师安排表 Excel 文件', 'danger')
        return redirect(url_for(_ep('teachers_import_page')))
    # 2026-10-13 全面审计 M-6：统一上传校验（白名单 + 危险类型 + magic 嗅探）
    from app.utils.upload_guard import validate_upload
    ok, msg = validate_upload(file.filename, allowed_exts=['xlsx', 'xls'],
                              stream=file.stream)
    if not ok:
        flash(f'{msg}（当前文件：{file.filename}）', 'danger')
        return redirect(url_for(_ep('teachers_import_page')))
    try:
        stream = io.BytesIO(file.read())
        parsed = teacher_import.parse_teacher_excel(stream)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for(_ep('teachers_import_page')))
    except Exception:
        flash('文件解析失败：请使用与《教师安排表》同构的格式（行=班级、科目为列）', 'danger')
        return redirect(url_for(_ep('teachers_import_page')))

    # 账号核对（任课教师 + 班主任 两个来源的姓名并集）
    head_items = parsed.get('homerooms') or []
    names = {l['teacher_name'] for l in parsed['links']} | \
            {h['teacher_name'] for h in head_items}
    name_map = {}
    for i in range(0, len(names), 500):
        chunk = list(names)[i:i + 500]
        for u in User.query.filter(User.real_name.in_(chunk)).all():
            name_map.setdefault(u.real_name, []).append(u)

    preview = []

    def check_account(name):
        """返回 (status, user_id, candidates, reason)"""
        users = name_map.get(name, [])
        actives = [u for u in users if u.is_active]
        if not users:
            return ('to_create', None, [], '')
        if len(actives) == 1:
            return ('bound', actives[0].id, [], '')
        if not actives and users:
            return ('disabled', None, [], '账号已停用，请先在教师管理中启用')
        return ('multiple', None,
                [{'id': u.id, 'username': u.username} for u in actives[:10]],
                '存在多个同名账号，请在网页端矩阵中人工指定')

    # 任课教师条目（kind=subject）
    for l in parsed['links']:
        status, uid, cands, reason = check_account(l['teacher_name'])
        item = {'idx': len(preview), 'kind': 'subject', 'label': l['subject'],
                'grade': l['grade'], 'class_name': l['class_name'],
                'subject': l['subject'], 'teacher_name': l['teacher_name'],
                'status': status, 'user_id': uid, 'candidates': cands,
                'username': user_account.gen_username(l['teacher_name']) or ''
                if status == 'to_create' else '',
                'password': '', 'reason': reason}
        preview.append(item)
    # 班主任条目（kind=head；职务=label）
    for h in head_items:
        status, uid, cands, reason = check_account(h['teacher_name'])
        item = {'idx': len(preview), 'kind': 'head', 'label': h['pos'],
                'grade': h['grade'], 'class_name': h['class_name'],
                'subject': '', 'teacher_name': h['teacher_name'],
                'status': status, 'user_id': uid, 'candidates': cands,
                'username': user_account.gen_username(h['teacher_name']) or ''
                if status == 'to_create' else '',
                'password': '', 'reason': reason}
        preview.append(item)

    token = str(uuid.uuid4())
    _purge_expired_drafts()
    _DRAFT[token] = {
        'parsed': parsed, 'preview': preview,
        'created_at': date.today().isoformat(),
        '_ts': time.time(),
        'fname': file.filename,
    }
    return render_template('grades/teacher_import_report.html', token=token,
                           sections=parsed['sections'], errors=parsed['errors'],
                           preview=preview, head_total=len(head_items), ep=_ep)


@bp.route('/teachers/import/confirm', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_import_confirm():
    token = (request.form.get('token') or '').strip()
    _purge_expired_drafts()
    draft = _DRAFT.get(token)
    if not draft:
        flash('导入批次已失效（服务重启后需重新上传）', 'danger')
        return redirect(url_for(_ep('teachers_import_page')))
    try:
        preview = draft['preview']
        created = []
        bound = 0
        # 班主任身份名单（按姓名；与班型设置页 class-profile 数据同源同步）
        head_names = {it['teacher_name'] for it in preview if it['kind'] == 'head'}
        head_meta = {}  # name -> {grade, class_name}
        for it in preview:
            if it['kind'] == 'head' and it['teacher_name'] not in head_meta:
                head_meta[it['teacher_name']] = {'grade': it['grade'],
                                                 'class_name': it['class_name']}
        # 1) 先处理需开户的（username 可编辑；同名只建一次；班主任按班主任角色开户）
        created_by_name = {}
        for item in preview:
            if item['status'] != 'to_create':
                continue
            if item['teacher_name'] in created_by_name:
                continue
            username = (request.form.get(f'un_{item["idx"]}') or '').strip()
            role = ('homeroom_teacher' if item['teacher_name'] in head_names
                    else 'teacher')
            user, pwd = user_account.create_teacher_account(item['teacher_name'],
                                                            username or None,
                                                            role=role)
            created_by_name[item['teacher_name']] = user.id
            created.append({'name': item['teacher_name'], 'username': user.username,
                            'password': pwd,
                            'role': '班主任' if role == 'homeroom_teacher' else '任课教师'})
        db.session.flush()
        # 2) 写入任课映射（覆盖同组合）
        for item in preview:
            if item['kind'] != 'subject' or item['status'] not in ('bound', 'to_create'):
                continue
            user_id = item['user_id'] or created_by_name.get(item['teacher_name'])
            if not user_id:
                continue
            link = (TeacherSubjectLink.query
                    .filter_by(grade=item['grade'], class_name=item['class_name'],
                               subject=item['subject']).first())
            if link is None:
                db.session.add(TeacherSubjectLink(grade=item['grade'],
                                                  class_name=item['class_name'],
                                                  subject=item['subject'],
                                                  user_id=user_id, active=True,
                                                  created_by=current_user.id))
            else:
                link.user_id = user_id
                link.active = True
            bound += 1
        # 3) 班主任：提升角色/权限组 + 建立 UserClassLink（与 class-profile 页面同数据）
        heads_added = 0
        head_map = {}  # name -> user_id
        for item in preview:
            if item['kind'] != 'head' or item['status'] not in ('bound', 'to_create'):
                continue
            user_id = item['user_id'] or created_by_name.get(item['teacher_name'])
            if not user_id:
                continue
            head_map.setdefault(item['teacher_name'], user_id)
            user = User.query.get(user_id)
            if user.role not in ('homeroom_teacher', 'admin'):
                user.role = 'homeroom_teacher'   # 与 class-profile 页面 add 行为一致
            # 保证成绩查看权限（无 grades.view 的组 → 换班主任组）
            if not user.has_perm('grades.view'):
                bg = PermissionGroup.query.filter_by(name='班主任组').first()
                if bg:
                    user.permission_group_id = bg.id
                    user.sync_primary_link()   # P1 双写
            if not user.grades or not user.class_name:
                meta = head_meta.get(item['teacher_name'])
                if meta:
                    user.grade = meta['grade']
                    user.class_name = meta['class_name']
            if not (UserClassLink.query
                    .filter_by(user_id=user_id, grade=item['grade'],
                               class_name=item['class_name']).first()):
                db.session.add(UserClassLink(user_id=user_id, grade=item['grade'],
                                             class_name=item['class_name']))
                heads_added += 1
        # 4) 移除同步：文件涉及班级的旧班主任不在本次名单 → 移除（含角色降级）
        file_classes = {(it['grade'], it['class_name']) for it in preview}
        removed_heads = 0
        demoted = 0
        # 2026-10-10 架构审计修复：原实现是**嵌套循环 + 双重 N+1** ——
        #   外层每班查 UserClassLink；内层每个被删用户再查 User、再查一次
        #   UserClassLink 判断是否还有别的班。
        # 改为：① keep_ids 预计算（每班一次遍历 → 一次性建 map）；
        #      ② 按 file_classes 批量取出全部 UserClassLink；
        #      ③ 批量取涉及 User；④ 批量统计"每人还剩几条 UserClassLink"。
        # 删除/降级判定与顺序完全不变。
        keep_ids_by_class = {}
        for it in preview:
            if it['kind'] != 'head' or it['status'] not in ('bound', 'to_create'):
                continue
            uid = it['user_id'] or created_by_name.get(it['teacher_name'])
            if uid:
                keep_ids_by_class.setdefault((it['grade'], it['class_name']),
                                             set()).add(uid)

        _head_links = []
        if file_classes:
            # 按 (grade, class_name) 组合批量取：SQLite 支持元组 in_（复合列比对）
            _head_links = UserClassLink.query.filter(
                tuple_(UserClassLink.grade, UserClassLink.class_name)
                .in_(list(file_classes))
            ).all()
        _uids = {lk.user_id for lk in _head_links}
        _user_map = {u.id: u for u in
                     User.query.filter(User.id.in_(_uids)).all()} if _uids else {}

        for (g, c) in file_classes:
            keep_ids = keep_ids_by_class.get((g, c), set())
            for link in [lk for lk in _head_links
                         if lk.grade == g and lk.class_name == c]:
                if link.user_id in keep_ids:
                    continue
                db.session.delete(link)
                removed_heads += 1
                # 不再担任任何班主任 → 角色/组降级为任课教师
                user = _user_map.get(link.user_id)
                if user and user.role == 'homeroom_teacher' and user.role != 'admin':
                    remain = (UserClassLink.query
                              .filter(UserClassLink.user_id == user.id).first())
                    if not remain:
                        user.role = 'teacher'
                        if user.permission_group \
                                and user.permission_group.name == '班主任组':
                            tg = PermissionGroup.query.filter_by(name='任课教师组').first()
                            if tg:
                                user.permission_group_id = tg.id
                                user.sync_primary_link()   # P1 双写
                        demoted += 1
        db.session.commit()
        # 第七轮审计（P-M8）：导入也改教师映射 → 同失效
        from app.modules.grades.utils import invalidate_teacher_cache
        invalidate_teacher_cache()
        # 2026-10-14 方案：导入写入主表后同步「当前方案」快照
        link_plan.sync_current(operator_id=current_user.id)
    except Exception as e:
        db.session.rollback()
        # 2026-10-18：异常原文不外露（含 SQL / 路径），详情进日志
        from flask import current_app
        current_app.logger.error('教师导入失败: %s', e, exc_info=True)
        flash('导入失败，已回滚（详情已记入系统日志）', 'danger')
        return redirect(url_for(_ep('teachers_import_page')))
    _DRAFT.pop(token, None)
    log_operation(current_user, '导入', '教师映射', None,
                  f'教师安排表导入：任课绑定 {bound} 条，班主任 {heads_added} 人，'
                  f'移除 {removed_heads} 人（降级 {demoted}），新开账号 {len(created)} 个',
                  module='grades')
    flash(f'导入完成：任课绑定/更新 {bound} 条，班主任同步 {heads_added} 人，'
          f'移除旧班主任 {removed_heads} 人（降级 {demoted}），新开账号 {len(created)} 个',
          'success')
    return render_template('grades/teacher_import_result.html', created=created,
                           sync={'heads_added': heads_added, 'removed_heads': removed_heads,
                                 'demoted': demoted, 'bound': bound}, ep=_ep)


@bp.route('/teacher-template.xlsx')
@login_required
@perm_required('grades.teachers')
def teacher_template_download():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '教师安排表'
    # 2026-10-15 用户口径「教师映射扩容，增加综合组」：表头 = 9 个文化科（展示名用「英语」）
    # + 综合组 6 科，六科同样按列填写（导入解析见 teacher_import 的 _parse_subject_columns）
    _show = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
    _comp = list(COMPREHENSIVE_SUBJECTS)
    ws.append(['年级', '班级', '选科', '正班', '副班1', '副班2'] + _show + _comp)
    ws.append(['2025级', '01班', '史政地', '王慧敏', '李雅婷', '',
               '李雅婷', '赵俊杰', '陈思远', '', '', '', '孙晓芸', '王慧敏', '周立诚']
              + [''] * len(_comp))
    ws.append(['2025级', '02班', '史政地', '吴梦琪', '郑文静', '',
               '吴梦琪', '高振宇', '林嘉怡', '', '', '', '郑文静', '冯天佑', '宋天宇']
              + [''] * len(_comp))
    ws.append(['2024级', '01班', '史政地', '徐建华', '何雨欣', '',
               '徐建华', '罗志强', '唐佳琪', '', '', '', '汪雪莉', '何雨欣', '秦若彤']
              + [''] * len(_comp))
    widths = [10, 8, 8, 10, 10, 10] + [10] * (len(_show) + len(_comp))
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws2 = wb.create_sheet('填表说明')
    tips = [
        ['教师映射导入模板说明'],
        [''],
        ['1. 直接使用教务《教师安排表》也可导入（含多年级区段自动识别；"英语"自动对应系统"外语"科目）。'],
        ['2. 必填列：年级(2024级格式)、班级(01班)、科目列中的教师姓名；不填的科目单元格表示该班无该科任课教师。'],
        ['3. 班主任列（正班/副班1/副班2，最多 3 人/班）：作为班主任账号导入并开通本班全部成绩查看权限，'],
        ['   与"系统管理-班型设置"页面的班主任保持一致（正班优先；同一人在任课列出现只开通一次账号）。'],
        ['4. 系统按“教师姓名”核对账号：已有账号直接绑定；没有账号将自动新建（账号=姓名拼音，可在预览页修改），'],
        ['   初始密码随机生成、首次登录强制修改；班主任自动归属“班主任组”。'],
        ['5. 重复导入以本次文件为准：文件涉及的班级中，原班主任不在本次名单将自动移除（不再任班主任的账号降为任课教师）。'],
        ['6. 体育 / 音乐 / 美术 / 信息技术 / 通用技术 / 心理 六科（综合组）同样按列填写，会写入任课映射；'],
        ['   教室位置 / 班型等其它列可保留，导入时自动忽略。'],
    ]
    for row in tips:
        ws2.append(row)
    ws2.column_dimensions['A'].width = 120
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=f'教师映射导入模板_{date.today():%Y%m%d}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

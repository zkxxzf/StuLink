# StuLink v1.18.2.1 2026-09-24
# 任课教师映射：矩阵维护（按年级）+ 教师安排表批量导入 + 自动开户
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import json
import time
import uuid
from datetime import date

import openpyxl
from flask import render_template, request, jsonify, abort, redirect, url_for, flash, \
    send_file
from flask_login import login_required, current_user
from app.extensions import db
from app.models import User, Student, PermissionGroup, UserClassLink
from app.models.grades import TeacherSubjectLink, SUBJECTS
from app.modules.grades import bp
from app.modules.grades.services import teacher_import, user_account
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
    """v1.18.2.1：教师候选的合法角色集合（明确排除 admin）"""
    return ('teacher', 'homeroom_teacher', 'grade_leader',
            'dorm_manager', 'school_viewer', 'staff')


def _teacher_candidates():
    """v1.18.2.1：所有启用且属于教师类角色的账号 id 集合"""
    rows = db.session.query(User.id).filter(
        User.role.in_(_teacher_roles_filter()),
        User.is_active.is_(True)
    ).all()
    return {r[0] for r in rows}


def _matrix_users(grade):
    """下拉候选教师：启用 + 教师类角色（明确排除 admin）。

    v1.18.2.1：历史逻辑“已绑定 user_id 即可入选”会导致 admin 错配后一直显示在下拉里，
    现改为仅按角色过滤；已绑定但非教师类的行依旧可在矩阵中显示历史名字（由 _build_matrix 侧），
    但下拉中不再提供 admin 作为新选择。"""
    valid = _teacher_candidates()
    if not valid:
        return []
    rows = User.query.filter(User.id.in_(valid)).order_by(User.real_name).all()
    return [{'id': u.id, 'name': u.real_name, 'username': u.username} for u in rows]


# ==================== 矩阵维护 ====================

@bp.route('/teachers')
@login_required
@perm_required('grades.teachers')
def teachers_page():
    grades = _all_active_grades()
    grade = request.args.get('grade', '')
    if grade not in grades:
        grade = grades[0] if grades else ''
    return render_template('grades/teachers.html', grade_options=grades, grade=grade)


@bp.route('/api/teachers')
@login_required
@perm_required('grades.teachers')
def teachers_get():
    grade = request.args.get('grade', '').strip()
    active = _all_active_grades()
    if grade not in active:
        # 防御：年级缺失或参数异常时回退到第一个活跃年级（前端下拉固定有值，正常不会触发）
        grade = active[0] if active else ''
    if not grade:
        return jsonify(success=False, message='年级无效'), 400
    links = TeacherSubjectLink.query.filter_by(grade=grade, active=True).all()
    cells = {}
    for l in links:
        cells[f'{l.class_name}|{l.subject}'] = {'user_id': l.user_id}
    return jsonify(success=True, data={
        'grade': grade,
        'classes': _grade_classes(grade),
        'subjects': SUBJECTS,
        'cells': cells,
        'users': _matrix_users(grade),
    })


@bp.route('/api/teachers/save', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_save():
    data = request.get_json(silent=True) or {}
    grade = (data.get('grade') or '').strip()
    cells = data.get('cells') or {}    # {'01班|语文': user_id 或 ''}
    active = _all_active_grades()
    if grade not in active:
        # 防御：年级缺失或参数异常时回退到第一个活跃年级（前端下拉固定有值，正常不会触发）
        grade = active[0] if active else ''
    if not grade:
        return jsonify(success=False, message='年级无效'), 400
    # 校验 user_id 存在
    user_ids = {int(v) for v in cells.values() if v}
    if user_ids:
        valid = {u.id for u in User.query.filter(User.id.in_(user_ids)).all()}
        if not user_ids <= valid:
            return jsonify(success=False, message='存在无效的教师账号'), 400
    changes = {'bound': 0, 'released': 0, 'changed': 0}
    for cls_sub, user_id in cells.items():
        try:
            class_name, subject = cls_sub.split('|', 1)
        except ValueError:
            continue
        if subject not in SUBJECTS or class_name not in _grade_classes(grade):
            continue
        link = (TeacherSubjectLink.query
                .filter_by(grade=grade, class_name=class_name, subject=subject).first())
        if user_id:
            if link is None:
                db.session.add(TeacherSubjectLink(grade=grade, class_name=class_name,
                                                  subject=subject, user_id=int(user_id),
                                                  active=True, created_by=current_user.id))
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
    log_operation(current_user, '维护', '教师映射', None,
                  f'{grade} 矩阵保存：{json.dumps(changes, ensure_ascii=False)}',
                  module='grades')
    return jsonify(success=True, message='已保存', changes=changes)


# ==================== 批量导入（教师安排表） ====================

@bp.route('/teachers/import')
@login_required
@perm_required('grades.teachers')
def teachers_import_page():
    return render_template('grades/teacher_import.html')


@bp.route('/teachers/import/upload', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_import_upload():
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择教师安排表 Excel 文件', 'danger')
        return redirect(url_for('grades.teachers_import_page'))
    try:
        stream = io.BytesIO(file.read())
        parsed = teacher_import.parse_teacher_excel(stream)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('grades.teachers_import_page'))
    except Exception:
        flash('文件解析失败：请使用与《教师安排表》同构的格式（行=班级、科目为列）', 'danger')
        return redirect(url_for('grades.teachers_import_page'))

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
                           preview=preview,
                           head_total=len(head_items))


@bp.route('/teachers/import/confirm', methods=['POST'])
@login_required
@perm_required('grades.teachers')
def teachers_import_confirm():
    token = (request.form.get('token') or '').strip()
    _purge_expired_drafts()
    draft = _DRAFT.get(token)
    if not draft:
        flash('导入批次已失效（服务重启后需重新上传）', 'danger')
        return redirect(url_for('grades.teachers_import_page'))
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
            if not user.grade or not user.class_name:
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
        for (g, c) in file_classes:
            keep_ids = set()
            for it in preview:
                if it['kind'] == 'head' and it['grade'] == g and it['class_name'] == c \
                        and it['status'] in ('bound', 'to_create'):
                    uid = it['user_id'] or created_by_name.get(it['teacher_name'])
                    if uid:
                        keep_ids.add(uid)
            for link in UserClassLink.query.filter_by(grade=g, class_name=c).all():
                if link.user_id in keep_ids:
                    continue
                db.session.delete(link)
                removed_heads += 1
                # 不再担任任何班主任 → 角色/组降级为任课教师
                user = User.query.get(link.user_id)
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
                        demoted += 1
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'导入失败，已回滚：{e}', 'danger')
        return redirect(url_for('grades.teachers_import_page'))
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
                                 'demoted': demoted, 'bound': bound})


@bp.route('/teacher-template.xlsx')
@login_required
@perm_required('grades.teachers')
def teacher_template_download():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '教师安排表'
    ws.append(['年级', '班级', '选科', '正班', '副班1', '副班2',
               '语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理'])
    ws.append(['2025级', '01班', '史政地', '王慧敏', '李雅婷', '',
               '李雅婷', '赵俊杰', '陈思远', '', '', '', '孙晓芸', '王慧敏', '周立诚'])
    ws.append(['2025级', '02班', '史政地', '吴梦琪', '郑文静', '',
               '吴梦琪', '高振宇', '林嘉怡', '', '', '', '郑文静', '冯天佑', '宋天宇'])
    ws.append(['2024级', '01班', '史政地', '徐建华', '何雨欣', '',
               '徐建华', '罗志强', '唐佳琪', '', '', '', '汪雪莉', '何雨欣', '秦若彤'])
    widths = [10, 8, 8, 10, 10, 10] + [10] * 9
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
        ['6. 体育/音乐/美术/信息/心理/教室位置/班型等列可保留，导入时自动忽略。'],
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

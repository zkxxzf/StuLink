# StuLink v1.19.50.53 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序基础配置：选科维护 / 分层模板 / 任课教师映射 / 教师映射导入 / 考场房间库。

全部复用网页实现：选科直接改 `Student.subject_selection`（逐条校验年级班级归属），
模板复用 `BandTemplate`（内置不可删、绑定考试同口径），任课映射复用
`TeacherSubjectLink` + `link_plan_service`，教师导入复用 `teacher_import.parse_teacher_excel`，
房间复用 `AffairRoomLib`。
"""
import io
import time
import uuid
from datetime import date

from flask import g, request

from app.extensions import db
from app.models import Student, User
from app.models.grades import (BandTemplate, LINK_SUBJECTS, SUBJECTS,
                               AffairRoomLib, ExamRoomGroup,
                               ExamRoomGroupMap, TeacherSubjectLink)
from app.modules.grades.routes.band_templates import (
    _exam_type_options, ensure_builtin_templates)
from app.modules.grades.routes.teachers import (
    _DRAFT, _grade_classes, _matrix_users, _purge_expired_drafts)
from app.modules.grades.services import link_plan_service as link_plan
from app.modules.grades.services import teacher_import, user_account
from app.modules.grades.services.exam_guard import (
    assert_exam_visible, assert_grade_visible)
from app.modules.grades.services.scope import _all_active_grades
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_err, wx_ok
from app.utils.helpers import log_operation, get_dict_values

# ==================== 选科维护 ====================


@bp.route('/grades/subjects/options')
@wx_perm_required('grades.subject_mgmt')
def wx_subjects_options():
    grades = [x for x in (_all_active_grades() or []) if x]
    return wx_ok({'grades': grades, 'selections': get_dict_values('subject') or []})


@bp.route('/grades/subjects/students')
@wx_perm_required('grades.subject_mgmt')
def wx_subjects_students():
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    if not grade:
        return wx_err('请选择年级')
    assert_grade_visible(grade)
    classes = _grade_classes(grade)
    if not class_name:
        class_name = classes[0] if classes else ''
    if not class_name:
        return wx_ok({'grade': grade, 'class_name': '',
                      'classes': classes, 'students': []})
    rows = (Student.query
            .filter(Student.grade == grade, Student.class_name == class_name,
                    Student.class_name != '不分班')
            .order_by(Student.student_number).all())
    return wx_ok({
        'grade': grade,
        'class_name': class_name,
        'classes': classes,
        'students': [{
            'id': s.id,
            'no': s.student_number or '',
            'name': s.name or '',
            'selection': s.subject_selection or '',
        } for s in rows],
    })


@bp.route('/grades/subjects/save', methods=['POST'])
@wx_perm_required('grades.subject_mgmt')
def wx_subjects_save():
    data = request.get_json(silent=True) or {}
    grade = (data.get('grade') or '').strip()
    class_name = (data.get('class_name') or '').strip()
    selections = data.get('selections') or {}
    if not grade or not class_name:
        return wx_err('请选择年级与班级')
    assert_grade_visible(grade)
    changed = 0
    for key, val in selections.items():
        try:
            sid = int(key)
        except (TypeError, ValueError):
            continue
        s = db.session.get(Student, sid)
        if s is None:
            continue
        if s.grade != grade or s.class_name != class_name:
            return wx_err('学号不属于所选年级/班级', 403)
        val = (val or '').strip()
        if (s.subject_selection or '') != val:
            s.subject_selection = val or None
            changed += 1
    if changed:
        db.session.commit()
        log_operation(g.wx_user, '选科维护', '学生', None,
                      f'{grade} {class_name} 修改 {changed} 人选科',
                      module='grades')
    return wx_ok({'changed': changed}, f'已更新 {changed} 名学生的选科')


# ==================== 分层模板 ====================


@bp.route('/grades/band-templates')
@wx_perm_required('grades.view')
def wx_band_templates():
    ensure_builtin_templates()
    exam_id = request.args.get('exam_id', type=int)
    rows = BandTemplate.query.order_by(BandTemplate.sort_order,
                                       BandTemplate.id).all()
    bound = suggested = None
    if exam_id:
        from app.models.grades import Exam
        exam = db.session.get(Exam, exam_id)
        if exam:
            bound = exam.band_template_id()
    templates = [{
        'id': t.id, 'name': t.name, 'lower_mode': t.lower_mode,
        'layers': t.get_layers(), 'remark': t.remark or '',
        'is_builtin': bool(t.is_builtin),
        'exam_types': t.get_exam_types(),
    } for t in rows]
    if exam_id and not bound:
        suggested = next((t['id'] for t in templates
                          if t['name'] == '默认模板'), None)
    return wx_ok({
        'templates': templates,
        'bound_id': bound,
        'suggested_id': suggested,
        'exam_types': _exam_type_options(),
        'can_edit': g.wx_user.has_perm('grades.settings'),
    })


@bp.route('/grades/band-templates/save', methods=['POST'])
@wx_perm_required('grades.settings')
def wx_band_template_save():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()
    if not name:
        return wx_err('请填写模板名称')
    layers = [{'name': str(x.get('name') or '').strip(),
               'ratio': (float(x['ratio']) if x.get('ratio') not in (None, '')
                         else None)}
              for x in (payload.get('layers') or [])
              if str(x.get('name') or '').strip()]
    if not layers:
        return wx_err('请至少填写一个层名')
    if len({x['name'] for x in layers}) != len(layers):
        return wx_err('层名不能重复')
    tid = payload.get('id')
    obj = db.session.get(BandTemplate, int(tid)) if tid else None
    if not obj:
        dup = BandTemplate.query.filter_by(name=name).first()
        if dup:
            return wx_err(f'模板名「{name}」已存在')
        mx = db.session.query(db.func.max(BandTemplate.sort_order)).scalar() or 0
        obj = BandTemplate(name=name, created_by=g.wx_user.id,
                           sort_order=mx + 1)
    else:
        obj.name = name
    lm = payload.get('lower_mode')
    obj.lower_mode = lm if lm in ('score', 'ratio', 'rank') else 'score'
    obj.remark = str(payload.get('remark') or '')[:100]
    obj.set_layers(layers)
    obj.set_exam_types([str(x).strip() for x in (payload.get('exam_types') or [])
                        if str(x).strip()])
    db.session.add(obj)
    db.session.commit()
    log_operation(g.wx_user, '保存', '划线模板', obj.id,
                  f'{name}（{obj.lower_mode}）：'
                  + '、'.join(f'{x["name"]}={x["ratio"]}' for x in layers),
                  module='grades')
    return wx_ok({'id': obj.id}, '模板已保存')


@bp.route('/grades/band-templates/<int:tid>/delete', methods=['POST'])
@wx_perm_required('grades.settings')
def wx_band_template_delete(tid):
    from app.models.grades import Exam
    obj = db.session.get(BandTemplate, tid)
    if obj is None:
        return wx_err('模板不存在', 404)
    if obj.is_builtin:
        return wx_err('内置模板不能删除')
    for exam in Exam.query.filter(
            Exam.config_json.contains(f'"band_template_id": {obj.id}')).all():
        if exam.band_template_id() == obj.id:
            exam.set_band_template(None)
            db.session.add(exam)
    name = obj.name
    db.session.delete(obj)
    db.session.commit()
    log_operation(g.wx_user, '删除', '划线模板', tid, name, module='grades')
    return wx_ok({'id': tid}, '模板已删除')


@bp.route('/grades/band-templates/bind', methods=['POST'])
@wx_perm_required('grades.settings')
def wx_band_template_bind():
    payload = request.get_json(silent=True) or {}
    exam = assert_exam_visible(payload.get('exam_id'))
    tid = payload.get('template_id')
    if not tid:
        exam.set_band_template(None)
    else:
        tpl = db.session.get(BandTemplate, int(tid))
        if tpl is None:
            return wx_err('模板不存在', 404)
        exam.set_band_template(tpl.id, tpl.name)
    db.session.add(exam)
    db.session.commit()
    return wx_ok({'template_id': exam.band_template_id(),
                  'template_name': exam.band_template_name()},
                 '已绑定' if tid else '已解除绑定')


# ==================== 任课教师映射 ====================


@bp.route('/grades/teachers')
@wx_perm_required('grades.teachers')
def wx_grades_teachers():
    grade = (request.args.get('grade') or '').strip()
    active = [x for x in (_all_active_grades() or []) if x]
    if grade not in active:
        grade = active[0] if active else ''
    if not grade:
        return wx_err('年级无效')
    links = TeacherSubjectLink.query.filter_by(grade=grade, active=True).all()
    names = link_plan.user_names({l.user_id for l in links})
    cells = {f'{l.class_name}|{l.subject}': {
        'user_id': l.user_id, 'user_name': names.get(l.user_id, '')}
        for l in links}
    return wx_ok({
        'grades': active,
        'grade': grade,
        'classes': _grade_classes(grade),
        'subjects': list(LINK_SUBJECTS),
        'subject_groups': [
            {'name': '', 'subjects': list(SUBJECTS)},
            {'name': '综合组', 'subjects': ['体育', '音乐', '美术',
                                           '信息技术', '通用技术', '心理']},
        ],
        'cells': cells,
        'users': _matrix_users(grade),
    })


@bp.route('/grades/teachers/save', methods=['POST'])
@wx_perm_required('grades.teachers')
def wx_grades_teachers_save():
    data = request.get_json(silent=True) or {}
    grade = (data.get('grade') or '').strip()
    cells = data.get('cells') or {}
    active = [x for x in (_all_active_grades() or []) if x]
    if grade not in active:
        grade = active[0] if active else ''
    if not grade:
        return wx_err('年级无效')
    # 2026-10-20 第十六轮：年级必须过数据范围 —— 原来直接取「全校活跃年级」，
    # 持 grades.teachers 的年级长可写外年级的任课映射/建教师账号改角色。
    try:
        assert_grade_visible(grade)
    except PermissionError:
        return wx_err('该年级不在你的数据范围内', 403)
    try:
        user_ids = {int(v) for v in cells.values() if v}
    except (TypeError, ValueError):
        return wx_err('参数不正确：教师账号需为数字')
    if user_ids:
        valid = {u.id for u in User.query.filter(User.id.in_(user_ids)).all()}
        if not user_ids <= valid:
            return wx_err('存在无效的教师账号')
    changes = {'bound': 0, 'released': 0, 'changed': 0}
    index = {}
    for l in TeacherSubjectLink.query.filter_by(grade=grade).all():
        index[(l.class_name, l.subject)] = l
    classes = _grade_classes(grade)
    for cls_sub, user_id in cells.items():
        try:
            class_name, subject = cls_sub.split('|', 1)
        except ValueError:
            continue
        if subject not in LINK_SUBJECTS or class_name not in classes:
            continue
        link = index.get((class_name, subject))
        if user_id:
            if link is None:
                link = TeacherSubjectLink(
                    grade=grade, class_name=class_name, subject=subject,
                    user_id=int(user_id), active=True,
                    created_by=g.wx_user.id)
                db.session.add(link)
                index[(class_name, subject)] = link
                changes['bound'] += 1
            elif not link.active or link.user_id != int(user_id):
                link.user_id = int(user_id)
                link.active = True
                changes['changed'] += 1
        elif link and link.active:
            link.active = False
            changes['released'] += 1
    db.session.commit()
    from app.modules.grades.utils import invalidate_teacher_cache
    invalidate_teacher_cache()
    plan = link_plan.sync_current(operator_id=g.wx_user.id)
    log_operation(g.wx_user, '维护', '教师映射', None,
                  f'{grade} 矩阵保存：{changes}', module='grades')
    return wx_ok({'changes': changes, 'plan': plan.to_dict() if plan else None},
                 '已保存')


@bp.route('/grades/teachers/import/upload', methods=['POST'])
@wx_perm_required('grades.teachers')
def wx_grades_teachers_import_upload():
    file = request.files.get('file')
    if not file or not file.filename:
        return wx_err('请选择教师安排表 Excel 文件')
    ok, msg = _validate_excel(file)
    if not ok:
        return wx_err(msg)
    try:
        parsed = teacher_import.parse_teacher_excel(io.BytesIO(file.read()))
    except ValueError as e:
        return wx_err(str(e))
    except Exception:
        return wx_err('文件解析失败：请使用与《教师安排表》同构的格式（行=班级、科目为列）')
    head_items = parsed.get('homerooms') or []
    names = ({l['teacher_name'] for l in parsed['links']}
             | {h['teacher_name'] for h in head_items})
    name_map = {}
    for i in range(0, len(names), 500):
        chunk = list(names)[i:i + 500]
        for u in User.query.filter(User.real_name.in_(chunk)).all():
            name_map.setdefault(u.real_name, []).append(u)

    preview = []
    for l in parsed['links']:
        status, uid, reason = _account_status(name_map, l['teacher_name'])
        preview.append({
            'idx': len(preview), 'kind': 'subject', 'label': l['subject'],
            'grade': l['grade'], 'class_name': l['class_name'],
            'subject': l['subject'], 'teacher_name': l['teacher_name'],
            'status': status, 'user_id': uid, 'reason': reason,
        })
    for h in head_items:
        status, uid, reason = _account_status(name_map, h['teacher_name'])
        preview.append({
            'idx': len(preview), 'kind': 'head', 'label': h['pos'],
            'grade': h['grade'], 'class_name': h['class_name'],
            'subject': '', 'teacher_name': h['teacher_name'],
            'status': status, 'user_id': uid, 'reason': reason,
        })
    token = str(uuid.uuid4())
    _purge_expired_drafts()
    _DRAFT[token] = {'parsed': parsed, 'preview': preview,
                     'created_at': date.today().isoformat(),
                     '_ts': time.time(), 'fname': file.filename}
    counts = {}
    for it in preview:
        counts[it['status']] = counts.get(it['status'], 0) + 1
    return wx_ok({
        'token': token,
        'fname': file.filename,
        'sections': parsed.get('sections') or [],
        'errors': parsed.get('errors') or [],
        'counts': counts,
        'items': preview[:300],
        'total': len(preview),
    })


@bp.route('/grades/teachers/import/confirm', methods=['POST'])
@wx_perm_required('grades.teachers')
def wx_grades_teachers_import_confirm():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    _purge_expired_drafts()
    draft = _DRAFT.get(token)
    if not draft:
        return wx_err('导入批次已失效（服务重启后需重新上传）')
    preview = draft['preview']
    # 2026-10-20 第十七轮审计：导入确认必须逐行过年级范围 —— 矩阵保存已有闸
    # （:270），导入路径原来完全没有：可写外年级任课映射、把账号改成班主任并
    # 挂进班主任组、批量开户（口令明文回显）。越界整批拒绝，不做静默过滤。
    from app.modules.grades.services.exam_guard import assert_grade_visible
    for _it in preview:
        _g = (_it.get('grade') or '').strip()
        if not _g:
            continue
        try:
            assert_grade_visible(_g)
        except PermissionError:
            return wx_err('导入内容包含不在你数据范围内的年级，已拒绝', 403)
    head_names = {it['teacher_name'] for it in preview if it['kind'] == 'head'}
    head_meta = {}
    for it in preview:
        if it['kind'] == 'head':
            head_meta.setdefault(it['teacher_name'],
                                 {'grade': it['grade'],
                                  'class_name': it['class_name']})
    created, created_by_name = [], {}
    for item in preview:
        if item['status'] != 'to_create':
            continue
        if item['teacher_name'] in created_by_name:
            continue
        role = ('homeroom_teacher' if item['teacher_name'] in head_names
                else 'teacher')
        user, pwd = user_account.create_teacher_account(
            item['teacher_name'], None, role=role)
        created_by_name[item['teacher_name']] = user.id
        created.append({'name': item['teacher_name'],
                        'username': user.username, 'password': pwd,
                        'role': '班主任' if role == 'homeroom_teacher'
                                else '任课教师'})
    db.session.flush()
    bound = 0
    for item in preview:
        if item['kind'] != 'subject' or item['status'] not in ('bound',
                                                              'to_create'):
            continue
        user_id = item['user_id'] or created_by_name.get(item['teacher_name'])
        if not user_id:
            continue
        link = (TeacherSubjectLink.query
                .filter_by(grade=item['grade'], class_name=item['class_name'],
                           subject=item['subject']).first())
        if link is None:
            db.session.add(TeacherSubjectLink(
                grade=item['grade'], class_name=item['class_name'],
                subject=item['subject'], user_id=user_id, active=True,
                created_by=g.wx_user.id))
        else:
            link.user_id = user_id
            link.active = True
        bound += 1
    heads = 0
    from app.models import UserClassLink, PermissionGroup
    for item in preview:
        if item['kind'] != 'head' or item['status'] not in ('bound',
                                                           'to_create'):
            continue
        user_id = item['user_id'] or created_by_name.get(item['teacher_name'])
        if not user_id:
            continue
        user = db.session.get(User, user_id)
        if user.role not in ('homeroom_teacher', 'admin'):
            user.role = 'homeroom_teacher'
        if not user.has_perm('grades.view'):
            bg = PermissionGroup.query.filter_by(name='班主任组').first()
            if bg:
                user.permission_group_id = bg.id
                user.sync_primary_link()
        meta = head_meta.get(item['teacher_name'])
        if meta and (not user.grades or not user.class_name):
            user.grade = meta['grade']
            user.class_name = meta['class_name']
        if not (UserClassLink.query
                .filter_by(user_id=user_id, grade=item['grade'],
                           class_name=item['class_name']).first()):
            db.session.add(UserClassLink(user_id=user_id,
                                         grade=item['grade'],
                                         class_name=item['class_name']))
            heads += 1
    db.session.commit()
    from app.modules.grades.utils import invalidate_teacher_cache
    invalidate_teacher_cache()
    link_plan.sync_current(operator_id=g.wx_user.id)
    _DRAFT.pop(token, None)
    log_operation(g.wx_user, '导入', '教师映射', None,
                  f'{draft.get("fname")}：开户{len(created)}、映射{bound}',
                  module='grades')
    return wx_ok({
        'created': created, 'bound': bound, 'heads_added': heads,
        'multiple': [it['teacher_name'] for it in preview
                     if it['status'] == 'multiple'],
        'note': '多同名/停用账号请在网页端矩阵中人工指定；旧班主任移除请在网页端完成',
    }, '导入完成')


def _account_status(name_map, name):
    users = name_map.get(name, [])
    actives = [u for u in users if u.is_active]
    if not users:
        return 'to_create', None, ''
    if len(actives) == 1:
        return 'bound', actives[0].id, ''
    if not actives:
        return 'disabled', None, '账号已停用，请先在教师管理中启用'
    return 'multiple', None, '存在多个同名账号，请在网页端矩阵中人工指定'


def _validate_excel(file):
    from app.utils.upload_guard import validate_upload
    return validate_upload(file.filename, allowed_exts=['xlsx', 'xls'],
                           stream=file.stream)


# ==================== 考场房间库 ====================


@bp.route('/grades/rooms')
@wx_perm_required('grades.edit')
def wx_rooms():
    from app.modules.grades.routes.exam_rooms import _ensure_default_groups
    _ensure_default_groups()
    groups = ExamRoomGroup.query.order_by(ExamRoomGroup.sort_order,
                                          ExamRoomGroup.id).all()
    gmap = {}
    for m in ExamRoomGroupMap.query.all():
        gmap.setdefault(m.lib_id, []).append(m.group_id)
    rooms = AffairRoomLib.query.order_by(AffairRoomLib.location).all()
    rows = [{
        'id': r.id, 'location': r.location,
        'capacity': r.capacity or 30, 'note': r.note or '',
        'group_ids': sorted(gmap.get(r.id, [])),
    } for r in rooms]
    return wx_ok({
        'rooms': rows,
        'groups': [{'id': x.id, 'name': x.name} for x in groups],
    })


@bp.route('/grades/rooms/save', methods=['POST'])
@wx_perm_required('grades.edit')
def wx_room_save():
    from app.utils.text_guard import sanitize_label
    data = request.get_json(silent=True) or {}
    lid = data.get('lib_id')
    location = sanitize_label(data.get('location'), 100)
    note = sanitize_label(data.get('note'), 100)
    if not location:
        return wx_err('房间位置必填')
    # 2026-10-20 第十六轮：lib_id 非数字原来直接 ValueError → 500
    try:
        lid = int(lid) if lid else None
    except (TypeError, ValueError):
        return wx_err('参数不正确：lib_id 需为数字')
    row = db.session.get(AffairRoomLib, lid) if lid else None
    dup = AffairRoomLib.query.filter(AffairRoomLib.location == location,
                                     AffairRoomLib.id != (row.id if row else 0)
                                     ).first()
    if dup:
        return wx_err(f'房间「{location}」已存在')
    if row is None:
        row = AffairRoomLib(location=location)
        db.session.add(row)
    row.location = location
    row.note = note
    db.session.commit()
    log_operation(g.wx_user, '保存', '考场房间', row.id, location,
                  module='grades')
    return wx_ok({'id': row.id}, '房间已保存')


@bp.route('/grades/rooms/<int:lid>/delete', methods=['POST'])
@wx_perm_required('grades.edit')
def wx_room_delete(lid):
    row = db.session.get(AffairRoomLib, lid)
    if row is None:
        return wx_err('房间不存在', 404)
    loc = row.location
    ExamRoomGroupMap.query.filter_by(lib_id=lid).delete()
    db.session.delete(row)
    db.session.commit()
    log_operation(g.wx_user, '删除', '考场房间', lid, loc, module='grades')
    return wx_ok({'id': lid}, '房间已删除')

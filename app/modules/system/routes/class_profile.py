# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required
from app.extensions import db
from app.models import DictCategory, DictItem, ClassProfile, ClassSubject, User, UserClassLink
from app.utils.decorators import perm_required
from app.utils.helpers import get_dict_values

bp = Blueprint('class_profile', __name__, url_prefix='/class-profile')


@bp.route('/')
@perm_required('system.class_profile')
def manage():
    """班型管理主页 - 列出全部班级，支持批量编辑"""
    grades = sorted(get_dict_values('grade'), reverse=True)
    class_options = get_dict_values('class')
    # 过滤掉特殊班级（未分班、已转出、离校等）
    normal_classes = [c for c in class_options if c.endswith('班') and '未分班' not in c and '离校' not in c and '已转出' not in c]

    # 获取现有 profiles
    profiles = ClassProfile.query.all()
    profile_map = {}
    for p in profiles:
        profile_map[(p.grade, p.class_name)] = p

    # 构建完整班级-年级矩阵
    matrix = {}
    # 预加载所有班主任关联
    all_links = UserClassLink.query.all()
    teacher_map = {}  # {(grade, class_name): [user_ids]}
    for link in all_links:
        teacher_map.setdefault((link.grade, link.class_name), []).append(link.user_id)
    # 预加载所有用户
    all_users = {u.id: u for u in User.query.all()}

    for grade in grades:
        matrix[grade] = []
        for cls in normal_classes:
            key = (grade, cls)
            p = profile_map.get(key)
            # 班主任
            teacher_ids = teacher_map.get(key, [])
            teachers = [{'id': uid, 'name': all_users[uid].real_name}
                        for uid in teacher_ids if uid in all_users]
            matrix[grade].append({
                'grade': grade,
                'class_name': cls,
                'profile_id': p.id if p else None,
                'class_type': p.class_type or '' if p else '',
                'subject_direction': p.subject_direction or '' if p else '',
                'subjects': p.subject_list if p else [],
                'teachers': teachers,
            })

    # 字典选项
    class_type_options = get_dict_values('class_type')
    direction_options = get_dict_values('subject_direction')
    subject_options = get_dict_values('subject')
    teacher_options = User.query.filter(
        User.role.in_(['homeroom_teacher', 'admin', 'grade_leader']),
        User.is_active == True
    ).order_by(User.real_name).all()

    return render_template('system/dictionary/class_profile.html',
                           grades=grades,
                           matrix=matrix,
                           class_type_options=class_type_options,
                           direction_options=direction_options,
                           subject_options=subject_options,
                           teacher_options=teacher_options)


@bp.route('/batch-save', methods=['POST'])
@perm_required('system.class_profile')
def batch_save():
    """批量保存所有班型设置"""
    import json
    data = request.get_json()
    if not data or 'items' not in data:
        return jsonify({'success': False, 'message': '数据格式错误'}), 400

    items = data['items']
    if not isinstance(items, list):
        return jsonify({'success': False, 'message': '数据格式错误'}), 400

    # 字典校验值
    valid_classes = set(get_dict_values('class'))
    valid_grades = set(get_dict_values('grade'))
    valid_types = set(get_dict_values('class_type'))
    valid_directions = set(get_dict_values('subject_direction'))
    valid_subjects = set(get_dict_values('subject'))

    updated = 0
    created = 0
    errors = []

    for item in items:
        grade = item.get('grade', '').strip()
        class_name = item.get('class_name', '').strip()
        class_type = item.get('class_type', '').strip()
        subject_direction = item.get('subject_direction', '').strip()

        if grade not in valid_grades or class_name not in valid_classes:
            continue

        if class_type and class_type not in valid_types:
            errors.append(f'{grade}{class_name}: 无效班型"{class_type}"')
            continue
        if subject_direction and subject_direction not in valid_directions:
            errors.append(f'{grade}{class_name}: 无效选科方向"{subject_direction}"')
            continue

        # 找或创建 profile
        profile = ClassProfile.query.filter_by(
            grade=grade, class_name=class_name
        ).first()

        has_changes = False
        if profile:
            if profile.class_type != (class_type or None):
                profile.class_type = class_type or None
                has_changes = True
            if profile.subject_direction != (subject_direction or None):
                profile.subject_direction = subject_direction or None
                has_changes = True
        else:
            if not class_type and not subject_direction:
                continue  # 全空就不创建
            profile = ClassProfile(
                grade=grade,
                class_name=class_name,
                class_type=class_type or None,
                subject_direction=subject_direction or None
            )
            db.session.add(profile)
            db.session.flush()
            created += 1
            has_changes = True

        # 处理选科
        new_subjects = item.get('subjects', [])
        if not isinstance(new_subjects, list):
            new_subjects = []
        # 过滤无效值
        new_subjects = [s for s in new_subjects if s in valid_subjects]

        if profile.id:
            old_subjects = profile.subject_list
            if sorted(old_subjects) != sorted(new_subjects):
                profile.subjects.delete()
                for sv in new_subjects:
                    profile.subjects.append(ClassSubject(subject_value=sv))
                has_changes = True

        if has_changes and profile.id:
            updated += 1

    # 如果有空记录（class_type 和 subject_direction 都为空），清理它们
    # 但不删除仍有 settings 的记录（由前端控制）

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'保存失败: {str(e)}'}), 500

    msg = f'已更新 {updated} 个班级'
    if created:
        msg += f'，新建 {created} 个'
    if errors:
        msg += f'（{len(errors)} 个警告）'

    return jsonify({
        'success': True,
        'message': msg,
        'updated': updated,
        'created': created,
        'errors': errors
    })


@bp.route('/teachers/<grade>/<class_name>', methods=['GET'])
@perm_required('system.class_profile')
def get_teachers(grade, class_name):
    """获取某班的班主任列表"""
    links = UserClassLink.query.filter_by(grade=grade, class_name=class_name).all()
    users = []
    for link in links:
        u = User.query.get(link.user_id)
        if u:
            users.append({'id': u.id, 'name': u.real_name})
    return jsonify({'success': True, 'teachers': users})


@bp.route('/teachers/<grade>/<class_name>/add', methods=['POST'])
@perm_required('system.class_profile')
def add_teacher(grade, class_name):
    """为某班添加班主任（最多3人）"""
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '请选择教师'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'}), 404
    existing = UserClassLink.query.filter_by(
        grade=grade, class_name=class_name
    ).count()
    if existing >= 3:
        return jsonify({'success': False, 'message': '每班最多3名班主任'}), 400
    if UserClassLink.query.filter_by(
        user_id=user_id, grade=grade, class_name=class_name
    ).first():
        return jsonify({'success': False, 'message': '该教师已是此班班主任'}), 400
    link = UserClassLink(user_id=user_id, grade=grade, class_name=class_name)
    db.session.add(link)
    # 如果用户还没设role为班主任，自动更新
    if user.role not in ('homeroom_teacher', 'admin'):
        user.role = 'homeroom_teacher'
    db.session.commit()
    return jsonify({'success': True, 'message': f'{user.real_name} 已设为 {grade}{class_name} 班主任',
                    'teacher': {'id': user.id, 'name': user.real_name}})


@bp.route('/teachers/<grade>/<class_name>/remove', methods=['POST'])
@perm_required('system.class_profile')
def remove_teacher(grade, class_name):
    """移除某班班主任"""
    data = request.get_json()
    user_id = data.get('user_id')
    link = UserClassLink.query.filter_by(
        user_id=user_id, grade=grade, class_name=class_name
    ).first()
    if not link:
        return jsonify({'success': False, 'message': '未找到该关联'}), 404
    user = User.query.get(user_id)
    db.session.delete(link)
    db.session.commit()
    return jsonify({'success': True, 'message': f'已取消 {user.real_name} 的班主任身份'})



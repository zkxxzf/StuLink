# StuLink v1.9.2 2026-09-18
# 选科维护：按班级快速查看/批量修改学生选科组合
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user
from app.models import Student
from app.extensions import db
from app.modules.grades import bp
from app.utils.decorators import perm_required
from app.utils.helpers import get_graduated_grades, get_dict_values, get_active_grades, log_operation


@bp.route('/subjects')
@perm_required('grades.subject_mgmt')
def subject_mgmt():
    """选科维护页面：按班级列出学生，批量修改选科组合"""
    graduated = get_graduated_grades()
    grades = [g for g in get_active_grades() if g not in graduated]
    classes = get_dict_values('class')
    subjects = get_dict_values('subject')

    grade = request.args.get('grade', '')
    class_name = request.args.get('class_name', '')
    students = []
    if grade and class_name:
        students = Student.query.filter(
            Student.grade == grade, Student.class_name == class_name,
            Student.class_name != '不分班'
        ).order_by(Student.student_number).all()

    return render_template('grades/subject_mgmt.html', grades=grades, classes=classes,
                           subjects=subjects, grade=grade, class_name=class_name,
                           students=students)


@bp.route('/subjects/save', methods=['POST'])
@perm_required('grades.subject_mgmt')
def subject_mgmt_save():
    """批量保存选科修改（仅更新有变化的）"""
    grade = request.form.get('grade', '')
    class_name = request.form.get('class_name', '')
    changed = 0
    for key in list(request.form.keys()):
        if not key.startswith('sel_'):
            continue
        try:
            sid = int(key[4:])
        except ValueError:
            continue
        val = request.form.get(key, '').strip()
        s = db.session.get(Student, sid)
        if s and (s.subject_selection or '') != val:
            s.subject_selection = val or None
            changed += 1
    if changed:
        db.session.commit()
        log_operation(current_user, '选科维护', '学生', None,
                      f'{grade} {class_name} 修改 {changed} 人选科')
        flash(f'已更新 {changed} 名学生的选科', 'success')
    else:
        flash('没有需要修改的选科', 'info')
    return redirect(url_for('grades.subject_mgmt', grade=grade, class_name=class_name))

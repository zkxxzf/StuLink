# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, PasswordField
from wtforms.validators import DataRequired, Length, Optional


class UserForm(FlaskForm):
    username = StringField('用户�?, validators=[DataRequired(message='请输入用户名'),
                                                 Length(max=50)])
    real_name = StringField('真实姓名', validators=[DataRequired(message='请输入真实姓�?),
                                                     Length(max=50)])
    role = SelectField('角色', choices=[
        ('admin', '管理�?),
        ('dorm_manager', '宿管教师'),
        ('homeroom_teacher', '班主�?),
        ('grade_leader', '年级�?),
        ('school_viewer', '全校�?),
    ], validators=[DataRequired()])
    permission_group_id = SelectField('权限�?, validators=[Optional()], coerce=int)
    grade = SelectField('管理年级', validators=[Optional()])
    class_name = SelectField('管理班级', validators=[Optional()])
    password = PasswordField('密码', validators=[Optional(), Length(min=6, message='密码至少6�?)])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.utils.helpers import get_dict_items
        from app.models import PermissionGroup
        self.grade.choices = [('', '不限')] + get_dict_items('grade')
        self.class_name.choices = [('', '不限')] + get_dict_items('class')
        groups = PermissionGroup.query.order_by(PermissionGroup.id).all()
        self.permission_group_id.choices = [(0, '�?请选择 �?)] + [(g.id, g.name) for g in groups]

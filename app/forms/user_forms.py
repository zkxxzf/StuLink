# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, PasswordField, ValidationError
from wtforms.validators import DataRequired, Length, Optional
from app.utils.password_policy import validate_password, MIN_LENGTH


class UserForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired(message='请输入用户名'),
                                                 Length(max=50)])
    real_name = StringField('真实姓名', validators=[DataRequired(message='请输入真实姓名'),
                                                     Length(max=50)])
    role = SelectField('角色', choices=[
        ('admin', '管理员'),
        ('dorm_manager', '宿管教师'),
        ('homeroom_teacher', '班主任'),
        ('grade_leader', '年级长'),
        ('school_viewer', '全校组'),
        ('staff', '教职人员'),
    ], validators=[DataRequired()])
    permission_group_id = SelectField('权限组', validators=[Optional()], coerce=int)
    grade = SelectField('管理年级', validators=[Optional()])
    class_name = SelectField('管理班级', validators=[Optional()])
    password = PasswordField('密码', validators=[Optional(), Length(min=MIN_LENGTH, message=f'密码至少{MIN_LENGTH}位')])

    def validate_password(self, field):
        """M-15：新建/编辑用户时手工填写的口令同样走统一策略（留空则自动生成）"""
        if not field.data:
            return
        ok, msg = validate_password(field.data,
                                    username=self.username.data,
                                    real_name=self.real_name.data)
        if not ok:
            raise ValidationError(msg)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.utils.helpers import get_dict_items
        from app.models import PermissionGroup
        self.grade.choices = [('', '不限')] + get_dict_items('grade')
        self.class_name.choices = [('', '不限')] + get_dict_items('class')
        groups = PermissionGroup.query.order_by(PermissionGroup.id).all()
        self.permission_group_id.choices = [(0, '— 请选择 —')] + [(g.id, g.name) for g in groups]



# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, ValidationError
from wtforms.validators import DataRequired, Length, EqualTo
from app.utils.password_policy import validate_password, MIN_LENGTH


class LoginForm(FlaskForm):
    username = StringField('用户名', validators=[DataRequired(message='请输入用户名')])
    password = PasswordField('密码', validators=[DataRequired(message='请输入密码')])


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('原密码', validators=[DataRequired(message='请输入原密码')])
    # M-15：长度门槛提到策略下限，具体强度校验统一走 password_policy（不再写第二份规则）
    new_password = PasswordField('新密码', validators=[
        DataRequired(message='请输入新密码'),
        Length(min=MIN_LENGTH, message=f'密码至少{MIN_LENGTH}位'),
    ])
    confirm_password = PasswordField('确认新密码', validators=[
        DataRequired(message='请再次输入新密码'),
        EqualTo('new_password', message='两次输入的密码不一致'),
    ])

    def validate_new_password(self, field):
        from flask_login import current_user
        ok, msg = validate_password(
            field.data,
            username=getattr(current_user, 'username', None),
            real_name=getattr(current_user, 'real_name', None),
        )
        if not ok:
            raise ValidationError(msg)
        # 不允许新密码与原密码相同
        if current_user.is_authenticated and current_user.check_password(field.data):
            raise ValidationError('新密码不能与当前密码相同')



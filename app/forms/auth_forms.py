# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Length, EqualTo


class LoginForm(FlaskForm):
    username = StringField('用户�?, validators=[DataRequired(message='请输入用户名')])
    password = PasswordField('密码', validators=[DataRequired(message='请输入密�?)])


class ChangePasswordForm(FlaskForm):
    old_password = PasswordField('原密�?, validators=[DataRequired(message='请输入原密码')])
    new_password = PasswordField('新密�?, validators=[
        DataRequired(message='请输入新密码'),
        Length(min=6, message='密码至少6�?),
    ])
    confirm_password = PasswordField('确认新密�?, validators=[
        DataRequired(message='请再次输入新密码'),
        EqualTo('new_password', message='两次输入的密码不一�?),
    ])

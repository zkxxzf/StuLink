# StuLink v1.4.5 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, TextAreaField
from wtforms.validators import DataRequired, Optional, Length


class StudentForm(FlaskForm):
    name = StringField('姓名', validators=[DataRequired(message='请输入姓名')])
    gender = SelectField('性别', choices=[('男', '男'), ('女', '女')],
                         validators=[DataRequired()])
    student_number = StringField('学号', validators=[DataRequired(message='请输入学号')])
    id_card_number = StringField('身份证号', validators=[Optional()])
    ethnicity = SelectField('民族', validators=[Optional()])
    phone1 = StringField('联系方式1', validators=[Optional()])
    phone2 = StringField('联系方式2', validators=[Optional()])
    grade = SelectField('年级', validators=[DataRequired(message='请选择年级')])
    class_name = SelectField('班级', validators=[DataRequired(message='请选择班级')])
    original_class = SelectField('原班级', validators=[Optional()])
    subject_selection = SelectField('选科', validators=[Optional()])
    boarding_type = SelectField('走读/住校', validators=[DataRequired(message='请选择')])
    day_student_type = SelectField('出门权限', validators=[Optional()])
    enrollment_status = SelectField('学籍情况', validators=[Optional()])
    textbook = SelectField('课本', validators=[Optional()])
    teacher_notes = TextAreaField('班主任备注', validators=[Optional()])
    enrollment_notes = StringField('学籍备注', validators=[Optional()])
    graduation_school_code = StringField('毕业学校代码', validators=[Optional(), Length(max=10)])
    graduation_school = StringField('毕业学校', validators=[Optional(), Length(max=100)])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.utils.helpers import get_dict_items
        # 动态加载字典选项
        self.ethnicity.choices = [('', '请选择')] + get_dict_items('ethnicity')
        self.grade.choices = [('', '请选择')] + get_dict_items('grade')
        self.class_name.choices = [('', '请选择')] + get_dict_items('class')
        self.original_class.choices = [('', '无')] + get_dict_items('class')
        self.subject_selection.choices = [('', '请选择')] + get_dict_items('subject')
        self.boarding_type.choices = [('', '请选择')] + get_dict_items('boarding_type')
        self.day_student_type.choices = [('', '无')] + get_dict_items('day_student_type')
        self.enrollment_status.choices = [('', '无')] + get_dict_items('enrollment_status')
        self.textbook.choices = [('', '无')] + get_dict_items('textbook')

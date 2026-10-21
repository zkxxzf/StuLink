"""教育事业统计：校舍与办学条件年度参数（2026-10-20 新增）

依据《教育事业综合统计调查》基表：
- 教基5170 中小学校舍情况（各项建筑面积）
- 教基5176 中小学、特殊教育学校资产等办学条件（占地/运动场/图书/终端/固定资产）
- 教基1102 基础教育学校基本情况里的「是否达标」项

设计：**一学年一行**（`school_year` 唯一），一次录入逐年复用；
页面在「系统管理 → 校舍资产」，数据落在 system.db。
面积单位平方米、金额单位万元、个数类为整数；未填留空（None）而不是 0，
避免把"没填"当成"确实是 0"报进基表。
"""
# StuLink v1.19.49.52 2026-10-20
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from datetime import datetime

from app.extensions import db


class EduStatAsset(db.Model):
    """某学年的校舍 / 办学条件参数。"""

    __bind_key__ = 'system'
    __tablename__ = 'edu_stat_assets'

    id = db.Column(db.Integer, primary_key=True)
    school_year = db.Column(db.String(20), unique=True, nullable=False)  # 如 2026-2027

    # ── 教基5170 校舍情况（平方米）────────────────────────────────────
    building_total = db.Column(db.Float)        # 本学年校舍建筑面积（总计）
    building_c_danger = db.Column(db.Float)     # #C级危房
    building_d_danger = db.Column(db.Float)     # #D级危房
    building_rented = db.Column(db.Float)       # #租用外单位
    room_teaching = db.Column(db.Float)         # 一、教学及辅助用房
    room_classroom = db.Column(db.Float)        #   普通教室
    room_special = db.Column(db.Float)          #   专用教室
    room_lab = db.Column(db.Float)              #     理化生实验室
    room_special_other = db.Column(db.Float)    #     其他（专用教室）
    room_public = db.Column(db.Float)           #   公共教学用房
    room_library = db.Column(db.Float)          #     图书阅览室
    room_sports_indoor = db.Column(db.Float)    #     室内体育用房
    room_psych = db.Column(db.Float)            #     心理辅导室
    room_public_other = db.Column(db.Float)     #     其他（公共教学用房）
    room_office = db.Column(db.Float)           # 二、行政办公用房
    room_teacher_office = db.Column(db.Float)   #   教师办公室
    room_office_other = db.Column(db.Float)     #   其他（行政办公）
    room_living = db.Column(db.Float)           # 三、生活用房
    room_staff_dorm = db.Column(db.Float)       #   教工值班宿舍
    room_teacher_turnover = db.Column(db.Float)  #   教师周转宿舍
    room_student_dorm = db.Column(db.Float)     #   学生宿舍
    room_canteen = db.Column(db.Float)          #   学生餐厅
    room_toilet = db.Column(db.Float)           #   厕所
    room_living_other = db.Column(db.Float)     #   其他（生活用房）
    room_other = db.Column(db.Float)            # 四、其他用房
    room_lent_out = db.Column(db.Float)         # #被外单位租（借）用

    # ── 教基5176 资产与办学条件 ────────────────────────────────────────
    land_area = db.Column(db.Float)             # 占地面积（平方米）
    green_area = db.Column(db.Float)            # #绿化用地面积（平方米）
    sports_field_area = db.Column(db.Float)     # #运动场地面积（平方米）
    football_11 = db.Column(db.Integer)         # 11人制足球场（个）
    football_7 = db.Column(db.Integer)          # 7人制足球场（个）
    football_5 = db.Column(db.Integer)          # 5人制足球场（个）
    books = db.Column(db.Integer)               # 图书（册）
    terminal_total = db.Column(db.Integer)      # 数字终端数（台）
    terminal_teacher = db.Column(db.Integer)    # #教师终端数（台）
    terminal_student = db.Column(db.Integer)    # #学生终端数（台）
    classroom_total = db.Column(db.Integer)     # 教室（间）
    classroom_multimedia = db.Column(db.Integer)  # #网络多媒体教室（间）
    asset_fixed = db.Column(db.Float)           # 固定资产总值（万元）
    asset_teaching_equipment = db.Column(db.Float)  # #教学仪器设备资产值（万元）

    # ── 教基1102 达标项（True/False；None=未填）────────────────────────
    ok_sports_venue = db.Column(db.Boolean)     # 体育运动场（馆）面积是否达标
    ok_sports_equipment = db.Column(db.Boolean)  # 体育器械配备是否达标
    ok_music_equipment = db.Column(db.Boolean)  # 音乐器材配备是否达标
    ok_art_equipment = db.Column(db.Boolean)    # 美术器材配备是否达标
    ok_science_lab = db.Column(db.Boolean)      # 理科实验仪器是否达标（中学）

    note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    updated_by = db.Column(db.Integer)          # users.id（跨表只存 id，不建外键）

    @classmethod
    def of(cls, school_year):
        """取某学年的参数行（可能为 None）。"""
        return cls.query.filter_by(school_year=school_year).first()

    @classmethod
    def upsert(cls, school_year, data, user_id=None):
        """按学年写入/更新；只写 data 里出现的字段，返回值与是否新建。"""
        row = cls.of(school_year)
        created = row is None
        if created:
            row = cls(school_year=school_year)
            db.session.add(row)
        for k, v in data.items():
            if hasattr(row, k) and k != 'id':
                setattr(row, k, v)
        row.updated_by = user_id
        row.updated_at = datetime.now()
        return row, created

# StuLink v1.19.70.64 2026-10-20
# 教育统计：页面（表格可复制粘贴）+ 校舍资产录入
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教育统计页面与校舍资产录入（系统管理 → 教育统计 / 校舍资产）。

数据全部来自既有业务表（students / teachers / student_accommodation），
校舍与办学条件参数按学年人工录入、逐年复用。

2026-10-20 用户口径：**不做 Excel 导出** —— 页面表格与样表格式一致，
直接框选/一键复制后粘贴进 Excel 即可（合并表头也随之带过去）。
"""
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import EduStatAsset
from app.modules.edustat import bp
from app.modules.edustat.services import edu_stat_service as stat_service
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

PERM = 'system.users'          # 与「系统概览 / 回收站」同权限：管理员或受授权组

# 校舍资产可写字段（白名单，避免任意属性注入）
_ASSET_FLOAT_FIELDS = (
    'building_total', 'building_c_danger', 'building_d_danger', 'building_rented',
    'room_teaching', 'room_classroom', 'room_special', 'room_lab',
    'room_special_other', 'room_public', 'room_library', 'room_sports_indoor',
    'room_psych', 'room_public_other', 'room_office', 'room_teacher_office',
    'room_office_other', 'room_living', 'room_staff_dorm',
    'room_teacher_turnover', 'room_student_dorm', 'room_canteen', 'room_toilet',
    'room_living_other', 'room_other', 'room_lent_out',
    'land_area', 'green_area', 'sports_field_area',
    'asset_fixed', 'asset_teaching_equipment',
)
_ASSET_INT_FIELDS = (
    'football_11', 'football_7', 'football_5', 'books', 'terminal_total',
    'terminal_teacher', 'terminal_student', 'classroom_total',
    'classroom_multimedia',
)
_ASSET_BOOL_FIELDS = (
    'ok_sports_venue', 'ok_sports_equipment', 'ok_music_equipment',
    'ok_art_equipment', 'ok_science_lab',
)


def _year_from_args():
    """学年（起始年）：?year=2026；非法/缺省取当前年。"""
    return stat_service.normalize_year(request.args.get('year'))


def _parse_float(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _parse_int(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _parse_bool(raw):
    raw = (raw or '').strip()
    if raw in ('1', 'true', '是', 'True'):
        return True
    if raw in ('0', 'false', '否', 'False'):
        return False
    return None


@bp.route('/edu-stat')
@login_required
@perm_required(PERM)
def edu_stat_page():
    """教育统计：学生 4 张表 + 教师 3 张表（与样表同格式，可直接复制粘贴）。"""
    year = _year_from_args()
    asset = EduStatAsset.of(stat_service.year_label(year))
    data = stat_service.collect(year, asset)
    return render_template('system/edu_stat.html', d=data, year=year,
                           school_year=data['school_year'])


@bp.route('/edu-assets', methods=['GET', 'POST'])
@login_required
@perm_required(PERM)
def edu_assets():
    """校舍资产（教基5170/5176/1102 参数）：按学年录入，一次填好逐年复用。"""
    if request.method == 'POST':
        label = stat_service.year_label(request.form.get('school_year'))
        data = {}
        for f in _ASSET_FLOAT_FIELDS:
            data[f] = _parse_float(request.form.get(f))
        for f in _ASSET_INT_FIELDS:
            data[f] = _parse_int(request.form.get(f))
        for f in _ASSET_BOOL_FIELDS:
            data[f] = _parse_bool(request.form.get(f))
        data['note'] = (request.form.get('note') or '').strip()[:200]
        row, created = EduStatAsset.upsert(label, data, user_id=current_user.id)
        db.session.commit()
        log_operation(current_user, '新建' if created else '更新', '校舍资产',
                      row.id, f'{label} 学年办学条件参数', module='system')
        flash(f'{label} 学年校舍资产已{"创建" if created else "保存"}', 'success')
        return redirect(url_for('edustat.edu_assets', year=label[:4]))

    year = _year_from_args()
    label = stat_service.year_label(year)
    asset = EduStatAsset.of(label)
    # 模板用「字段名 → 值」字典取值（不依赖 Jinja 的 attr 过滤器版本）
    values = {}
    for f in (_ASSET_FLOAT_FIELDS + _ASSET_INT_FIELDS + _ASSET_BOOL_FIELDS):
        values[f] = getattr(asset, f, None) if asset else None
    labels = [r.school_year for r in
              EduStatAsset.query.order_by(EduStatAsset.school_year.desc()).all()]
    return render_template('system/edu_assets.html', asset=asset, values=values,
                           year=year, school_year=label, years=labels)

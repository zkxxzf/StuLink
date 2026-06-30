# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required
from app.extensions import db
from app.models import DictCategory, DictItem
from app.utils.decorators import role_required
from app.utils.helpers import is_dict_value_in_use, clear_dict_cache

bp = Blueprint('dictionary', __name__, url_prefix='/dictionary')

# 当前管理的字典分�?
MANAGED_CATEGORIES = ['grade', 'class', 'building', 'floor', 'boarding_type', 'enrollment_status', 'day_student_type', 'subject_direction', 'class_type']


@bp.route('/')
@role_required('admin')
def list_items():
    """字典管理主页"""
    current_tab = request.args.get('tab', 'grade')
    if current_tab not in MANAGED_CATEGORIES:
        current_tab = 'grade'

    categories = DictCategory.query.filter(
        DictCategory.code.in_(MANAGED_CATEGORIES)
    ).all()

    # 保持标签页顺序一�?
    cat_order = {code: i for i, code in enumerate(MANAGED_CATEGORIES)}
    categories.sort(key=lambda c: cat_order.get(c.code, 99))

    # 获取当前标签页的字典�?
    current_cat = DictCategory.query.filter_by(code=current_tab).first()
    items = []
    if current_cat:
        raw_items = current_cat.items.order_by(DictItem.sort_order).all()
        for item in raw_items:
            items.append({
                'id': item.id,
                'value': item.value,
                'sort_order': item.sort_order,
                'is_active': item.is_active,
                'in_use': is_dict_value_in_use(current_tab, item.value),
            })

    return render_template('system/dictionary/list.html',
                           categories=categories,
                           current_tab=current_tab,
                           items=items)


@bp.route('/add', methods=['POST'])
@role_required('admin')
def add_item():
    """添加字典�?""
    category_code = request.form.get('category_code', '').strip()
    value = request.form.get('value', '').strip()

    if not value:
        flash('值不能为�?, 'danger')
        return redirect(url_for('dictionary.list_items', tab=category_code))

    cat = DictCategory.query.filter_by(code=category_code).first()
    if not cat:
        flash('字典分类不存�?, 'danger')
        return redirect(url_for('dictionary.list_items'))

    # 检查唯一�?
    existing = DictItem.query.filter_by(category_id=cat.id, value=value).first()
    if existing:
        flash(f'"{value}" 已存�?, 'warning')
        return redirect(url_for('dictionary.list_items', tab=category_code))

    # 自动排序�?
    max_order = db.session.query(db.func.max(DictItem.sort_order)).filter_by(
        category_id=cat.id).scalar() or 0
    item = DictItem(category_id=cat.id, value=value, sort_order=max_order + 1)
    db.session.add(item)
    db.session.commit()
    
    # 清除字典缓存
    clear_dict_cache()
    
    flash(f'已添�?"{value}"', 'success')
    return redirect(url_for('dictionary.list_items', tab=category_code))


@bp.route('/<int:item_id>/delete', methods=['POST'])
@role_required('admin')
def delete_item(item_id):
    """删除字典值（仅未使用的可以删除）"""
    item = DictItem.query.get_or_404(item_id)
    cat_code = item.category.code

    if is_dict_value_in_use(cat_code, item.value):
        flash(f'"{item.value}" 已被使用，无法删�?, 'danger')
        return redirect(url_for('dictionary.list_items', tab=cat_code))

    db.session.delete(item)
    db.session.commit()
    
    # 清除字典缓存
    clear_dict_cache()
    
    flash(f'已删�?"{item.value}"', 'success')
    return redirect(url_for('dictionary.list_items', tab=cat_code))

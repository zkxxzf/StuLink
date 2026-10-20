# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from app.extensions import db
from app.models import DictCategory, DictItem
from app.utils.decorators import perm_required
from app.utils.helpers import is_dict_value_in_use, clear_dict_cache

bp = Blueprint('dictionary', __name__, url_prefix='/dictionary')

# 当前管理的字典分类
MANAGED_CATEGORIES = ['grade', 'class', 'building', 'floor', 'boarding_type', 'enrollment_status', 'day_student_type', 'subject_direction', 'class_type']


@bp.route('/')
@perm_required('system.dictionary')
def list_items():
    """字典管理主页"""
    current_tab = request.args.get('tab', 'grade')
    if current_tab not in MANAGED_CATEGORIES:
        current_tab = 'grade'

    categories = DictCategory.query.filter(
        DictCategory.code.in_(MANAGED_CATEGORIES)
    ).all()

    # 保持标签页顺序一致
    cat_order = {code: i for i, code in enumerate(MANAGED_CATEGORIES)}
    categories.sort(key=lambda c: cat_order.get(c.code, 99))

    # 获取当前标签页的字典项
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
@perm_required('system.dictionary')
def add_item():
    """添加字典值"""
    category_code = request.form.get('category_code', '').strip()
    value = request.form.get('value', '').strip()

    if not value:
        flash('值不能为空', 'danger')
        return redirect(url_for('dictionary.list_items', tab=category_code))
    # 2026-10-19 第十三轮审计：只允许本页管理的 9 个分类（原可往任意
    # DictCategory 塞值，例如选科/权限相关分类）
    if category_code not in MANAGED_CATEGORIES:
        flash('该字典分类不在管理范围内', 'danger')
        return redirect(url_for('dictionary.list_items'))

    cat = DictCategory.query.filter_by(code=category_code).first()
    if not cat:
        flash('字典分类不存在', 'danger')
        return redirect(url_for('dictionary.list_items'))

    # 检查唯一性
    existing = DictItem.query.filter_by(category_id=cat.id, value=value).first()
    if existing:
        flash(f'"{value}" 已存在', 'warning')
        return redirect(url_for('dictionary.list_items', tab=category_code))

    # 自动排序号
    max_order = db.session.query(db.func.max(DictItem.sort_order)).filter_by(
        category_id=cat.id).scalar() or 0
    item = DictItem(category_id=cat.id, value=value, sort_order=max_order + 1)
    db.session.add(item)
    db.session.commit()
    
    # 清除字典缓存
    clear_dict_cache()
    # 2026-10-19 第十四轮：字典新增补审计
    from app.utils.helpers import log_operation
    log_operation(current_user, '新增', '字典项', item.id,
                  f'{category_code} · {value}', module='system')
    
    flash(f'已添加 "{value}"', 'success')
    return redirect(url_for('dictionary.list_items', tab=category_code))


@bp.route('/<int:item_id>/delete', methods=['POST'])
@perm_required('system.dictionary')
def delete_item(item_id):
    """删除字典值（仅未使用的可以删除）"""
    item = DictItem.query.get_or_404(item_id)
    # 2026-10-18：category 缺失（脏外键）时不要 AttributeError → 500
    cat_code = (item.category.code if item.category else '')
    # 2026-10-19 第十三轮审计：同 add_item，只允许删本页管理的分类
    if cat_code and cat_code not in MANAGED_CATEGORIES:
        flash('该字典分类不在管理范围内', 'danger')
        return redirect(url_for('dictionary.list_items'))

    if is_dict_value_in_use(cat_code, item.value):
        flash(f'"{item.value}" 已被使用，无法删除', 'danger')
        return redirect(url_for('dictionary.list_items', tab=cat_code))

    # 2026-10-18（口径）：字典项删除接回收站 —— 误删会让下拉框缺项，必须可恢复
    from app.utils.trash import archive
    archive('system', 'dictionary_item', '字典项', item,
            summary=f'{item.category.name if item.category else cat_code} · {item.value}',
            operator=current_user)
    # 归档先提交、再删除：非 WAL 模式下 SQLite 是**库级锁**，同一事务里
    # "先读后写 + 再删" 容易触发读锁升级失败（database is locked）。
    # 拆成两个事务后，与 students.delete 的做法保持一致。
    db.session.commit()
    db.session.delete(item)
    db.session.commit()

    # 清除字典缓存
    clear_dict_cache()
    # 2026-10-19 第十四轮：字典增删补审计（下拉框数据源，误改影响全局）
    from app.utils.helpers import log_operation
    log_operation(current_user, '删除', '字典项', item_id,
                  f'{cat_code} · {item.value}', module='system')

    flash(f'已删除 "{item.value}"（已进回收站，可恢复）', 'success')
    return redirect(url_for('dictionary.list_items', tab=cat_code))



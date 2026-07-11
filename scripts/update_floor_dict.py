"""更新楼层字典：从'X 楼'改为'X 层'"""
import os
import sys

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import DictCategory, DictItem

app = create_app()

with app.app_context():
    # 获取 floor 分类
    cat = DictCategory.query.filter_by(code='floor').first()
    
    if not cat:
        print('楼层分类不存在')
        sys.exit(1)
    
    # 删除所有旧的楼层项
    old_items = DictItem.query.filter_by(category_id=cat.id).all()
    for item in old_items:
        db.session.delete(item)
    
    # 添加新的楼层项
    new_floors = ['1 层', '2 层', '3 层', '4 层', '5 层', '6 层']
    for i, floor in enumerate(new_floors):
        item = DictItem(category_id=cat.id, value=floor, sort_order=i+1)
        db.session.add(item)
        print(f'添加：{floor}')
    
    db.session.commit()
    print('\n楼层字典已更新完成！')

# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0



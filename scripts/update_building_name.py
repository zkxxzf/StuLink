"""更新宿舍楼名称：男生宿舍楼->西宿舍楼，女生宿舍楼->东宿舍楼"""
import os
import sys

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import DictCategory, DictItem, Room

app = create_app()

with app.app_context():
    # 1. 更新字典表
    cat = DictCategory.query.filter_by(code='building').first()
    
    if cat:
        # 删除旧的宿舍楼项
        old_items = DictItem.query.filter_by(category_id=cat.id).all()
        for item in old_items:
            db.session.delete(item)
        
        # 添加新的宿舍楼项
        new_buildings = ['西宿舍楼', '东宿舍楼']
        for i, building in enumerate(new_buildings):
            item = DictItem(category_id=cat.id, value=building, sort_order=i+1)
            db.session.add(item)
            print(f'字典添加：{building}')
    
    # 2. 更新已有宿舍记录
    rooms_updated = 0
    for room in Room.query.filter_by(is_active=True).all():
        if room.building == '男生宿舍楼':
            room.building = '西宿舍楼'
            rooms_updated += 1
        elif room.building == '女生宿舍楼':
            room.building = '东宿舍楼'
            rooms_updated += 1
    
    db.session.commit()
    
    print(f'\n宿舍楼名称已更新完成！')
    print(f'更新了 {rooms_updated} 间宿舍记录')

# StuLink v1.4.5 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0

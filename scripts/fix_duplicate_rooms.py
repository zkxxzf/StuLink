"""修复重复房间数据 - 删除楼栋名称错误的重复房间"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import Room, BedAssignment

app = create_app()

with app.app_context():
    # 找出所有楼栋名称有"楼楼"的房间
    wrong_rooms = Room.query.filter(Room.building.like('%楼楼%')).all()
    print(f'发现 {len(wrong_rooms)} 间楼栋名称错误的房间')
    
    deleted_count = 0
    for room in wrong_rooms:
        # 修正楼栋名称
        correct_building = room.building.replace('楼楼', '楼')
        
        # 检查是否已存在正确名称的房间
        existing = Room.query.filter_by(
            building=correct_building,
            room_number=room.room_number
        ).first()
        
        if existing:
            # 先删除该房间的所有床位
            BedAssignment.query.filter_by(room_id=room.id).delete()
            # 再删除房间
            db.session.delete(room)
            deleted_count += 1
        else:
            # 如果不存在，则修正名称
            room.building = correct_building
    
    db.session.commit()
    print(f'已删除 {deleted_count} 间重复房间')
    
    # 统计修正后的数据
    total_rooms = Room.query.count()
    active_rooms = Room.query.filter_by(is_active=True).count()
    total_beds = BedAssignment.query.count()
    
    print(f'\n修正后统计:')
    print(f'  总房间数: {total_rooms}')
    print(f'  活跃房间数: {active_rooms}')
    print(f'  总床位数: {total_beds}')
    
    # 按楼栋统计
    from sqlalchemy import func
    buildings = db.session.query(Room.building, func.count(Room.id)).group_by(Room.building).all()
    print(f'\n按楼栋统计:')
    for building, count in buildings:
        print(f'  {building}: {count}间')

# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0



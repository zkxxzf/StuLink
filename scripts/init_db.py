"""初始化数据库：建表 + 创建默认管理员 + 导入字典数据"""
# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
import sys

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, DICT_DATA, ROOM_NUMBERS
from app.extensions import db
from app.models import User, DictCategory, DictItem, Room, BedAssignment

app = create_app()


def init_database():
    with app.app_context():
        # 建表
        db.create_all()
        print('数据库表已创建')

        # 创建默认管理员
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                real_name='系统管理员',
                role='admin',
                must_change_pwd=False,
            )
            # ⚠️ 生产环境请修改默认密码！或者部署后立即通过系统界面修改
            admin.set_password('admin123')
            db.session.add(admin)
            print('默认管理员已创建 (用户名: admin, 密码: admin123) ⚠️ 请立即修改！')

        # 导入字典数据
        for code, (name, values) in DICT_DATA.items():
            cat = DictCategory.query.filter_by(code=code).first()
            if not cat:
                cat = DictCategory(code=code, name=name)
                db.session.add(cat)
                db.session.flush()
                for i, val in enumerate(values):
                    db.session.add(DictItem(category_id=cat.id, value=val, sort_order=i))
                print(f'字典 [{name}] 已导入 {len(values)} 项')

        # 创建宿舍房间 + 预创建床位
        created_rooms = 0
        for raw_number in ROOM_NUMBERS:
            gender = '男' if raw_number.startswith('男') else '女'
            building = '西宿舍楼' if raw_number.startswith('男') else '东宿舍楼'
            room_number = raw_number[1:]  # 去掉性别前缀：男201 -> 201
            floor_num = int(room_number[0])  # 从房间号提取楼层：201 -> 2
            if Room.query.filter_by(building=building, room_number=room_number).first():
                continue
            room = Room(building=building, room_number=room_number, gender=gender,
                        floor=floor_num, capacity=8, is_active=True)
            db.session.add(room)
            db.session.flush()
            # 预创建8个床位
            for bed_num in range(1, 9):
                db.session.add(BedAssignment(room_id=room.id, bed_number=bed_num))
            created_rooms += 1

        if created_rooms:
            print(f'已创建 {created_rooms} 间宿舍（每间8个床位）')

        db.session.commit()
        print('\n数据库初始化完成！')


if __name__ == '__main__':
    init_database()

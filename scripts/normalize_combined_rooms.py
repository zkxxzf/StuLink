# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""
统一合班宿舍数据规范（v1.7.0 增量脚本）

规则（合班宿舍不分主次）：
1. class_name 存完整合班名（多个班用 + 拼接，如 "04班+07班"）
2. combined_class 为兼容字段，自动同步为合班名
3. 只要 class_name 含 '+'（超过一个班）即自动识别为合班宿舍，无需手动设置

历史数据处理：
- class_name 只存主班、合班名在 combined_class → class_name 补全为合班名
- class_name 已含 '+' 但 combined_class 不同步 → combined_class 同步为合班名

用法：python scripts/normalize_combined_rooms.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import Room


def main():
    app = create_app()
    with app.app_context():
        rooms = Room.query.all()
        changed = 0
        for r in rooms:
            cn = (r.class_name or '').strip()
            cc = (r.combined_class or '').strip()
            new_cn = None
            if '+' in cc and '+' not in cn:
                # 历史：class_name 只存主班，完整合班名在 combined_class
                new_cn = cc
            elif '+' in cn and cc != cn:
                # class_name 已含完整合班名，combined_class 未同步
                new_cn = cn
            if new_cn is not None:
                r.class_name = new_cn
                r.combined_class = new_cn
                changed += 1
                print(f'  房间 {r.building} {r.room_number}: class_name="{cn or "-"}" combined_class="{cc or "-"}" -> "{new_cn}"')
        db.session.commit()

        total = Room.query.count()
        combined = sum(1 for r in Room.query.all() if r.is_combined)
        print(f'完成：共 {total} 间宿舍，归一化 {changed} 间，当前合班宿舍 {combined} 间')


if __name__ == '__main__':
    main()

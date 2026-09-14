# v1.12.2 考场库扩容种子：补充房间使总容量覆盖 ~1500 人（幂等，按位置去重）
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.grades import AffairRoomLib

# (位置, 容量, 备注)
ROOMS = []
# A 栋 104~306（每层 3 间，30 座）
for floor in (1, 2, 3):
    for no in (4, 5, 6):
        ROOMS.append((f'教学楼A-{floor}0{no}', 30, f'{floor}楼'))
# B 栋 103~305（每层 3 间，40 座）
for floor in (1, 2, 3):
    for no in (3, 4, 5):
        ROOMS.append((f'教学楼B-{floor}0{no}', 40, f'{floor}楼'))
# 大阶梯教室
ROOMS.append(('阶梯教室-4', 100, '大阶梯'))
ROOMS.append(('阶梯教室-5', 100, '大阶梯'))


def main():
    app = create_app()
    with app.app_context():
        added = 0
        for loc, cap, note in ROOMS:
            if not AffairRoomLib.query.filter_by(location=loc).first():
                db.session.add(AffairRoomLib(location=loc, capacity=cap, note=note))
                added += 1
        db.session.commit()
        total = AffairRoomLib.query.count()
        cap_sum = sum(r.capacity or 0 for r in AffairRoomLib.query.all())
        print(f'新增 {added} 个房间，考场库共 {total} 个房间，总容量 {cap_sum} 座。')


if __name__ == '__main__':
    main()

# v1.12.2 考场编号补零迁移：把纯数字的 room_no（1、2…）统一为两位（01、02…）
# 同步更新 AffairRoom 与 AffairStudent 中引用的 room_no，保证编排/桌签一致。
# 幂等：已补零的记录再次执行不受影响。
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.grades import AffairRoom, AffairStudent


def pad(no):
    s = str(no).strip()
    return s.zfill(2) if s.isdigit() else s


def main():
    app = create_app()
    with app.app_context():
        rooms = AffairRoom.query.all()
        # 每个批次内：旧编号 -> 新编号 的映射，供同步学生引用
        changed = 0
        maps = {}  # {affair_id: {old: new}}
        for r in rooms:
            new_no = pad(r.room_no)
            if new_no != r.room_no:
                maps.setdefault(r.affair_id, {})[r.room_no] = new_no
                r.room_no = new_no
                changed += 1
        db.session.flush()

        stu_fixed = 0
        for aid, m in maps.items():
            for old, new in m.items():
                n = AffairStudent.query.filter_by(affair_id=aid, room_no=old).update(
                    {AffairStudent.room_no: new}, synchronize_session=False)
                n2 = AffairStudent.query.filter_by(affair_id=aid, fixed_room=old).update(
                    {AffairStudent.fixed_room: new}, synchronize_session=False)
                stu_fixed += (n or 0) + (n2 or 0)
        db.session.commit()
        print(f'考场编号补零：更新 {changed} 个考场，同步 {stu_fixed} 条学生引用。')


if __name__ == '__main__':
    main()

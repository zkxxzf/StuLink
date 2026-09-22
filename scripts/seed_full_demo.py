# -*- coding: utf-8 -*-
"""全模块大规模演示数据生成器（学生 / 宿舍床位 / 积分 / 成绩一体）

一次生成：
  - N 名学生（默认 8000），编入若干教学班（每班 50 人，物理/历史方向各半）
  - 教师账号 + 任课映射（复用 seed_large_demo）
  - 宿舍床位分配：按性别把住校生铺进床位（房间不足自动扩建南/北宿舍楼）
  - M 场考试（默认 25），每场全体学生 6 科成绩 + 总分，自动排名与划线
  - 每名学生若干条积分加减分记录（纪律/学习/卫生/活动/其他）

用法：
  python scripts/seed_full_demo.py                          # 8000 人 / 25 场
  python scripts/seed_full_demo.py --students 200 --exams 3 # 快速试跑
  python scripts/seed_full_demo.py --clean                  # 仅清空演示数据

说明：幂等——重复执行会先清空本年级演示数据再重建。
"""
import argparse
import os
import random
import sys
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, SCRIPT_DIR)

# 复用已验证的「学生 + 教师 + 考试 + 成绩 + 划线」逻辑
from seed_large_demo import (  # noqa: E402
    GRADE, PWD, CLASS_SIZE,
    build_students, build_teachers, build_exams, build_bands,
    clean_demo_data,
)
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User, Student, Room, BedAssignment, StudentAccommodation, PointRecord  # noqa: E402
from app.models.grades import subjects_of_selection  # noqa: E402

app = create_app()

# 宿舍容量与床位上限
BED_PER_ROOM = 8
BOARDING_RATIO = 0.85  # 住校比例

POINT_CATS = {
    '纪律': ['迟到', '课堂违纪', '仪容仪表不规范', '午休秩序好', '课间操认真'],
    '学习': ['作业优秀', '考试进步', '竞赛获奖', '早读认真', '笔记工整'],
    '卫生': ['宿舍卫生优秀', '值日认真', '内务整洁', '公共区保洁'],
    '活动': ['运动会获奖', '志愿服务', '文艺表演', '社团活跃', '主题班会积极'],
    '其他': ['拾金不昧', '帮助同学', '好人好事', '文明礼貌'],
}
POINT_VALUES = [5, 3, 2, 1, -1, -2, -3, -5]


def _ensure_rooms(gender, needed):
    """确保某性别可用床位 >= needed，不足则自动扩建南/北宿舍楼"""
    existing = Room.query.filter_by(gender=gender, is_active=True).all()
    have = sum(r.capacity for r in existing)
    if have >= needed:
        return 0
    deficit = needed - have
    n_new = (deficit + BED_PER_ROOM - 1) // BED_PER_ROOM
    building = '南宿舍楼' if gender == '男' else '北宿舍楼'
    created = 0
    for i in range(n_new):
        rn = f'{101 + i}'
        room = Room(building=building, room_number=rn, gender=gender,
                    floor=int(rn[0]), capacity=BED_PER_ROOM, is_active=True,
                    grade=GRADE)
        db.session.add(room)
        db.session.flush()
        for b in range(1, BED_PER_ROOM + 1):
            db.session.add(BedAssignment(room_id=room.id, bed_number=b))
        created += 1
    db.session.commit()
    return created


def build_accommodation_and_dorm(students, rnd):
    """为每名学生建住宿档案；住校生铺进床位（按性别）"""
    # 先决定住校/走读并建立住宿档案
    boarders = []
    for s in students:
        is_boarder = rnd.random() < BOARDING_RATIO
        if is_boarder:
            boarding, day_type = '住校', None
            boarders.append(s)
        else:
            boarding = '走读'
            day_type = rnd.choice(['午晚走读', '晚走读'])
        acc = StudentAccommodation(student_id=s.id, boarding_type=boarding,
                                   day_student_type=day_type, textbook='领')
        db.session.add(acc)
    db.session.commit()

    by_gender = {'男': [], '女': []}
    for s in boarders:
        by_gender.setdefault(s.gender, []).append(s)

    total_assigned = 0
    for gender, slist in by_gender.items():
        _ensure_rooms(gender, len(slist))
        rooms = Room.query.filter_by(gender=gender, is_active=True) \
            .order_by(Room.id).all()
        # 预载每个房间的床位，记录已用光标
        room_beds = {}
        for room in rooms:
            beds = BedAssignment.query.filter_by(room_id=room.id) \
                .order_by(BedAssignment.bed_number).all()
            room_beds[room.id] = [room, beds, 0]
        r_idx = 0
        for s in slist:
            placed = False
            while r_idx < len(rooms):
                room, beds, idx = room_beds[rooms[r_idx].id]
                if idx < len(beds):
                    beds[idx].student_id = s.id
                    room.grade = GRADE
                    if not room.class_name:
                        room.class_name = s.class_name
                    room_beds[rooms[r_idx].id][2] = idx + 1
                    placed = True
                    break
                r_idx += 1
            if placed:
                total_assigned += 1
            else:
                break  # 房间真的用尽（理论上 _ensure_rooms 已兜底）
    db.session.commit()
    return len(boarders), total_assigned


def build_points(students, rnd, admin_id):
    """为每名学生生成若干条积分加减分记录"""
    rows = []
    for s in students:
        n = rnd.randint(0, 5)
        for _ in range(n):
            cat = rnd.choice(list(POINT_CATS))
            reason = rnd.choice(POINT_CATS[cat])
            pts = rnd.choice(POINT_VALUES)
            d = date(2026, 9, 1) - timedelta(days=rnd.randint(0, 150))
            rows.append(PointRecord(
                student_no=s.student_number, student_name=s.name,
                grade=s.grade, class_name=s.class_name,
                points=pts, category=cat, reason=reason,
                recorded_at=d, operator_id=admin_id,
                operator_name='系统管理员'))
    db.session.bulk_save_objects(rows)
    db.session.commit()
    return len(rows)


def clean_full():
    """清空本年级全部演示数据（含宿舍床位/积分）"""
    StudentAccommodation.query.delete()
    PointRecord.query.filter_by(grade=GRADE).delete()
    for b in BedAssignment.query.all():
        b.student_id = None
    db.session.commit()
    Room.query.filter(Room.building.in_(['南宿舍楼', '北宿舍楼'])).delete()
    db.session.commit()
    n_s, n_e, n_r = clean_demo_data()
    return n_s, n_e, n_r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--students', type=int, default=8000)
    ap.add_argument('--exams', type=int, default=25)
    ap.add_argument('--clean', action='store_true', help='仅清空演示数据')
    ap.add_argument('--seed', type=int, default=20260913)
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    with app.app_context():
        admin = User.query.filter_by(username='admin').first()
        admin_id = admin.id if admin else None

        if args.clean:
            n_s, n_e, n_r = clean_full()
            print(f'已清空：学生 {n_s}、考试 {n_e}、成绩/划线行 {n_r}（床位已释放）')
            return 0

        print(f'目标：{GRADE} {args.students} 名学生 / {args.exams} 场考试（全模块）')
        n_s, n_e, n_r = clean_full()
        print(f'清理旧演示数据：学生 {n_s}、考试 {n_e}、行 {n_r}')

        students, n_class = build_students(args.students, rnd)
        print(f'学生 {len(students)} 人，{n_class} 个班')

        n_link = build_teachers(students)
        print(f'任课映射 {n_link} 条')

        n_boarders, n_assigned = build_accommodation_and_dorm(students, rnd)
        print(f'住宿档案 {n_boarders} 人，已分配床位 {n_assigned} 个')

        created = build_exams(students, args.exams, rnd)
        n_band = build_bands([c[0] for c in created])
        print(f'考试 {len(created)} 场 / 划线 {n_band} 行')

        n_pts = build_points(students, rnd, admin_id)
        print(f'积分记录 {n_pts} 条')

        print('\n完成！访问 http://localhost:5000 '
              f'(学生 {len(students)} / 床位 {n_assigned} / 积分 {n_pts} / '
              f'考试 {len(created)})')
    return 0


if __name__ == '__main__':
    sys.exit(main())

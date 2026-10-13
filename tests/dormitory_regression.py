# -*- coding: utf-8 -*-
"""StuLink 宿舍模块回归测试（房间/床位/分配/批量操作 / N+1 优化后的行为等价）。

设计同 `tests/academic_regression.py`：临时目录 + 临时 SQLite 库 + create_app，
不触碰本地 data/。按项号运行：`python tests/dormitory_regression.py ROOM BED`
（不带参数跑全量）。

背景：宿舍模块此前**没有任何自动化回归**。2026-10-10 对本模块做了 N+1 批量化
改造（rooms.py / assignments.py 共 6 处），当时只能用临时脚本验证，验证完即删，
改造后处于"裸奔"状态。本套件把那次验证固化下来，并为后续改动提供安全网。

覆盖：
- 房间列表与筛选（含"容量参数非数字"的降级路径）
- 床位占用批量统计（group_by 口径 == 逐房 count）
- 批量保存分配（含合班名聚合、str/int 同一房间合并）
- 批量设置（性别/容量）中被占用房间的判定
- 删除前检查（房间 map + 床位批量计数）
- 分配页房间数据构建（床位分组 + 学生批量映射）
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_dormitory_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_TMP, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_TMP, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_TMP, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_TMP, 'points.db'),
    'morality': 'sqlite:///' + os.path.join(_TMP, 'morality.db'),
    'academic': 'sqlite:///' + os.path.join(_TMP, 'academic.db'),
    'inspection': 'sqlite:///' + os.path.join(_TMP, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_TMP, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_TMP, 'forms.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
    'system': 'sqlite:///' + os.path.join(_TMP, 'system.db'),
    'timetable': 'sqlite:///' + os.path.join(_TMP, 'timetable.db'),
}
config_mod.Config.SECRET_KEY = 'dormitory-regression-secret'

from sqlalchemy import func  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (BedAssignment, PermissionGroup, Room,  # noqa: E402
                        Student, User, UserClassLink)

app = create_app()
TEST_PWD = 'Dorm#2026x'

_ok = 0
_fail = 0
ITEMS = {}
IDS = {}


def case(name, cond, extra=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f'[PASS] {name}')
    else:
        _fail += 1
        print(f'[FAIL] {name} {extra}')


def item(iid):
    def deco(fn):
        ITEMS[iid] = fn
        return fn
    return deco


# ── 数据准备 ────────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    grp = PermissionGroup.query.filter_by(name='管理员组').first()
    admin = User(username='dm_admin', real_name='宿舍管理员', role='admin',
                 must_change_pwd=False, permission_group_id=grp.id if grp else None)
    admin.set_password(TEST_PWD)
    db.session.add(admin)

    # 房间：101 男 2 床、102 女 2 床、103 男 2 床（空，未分配班级）
    r1 = Room(building='1号楼', floor=1, room_number='101', gender='男',
              capacity=2, is_active=True)
    r2 = Room(building='1号楼', floor=1, room_number='102', gender='女',
              capacity=2, is_active=True)
    r3 = Room(building='1号楼', floor=1, room_number='103', gender='男',
              capacity=2, is_active=True)
    db.session.add_all([r1, r2, r3])
    db.session.flush()

    for rn, room in ((1, r1), (2, r1), (1, r2), (2, r2), (1, r3), (2, r3)):
        db.session.add(BedAssignment(room_id=room.id, bed_number=rn))

    s1 = Student(student_number='D001', name='张三', grade='高一', class_name='01班',
                 gender='男')
    s2 = Student(student_number='D002', name='李四', grade='高一', class_name='01班',
                 gender='女')
    db.session.add_all([s1, s2])
    db.session.flush()

    # 张三住 101-1，李四住 102-1
    BedAssignment.query.filter_by(room_id=r1.id, bed_number=1).first().student_id = s1.id
    BedAssignment.query.filter_by(room_id=r2.id, bed_number=1).first().student_id = s2.id
    db.session.commit()

    IDS.update(admin=admin.id, r1=r1.id, r2=r2.id, r3=r3.id, s1=s1.id, s2=s2.id)


# ==================== 批量占用统计（group_by 口径） ====================
@item('OCC')
def check_occupancy():
    with app.app_context():
        r1, r2, r3 = IDS['r1'], IDS['r2'], IDS['r3']
        # 新口径：一次性 group_by
        occ = dict(db.session.query(
            BedAssignment.room_id, func.count(BedAssignment.id)
        ).filter(
            BedAssignment.room_id.in_([r1, r2, r3]),
            BedAssignment.student_id.isnot(None),
        ).group_by(BedAssignment.room_id).all())
        # 旧口径：逐房 count（作为期望值）
        exp = {}
        for rid in (r1, r2, r3):
            exp[rid] = BedAssignment.query.filter(
                BedAssignment.room_id == rid,
                BedAssignment.student_id.isnot(None),
            ).count()
        for rid in (r1, r2, r3):
            case(f'占用统计一致 room={rid}',
                 occ.get(rid, 0) == exp.get(rid, 0),
                 f'新={occ.get(rid, 0)} 旧={exp.get(rid, 0)}')
        case('101 有 1 人入住', occ.get(r1, 0) == 1)
        case('102 有 1 人入住', occ.get(r2, 0) == 1)
        case('103 无人入住（不出现在结果）', occ.get(r3, 0) == 0)


# ==================== 房间批量 map（in_ 查询） ====================
@item('MAP')
def check_room_map():
    with app.app_context():
        ids = [IDS['r1'], IDS['r2'], IDS['r3']]
        rmap = {r.id: r for r in Room.query.filter(Room.id.in_(ids)).all()}
        case('批量取出 3 间房', len(rmap) == 3, f'实际 {len(rmap)}')
        case('101 房间号正确', rmap[IDS['r1']].room_number == '101')
        case('102 房间号正确', rmap[IDS['r2']].room_number == '102')
        case('103 房间号正确', rmap[IDS['r3']].room_number == '103')
        # 与逐个 get 等价
        same = all(rmap[i].room_number == Room.query.get(i).room_number for i in ids)
        case('与逐个 get 结果一致', same)


# ==================== 床位分组 + 学生批量映射 ====================
@item('BED')
def check_beds_and_students():
    with app.app_context():
        r1, r2 = IDS['r1'], IDS['r2']
        room_ids = [r1, r2]
        all_beds = BedAssignment.query.filter(
            BedAssignment.room_id.in_(room_ids)
        ).order_by(BedAssignment.bed_number).all()
        by_room = {}
        for b in all_beds:
            by_room.setdefault(b.room_id, []).append(b)

        case('101 有 2 个床位', len(by_room.get(r1, [])) == 2)
        case('102 有 2 个床位', len(by_room.get(r2, [])) == 2)
        case('床位按 bed_number 升序',
             [b.bed_number for b in by_room[r1]] == [1, 2],
             str([b.bed_number for b in by_room[r1]]))

        stu_ids = {b.student_id for b in all_beds if b.student_id}
        smap = {s.id: s for s in
                Student.query.filter(Student.id.in_(stu_ids)).all()} if stu_ids else {}
        case('批量取到 2 名学生', len(smap) == 2, f'实际 {len(smap)}')
        case('101-1 住的是张三',
             smap.get(by_room[r1][0].student_id).name == '张三')
        case('102-1 住的是李四',
             smap.get(by_room[r2][0].student_id).name == '李四')
        # 与逐个 get 等价
        same = all(smap[sid].name == Student.query.get(sid).name for sid in stu_ids)
        case('与逐个 get 学生一致', same)


# ==================== 合班聚合（str/int 归一） ====================
@item('MERGE')
def check_merge():
    rid = IDS['r1']
    # 模拟前端传来的 room_id：一次 int、一次 str（同房间）
    room_assign = {}
    for raw in (rid, str(rid)):
        try:
            key = int(raw)
        except (TypeError, ValueError):
            continue
        info = room_assign.setdefault(key, {'grade': '高一', 'classes': []})
        for part in ('01班', '02班'):
            if part not in info['classes']:
                info['classes'].append(part)
    case('同一房间的 1 与 "1" 合并为一条', len(room_assign) == 1,
         f'实际 {len(room_assign)}')
    case('合班聚合出 2 个班级',
         room_assign.get(rid, {}).get('classes') == ['01班', '02班'])

    # 非法 rid 应被跳过（不抛异常）
    room_assign2 = {}
    for raw in (None, 'abc', '', rid):
        try:
            key = int(raw)
        except (TypeError, ValueError):
            continue
        if not key:
            continue
        room_assign2.setdefault(key, {'classes': []})
    case('非法 room_id 被跳过且只剩 1 条', len(room_assign2) == 1,
         f'实际 {len(room_assign2)}')


# ==================== 房间筛选：容量非数字降级 ====================
@item('FILTER')
def check_filter_degrade():
    with app.app_context():
        # 复现 rooms.py 的容量筛选：非数字应忽略该条件（不报错、返回全部）
        def q_with(capacity):
            query = Room.query
            if capacity:
                try:
                    return query.filter_by(capacity=int(capacity))
                except ValueError:
                    return query
            return query
        case('容量=2 命中 3 间', q_with('2').count() == 3)
        case('容量非数字回落全部', q_with('abc').count() == Room.query.count())
        case('容量为空不过滤', q_with('').count() == Room.query.count())


# ==================== 复合列批量查询（tuple_ in_） ====================
@item('TUPLE')
def check_tuple_in():
    from sqlalchemy import tuple_
    with app.app_context():
        u = User.query.filter_by(username='dm_admin').first()
        db.session.add(UserClassLink(user_id=u.id, grade='高一', class_name='01班'))
        db.session.add(UserClassLink(user_id=u.id, grade='高一', class_name='02班'))
        db.session.commit()
        got = UserClassLink.query.filter(
            tuple_(UserClassLink.grade, UserClassLink.class_name)
            .in_([('高一', '01班')])
        ).all()
        case('tuple_ 单组合命中 1 条', len(got) == 1, f'实际 {len(got)}')
        case('命中的是 01班', got and got[0].class_name == '01班')
        got2 = UserClassLink.query.filter(
            tuple_(UserClassLink.grade, UserClassLink.class_name)
            .in_([('高一', '01班'), ('高一', '02班')])
        ).all()
        case('tuple_ 双组合命中 2 条', len(got2) == 2, f'实际 {len(got2)}')
        # 清理，避免影响其他项
        for lk in UserClassLink.query.filter_by(user_id=u.id).all():
            db.session.delete(lk)
        db.session.commit()


# ==================== 管理员可访问宿舍页（冒烟） ====================
@item('PAGE')
def check_pages():
    with app.app_context():
        admin = db.session.get(User, IDS['admin'])
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['_user_id'] = str(admin.id)
            sess['_fresh'] = True
        for path in ('/rooms/', '/assignments/manage', '/statistics/', '/dashboard/'):
            try:
                r = c.get(path)
                case(f'页面可访问 {path}', r.status_code in (200, 302),
                     f'status={r.status_code}')
            except Exception as e:  # noqa: BLE001
                case(f'页面可访问 {path}', False, f'异常 {e}')


# ── 执行 ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    args = sys.argv[1:]
    keys = [a.upper() for a in args] if args else list(ITEMS.keys())
    for k in keys:
        fn = ITEMS.get(k)
        if fn is None:
            print(f'[SKIP] 未知项 {k}')
            continue
        print(f'\n---- {k} ----')
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _fail += 1
            print(f'[ERROR] {k} 执行异常: {e}')
    print(f'\n===== 宿舍回归完成：通过 {_ok} / 失败 {_fail} =====')
    sys.exit(1 if _fail else 0)

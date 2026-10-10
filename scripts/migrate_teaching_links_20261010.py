# -*- coding: utf-8 -*-
"""迁移：成绩管理"教师任课映射"按在用班级重导（2026-10-10）。

背景：teacher_subject_links 里有 3840 条映射，其中 3696 条落在"批量导入 / 历史"的
未启用班级上（每届 160 个班那种），只有 144 条属于真正在用的 18 个班。教务读它做
"谁教哪班哪科"时，会算出"每位老师教 160 个班"这种结果。

做法（**先整目录备份，只动 grades.db 的这一张表**）：
1. 备份 data/ → data_backup_20261010/（已存在则跳过，避免重复备份）；
2. 删除落在未启用班级上的映射；
3. 按课表（schedule_entries）推导"谁上这班这科"，补齐缺失的映射
   （teacher_uid → teachers.user_id，48 位教师均已绑定账号）；
4. 打印复核统计。

幂等：重复执行不会产生重复映射（按 user_id+年级+班+学科 去重）。
回滚：把 data_backup_20261010/ 覆盖回 data/ 即可。

用法：python scripts/migrate_teaching_links_20261010.py
"""
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                       # noqa: E402
from app.extensions import db                                    # noqa: E402
from app.modules.academic.services import teaching_scope_service as ts  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, 'data')
BACKUP_DIR = os.path.join(BASE, 'data_backup_20261010')


def backup():
    if os.path.exists(BACKUP_DIR):
        print('[OK] 备份已存在，跳过：', BACKUP_DIR)
        return
    if not os.path.exists(DATA_DIR):
        print('[SKIP] 未找到 data/ 目录：', DATA_DIR)
        return
    shutil.copytree(DATA_DIR, BACKUP_DIR)
    print('[OK] 已备份 data/ →', BACKUP_DIR)


def main():
    app = create_app()
    with app.app_context():
        backup()
        active = set(ts.active_class_pairs())
        print('[INFO] 在用班级：%d 个' % len(active))
        # 安全闸（2026-10-10 生产事故后补）：在用班级为 0 说明"班级启用状态"判定异常
        # （典型：课表尚未导入 → class_profiles 全被判未启用），此时若继续执行会把
        # **全部**任课映射清空（该表是"谁教哪班哪科"的唯一依据）。故一律不删。
        if not active and '--force' not in sys.argv:
            print('[ABORT] 在用班级为 0，判定为异常，已取消删除与补齐（未改动数据）。')
            print('        请先排查班级启用状态（scripts/migrate_class_active_20261009.py）；')
            print('        确需在零在用班级下执行时，显式加 --force。')
            return 1
        eng = db.engines['grades']

        # 1) 删除落在未启用班级上的映射
        with eng.begin() as c:
            rows = c.exec_driver_sql(
                'SELECT id, grade, class_name FROM teacher_subject_links').fetchall()
            drop = [r[0] for r in rows if (r[1], r[2]) not in active]
            for i in range(0, len(drop), 500):
                batch = drop[i:i + 500]
                c.exec_driver_sql(
                    'DELETE FROM teacher_subject_links WHERE id IN (%s)'
                    % ','.join('?' * len(batch)), tuple(batch))
        print('[OK] 删除未启用班级的映射：%d 条' % len(drop))

        # 2) 按课表补齐：谁上这班这科 → 一条映射
        # 注意唯一约束是 (grade, class_name, subject) —— 一个班一个学科只能一条，
        # 所以同班同学科若课表里有多位教师，取课时最多的那位。
        with db.engines['timetable'].begin() as c:
            sched = c.exec_driver_sql(
                'SELECT teacher_uid, grade, class_name, subject, COUNT(*) '
                'FROM schedule_entries WHERE COALESCE(is_deleted,0)=0 '
                'AND teacher_uid IS NOT NULL AND teacher_uid<>"" '
                'GROUP BY teacher_uid, grade, class_name, subject').fetchall()
        with db.engines['academic'].begin() as c:
            tmap = {r[0]: r[1] for r in c.exec_driver_sql(
                'SELECT teacher_uid, user_id FROM teachers WHERE user_id IS NOT NULL')}
        best = {}
        multi = 0
        for uid, g, cn, subj, n in sched:
            u = tmap.get(uid)
            if not u or (g, cn) not in active:
                continue
            key = (g, cn, subj or '')
            cur = best.get(key)
            if cur is None:
                best[key] = (u, n)
            else:
                multi += 1
                if n > cur[1]:
                    best[key] = (u, n)
        with eng.begin() as c:
            have = {(r[0], r[1], r[2]) for r in c.exec_driver_sql(
                'SELECT grade, class_name, subject FROM teacher_subject_links')}
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        to_add = [(g, cn, subj, u)
                  for (g, cn, subj), (u, _n) in best.items()
                  if (g, cn, subj) not in have]
        if multi:
            print('[INFO] 同班同学科有多位教师的：%d 组，按课时最多的那位写入' % multi)
        with eng.begin() as c:
            for g, cn, subj, u in to_add:
                c.exec_driver_sql(
                    'INSERT INTO teacher_subject_links '
                    '(grade, class_name, subject, user_id, active, created_at, updated_at) '
                    'VALUES (?,?,?,?,1,?,?)', (g, cn, subj, u, now, now))
        print('[OK] 按课表新增映射：%d 条' % len(to_add))

        # 3) 复核
        with eng.begin() as c:
            total = c.exec_driver_sql('SELECT COUNT(*) FROM teacher_subject_links').scalar()
            ncls = c.exec_driver_sql(
                'SELECT COUNT(*) FROM (SELECT grade, class_name FROM teacher_subject_links '
                'GROUP BY grade, class_name)').scalar()
            ntea = c.exec_driver_sql(
                'SELECT COUNT(DISTINCT user_id) FROM teacher_subject_links').scalar()
        print('[OK] 复核：映射合计 %d 条 / 覆盖 %d 个班 / %d 位教师' % (total, ncls, ntea))
        print('[INFO] 回滚：把 data_backup_20261010/ 覆盖回 data/ 即可')
        return 0


if __name__ == '__main__':
    sys.exit(main())

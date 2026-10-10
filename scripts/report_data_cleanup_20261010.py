# -*- coding: utf-8 -*-
"""数据清理 / 任课重导 · 清单报告（**只读**，不写库、不删任何数据）。

输出两份清单，供人工过目后再决定是否执行：
1. 成绩管理 teacher_subject_links 按真实在用班级重导：该新增哪些、该删哪些；
2. 历史 / 批量导入的膨胀数据：未启用班级、所属学生、2099级残留账号。

用法：python scripts/report_data_cleanup_20261010.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.modules.academic.services import teaching_scope_service as ts  # noqa: E402


def q(bind, sql, args=None):
    with db.engines[bind].begin() as c:
        return c.exec_driver_sql(sql, args or ()).fetchall()


app = create_app()
with app.app_context():
    active = set(ts.active_class_pairs())
    print('=' * 70)
    print('一、成绩管理任课映射（teacher_subject_links）重导清单')
    print('   在用班级：%d 个' % len(active))

    # 现有映射
    rows = q('grades', 'SELECT id, user_id, grade, class_name, subject, COALESCE(active,1) '
                       'FROM teacher_subject_links')
    print('   现有映射：%d 条' % len(rows))
    keep = [r for r in rows if (r[2], r[3]) in active]
    drop = [r for r in rows if (r[2], r[3]) not in active]
    print('   属于在用班级（保留）：%d 条' % len(keep))
    print('   属于未启用班级（建议清理）：%d 条' % len(drop))

    # 课表能推出的"应任职"清单
    sched = q('timetable', 'SELECT DISTINCT teacher_uid, grade, class_name, subject '
                           'FROM schedule_entries WHERE COALESCE(is_deleted,0)=0 '
                           'AND teacher_uid IS NOT NULL AND teacher_uid<>""')
    tmap = {r[0]: r[1] for r in q('academic', 'SELECT teacher_uid, user_id FROM teachers')}
    should = []
    for uid, g, cn, subj in sched:
        u = tmap.get(uid)
        if u and (g, cn) in active:
            should.append((u, g, cn, subj))
    have = {(r[1], r[2], r[3]) for r in keep}
    add = [s for s in should if (s[0], s[1], s[2]) not in have]
    print('   课表可推出的任课关系：%d 条（其中新增 %d 条）' % (len(should), len(add)))
    print('   新增示例（user_id, 年级, 班, 学科）:', add[:5])
    miss_uid = sorted({uid for uid, _g, _c, _s in sched if not tmap.get(uid)})
    print('   课表里有、但教师档案没绑定登录账号（无法写映射）:', miss_uid[:8])

    print('\n二、膨胀数据清理清单（只读统计，未删除）')
    inactive = q('system', 'SELECT grade, class_name, COUNT(*) FROM class_profiles '
                           'WHERE COALESCE(is_active,1)=0 GROUP BY grade, class_name')
    print('   未启用班级：%d 个' % len(inactive))
    print('   示例：', inactive[:6])
    for grade, n in q('system', 'SELECT grade, COUNT(*) FROM class_profiles '
                                'WHERE COALESCE(is_active,1)=0 GROUP BY grade'):
        print(f'      {grade}: {n} 个班')
    stu = q('system', 'SELECT COUNT(*) FROM students s WHERE NOT EXISTS ('
                      'SELECT 1 FROM class_profiles c WHERE c.grade=s.grade '
                      'AND c.class_name=s.class_name AND COALESCE(c.is_active,1)=1)')
    print('   落在未启用班级里的学生：%d 人（如清理班级档案，这些学生需一并处理）' % stu[0][0])

    weird = q('system', "SELECT grade, COUNT(*) FROM class_profiles "
                        "WHERE grade NOT LIKE '20__级' OR grade LIKE '2099%' GROUP BY grade")
    print('   可疑年级（非 20xx级 / 2099级）：', weird)
    u2099 = q('system', "SELECT role, COUNT(*) FROM users WHERE grade='2099级' GROUP BY role")
    print('   2099级账号：', u2099)

    print('\n三、建议执行顺序（执行前请再确认）')
    print('   1) 备份 data/ 目录；')
    print('   2) 任课映射：删除 %d 条未启用班的映射 → 再按课表补 %d 条；' % (len(drop), len(add)))
    print('   3) 班级档案：未启用班级先保留（已不显示），确认无误后再物理删除；')
    print('   4) 2099级等测试残留单独清理。')

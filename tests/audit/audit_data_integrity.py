
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 D4：跨库孤儿与重复（修正表/列名）"""
import sqlite3


def ids(db, sql):
    c = sqlite3.connect(f'data/{db}.db')
    try:
        return {r[0] for r in c.execute(sql) if r[0] is not None}
    finally:
        c.close()


def rows(db, sql):
    c = sqlite3.connect(f'data/{db}.db')
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


student_ids = ids('system', 'SELECT id FROM students')
student_nos = ids('system', 'SELECT student_number FROM students')
exams = ids('grades', 'SELECT id FROM exams')
users = ids('system', 'SELECT id FROM users')
rooms = ids('dormitory', 'SELECT id FROM rooms')
schedules = ids('timetable', 'SELECT id FROM term_schedules')
entries = ids('timetable', 'SELECT id FROM schedule_entries')
print(f'基数 学生 {len(student_ids)}/学号 {len(student_nos)}｜考试 {len(exams)}｜用户 {len(users)}')

checks = []
sc = rows('grades', 'SELECT id, exam_id, student_no FROM exam_scores')
checks.append(('分数→考试(不存在)', len([r for r in sc if r[1] not in exams]),
               [r for r in sc if r[1] not in exams][:3]))
checks.append(('分数→学号(不存在)', len([r for r in sc if r[2] not in student_nos]),
               [r for r in sc if r[2] not in student_nos][:3]))
dup = rows('grades', 'SELECT exam_id, student_no, subject, COUNT(*) c FROM exam_scores '
                     'GROUP BY exam_id, student_no, subject HAVING c > 1')
checks.append(('分数重复(考试+学号+科目)', len(dup), dup[:3]))

af = ids('grades', 'SELECT id FROM exam_affairs')
for t, label in (('affair_students', '考务名单→批次'), ('affair_rooms', '考务考场→批次')):
    rs = rows('grades', f'SELECT id, affair_id FROM {t}')
    bad = [r for r in rs if r[1] not in af]
    checks.append((f'{label}(不存在)', len(bad), bad[:3]))

beds = rows('dormitory', 'SELECT id, student_id, room_id FROM bed_assignments')
checks.append(('床位→学生(不存在)', len([r for r in beds if r[1] and r[1] not in student_ids]),
               [r for r in beds if r[1] and r[1] not in student_ids][:3]))
checks.append(('床位→房间(不存在)', len([r for r in beds if r[2] not in rooms]),
               [r for r in beds if r[2] not in rooms][:3]))
dup_bed = rows('dormitory', 'SELECT room_id, bed_number, COUNT(*) c FROM bed_assignments '
                            'GROUP BY room_id, bed_number HAVING c > 1')
checks.append(('同房同床重复', len(dup_bed), dup_bed[:3]))

tsl = rows('grades', 'SELECT id, user_id FROM teacher_subject_links')
checks.append(('任课映射→用户(不存在)', len([r for r in tsl if r[1] not in users]),
               [r for r in tsl if r[1] not in users][:3]))
etl = rows('grades', 'SELECT id, exam_id FROM exam_teacher_links')
checks.append(('考试任课→考试(不存在)', len([r for r in etl if r[1] not in exams]),
               [r for r in etl if r[1] not in exams][:3]))

se = rows('timetable', 'SELECT id, term_schedule_id FROM schedule_entries')
checks.append(('课程条目→课表(不存在)', len([r for r in se if r[1] not in schedules]),
               [r for r in se if r[1] not in schedules][:3]))
sw = rows('timetable', 'SELECT id, original_entry_id FROM schedule_entries '
                       'WHERE original_entry_id IS NOT NULL')
checks.append(('原课引用→条目(不存在)', len([r for r in sw if r[1] not in entries]),
               [r for r in sw if r[1] not in entries][:3]))
act = rows('timetable', "SELECT id, name FROM term_schedules WHERE status='active'")
checks.append(('active 学期(>1 异常)', max(0, len(act) - 1), act))

checks.append(('学生无班级', len(rows('system',
               "SELECT id FROM students WHERE class_name IS NULL OR class_name=''")), []))
teachers = ids('system', "SELECT id FROM users WHERE role='teacher'")
bound = {r[0] for r in tsl if r[0]}
checks.append(('任课教师未绑定任课', len(teachers - {r[1] for r in tsl}), []))

print()
bad = 0
for label, n, sample in checks:
    if n:
        bad += 1
        print(f'  !! {label:<28}{n}')
        for s in (sample or [])[:3]:
            print(f'       {s}')
    else:
        print(f'  ok {label:<28}0')
print(f'\n===== 一致性问题 {bad} 类 =====')
"""临时诊断：教师「张真」课表条目 + 节次定义（只读，跑完即删）"""
import os
import sqlite3

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'timetable.db')
con = sqlite3.connect('file:%s?mode=ro' % db.replace('\\', '/'), uri=True)
cur = con.cursor()


def g(sql, *p):
    return cur.execute(sql, p).fetchall()


print('== 学期 ==')
print('  cols:', [c[1] for c in g('pragma table_info(term_schedules)')])
for r in g('select * from term_schedules'):
    print('  ', r)

print('\n== 节次定义 ==')
print('  cols:', [c[1] for c in g('pragma table_info(period_defs)')])
for r in g('select * from period_defs order by term_schedule_id, period_number'):
    print('  ', r)

print('\n== 教师 张真 / TJH8L7F 的条目（按星期+节次）==')
for r in g('select id, term_schedule_id, grade, class_name, weekday, period_number, '
           'week_range, subject, teacher_uid, teacher_name, entry_type, is_deleted '
           'from schedule_entries where teacher_name like ? or teacher_uid like ? '
           'order by weekday, period_number', '%张真%', 'TJH8L7F'):
    print('  ', r)

print('\n== 信息技术 全部条目（看别人怎么排）==')
for r in g('select id, class_name, weekday, period_number, week_range, subject, '
           'teacher_name, teacher_uid from schedule_entries where subject like ? '
           'order by weekday, period_number limit 60', '%信息技术%'):
    print('  ', r)
con.close()

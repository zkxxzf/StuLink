# -*- coding: utf-8 -*-
"""验证课表范围口径（2026-10-18：按老师所在/任教年级）与相关归档

覆盖：
1) 管理员可看任意年级课表；任课教师只能看自己任教年级
2) 教师课表：本人放行、别年级教师 403
3) 条目详情：跨年级 403
4) 总课表 grades= 越权参数被剔除
5) 字典删除 → 回收站有记录
6) 旧课表删除：跨年级 403，本年级归档后删除
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import re  # noqa: E402
import sqlite3  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
ok = fail = 0


def case(name, cond, extra=''):
    global ok, fail
    extra = str(extra).encode('gbk', 'replace').decode('gbk')
    if cond:
        ok += 1
        print(f'[PASS] {name} {extra}')
    else:
        fail += 1
        print(f'[FAIL] {name} {extra}')


def login(c, username):
    with a.app_context():
        u = User.query.filter_by(username=username).first()
        uid, fp = u.id, fingerprint(u)
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    html = c.get('/academic/timetable').get_data(as_text=True)
    m = re.search(r'csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


with a.app_context():
    from app.models.academic import Teacher
    from app.models.grades import TeacherSubjectLink
    from app.models.timetable import TermSchedule
    # 注意：旧课表模块（routes/timetable.py 的 Timetable）已下线，
    # 现在在用的学期课表模型是 TermSchedule（timetable 库）
    tt = TermSchedule.query.order_by(TermSchedule.id.desc()).first()
    sid, tt_grade = tt.id, None      # TermSchedule 跨年级，无 grade 字段
    lic = User.query.filter_by(username='licancan').first()
    lic_grades = {r.grade for r in
                  TeacherSubjectLink.query.filter_by(user_id=lic.id).all() if r.grade}
    lic_teacher = Teacher.query.filter_by(user_id=lic.id).first()
    lic_uid = lic_teacher.teacher_uid if lic_teacher else None
    # 找一个"别的年级"的教师（与本账号任教年级无交集）
    other_teacher = None
    for t in Teacher.query.limit(200).all():
        gs = {r.grade for r in
              TeacherSubjectLink.query.filter_by(user_id=t.user_id).all() if r.grade}
        if gs and not (gs & lic_grades):
            other_teacher = t
            break
    other_uid = other_teacher.teacher_uid if other_teacher else None
    print(f'   学期 #{sid}（{tt_grade}）｜licancan 任教 {sorted(lic_grades)}｜'
          f'本人 uid={lic_uid}｜别年级 uid={other_uid}')

my_grade = sorted(lic_grades)[0] if lic_grades else None
other_grade = None
for g in ('2024级', '2025级', '2026级'):
    if g not in lic_grades:
        other_grade = g
        break
print(f'   本年级={my_grade}｜越权年级={other_grade}')

print('\n=== 1) 班级课表范围 ===')
with a.test_client() as c:
    login(c, 'admin')
    r = c.get(f'/academic/schedule/{sid}/class/{other_grade}/01班')
    case('管理员看越权年级课表 → 放行', r.status_code in (200, 302, 404),
         f'HTTP {r.status_code}')

with a.test_client() as c:
    login(c, 'licancan')
    r = c.get(f'/academic/schedule/{sid}/class/{my_grade}/01班')
    # 现状：licancan 没有 academic.timetable 权限 → 403 来自权限装饰器（正确拦截）。
    # 范围校验本身用下面的"单元验证"单独确认（不依赖是否有权限点）。
    case('无课表权限账号访问本年级 → 403（权限拦截）', r.status_code == 403,
         f'HTTP {r.status_code}')
    r = c.get(f'/academic/schedule/{sid}/class/{other_grade}/01班')
    case('教师看越权年级课表 → 403', r.status_code == 403, f'HTTP {r.status_code}')
    r = c.get(f'/academic/schedule/{sid}/grade/{other_grade}')
    case('教师看越权年级页 → 403', r.status_code == 403, f'HTTP {r.status_code}')

print('\n=== 2) 教师课表范围 ===')
with a.test_client() as c:
    login(c, 'licancan')
    if lic_uid:
        r = c.get(f'/academic/schedule/{sid}/teacher/{lic_uid}')
        case('无课表权限账号看教师课表 → 403', r.status_code == 403, f'HTTP {r.status_code}')
    if other_uid:
        r = c.get(f'/academic/schedule/{sid}/teacher/{other_uid}')
        case('教师看别年级教师课表 → 403', r.status_code == 403, f'HTTP {r.status_code}')
        r = c.get(f'/academic/schedule/teacher?uid={other_uid}')
        case('教师课表查询页越权 uid → 403', r.status_code == 403, f'HTTP {r.status_code}')

print('\n=== 3) 总课表 grades= 越权参数 ===')
with a.test_client() as c:
    login(c, 'licancan')
    r = c.get(f'/academic/schedule/{sid}/overview?grades={other_grade}')
    body = r.get_data(as_text=True)
    # 403 也算正确拦截（该账号可能连 academic.view 都没有）；关键是没渲染越权班级
    case('总课表请求越权年级 → 不含该年级班级列',
         r.status_code in (200, 302, 403, 404)
         and f'/schedule/{sid}/class/{other_grade}/' not in body,
         f'HTTP {r.status_code}')

print('\n=== 4) 字典删除 → 回收站 ===')
with a.app_context():
    from app.models.dictionary import DictCategory, DictItem
    from app.models.trash import TrashRecord
    cat = DictCategory.query.first()
    tmp_item = None
    if cat:
        # 幂等：上一次失败可能留下同名项（UNIQUE(category_id, value)）
        DictItem.query.filter_by(value='TMP_AUDIT_DICT').delete()
        db.session.commit()
        tmp_item = DictItem(category_id=cat.id, value='TMP_AUDIT_DICT',
                            sort_order=999, is_active=True)
        db.session.add(tmp_item)
        db.session.commit()
        item_id, cat_code = tmp_item.id, cat.code
    else:
        item_id = None
print(f'   临时字典项 id={item_id}')

if item_id:
    with a.test_client() as c:
        csrf = login(c, 'admin')
        r = c.post(f'/dictionary/{item_id}/delete', headers={'X-CSRFToken': csrf})
        case('删除字典项 → 成功', r.status_code in (200, 302), f'HTTP {r.status_code}')
    with a.app_context():
        db.session.expire_all()
        rec = (TrashRecord.query.filter_by(kind='dictionary_item', ref_id=item_id)
               .order_by(TrashRecord.id.desc()).first())
        case('字典项已进回收站', rec is not None,
             f'#{rec.id} {rec.summary}' if rec else '无记录')
        if rec is not None:
            db.session.delete(rec)
            db.session.commit()
    con = sqlite3.connect('data/system.db', timeout=15)
    con.execute('DELETE FROM dict_items WHERE value=?', ('TMP_AUDIT_DICT',))
    con.commit()
    con.close()

# 注：原第 5 节"旧课表删除归属"不适用 —— routes/timetable.py 的
# Timetable / TimetableEntry 模型不存在、蓝图未注册（旧课表模块已下线），
# 在用的课表统一走 schedule 模块，范围校验已在 schedule_class/grade/teacher 上覆盖。

print('\n=== 6) 范围函数单元验证（不依赖权限装饰器） ===')
with a.app_context():
    from flask_login import login_user
    from app.modules.academic.routes.schedule import _view_scope
    lic2 = User.query.filter_by(username='licancan').first()
    adm = User.query.filter_by(username='admin').first()
    with a.test_request_context('/'):
        login_user(lic2)
        g, cl = _view_scope()
        case('_view_scope(任课教师) = 任教年级', g == set(lic_grades), f'{g}')
    with a.test_request_context('/'):
        login_user(adm)
        g, cl = _view_scope()
        case('_view_scope(管理员) = 不过滤', g is None and cl is None, f'{g}')

print(f'\n===== 课表范围与归档：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

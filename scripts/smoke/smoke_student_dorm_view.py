# -*- coding: utf-8 -*-
"""学生管理模块「宿舍信息只读展示」冒烟（v1.19.47.4）

覆盖：
- 列表页 /students/ 展示宿舍列（住校/走读、出门权限、宿舍、床位）
- 这些列为只读：页面不提供编辑/批量修改入口，并提示归属宿舍管理模块
- 批量导出可勾选宿舍列（boarding_type / day_student_type / room / bed），值取自 dormitory 库
- 学生查询页带的宿舍筛选（boarding_type/room_number）在导出时不被静默忽略
- 无 dormitory 权限的学生（如任课教师组）也能看到宿舍列（列表只读呈现，不额外设门槛）

用法：python scripts/smoke/smoke_student_dorm_view.py
"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import io
import os
import re
import sys
import tempfile

import openpyxl

BASE = _ROOT
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_stu_dorm_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    k: 'sqlite:///' + os.path.join(_TMP, k + '.db')
    for k in ('dormitory', 'history', 'grades', 'points', 'academic', 'inspection',
              'achievement', 'forms', 'system', 'portrait', 'timetable', 'morality',
              'timed')
}
config_mod.Config.SECRET_KEY = 'stu-dorm-check'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (User, Student, Room, BedAssignment,  # noqa: E402
                        StudentAccommodation, PermissionGroup)

app = create_app()
ok = fail = 0


def check(name, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'[PASS] {name}')
    else:
        fail += 1
        print(f'[FAIL] {name}  {extra}')


def _csrf(c, path='/login'):
    html = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


# ==================== 1. 造数据：学生 + 住宿 + 床位 ====================
with app.app_context():
    # 管理员（全部权限）
    adm = User(username='dormadm', real_name='宿舍管理员', role='admin', must_change_pwd=False)
    adm.set_password('DormAdm#2026')
    db.session.add(adm)
    db.session.flush()
    # 任课教师（绑定任课教师组：仅 students.view，无 dormitory 权限）
    tea_gid = PermissionGroup.query.filter_by(name='任课教师组').first().id
    tea = User(username='dormtea', real_name='任课教师', role='teacher',
               permission_group_id=tea_gid, must_change_pwd=False)
    tea.set_password('DormTea#2026')
    db.session.add(tea)
    db.session.flush()

    s1 = Student(name='张住宿', gender='男', student_number='260101',
                 grade='2026级', class_name='01班', subject_selection='物化生')
    s2 = Student(name='李走读', gender='女', student_number='260102',
                 grade='2026级', class_name='01班', subject_selection='史政地')
    s3 = Student(name='王无床位', gender='男', student_number='260103',
                 grade='2026级', class_name='02班', subject_selection='物化生')
    db.session.add_all([s1, s2, s3])
    db.session.flush()
    s1_id, s2_id, s3_id = s1.id, s2.id, s3.id

    # 宿舍（dormitory 库）
    room = Room(building='东宿舍楼', room_number='301', gender='男', floor=3,
                capacity=8, grade='2026级', class_name='01班')
    db.session.add(room)
    db.session.flush()
    bed = BedAssignment(room_id=room.id, bed_number=3, student_id=s1_id)
    db.session.add(bed)

    # 住宿档案（dormitory 库）
    db.session.add_all([
        StudentAccommodation(student_id=s1_id, boarding_type='住校',
                             day_student_type='全天可出', textbook='人教版'),
        StudentAccommodation(student_id=s2_id, boarding_type='走读',
                             day_student_type='放学可出'),
    ])
    db.session.commit()
    room_id, bed_no = room.id, 3

with app.test_client() as c:
    # ==================== 2. 管理员：列表页展示 ====================
    csrf = _csrf(c)
    c.post('/login', data={'csrf_token': csrf, 'username': 'dormadm',
                           'password': 'DormAdm#2026'}, follow_redirects=True)
    csrf = _csrf(c, '/students/')

    r = c.get('/students/')
    html = r.get_data(as_text=True)
    check('管理员：学生列表页 200', r.status_code == 200, str(r.status_code))
    # 表头四列
    check('列表页含「住校/走读」表头', '住校/走读' in html)
    check('列表页含「出门权限」表头', '出门权限' in html)
    check('列表页含「宿舍」表头', '宿舍' in html)
    check('列表页含「床位」表头', '床位' in html)
    # 归属提示
    check('列表页提示这几列属于宿舍管理模块',
          '宿舍管理模块' in html and '只读' in html, '')
    check('提示含宿舍管理模块跳转链接', "url_for('rooms.list_rooms')" not in html
          and '/rooms' in html, '')
    # 数据值
    check('张住宿显示住校', '张住宿' in html and '住校' in html)
    check('东宿舍楼 301 在页面中', '东宿舍楼' in html and '301' in html)
    check('李走读显示走读', '李走读' in html and '走读' in html)
    # 只读：宿舍列不渲染任何可编辑控件
    check('宿舍列无 input/select 控件',
          'col-dorm' in html and 'name="boarding_type"' not in html, '')
    # 只读样式类
    check('宿舍列带只读样式类 col-dorm', 'class="col-dorm' in html)

    # 导出弹窗勾选区
    check('导出弹窗含「宿舍信息（只读）」分组',
          '宿舍信息（只读' in html, '')
    check('导出可勾选 boarding_type', 'name="columns" value="boarding_type"' in html)
    check('导出可勾选 day_student_type', 'name="columns" value="day_student_type"' in html)
    check('导出可勾选 room', 'name="columns" value="room"' in html)
    check('导出可勾选 bed', 'name="columns" value="bed"' in html)

    # ==================== 3. 管理员：导出（勾选宿舍列） ====================
    r = c.post('/students/export', data={
        'csrf_token': csrf,
        'columns': ['name', 'boarding_type', 'day_student_type', 'room', 'bed'],
    })
    check('导出 200', r.status_code == 200, str(r.status_code))
    is_xlsx = r.mimetype.endswith('spreadsheetml.sheet')
    check('导出为 xlsx', is_xlsx, str(r.mimetype))
    body = r.get_data()
    if is_xlsx:
        wb = openpyxl.load_workbook(io.BytesIO(body))
        ws = wb.active
        headers = [ws.cell(row=1, column=i).value for i in range(1, ws.max_column + 1)]
        check('导出表头含 学号/姓名/住校/走读/出门权限/宿舍/床位',
              '学号' in headers and '姓名' in headers and '住校/走读' in headers
              and '出门权限' in headers and '宿舍' in headers and '床位' in headers,
              str(headers))
        rows = {}
        for i in range(2, ws.max_row + 1):
            sno = ws.cell(row=i, column=headers.index('学号') + 1).value
            rows[sno] = [ws.cell(row=i, column=j + 1).value
                         for j in range(len(headers))]
        check('导出含 3 名学生', len(rows) == 3, str(list(rows)))

        def _val(sno, col):
            return rows[sno][headers.index(col)]

        check('张住宿：住校 + 全天可出 + 东宿舍楼 301 + 3床',
              _val('260101', '住校/走读') == '住校'
              and _val('260101', '出门权限') == '全天可出'
              and '301' in str(_val('260101', '宿舍'))
              and '3' in str(_val('260101', '床位')),
              str(_val('260101', '宿舍')) + ' / ' + str(_val('260101', '床位')))
        check('李走读：走读 + 放学可出 + 宿舍为空',
              _val('260102', '住校/走读') == '走读'
              and _val('260102', '出门权限') == '放学可出'
              and not _val('260102', '宿舍'), str(rows.get('260102')))
        check('王无床位：住宿信息为空，宿舍/床位为空',
              not _val('260103', '住校/走读') and not _val('260103', '宿舍'),
              str(rows.get('260103')))
        # 宿舍列默认勾选（住校/走读）时也能导出
    else:
        check('导出为 xlsx', False, '未获得 xlsx')

    # 默认勾选：只传 boarding_type
    r = c.post('/students/export', data={
        'csrf_token': csrf,
        'columns': ['boarding_type'],
    })
    check('只勾住校/走读也能导出', r.status_code == 200
          and r.mimetype.endswith('spreadsheetml.sheet'), str(r.status_code))
    wb2 = openpyxl.load_workbook(io.BytesIO(r.get_data()))
    ws2 = wb2.active
    h2 = [ws2.cell(row=1, column=i).value for i in range(1, ws2.max_column + 1)]
    check('默认列含学号（必选）', h2 and h2[0] == '学号', str(h2))
    check('住校/走读列出现在导出中', '住校/走读' in h2, str(h2))

    # ==================== 4. 宿舍筛选参数不再被静默忽略 ====================
    r = c.get('/students/export?boarding_type=走读&columns=name')
    if r.mimetype.endswith('spreadsheetml.sheet'):
        wb3 = openpyxl.load_workbook(io.BytesIO(r.get_data()))
        ws3 = wb3.active
        h3 = [ws3.cell(row=1, column=i).value for i in range(1, ws3.max_column + 1)]
        names = [ws3.cell(row=i, column=2).value for i in range(2, ws3.max_row + 1)]
        check('按走读筛选：只导出李走读', names == ['李走读'], str(names))
    else:
        check('按走读筛选导出', False, str(r.status_code))

    r = c.get('/students/export?room_number=301&columns=name')
    if r.mimetype.endswith('spreadsheetml.sheet'):
        wb4 = openpyxl.load_workbook(io.BytesIO(r.get_data()))
        ws4 = wb4.active
        names4 = [ws4.cell(row=i, column=2).value for i in range(2, ws4.max_row + 1)]
        check('按宿舍号 301 筛选：只导出张住宿', names4 == ['张住宿'], str(names4))
    else:
        check('按宿舍号筛选导出', False, str(r.status_code))

    c.get('/logout', follow_redirects=True)

    # ==================== 5. 任课教师（无 dormitory 权限）：仍可见宿舍列 ====================
    csrf = _csrf(c)
    c.post('/login', data={'csrf_token': csrf, 'username': 'dormtea',
                           'password': 'DormTea#2026'}, follow_redirects=True)
    r = c.get('/students/')
    html_t = r.get_data(as_text=True)
    check('任课教师：学生列表 200', r.status_code == 200, str(r.status_code))
    check('任课教师也能看到宿舍列（只读）',
          '住校/走读' in html_t and 'col-dorm' in html_t, '')
    check('任课教师页面同样提示归属宿舍管理模块', '宿舍管理模块' in html_t, '')
    c.get('/logout', follow_redirects=True)

print(f'\n===== 学生宿舍只读展示冒烟：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

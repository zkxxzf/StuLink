# -*- coding: utf-8 -*-
"""StuLink 教务模块回归测试（课表 / 总课表 / 教室 / 任课安排 / 备课组长 / 工作台 / 名单 / 查课 / 业绩）。

设计同 `tests/sec_regression.py`：临时目录 + 临时 SQLite 库 + create_app，不触碰本地 data/。
按项号运行：`python tests/academic_regression.py SCHED OVERVIEW`（不带参数跑全量）。

覆盖的是 2026-09-25 ~ 09-26 这批教务改造的关键行为：
- 网格视觉（学科配色/吸附/拖拽属性）、拖拽换格接口与冲突拒绝
- 全校总课表（分块/过滤/Excel/分享）
- 教室视图（清单与数据隔离）
- 任课安排（三种视角/周次过滤/班型筛选/课时预警/Excel）
- 备课组长（唯一键更新/分组展示/筛选/导出/删除）
- 教务工作台、教师名单（筛选+导出）、查课（检查人姓名+编辑）、业绩（审核意见）
"""
import io
import os
import re
import shutil
import sys
import tempfile
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_academic_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_TMP, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_TMP, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_TMP, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_TMP, 'points.db'),
    'academic': 'sqlite:///' + os.path.join(_TMP, 'academic.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
    'system': 'sqlite:///' + os.path.join(_TMP, 'system.db'),
    'timetable': 'sqlite:///' + os.path.join(_TMP, 'timetable.db'),
}
config_mod.Config.SECRET_KEY = 'academic-regression-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')
# 测试内需要在"无请求上下文"时用 url_for 生成 URL（如分享链接），
# Flask 2.2+ 要求此时配置 SERVER_NAME。
config_mod.Config.SERVER_NAME = 'localhost'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import ClassProfile, ClassSubject, PermissionGroup, User  # noqa: E402
from app.models.academic import (InspectionRecord, SubjectLeader,  # noqa: E402
                                 Teacher, TeacherAchievement)
from app.models.timetable import (PeriodDef, ScheduleEntry, TermSchedule,  # noqa: E402
                                  get_default_periods)

app = create_app()
TEST_PWD = 'Edu#2026x'

_ok = 0
_fail = 0
ITEMS = {}


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


def get_csrf(c, path='/login'):
    html = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


def login(c, username, password):
    csrf = get_csrf(c)
    return c.post('/login', data={'csrf_token': csrf, 'username': username,
                                  'password': password})


def sheet_texts(data):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data))
    ws = wb[wb.sheetnames[0]]
    return [str(v) for row in ws.iter_rows() for v in (c.value for c in row) if v]


def excel_sheets(data):
    from openpyxl import load_workbook
    return load_workbook(io.BytesIO(data))


# ── 数据准备 ────────────────────────────────────────────────────────────────
TS_ID = None
EID = None
IDS = {}

with app.app_context():
    db.create_all()
    grp = PermissionGroup.query.filter_by(name='管理员组').first()
    admin = User(username='ac_admin', real_name='教务主任', role='admin',
                 must_change_pwd=False, permission_group_id=grp.id if grp else None)
    admin.set_password(TEST_PWD)
    db.session.add(admin)
    head = User(username='ac_head', real_name='钱班主任', role='homeroom_teacher',
                grade='高一', class_name='01班', must_change_pwd=False)
    head.set_password(TEST_PWD)
    db.session.add(head)
    # 高中场景：班型 + 选科方向（新高考 3+1+2）+ 选科组合
    cp1 = ClassProfile(grade='高一', class_name='01班', class_type='强基班',
                       subject_direction='物理')
    cp2 = ClassProfile(grade='高一', class_name='02班', class_type='卓越班',
                       subject_direction='历史')
    db.session.add_all([cp1, cp2])
    db.session.flush()
    db.session.add_all([
        ClassSubject(class_profile_id=cp1.id, subject_value='物理'),
        ClassSubject(class_profile_id=cp1.id, subject_value='化学'),
        ClassSubject(class_profile_id=cp1.id, subject_value='生物'),
        ClassSubject(class_profile_id=cp2.id, subject_value='历史'),
        ClassSubject(class_profile_id=cp2.id, subject_value='政治'),
        ClassSubject(class_profile_id=cp2.id, subject_value='地理'),
    ])

    for uid, name, subject in (('T900001', '张语文', '语文'),
                               ('T900002', '李数学', '数学'),
                               ('T900003', '王英语', '英语')):
        db.session.add(Teacher(teacher_uid=uid, name=name, subject=subject, status='active'))
    db.session.add(Teacher(teacher_uid='T900009', name='离职老师', subject='物理',
                           status='left'))
    db.session.commit()
    IDS['admin'] = admin.id
    IDS['head'] = head.id

    ts = TermSchedule(name='2026-2027学年第一学期', school_year='2026-2027',
                      term='第一学期', status='active', is_current=True,
                      start_date=date(2026, 9, 1), total_weeks=20)
    db.session.add(ts)
    db.session.flush()
    TS_ID = ts.id
    for p in get_default_periods():
        db.session.add(PeriodDef(term_schedule_id=ts.id, **p))

    rows = [
        ('高一', '01班', 1, 2, '语文', 'T900001', '张语文', '101', '1-20', 'normal'),
        ('高一', '01班', 1, 3, '数学', 'T900002', '李数学', '101', '1-20', 'normal'),
        ('高一', '01班', 2, 3, '英语', 'T900003', '王英语', '101', '1-20', 'normal'),
        ('高一', '02班', 1, 2, '语文', 'T900001', '张语文', '102', '1-20', 'normal'),
        ('高一', '02班', 3, 8, '物理', 'T900004', '赵物理', '实验室1', '单周', 'normal'),
        ('高二', '01班', 1, 2, '化学', 'T900005', '钱化学', '201', '1-20', 'normal'),
        ('高二', '01班', 5, 7, '体育', 'T900006', '孙体育', '操场', '1-20', 'swap'),
    ]
    for g, cn, wd, pn, subj, tuid, tname, room, wr, etype in rows:
        db.session.add(ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                                     weekday=wd, period_number=pn, subject=subj,
                                     teacher_uid=tuid, teacher_name=tname, room=room,
                                     week_range=wr, entry_type=etype))
    db.session.add(InspectionRecord(inspect_date=date.today(), grade='高一', period=2,
                                    teacher_uid='T900002', teacher_name='李数学',
                                    class_name='01班', subject='数学', result='late',
                                    inspector_id=admin.id, note='迟到 3 分钟'))
    db.session.commit()
    EID = ScheduleEntry.query.filter_by(term_schedule_id=TS_ID, grade='高一',
                                        class_name='01班', weekday=1,
                                        period_number=2).first().id


def _urls():
    from flask import url_for
    return {
        'master': url_for('academic.schedule_master', sid=TS_ID),
        'grade': url_for('academic.schedule_grade', sid=TS_ID, grade='高一'),
        'class': url_for('academic.schedule_class', sid=TS_ID, grade='高一',
                         class_name='01班'),
        'teacher': url_for('academic.schedule_teacher', sid=TS_ID, uid='T900001'),
        'room': url_for('academic.schedule_room', sid=TS_ID, room='101'),
        'room_index': url_for('academic.schedule_room_index'),
        'rooms': url_for('academic.api_schedule_rooms', sid=TS_ID),
        'room_data': url_for('academic.api_schedule_room_data', sid=TS_ID),
        'class_data': url_for('academic.api_schedule_class_data', sid=TS_ID),
        'edit': url_for('academic.schedule_entry_edit', sid=TS_ID, eid=EID),
        'overview': url_for('academic.schedule_overview', sid=TS_ID),
        'overview_index': url_for('academic.schedule_overview_index'),
        'timetable': url_for('academic.schedule_timetable', sid=TS_ID),
        'timetable_index': url_for('academic.schedule_timetable_index'),
        'teaching': url_for('academic.schedule_teaching', sid=TS_ID),
        'teaching_index': url_for('academic.schedule_teaching_index'),
        'student': url_for('academic.schedule_student', sid=TS_ID),
        'student_index': url_for('academic.schedule_student_index'),
        'night': url_for('academic.night_duty', sid=TS_ID),
        'night_index': url_for('academic.night_duty_index'),
        'night_auto': url_for('academic.night_duty_auto', sid=TS_ID),
        'night_export': url_for('academic.night_duty_export', sid=TS_ID),
        'smart_import': url_for('academic.schedule_smart_import', sid=TS_ID),
        'smart_template': url_for('academic.schedule_smart_template', sid=TS_ID),
        'achievements': url_for('academic.achievements_page'),
        'duty': url_for('academic.duty_table'),
        'duty_export': url_for('academic.duty_export'),
        'leaders': url_for('academic.subject_leaders'),
        'leaders_save': url_for('academic.subject_leader_save'),
        'leaders_del': url_for('academic.subject_leader_delete'),
        'leaders_export': url_for('academic.subject_leader_export'),
        'home': url_for('academic.academic_home'),
        'teachers': url_for('academic.teachers_page'),
        'teachers_export': url_for('academic.teachers_export'),
        'insp': url_for('academic.inspection_page'),
        'insp_export': url_for('academic.inspection_export'),
        'insp_edit_tpl': url_for('academic.inspection_edit', rid=0),
        'ach': url_for('academic.achievements_page'),
        'ach_export': url_for('academic.achievements_export'),
        'ach_add': url_for('academic.achievements_add'),
        'ach_review_tpl': url_for('academic.achievements_review', aid=0),
        'share': url_for('academic.schedule_share', sid=TS_ID),
        'export': url_for('academic.schedule_export', sid=TS_ID),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 课表网格：视觉 + 拖拽换格
# ══════════════════════════════════════════════════════════════════════════════
@item('SCHED')
def check_sched():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        for key in ('master', 'grade', 'class', 'teacher', 'room'):
            r = c.get(u[key])
            case(f'课表 {key} 页 200', r.status_code == 200, str(r.status_code))

        html = c.get(u['class']).get_data(as_text=True)
        case('学科配色变量注入', '--sch:' in html)
        case('节次列吸附样式', 'position: sticky' in html)
        case('条目可拖拽', 'draggable="true"' in html)
        case('调课徽章 / 周次徽章', 'sch-badge-swap' in html and 'sch-badge-week' in html)
        case('统计容器 id 完整', 'id="gridTotal"' in html and 'id="statBadges"' in html)
        case('学科配色区分语文/数学',
             '#fee2e2' in html and '#dbeafe' in html)
        # 高中：班级课表要一眼看出班型与选科（新高考 3+1+2）
        case('班级课表显示班型/选科徽章',
             '强基班' in html and '物理类' in html and '物化生' in html,
             'class_type/direction/combo')
        case('晚自习行有专用底色（与正课区分）', 'sch-evening' in html)

        token = get_csrf(c, u['master'])
        hdrs = {'X-CSRFToken': token}
        r = c.post(u['edit'], json={'grade': '高一', 'class_name': '01班', 'weekday': 6,
                                    'period_number': 8, 'subject': '语文',
                                    'teacher_uid': 'T900001', 'teacher_name': '张语文',
                                    'room': '101', 'week_range': '1-20', 'note': ''},
                   headers=hdrs)
        case('大课表配置块含节次定义（弹窗节次不回退默认）',
             '"periods"' in c.get(u['master']).get_data(as_text=True))
        case('拖拽换格接口成功', bool((r.get_json() or {}).get('success')),
             f'{r.status_code} {r.get_data(as_text=True)[:120]}')
        with app.app_context():
            e = db.session.get(ScheduleEntry, EID)
            case('换格后库中时段已更新', e.weekday == 6 and e.period_number == 8,
                 f'{e.weekday}/{e.period_number}')
        # 换到"高二 01班 周一第2节"同班冲突位置 → 应被拒
        r = c.post(u['edit'], json={'grade': '高一', 'class_name': '01班', 'weekday': 1,
                                    'period_number': 3, 'subject': '语文',
                                    'teacher_uid': 'T900001', 'teacher_name': '张语文',
                                    'room': '101', 'week_range': '1-20', 'note': ''},
                   headers=hdrs)
        case('换格冲突被拒（同班同时段已占用）',
             not (r.get_json() or {}).get('success'), str(r.get_json())[:120])


# ══════════════════════════════════════════════════════════════════════════════
# 全校总课表
# ══════════════════════════════════════════════════════════════════════════════
@item('OVERVIEW')
def check_overview():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['overview'])
        html = r.get_data(as_text=True)
        case('总课表页 200', r.status_code == 200, str(r.status_code))
        case('按年级分块', html.count('data-grade="') == 2, str(html.count('data-grade="')))
        # 高中习惯：毕业年级在前（高二 → 高一）
        case('总课表年级按毕业年级优先排序',
             '高二' in html and html.index('高二') < html.index('高一)')
             if '高一)' in html else '高二' in html, '排序未按高三→高二→高一')
        case('总课表带班型/选科徽章', '物理类' in html and '强基班' in html)
        case('总课表提供选科筛选', 'direction=%E7%89%A9%E7%90%86' in html
             or 'direction=物理' in html, '选科筛选缺失')
        case('班级并列与学科+教师', '高二01班' in html and '张语文' in html)
        case('导出/打印入口', 'view_type=overview' in html and '打印总课表' in html)
        r = c.get(u['overview'], query_string={'main': '1'})
        case('main=1 去掉午休行', '午休<small' not in r.get_data(as_text=True))
        r = c.get(u['overview'], query_string={'grades': '高一'})
        mh = r.get_data(as_text=True)
        case('年级过滤 + 仍可切回', mh.count('data-grade="') == 1
             and ('grades=高二' in mh or 'grades=%E9%AB%98%E4%BA%8C' in mh),
             f'块数={mh.count(chr(34) + ">")} 含切回={("grades=" in mh)}')
        r = c.get(u['overview'], query_string={'room': '1'})
        case('显示教室开关', '101' in r.get_data(as_text=True))
        r = c.get(u['overview_index'], follow_redirects=False)
        case('总课表入口重定向', r.status_code in (301, 302)
             and '/overview' in r.headers.get('Location', ''))
        r = c.get(u['export'], query_string={'view_type': 'overview'})
        try:
            wb = excel_sheets(r.data)
            case('总课表 Excel（sheet 与内容）',
                 '全校总课表' in wb.sheetnames and any(
                     '高一' in str(cell.value) for row in wb['全校总课表'].iter_rows()
                     for cell in row if cell.value))
        except Exception as e:  # noqa: BLE001
            case('总课表 Excel 可解析', False, str(e))
        token = get_csrf(c, u['overview'])
        r = c.post(u['share'], json={'view': 'overview'}, headers={'X-CSRFToken': token})
        j = r.get_json() or {}
        case('总课表分享签发', bool(j.get('success')), str(j)[:120])
        link = (j.get('data') or {}).get('url', '')
        if link:
            path = link[link.index('/academic'):] if '/academic' in link else link
            r = c.get(path, follow_redirects=False)
            case('分享链接跳到总课表', r.status_code in (301, 302)
                 and '/overview' in r.headers.get('Location', ''))


# ══════════════════════════════════════════════════════════════════════════════
# 作息时间表（高中贴墙/打印）
# ══════════════════════════════════════════════════════════════════════════════
@item('TIMETABLE')
def check_timetable():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['timetable'])
        html = r.get_data(as_text=True)
        case('作息时间表页 200', r.status_code == 200, str(r.status_code))
        case('含正课/早读/晚自习分类',
             '正课' in html and '晚自习' in html and '早读' in html)
        case('含时长与统计', '分钟' in html and '小时' in html)
        r = c.get(u['timetable_index'], follow_redirects=False)
        case('作息表入口重定向到当前学期', r.status_code in (301, 302)
             and '/timetable' in r.headers.get('Location', ''),
             f"{r.status_code} {r.headers.get('Location')}")


# ══════════════════════════════════════════════════════════════════════════════
# 走班（新高考 3+1+2）：教学班 / 学生课表 / 撞课检测
# ══════════════════════════════════════════════════════════════════════════════
@item('WALKING')
def check_walking():
    from app.modules.academic.services import schedule_service as svc
    with app.app_context():
        # 周六第2节（课表 seed 只排周一至周五，此处必定空闲）
        ok, res = svc.add_entry(TS_ID, '高一', '01班', 6, 2, '物理',
                                teacher_name='王物理', room='物理实验室',
                                teaching_class='物化生1', operator=None)
        case('可新增走班课（带教学班）', ok, str(res))
        tcs = svc.list_teaching_classes(TS_ID)
        case('教学班清单含物化生1', '物化生1' in tcs, str(tcs))
        ok2, res2 = svc.add_entry(TS_ID, '高一', '01班', 6, 2, '物理',
                                  teacher_name='李物理', teaching_class='物化生2',
                                  operator=None)
        case('同时段可并行开第二个教学班（不被班级冲突误拦）', ok2, str(res2))

        v = svc.get_student_view(TS_ID, '高一', '01班')      # 物化生组合
        case('物化生组合学生课表含 2 节走班课', v['teaching_count'] == 2,
             str(v['teaching_count']))
        case('学生课表同时保留行政班课', v['total'] > v['teaching_count'],
             f"total={v['total']} walk={v['teaching_count']}")
        v2 = svc.get_student_view(TS_ID, '高一', '02班')     # 史政地组合
        case('史政地组合学生不含物化生走班课', v2['teaching_count'] == 0,
             str(v2['teaching_count']))
        cf = svc.check_teaching_conflicts(TS_ID)
        case('走班撞课被检出（同组合同段两个教学班）',
             any(c['combo'] == '物化生' for c in cf), str(cf[:2]))

    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['teaching'])
        html = r.get_data(as_text=True)
        case('教学班课表页 200', r.status_code == 200, str(r.status_code))
        case('教学班页列出物化生1', '物化生1' in html)
        case('教学班页提示撞课', '撞课' in html)
        # 显式指定物化生组合的班级（默认取第一个班，组合不一定匹配）
        r = c.get(u['student'] + '?grade=高一&class_name=01班')
        html = r.get_data(as_text=True)
        case('学生课表页 200', r.status_code == 200, str(r.status_code))
        case('学生课表标注选科组合与走班说明',
             '物化生' in html and '走班' in html)
        for name, key in (('教学班', 'teaching_index'), ('学生课表', 'student_index')):
            r = c.get(u[key], follow_redirects=False)
            case(f'{name}入口重定向', r.status_code in (301, 302),
                 f"{name} {r.status_code}")


# ══════════════════════════════════════════════════════════════════════════════
# 学校原样课表导入（矩阵式）
# ══════════════════════════════════════════════════════════════════════════════
@item('MATRIX')
def check_matrix_import():
    import io as _io
    from openpyxl import Workbook
    from app.modules.academic.services import schedule_matrix_import as mi
    from app.modules.academic.services import schedule_service as svc
    from app.models.academic import Teacher

    wb = Workbook()
    ws = wb.active
    ws.title = '高一3班'
    ws.append(['节次', '周一', '周二', '周三'])
    ws.append(['早读', '语文', '英语', '语文'])
    ws.append(['第1节', '数', '语文(单周)', '—'])
    ws.append(['第2节', '物理', '化学', ''])
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    with app.app_context():
        periods = svc.get_periods(TS_ID)
        teachers = Teacher.query.filter_by(status='active').all()
        data = mi.parse_school_workbook(buf, periods, ['高一', '高二'], teachers)
        case('识别矩阵表', len(data['sheets']) == 1, str(len(data['sheets'])))
        sh = data['sheets'][0]
        case('从 sheet 名识别班级（高一3班）',
             sh['grade'] == '高一' and sh['class_name'] == '03班',
             f"{sh['grade']}/{sh['class_name']}")
        subjects = [e['subject'] for e in sh['entries']]
        case('学科简写归一（数 → 数学）', '数学' in subjects, str(sorted(set(subjects))))
        # 关键：学校表格的「第1节」是系统 2 号位（1 号位是早读），不能错位
        p1 = {(e['weekday'], e['period_number']) for e in sh['entries']}
        case('节次未错位（第1节→2、第2节→3、早读→1）',
             (1, 1) in p1 and (1, 2) in p1 and (1, 3) in p1, str(sorted(p1)[:6]))
        case('空值/破折号不生成条目',
             not [e for e in sh['entries'] if e['subject'] in ('—', '-', '')])
        wr = {e['week_range'] for e in sh['entries']}
        case('单双周标注解析', '单周' in wr, str(wr))

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(_urls()['smart_import'])
        html = r.get_data(as_text=True)
        case('原样导入页 200', r.status_code == 200, str(r.status_code))
        case('页面含上传与说明', '学校发的原样即可' in html or '上传课表' in html)
        r = c.get(_urls()['smart_template'])
        case('原样模板可下载', r.status_code == 200
             and 'spreadsheetml' in (r.headers.get('Content-Type') or ''),
             str(r.status_code))


# ══════════════════════════════════════════════════════════════════════════════
# 晚自习值班（高中刚需）
# ══════════════════════════════════════════════════════════════════════════════
@item('NIGHT')
def check_night_duty():
    from collections import Counter, defaultdict
    from app.modules.academic.services import night_duty_service as nd
    with app.app_context():
        from app.extensions import db
        from app.models.academic import Teacher
        # 临时库教师很少，先补一批（幂等），否则排班只能靠放宽上限
        if Teacher.query.filter_by(status='active').count() < 12:
            for i in range(12):
                uid = f'NDT{i:03d}'
                if not Teacher.query.filter_by(teacher_uid=uid).first():
                    db.session.add(Teacher(
                        teacher_uid=uid, name=f'值班教师{i + 1}',
                        subject='语文' if i % 2 else '数学', status='active'))
            db.session.commit()

        periods = nd.evening_period_numbers(TS_ID)
        case('识别晚自习节次', len(periods) >= 1, str(periods))
        grades = nd.grades_of(TS_ID)
        okk, msg = nd.auto_assign(TS_ID, max_per_week=3, operator=None)
        case('一键均衡排班', okk, msg)
        data = nd.get_roster(TS_ID)
        rows = [d for b in data['blocks'] for wd in b['grid'].values()
                for d in wd.values()]
        case('班次排满', len(rows) == len(grades) * 5 * len(periods),
             f"{len(rows)}/{len(grades) * 5 * len(periods)}")
        day = defaultdict(int)
        week = Counter()
        for d in rows:
            day[(d['teacher_uid'], d['weekday'])] += 1
            week[d['teacher_uid']] += 1
        case('同一教师同一天只值一节',
             not {k: v for k, v in day.items() if v > 1})
        case('周值班次数不超上限', max(week.values()) <= 3,
             f'最多 {max(week.values())} 次')
        case('教师值班统计覆盖参与者', len(nd.teacher_stats(TS_ID)) == len(week))
        case('导出工作簿可生成',
             nd.export_workbook(TS_ID).active.title == '晚自习值班表')
        # 手工指定 + 清空
        t = nd.teacher_pool()[0]
        ok_set, _ = nd.set_duty(TS_ID, grades[0], 1, periods[0], t['uid'],
                                t['name'], operator=None)
        got = (nd.get_roster(TS_ID, grades=[grades[0]])['blocks'][0]['grid']
               .get(1, {}).get(periods[0], {}).get('teacher_uid'))
        case('手工指定值班教师', ok_set and got == t['uid'], str(got))
        nd.set_duty(TS_ID, grades[0], 1, periods[0], None, None, operator=None)
        gone = (nd.get_roster(TS_ID, grades=[grades[0]])['blocks'][0]['grid']
                .get(1, {}).get(periods[0]))
        case('清空值班班次', not gone, str(gone))
        # 重复排同一人是允许的（别的天），但同一天不行 —— 校验 set_duty 不拦
        case('值班冲突检查可用',
             isinstance(nd.check_conflicts(TS_ID), list))

    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['night'])
        html = r.get_data(as_text=True)
        case('值班表页面 200', r.status_code == 200, str(r.status_code))
        case('页面含值班网格与统计',
             'nd-cell' in html and '教师值班次数' in html and '今日值班' in html)
        r = c.get(u['night_export'])
        case('值班表导出 200', r.status_code == 200, str(r.status_code))
        r = c.get(u['night_index'], follow_redirects=False)
        case('值班入口重定向', r.status_code in (301, 302), str(r.status_code))


# ══════════════════════════════════════════════════════════════════════════════
# 教师业绩库：标签 + 附件（图片/PDF）
# ══════════════════════════════════════════════════════════════════════════════
@item('ACHV')
def check_achievement_files():
    import io as _io
    from werkzeug.datastructures import FileStorage
    from app.extensions import db
    from app.models.academic import AchievementAttachment, TeacherAchievement
    from app.modules.academic.services import achievement_service as ach

    with app.app_context():
        rec = TeacherAchievement(teacher_uid='T0001', teacher_name='测试教师',
                                 category='honor', title='TMP附件断言',
                                 status='approved', submitted_by=1)
        db.session.add(rec)
        db.session.commit()
        rid = rec.id
        case('标签解析去重（逗号/空格混杂）',
             ach.parse_tags('课题, 省级 数学,课题') == ['课题', '省级', '数学'])
        ach.set_tags(rec, '课题,数学')
        case('标签落库读回一致', ach.tags_of(rec) == ['课题', '数学'])

        png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
        ok1, m1, a1 = ach.save_attachment(
            rec, FileStorage(stream=_io.BytesIO(png), filename='证书.png'))
        case('图片附件可上传', ok1 and a1 is not None, m1)
        ok2, m2, _ = ach.save_attachment(
            rec, FileStorage(stream=_io.BytesIO(b'<html>x</html>'), filename='x.html'))
        case('危险类型被拒（.html）', not ok2, m2)
        ok3, m3, _ = ach.save_attachment(
            rec, FileStorage(stream=_io.BytesIO(b'<html>x</html>'), filename='伪装.png'))
        case('伪装扩展名被拒（magic 嗅探）', not ok3, m3)
        d = ach.detail_dict(rec)
        case('详情含标签与附件', d['tags'] == ['课题', '数学']
             and len(d['attachments']) == 1, str(len(d['attachments'])))
        case('图片标为可预览', d['attachments'][0]['is_image']
             and d['attachments'][0]['previewable'])
        aid = a1.id

    with app.app_context():
        from flask import url_for as _url_for
        u = _urls()
        detail_url = _url_for('academic.achievement_detail', aid=rid)
        view_url = _url_for('academic.achievement_file_view', fid=aid)
        dl_url = _url_for('academic.achievement_file_download', fid=aid)
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(detail_url)
        case('详情接口 200', r.status_code == 200
             and (r.get_json() or {}).get('success'), str(r.status_code))
        r = c.get(view_url)
        case('图片内联预览（Content-Type=image/*）', r.status_code == 200
             and (r.headers.get('Content-Type') or '').startswith('image/'))
        r = c.get(dl_url)
        case('下载带 attachment 头',
             'attachment' in (r.headers.get('Content-Disposition') or ''))
        r = c.get(u['achievements'])
        case('业绩页面含标签与附件入口', r.status_code == 200
             and 'btn-ach-detail' in r.get_data(as_text=True))

    with app.app_context():
        rec = db.session.get(TeacherAchievement, rid)
        for att in ach.list_attachments(rec):
            ach.delete_attachment(att)
        db.session.delete(rec)
        db.session.commit()
        case('清理测试业绩与附件',
             AchievementAttachment.query.filter_by(achievement_id=rid).count() == 0)


# ══════════════════════════════════════════════════════════════════════════════
# 教室视图
# ══════════════════════════════════════════════════════════════════════════════
@item('ROOM')
def check_room():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['room'])
        html = r.get_data(as_text=True)
        case('教室页 200 含教室名与切换', r.status_code == 200
             and 'id="roomTitle"' in html and 'roomSelect' in html)
        j = c.get(u['rooms']).get_json() or {}
        case('教室清单 API', bool(j.get('success')) and j['data'].get('101', 0) >= 3,
             str(j.get('data')))
        j = c.get(u['room_data'], query_string={'room': '101'}).get_json() or {}
        case('教室数据仅含本教室', (j.get('data') or {}).get('total') == 3,
             str((j.get('data') or {}).get('total')))
        r = c.get(u['room_index'], follow_redirects=False)
        case('教室入口重定向到当前学期', r.status_code in (301, 302)
             and '/room' in r.headers.get('Location', ''))


# ══════════════════════════════════════════════════════════════════════════════
# 任课安排
# ══════════════════════════════════════════════════════════════════════════════
@item('DUTY')
def check_duty():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['duty'], query_string={'sid': TS_ID})
        html = r.get_data(as_text=True)
        case('任课安排页 200 / 年级分块', r.status_code == 200
             and html.count('class="duty-block"') == 2)
        case('班主任与班型列', '钱班主任' in html and '强基班' in html)
        case('学科合计与教师清单行', '学科合计' in html and '本年级学科教师' in html)
        case('格内教师 + 周课时', '张语文' in html and 'duty-hours' in html)
        case('任课表含选科列与组合', '选科' in html and '物化生' in html, '选科未贯穿')
        case('任课表含选科方向筛选', 'direction=%E7%89%A9%E7%90%86' in html
             or 'direction=物理' in html, '选科筛选缺失')
        r = c.get(u['duty'], query_string={'sid': TS_ID, 'view': 'teacher'})
        th = r.get_data(as_text=True)
        case('按教师视角（班级汇总）', '张语文' in th and '高一01班、高一02班' in th)
        r = c.get(u['duty'], query_string={'sid': TS_ID, 'view': 'subject'})
        case('按学科视角', '数学' in r.get_data(as_text=True))
        r = c.get(u['duty'], query_string={'sid': TS_ID, 'week': 2})
        case('week 过滤剔除单周课', '赵物理' not in r.get_data(as_text=True))
        r = c.get(u['duty'], query_string={'sid': TS_ID, 'class_type': '强基班'})
        cth = r.get_data(as_text=True)
        case('班型筛选', '01班' in cth and '>02班<' not in cth and '班型' in cth)
        # 筛选后必须还能切回（曾出现"筛出某年级后按钮组只剩该年级"与"空结果吞掉筛选栏"）
        gh = c.get(u['duty'], query_string={'sid': TS_ID, 'grade': '高一'}).get_data(as_text=True)
        case('年级筛选后仍可切回其它年级',
             'grade=高二' in gh or 'grade=%E9%AB%98%E4%BA%8C' in gh)
        eh = c.get(u['duty'], query_string={'sid': TS_ID, 'class_type': '不存在的班型'}).get_data(as_text=True)
        case('筛选无结果时保留筛选栏与清除入口',
             '清除筛选' in eh and '班型' in eh)
        case('切视角保留班型筛选',
             'class_type=' in c.get(u['duty'], query_string={
                 'sid': TS_ID, 'class_type': '强基班'}).get_data(as_text=True))
        r = c.get(u['duty'], query_string={'sid': TS_ID, 'view': 'teacher', 'warn': 1})
        case('课时预警标记', 'table-warning' in r.get_data(as_text=True))
        r = c.get(u['duty'], query_string={'sid': TS_ID, 'view': 'teacher', 'warn': 99})
        case('阈值内不预警', 'table-warning' not in r.get_data(as_text=True))
        for view, sheet in (('class', '任课安排'), ('teacher', '按教师'),
                            ('subject', '按学科')):
            r = c.get(u['duty_export'], query_string={'sid': TS_ID, 'view': view})
            try:
                wb = excel_sheets(r.data)
                case(f'任课表导出（{view}）', r.status_code == 200 and sheet in wb.sheetnames)
            except Exception as e:  # noqa: BLE001
                case(f'任课表导出（{view}）可解析', False, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# 备课组长
# ══════════════════════════════════════════════════════════════════════════════
@item('LEADER')
def check_leader():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, u['leaders'])
        payload = {'csrf_token': token, 'school_year': '2026-2027', 'term': '',
                   'grade': '高一', 'subject': '语文', 'leader_name': '张语文',
                   'leader_uid': 'T900001', 'members': '高一语文备课组（5人）',
                   'duty': '① 统筹进度 ② 组织集体备课'}
        r = c.post(u['leaders_save'], data=payload, follow_redirects=True)
        case('新增备课组长', r.status_code == 200 and '张语文' in r.get_data(as_text=True))
        with app.app_context():
            case('落库唯一', SubjectLeader.query.count() == 1)
        payload2 = dict(payload, leader_name='李数学', duty='更新后的职责')
        c.post(u['leaders_save'], data=payload2, follow_redirects=True)
        with app.app_context():
            rows = SubjectLeader.query.all()
            case('同键重复保存=更新', len(rows) == 1 and rows[0].leader_name == '李数学',
                 f'{len(rows)}')
        r = c.get(u['leaders'])
        lh = r.get_data(as_text=True)
        case('按学科分组展示 + 职责', '语文' in lh and '李数学' in lh and '更新后的职责' in lh)
        case('备课组范围渲染', '高一语文备课组（5人）' in lh)
        r = c.get(u['leaders'], query_string={'subject': '数学'})
        case('学科筛选生效', '李数学' not in r.get_data(as_text=True))
        r = c.get(u['leaders_export'], query_string={'school_year': '2026-2027'})
        try:
            texts = sheet_texts(r.data)
            case('组长名单导出（含职责列）',
                 any('李数学' in t for t in texts) and any('主要职责' in t for t in texts))
        except Exception as e:  # noqa: BLE001
            case('组长名单导出可解析', False, str(e))
        with app.app_context():
            lid = SubjectLeader.query.first().id
        c.post(u['leaders_del'], data={'csrf_token': token, 'id': lid,
                                       'school_year': '2026-2027'}, follow_redirects=True)
        with app.app_context():
            case('删除组长记录', SubjectLeader.query.count() == 0)


# ══════════════════════════════════════════════════════════════════════════════
# 教务工作台 / 教师名单 / 查课 / 业绩
# ══════════════════════════════════════════════════════════════════════════════
@item('HOME')
def check_home():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['home'])
        html = r.get_data(as_text=True)
        case('教务工作台 200', r.status_code == 200, str(r.status_code))
        case('含 KPI / 待办 / 入口 / 动态', all(k in html for k in
             ('教务工作台', '待办事项', '常用入口', '最近动态')))
        case('显示当前学期', '2026-2027学年第一学期' in html)


@item('TEACHERS')
def check_teachers():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['teachers'], query_string={'status': 'left'})
        html = r.get_data(as_text=True)
        case('教师名单状态筛选', '离职老师' in html and '张语文' not in html)
        r = c.get(u['teachers'], query_string={'subject': '数学'})
        case('学科筛选', '李数学' in r.get_data(as_text=True)
             and '王英语' not in r.get_data(as_text=True))
        r = c.get(u['teachers_export'])
        try:
            texts = sheet_texts(r.data)
            case('教师名单导出', r.status_code == 200
                 and any('教师编号' in t for t in texts)
                 and any('张语文' in t for t in texts)
                 and any('离职' in t for t in texts))
        except Exception as e:  # noqa: BLE001
            case('教师名单导出可解析', False, str(e))


@item('INSPECTION')
def check_inspection():
    with app.app_context():
        u = _urls()
        rid = InspectionRecord.query.first().id
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, u['insp'])
        html = c.get(u['insp']).get_data(as_text=True)
        case('查课页显示检查人姓名', '教务主任' in html)
        # 表头 10 列必须有对应 10 个单元格（曾漏渲染"检查人"td 导致整行列错位）
        case('查课表格检查人列落到正确单元格', '教务主任</td>' in html)
        case('查课页含年级筛选', 'name="grade"' in html)
        c.post(u['insp_edit_tpl'].replace('/0/edit', f'/{rid}/edit'),
               data={'csrf_token': token, 'result': 'normal', 'class_name': '01班',
                     'subject': '数学', 'period': 2, 'note': '已核实：事假'},
               follow_redirects=True)
        with app.app_context():
            rec = db.session.get(InspectionRecord, rid)
            case('编辑查课记录生效',
                 rec.result == 'normal' and rec.note == '已核实：事假',
                 f'{rec.result}/{rec.note}')
        r = c.get(u['insp_export'])
        try:
            case('导出检查人为姓名', any('教务主任' in t for t in sheet_texts(r.data)))
        except Exception as e:  # noqa: BLE001
            case('查课导出可解析', False, str(e))


@item('ACHIEVEMENT')
def check_achievement():
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, u['ach'])
        html = c.get(u['ach']).get_data(as_text=True)
        case('业绩页统计卡', '业绩总数' in html and '已驳回' in html)
        case('业绩页年度筛选', 'name="year"' in html)
        c.post(u['ach_add'], data={'csrf_token': token, 'teacher_uid': 'T900002',
                                   'category': 'honor', 'title': '市级优秀教师',
                                   'level': '市级', 'obtain_date': '2026-06-01',
                                   'issuer': '市教育局'}, follow_redirects=True)
        with app.app_context():
            aid = TeacherAchievement.query.first().id
            case('业绩录入', TeacherAchievement.query.count() == 1)
        c.post(u['ach_review_tpl'].replace('/0/review', f'/{aid}/review'),
               data={'csrf_token': token, 'action': 'reject', 'review_note': ''},
               follow_redirects=True)
        with app.app_context():
            case('驳回缺意见被拒', db.session.get(TeacherAchievement, aid).status == 'approved')
        c.post(u['ach_review_tpl'].replace('/0/review', f'/{aid}/review'),
               data={'csrf_token': token, 'action': 'reject',
                     'review_note': '材料不全，请补证书扫描件'}, follow_redirects=True)
        with app.app_context():
            rec = db.session.get(TeacherAchievement, aid)
            case('带意见驳回成功',
                 rec.status == 'rejected' and '材料不全' in (rec.review_note or ''))
        case('列表显示审核意见', '材料不全' in c.get(u['ach']).get_data(as_text=True))
        r = c.get(u['ach_export'], query_string={'status': 'rejected'})
        try:
            texts = sheet_texts(r.data)
            case('业绩导出含审核意见', any('市级优秀教师' in t for t in texts)
                 and any('材料不全' in t for t in texts))
        except Exception as e:  # noqa: BLE001
            case('业绩导出可解析', False, str(e))


# ── 入口 ────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    args = [a.upper() for a in sys.argv[1:]]
    targets = args or list(ITEMS)
    try:
        for iid in targets:
            fn = ITEMS.get(iid)
            if not fn:
                print(f'[SKIP] 未找到项号 {iid}（可选：{"、".join(ITEMS)}）')
                continue
            print(f'\n---- {iid} ----')
            fn()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print(f'\n===== 教务回归完成：通过 {_ok} / 失败 {_fail} =====')
    sys.exit(1 if _fail else 0)

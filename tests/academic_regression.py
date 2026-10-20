# -*- coding: utf-8 -*-
"""StuLink 教务模块回归测试（课表 / 总课表 / 学科组长 / 任课教师映射 / 工作台 / 名单 / 查课 / 业绩）。

设计同 `tests/sec_regression.py`：临时目录 + 临时 SQLite 库 + create_app，不触碰本地 data/。
按项号运行：`python tests/academic_regression.py SCHED OVERVIEW`（不带参数跑全量）。

覆盖的是 2026-09-25 ~ 10-10 这批教务改造的关键行为：
- 网格视觉（学科配色/吸附/拖拽属性）、拖拽换格接口与冲突拒绝
- 全校总课表（分块/过滤/Excel/分享）
- 任课教师映射：教务侧 /academic/teacher-links 与成绩侧同源（教师-学科关系的唯一依据）
- 任课安排下线核查：/academic/duty* 一律 404（2026-10-10 整功能移除）
- 学科组长（备课组长＝年级×学科 / 教研组长＝只按学科、组长必须本学科教师 / 综合组 / 导出 / 删除）
- 教务工作台、教师名单（筛选+导出）、查课（检查人姓名+编辑）、业绩（审核意见）
- 教师三页合一（2026-10-14）：教师名单 / 任课教师映射 / 学科组长 顶部 tab 互切（STAFFTABS）
"""
import io
import json
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
    # 2026-10-13（v1.19.49.46）：德育奖惩库
    'morality': 'sqlite:///' + os.path.join(_TMP, 'morality.db'),
    'academic': 'sqlite:///' + os.path.join(_TMP, 'academic.db'),
    # 2026-10-10：教务分库——查课 / 业绩 / 表单 各自独立库
    'inspection': 'sqlite:///' + os.path.join(_TMP, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_TMP, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_TMP, 'forms.db'),
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
from app.models.timetable import (PeriodDef, ScheduleEntry, ScheduleSwap,  # noqa: E402
                                  ScheduleVersion, SCHEDULE_STATUS, TermSchedule,
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
    # 今日课表只渲染「当天」：按今天补两条课，保证任何日期跑测试都有内容
    wd_today = date.today().isoweekday()
    for _i, (_g, _cn) in enumerate((('高一', '01班'), ('高二', '01班'))):
        db.session.add(ScheduleEntry(term_schedule_id=TS_ID, grade=_g, class_name=_cn,
                                     weekday=wd_today, period_number=3 + _i,
                                     subject='语文', teacher_uid='T900001',
                                     teacher_name='张语文', room='101',
                                     week_range='1-20', entry_type='normal'))
    db.session.commit()
    EID = ScheduleEntry.query.filter_by(term_schedule_id=TS_ID, grade='高一',
                                        class_name='01班', weekday=1,
                                        period_number=2).first().id


def _urls():
    from flask import url_for
    return {
        # 2026-10-10：'master'（大课表）已下线，不再入表
        'grade': url_for('academic.schedule_grade', sid=TS_ID, grade='高一'),
        'class': url_for('academic.schedule_class', sid=TS_ID, grade='高一',
                         class_name='01班'),
        'teacher': url_for('academic.schedule_teacher', sid=TS_ID, uid='T900001'),
        'class_data': url_for('academic.api_schedule_class_data', sid=TS_ID),
        'edit': url_for('academic.schedule_entry_edit', sid=TS_ID, eid=EID),
        'overview': url_for('academic.schedule_overview', sid=TS_ID),
        # 2026-10-12：'overview_index'（无 sid 的侧栏跳转入口）已随侧栏入口下线，不再入表
        'timetable': url_for('academic.schedule_timetable', sid=TS_ID),
        'timetable_index': url_for('academic.schedule_timetable_index'),
        'smart_import': url_for('academic.schedule_smart_import', sid=TS_ID),
        'smart_template': url_for('academic.schedule_smart_template', sid=TS_ID),
        'achievements': url_for('academic.achievements_page'),
        # 2026-10-10：任课安排（/academic/duty*）已下线 ——
        # 教师-学科关系以「任课教师映射」为唯一依据，故此处不再取 duty 地址
        'leaders': url_for('academic.subject_leaders'),
        'leaders_export': url_for('academic.subject_leader_export'),
        'matrix_save': url_for('academic.subject_leader_matrix_save'),
        'home': url_for('academic.academic_home'),
        'teachers': url_for('academic.teachers_page'),
        'teachers_export': url_for('academic.teachers_export'),
        'insp': url_for('academic.inspection_page'),
        'insp_entry': url_for('academic.inspection_entry_page'),
        'insp_add': url_for('academic.inspection_add'),
        'insp_batch': url_for('academic.inspection_batch'),
        'insp_export': url_for('academic.inspection_export'),
        'insp_edit_tpl': url_for('academic.inspection_edit', rid=0),
        'insp_live': url_for('academic.inspection_live_schedule'),
        'insp_mark': url_for('academic.inspection_mark'),
        # 2026-10-14：录入页按教师自动带出班级/科目（反查当天生效课表）
        'insp_lookup': url_for('academic.api_inspection_teacher_courses'),
        'insp_stats': url_for('academic.inspection_stats'),
        'wl_config': url_for('academic.inspection_workload_config'),
        'wl_rules': url_for('academic.inspection_workload_rules_save'),
        'wl_exam_add': url_for('academic.inspection_exam_period_add'),
        'wl_exam_del_tpl': url_for('academic.inspection_exam_period_delete', pid=0),
        'wl_export': url_for('academic.inspection_workload_export'),
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
        for key in ('grade', 'class', 'teacher'):
            r = c.get(u[key])
            case(f'课表 {key} 页 200', r.status_code == 200, str(r.status_code))
        # 2026-10-10：大课表页与其年级数据接口已下线（见下方 404 断言）
        r = c.get(f'/academic/schedule/{TS_ID}/master')
        case('大课表页已下线 404', r.status_code == 404, str(r.status_code))
        r = c.get(f'/academic/api/schedule/{TS_ID}/grade-data')
        case('大课表年级数据接口已下线 404', r.status_code == 404, str(r.status_code))
        # 2026-10-12（用户要求）：跨学期对比整体下线（页面/导出/数据接口一并删除）
        for _p, _label in (('/academic/schedule/compare', '对比页'),
                           ('/academic/schedule/compare/export', '对比导出'),
                           ('/academic/api/schedule/compare-data', '对比数据接口')):
            r = c.get(_p)
            case(f'跨学期对比：{_label}已下线 404', r.status_code == 404,
                 f'{_p} → {r.status_code}')

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

        # 2026-10-12（用户要求）：教师课表每格显示"哪个班"（老师知道自己教什么），
        # 班级/年级课表不变（学生家长视角要看学科）
        thtml = c.get(u['teacher']).get_data(as_text=True)
        case('教师课表主文本是班级', '<div class="sch-subject">高一01班' in thtml,
             '应显示 高一01班 而不是只显示学科')
        case('教师课表副行是学科', '<div class="sch-teacher">语文</div>' in thtml)
        case('教师课表配置块声明 showClass', '"showClass": true' in thtml)
        case('班级课表仍以学科为主文本', '<div class="sch-subject">语文' in html)

        token = get_csrf(c, u['grade'])
        hdrs = {'X-CSRFToken': token}
        r = c.post(u['edit'], json={'grade': '高一', 'class_name': '01班', 'weekday': 6,
                                    'period_number': 8, 'subject': '语文',
                                    'teacher_uid': 'T900001', 'teacher_name': '张语文',
                                    'room': '101', 'week_range': '1-20', 'note': ''},
                   headers=hdrs)
        # 2026-10-10：原断言取的是大课表页，该页下线后改在年级课表页校验同一件事
        case('课表页配置块含节次定义（弹窗节次不回退默认）',
             '"periods"' in c.get(u['grade']).get_data(as_text=True))
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
        # 2026-10-10：课表矩阵 CSS 从内联抽成静态文件（页面只留 <link>，浏览器可缓存）
        case('矩阵样式走静态文件（不再内联 ~8KB style）',
             'css/schedule_matrix.css' in html and '.ovw-block {' not in html,
             '内联样式残留' if '.ovw-block {' in html else '未引用静态 CSS')
        # 2026-10-10：竖版后 data-grade 落在"每个班级列/格"上（不再一块一个），
        # 所以按"表里出现的年级集合"断言，而不是计数
        grades_in_page = sorted({g for g in re.findall(r'data-grade="([^"]*)"', html) if g})
        case('按年级分块（两个年级进同一张竖版表）',
             grades_in_page == ['高一', '高二'], str(grades_in_page))
        # 高中习惯：毕业年级在前（高二 → 高一）
        case('总课表年级按毕业年级优先排序',
             '高二' in html and html.index('高二') < html.index('高一)')
             if '高一)' in html else '高二' in html, '排序未按高三→高二→高一')
        case('总课表带班型/选科徽章', '物理类' in html and '强基班' in html)
        # 2026-10-10 用户要求：移除「选科方向 / 班型」筛选（使用率低、占空间），
        # 只保留「星期 / 年级 / 只看正课」
        case('选科/班型筛选已下线（保留星期·年级·只看正课）',
             'direction=' not in html and 'class_type=' not in html
             and 'ovv-seg' in html and '只看正课' in html)
        # 2026-10-10 三改：竖版布局 —— 行＝节次、列＝班级，三个年级并进同一张表
        case('总课表改为竖版：行=节次、列=班级（三个年级一张表）',
             html.count('ovv-table') == 1 and html.count('ovv-grade-row') == 1
             and 'ovv-class-row' in html and 'ovv-axis-g' in html,
             'ovv-table=%d 年级行=%d' % (html.count('ovv-table'),
                                         html.count('ovv-grade-row')))
        case('左侧两列＝作息分组 + 节次名（旧版横版表头消失）',
             'ovv-group-t' in html and 'ovv-pname' in html and 'ovv-class' in html
             and 'ovw-class-col' not in html,
             '旧版横版表头残留' if 'ovw-class-col' in html else '')
        case('班级列表头带年级前缀（截图/打印不歧义）',
             '高二01班' in html and 'ovv-grade-start' in html)
        case('格内学科+教师（上行学科、下行教师）', '高二01班' in html and '张语文' in html)
        case('新作息节次渲染（早读 + 晚自习）', '早读' in html and '晚自习' in html)
        case('作息分组在左列（上午/下午/晚自习，跨行合并）',
             html.count('ovv-group-t') >= 2 and '上午' in html and '晚自习' in html
             and 'ovv-group-start' in html)
        case('星期标签切换（可切到周二，不再有整周表）',
             ('day=2' in html or 'day%3D2' in html) and '>整周<' not in html)
        case('导出/打印入口', 'view_type=overview' in html and '打印总课表' in html)
        # 竖版下空档（午休/课间）是"行"，main=1 时整行不渲染
        full_rows = html.count('ovv-row')
        full_break_rows = html.count('ovv-break-row')
        r = c.get(u['overview'], query_string={'main': '1'})
        mh = r.get_data(as_text=True)
        case('main=1 隐藏午休等空档节次（空档行整行消失）',
             'ovv-break-row' not in mh and mh.count('ovv-row') <= full_rows,
             '空档行 %d → main=1 %d' % (full_break_rows, mh.count('ovv-break-row')))
        r = c.get(u['overview'], query_string={'grades': '高一'})
        mh = r.get_data(as_text=True)
        mh_grades = sorted({g for g in re.findall(r'data-grade="([^"]*)"', mh) if g})
        case('年级过滤 + 仍可切回', mh_grades == ['高一']
             and ('grades=高二' in mh or 'grades=%E9%AB%98%E4%BA%8C' in mh),
             f'年级={mh_grades} 含切回={("grades=" in mh)}')
        # 2026-10-10 用户要求：总课表不再显示教室（原 room=1 开关已去掉）
        r = c.get(u['overview'], query_string={'room': '1'})
        # 2026-10-11 合并回归发现：静态资源 ?v= 取自文件 mtime，数字串可能恰好含「101」
        # （实测 ?v=1791705101）导致本断言假阳性；比对前剔除版本串，只查真实渲染内容
        _room_html = re.sub(r'\?v=\d+', '', r.get_data(as_text=True))
        case('总课表不显示教室（room=1 也不渲染）',
             '101' not in _room_html)
        # 2026-10-12（用户要求）：侧栏「全校总课表」入口及其无 sid 跳转路由一并下线，
        # 入口改由学期卡片直接带 sid 进入 —— 旧地址按纪律给生 404
        r = c.get('/academic/schedule/overview', follow_redirects=False)
        case('旧「全校总课表」入口地址已下线 404', r.status_code == 404, str(r.status_code))
        r = c.get(u['export'], query_string={'view_type': 'overview'})
        try:
            wb = excel_sheets(r.data)
            case('总课表 Excel（sheet 与内容）',
                 '全校总课表' in wb.sheetnames and any(
                     '高一' in str(cell.value) for row in wb['全校总课表'].iter_rows()
                     for cell in row if cell.value))
        except Exception as e:  # noqa: BLE001
            case('总课表 Excel 可解析', False, str(e))
        # 2026-10-14（用户报障「导出的 Excel 只有各个班的表」）：整校导出＝全校 + 年级 + 班级三层
        r = c.get(u['export'], query_string={'view_type': 'all'})
        try:
            wb = excel_sheets(r.data)
            names = wb.sheetnames
            case('整校导出：全校表在最前 + 各年级表 + 各班表齐全',
                 names and names[0] == '全校总课表'
                 and {'高一总课表', '高二总课表'} <= set(names)
                 and {'高一01班', '高一02班', '高二01班'} <= set(names),
                 str(names))
            _gt = ' '.join(str(v) for row in wb['高一总课表'].iter_rows()
                           for v in (cell.value for cell in row) if v)
            case('年级总课表内容＝该年级各班并列（不含其它年级）',
                 '高一01班' in _gt and '高一02班' in _gt and '张语文' in _gt
                 and '高二01班' not in _gt)
        except Exception as e:  # noqa: BLE001
            case('整校导出可解析', False, str(e))
        # 年级导出同样先给年级总课表，再给各班表
        r = c.get(u['export'], query_string={'view_type': 'grade', 'grade': '高一'})
        try:
            names = excel_sheets(r.data).sheetnames
            case('年级导出：年级总课表 + 该年级各班表',
                 names and names[0] == '高一总课表'
                 and {'高一01班', '高一02班'} <= set(names), str(names))
        except Exception as e:  # noqa: BLE001
            case('年级导出可解析', False, str(e))
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
    """2026-10-10：作息表已并入「作息时间」——旧地址重定向，视图内容在作息时间页。"""
    with app.app_context():
        u = _urls()
        from flask import url_for
        periods_url = url_for('academic.schedule_periods', sid=TS_ID)
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['timetable'], follow_redirects=False)
        case('旧作息表地址重定向到作息时间', r.status_code in (301, 302)
             and r.headers.get('Location', '').endswith(f'/schedule/{TS_ID}/periods'),
             f"{r.status_code} {r.headers.get('Location')}")
        r = c.get(periods_url)
        html = r.get_data(as_text=True)
        case('作息时间页 200（含作息表视图）', r.status_code == 200, str(r.status_code))
        case('含正课/早读/晚自习分类',
             '正课' in html and '晚自习' in html and '早读' in html)
        case('含时长与统计', '分钟' in html and '小时' in html)
        case('内嵌作息表视图', '作息时间表' in html)
        # 2026-10-12 更名：「节次配置」→「作息时间」（只改称呼，入口/标识不变）
        case('页面命名为「作息时间」（旧名「节次配置」不残留）',
             '作息时间' in html and '节次配置' not in html)
        r = c.get(u['timetable_index'], follow_redirects=False)
        case('作息表入口重定向到作息时间', r.status_code in (301, 302)
             and r.headers.get('Location', '').endswith(f'/schedule/{TS_ID}/periods'),
             f"{r.status_code} {r.headers.get('Location')}")


# ══════════════════════════════════════════════════════════════════════════════
# 节次数量不限 + 全局作息模板（2026-10-10 用户报障：「谁让你限制 13 节的」）
# ══════════════════════════════════════════════════════════════════════════════
@item('PERIODS')
def check_periods_unlimited_and_global():
    """① 一天几节不设上限（能存到第 15 节）；② 作息可存为全局模板并复用到其它学期。"""
    from flask import url_for
    from app.modules.academic.services import schedule_service as svc
    from app.modules.academic.services.schedule_service import (
        apply_global_periods, get_global_periods_info, save_global_periods)
    from app.models.system_setting import SystemSetting

    with app.app_context():
        # 页面文案：不再出现「一天最多 13 节」
        periods_url = url_for('academic.schedule_periods', sid=TS_ID)
        dump = svc.create_schedule('回归节次学期', '2099-2100', '第二学期')
        dump_id = dump.id
        inherited = svc.create_schedule('回归继承作息学期', '2099-2100', '第二学期')
        inherited_id = inherited.id

        # ① 造 15 节（原上限 13 会被静默丢掉第 14/15 节）
        data = [{'period_number': i, 'period_name': f'第{i}节',
                 'start_time': f'{6 + i:02d}:00', 'end_time': f'{6 + i:02d}:40',
                 'period_type': 'morning' if i <= 7 else 'afternoon', 'sort_order': i}
                for i in range(1, 16)]
        svc.save_periods(dump_id, data, remove_missing=True)
        got = {p.period_number for p in svc.get_periods(dump_id)}
        case('节次数量不设上限：15 节全部保存', len(got) == 15, str(sorted(got)))
        case('第 14 / 15 节确实入库（原 13 上限会丢）', got >= {14, 15})

        # ② 存为全局模板 → 新建学期默认套用
        ok, msg = save_global_periods(dump_id)
        case('作息可存为全局模板', ok, msg)
        info = get_global_periods_info()
        case('全局作息模板可读（15 节）', bool(info) and info['count'] == 15, str(info))

        # ③ 套用到另一个学期（先压成 3 节，再套回 15 节）
        svc.save_periods(inherited_id, [{'period_number': i, 'period_name': f'第{i}节',
                                         'period_type': 'morning', 'sort_order': i}
                                        for i in (1, 2, 3)], remove_missing=True)
        case('（前置）该学期被压到 3 节',
             len(svc.get_periods(inherited_id)) == 3)
        ok2, msg2, _res = apply_global_periods(inherited_id)
        case('套用全局作息：该学期恢复到 15 节',
             ok2 and len(svc.get_periods(inherited_id)) == 15, f'{ok2} {msg2}')

        # ④ 时长口径（2026-10-10 用户给定）：早读 40 / 正课 45 / 晚自习 50 分钟
        def _dur(p):
            h1, m1 = p['start_time'].split(':')
            h2, m2 = p['end_time'].split(':')
            return (int(h2) * 60 + int(m2)) - (int(h1) * 60 + int(m1))

        tpl = get_default_periods()
        reading = [p for p in tpl if '早读' in p['period_name']]
        evening = [p for p in tpl if p['period_type'] == 'evening']
        normal = [p for p in tpl if p['period_type'] in ('morning', 'afternoon')
                  and '早读' not in p['period_name']]
        case('默认作息：早读 40 分钟',
             bool(reading) and all(_dur(p) == 40 for p in reading),
             str([_dur(p) for p in reading]))
        case('默认作息：正课 45 分钟',
             bool(normal) and all(_dur(p) == 45 for p in normal),
             str(sorted({_dur(p) for p in normal})))
        case('默认作息：晚自习 50 分钟（原默认是 60）',
             bool(evening) and all(_dur(p) == 50 for p in evening),
             str(sorted({_dur(p) for p in evening})))

        # ⑤ 清场：删全局模板与两个临时学期（否则会影响后面的建学期用例）
        row = SystemSetting.query.filter_by(key='global_periods').first()
        if row:
            db.session.delete(row)
            db.session.commit()
        svc.delete_schedule(inherited_id)
        svc.delete_schedule(dump_id)
        case('清场：全局作息模板已移除', get_global_periods_info() is None)

    # 页面级：作息时间页不再出现「一天最多」字样，且给出全局作息入口
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get(periods_url).get_data(as_text=True)
        case('作息时间页不再写「一天最多 13 节」',
             '一天最多' not in html and '数量不限' in html)
        case('作息时间页给出「存为全局作息」入口',
             '/periods/global-save' in html)
        case('作息时间页有「时长」列与 40/45/50 快捷设置',
             'period-dur-sel' in html and '改时长' in html)
        case('作息时间页写明常规时长口径（早读40/正课45/晚自习50）',
             '常规时长' in html and '早读 40' in html and '晚自习 50' in html)


# ══════════════════════════════════════════════════════════════════════════════
# 课表二期改造（2026-10-10）：导入统一 / 使用情况&校历下线 / 归档语义 / 草稿学期不 500
# ══════════════════════════════════════════════════════════════════════════════
@item('SCHED2')
def check_sched_v2():
    from flask import url_for
    from app.modules.academic.services import schedule_service as svc
    from app.modules.academic.services import term_service as tsvc

    with app.app_context():
        smart_url = url_for('academic.schedule_smart_import', sid=TS_ID)
        smart_tpl_url = url_for('academic.schedule_smart_template', sid=TS_ID)
        overview_url = url_for('academic.schedule_overview', sid=TS_ID)
        # 全新草稿学期：模拟「新建学期后立即点导入」
        draft = svc.create_schedule('回归草稿学期', '2099-2100', '第一学期')
        draft_sid = draft.id
        draft_import_url = url_for('academic.schedule_smart_import', sid=draft_sid)
        draft_periods_url = url_for('academic.schedule_periods', sid=draft_sid)
        manage_url = url_for('academic.schedule_manage')
        draft_error = ''
        try:
            svc.set_current_term(draft_sid)
        except ValueError as e:
            draft_error = str(e)

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)

        # 1) 「创建新课后导入直接报错」修复：草稿学期页面不再 500
        r = c.get(draft_import_url)
        case('草稿学期：导入页 200（原 500）', r.status_code == 200, str(r.status_code))
        r = c.get(draft_periods_url)
        case('草稿学期：作息时间页 200（草稿提示条可渲染）',
             r.status_code == 200, str(r.status_code))
        case('未启用提示条可见', '未启用' in r.get_data(as_text=True))

        # 2) 逐行长表导入下线：统一到「整班 · 原样导入」
        # Location 是相对路径（/academic/...），按后缀断言
        r = c.get(f'/academic/schedule/{TS_ID}/import', follow_redirects=False)
        case('旧长表导入地址 → 原样导入',
             r.status_code in (301, 302)
             and r.headers.get('Location', '').endswith(
                 f'/schedule/{TS_ID}/smart-import'),
             f"{r.status_code} {r.headers.get('Location')}")
        r = c.get(f'/academic/schedule/{TS_ID}/template', follow_redirects=False)
        case('旧长表模板下载 → 整班模板',
             r.status_code in (301, 302)
             and r.headers.get('Location', '').endswith(
                 f'/schedule/{TS_ID}/smart-import/template.xlsx'),
             f"{r.status_code} {r.headers.get('Location')}")
        r = c.get(smart_url)
        html = r.get_data(as_text=True)
        case('导入页主推原样/整班（含整批周次）',
             '整批周次' in html and '整班矩阵' in html)
        case('导入页说明两种版式（全校总课表 + 整班矩阵）',
             '全校总课表' in html and '形态 ①' in html and '形态 ②' in html)
        r = c.get(smart_tpl_url + '?layout=full')
        case('全校总课表模板可下载',
             r.status_code == 200
             and 'attachment' in r.headers.get('Content-Disposition', ''),
             str(r.status_code))
        r = c.get(smart_tpl_url + '?blank=1')
        case('整班模板（空白）可下载',
             r.status_code == 200
             and 'attachment' in r.headers.get('Content-Disposition', ''),
             str(r.status_code))

        # 3) 使用情况 / 校历 下线
        for path, label in ((f'/academic/schedule/{TS_ID}/usage', '使用情况'),
                            (f'/academic/schedule/{TS_ID}/usage/export', '使用情况导出'),
                            (f'/academic/schedule/{TS_ID}/calendar', '校历')):
            r = c.get(path)
            case(f'{label}已下线 404', r.status_code == 404, str(r.status_code))

        # 4) 全校总课表：信息提示条已删
        html = c.get(overview_url).get_data(as_text=True)
        case('全校总课表不再显示说明提示条', '每个年级一张总表' not in html)

        # 5) 归档语义：与「设为当前」区分开
        case('未启用学期不能设为当前（提示先激活）', '未启用' in draft_error, draft_error)

        # 6) 2026-10-10 状态口径：未启用要显示「未启用」；归档不再要求先「激活」
        html = c.get(manage_url).get_data(as_text=True)
        # 注意：夹具学期名本身叫「回归草稿学期」，所以只能断言标签文案，不能断言页面无「草稿」二字
        case('学期管理页：未启用状态显示为「未启用」', '未启用' in html)
        case('状态标签映射 draft → 未启用（不再对外叫「草稿」）',
             SCHEDULE_STATUS.get('draft') == '未启用', str(SCHEDULE_STATUS))
        case('学期管理页：未启用学期直接给「归档」入口（不必先激活）',
             f'/schedule/{draft_sid}/archive-term' in html,
             '未启用学期缺少归档入口')
        # 反向：正在使用的学期不该有手动归档入口（由"激活别的学期"自动归档）
        case('学期管理页：启用中的学期不给手动归档入口',
             f'/schedule/{TS_ID}/archive-term' not in html,
             '启用中的学期仍挂着归档按钮')
        # 2026-10-12（用户要求精简界面）：状态提示「切换启用其他学期时自动归档」已删除，
        # 低频/危险操作收进「更多」下拉（断言改为"已下线"口径，不是漏测）
        case('学期管理页：不再显示「自动归档」状态提示',
             '切换启用其他学期时自动归档' not in html)
        case('学期管理页：低频操作收进「更多」下拉（年级课表入口仍在）',
             '更多' in html and 'dropdown-menu' in html
             and f'/schedule/{TS_ID}/grade/' in html,
             '更多下拉缺失或年级课表入口丢失')
        # 2026-10-14（用户要求）：还没导入课表的学期，主按钮 = 「导入课表」
        # （原先把「全校总课表」摆在最前，空表点进去什么都没有，还挡着导入引导）
        draft_import_hits = html.count(f'/schedule/{draft_sid}/smart-import')
        case('学期管理页：空课表学期标出「尚未导入」', '尚未导入' in html)
        case('学期管理页：空课表学期主按钮为「导入课表」（不再是全校总课表）',
             draft_import_hits == 1 and f'/schedule/{draft_sid}/overview' not in html,
             f'导入入口 {draft_import_hits} 个；'
             + ('总课表按钮仍在' if f'/schedule/{draft_sid}/overview' in html else '已撤'))
        # 2026-10-12（用户要求）：侧栏「全校总课表」入口删除 —— 学期卡片上已有该按钮，
        # 侧栏再挂一个是重复入口；原来只为侧栏服务、无 sid 的跳转路由随之下线
        case('侧栏不再挂「全校总课表」重复入口',
             '/academic/schedule/overview"' not in html,
             '侧栏仍有无 sid 的全校总课表入口')

    # 归档临时未启用学期（写快照）→ 出现在历史课表
    with app.app_context():
        ok, _msg = tsvc.archive_term(draft_sid)
        ts2 = db.session.get(TermSchedule, draft_sid)
        case('归档：写快照并置 archived（移入历史课表）',
             ok and ts2.status == 'archived', f'{ok} {ts2.status}')
        case('归档后取消当前标记', not ts2.is_current)
        history_url = url_for('academic.schedule_history')
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get(history_url).get_data(as_text=True)
        case('历史课表页列出归档学期', '回归草稿学期' in html)
        # 2026-10-10：页面分工 —— 归档的只进「历史课表」，学期管理只管在用/待启用
        case('历史课表页给出「重新启用」入口（归档可回退）',
             f'/schedule/{draft_sid}/activate' in html,
             '归档学期没有重新启用入口')
        m_html = c.get(manage_url).get_data(as_text=True)
        case('学期管理页不再列已归档学期', '回归草稿学期' not in m_html)
        case('学期管理页仍列启用中的学期（入口为全校总课表）',
             f'/schedule/{TS_ID}/overview' in m_html)


# ══════════════════════════════════════════════════════════════════════════════
# 课表查询三项（2026-10-12 新增：教师课表 / 备课组课表 / 教研组课表）
# ══════════════════════════════════════════════════════════════════════════════
@item('GROUPQ')
def check_group_schedule_query():
    """① 成员按本学期课表聚合（不另配组织关系表）——备课组＝年级×学科、教研组＝学科跨年级；
    ② 组长标注取 SubjectLeader（备课组页＝该年级备课组长，教研组页＝教研组长）；
    ③ 共同空档＝当天全组都没课的节次（排除课间空档）；
    ④ 三个入口都是查看类页面（academic.view 即可，不限课表管理员）。"""
    from flask import url_for
    from app.modules.academic.services import schedule_service as svc
    from app.models.academic import SubjectLeader

    with app.app_context():
        # 2026-10-14：学科组长分两类 —— 备课组长（年级 × 学科）与教研组长
        # （不分年级，grade=''）；年级备课组长 + 全校教研组长各一条：两种标签各有归属。
        # **用完即清理**：LEADER 项断言 SubjectLeader 全表只有它自己造的那条，
        # 这里留下的夹具会把那条断言连带打红。
        ld1 = SubjectLeader(school_year='2026-2027', term='第一学期', grade='高一',
                            subject='语文', category='备课组长',
                            leader_uid='T900001', leader_name='张语文')
        ld2 = SubjectLeader(school_year='2026-2027', term='', grade='',
                            subject='语文', category='教研组长',
                            leader_uid='T900001', leader_name='张语文')
        db.session.add_all([ld1, ld2])
        db.session.commit()
        ld_ids = [ld1.id, ld2.id]

        g = svc.get_group_view(TS_ID, scope='lesson', grade='高一', subject='语文')
        case('备课组按「年级×学科」从课表聚合成员',
             [m['name'] for m in g['members']] == ['张语文'],
             str([m['name'] for m in g['members']]))
        case('备课组只算本年级该学科的课（别的年级不混入）',
             bool(g['members']) and all(m['grades'] == ['高一'] for m in g['members']),
             str([m['grades'] for m in g['members']]))
        case('组长标注取自 SubjectLeader（高一备课组长）',
             g['members'][0]['roles'] == ['高一备课组长'], str(g['members'][0]['roles']))
        case('组长排成员列表最前', g['members'][0]['is_leader'])

        sub = svc.get_group_view(TS_ID, scope='subject', subject='语文')
        case('教研组跨年级聚合（张语文：高一 + 高二）',
             sub['member_count'] == 1 and sub['members'][0]['grades'] == ['高一', '高二'],
             str([m['grades'] for m in sub['members']]))
        case('教研组页同时标出教研组长（全校级登记）与年级备课组长',
             set(sub['members'][0]['roles']) == {'教研组长', '高一备课组长'},
             str(sub['members'][0]['roles']))

        mon = svc.get_group_view(TS_ID, scope='lesson', grade='高一', subject='语文', day=1)
        nums = [p['period_number'] for p in mon['free_periods']]
        case('共同空档排除了有人上课的节次（周一第2节）', bool(nums) and 2 not in nums,
             str(nums))

    with app.app_context():
        u_lesson = url_for('academic.schedule_lesson_group', sid=TS_ID,
                           grade='高一', subject='语文')
        u_subject = url_for('academic.schedule_subject_group', sid=TS_ID, subject='语文')
        u_teacher = url_for('academic.schedule_teacher_query', sid=TS_ID)
        u_teacher_full = url_for('academic.schedule_teacher_query', sid=TS_ID,
                                 uid='T900001')
        u_manage = url_for('academic.schedule_manage')
        u_home = url_for('academic.academic_home')

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        h = c.get(u_lesson).get_data(as_text=True)
        case('备课组课表页 200：渲染成员行 + 表格头',
             '张语文' in h and '教师' in h and '备课组课表' in h)
        case('组课表格内副行是任教班级（行＝教师，格内看班级）', '高一01班' in h)
        case('组课表页给出共同空档提示', '共同空档' in h)
        case('星期段标出「今天」（与全校总课表同口径）', '今天' in h)
        case('组长徽章渲染到页面', '高一备课组长' in h)
        h2 = c.get(u_subject).get_data(as_text=True)
        case('教研组课表页 200 且标出教研组长', '教研组长' in h2)
        ht = c.get(u_teacher).get_data(as_text=True)
        case('教师课表入口态＝选教师（列出有课教师）',
             '张语文' in ht and '选择教师' in ht)
        case('入口态不渲染个人课表统计卡', '每周课时统计' not in ht)
        hf = c.get(u_teacher_full).get_data(as_text=True)
        case('带 uid 直接出该教师课表（复用原页）',
             '张语文' in hf and '每周课时统计' in hf)
        # 2026-10-12（用户要求）：三个查询页**合成一个入口** —— 侧栏撤掉三项，统一入口在
        # 教务工作台，页内用 tab 互切（三个 URL 仍各自独立，可直接收藏/分享/后退）
        case('三个查询页顶部都有 tab 条，可互切',
             '/academic/schedule/lesson-group' in h
             and '/academic/schedule/subject-group' in h
             and '/academic/schedule/teacher' in h
             and '/academic/schedule/teacher' in h2
             and '/academic/schedule/lesson-group' in h2
             and '/academic/schedule/lesson-group' in ht)
        mhtml = c.get(u_manage).get_data(as_text=True)
        case('侧栏不再分挂三个查询入口（已合成到工作台）',
             '/academic/schedule/lesson-group' not in mhtml
             and '/academic/schedule/subject-group' not in mhtml)
        hhome = c.get(u_home).get_data(as_text=True)
        case('教务工作台给出统一「课表查询」入口',
             '课表查询' in hhome and '/academic/schedule/teacher' in hhome)
    with app.test_client() as c:
        # 查看类放开的是「教务模块内可见即可看」（academic.view），不外溢给班主任等
        # 非教务角色 —— 他们连侧栏教务菜单都没有；这里钉住 403（不是 500、更不是 200）
        login(c, 'ac_head', TEST_PWD)
        r = c.get(u_subject)
        case('无 academic.view 的账号访问组课表被拦（403）',
             r.status_code == 403, str(r.status_code))
    with app.test_client() as c:
        r = c.get(u_lesson)
        case('未登录访问组课表被拦（302）', r.status_code in (301, 302),
             str(r.status_code))

    with app.app_context():
        # 清理组长夹具：上面两个页面断言要能看到组长徽章，所以清理只能放在**全部断言之后**
        # （放前面会把「组长徽章渲染到页面」一起打红）—— LEADER 项还断言全表只有它那条。
        for _lid in ld_ids:
            _row = db.session.get(SubjectLeader, _lid)
            if _row:
                db.session.delete(_row)
        db.session.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 学期定位 / 周次口径 / 统一调课冲突（2026-10-10 三处用户实测报障的回归）
# ══════════════════════════════════════════════════════════════════════════════
@item('TERMWK')
def check_term_week_bulk_swap():
    """① 学期定位以「当前生效学期」为准（不被归档学期的日期区间抢走）；
    ② 周次按自然周（周一~周日）；③ 统一调课冲突按周次交集判定。"""
    from app.modules.academic.services import swap_service, term_service

    with app.app_context():
        # ① 一个"日期覆盖今天"的已归档学期，不能抢走当前生效学期
        arch = TermSchedule(name='回归归档学期', school_year='2020-2021',
                            term='第一学期', status='archived',
                            start_date=date(2020, 9, 1), end_date=date(2030, 6, 30),
                            total_weeks=20)
        db.session.add(arch)
        db.session.commit()
        got, _week = term_service.resolve_schedule_by_date(date.today())
        case('学期定位：日期命中的已归档学期不抢当前生效学期',
             got is not None and got.id == TS_ID,
             f'取到 {got.id if got else None}，应为生效学期 {TS_ID}')
        case('调课/查课同一口径 get_active_schedule()',
             getattr(swap_service.get_active_schedule(), 'id', None) == TS_ID)

        # ② 周次：周二开学 → 第 1 周到周日结束，第 2 周从周一起（不漂移）
        t = TermSchedule(name='周次校验', school_year='2026-2027', term='第一学期',
                         start_date=date(2026, 9, 1), end_date=date(2027, 1, 22),
                         total_weeks=20)
        case('周次：第 1 周 = 开学日 ~ 该周周日（不往后漂 7 天）',
             t.get_week_date_range(1) == (date(2026, 9, 1), date(2026, 9, 6)),
             str(t.get_week_date_range(1)))
        case('周次：第 2 周从周一开始（自然周）',
             t.get_week_date_range(2) == (date(2026, 9, 7), date(2026, 9, 13)),
             str(t.get_week_date_range(2)))
        case('周次：周日算本周、周一进下一周',
             t.get_week_number(date(2026, 9, 6)) == 1
             and t.get_week_number(date(2026, 9, 7)) == 2,
             f'{t.get_week_number(date(2026, 9, 6))}/'
             f'{t.get_week_number(date(2026, 9, 7))}')
        case('周次：开学前一天 / 未配置起止日期 → None',
             t.get_week_number(date(2026, 8, 30)) is None
             and TermSchedule(name='x', school_year='x',
                              term='x').get_week_number() is None)

        # ③ 统一调课：单双周同格（周次不交集）不该被判冲突
        made = []
        for subj, wr in (('语文', '单周'), ('数学', '双周')):
            e = ScheduleEntry(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                              weekday=6, period_number=12, subject=subj,
                              week_range=wr, entry_type='normal')
            db.session.add(e)
            made.append(e)
        db.session.commit()
        ok, msg, res = swap_service.bulk_apply_swap(
            applicant_uid='regress', applicant_name='回归',
            entry_ids=[e.id for e in made], new_weekday=7, new_period=12,
            is_permanent=True, reason='回归')
        case('统一调课：单双周同格一起调不误报冲突',
             ok and res.get('created') == 2, f'{msg} {res.get("conflicts")}')
        for s in ScheduleSwap.query.filter_by(applicant_uid='regress').all():
            db.session.delete(s)
        for e in made:
            db.session.delete(e)
        db.session.commit()

        # ③b 原课表同一格本就重复（周次重叠）→ 调课时点名"原课表本就有两门课"
        made2 = []
        for subj in ('语文', '数学'):
            e = ScheduleEntry(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                              weekday=6, period_number=11, subject=subj,
                              week_range='1-20', entry_type='normal')
            db.session.add(e)
            made2.append(e)
        db.session.commit()
        ok2, msg2, res2 = swap_service.bulk_apply_swap(
            applicant_uid='regress', applicant_name='回归',
            entry_ids=[e.id for e in made2], new_weekday=7, new_period=11,
            is_permanent=True, reason='回归')
        descs = ' '.join(c.get('desc', '') for c in (res2.get('conflicts') or []))
        case('统一调课：原课表同格重复时给出可操作提示（点名重复条目）',
             (not ok2) and '本就有' in descs, f'{msg2} {descs}')
        for e in made2:
            db.session.delete(e)
        db.session.delete(arch)
        db.session.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 统一调课 = 一条记录（2026-10-10 用户要求：整批一次审批，别让我一条条点）
# ══════════════════════════════════════════════════════════════════════════════
@item('SWAPBATCH')
def check_swap_batch_one_record():
    """统一调课在列表里折叠成一条：计数按批、详情列全本批课程、
    点一次审批/执行/撤销即整批生效（不再逐条点）。"""
    from app.modules.academic.services import swap_service

    with app.app_context():
        made = []
        for g, cn, pn, subj in (('高一', '01班', 5, '语文'),
                                ('高一', '02班', 5, '数学'),
                                ('高二', '01班', 6, '英语')):
            e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                              weekday=2, period_number=pn, subject=subj,
                              week_range='1-20', entry_type='normal')
            db.session.add(e)
            made.append(e)
        db.session.commit()
        eids = [e.id for e in made]
        ok, msg, res = swap_service.bulk_apply_swap(
            applicant_uid='batchreg', applicant_name='批次回归',
            entry_ids=eids, new_weekday=7, new_period=13,
            is_permanent=True, reason='批次回归')
        bid = res.get('batch_id')
        case('统一调课：整批共享一个批次号', ok and bid, f'{msg} {res}')
        if not (ok and bid):
            return
        # ── 列表 / 计数：整批只算一条 ──
        items, _pg = swap_service.get_swap_list(schedule_id=TS_ID, is_reviewer=True,
                                                per_page=100)
        rows = [it for it in items if it.get('batch_id') == bid]
        case('列表：一次统一调课只出现一条记录', len(rows) == 1, f'{len(rows)} 条')
        case('列表：该条记录带批次规模（共 3 门）',
             bool(rows) and rows[0].get('batch_count') == 3,
             str(rows[0].get('batch_count') if rows else None))
        rep_id = rows[0]['id'] if rows else 0
        pend, _p2 = swap_service.get_swap_list(status='pending', schedule_id=TS_ID,
                                              is_reviewer=True, per_page=100)
        case('待审计数与列表同口径（徽章不按 3 条虚高）',
             swap_service.count_pending(schedule_id=TS_ID) == len(pend),
             f'{swap_service.count_pending(schedule_id=TS_ID)} vs {len(pend)}')
        d = swap_service.get_swap_detail(rep_id)
        case('详情：列出本批全部课程并标出当前记录',
             d['is_batch'] and d['batch_count'] == 3 and len(d['batch_members']) == 3
             and sum(1 for m in d['batch_members'] if m['is_current']) == 1,
             f"batch={d['batch_count']} members={len(d['batch_members'])}")
        chain_len = len(swap_service.approval_chain())

    # ── 页面：整批只渲染一行；审批/执行一次点完整批 ──
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get('/academic/swap').get_data(as_text=True)
        case('列表页：该批次只渲染一行（整批一条记录）',
             html.count('data-batch="3"') == 1, str(html.count('data-batch="3"')))
        case('列表页：标注「共 3 门 · 一次审批」', '共 3 门 · 一次审批' in html)
        # 操作列曾用内联 onclick="event.stopPropagation()" 拦冒泡，而通过/驳回/执行是
        # document 上的委托事件 → 四个按钮全点不动（用户实测报障）。
        case('列表页：操作列不再吞掉点击（委托事件能触发）',
             'event.stopPropagation()' not in html
             and "closest('a, button, .row-actions')" in html,
             '操作列仍有内联 stopPropagation 或行点击未排除操作列')
        hdrs = {'X-CSRFToken': get_csrf(c, '/academic/swap')}
        r = c.post(f'/academic/swap/{rep_id}/approve', data={'review_note': ''},
                   headers=hdrs)
        body = r.get_json() or {}
        with app.app_context():
            sibs = ScheduleSwap.query.filter_by(batch_id=bid).all()
            first = 'pending' if chain_len > 1 else 'approved'
            case('一次审批：整批同级推进（不是只推进一条）',
                 bool(body.get('success')) and len(sibs) == 3
                 and all(s.status == first and (s.approval_step or 0) == (1 if chain_len > 1 else 0)
                         for s in sibs),
                 f'{body.get("message")} {[(s.status, s.approval_step) for s in sibs]}')
        for _ in range(chain_len - 1):
            c.post(f'/academic/swap/{rep_id}/approve', data={'review_note': ''},
                   headers=hdrs)
        with app.app_context():
            case('终审：整批一起 approved',
                 all(s.status == 'approved' for s in
                     ScheduleSwap.query.filter_by(batch_id=bid).all()))
        r = c.post(f'/academic/swap/{rep_id}/execute', data={}, headers=hdrs)
        body = r.get_json() or {}
        with app.app_context():
            st = {s.status for s in ScheduleSwap.query.filter_by(batch_id=bid).all()}
            newn = ScheduleEntry.query.filter(
                ScheduleEntry.original_entry_id.in_(eids),
                ScheduleEntry.is_deleted.is_(False)).count()
            case('一次执行：整批 3 门课全部落到新课表',
                 bool(body.get('success')) and st == {'executed'} and newn == 3,
                 f'status={st} new={newn} {body.get("message")}')

        # 2026-10-11 新增：撤销执行（回退课表）—— 整批回退，原课恢复、生成条目移除
        r = c.post(f'/academic/swap/{rep_id}/revoke', data={}, headers=hdrs)
        body = r.get_json() or {}
        with app.app_context():
            st2 = {s.status for s in ScheduleSwap.query.filter_by(batch_id=bid).all()}
            restored = ScheduleEntry.query.filter(
                ScheduleEntry.id.in_(eids),
                ScheduleEntry.is_deleted.is_(False)).count()
            left_new = ScheduleEntry.query.filter(
                ScheduleEntry.original_entry_id.in_(eids),
                ScheduleEntry.is_deleted.is_(False)).count()
            case('撤销执行：整批回到「已通过」可重执行',
                 bool(body.get('success')) and st2 == {'approved'},
                 f'status={st2} {body.get("message")}')
            case('撤销执行：原 3 门课恢复、生成条目移除',
                 restored == 3 and left_new == 0,
                 f'restored={restored} new={left_new}')

    # ── 撤销整批 ──
    with app.app_context():
        made2 = []
        for g, cn, subj in (('高一', '01班', '化学'), ('高一', '02班', '生物')):
            e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                              weekday=3, period_number=6, subject=subj,
                              week_range='1-20', entry_type='normal')
            db.session.add(e)
            made2.append(e)
        db.session.commit()
        eids2 = [e.id for e in made2]
        # 目标避开上一段执行后已经占了 (周日,13) 的那三条
        ok2, msg2, res2 = swap_service.bulk_apply_swap(
            applicant_uid='batchreg', applicant_name='批次回归',
            entry_ids=eids2, new_weekday=7, new_period=12,
            is_permanent=True, reason='批次回归撤销')
        bid2 = res2.get('batch_id')
        rep2 = ScheduleSwap.query.filter_by(batch_id=bid2).order_by(ScheduleSwap.id).first()
        rep2_id = rep2.id if rep2 else 0
        ok3, msg3, _ = swap_service.cancel_swap(rep2_id, 'batchreg')
        left = ScheduleSwap.query.filter_by(batch_id=bid2,
                                           status='pending').count()
        case('撤销：一条记录撤掉整批（不是只撤第一条）',
             ok3 and left == 0 and '2 门课' in msg3, f'{msg3} 剩余 {left}')
        # 清理（含执行产生的条目与版本记录，别影响其它用例）
        new_ids = [e.id for e in ScheduleEntry.query.filter(
            ScheduleEntry.original_entry_id.in_(eids + eids2)).all()]
        ScheduleVersion.query.filter(
            ScheduleVersion.entry_id.in_(eids + eids2 + new_ids)).delete(
                synchronize_session=False)
        ScheduleEntry.query.filter(
            ScheduleEntry.id.in_(eids + eids2 + new_ids)).delete(
                synchronize_session=False)
        ScheduleSwap.query.filter(
            ScheduleSwap.batch_id.in_([bid, bid2])).delete(
                synchronize_session=False)
        db.session.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 统一调课「整天一起挪」口径（2026-10-14 用户报障：提交一整天的课直接崩）
#  ① 整批让位（与审批/执行同口径）② 走班并行课 ③ 批量检测接口 ④ 失败页回填勾选
# ══════════════════════════════════════════════════════════════════════════════

@item('BULKOFF')
def check_bulk_swap_day_offsets():
    """整天课统一调整的提交口径（2026-10-14 修复）。

    修复前实测（真实学期数据，周三 266 条）：
    - "整体后移 1 节"报 235 条冲突 —— 提交时逐条只排除自己，同批其它条目还占着目标格；
    - "整天挪到周六"报 5 条冲突 —— 并行走班课（不同教学班同格）被判成班级冲突；
    - 前端对每一行各发一次 available-slots（266 个并发请求），页面与提交一起被拖死。
    """
    from app.modules.academic.services import swap_service
    grade, cname = '高三', '88班'

    with app.app_context():
        made = []
        for pn, subj in ((2, '语文'), (3, '数学'), (4, '英语')):
            e = ScheduleEntry(term_schedule_id=TS_ID, grade=grade, class_name=cname,
                              weekday=4, period_number=pn, subject=subj,
                              week_range='1-20', entry_type='normal')
            db.session.add(e)
            made.append(e)
        db.session.commit()
        eids = [e.id for e in made]

        # ① 永久调课整批让位：第 2/3/4 节一起后移 1 节（第 2→3、3→4、4→5）
        ok, msg, res = swap_service.bulk_apply_swap(
            applicant_uid='bulkoff', applicant_name='整天回归',
            entry_ids=eids, new_weekday=None, new_period=None,
            is_permanent=True, reason='整天后移一节', period_offset=1)
        bid = res.get('batch_id')
        case('整批让位：同一班 3 节课整体后移 1 节可一次提交（不再自撞）',
             ok and res.get('created') == 3, f'{msg} conflicts={res.get("conflicts")}')

        # 临时调课不动常规课表 → 原格仍被占，不套用让位口径（该冲突要照报）
        ok_t, msg_t, res_t = swap_service.bulk_apply_swap(
            applicant_uid='bulkoff', applicant_name='整天回归',
            entry_ids=eids[:2], new_weekday=None, new_period=None,
            swap_date=date(2026, 10, 15), is_permanent=False,
            reason='临时整天后移', period_offset=1)
        case('临时调课不套用让位口径（原格仍占用，冲突照报）',
             (not ok_t) and bool(res_t.get('conflicts')), f'{msg_t}')

        # ② 走班并行课：同班同格两条不同教学班（不同教师）一起挪
        par = []
        for tc, subj, tuid in (('地政·地理', '地理', 't_geo'),
                               ('地政·政治', '政治', 't_zz')):
            e = ScheduleEntry(term_schedule_id=TS_ID, grade=grade, class_name=cname,
                              weekday=4, period_number=8, subject=subj,
                              teacher_uid=tuid, teacher_name=subj + '老师',
                              week_range='1-20', teaching_class=tc,
                              entry_type='normal')
            db.session.add(e)
            par.append(e)
        db.session.commit()
        par_ids = [e.id for e in par]
        ok2, msg2, res2 = swap_service.bulk_apply_swap(
            applicant_uid='bulkoff', applicant_name='整天回归',
            entry_ids=par_ids, new_weekday=7, new_period=8,
            is_permanent=True, reason='走班并行课一起挪')
        bid2 = res2.get('batch_id')
        case('走班并行课：不同教学班同格一起调课不再被误判冲突',
             ok2 and res2.get('created') == 2, f'{msg2} conflicts={res2.get("conflicts")}')

        # 同名教学班（同一次走班课占多班课表）不算教师冲突：
        # 目标格上有一条"同教师 + 同教学班"的课（另一个班），应放行
        ok3 = False
        bid3 = None
        ex_id = None
        if ok2:
            e_x = ScheduleEntry(term_schedule_id=TS_ID, grade=grade, class_name='89班',
                                weekday=6, period_number=5, subject='地理',
                                teacher_uid='t_geo', teacher_name='地理老师',
                                week_range='1-20', teaching_class='地政·地理',
                                entry_type='normal')
            db.session.add(e_x)
            db.session.commit()
            ex_id = e_x.id
            ok3, msg3, res3 = swap_service.bulk_apply_swap(
                applicant_uid='bulkoff', applicant_name='整天回归',
                entry_ids=[par[0].id], new_weekday=6, new_period=5,
                is_permanent=True, reason='走班课挪到同名教学班那格')
            bid3 = res3.get('batch_id')
            case('走班课：目标格是"同教师 + 同名教学班"的另一行 → 不算教师冲突',
                 ok3, f'{msg3} conflicts={res3.get("conflicts")}')

        # 执行整批：教学班必须随行（丢了它，同格另一条执行时会被判"本班已有课"）
        done = 0
        if ok2:
            for _i in range(4):
                rep = (ScheduleSwap.query.filter_by(batch_id=bid2, status='pending')
                       .order_by(ScheduleSwap.id).first())
                if not rep:
                    break
                swap_service.review_swap_batch(rep.id, 1, '整天回归', 'approve')
            for s in ScheduleSwap.query.filter_by(batch_id=bid2,
                                                  status='approved').all():
                o, _m, _s = swap_service.execute_swap(s.id, 1, '整天回归')
                done += 1 if o else 0
            tcs = sorted((e.teaching_class or '') for e in ScheduleEntry.query.filter_by(
                term_schedule_id=TS_ID, grade=grade, class_name=cname,
                weekday=7, period_number=8, is_deleted=False).all())
            case('整批执行：走班并行两条都成功、教学班随行不丢',
                 done == 2 and tcs == ['地政·地理', '地政·政治'],
                 f'done={done} teaching_class={tcs}')

    # ③ 批量检测接口（一次请求算全部条目的目标格）＋ ④ 失败页回填勾选
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, '/academic/swap/bulk')
        hdrs = {'X-CSRFToken': token}
        # 语文(第 2 节) 的目标格选第 3 节 —— 本班数学还在（未执行），必冲突
        r = c.post('/academic/api/swap/bulk-check', data={
            'entry_ids': str(eids[0]), 'mode': 'target',
            'new_weekday': '4', 'new_period': '3', 'is_permanent': '1'}, headers=hdrs)
        data = (r.get_json() or {}).get('data') or {}
        rows = data.get('rows') or []
        case('批量检测接口：一次请求返回逐条结果（与提交同口径）',
             r.status_code == 200 and data.get('total') == 1 and len(rows) == 1
             and rows[0].get('ok') is False and '本班已有' in (rows[0].get('desc') or ''),
             f'{r.status_code} {str(data)[:160]}')
        r2 = c.post('/academic/swap/bulk', data={
            'csrf_token': token, 'entry_ids': str(eids[0]), 'mode': 'target',
            'new_weekday': '4', 'new_period': '3', 'is_permanent': '1',
            'reason': '回填回归'}, headers=hdrs)
        page = r2.get_data(as_text=True)
        case('提交失败页：带回填数据与批量检测地址（自动勾回非冲突条目）',
             'bulkRetryData' in page and 'bulkCheckUrl' in page
             and '"conflict_ids"' in page, f'status={r2.status_code}')

    # 清理（含执行产生的条目与版本记录，别影响其它用例）
    with app.app_context():
        all_ids = list(eids) + list(par_ids)
        if ex_id:
            all_ids.append(ex_id)
        new_ids = [e.id for e in ScheduleEntry.query.filter(
            ScheduleEntry.original_entry_id.in_(all_ids)).all()]
        ScheduleVersion.query.filter(
            ScheduleVersion.entry_id.in_(all_ids + new_ids)).delete(
                synchronize_session=False)
        ScheduleEntry.query.filter(
            ScheduleEntry.id.in_(all_ids + new_ids)).delete(synchronize_session=False)
        bids = [b for b in (bid, bid2, bid3) if b]
        if bids:
            ScheduleSwap.query.filter(ScheduleSwap.batch_id.in_(bids)).delete(
                synchronize_session=False)
        db.session.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 个人调课 = 多节一起申请（2026-10-12 用户要求：多节课一块申请、各自目标时段）
# ══════════════════════════════════════════════════════════════════════════════
@item('SWAPMULTI')
def check_personal_multi_swap():
    """个人调课一次提交多节：各自目标时段、共享批次号（整批一次审批/执行），
    并支持同批让位/互换（整体后移一节、两节互换不算冲突）。"""
    from app.modules.academic.services import swap_service

    def _mk(g, cn, wd, pn, subj, tuid, tname):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range='1-20', entry_type='normal')
        db.session.add(e)
        return e

    made_ids, swap_ids, exec_rep = [], [], None
    with app.app_context():
        # ── ① 两节各自目标：一次提交成功、共享批次号 ──
        e1 = _mk('高三', '03班', 4, 1, '语文', 'T900001', '张语文')
        e2 = _mk('高三', '03班', 4, 2, '数学', 'T900002', '李数学')
        db.session.commit()
        made_ids += [e1.id, e2.id]
        ok, msg, res = swap_service.apply_swap_multi(
            applicant_uid='multireg', applicant_name='多节回归',
            items=[{'entry_id': e1.id, 'new_weekday': 5, 'new_period': 5},
                   {'entry_id': e2.id, 'new_weekday': 5, 'new_period': 6}],
            is_permanent=True, reason='多节回归')
        bid_a = res.get('batch_id')
        case('多节申请：两节各自目标一次提交成功（共享批次号）',
             ok and res.get('created') == 2 and bool(bid_a), f'{msg} {res}')
        rows = [it for it in swap_service.get_swap_list(
            schedule_id=TS_ID, is_reviewer=True, per_page=100)[0]
            if bid_a and it.get('batch_id') == bid_a]
        case('多节申请：列表折叠成一条（共 2 节 · 一次审批）',
             len(rows) == 1 and rows[0].get('batch_count') == 2,
             f'{len(rows)} 条 {rows[0].get("batch_count") if rows else None}')
        d = swap_service.get_swap_detail(rows[0]['id']) if rows else {}
        case('多节申请：详情列出两节各自的目标时段',
             bool(d) and d.get('batch_count') == 2
             and len({m['target_slot'] for m in (d.get('batch_members') or [])}) == 2,
             f"batch={d.get('batch_count')} slots="
             f"{[m['target_slot'] for m in (d.get('batch_members') or [])]}")
        if bid_a:
            swap_ids += [s.id for s in ScheduleSwap.query.filter_by(batch_id=bid_a).all()]

        # ── ② 单节走同一入口：不打批次号（保持旧行为） ──
        e3 = _mk('高三', '03班', 4, 3, '英语', 'T900003', '王英语')
        db.session.commit()
        made_ids.append(e3.id)
        ok1, msg1, res1 = swap_service.apply_swap_multi(
            applicant_uid='multireg', applicant_name='多节回归',
            items=[{'entry_id': e3.id, 'new_weekday': 5, 'new_period': 8}],
            is_permanent=True, reason='单节回归')
        case('单节走同一入口：不产生批次号（与旧行为一致）',
             ok1 and res1.get('created') == 1 and not res1.get('batch_id'),
             f'{msg1} {res1}')
        swap_ids += [s.id for s in ScheduleSwap.query.filter(
            ScheduleSwap.original_entry_id == e3.id).all()]

        # ── ③ 批内互斥：两节落到同一格 → 整批不创建 ──
        e4 = _mk('高三', '04班', 4, 1, '语文', 'T900001', '张语文')
        e5 = _mk('高三', '04班', 4, 2, '数学', 'T900002', '李数学')
        db.session.commit()
        made_ids += [e4.id, e5.id]
        before = ScheduleSwap.query.filter_by(applicant_uid='multireg').count()
        ok2, msg2, res2 = swap_service.apply_swap_multi(
            applicant_uid='multireg', applicant_name='多节回归',
            items=[{'entry_id': e4.id, 'new_weekday': 5, 'new_period': 9},
                   {'entry_id': e5.id, 'new_weekday': 5, 'new_period': 9}],
            is_permanent=True, reason='批内互斥')
        after = ScheduleSwap.query.filter_by(applicant_uid='multireg').count()
        case('批内互斥：两节落同一格 → 整批不创建',
             (not ok2) and before == after and bool(res2.get('conflicts')),
             f'{msg2} {before}->{after}')

        # ── ④ 让位/互换：各自落到对方原位置（同批永久一起生效） ──
        ok3, msg3, res3 = swap_service.apply_swap_multi(
            applicant_uid='multireg', applicant_name='多节回归',
            items=[{'entry_id': e4.id, 'new_weekday': 4, 'new_period': 2},
                   {'entry_id': e5.id, 'new_weekday': 4, 'new_period': 1}],
            is_permanent=True, reason='互换回归')
        bid_c = res3.get('batch_id')
        case('两节互换（各自落到对方原位置）允许提交',
             ok3 and bool(bid_c), f'{msg3} {res3}')
        if bid_c:
            sibs = ScheduleSwap.query.filter_by(batch_id=bid_c).order_by(
                ScheduleSwap.id).all()
            swap_ids += [s.id for s in sibs]
            exec_rep = sibs[0].id if sibs else None

        # ── ⑤ 真冲突：目标格被别的课占着 → 整批拒绝并说明 ──
        e6 = _mk('高三', '04班', 5, 3, '化学', 'T900005', '钱化学')
        db.session.commit()
        made_ids.append(e6.id)
        ok4, msg4, res4 = swap_service.apply_swap_multi(
            applicant_uid='multireg', applicant_name='多节回归',
            items=[{'entry_id': e6.id, 'new_weekday': 4, 'new_period': 1}],
            is_permanent=True, reason='真冲突')
        case('目标格被占 → 整批拒绝且给出冲突说明',
             (not ok4) and any('已有课程' in (c.get('desc') or '')
                               for c in (res4.get('conflicts') or [])),
             f'{msg4} {res4.get("conflicts")}')

        # ── ⑥ 临时调课：每节各自的日期 ──
        e7 = _mk('高三', '06班', 4, 1, '语文', 'T900001', '张语文')
        e8 = _mk('高三', '06班', 4, 2, '数学', 'T900002', '李数学')
        db.session.commit()
        made_ids += [e7.id, e8.id]
        ok5, msg5, res5 = swap_service.apply_swap_multi(
            applicant_uid='multireg', applicant_name='多节回归',
            items=[{'entry_id': e7.id, 'new_weekday': 5, 'new_period': 11,
                    'swap_date': date(2026, 10, 16)},
                   {'entry_id': e8.id, 'new_weekday': 6, 'new_period': 12,
                    'swap_date': date(2026, 10, 17)}],
            is_permanent=False, reason='临时多节回归')
        rows5 = (ScheduleSwap.query.filter_by(batch_id=res5.get('batch_id'))
                 .order_by(ScheduleSwap.id).all()) if res5.get('batch_id') else []
        case('临时调课：每节各自的目标日期（10-16 / 10-17）',
             ok5 and len(rows5) == 2
             and {s.swap_date for s in rows5} == {date(2026, 10, 16), date(2026, 10, 17)},
             f'{msg5} {[str(s.swap_date) for s in rows5]}')
        swap_ids += [s.id for s in rows5]

        # ── ⑦ 跨学期条目一起调 → 拒绝 ──
        other = TermSchedule(name='多节回归别学期', school_year='2026-2027',
                             term='第二学期', status='draft')
        db.session.add(other)
        db.session.flush()
        e9 = ScheduleEntry(term_schedule_id=other.id, grade='高三', class_name='07班',
                           weekday=4, period_number=1, subject='语文',
                           teacher_uid='T900001', teacher_name='张语文',
                           week_range='1-20', entry_type='normal')
        db.session.add(e9)
        db.session.commit()
        other_id, e9_id = other.id, e9.id
        ok6, msg6, res6 = swap_service.apply_swap_multi(
            applicant_uid='multireg', applicant_name='多节回归',
            items=[{'entry_id': e7.id, 'new_weekday': 5, 'new_period': 12},
                   {'entry_id': e9.id, 'new_weekday': 5, 'new_period': 12}],
            is_permanent=True, reason='跨学期')
        case('跨学期条目不允许一起调课',
             (not ok6) and any('跨学期' in (c.get('desc') or '')
                               for c in (res6.get('conflicts') or [])),
             f'{msg6} {res6.get("conflicts")}')
        db.session.delete(db.session.get(ScheduleEntry, e9_id))
        db.session.delete(db.session.get(TermSchedule, other_id))
        db.session.commit()
        e4_id, e5_id = e4.id, e5.id

    # ── ⑧ HTTP：申请页契约 + 多节提交 302 到详情页 ──
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get('/academic/swap/apply').get_data(as_text=True)
        case('申请页：多节提交契约（items_json 字段）在位', 'items_json' in html)
        hdrs = {'X-CSRFToken': get_csrf(c, '/academic/swap/apply')}
        with app.app_context():
            e10 = _mk('高三', '05班', 4, 1, '语文', 'T900001', '张语文')
            e11 = _mk('高三', '05班', 4, 2, '数学', 'T900002', '李数学')
            db.session.commit()
            made_ids += [e10.id, e11.id]
            payload = {'items': [
                {'entry_id': e10.id, 'new_weekday': 5, 'new_period': 12},
                {'entry_id': e11.id, 'new_weekday': 5, 'new_period': 13}]}
            p10, p11 = e10.id, e11.id
        r = c.post('/academic/swap/apply',
                   data={'csrf_token': hdrs['X-CSRFToken'], 'reason': 'HTTP 多节回归',
                         'is_permanent': '1',
                         'items_json': json.dumps(payload, ensure_ascii=False)},
                   follow_redirects=False)
        loc = r.headers.get('Location') or ''
        with app.app_context():
            http_sws = ScheduleSwap.query.filter(
                ScheduleSwap.original_entry_id.in_([p10, p11])).all()
            swap_ids += [s.id for s in http_sws]
            case('申请页：多节提交成功并跳到详情页',
                 r.status_code == 302 and '/academic/swap/' in loc
                 and len(http_sws) == 2
                 and len({s.batch_id for s in http_sws}) == 1
                 and all(s.batch_id for s in http_sws),
                 f'{r.status_code} {loc} {len(http_sws)}')
        bad = c.post('/academic/swap/apply',
                     data={'csrf_token': hdrs['X-CSRFToken'], 'reason': '无效条目',
                           'is_permanent': '1',
                           'items_json': json.dumps(
                               {'items': [{'entry_id': 99999999, 'new_weekday': 5,
                                           'new_period': 3}]}, ensure_ascii=False)},
                     follow_redirects=False)
        with app.app_context():
            case('申请页：条目不存在 → 退回申请页（不落库）',
                 bad.status_code == 302 and '/apply' in (bad.headers.get('Location') or '')
                 and not ScheduleSwap.query.filter_by(reason='无效条目').count(),
                 f'{bad.status_code} {bad.headers.get("Location")}')

        # ── ⑨ 批次审批 + 执行：互换两节真正落到对方格子 ──
        if exec_rep:
            with app.app_context():
                chain_len = len(swap_service.approval_chain())
            for _ in range(chain_len):
                c.post(f'/academic/swap/{exec_rep}/approve', data={'review_note': ''},
                       headers=hdrs)
            r2 = c.post(f'/academic/swap/{exec_rep}/execute', data={}, headers=hdrs)
            body = r2.get_json() or {}
            with app.app_context():
                news = ScheduleEntry.query.filter(
                    ScheduleEntry.original_entry_id.in_([e4_id, e5_id]),
                    ScheduleEntry.is_deleted.is_(False)).all()
                got = {(e.weekday, e.period_number, e.subject) for e in news}
                case('整批执行：互换两节各自落到对方格子（不是只改一条）',
                     got == {(4, 2, '语文'), (4, 1, '数学')},
                     f'{got} {body.get("message")}')
                old_deleted = ScheduleEntry.query.filter(
                    ScheduleEntry.id.in_([e4_id, e5_id]),
                    ScheduleEntry.is_deleted.is_(True)).count()
                case('整批执行：原两节都软删除', old_deleted == 2, f'{old_deleted}/2')

    # ── 清理：本项创建的条目 / 调课记录 / 版本（别影响其它用例） ──
    with app.app_context():
        all_ids = [i for i in made_ids if i]
        news = [e.id for e in ScheduleEntry.query.filter(
            ScheduleEntry.original_entry_id.in_(all_ids)).all()] if all_ids else []
        if all_ids or news:
            ScheduleVersion.query.filter(
                ScheduleVersion.entry_id.in_(all_ids + news)).delete(
                    synchronize_session=False)
            ScheduleEntry.query.filter(
                ScheduleEntry.id.in_(all_ids + news)).delete(
                    synchronize_session=False)
        ids = [i for i in swap_ids if i]
        if ids:
            ScheduleSwap.query.filter(ScheduleSwap.id.in_(ids)).delete(
                synchronize_session=False)
        ScheduleSwap.query.filter(
            ScheduleSwap.applicant_uid == 'multireg').delete(
                synchronize_session=False)
        db.session.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 「全校总课表」版式（2026-10-10 新增）：行 = 星期×节次、列 = 班级
# ══════════════════════════════════════════════════════════════════════════════
@item('FULLSCH')
def check_full_school_layout():
    """学校教务手上的总课表（一张表放全校）能直接解析 + 生成同版式模板。"""
    import io as _io

    from openpyxl import Workbook, load_workbook

    from app.models.timetable import get_default_periods
    from app.modules.academic.services import schedule_matrix_import as mi

    class _P:
        def __init__(self, d):
            self.period_name = d['period_name']
            self.period_number = d['period_number']
            self.period_type = d.get('period_type')

    periods = [_P(d) for d in get_default_periods()]

    wb = Workbook()
    ws = wb.active
    ws.title = '全校总课表'
    ws.append(['星期', '节次', '高三1班', '高三2班'])
    ws.append(['星期一', '1节', '历史\n郭艳静', '英语\n王一楠'])
    ws.append([None, '2节', '语文\n刘稳', '数学\n朱丹丽'])
    ws.append(['星期二', '1节', '数学\n朱丹丽', '语文\n刘稳'])
    ws.append([None, '午休', None, None])            # 空行不该产生条目
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    data = mi.parse_school_workbook(buf, periods, ['2024级', '2025级', '2026级'], [])
    case('全校总课表：无错误', not data['errors'], str(data['errors']))
    case('全校总课表：按列拆成 2 个班', len(data['sheets']) == 2,
         str(len(data['sheets'])))
    if len(data['sheets']) == 2:
        a, b = data['sheets']
        case('全校总课表：班级名取自表头列', a['class_name'] == '01班'
             and b['class_name'] == '02班', f"{a['class_name']}/{b['class_name']}")
        case('全校总课表：年级与学期年级对齐（高三→2024级）',
             a['grade'] == '2024级', a['grade'])
        case('全校总课表：星期向下继承（合并单元格）+ 条目数正确',
             len(a['entries']) == 3, str(len(a['entries'])))
        case('全校总课表：格内两行＝学科+教师',
             a['entries'][0]['subject'] == '历史'
             and a['entries'][0]['teacher_name'] == '郭艳静',
             str(a['entries'][0]))
        case('全校总课表：「1节」→ 系统「第1节」（period_number=2）',
             a['entries'][0]['period_number'] == 2,
             str(a['entries'][0]['period_number']))
        case('全校总课表：默认周次 1-18',
             a['entries'][0]['week_range'] == '1-18',
             a['entries'][0]['week_range'])

    # 一格「学科并列 + 教师并列」两行写法 → 配成两条
    items = mi.parse_cell_content('地/政\n陈/邓')
    case('一格多科多师：配对成 2 条',
         len(items) == 2 and {i['subject'] for i in items} == {'地理', '政治'}
         and {i['teacher_name'] for i in items} == {'陈', '邓'}, str(items))
    case('学科带考试类型后缀归一（生物学考→生物）',
         mi.parse_cell_content('生物学考')[0]['subject'] == '生物')

    # 模板：生成的表头与解析器自洽（写进去再读回来）
    tpl = mi.build_full_school_template(periods, classes=[('高三', '01班')],
                                        sample=False)
    wb2 = load_workbook(tpl)
    ws2 = wb2['全校总课表']
    case('全校总课表模板：表头 = 星期|节次|班级列',
         ws2.cell(row=1, column=1).value == '星期'
         and ws2.cell(row=1, column=2).value == '节次'
         and ws2.cell(row=1, column=3).value == '高三01班',
         f'{ws2.cell(row=1, column=1).value}/{ws2.cell(row=1, column=2).value}/'
         f'{ws2.cell(row=1, column=3).value}')
    case('全校总课表模板：含填写说明表', '填写说明' in wb2.sheetnames,
         str(wb2.sheetnames))
    tpl2 = mi.build_full_school_template(periods, classes=[('高三', '01班')])
    wb3 = load_workbook(tpl2)
    case('全校总课表模板（示例）：格子有示例内容',
         bool(wb3['全校总课表'].cell(row=2, column=3).value))


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

        # 2026-10-09：导入预览要给出「行＝班级、列＝节次」的矩阵（与总课表/查课页同版式）
        wb2 = Workbook()
        ws2 = wb2.active
        ws2.title = '高一2班'
        ws2.append(['节次', '周一', '周二'])
        ws2.append(['早读', '语文', '英语'])
        ws2.append(['第1节', '数学', '语文(单周)'])
        buf2 = _io.BytesIO()
        wb2.save(buf2)
        buf2.seek(0)
        token = get_csrf(c, _urls()['smart_import'])
        r = c.post(_urls()['smart_import'],
                   data={'csrf_token': token, 'file': (buf2, 'demo.xlsx')},
                   content_type='multipart/form-data')
        html2 = r.get_data(as_text=True)
        case('导入预览页 200', r.status_code == 200, str(r.status_code))
        case('导入预览用矩阵版式（班级列 + 节次列）',
             'ovw-table' in html2 and 'ovw-class-col' in html2 and 'ovw-per-col' in html2)
        case('矩阵预览标题与班级行都在',
             '矩阵预览' in html2 and '高一2班' in html2 and '01班' in html2,
             'sheet/class 行缺失')
        # 2026-10-10 修正：原断言 `'ovw-flag-week' in html2` 是**假阳性** —— 它命中的是页面
        # 内联 CSS 里的类名（该 CSS 已抽到 static/css/schedule_matrix.css），而预览默认只渲染
        # days[0]=周一，单双周那条数据在周二，徽标元素压根不在首屏。改为切到 pday=2 校验真实元素。
        m = re.search(r'draft=([0-9a-f]{8,})', html2)
        case('预览页给出草稿回看链接（切星期几不用重传）', bool(m))
        if m:
            r2 = c.get(_urls()['smart_import'] + '?draft=' + m.group(1) + '&pday=2')
            html_day2 = r2.get_data(as_text=True)
            case('草稿回看 + 切换星期几可渲染',
                 r2.status_code == 200 and 'ovw-class-col' in html_day2,
                 str(r2.status_code))
            case('单双周徽标在预览里可见（周二那节）',
                 'ovw-flag-week' in html_day2 and '单周' in html_day2,
                 'pday=2 未找到徽标元素')


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

        # 2026-10-09：附件统一 PDF（扫描件/通知书/课题材料都转成 PDF）
        pdf = (b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n'
               b'trailer\n<< /Root 1 0 R >>\n%%EOF')
        ok1, m1, a1 = ach.save_attachment(
            rec, FileStorage(stream=_io.BytesIO(pdf), filename='证书.pdf'),
            doc_type='cert')
        case('PDF 附件可上传', ok1 and a1 is not None, m1)
        ok1b, m1b, _ = ach.save_attachment(
            rec, FileStorage(stream=_io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'\x00' * 64),
                             filename='证书.png'))
        case('非 PDF（图片）已按新口径拒绝', not ok1b, m1b)
        ok2, m2, _ = ach.save_attachment(
            rec, FileStorage(stream=_io.BytesIO(b'<html>x</html>'), filename='x.html'))
        case('危险类型被拒（.html）', not ok2, m2)
        ok3, m3, _ = ach.save_attachment(
            rec, FileStorage(stream=_io.BytesIO(b'<html>x</html>'), filename='伪装.pdf'))
        case('伪装扩展名被拒（magic 嗅探）', not ok3, m3)
        d = ach.detail_dict(rec)
        case('详情含标签与附件', d['tags'] == ['课题', '数学']
             and len(d['attachments']) == 1, str(len(d['attachments'])))
        case('PDF 标为可预览（非图片）',
             (not d['attachments'][0]['is_image'])
             and d['attachments'][0]['previewable'])
        case('附件材料分类落库', d['attachments'][0].get('doc_type') == 'cert',
             str(d['attachments'][0].get('doc_type')))
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
        case('PDF 内联预览（Content-Type=application/pdf）', r.status_code == 200
             and (r.headers.get('Content-Type') or '').startswith('application/pdf'),
             str(r.headers.get('Content-Type')))
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
# 任课安排（2026-10-10 整功能下线）
# 教师-学科关系统一以「任课教师映射」（TeacherSubjectLink）为唯一依据：
# 教务侧页面 /academic/teacher-links，成绩侧页面 /grades/teachers。
# ══════════════════════════════════════════════════════════════════════════════
@item('DUTY_OFFLINE')
def check_duty_offline():
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        for path in ('/academic/duty', '/academic/duty/export', '/academic/api/duty'):
            r = c.get(path)
            case(f'已下线地址 404：{path}', r.status_code == 404,
                 f'status={r.status_code}')
        r = c.get('/academic/teacher-links')
        html = r.get_data(as_text=True)
        case('任课教师映射（教务侧）替代任课安排',
             r.status_code == 200 and 'tcApp' in html, f'status={r.status_code}')
        case('教务页面不再出现任课安排入口',
             '/academic/duty' not in html and '任课安排' not in html)


# ══════════════════════════════════════════════════════════════════════════════
# 学科组长（2026-10-14：备课组长＝年级×学科，教研组长＝只按学科；组长必须是本学科教师）
# ══════════════════════════════════════════════════════════════════════════════
@item('LEADER')
def check_leader():
    """页面矩阵渲染 + 命名 / 导出字段 + 「职责 / 组范围」整功能已删除。"""
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, u['leaders'])
        hdrs = {'X-CSRFToken': token}

        # 备课组长（高一语文）+ 教研组长（语文，不分年级）共存：唯一键必须含类别
        c.post(u['matrix_save'], headers=hdrs, json={
            'school_year': '2026-2027', 'term': '',
            'cells': {'高一|语文': 'T900001', '教研组|语文': 'T900001'}})
        with app.app_context():
            rows = SubjectLeader.query.filter_by(school_year='2026-2027').all()
        case('备课组长与教研组长可共存（唯一键含类别）',
             len(rows) == 2 and {(x.category, x.grade) for x in rows}
             == {('备课组长', '高一'), ('教研组长', '')},
             str([(x.category, x.grade) for x in rows]))

        lh = c.get(u['leaders']).get_data(as_text=True)
        case('页面是学科组长矩阵（两类组长 + 年级列）',
             '学科组长' in lh and '备课组长' in lh and '教研组长' in lh
             and 'id="ldMatrix"' in lh)
        case('音体美信通心合并为综合组', '综合组' in lh and '孙体育' in lh)
        case('页面不再有职责 / 组范围（整功能删除）',
             not any(k in lh for k in ('职责与组范围', '主要职责', '备课组范围',
                                       '教研组范围', '填入常用职责', 'ldDuty',
                                       'leaderModal')), '页面仍残留职责相关元素')

        # 已删除的路由必须 404（不留死 API）
        for path in ('/academic/leaders/save', '/academic/leaders/delete',
                     '/academic/leaders/candidates'):
            r = c.post(path, data={'csrf_token': token})
            case(f'旧接口已删除 {path}', r.status_code == 404, f'status={r.status_code}')

        r = c.get(u['leaders_export'], query_string={'school_year': '2026-2027'})
        try:
            texts = sheet_texts(r.data)
            joined = ' '.join(texts)
            case('导出含类别 / 学科组列',
                 any('学科组长名单' in t for t in texts) and '学科组' in joined
                 and '张语文' in joined)
            case('导出不再含职责 / 组范围列',
                 '主要职责' not in joined and '组范围' not in joined)
        except Exception as e:  # noqa: BLE001
            case('组长名单导出可解析', False, str(e))

        with app.app_context():
            SubjectLeader.query.filter_by(school_year='2026-2027').delete()
            db.session.commit()
            case('组长用例清理干净', SubjectLeader.query.count() == 0)


# ══════════════════════════════════════════════════════════════════════════════
# 教师映射扩容（2026-10-15 用户口径「教师映射扩容，增加综合组」）
# 科目列 = 9 文化科 + 综合组 6 科（体育/音乐/美术/信息技术/通用技术/心理）
# ══════════════════════════════════════════════════════════════════════════════
@item('LINKEXPAND')
def check_link_subjects_expand():
    """任课映射的科目维度扩到 15 科：矩阵接口 / 保存白名单 / 导入模板 / 导入解析 全同步。"""
    import io as _io

    import openpyxl

    from app.models.academic import COMPREHENSIVE_SUBJECTS
    from app.models.grades import LINK_SUBJECTS, SUBJECTS, TeacherSubjectLink

    case('常量口径：LINK_SUBJECTS = 9 文化科 + 综合组 6 科（去重、顺序稳定）',
         len(LINK_SUBJECTS) == 15 and len(set(LINK_SUBJECTS)) == 15
         and LINK_SUBJECTS[:9] == SUBJECTS
         and set(LINK_SUBJECTS[9:]) == set(COMPREHENSIVE_SUBJECTS), str(LINK_SUBJECTS))

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get('/academic/teacher-links/api?grade=高一')
        d = (r.get_json() or {}).get('data') or {}
        case('矩阵接口：科目列 15 科（含综合组）',
             d.get('subjects') == LINK_SUBJECTS, str(d.get('subjects')))
        groups = d.get('subject_groups') or []
        case('矩阵接口：两段分组（文化科目 9 / 综合组 6）',
             len(groups) == 2 and len(groups[0]['subjects']) == 9
             and groups[1].get('name') == '综合组'
             and set(groups[1]['subjects']) == set(COMPREHENSIVE_SUBJECTS), str(groups))

        with app.app_context():
            u = User.query.filter_by(username='ac_admin').first()
        # 班级名与年级取接口实际返回的（测试库的活跃年级/班级名与真实库不同）；
        # `_grade_classes` 以 students 表为口径 —— 空年级没有教学班，先补一个班
        _grade = (d.get('grade') or '高一').strip()
        _cls = (d.get('classes') or [None])[0]
        if not _cls:
            _cls = '01班'
            from app.models import Student
            with app.app_context():
                db.session.add(Student(grade=_grade, class_name=_cls, name='映射测试生',
                                       student_number='T9999001', gender='男'))
                db.session.commit()
        token = get_csrf(c, '/academic/teacher-links')
        hdrs = {'X-CSRFToken': token}
        key = f'{_cls}|体育'
        r = c.post('/academic/teacher-links/api/save', headers=hdrs,
                   json={'grade': _grade,
                         'cells': {f'{_cls}|语文': u.id, key: u.id}})
        j = r.get_json() or {}
        with app.app_context():
            row = TeacherSubjectLink.query.filter_by(
                grade=_grade, class_name=_cls, subject='体育', active=True).first()
            _yw = TeacherSubjectLink.query.filter_by(
                grade=_grade, class_name=_cls, subject='语文', active=True).first()
        case('保存：体育科目能落库（旧口径下该键会被静默丢弃）',
             j.get('success') and row is not None and row.user_id == u.id,
             f'{j} row={row} 语文={_yw} grade={_grade} cls={_cls}')
        c.post('/academic/teacher-links/api/save', headers=hdrs,
               json={'grade': _grade, 'cells': {key: ''}})
        with app.app_context():
            left = TeacherSubjectLink.query.filter_by(
                grade=_grade, class_name=_cls, subject='体育', active=True).count()
        case('清空：综合组科目的映射可释放', left == 0, str(left))

        # 导入模板表头
        r = c.get('/academic/teacher-links/template.xlsx')
        wb = openpyxl.load_workbook(_io.BytesIO(r.data))
        head = [c0.value for c0 in wb.active[1]]
        case('导入模板：表头含综合组六科',
             r.status_code == 200
             and all(s in head for s in COMPREHENSIVE_SUBJECTS), str(head[-8:]))

    # 导入解析：综合组列 + 常见写法（信息 → 信息技术、心理健康 → 心理）
    from app.modules.grades.services import teacher_import as ti
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['年级', '班级', '语文', '体育', '信息', '心理健康'])
    ws.append(['2025级', '01班', '张语文', '李体育', '王信息', '赵心理'])
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    res = ti.parse_teacher_excel(buf)
    got = {(lk['subject'], lk['teacher_name']) for lk in res['links']}
    case('导入解析：综合组列识别 + 科名归一（体育 / 信息→信息技术 / 心理健康→心理）',
         ('体育', '李体育') in got and ('信息技术', '王信息') in got
         and ('心理', '赵心理') in got and ('语文', '张语文') in got, str(sorted(got)))


# ══════════════════════════════════════════════════════════════════════════════
# 学科组长矩阵（2026-10-14：行＝学科、列＝高一/高二/高三/教研组，格内选本学科教师）
# ══════════════════════════════════════════════════════════════════════════════
@item('LEADMATRIX')
def check_leader_matrix():
    """矩阵视图：服务端渲染 15 学科 × 4 列；批量保存在应用层逐格校验本学科教师。"""
    with app.app_context():
        u = _urls()
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get(u['leaders']).get_data(as_text=True)
        case('矩阵渲染：三类班长列 + 综合组分带',
             all(k in html for k in ('高一', '高二', '高三', '教研组（不分年级）', '综合组'))
             and 'id="ldMatrix"' in html)
        case('矩阵格内是本学科教师下拉',
             'class="form-select form-select-sm lcell"' in html and 'data-key="高一|语文"' in html)
        # 2026-10-14 用户口径（二次修订）：整个综合组只设 1 名组长 —— 矩阵里综合组
        # 只渲染一行一格（键「综合组|综合组」），不再按 6 科 × 4 列铺格子
        case('矩阵行：文化科 9 行 + 综合组一行一格',
             all(f'data-key="教研组|{s}"' in html for s in
                 ('语文', '数学', '外语', '物理', '化学', '生物', '政治', '历史', '地理'))
             and 'data-key="综合组|综合组"' in html
             and 'data-key="高一|体育"' not in html
             and 'data-key="教研组|体育"' not in html)
        token = get_csrf(c, u['leaders'])
        hdrs = {'X-CSRFToken': token}       # JSON 接口：CSRF 走请求头

        # 批量保存：备课组长（高一语文）+ 教研组长（语文，不分年级）
        r = c.post(u['matrix_save'], headers=hdrs, json={
            'school_year': '2026-2027', 'term': '',
            'cells': {'高一|语文': 'T900001', '教研组|语文': 'T900001'}})
        res = r.get_json() or {}
        case('矩阵保存：备课组长 + 教研组长各 1 条',
             res.get('success') and res['changes']['set'] == 2, str(res))
        with app.app_context():
            rows = SubjectLeader.query.filter_by(school_year='2026-2027').all()
            case('矩阵落库口径正确（类别 / 年级）',
                 len(rows) == 2 and {(x.category, x.grade) for x in rows}
                 == {('备课组长', '高一'), ('教研组长', '')},
                 str([(x.category, x.grade, x.leader_name) for x in rows]))
            case('矩阵自动带出教师姓名与编号',
                 all(x.leader_name == '张语文' and x.leader_uid == 'T900001' for x in rows))

        # 跨学科教师：李数学不能当语文组长（该格被忽略，不写库）
        r = c.post(u['matrix_save'], headers=hdrs, json={
            'school_year': '2026-2027', 'term': '',
            'cells': {'高二|语文': 'T900002'}})
        res = r.get_json() or {}
        with app.app_context():
            n = SubjectLeader.query.filter_by(school_year='2026-2027').count()
        case('矩阵拒绝跨学科教师', res.get('skipped') and n == 2 and not res['changes']['set'],
             str(res))

        # 改派（换成另一位语文老师）+ 清空教研组长格
        with app.app_context():
            from app.models.academic import Teacher
            db.session.add(Teacher(teacher_uid='T900010', name='赵语文', subject='语文',
                                   status='active'))
            db.session.commit()
        r = c.post(u['matrix_save'], headers=hdrs, json={
            'school_year': '2026-2027', 'term': '',
            'cells': {'高一|语文': 'T900010', '教研组|语文': ''}})
        res = r.get_json() or {}
        with app.app_context():
            row = SubjectLeader.query.filter_by(school_year='2026-2027',
                                                category='备课组长').first()
            left = SubjectLeader.query.filter_by(school_year='2026-2027').all()
        case('矩阵改派 1 条 + 清空 1 条',
             res['changes']['changed'] == 1 and res['changes']['cleared'] == 1
             and row is not None and row.leader_name == '赵语文' and len(left) == 1,
             str(res))

        # 综合组：整组一格（体育老师走课表兜底候选也能当综合组组长）
        r = c.post(u['matrix_save'], headers=hdrs, json={
            'school_year': '2026-2027', 'term': '',
            'cells': {'综合组|综合组': 'T900006'}})
        res = r.get_json() or {}
        with app.app_context():
            _g = SubjectLeader.query.filter_by(school_year='2026-2027',
                                               subject='综合组').first()
        case('矩阵可维护综合组（整组一格，体育老师可当）',
             res.get('success') and res['changes']['set'] == 1
             and _g is not None and _g.leader_uid == 'T900006'
             and (_g.grade or '') == '' and _g.category == '综合组组长',
             str(res) + str(_g and (_g.subject, _g.grade, _g.category)))

        # 综合组整组一格：按年级 / 按具体学科另开格子一律被拒（skipped，不写库）
        for _bad in ('高一|体育', '教研组|体育'):
            r = c.post(u['matrix_save'], headers=hdrs, json={
                'school_year': '2026-2027', 'term': '', 'cells': {_bad: 'T900006'}})
            res = r.get_json() or {}
            case(f'综合组整组一格：{_bad} 被拒',
                 res.get('success') and not res['changes']['set'] and res.get('skipped'),
                 str(res))

        # 未提交的格子不动（局部保存）
        r = c.post(u['matrix_save'], headers=hdrs,
                   json={'school_year': '2026-2027', 'term': '', 'cells': {}})
        res = r.get_json() or {}
        with app.app_context():
            n = SubjectLeader.query.filter_by(school_year='2026-2027').count()
        case('矩阵局部保存：未提交的格子不动', n == 2, f'{n}')

        # 学年必填 + 非 JSON body 容错
        r = c.post(u['matrix_save'], headers=hdrs, json={'cells': {}})
        case('矩阵保存校验学年必填', r.status_code == 400)
        r = c.post(u['matrix_save'], headers=hdrs, data=json.dumps(
            {'school_year': '2026-2027', 'term': '', 'cells': {}}),
            content_type='application/x-www-form-urlencoded')
        msg = (r.get_json() or {}).get('success')
        case('矩阵兼容非 JSON body（jQuery 老写法）', msg,
             f'status={r.status_code} body={r.get_data(as_text=True)[:120]}')

        # 学期隔离：第一学期的登记不该出现在「整学年」矩阵里
        c.post(u['matrix_save'], headers=hdrs, json={
            'school_year': '2026-2027', 'term': '第一学期',
            'cells': {'高三|数学': 'T900002'}})
        with app.app_context():
            from app.modules.academic.services import subject_leader_service as _svc
            mx = _svc.matrix_data('2026-2027', '')
            term1 = _svc.matrix_data('2026-2027', '第一学期')
        case('矩阵按学期隔离',
             (mx['cells']['高三|数学'] or {}).get('uid') in (None, '')
             and (term1['cells']['高三|数学'] or {}).get('name') == '李数学',
             str(term1['cells']['高三|数学']))

        with app.app_context():
            from app.models.academic import Teacher
            for x in SubjectLeader.query.filter_by(school_year='2026-2027').all():
                db.session.delete(x)
            t = Teacher.query.filter_by(teacher_uid='T900010').first()
            if t:
                db.session.delete(t)
            db.session.commit()
            case('矩阵用例清理干净', SubjectLeader.query.count() == 0)


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
             ('教务工作台', '待办事项', '教务主流程', '最近动态')))
        case('显示当前学期', '2026-2027学年第一学期' in html)
        # 2026-10-12（用户要求）：头部那排快捷按钮删除（内容在下方「教务主流程」三块里各有一个
        # 主入口，头部再挂是重复），学期信息并到标题后面同一行
        case('头部不再重复挂快捷按钮（全校总课表 / 查课 / 调课审批）',
             'btn btn-sm btn-light' not in html)
        case('学期信息跟在标题后面（同一行）',
             'ac-hero-meta' in html and html.index('教务工作台') < html.index('当前学期：'))
        # 2026-10-12（用户口径）：顶部 KPI 换成实时数据 —— 查课（今日/本月/覆盖率）、
        # 迟到/缺勤（风险红卡）、待审批调课 / 待审业绩、材料回收率与待回收份数。
        # 数字断言基于本文件夹具里那条「今日 late」记录（HOME 项在 INSPECTION 改它之前执行）。
        case('顶部实时 KPI：今日查课 + 本月累计 + 今日覆盖率',
             '本月累计' in html and '今日覆盖率' in html)
        case('顶部实时 KPI：本月迟到 1 人次（夹具今日 1 条 late）',
             '本月迟到 1 人次' in html)
        case('顶部实时 KPI：本月缺勤 0 人次（无缺勤记录）',
             '本月缺勤 0 人次' in html)
        case('迟到/缺勤风险红卡：有值时套 risk 样式',
             'ac-kpi p-3 risk' in html)
        case('顶部实时 KPI：待审批调课 / 教师业绩待审核',
             '待审批调课申请' in html and '教师业绩待审核' in html)
        case('顶部实时 KPI：材料回收（回收率 / 待回收份数）',
             '材料回收 · 待回收' in html)
        case('旧基础数据卡已下线（学生 / 教师 / 课时 / 备课组长）',
             all(k not in html for k in ('在校学生', '在职教师', '本学期课时',
                                         '已登记备课组长')))


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


@item('STAFFTABS')
def check_staff_tabs():
    """2026-10-14 教师三页合一：教师名单 / 任课教师映射 / 学科组长 顶部 tab 互切。

    - 三页共用 _staff_tabs.html，各页高亮自己的项（data-tab 定位）；
    - 侧栏「教师」合并为一个入口（旧的折叠组 navAcaTch 不再存在）；
    - 成绩管理侧 /grades/teachers 是同源另一入口：不出现教务 tab、保留返回按钮。
    """
    from flask import url_for
    with app.app_context():
        u = _urls()
        links_url = url_for('academic.teacher_links_page')
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        pages = {'roster': u['teachers'], 'links': links_url, 'leaders': u['leaders']}
        for tab, path in pages.items():
            r = c.get(path)
            html = r.get_data(as_text=True)
            case(f'教师页 {path} 200', r.status_code == 200, str(r.status_code))
            case(f'教师页 tab 栏渲染 + 当前项高亮（{tab}）',
                 'id="staffTabs"' in html
                 and bool(re.search(r'class="nav-link[^"]*active[^"]*"[^>]*data-tab="'
                                    + tab + '"', html)))
            case(f'教师页 tab 三项齐全（{tab} 视角）',
                 all(f'data-tab="{t}"' in html for t in ('roster', 'links', 'leaders')))
        # 侧栏：旧的「教师」折叠组（navAcaTch）已删除，只留一个「教师」入口
        case('侧栏教师合并为一个入口（旧折叠组已删除）',
             'navAcaTch' not in html
             and bool(re.search(r'bi-person-vcard"></i>教师\s*</a>', html)))
        # 成绩管理侧不受影响
        r = c.get('/grades/teachers')
        ghtml = r.get_data(as_text=True)
        case('成绩侧任课映射正常 200 且不出现教务 tab',
             r.status_code == 200 and 'id="staffTabs"' not in ghtml, str(r.status_code))
        case('成绩侧保留返回按钮', 'bi bi-arrow-left' in ghtml)


@item('USERTABS')
def check_user_tabs():
    """2026-10-14 系统管理「教师管理」拆成两个子页：教师名单 / 教师权限管理。

    - `/users/`（教师权限管理，权限 system.users）与 `/users/teachers`（教师名单，权限
      academic.view，复用教务侧同一视图与数据）共用一条 tab 栏；
    - 页内链接 / 表单 / 重定向按当前模块自适应（users.* ↔ academic.*），不跨模块跳；
    - 权限边界：只有 academic.view 的身份进得来教师名单、进不去教师权限管理（反之亦然）。
    """
    from flask import url_for
    with app.app_context():
        g_aca = PermissionGroup(name='回归-仅教务查看')
        g_aca.set_menu_keys(['academic.view'])
        g_sys = PermissionGroup(name='回归-仅系统用户')
        g_sys.set_menu_keys(['system.users'])
        db.session.add_all([g_aca, g_sys])
        db.session.flush()
        for uname, rname, gid in (('ac_only_aca', '教务查看员', g_aca.id),
                                  ('ac_only_sys', '系统用户员', g_sys.id)):
            u = User(username=uname, real_name=rname, role='staff',
                     must_change_pwd=False, permission_group_id=gid)
            u.set_password(TEST_PWD)
            db.session.add(u)
        db.session.commit()

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get('/users/').get_data(as_text=True)
        case('教师管理页 tab 栏 2 项 + 高亮教师权限管理',
             'id="userTabs"' in html and bool(re.search(
                 r'class="nav-link[^"]*active[^"]*"[^>]*data-tab="perms"', html)))
        case('教师管理页 tab 含教师名单子页（管理员有 academic.view）',
             'data-tab="roster"' in html and '/users/teachers' in html)
        r = c.get('/users/teachers')
        html = r.get_data(as_text=True)
        case('教师名单子页 200', r.status_code == 200, str(r.status_code))
        case('教师名单子页高亮 roster（系统管理那组 tab）',
             'id="userTabs"' in html and bool(re.search(
                 r'class="nav-link[^"]*active[^"]*"[^>]*data-tab="roster"', html)))
        case('教师名单子页内容正确（教师编号 / 姓名 / 科目）',
             '教师编号' in html and '张语文' in html)
        case('教师名单子页不再出现教务那组 tab', 'id="staffTabs"' not in html)
        # 双端自适应：链接必须留在当前模块（/users/...），不把用户送回教务
        case('子页链接按当前模块自适应（导出 / 模板 / 编辑 / 分页）',
             '/users/teachers/export' in html and '/users/teachers/template.xlsx' in html
             and '/users/teachers/' in html and '/academic/teachers' not in html,
             '存在跨模块链接')
        r = c.get('/users/teachers/export')
        case('子页导出可用（xlsx）',
             r.status_code == 200 and r.data[:2] == b'PK', str(r.status_code))
        # 教务侧仍是独立的三 tab，不受影响
        html = c.get(url_for('academic.teachers_page')).get_data(as_text=True)
        case('教务侧教师名单仍是教务 tab 且不含系统管理 tab',
             'id="staffTabs"' in html and 'id="userTabs"' not in html
             and '/academic/teachers/export' in html)

    with app.test_client() as c:
        login(c, 'ac_only_aca', TEST_PWD)
        r = c.get('/users/teachers')
        case('仅 academic.view：教师名单子页可访问', r.status_code == 200, str(r.status_code))
        r = c.get('/users/')
        case('仅 academic.view：教师权限管理被拒 403', r.status_code == 403, str(r.status_code))

    with app.test_client() as c:
        login(c, 'ac_only_sys', TEST_PWD)
        html = c.get('/users/').get_data(as_text=True)
        case('仅 system.users：教师权限管理可用且 tab 不含教师名单',
             'id="userTabs"' in html and 'data-tab="perms"' in html
             and 'data-tab="roster"' not in html)
        r = c.get('/users/teachers')
        case('仅 system.users：教师名单子页被拒 403', r.status_code == 403, str(r.status_code))

    with app.app_context():
        from sqlalchemy import text
        uids = [u.id for u in User.query.filter(
            User.username.in_(('ac_only_aca', 'ac_only_sys'))).all()]
        if uids:
            # 登录会写 operation_logs（引用 users.id），先清这些行再删用户，避免外键阻断
            db.session.execute(
                text('DELETE FROM operation_logs WHERE user_id IN :ids')
                .bindparams(db.bindparam('ids', expanding=True)), {'ids': uids})
            db.session.commit()
        User.query.filter(User.id.in_(uids)).delete(synchronize_session=False)
        PermissionGroup.query.filter(PermissionGroup.name.in_(
            ('回归-仅教务查看', '回归-仅系统用户'))).delete(synchronize_session=False)
        db.session.commit()
        case('教师管理子页用例清理干净',
             User.query.filter_by(username='ac_only_aca').first() is None)


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
        # 2026-10-13（用户口径）：录入（单条 + 批量）拆到独立页 —— 统计页只留统计与导出
        # 注：url_for 在本套件里是无请求上下文生成的**绝对 URL**（http://localhost/…），
        # 页面里是相对路径，所以这里一律按路径片段断言，不能用 u[...] 整串做 in 判断。
        case('统计页不再承载录入（录入表单 / 批量模态框已移除）',
             '/inspection/add' not in html and '/inspection/batch' not in html
             and 'id="batchModal"' not in html)
        case('统计页顶部有进入录入页的入口', '/inspection/entry' in html)
        entry_resp = c.get(u['insp_entry'])
        entry_html = entry_resp.get_data(as_text=True)
        case('录入页可访问且含单条 / 批量两种方式',
             entry_resp.status_code == 200
             and '单条录入' in entry_html and '批量录入' in entry_html
             and '/inspection/add' in entry_html and '/inspection/batch' in entry_html
             and 'id="batchForm"' in entry_html,
             f'status={entry_resp.status_code} len={len(entry_html)}')
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
        # 2026-10-13 录入闭环：独立录入页提交 → 落库 → 重定向回独立录入页
        token_entry = get_csrf(c, u['insp_entry'])
        r = c.post(u['insp_add'], data={
            'csrf_token': token_entry, 'inspect_date': date.today().isoformat(),
            'period': 4, 'grade': '高一', 'teacher_uid': 'T900002',
            'class_name': '01班', 'subject': '数学', 'result': 'exam',
            'note': '录入页闭环验证'})
        case('单条录入后重定向回独立录入页',
             r.status_code in (301, 302)
             and '/inspection/entry' in (r.headers.get('Location') or ''),
             f'{r.status_code} {r.headers.get("Location")}')
        with app.app_context():
            rec_new = InspectionRecord.query.filter_by(
                teacher_uid='T900002', result='exam', note='录入页闭环验证').first()
            case('录入页提交落库（考试状态）', rec_new is not None)
            if rec_new:                      # 断言完立即清理，保持夹具干净
                db.session.delete(rec_new)
                db.session.commit()

        # ── 2026-10-14 用户口径：录入时按教师自动追踪班级 / 科目（仍可手改） ──
        case('录入页挂上自动带出接口与提示位',
             'id="singleLookupHint"' in entry_html
             and '/api/inspection/teacher-courses' in entry_html
             and all(k in entry_html for k in ('id="singleTeacher"', 'id="singleDate"',
                                               'id="singlePeriod"', 'id="singleGrade"',
                                               'id="singleClass"', 'id="singleSubject"')),
             f'len={len(entry_html)}')
        # 2026-10-14（用户报「缺个班级」）：节次手填数字会错位（课表里 1=早读、2=第1节）——
        # 录入 / 编辑一律改下拉，选项名取本学期作息（get_default_periods：1=早读、2=第1节）
        case('录入页节次改下拉（作息节次名，不再手填数字）',
             'id="singlePeriod"' in entry_html and '<select name="period" id="singlePeriod"' in entry_html
             and '>早读</option>' in entry_html and '>第1节</option>' in entry_html
             and '"periods":' in entry_html,
             f'len={len(entry_html)}')
        case('查课列表节次显示节次名（period_label 口径）', '<td>第1节</td>' in html,
             'expect 第1节 for period=2')
        case('编辑弹窗节次同样用下拉',
             '<select name="period" id="eiPeriod"' in html)
        today_str = date.today().isoformat()
        r = c.get(u['insp_lookup'] + f'?date={today_str}&teacher_uid=T900001')
        look = r.get_json() or {}
        courses = look.get('courses') or []
        # 2026-10-17：原先断言 `len(courses) == 2`（今天补的两条课）。
        # 但前面的 BULKOFF 夹具会把「高一01班 语文」换到**周六第 8 节**并留下生效记录，
        # 于是"今天是周六"时反查会多出一条 → 全量跑必红（单独跑 INSPECTION 却绿，
        # 典型的跨 item 残留 + 星期依赖）。断言本意是"按教师带出班级/科目/节次"，
        # 改成校验今天那两条都在、且返回的都确实是这位教师的语文课，
        # 不再把其它夹具的残留当成失败（改动前的代码今天跑同样红，已对照确认）。
        case('反查接口：按教师带出当天课（班级 / 科目 / 节次）',
             r.status_code == 200 and look.get('success')
             and any(cc['class_name'] == '01班' and cc['subject'] == '语文'
                     and cc['grade'] == '高一' and cc['period'] == 3
                     for cc in courses)
             and any(cc['class_name'] == '01班' and cc['subject'] == '语文'
                     and cc['grade'] == '高二' and cc['period'] == 4
                     for cc in courses)
             and all(cc['teacher_uid'] == 'T900001' and cc['subject'] == '语文'
                     for cc in courses),
             f'{r.status_code} {str(look)[:200]}')

        # 叠加当天临时调课：把「李数学 周一第3节」调到今天第9节 ——
        # 反查应带出调入的第9节（is_temp_swap=True），且当天不再出现原时段。
        with app.app_context():
            src = ScheduleEntry.query.filter_by(
                term_schedule_id=TS_ID, teacher_uid='T900002', weekday=1,
                period_number=3).first()
            tgt = ScheduleEntry(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                                weekday=date.today().isoweekday(), period_number=9,
                                week_range='1-20', subject='数学', teacher_uid='T900002',
                                teacher_name='李数学', room='101', entry_type='swap',
                                is_deleted=False)
            db.session.add(tgt)
            db.session.flush()
            sw = ScheduleSwap(term_schedule_id=TS_ID, swap_type='personal',
                              original_entry_id=src.id, target_entry_id=tgt.id,
                              applicant_uid='T900002', applicant_name='李数学',
                              swap_date=date.today(),
                              new_weekday=date.today().isoweekday(), new_period=9,
                              is_permanent=False, status='executed',
                              reason='回归：查课反查叠加验证')
            db.session.add(sw)
            db.session.commit()
            tgt_id, sw_id = tgt.id, sw.id
        r = c.get(u['insp_lookup'] + f'?date={today_str}&teacher_uid=T900002')
        courses2 = (r.get_json() or {}).get('courses') or []
        case('反查接口叠加当天临时调课（按实际任课教师带出、原时段不计）',
             any(cc['entry_id'] == tgt_id and cc['is_temp_swap'] and cc['period'] == 9
                 for cc in courses2)
             and all(cc['period'] != 3 for cc in courses2),
             str(courses2)[:160])
        with app.app_context():          # 断言完立即清理，保持夹具干净
            db.session.delete(db.session.get(ScheduleSwap, sw_id))
            db.session.delete(db.session.get(ScheduleEntry, tgt_id))
            db.session.commit()

    # 反查接口与录入同权限：无 academic.edit 的账号不得调用
    with app.test_client() as c3:
        login(c3, 'ac_head', TEST_PWD)
        r = c3.get(u['insp_lookup'] + f'?date={today_str}&teacher_uid=T900001')
        case('反查接口需录入权限（班主任被拒）', r.status_code == 403, str(r.status_code))


@item('WORKLOAD')
def check_workload():
    """课时量统计与导出（2026-10-13 用户口径）：课型折算配置 + 考试期间豁免 + 导出可解析。

    用户要求：①导出课时量能按课型分别设置计算规则（第五节/早读/早自习/晚自习/
    下午最后一节课/监考…各一行）；②可把某些班标记为「考试期间」——期间不记
    工作量，也不计违纪/迟到。两者共用同一份配置（workload_service）。
    """
    from app.models.academic import ExamPeriod, WorkloadRule
    from app.models.timetable import ScheduleEntry
    from app.modules.academic.services import workload_service as wl

    today = date.today()
    # 2026-10-19 审计：查课夹具会占用 T900001/T900002 的同一时段，
    # 用独立 uid 才能保证「第 2 节 0.5」只统计本用例造的那一节。
    WL_UID = 'T_WL_QA'
    WL_NAME = '李数学'
    orig_uid = orig_name = None
    with app.app_context():
        u = _urls()
        # 夹具是周课表，不一定覆盖「今天」——这里保证当天有一节确定的课
        entry = (ScheduleEntry.query
                 .filter_by(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                            weekday=today.isoweekday(), period_number=2,
                            is_deleted=False).first())
        entry_id = entry.id if entry else None
        added_new = entry is None
        if entry is None:
            e = ScheduleEntry(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                              weekday=today.isoweekday(), period_number=2,
                              week_range='全周', subject='数学', teacher_uid=WL_UID,
                              teacher_name=WL_NAME, room='101', entry_type='normal',
                              is_deleted=False)
            db.session.add(e)
            db.session.commit()
            entry_id = e.id
        else:
            orig_uid, orig_name = entry.teacher_uid, entry.teacher_name
            entry.teacher_uid = WL_UID
            entry.teacher_name = WL_NAME
            db.session.commit()

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, u['insp'])
        hdrs = {'X-CSRFToken': token}

        # ① 配置接口：每个节次一行 + 监考折算
        r = c.get(u['wl_config'])
        d = ((r.get_json() or {}).get('data') or {})
        case('课时量配置接口可用（节次逐行 + 监考折算）',
             r.status_code == 200 and isinstance(d.get('periods'), list)
             and 'invigilation_factor' in d, f'{r.status_code}')
        case('未配置时默认 1 课时 / 节',
             bool(d.get('periods'))
             and all(abs(float(p['factor']) - 1) < 1e-6 for p in d['periods']),
             str([p.get('factor') for p in (d.get('periods') or [])][:5]))

        # ② 保存折算规则：第 2 节 0.5、监考 1.5
        r = c.post(u['wl_rules'], json={'items': [
            {'kind': 'period', 'period_number': 2, 'factor': 0.5},
            {'kind': 'invigilation', 'period_number': 0, 'factor': 1.5},
        ]}, headers=hdrs)
        case('课型折算规则保存成功', bool((r.get_json() or {}).get('success')),
             f'{r.status_code}')
        with app.app_context():
            r2 = WorkloadRule.query.filter_by(kind='period', period_number=2).first()
            ri = WorkloadRule.query.filter_by(kind='invigilation', period_number=0).first()
        case('折算规则落库（第2节 0.5 / 监考 1.5）',
             bool(r2 and abs(r2.factor - 0.5) < 1e-6)
             and bool(ri and abs(ri.factor - 1.5) < 1e-6),
             f'period2={r2.factor if r2 else None} invig={ri.factor if ri else None}')

        # ③ 基线：今天这节课按 0.5 折算
        # 2026-10-19 审计：夹具里同一老师当天可能还有第 3 节（查课用例留下的正常课），
        # 原断言写死「总课时 == 0.5」会误判。改为按 counts 现算期望：
        # 第 2 节 × 0.5，其余节次按默认 1.0；同时要求第 2 节确实只有 1 节。
        with app.app_context():
            base = wl.calc_workload(today, today, grade='高一')
            bmap = {x['teacher_uid']: x for x in base['rows']}
        _row = bmap.get(WL_UID) or {}
        _counts = _row.get('counts') or {}
        _exp = round(0.5 * _counts.get(2, 0)
                     + sum(v for p, v in _counts.items() if p != 2), 4)
        case('课时量统计到当天的课（第 2 节 × 0.5）',
             _counts.get(2) == 1 and abs((_row.get('period_hours') or 0) - _exp) < 1e-6,
             f'{_row.get("period_hours")} counts={_counts} exp={_exp}')

        # ④ 标记考试期间 → 期间不计工作量
        r = c.post(u['wl_exam_add'],
                   json={'grade': '高一', 'class_name': '01班',
                         'start_date': today.isoformat(), 'end_date': today.isoformat(),
                         'note': '课时量回归'}, headers=hdrs)
        case('标记考试期间成功', bool((r.get_json() or {}).get('success')),
             f'{r.status_code}')
        with app.app_context():
            n_exam = ExamPeriod.query.filter_by(grade='高一', class_name='01班').count()
            in_exam = wl.calc_workload(today, today, grade='高一')
        emap = {x['teacher_uid']: x for x in in_exam['rows']}
        case('考试期间不计课时工作量',
             n_exam == 1 and in_exam['summary']['exam_excluded'] >= 1
             and (emap.get(WL_UID) or {}).get('period_hours', 0) == 0,
             f'n={n_exam} excluded={in_exam["summary"]["exam_excluded"]}')

        # ⑤ 导出 Excel 可解析
        r = c.get(u['wl_export'], query_string={'date_from': today.isoformat(),
                                                'date_to': today.isoformat()})
        try:
            # sheet_texts 返回的是「单元格文本列表」，要按子串匹配（不能 in 列表）
            texts = sheet_texts(r.data)
            ok = (r.status_code == 200
                  and any('课时量统计' in t for t in texts)
                  and any('合计课时' in t for t in texts))
            case('课时量导出 Excel 可解析', ok, f'{r.status_code} len={len(r.data)}')
        except Exception as e:  # noqa: BLE001
            case('课时量导出 Excel 可解析', False, str(e))

        # ⑥ 清理：删除考试期间 → 恢复计数；并还原本项造的数据
        with app.app_context():
            pid = ExamPeriod.query.filter_by(grade='高一', class_name='01班').first().id
        r = c.post(u['wl_exam_del_tpl'].replace('/0/delete', f'/{pid}/delete'),
                   headers=hdrs)
        with app.app_context():
            back = wl.calc_workload(today, today, grade='高一')
            bkmap = {x['teacher_uid']: x for x in back['rows']}
            _brow = bkmap.get(WL_UID) or {}
            _bc = _brow.get('counts') or {}
            _bexp = round(0.5 * _bc.get(2, 0)
                          + sum(v for p, v in _bc.items() if p != 2), 4)
            ExamPeriod.query.filter_by(grade='高一', class_name='01班').delete()
            WorkloadRule.query.delete()
            if entry_id:
                tmp = db.session.get(ScheduleEntry, entry_id)
                if tmp:
                    if added_new:
                        db.session.delete(tmp)
                    else:
                        tmp.teacher_uid = orig_uid
                        tmp.teacher_name = orig_name
            db.session.commit()
        case('删除考试期间后恢复计课时',
             bool((r.get_json() or {}).get('success'))
             and _bc.get(2) == 1
             and abs((_brow.get('period_hours') or 0) - _bexp) < 1e-6,
             f'{_brow.get("period_hours")} counts={_bc} exp={_bexp}')


@item('CHECK')
def check_inspection_mark():
    """查课核对改版（2026-10-09）：矩阵版式 + 一键标记 正常/迟到/缺课/调课 + 覆盖率统计。"""
    with app.app_context():
        u = _urls()
        today = date.today()
        # 查课标记挂在"有任课教师"的课上（自习/空教师格不该被标记）
        cell = (ScheduleEntry.query
                .filter_by(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                           weekday=today.isoweekday())
                .filter(ScheduleEntry.teacher_uid.isnot(None),
                        ScheduleEntry.teacher_uid != '')
                .order_by(ScheduleEntry.period_number).first())
        if cell is None:      # 兜底：当天这门课没教师时，取该班当天任意一格
            cell = (ScheduleEntry.query
                    .filter_by(term_schedule_id=TS_ID, grade='高一',
                               class_name='01班', weekday=today.isoweekday())
                    .order_by(ScheduleEntry.period_number).first())
    if cell is None:
        case('查课核对：种子缺少当天课表（无法继续）', False, 'no entry today')
        return
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get(u['insp_live']).get_data(as_text=True)
        case('查课核对页改用矩阵版式（班级列 + 节次列）',
             'ovw-table' in html and 'ovw-class-col' in html and 'ovw-per-col' in html)
        case('格子可标记（data-check / data-entry）',
             'data-check=' in html and 'data-entry=' in html)
        case('未标记格子显示「未查」', 'ovw-chk-none' in html and '未查' in html)
        case('顶部有 已标记 / 应查 / 覆盖率 计数',
             all(k in html for k in ('id="statChecked"', 'id="statExpected"',
                                     'id="statRate"')))
        # 2026-10-10 用户改版：原「标记面板」弹窗整体移除，改为 单击=正常 / 双击=迟到 /
        # 右键=缺课·调课·迟到说明（快捷直达，页面顶部写明操作方式）
        case('标记改版：页面写明 单击/双击/右键（缺课 → 缺勤）',
             '单击=正常' in html and '双击=迟到' in html and '右键=缺勤' in html)
        # 2026-10-16 UI 优化：那行长句操作说明不再常驻工具栏，收进 ⓘ 的 title
        # （用户口径「页面少放说明文字，细则进悬浮提示」）；文案不变，只是位置变了。
        case('操作说明收进信息图标悬浮提示（不再常驻页面）',
             'bi-info-circle' in html and 'title="单击=正常' in html)
        case('图例含「考试」新状态', '>考试</span>' in html)
        case('原「标记面板」五大结果按钮已移除', 'data-result=' not in html)

        # ── 2026-10-16 单节查课 UI 优化（巡课视角：一眼看出哪些班还没查）──
        pov = c.get(u['insp_live'] + '?view=period&date=' + today.isoformat()).get_data(as_text=True)
        case('单节视图：卡片带查课状态类（未查/已标记一眼可分）',
             'pov-card can chk-' in pov)
        case('单节视图：学科色点复用课表配色（--sch）',
             'class="dot" style="--sch:' in pov)
        case('单节视图：卡片可键盘操作（role=button + tabindex + aria-label）',
             'role="button"' in pov and 'tabindex="0"' in pov and 'aria-label=' in pov)
        case('单节视图：「换人」默认淡出、hover 才显形（不再 30 个按钮常驻）',
             '.pov-act {' in pov and 'opacity: 0;' in pov and '.pov-card:hover .pov-act' in pov)
        case('单节视图：节次下拉标注有课数 / 空档',
             '课</option>' in pov and '· 空</option>' in pov)
        # ── 2026-10-17 二轮复核新增（打印 / 空年级块折叠 / 本周不上）──
        case('单节视图：打印态去底色、隐藏「换人」、卡片不跨页断',
             '@media print' in pov and '.pov-card .pov-act { display: none !important; }' in pov
             and 'break-inside: avoid' in pov)
        # 折叠逻辑在 Jinja 标签里，渲染后的 HTML 里没有 → 读模板源断言
        _tpl = io.open(os.path.join(BASE, 'app/templates/academic/inspection_live.html'),
                       encoding='utf-8').read()
        case('单节视图：整年级都没排课时折成一行（不铺相同空档卡）',
             'pov-grid-na' in pov and '{% if b.scheduled %}' in _tpl
             and '本节没有排课（{{ b.total }} 个班全部空档）' in _tpl)
        case('单节视图：图例直接复用徽标类（不再手抄一份色值）',
             'chk-legend' in pov and 'ovw-chk ovw-chk-exam' in pov)
        case('单节视图：右键菜单走 --sl-* 变量（深色主题可读）',
             '.chk-menu { position: absolute; z-index: 3000; background: var(--sl-surface);' in pov)
        case('单节视图：窄屏有断点（375px 下也不抖动）', 'max-width: 575.98px' in pov)
        case('单节视图：「调走」与「空档」样式分开',
             '.pov-card.moved' in pov and '.pov-card.empty' in pov)

        js = io.open(os.path.join(BASE, 'app/static/js/inspection_live.js'),
                     encoding='utf-8').read()
        case('交互修复：连点不同卡片不再误标迟到（timer 挂元素）',
             "$el.data('clickTimer'" in js and 'var clickTimer = null;' not in js)
        case('交互修复：切日期/点「今天」保留单节视图与节次',
             "p.view = 'period';" in js and 'p.period = pn;' in js)
        case('交互修复：只读账号的覆盖率不被 JS 清零',
             'if (!cfg.canMark) { return; }' in js)
        case('交互修复：触屏长按开菜单 + 菜单贴边收敛',
             'touchstart' in js and 'pressMoved' in js and 'window.innerWidth - w' in js)
        case('交互修复：键盘 Enter/空格＝正常',
             "ev.key !== 'Enter'" in js and "markCell($(this), 'normal', '')" in js)
        case('交互修复：toast 走全局 stuToast（不再自拼白底）',
             "typeof window.stuToast === 'function'" in js
             and "typeof window.toast === 'function'" not in js)
        case('单节视图：「保存本节 / 通知领导」可统计卡片（periodCells 扩到 .pov-card）',
             'povContainer .pov-card[data-check][data-period=' in js
             and 'function currentSavePeriod()' in js)
        case('单节视图：标记后卡片状态色跟着变（不只是徽标）',
             "removeClass('chk-none chk-normal chk-late chk-absent chk-swap chk-exam chk-other')" in js)
        # ── 2026-10-16 收尾（遗留项）──
        case('单节视图：批量操作后有「撤销上一次批量」入口（平时隐藏）',
             'btnUndoBatch' in pov and 'd-none' in pov and 'undoBatchN' in pov)
        case('撤销批量：把上次那批格子改回未查（result=空串）',
             'lastBatch' in js and "$.extend({}, c, { result: '' })" in js)
        src = io.open(os.path.join(BASE, 'app/modules/academic/routes/inspection.py'),
                      encoding='utf-8').read()
        _j = src.find('data = schedule_service.get_day_matrix')
        case('单节视图不再装配全天矩阵（get_day_matrix 只在 else 分支）',
             _j > 0 and src.rfind('else:', 0, _j) > src.rfind("if view == 'period':", 0, _j))
        _css = io.open(os.path.join(BASE, 'app/static/css/theme.css'),
                       encoding='utf-8').read()
        case('主题层不再有 .ovw-chk-none 死规则（生效的是 schedule_matrix.css）',
             '.ovw-chk-none { background: var(--sl-surface-3)' not in _css)

        token = get_csrf(c, u['insp_live'])
        hdrs = {'X-CSRFToken': token}
        # 跨 app_context 只用快照值（ORM 对象出上下文后属性可能读串）
        cell = {'grade': cell.grade, 'class_name': cell.class_name,
                'period_number': cell.period_number, 'id': cell.id,
                'teacher_uid': cell.teacher_uid}
        base_cell = {'grade': cell['grade'], 'class_name': cell['class_name'],
                     'period_number': cell['period_number'], 'entry_id': cell['id'],
                     'teacher_uid': cell['teacher_uid'] or ''}
        r = c.post(u['insp_mark'],
                   json={'inspect_date': today.isoformat(),
                         'cells': [dict(base_cell, result='absent', note='上课铃响无人')]},
                   headers=hdrs)
        data = r.get_json() or {}
        case('标记接口写入成功', bool(data.get('success')) and data.get('updated') == 1,
             f'{r.status_code} {str(data)[:120]}')
        with app.app_context():
            rec = InspectionRecord.query.filter_by(
                inspect_date=today, grade=cell['grade'], class_name=cell['class_name'],
                period=cell['period_number']).first()
            case('标记落库（缺课 + 备注 + 教师按课表条目对齐）',
                 bool(rec) and rec.result == 'absent' and rec.note == '上课铃响无人'
                 and rec.teacher_uid == cell['teacher_uid'],
                 f'{rec.result if rec else None}/{rec.note if rec else None}'
                 f'/rec_uid={rec.teacher_uid if rec else None}'
                 f'/cell_uid={cell["teacher_uid"]}/cell_id={cell["id"]}')
            rid = rec.id if rec else 0
        case('标记后页面出现缺课徽标', 'ovw-chk-absent' in
             c.get(u['insp_live']).get_data(as_text=True))

        # 同一格再标记 → 覆盖更新，不新增记录（巡课改了结果不产生重复记录）
        c.post(u['insp_mark'],
               json={'inspect_date': today.isoformat(),
                     'cells': [dict(base_cell, result='normal', note='')]}, headers=hdrs)
        with app.app_context():
            same = InspectionRecord.query.filter_by(
                inspect_date=today, grade=cell['grade'], class_name=cell['class_name'],
                period=cell['period_number']).count()
            rec = db.session.get(InspectionRecord, rid)
            case('同格重复标记 = 覆盖更新（不新增）',
                 same == 1 and rec.result == 'normal', f'count={same}')

        # 2026-10-13：右键状态新增「考试」（该格是考试而非正常上课：
        # 不计课时工作量、也不算违纪/迟到 —— 口径见 models/academic.py::INSPECTION_EXEMPT_KEYS）
        r = c.post(u['insp_mark'],
                   json={'inspect_date': today.isoformat(),
                         'cells': [dict(base_cell, result='exam', note='本章节考试')]},
                   headers=hdrs)
        ex_data = r.get_json() or {}
        with app.app_context():
            ex_rec = InspectionRecord.query.filter_by(id=rid).first()
        case('「考试」标记落库', bool(ex_data.get('success')) and ex_rec is not None
             and ex_rec.result == 'exam',
             f'{r.status_code} {ex_rec.result if ex_rec else None}')
        case('标记后页面出现考试徽标', 'ovw-chk-exam' in
             c.get(u['insp_live']).get_data(as_text=True))

        # 撤销标记
        c.post(u['insp_mark'],
               json={'inspect_date': today.isoformat(),
                     'cells': [dict(base_cell, result='')]}, headers=hdrs)
        with app.app_context():
            case('撤销标记删除该格记录',
                 InspectionRecord.query.filter_by(id=rid).count() == 0)

        # 非法结果值应被忽略且不写库
        r = c.post(u['insp_mark'],
                   json={'inspect_date': today.isoformat(),
                         'cells': [dict(base_cell, result='bogus')]}, headers=hdrs)
        d2 = r.get_json() or {}
        case('非法结果值被忽略（不写库）',
             d2.get('updated') == 0 and d2.get('skipped') == 1, str(d2)[:100])

        js = c.get(u['insp_stats']).get_json() or {}
        case('统计接口新增 班级/检查人/节次/覆盖率 四个维度',
             all(k in js for k in ('by_class', 'by_inspector', 'by_period', 'coverage')))
        case('覆盖率字段完整（今日/本月 已查+应查）',
             {'today_checked', 'today_expected', 'month_checked', 'month_expected'}
             <= set((js.get('coverage') or {}).keys()))
        html3 = c.get(u['insp']).get_data(as_text=True)
        case('查课记录页新增覆盖率卡与班级/节次/检查人图表位置',
             all(k in html3 for k in ('statTodayChecked', 'statTodayRate', 'chartClass',
                                      'chartPeriod', 'tableInspector')))

    # 无权限账号不得标记：CSRF 可能先拦（400），但底线是"不能写库"
    with app.test_client() as c2:
        login(c2, 'ac_head', TEST_PWD)
        t2 = get_csrf(c2, '/')
        hdrs2 = {'X-CSRFToken': t2} if t2 else {}
        r = c2.post(u['insp_mark'],
                    json={'inspect_date': date.today().isoformat(),
                          'cells': [dict(grade='高一', class_name='01班',
                                         period_number=cell['period_number'],
                                         result='late')]},
                    headers=hdrs2)
        with app.app_context():
            leaked = InspectionRecord.query.filter_by(
                inspect_date=date.today(), grade='高一', class_name='01班',
                period=cell['period_number'], result='late').count()
        case('无查课权限的账号被拦且未写库',
             r.status_code in (400, 403) and leaked == 0,
             f'{r.status_code}/leaked={leaked}')

    # 2026-10-16：单节视图的数据层口径（周次过滤 / 「调走」标志 / 节次有课数）
    with app.app_context():
        from app.modules.academic.services import swap_service as _sw
        from app.modules.academic.services import term_service as _ts
        sched, wk = _ts.resolve_schedule_by_date(today)
        case('单节视图：节次有课数（供下拉标注与缺省节次）',
             bool(sched) and _sw.count_period_classes(sched.id, today, week=wk).get(3, 0) >= 1)
        rows = _sw.build_live_schedule(sched.id, today, 3, week=wk)
        case('单节视图：每行都带 is_moved_away 标志（区分"调走"与"空档"）',
             all('is_moved_away' in r for rs in rows.values() for r in rs))
        # 2026-10-16 边界遍历抓到的 500：年级筛选传的是**单个年级字符串**，
        # `column.in_(str)` 在 SQLAlchemy 2.0 下抛 ArgumentError → 单节视图整页 500
        try:
            _c1 = _sw.count_period_classes(sched.id, today, week=wk, grade_filter='高一')
            case('count_period_classes 接受单年级字符串（不抛 ArgumentError）',
                 isinstance(_c1, dict), str(_c1))
        except Exception as _e:      # noqa: BLE001
            case('count_period_classes 接受单年级字符串（不抛 ArgumentError）',
                 False, f'{type(_e).__name__}: {_e}')
        if wk is not None:
            # 造一条"周次不覆盖本周"的课：单节视图应与全天矩阵一样按空档处理
            far = f'{wk + 30}-{wk + 31}'
            e = ScheduleEntry(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                              weekday=today.isoweekday(), period_number=9,
                              subject='地理', teacher_uid='T900001',
                              teacher_name='张语文', room='101',
                              week_range=far, entry_type='normal')
            db.session.add(e)
            db.session.commit()
            try:
                off = _sw.build_live_schedule(sched.id, today, 9, week=wk)
                on = _sw.build_live_schedule(sched.id, today, 9, week=None)
                n_off = sum(1 for rs in off.values() for r in rs if r.get('subject'))
                n_on = sum(1 for rs in on.values() for r in rs if r.get('subject'))
                case('单节视图：周次不覆盖的课按空档处理（与全天矩阵同口径）',
                     n_off == 0 and n_on >= 1, f'{n_off}/{n_on}')
                case('单节视图：周次不覆盖的课标出 week_miss（卡片写「本周不上」）',
                     any(r.get('week_miss') == far for rs in off.values() for r in rs),
                     str([r.get('week_miss') for rs in off.values() for r in rs if r.get('week_miss')]))
            finally:
                db.session.delete(e)
                db.session.commit()


@item('SWAPEXCH')
def check_two_person_exchange():
    """两人换课（v1.19.49.46）：换时段 / 换班上课 / 请人代课 × 临时与永久，
    覆盖「对方先确认 → 教务审批 → 执行」全链路 + 拒绝 / 越权 / 校验路径。"""
    from app.modules.academic.services import swap_service

    class _Admin:
        role = 'admin'
        id = 1

        def has_perm(self, k):
            return True

    def _mk(g, cn, wd, pn, subj, tuid, tname):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range='1-20', entry_type='normal')
        db.session.add(e)
        return e

    swap_ids, entry_ids = [], []
    with app.app_context():
        # ── ① 换时段（临时）：申请 → 未确认不能审 → 同意 → 审批 → 执行 ──
        a1 = _mk('高二', '01班', 1, 1, '语文', 'TX0001', '甲语文')
        b1 = _mk('高二', '02班', 2, 2, '数学', 'TX0002', '乙数学')
        db.session.commit()
        entry_ids += [a1.id, b1.id]
        ok, msg, res = swap_service.apply_exchange_batch(
            applicant_uid='TX0001', applicant_name='甲语文',
            items=[{'entry_id': a1.id, 'partner_uid': 'TX0002', 'partner_entry_id': b1.id, 'swap_time': True, 'swap_class': False, 'swap_date': date(2026, 10, 14)}],
            kind='exchange', is_permanent=False, reason='换时段回归')
        sid = res.get('rep_id')
        swap_ids.append(sid)
        sw = db.session.get(ScheduleSwap, sid) if sid else None
        case('换课·换时段：申请创建成功（待对方确认 / 类型=两人换课）',
             bool(ok and sw and sw.exchange_kind == 'exchange' and sw.swap_time and not sw.swap_class and sw.swap_type == 'exchange'
                  and sw.partner_status == 'pending'), f'{msg} {res}')
        admin = _Admin()
        case('换课·换时段：对方未确认前，教务审不了（不进待审队列）',
             bool(sw) and not swap_service.can_review(sw, admin),
             f'partner_status={sw.partner_status if sw else None}')
        ok_r, msg_r, _ = swap_service.respond_exchange(sid, 'agree', 1, '乙数学')
        db.session.expire_all()
        sw = db.session.get(ScheduleSwap, sid)
        case('换课·换时段：对方同意后进入教务审批队列',
             bool(ok_r and sw.partner_status == 'agreed'
                  and swap_service.can_review(sw, admin)), f'{msg_r}')
        swap_service.review_swap(sid, 1, '教务主任', 'approve')
        swap_service.review_swap(sid, 1, '课表管理', 'approve')
        db.session.expire_all()
        sw = db.session.get(ScheduleSwap, sid)
        case('换课·换时段：两级审批通过 → 已通过（待执行）',
             sw.status == 'approved', f'status={sw.status}')
        ok_e, msg_e, _ = swap_service.execute_swap(sid, 1, '课表管理')
        db.session.expire_all()
        sw = db.session.get(ScheduleSwap, sid)
        t1 = db.session.get(ScheduleEntry, sw.target_entry_id) if sw.target_entry_id else None
        t2 = (db.session.get(ScheduleEntry, sw.partner_target_entry_id)
              if sw.partner_target_entry_id else None)
        wd = date(2026, 10, 14).isoweekday()  # 周三 → 3
        case('换课·换时段：执行生成两条当天条目（各自落到对方节次）',
             bool(ok_e and t1 and t2 and t1.weekday == wd and t1.period_number == 2
                  and t1.class_name == '01班' and t1.teacher_uid == 'TX0001'
                  and t2.weekday == wd and t2.period_number == 1
                  and t2.class_name == '02班' and t2.teacher_uid == 'TX0002'),
             f'{msg_e} t1={t1} t2={t2}')
        eff = swap_service.resolve_effective_entry(TS_ID, '高二', '01班', wd, 2,
                                                   date(2026, 10, 14))
        case('换课·换时段：实时课表按对调后生效（01班 周三第2节 = 甲的课）',
             bool(eff and eff.get('teacher_uid') == 'TX0001'), f'{eff}')
        case('换课·换时段：两条临时条目都不进常规周课表',
             bool(t1 and t2 and swap_service._temp_target_ids(TS_ID).issuperset({t1.id, t2.id})),
             'temp-target-ids')

        # ── ② 请人代课（临时）：只换教师，时间班级不动 ──
        a2 = _mk('高二', '03班', 4, 1, '英语', 'TX0001', '甲语文')
        db.session.commit()
        entry_ids.append(a2.id)
        ok2, msg2, res2 = swap_service.apply_exchange_batch(
            applicant_uid='TX0001', applicant_name='甲语文',
            items=[{'entry_id': a2.id, 'partner_uid': 'TX0002', 'swap_date': date(2026, 10, 15)}],
            kind='cover', is_permanent=False, reason='代课回归')
        sid2 = res2.get('rep_id')
        swap_ids.append(sid2)
        swap_service.respond_exchange(sid2, 'agree', 2, '乙数学')
        swap_service.review_swap(sid2, 1, '教务主任', 'approve')
        swap_service.review_swap(sid2, 1, '课表管理', 'approve')
        ok_e2, msg_e2, _ = swap_service.execute_swap(sid2, 1, '课表管理')
        db.session.expire_all()
        sw2 = db.session.get(ScheduleSwap, sid2)
        c1 = db.session.get(ScheduleEntry, sw2.target_entry_id) if sw2.target_entry_id else None
        case('换课·请人代课：执行后只有一条当天条目，教师换成对方（时间/班级不变）',
             bool(ok_e2 and c1 and c1.teacher_uid == 'TX0002' and c1.period_number == 1
                  and c1.class_name == '03班' and not sw2.partner_target_entry_id),
             f'{msg_e2} c1={c1}')

        # ── ③ 换班上课（永久）：两条原条目软删，新条目整体对调 ──
        a3 = _mk('高二', '05班', 1, 3, '语文', 'TX0001', '甲语文')
        b3 = _mk('高二', '06班', 3, 5, '语文', 'TX0002', '乙数学')
        db.session.commit()
        entry_ids += [a3.id, b3.id]
        ok3, msg3, res3 = swap_service.apply_exchange_batch(
            applicant_uid='TX0001', applicant_name='甲语文',
            items=[{'entry_id': a3.id, 'partner_uid': 'TX0002', 'partner_entry_id': b3.id, 'swap_time': True, 'swap_class': True}],
            kind='exchange', is_permanent=True, reason='换班回归')
        sid3 = res3.get('rep_id')
        swap_ids.append(sid3)
        swap_service.respond_exchange(sid3, 'agree', 2, '乙数学')
        swap_service.review_swap(sid3, 1, '教务主任', 'approve')
        swap_service.review_swap(sid3, 1, '课表管理', 'approve')
        ok_e3, msg_e3, _ = swap_service.execute_swap(sid3, 1, '课表管理')
        db.session.expire_all()
        o1 = db.session.get(ScheduleEntry, a3.id)
        o2 = db.session.get(ScheduleEntry, b3.id)
        sw3 = db.session.get(ScheduleSwap, sid3)
        n1 = db.session.get(ScheduleEntry, sw3.target_entry_id)
        n2 = db.session.get(ScheduleEntry, sw3.partner_target_entry_id)
        case('换课·换班（永久）：原条目软删 + 新条目整体对调（我上对方的课）',
             bool(ok_e3 and o1.is_deleted and o2.is_deleted
                  and n1.weekday == 3 and n1.period_number == 5
                  and n1.class_name == '06班' and n1.teacher_uid == 'TX0001'
                  and n2.weekday == 1 and n2.period_number == 3
                  and n2.class_name == '05班' and n2.teacher_uid == 'TX0002'),
             f'{msg_e3}')

        # ── ④ 对方拒绝 → 驳回并带原因；且不能重复响应 ──
        a4 = _mk('高二', '07班', 1, 4, '化学', 'TX0001', '甲语文')
        b4 = _mk('高二', '08班', 2, 4, '化学', 'TX0002', '乙数学')
        db.session.commit()
        entry_ids += [a4.id, b4.id]
        _ok4, _msg4, res4 = swap_service.apply_exchange_batch(
            applicant_uid='TX0001', applicant_name='甲语文',
            items=[{'entry_id': a4.id, 'partner_uid': 'TX0002', 'partner_entry_id': b4.id, 'swap_time': True, 'swap_class': False}],
            kind='exchange', is_permanent=True, reason='拒绝回归')
        sid4 = res4.get('rep_id')
        swap_ids.append(sid4)
        ok_r4, msg_r4, _ = swap_service.respond_exchange(sid4, 'refuse', 2, '乙数学',
                                                         note='那天我有事')
        db.session.expire_all()
        sw4 = db.session.get(ScheduleSwap, sid4)
        case('换课：对方拒绝 → 记录驳回并带原因（不可重复响应）',
             bool(ok_r4 and sw4.partner_status == 'refused' and sw4.status == 'rejected'
                  and '对方不同意' in (sw4.review_note or '')
                  and not swap_service.respond_exchange(sid4, 'agree', 2, '乙数学')[0]),
             f'{msg_r4} {sw4.status}/{sw4.partner_status}')

        # ── ⑤ 校验：非本人发起 / 同一节课重复提交 / 单双周不同 ──
        ok5, msg5, _ = swap_service.apply_exchange_batch(
            applicant_uid='TX9999', applicant_name='别人',
            items=[{'entry_id': a4.id, 'partner_uid': 'TX0002'}],
            kind='cover', is_permanent=True, reason='越权回归')
        case('换课：非授课教师本人发起 → 拒绝',
             (not ok5) and ('自己的课程' in msg5 or '本人' in msg5), msg5)
        a5 = _mk('高二', '09班', 2, 1, '生物', 'TX0001', '甲语文')
        b5 = _mk('高二', '10班', 3, 1, '生物', 'TX0002', '乙数学')
        db.session.commit()
        entry_ids += [a5.id, b5.id]
        _ok_a, _msg_a, res_a = swap_service.apply_exchange_batch(
            applicant_uid='TX0001', applicant_name='甲语文',
            items=[{'entry_id': a5.id, 'partner_uid': 'TX0002', 'partner_entry_id': b5.id, 'swap_time': True, 'swap_class': False}],
            kind='exchange', is_permanent=True, reason='重复回归一')
        swap_ids.append(res_a.get('rep_id'))
        ok_b, msg_b, _ = swap_service.apply_exchange_batch(
            applicant_uid='TX0001', applicant_name='甲语文',
            items=[{'entry_id': a5.id, 'partner_uid': 'TX0002', 'partner_entry_id': b5.id, 'swap_time': True, 'swap_class': False}],
            kind='exchange', is_permanent=True, reason='重复回归二')
        case('换课：同一节课已有待处理申请 → 拒绝重复提交',
             (not ok_b) and '待处理' in msg_b, msg_b)
        a6 = _mk('高二', '11班', 4, 2, '地理', 'TX0001', '甲语文')
        b6 = _mk('高二', '12班', 5, 2, '地理', 'TX0002', '乙数学')
        b6.week_range = '2-20'
        db.session.commit()
        entry_ids += [a6.id, b6.id]
        # 2026-10-14：两节课周次不同**也可以对调**（各保留自己的周次），不再拒绝
        ok7, msg7, res7 = swap_service.apply_exchange_batch(
            applicant_uid='TX0001', applicant_name='甲语文',
            items=[{'entry_id': a6.id, 'partner_uid': 'TX0002', 'partner_entry_id': b6.id, 'swap_time': True, 'swap_class': False}],
            kind='exchange', is_permanent=True, reason='单双周回归')
        swap_ids.append(res7.get('rep_id'))
        case('换课：两节课单双周设置不同 → 允许对调（各保留自己的周次）',
             bool(ok7 and res7.get('created') == 1), f'{msg7} {res7}')

        # ── 清理本次自建数据（临时库，保持其他项互不干扰）──
        for eid in entry_ids:
            e = db.session.get(ScheduleEntry, eid)
            if e:
                db.session.delete(e)
        for t in ScheduleEntry.query.filter(
                ScheduleEntry.note.like('%换课%'), ScheduleEntry.note.isnot(None)).all():
            db.session.delete(t)
        for one in [i for i in swap_ids if i]:
            s = db.session.get(ScheduleSwap, one)
            if s:
                db.session.delete(s)
        db.session.commit()


@item('SWAPEXBATCH')
def check_swap_exchange_batch():
    """两人换课 / 请人代课 —— **批量**（一次与多位老师换/代多节）+ 对调内容 + 双方日期。"""
    from app.models.timetable import ScheduleEntry, ScheduleSwap
    from app.modules.academic.services import swap_service

    class _Admin:
        role = 'admin'
        id = 1

        def has_perm(self, k):
            return True

    def _mk(g, cn, wd, pn, subj, tuid, tname):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range='1-20', entry_type='normal')
        db.session.add(e)
        return e

    admin = _Admin()
    entry_ids, batches = [], []
    with app.app_context():
        # ── ① 批量换课：甲的 2 节 ↔ 乙 1 节 + 丙 1 节（两种对调内容）──
        m1 = _mk('高三', '01班', 1, 1, '语文', 'BX0001', '甲老师')
        m2 = _mk('高三', '02班', 1, 2, '语文', 'BX0001', '甲老师')
        p1 = _mk('高三', '03班', 3, 4, '数学', 'BX0002', '乙老师')
        p2 = _mk('高三', '04班', 4, 5, '英语', 'BX0003', '丙老师')
        db.session.commit()
        entry_ids += [m1.id, m2.id, p1.id, p2.id]
        ok, msg, res = swap_service.apply_exchange_batch(
            applicant_uid='BX0001', applicant_name='甲老师',
            items=[{'entry_id': m1.id, 'partner_uid': 'BX0002',
                    'partner_entry_id': p1.id,
                    'swap_time': True, 'swap_class': True,
                    'swap_date': date(2026, 11, 2),
                    'partner_swap_date': date(2026, 11, 4)},
                   {'entry_id': m2.id, 'partner_uid': 'BX0003',
                    'partner_entry_id': p2.id,
                    'swap_time': True, 'swap_class': False,
                    'swap_date': date(2026, 11, 3),
                    'partner_swap_date': date(2026, 11, 5)}],
            kind='exchange', is_permanent=False, reason='批量换课回归')
        b1 = res.get('batch_id')
        batches.append(b1)
        rows = ScheduleSwap.query.filter_by(batch_id=b1).all()
        case('批量换课：一次与两位老师换两节 → 2 条记录共享同一批次',
             bool(ok and res.get('created') == 2 and b1), f'{msg} {res}')
        case('批量换课：每条都有自己的 partner（各自确认）',
             len({r.partner_uid for r in rows}) == 2
             and all(r.partner_status == 'pending' for r in rows),
             f'{[(r.partner_uid, r.partner_status) for r in rows]}')
        case('批量换课：对调内容按勾选分别落库（整条互换 / 只换时间）',
             sorted([(r.swap_time, r.swap_class) for r in rows])
             == [(True, False), (True, True)],
             f'{[(r.original_entry_id, r.swap_time, r.swap_class) for r in rows]}')
        case('批量换课：双方日期各自留存（我这天 / 对方那天）',
             sorted([(str(r.swap_date), str(r.partner_swap_date)) for r in rows])
             == [('2026-11-02', '2026-11-04'), ('2026-11-03', '2026-11-05')],
             f'{[(str(r.swap_date), str(r.partner_swap_date)) for r in rows]}')
        ok_r, msg_r, _ = swap_service.review_swap_batch(rows[0].id, 1, '教务主任',
                                                       'approve')
        case('批量换课：还有人没确认 → 不允许整批通过',
             (not ok_r) and '等待对方确认' in msg_r, msg_r)
        for r in rows:
            swap_service.respond_exchange(r.id, 'agree', 1, '对方老师')
        db.session.expire_all()
        rows = ScheduleSwap.query.filter_by(batch_id=b1).all()
        case('批量换课：各自同意后全部可审',
             all(swap_service.can_review(r, admin) for r in rows),
             f'{[(r.id, r.partner_status, r.status) for r in rows]}')

        # ── ② 执行：两条各自按"两边各自的日期"落位 ──
        row1 = [r for r in rows if r.original_entry_id == m1.id][0]
        swap_service.review_swap(row1.id, 1, '教务主任', 'approve')
        swap_service.review_swap(row1.id, 1, '课表管理', 'approve')
        ok_e, msg_e, _ = swap_service.execute_swap(row1.id, 1, '课表管理')
        db.session.expire_all()
        row1 = db.session.get(ScheduleSwap, row1.id)
        t_a = db.session.get(ScheduleEntry, row1.target_entry_id) if row1.target_entry_id else None
        t_b = (db.session.get(ScheduleEntry, row1.partner_target_entry_id)
               if row1.partner_target_entry_id else None)
        case('批量换课（整条互换）：执行后两条临时条目按各自日期生成',
             bool(ok_e and t_a and t_b
                  and t_a.weekday == date(2026, 11, 2).isoweekday()
                  and t_a.period_number == 4 and t_a.class_name == '03班'
                  and t_b.weekday == date(2026, 11, 4).isoweekday()
                  and t_b.period_number == 1 and t_b.class_name == '01班'),
             f'{msg_e} t_a={t_a} t_b={t_b}')

        # ── ③ 批量代课：一次请两位老师代两节 ──
        c1 = _mk('高三', '05班', 2, 1, '物理', 'BX0001', '甲老师')
        c2 = _mk('高三', '05班', 2, 2, '物理', 'BX0001', '甲老师')
        db.session.commit()
        entry_ids += [c1.id, c2.id]
        ok2, msg2, res2 = swap_service.apply_exchange_batch(
            applicant_uid='BX0001', applicant_name='甲老师',
            items=[{'entry_id': c1.id, 'partner_uid': 'BX0002',
                    'swap_date': date(2026, 11, 9)},
                   {'entry_id': c2.id, 'partner_uid': 'BX0003',
                    'swap_date': date(2026, 11, 9)}],
            kind='cover', is_permanent=False, reason='批量代课回归')
        b2 = res2.get('batch_id')
        batches.append(b2)
        rows2 = ScheduleSwap.query.filter_by(batch_id=b2).all()
        case('批量代课：一次请两位老师代两节 → 2 条记录同一批次',
             bool(ok2 and res2.get('created') == 2 and b2), f'{msg2} {res2}')
        case('批量代课：每条 partner 不同、且不牵动对方的课',
             len({r.partner_uid for r in rows2}) == 2
             and all(not r.partner_entry_id for r in rows2),
             f'{[(r.partner_uid, r.partner_entry_id) for r in rows2]}')

        # ── 清理本次自建数据 ──
        for eid in entry_ids:
            e = db.session.get(ScheduleEntry, eid)
            if e:
                db.session.delete(e)
        for t in ScheduleEntry.query.filter(
                ScheduleEntry.note.like('%换课%'), ScheduleEntry.note.isnot(None)).all():
            db.session.delete(t)
        for b in batches:
            for s in ScheduleSwap.query.filter_by(batch_id=b).all():
                db.session.delete(s)
        db.session.commit()


@item('SWAPMIXED')
def check_swap_mixed_batch():
    """调课页合并（v1.19.49.46）：一次提交 = 几节调空档 + 几节与同事换上课时间（同一批次）；
    2026-10-14：对调只换时间（不再连班级一起换）。"""
    from app.models.timetable import ScheduleEntry, ScheduleSwap
    from app.modules.academic.services import swap_service

    class _Admin:
        role = 'admin'
        id = 1

        def has_perm(self, k):
            return True

    def _mk(g, cn, wd, pn, subj, tuid, tname):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range='1-20', entry_type='normal')
        db.session.add(e)
        return e

    admin = _Admin()
    entry_ids, batch_id = [], None
    with app.app_context():
        # 甲的两节语文（周一1节 / 周二1节，都在 01班）；
        # 01班 周三5节 = 乙的数学（可对调对象）；01班 周四1节 = 空档
        a1 = _mk('高二', '01班', 1, 1, '语文', 'MX0001', '甲老师')
        a2 = _mk('高二', '01班', 2, 1, '语文', 'MX0001', '甲老师')
        b1 = _mk('高二', '01班', 3, 5, '数学', 'MX0002', '乙老师')
        db.session.commit()
        entry_ids += [a1.id, a2.id, b1.id]
        ok, msg, res = swap_service.apply_swap_batch(
            applicant_uid='MX0001', applicant_name='甲老师',
            items=[{'entry_id': a1.id, 'new_weekday': 4, 'new_period': 1,
                    'swap_date': date(2026, 11, 12)},                    # 空档
                   {'entry_id': a2.id, 'new_weekday': 3, 'new_period': 5,
                    'swap_date': date(2026, 11, 11),
                    'partner_entry_id': b1.id, 'swap_class': False,
                    'need_partner_confirm': True}],                      # 与乙换时间（需他确认）
            is_permanent=False, reason='混合批量回归')
        batch_id = res.get('batch_id')
        rows = ScheduleSwap.query.filter_by(batch_id=batch_id).all()
        normal = [r for r in rows if not r.partner_entry_id]
        exch = [r for r in rows if r.partner_entry_id]
        case('混合批量：一次「调空档 + 与同事换时间」→ 2 条同批次',
             bool(ok and res.get('created') == 2 and batch_id), f'{msg} {res}')
        case('混合批量：空档那条仍是普通调课（没有 partner、不需要对方确认）',
             len(normal) == 1 and normal[0].swap_type == 'personal'
             and normal[0].partner_status is None,
             f'{[(r.id, r.swap_type, r.partner_status) for r in rows]}')
        case('混合批量：对调那条带上 partner 且待确认（只换时间、不换班）',
             len(exch) == 1 and exch[0].partner_uid == 'MX0002'
             and exch[0].partner_status == 'pending'
             and exch[0].swap_time and not exch[0].swap_class,
             f'{[(r.partner_uid, r.partner_status, r.swap_time, r.swap_class) for r in exch]}')
        case('混合批量：普通那条可直接审；对调那条未确认前不能审',
             bool(normal and swap_service.can_review(normal[0], admin))
             and bool(exch and not swap_service.can_review(exch[0], admin)),
             'can_review 分流')
        swap_service.respond_exchange(exch[0].id, 'agree', 1, '乙老师')
        db.session.expire_all()
        ex = db.session.get(ScheduleSwap, exch[0].id)
        case('混合批量：对方同意后对调那条进入审批队列',
             swap_service.can_review(ex, admin), f'{ex.partner_status}')
        # 目标格被"我自己的课"占着 → 紫格（自交换）；这里用接口口径验证
        slots = swap_service.get_available_slots(TS_ID, '高二', '01班', 'MX0001',
                                                 exclude_entry_id=a1.id, include_all=True)
        wed5 = [s for s in slots if s['weekday'] == 3 and s['period_number'] == 5]
        case('目标格接口：有人上的格子返回「可换」而不是一味标红',
             bool(wed5) and wed5[0].get('swappable')
             and wed5[0].get('swap_teacher_uid') == 'MX0002',
             f'{wed5}')

        # ── 清理 ──
        for eid in entry_ids:
            e = db.session.get(ScheduleEntry, eid)
            if e:
                db.session.delete(e)
        for t in ScheduleEntry.query.filter(
                ScheduleEntry.note.like('%换课%'), ScheduleEntry.note.isnot(None)).all():
            db.session.delete(t)
        for s in ScheduleSwap.query.filter_by(batch_id=batch_id).all():
            db.session.delete(s)
        db.session.commit()


@item('SWAPPARITY')
def check_swap_parity():
    """单双周调课（2026-10-14）：同格的单/双周课不算冲突、跨周次也能对调、
    临时调到"不是上课周"的那天要被拒绝。"""
    from app.models.timetable import ScheduleEntry, ScheduleSwap
    from app.modules.academic.services import swap_service

    def _mk(g, cn, wd, pn, subj, tuid, tname, wr):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range=wr, entry_type='normal')
        db.session.add(e)
        return e

    entry_ids, swap_ids = [], []
    with app.app_context():
        # 96班：甲 双周 信息技术 与 乙 单周 心理健康 共用 周三第2节（典型单双周排法）
        a = _mk('高二', '96班', 3, 2, '信息技术', 'PZ0001', '甲老师', '双周')
        b = _mk('高二', '96班', 3, 2, '心理健康', 'PZ0002', '乙老师', '单周')
        # 97班：丙 双周课 / 丁 每周课（用于跨周次对调）
        c = _mk('高二', '97班', 3, 2, '信息技术', 'PZ0003', '丙老师', '双周')
        d = _mk('高二', '97班', 4, 3, '数学', 'PZ0004', '丁老师', '1-18')
        # 98班：戊 双周课（用于临时调课的周次校验）
        f = _mk('高二', '98班', 3, 2, '信息技术', 'PZ0005', '戊老师', '双周')
        db.session.commit()
        entry_ids += [a.id, b.id, c.id, d.id, f.id]

        # ① 目标格：双周课看"单周占用的那一格"= 自习空档（不是冲突、也不是对调）
        slots = swap_service.get_available_slots(
            TS_ID, '高二', '96班', 'PZ0001', exclude_entry_id=a.id, include_all=True,
            week_range='双周')
        wed2 = [s for s in slots if s['weekday'] == 3 and s['period_number'] == 2]
        case('单双周：双周课看单周占用格 = 自习空档（不再标冲突/对调）',
             bool(wed2) and wed2[0]['available'] and not wed2[0]['swappable'],
             f'{wed2}')
        slots_b = swap_service.get_available_slots(
            TS_ID, '高二', '96班', 'PZ0002', exclude_entry_id=b.id, include_all=True,
            week_range='单周')
        wed2b = [s for s in slots_b if s['weekday'] == 3 and s['period_number'] == 2]
        case('单双周：反过来（单周课看双周占用格）同样是空档',
             bool(wed2b) and wed2b[0]['available'] and not wed2b[0]['swappable'],
             f'{wed2b}')
        # 不传周次上下文时保守按占用处理（老口径兜底，避免误判）
        slots0 = swap_service.get_available_slots(
            TS_ID, '高二', '96班', 'PZ0001', exclude_entry_id=a.id, include_all=True)
        wed2c = [s for s in slots0 if s['weekday'] == 3 and s['period_number'] == 2]
        case('单双周：不传周次上下文时保守标为占用（老口径兜底）',
             bool(wed2c) and not wed2c[0]['available'] and wed2c[0]['swappable'],
             f'{wed2c}')

        # ② 跨周次对调：丙（双周）↔ 丁（每周）—— 各保留自己的周次
        ok, msg, res = swap_service.apply_swap_batch(
            applicant_uid='PZ0003', applicant_name='丙老师',
            items=[{'entry_id': c.id, 'new_weekday': 4, 'new_period': 3,
                    'partner_entry_id': d.id}],
            is_permanent=True, reason='单双周对调回归')
        sid = res.get('rep_id')
        swap_ids.append(sid)
        sw = db.session.get(ScheduleSwap, sid) if sid else None
        case('单双周：双周课与每周课可以对调（不再拒绝）',
             bool(ok and sw and sw.partner_entry_id == d.id and sw.swap_time
                  and not sw.swap_class), f'{msg} {res}')
        swap_service.respond_exchange(sid, 'agree', 1, '丁老师')
        swap_service.review_swap(sid, 1, '教务主任', 'approve')
        swap_service.review_swap(sid, 1, '课表管理', 'approve')
        ok_e, msg_e, _ = swap_service.execute_swap(sid, 1, '课表管理')
        db.session.expire_all()
        sw = db.session.get(ScheduleSwap, sid)
        n1 = db.session.get(ScheduleEntry, sw.target_entry_id) if sw.target_entry_id else None
        n2 = (db.session.get(ScheduleEntry, sw.partner_target_entry_id)
              if sw.partner_target_entry_id else None)
        case('单双周：执行后各自保留自己的周次（双周 → 周四第3节仍双周）',
             bool(ok_e and n1 and n2
                  and n1.weekday == 4 and n1.period_number == 3 and n1.week_range == '双周'
                  and n2.weekday == 3 and n2.period_number == 2 and n2.week_range == '1-18'),
             f'{msg_e} n1={n1.week_range if n1 else None} n2={n2.week_range if n2 else None}')
        if n1:
            entry_ids.append(n1.id)
        if n2:
            entry_ids.append(n2.id)

        # ③ 临时调课：目标日期不是这节课的上课周 → 拒绝并说明是第几周
        ok_bad, msg_bad, res_bad = swap_service.apply_swap_batch(
            applicant_uid='PZ0005', applicant_name='戊老师',
            items=[{'entry_id': f.id, 'new_weekday': 4, 'new_period': 3,
                    'swap_date': date(2026, 9, 15)}],       # 第3周 = 单周
            is_permanent=False, reason='单周日期调双周课')
        bad_desc = ((res_bad.get('conflicts') or [{}])[0]).get('desc', '')
        case('单双周：临时调到"不是上课周"的那天 → 拒绝（明细说明周次）',
             (not ok_bad) and ('周次' in bad_desc or '上课周' in bad_desc),
             f'{msg_bad} / {bad_desc}')
        ok_ok, msg_ok, res_ok = swap_service.apply_swap_batch(
            applicant_uid='PZ0005', applicant_name='戊老师',
            items=[{'entry_id': f.id, 'new_weekday': 4, 'new_period': 3,
                    'swap_date': date(2026, 9, 8)}],        # 第2周 = 双周
            is_permanent=False, reason='双周日期调双周课')
        swap_ids.append(res_ok.get('rep_id'))
        case('单双周：临时调到它的上课周那天 → 正常创建',
             bool(ok_ok and res_ok.get('created') == 1), f'{msg_ok} {res_ok}')

        # ── 清理 ──
        for s in ScheduleSwap.query.filter(
                ScheduleSwap.id.in_([i for i in swap_ids if i])).all():
            db.session.delete(s)
        db.session.commit()
        for eid in entry_ids:
            e = db.session.get(ScheduleEntry, eid)
            if e:
                db.session.delete(e)
        for t in ScheduleEntry.query.filter(
                ScheduleEntry.note.like('%换课%'), ScheduleEntry.note.isnot(None)).all():
            db.session.delete(t)
        db.session.commit()


@item('SWAPSELF')
def check_swap_self_swap():
    """我自己的两节课互换（紫格）：不需要任何人确认，执行时两条一起换位置。"""
    from app.models.timetable import ScheduleEntry, ScheduleSwap
    from app.modules.academic.services import swap_service

    class _Admin:
        role = 'admin'
        id = 1

        def has_perm(self, k):
            return True

    def _mk(g, cn, wd, pn, subj, tuid, tname):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range='1-20', entry_type='normal')
        db.session.add(e)
        return e

    admin = _Admin()
    entry_ids, sid = [], None
    with app.app_context():
        a = _mk('高二', '01班', 1, 1, '语文', 'SX0001', '甲老师')
        b = _mk('高二', '01班', 3, 5, '语文', 'SX0001', '甲老师')
        db.session.commit()
        entry_ids += [a.id, b.id]
        slots = swap_service.get_available_slots(TS_ID, '高二', '01班', 'SX0001',
                                                 exclude_entry_id=a.id, include_all=True)
        wed5 = [s for s in slots if s['weekday'] == 3 and s['period_number'] == 5]
        case('自己的另一节课：接口返回「可自换」而不是标红冲突',
             bool(wed5) and wed5[0].get('swappable') and wed5[0].get('swap_self'),
             f'{wed5}')
        ok, msg, res = swap_service.apply_swap_batch(
            applicant_uid='SX0001', applicant_name='甲老师',
            items=[{'entry_id': a.id, 'new_weekday': 3, 'new_period': 5,
                    'partner_entry_id': b.id}],
            is_permanent=True, reason='自己两节互换')
        sid = res.get('rep_id')
        sw = db.session.get(ScheduleSwap, sid) if sid else None
        case('自己两节互换：申请成功且不需要对方确认',
             bool(ok and sw and sw.partner_uid == 'SX0001'
                  and sw.partner_status is None), f'{msg} {res}')
        case('自己两节互换：可直进教务审批（不被「待对方确认」卡住）',
             bool(sw) and swap_service.can_review(sw, admin),
             f'{sw.status if sw else None}')
        swap_service.review_swap(sid, 1, '教务主任', 'approve')
        swap_service.review_swap(sid, 1, '课表管理', 'approve')
        ok_e, msg_e, _ = swap_service.execute_swap(sid, 1, '课表管理')
        db.session.expire_all()
        sw = db.session.get(ScheduleSwap, sid)
        n1 = db.session.get(ScheduleEntry, sw.target_entry_id) if sw.target_entry_id else None
        n2 = (db.session.get(ScheduleEntry, sw.partner_target_entry_id)
              if sw.partner_target_entry_id else None)
        o1 = db.session.get(ScheduleEntry, a.id)
        o2 = db.session.get(ScheduleEntry, b.id)
        case('自己两节互换（永久）：执行后两条位置对调、原条目软删',
             bool(ok_e and o1.is_deleted and o2.is_deleted and n1 and n2
                  and n1.weekday == 3 and n1.period_number == 5
                  and n2.weekday == 1 and n2.period_number == 1
                  and n1.teacher_uid == 'SX0001' and n2.teacher_uid == 'SX0001'),
             f'{msg_e} n1={n1} n2={n2}')

        # ── 清理 ──
        for eid in entry_ids:
            e = db.session.get(ScheduleEntry, eid)
            if e:
                db.session.delete(e)
        for t in ScheduleEntry.query.filter(
                ScheduleEntry.note.like('%换课%'), ScheduleEntry.note.isnot(None)).all():
            db.session.delete(t)
        s = db.session.get(ScheduleSwap, sid)
        if s:
            db.session.delete(s)
        db.session.commit()


@item('SWAPOFFSET')
def check_swap_bulk_offset():
    """统一调课「整体平移 N 节」：各按自己的原节次挪 —— 一整天的课一次搞定。"""
    from app.models.timetable import ScheduleEntry, ScheduleSwap
    from app.modules.academic.services import swap_service

    def _mk(g, cn, wd, pn, subj, tuid, tname):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range='1-20', entry_type='normal')
        db.session.add(e)
        return e

    entry_ids, batch_id = [], None
    with app.app_context():
        # 用自建班级（固定测试数据里高二 01 班周一第 2 节已有课，会撞真实冲突）
        a = _mk('高三', '90班', 1, 5, '语文', 'BO0001', '甲老师')
        b = _mk('高三', '91班', 1, 6, '数学', 'BO0002', '乙老师')
        c = _mk('高三', '92班', 1, 7, '英语', 'BO0003', '丙老师')
        db.session.commit()
        entry_ids += [a.id, b.id, c.id]
        ok, msg, res = swap_service.bulk_apply_swap(
            applicant_uid='BO0001', applicant_name='甲老师',
            entry_ids=entry_ids, new_weekday=None, new_period=None,
            is_permanent=True, reason='整体后移一节', period_offset=1)
        batch_id = res.get('batch_id')
        rows = ScheduleSwap.query.filter_by(batch_id=batch_id).all()
        case('统一调课·整体平移：3 节一次提交、合成 1 批',
             bool(ok and res.get('created') == 3 and batch_id), f'{msg} {res}')
        got = sorted([(r.original_entry_id, r.new_period) for r in rows])
        case('统一调课·整体平移：各按原节次后移 1 节（5→6、6→7、7→8）',
             got == sorted([(a.id, 6), (b.id, 7), (c.id, 8)]), f'{got}')
        ok2, msg2, _ = swap_service.bulk_apply_swap(
            applicant_uid='BO0001', applicant_name='甲老师',
            entry_ids=[c.id], new_weekday=None, new_period=None,
            is_permanent=True, reason='平移越界', period_offset=99)
        case('统一调课·整体平移：平移值越界 → 拒绝', (not ok2), msg2)
        # 留空维度仍应落库为 NULL（执行时沿用原值），不被平移逻辑破坏
        ok3, msg3, res3 = swap_service.bulk_apply_swap(
            applicant_uid='BO0001', applicant_name='甲老师',
            entry_ids=[a.id], new_weekday=None, new_period=None,
            new_room='综合楼305', is_permanent=True, reason='只换教室')
        row3 = ScheduleSwap.query.filter_by(batch_id=res3.get('batch_id')).first()
        case('统一调课：不平移时仍保留「留空沿用原值」（new_period 为 NULL）',
             bool(ok3 and row3 and row3.new_period is None), f'{msg3}')

        # ── 清理 ──
        for eid in entry_ids:
            e = db.session.get(ScheduleEntry, eid)
            if e:
                db.session.delete(e)
        for bid in (batch_id, res3.get('batch_id')):
            for s in ScheduleSwap.query.filter_by(batch_id=bid).all():
                db.session.delete(s)
        db.session.commit()


@item('WORKBENCHSWAP')
def check_workbench_swap():
    """教师工作台里的调课区块（v1.19.49.46）：入口 + 待我确认 + 我的申请。"""
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get('/workbench/')      # 蓝图前缀 /workbench + 路由 '/'（不带斜杠会 308）
        case('工作台：首页 200', r.status_code == 200, str(r.status_code))
        html = r.get_data(as_text=True) if r.status_code == 200 else ''
        # 申请入口受 academic.swap 权限控制（无权限时只保留"查看记录"），
        # 所以这里只断言区块存在 + 有权限时两个入口都在。
        case('工作台：含调课区块',
             bool(html) and ('调课' in html), 'swap block')
        has_swap_entry = ('/academic/swap/apply' in html) and ('/academic/swap/cover' in html)
        case('工作台：调课入口（有 academic.swap 时出现两个申请入口，否则至少可查看记录）',
             bool(html) and (has_swap_entry or '/academic/swap' in html),
             f'entries={has_swap_entry}')


@item('WORKBENCHPROFILE')
def check_workbench_profile():
    """教师工作台（2026-10-13，用户口径："哪怕显示一个欢迎到访都行"）：
    首页**不展示**手机号 / 登录密码 / 身份证号等个人信息，只留欢迎语与身份徽章；
    隐私信息与修改入口统一在「个人中心」页（workbench.profile）。"""
    with app.app_context():
        u = User.query.filter_by(username='wb_prof').first()
        if not u:
            u = User(username='wb_prof', real_name='工作台测试教师', role='teacher',
                     must_change_pwd=False)
            u.set_password(TEST_PWD)
            db.session.add(u)
            db.session.flush()
        t = Teacher.query.filter_by(teacher_uid='TWB001').first()
        if not t:
            t = Teacher(teacher_uid='TWB001', name='工作台测试教师', subject='语文',
                        status='active', phone='13800000001', user_id=u.id)
            db.session.add(t)
            db.session.commit()
        uid, tid = u.id, t.id

    with app.test_client() as c:
        login(c, 'wb_prof', TEST_PWD)
        r = c.get('/workbench/')
        html = r.get_data(as_text=True) if r.status_code == 200 else ''
        case('工作台首页：200', r.status_code == 200, str(r.status_code))
        case('工作台首页：有「个人中心」入口', '/workbench/profile' in html, 'entry')
        # 2026-10-13 用户口径：首页不展示个人信息（手机号 / 登录密码 / 身份证号 / 账号）
        case('工作台首页：不再出现手机号与隐私三项标签',
             bool(html) and all(k not in html for k in
                                ('手机号（登录账号）', '登录密码', '身份证号')
                                ) and '13800000001' not in html, 'privacy')
        case('工作台首页：改为欢迎卡片（欢迎语 + 身份/权限组徽章）',
             bool(html) and '欢迎' in html and '个人中心' in html, 'welcome')
        case('工作台首页：不再有「修改手机号」弹窗', 'phoneModal' not in html, 'modal')

        r2 = c.get('/workbench/profile')
        h2 = r2.get_data(as_text=True) if r2.status_code == 200 else ''
        case('个人信息页 200', r2.status_code == 200, str(r2.status_code))
        case('个人信息页：隐私三项齐全（手机号/登录密码/身份证）',
             all(k in h2 for k in ('手机号（登录账号）', '登录密码', '身份证号')), 'items')
        case('个人信息页：手机号表单回跳本页',
             'name="return_to" value="profile"' in h2, 'return_to')
        case('个人信息页：改密表单复用 auth 改密路由并回跳本页',
             ('name="next"' in h2) and ('name="old_password"' in h2), 'pwd form')

        # ① 手机号：密码错 → 拒绝；密码对 → 登录名与教师名单同步
        csrf = get_csrf(c, '/workbench/profile')
        bad = c.post('/workbench/phone',
                     data={'csrf_token': csrf, 'new_phone': '13800000002',
                           'current_password': 'wrong-pwd', 'return_to': 'profile'})
        with app.app_context():
            case('个人信息：密码错误时手机号不变',
                 db.session.get(User, uid).username == 'wb_prof'
                 and db.session.get(Teacher, tid).phone == '13800000001', 'unchanged')
        case('个人信息：手机号修改失败回跳个人信息页',
             bad.status_code == 302 and bad.headers['Location'].endswith('/workbench/profile'),
             str(bad.headers.get('Location')))
        csrf = get_csrf(c, '/workbench/profile')
        ok = c.post('/workbench/phone',
                    data={'csrf_token': csrf, 'new_phone': '13800000002',
                          'current_password': TEST_PWD, 'return_to': 'profile'})
        with app.app_context():
            u2, t2 = db.session.get(User, uid), db.session.get(Teacher, tid)
            case('个人信息：手机号修改成功并同步登录名 / 教师名单',
                 u2.username == '13800000002' and t2.phone == '13800000002',
                 f'{u2.username}/{t2.phone}')
            # 复原（同一进程内后续用例还会登录这个账号）
            u2.username, t2.phone = 'wb_prof', '13800000001'
            db.session.commit()
        case('个人信息：手机号修改成功回跳个人信息页',
             ok.status_code == 302 and ok.headers['Location'].endswith('/workbench/profile'),
             str(ok.headers.get('Location')))

        # ② 登录密码：走 auth.change_password 同一套校验，next 回跳个人信息页
        csrf = get_csrf(c, '/workbench/profile')
        bad2 = c.post('/change-password',
                      data={'csrf_token': csrf, 'old_password': 'nope-not-it',
                            'new_password': 'New#Pwd2026', 'confirm_password': 'New#Pwd2026',
                            'next': '/workbench/profile'})
        case('个人信息：原密码错误 → 回跳个人信息页（不落到独立改密页）',
             bad2.status_code == 302 and bad2.headers['Location'].endswith('/workbench/profile'),
             str(bad2.headers.get('Location')))
        with app.app_context():
            case('个人信息：原密码错误时口令未变',
                 db.session.get(User, uid).check_password(TEST_PWD), 'pwd kept')
        csrf = get_csrf(c, '/workbench/profile')
        okp = c.post('/change-password',
                     data={'csrf_token': csrf, 'old_password': TEST_PWD,
                           'new_password': 'New#Pwd2026', 'confirm_password': 'New#Pwd2026',
                           'next': '/workbench/profile'})
        case('个人信息：改密成功 → 回跳个人信息页',
             okp.status_code == 302 and okp.headers['Location'].endswith('/workbench/profile'),
             str(okp.headers.get('Location')))
        with app.app_context():
            case('个人信息：新口令已生效',
                 db.session.get(User, uid).check_password('New#Pwd2026'), 'new pwd')


@item('THEMEENTRY')
def check_theme_entry():
    """主题切换入口唯一化（2026-10-13 用户口径：头像菜单里不要「切换为深色模式」）：
    顶栏 `#themeToggle` 按钮保留，用户下拉菜单那项及其 JS 引用全部删除。"""
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get('/workbench/')
        html = r.get_data(as_text=True) if r.status_code == 200 else ''
        case('主题切换：顶栏按钮保留', bool(html) and 'id="themeToggle"' in html, 'button')
        case('主题切换：头像菜单不再有第二入口',
             bool(html) and 'themeToggleItem' not in html
             and '切换为深色模式' not in html, 'menu item')
    with open(os.path.join(BASE, 'app', 'templates', 'base.html'), encoding='utf-8') as f:
        tpl = f.read()
    case('主题切换：base.html 不再引用 themeToggleItem',
         'themeToggleItem' not in tpl, 'template')
    case('主题切换：菜单项 <li> 已删除（模板里只剩注释）',
         'id="themeToggleItem"' not in tpl
         and tpl.count('class="dropdown-item"') >= 1, 'li removed')


@item('BANDTPL')
def check_band_template_suggest():
    """划线分层「先定模板」（2026-10-13）：band-templates API 按**本场已保存划线的
    层名（归一化，去尾「线」）**推荐模板；AI 导入绑定模板同口径，不再重复新建。"""
    from datetime import date as _date
    from app.models.grades import Exam, ExamBand, BandTemplate

    JUNK_TPL = 'BANDTPL干扰模板（清北985211特控本科）'

    with app.app_context():
        exam = Exam.query.filter_by(name='BANDTPL划线模板测试').first()
        if not exam:
            exam = Exam(name='BANDTPL划线模板测试', grade='2024级',
                        exam_date=_date(2026, 5, 20), exam_type='联考')  # 不命中任何模板的适用类型
            db.session.add(exam)
            db.session.flush()
            for i, (nm, v) in enumerate([('985', 750), ('211', 700),
                                         ('特控', 560), ('本科', 469)], start=1):
                db.session.add(ExamBand(exam_id=exam.id, direction='', subject='总分',
                                        seq=i, name=nm, lower_mode='score',
                                        lower_value=float(v)))
            db.session.commit()
        # 干扰项：模拟 AI 导入遗留的「多两层」模板（命中数一样，但多出没有划线值的层），
        # 且 sort_order=0 排在最前 —— 旧实现"先到先得"会推荐它
        if not BandTemplate.query.filter_by(name=JUNK_TPL).first():
            junk = BandTemplate(name=JUNK_TPL, lower_mode='score', sort_order=0,
                                remark='回归测试用干扰模板', is_builtin=False)
            junk.set_layers([{'name': n, 'ratio': None} for n in
                             ('清北线', '985线', '211线', '特控线', '本科线')])
            db.session.add(junk)
            db.session.commit()
        exam_id = exam.id

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get('/grades/api/band-templates', query_string={'exam_id': exam_id})
        d = (r.get_json() or {}).get('data') or {}
        case('划线模板：API 200 且带 suggest_reason', r.status_code == 200
             and 'suggest_reason' in d, str(r.status_code))
        case('划线模板：未绑定时按已保存划线层名推荐',
             d.get('suggest_reason') == 'lines' and d.get('suggested_id'),
             'reason=%s suggested=%s' % (d.get('suggest_reason'), d.get('suggested_id')))
        with app.app_context():
            default_tpl = BandTemplate.query.filter_by(name='默认模板').first()
            junk_tpl = BandTemplate.query.filter_by(name=JUNK_TPL).first()
            case('划线模板：推荐命中的是「默认模板」（985/211/特控/本科 归一化对上）',
                 default_tpl is not None and d.get('suggested_id') == default_tpl.id,
                 'suggested=%s default=%s' % (d.get('suggested_id'),
                                              default_tpl.id if default_tpl else None))
            # 命中数相同时比「谁少出空层」：AI 遗留的五层模板虽然也命中 4 层，
            # 但比默认模板多出 1 层没有划线值的层 —— 不该被推荐
            case('划线模板：命中数相同时不推荐「多出空层」的遗留模板',
                 junk_tpl is not None and d.get('suggested_id') != junk_tpl.id,
                 'suggested=%s junk=%s' % (d.get('suggested_id'),
                                           junk_tpl.id if junk_tpl else None))
        case('划线模板：返回命中/待填层数（前端展示推荐依据）',
             d.get('suggest_hit') == 4 and d.get('suggest_extra') == 0,
             'hit=%s extra=%s' % (d.get('suggest_hit'), d.get('suggest_extra')))

        # ④ 兜底：全新考试（无划线、类型也没匹配）→ 仍推荐内置「默认模板」，
        #    不落到「不套用 + 默认四层（优秀/良好/及格/待提升）」
        with app.app_context():
            e2 = Exam.query.filter_by(name='BANDTPL兜底测试').first()
            if not e2:
                e2 = Exam(name='BANDTPL兜底测试', grade='2024级',
                          exam_date=_date(2026, 5, 21), exam_type='ZZZ不存在类型')
                db.session.add(e2)
                db.session.commit()
            e2_id = e2.id
        r2 = c.get('/grades/api/band-templates', query_string={'exam_id': e2_id})
        d2 = (r2.get_json() or {}).get('data') or {}
        with app.app_context():
            default_tpl = BandTemplate.query.filter_by(name='默认模板').first()
            case('划线模板：无任何依据时兜底推荐内置「默认模板」（不再不套用）',
                 d2.get('suggest_reason') == 'default'
                 and default_tpl is not None
                 and d2.get('suggested_id') == default_tpl.id,
                 'reason=%s suggested=%s' % (d2.get('suggest_reason'),
                                             d2.get('suggested_id')))
        # AI 导入的模板归一化匹配：同一线名序列只认已有模板，不重复新建
        with app.app_context():
            from app.modules.grades.routes.ai_import import _norm_line
            case('划线模板：线名归一化（去尾「线」）口径',
                 _norm_line(' 特控线 ') == '特控' and _norm_line('本科') == '本科',
                 _norm_line('特控线'))

    with app.app_context():   # 清理，避免污染其它用例的考试列表
        exam = Exam.query.filter_by(name='BANDTPL划线模板测试').first()
        if exam:
            ExamBand.query.filter_by(exam_id=exam.id).delete()
            db.session.delete(exam)
        junk_tpl = BandTemplate.query.filter_by(name=JUNK_TPL).first()
        if junk_tpl:
            db.session.delete(junk_tpl)
        e2 = Exam.query.filter_by(name='BANDTPL兜底测试').first()
        if e2:
            ExamBand.query.filter_by(exam_id=e2.id).delete()
            db.session.delete(e2)
        db.session.commit()


@item('AUDITP0')
def check_audit_p0_scope_guards():
    """全面审计 P0 修复回归（2026-10-13，docs/审计报告/全面审计报告_20261013.md）：

    · H-1 业绩附件删除：改走 `ach_svc.can_view`（academic.view 持有者 OR 提交者本人）
      —— 「只勾 academic.edit、没勾 academic.view」的权限组不得删他人附件（403）
    · M-1 划线模板绑定：改走 `assert_exam_visible` —— 越界年级 403
    · M-2 考务 from-lib 生成考场：改走 `_get_affair_checked` —— 越界年级 403
    三处均为"按 id 取对象但不校验数据范围"的写操作，故用**受限账号实打**验证。
    """
    from datetime import date as _date
    from app.models import User, PermissionGroup, UserDataScope
    from app.models.academic import TeacherAchievement, AchievementAttachment
    from app.models.grades import (Exam, ExamAffair, AffairRoom, AffairRoomLib,
                                   BandTemplate)

    GRADE_MINE, GRADE_OTHER = '2025级', '2024级'

    with app.app_context():
        # ① 只有写入档（无 academic.view）的组 —— 验 H-1
        pg_edit = PermissionGroup.query.filter_by(name='AUDITP0仅编辑组').first()
        if not pg_edit:
            pg_edit = PermissionGroup(name='AUDITP0仅编辑组', scope_type='school',
                                      role='staff', description='审计用例：仅 academic.edit')
            db.session.add(pg_edit)
            db.session.flush()
            pg_edit.set_menu_keys(['academic.edit'])
        # ② 有 grades.edit/grades.settings，但数据范围只授 2025 级 —— 验 M-1/M-2
        pg_scope = PermissionGroup.query.filter_by(name='AUDITP0范围组').first()
        if not pg_scope:
            pg_scope = PermissionGroup(name='AUDITP0范围组', scope_type='school',
                                       role='staff', description='审计用例：范围受限')
            db.session.add(pg_scope)
            db.session.flush()
            pg_scope.set_menu_keys(['grades.edit', 'grades.settings'])

        def _get_user(uname, pg, scope_grade=None):
            u = User.query.filter_by(username=uname).first()
            if not u:
                u = User(username=uname, real_name=uname, role='staff',
                         permission_group_id=pg.id, must_change_pwd=False)
                u.set_password(TEST_PWD)
                db.session.add(u)
                db.session.flush()
            if scope_grade and not UserDataScope.query.filter_by(
                    user_id=u.id, grade=scope_grade).first():
                db.session.add(UserDataScope(user_id=u.id, grade=scope_grade))
            return u

        u_edit = _get_user('audit_edit', pg_edit)
        u_scope = _get_user('audit_scope', pg_scope, GRADE_MINE)
        db.session.flush()

        # ③ 业绩 + 两个附件（提交人是**别人**）
        rec = TeacherAchievement.query.filter_by(title='AUDITP0审计业绩').first()
        if not rec:
            rec = TeacherAchievement(teacher_uid='TAUDIT0', teacher_name='审计用例',
                                     category='honor', title='AUDITP0审计业绩',
                                     status='approved', submitted_by=u_scope.id)
            db.session.add(rec)
            db.session.flush()
        att_ids = []
        for i in (1, 2):
            a = AchievementAttachment(achievement_id=rec.id,
                                      file_name=f'审计附件{i}.pdf',
                                      stored_name=f'audit{i}.pdf', ext='pdf',
                                      size=10, uploaded_by=u_scope.id)
            db.session.add(a)
            db.session.flush()
            att_ids.append(a.id)

        # ④ 别人年级的考试 + 考务批次 + 全局房间库 + 一个模板
        exam = Exam.query.filter_by(name='AUDITP0越界考试').first()
        if not exam:
            exam = Exam(name='AUDITP0越界考试', grade=GRADE_OTHER,
                        exam_date=_date(2026, 5, 22), exam_type='AUDITP0')
            db.session.add(exam)
            db.session.flush()
        affair = ExamAffair.query.filter_by(name='AUDITP0越界考务').first()
        if not affair:
            affair = ExamAffair(name='AUDITP0越界考务', grade=GRADE_OTHER,
                                exam_id=exam.id)
            db.session.add(affair)
            db.session.flush()
        lib = AffairRoomLib.query.filter_by(location='AUDITP0教室').first()
        if not lib:
            lib = AffairRoomLib(location='AUDITP0教室', capacity=30, note='审计用例')
            db.session.add(lib)
            db.session.flush()
        tpl = BandTemplate.query.filter_by(name='默认模板').first()
        if not tpl:
            tpl = BandTemplate(name='AUDITP0模板', lower_mode='score',
                               sort_order=99, is_builtin=False)
            tpl.set_layers([{'name': 'A线', 'ratio': None}])
            db.session.add(tpl)
            db.session.flush()
        db.session.commit()
        exam_id, affair_id, lib_id, tpl_id = exam.id, affair.id, lib.id, tpl.id

    with app.test_client() as c:
        # —— H-1：只有 academic.edit 的账号删他人附件 → 403（且不落库）——
        login(c, 'audit_edit', TEST_PWD)
        csrf = get_csrf(c, '/workbench/')
        r = c.post(f'/academic/achievements/attachment/{att_ids[0]}/delete',
                   data={'csrf_token': csrf})
        case('审计P0：无 academic.view 的编辑账号删他人附件被拒（403）',
             r.status_code == 403, str(r.status_code))
        with app.app_context():
            case('审计P0：被拒后附件仍在（未落库）',
                 db.session.get(AchievementAttachment, att_ids[0]) is not None, 'kept')
        c.get('/logout')

        # 合法路径不回归：admin（有 academic.view）可正常删
        login(c, 'ac_admin', TEST_PWD)
        csrf = get_csrf(c, '/academic/achievements')
        r = c.post(f'/academic/achievements/attachment/{att_ids[0]}/delete',
                   data={'csrf_token': csrf})
        case('审计P0：有 academic.view 的教务端仍可正常删除（200）',
             r.status_code == 200, str(r.status_code))
        with app.app_context():
            case('审计P0：合法删除已生效',
                 db.session.get(AchievementAttachment, att_ids[0]) is None, 'deleted')
        c.get('/logout')

        # —— M-1：范围只有 2025 级的账号，给 2024 级考试绑模板 → 403 ——
        login(c, 'audit_scope', TEST_PWD)
        csrf = get_csrf(c, '/workbench/')
        r = c.post('/grades/api/exam-band-template',
                   json={'exam_id': exam_id, 'template_id': tpl_id},
                   headers={'X-CSRFToken': csrf})
        case('审计P0：越界年级绑定划线模板被拒（403）',
             r.status_code == 403, str(r.status_code))
        with app.app_context():
            case('审计P0：越界绑定未落库',
                 db.session.get(Exam, exam_id).band_template_id() != tpl_id, 'unbound')
        c.get('/logout')

        # 合法路径不回归：admin 可正常绑定/解绑
        login(c, 'ac_admin', TEST_PWD)
        csrf = get_csrf(c, '/grades/band-templates')
        r = c.post('/grades/api/exam-band-template',
                   json={'exam_id': exam_id, 'template_id': tpl_id},
                   headers={'X-CSRFToken': csrf})
        case('审计P0：有范围的账号绑定不受影响（200）',
             r.status_code == 200, str(r.status_code))
        r = c.post('/grades/api/exam-band-template',
                   json={'exam_id': exam_id, 'template_id': 0},
                   headers={'X-CSRFToken': csrf})
        with app.app_context():
            case('审计P0：解绑也走同一守卫（0=解绑 200 + 已解绑）',
                 r.status_code == 200
                 and not db.session.get(Exam, exam_id).band_template_id(), 'unbind')

        # —— M-2：越界年级从房间库生成考场 → 403 ——
        c.get('/logout')                     # 必须先退出 admin，否则仍以 admin 身份发请求
        login(c, 'audit_scope', TEST_PWD)
        csrf = get_csrf(c, '/workbench/')
        r = c.post(f'/grades/affairs/{affair_id}/rooms/from-lib',
                   data={'csrf_token': csrf, 'lib_id': lib_id})
        case('审计P0：越界年级从房间库建考场被拒（403）',
             r.status_code == 403, str(r.status_code))
        with app.app_context():
            case('审计P0：越界考场未落库',
                 AffairRoom.query.filter_by(affair_id=affair_id,
                                            location='AUDITP0教室').count() == 0,
                 'no room')
        c.get('/logout')

        # 合法路径不回归：admin 同批次建考场 → 200
        login(c, 'ac_admin', TEST_PWD)
        csrf = get_csrf(c, '/workbench/')
        r = c.post(f'/grades/affairs/{affair_id}/rooms/from-lib',
                   data={'csrf_token': csrf, 'lib_id': lib_id})
        case('审计P0：有范围的账号建考场不受影响（200）',
             r.status_code == 200, str(r.status_code))

    # 清理，避免污染本轮的考试/考务/业绩列表（分段提交，避免 ORM 删除顺序引发外键告警）
    with app.app_context():
        AffairRoom.query.filter_by(affair_id=affair_id).delete(synchronize_session=False)
        ExamAffair.query.filter_by(id=affair_id).delete(synchronize_session=False)
        AffairRoomLib.query.filter_by(location='AUDITP0教室').delete(synchronize_session=False)
        db.session.commit()
    with app.app_context():
        Exam.query.filter_by(id=exam_id).delete(synchronize_session=False)
        db.session.commit()
    with app.app_context():
        _rec = TeacherAchievement.query.filter_by(title='AUDITP0审计业绩').first()
        if _rec:
            AchievementAttachment.query.filter_by(
                achievement_id=_rec.id).delete(synchronize_session=False)
            TeacherAchievement.query.filter_by(
                id=_rec.id).delete(synchronize_session=False)
            db.session.commit()
    with app.app_context():
        from app.models import OperationLog
        _uids = [u.id for u in User.query.filter(
            User.username.in_(('audit_edit', 'audit_scope'))).all()]
        if _uids:
            UserDataScope.query.filter(
                UserDataScope.user_id.in_(_uids)).delete(synchronize_session=False)
            # 登录等操作日志引用了 users.id（有外键），必须先删，否则用户删不掉
            OperationLog.query.filter(
                OperationLog.user_id.in_(_uids)).delete(synchronize_session=False)
            User.query.filter(User.id.in_(_uids)).delete(synchronize_session=False)
            db.session.commit()      # 先落用户删除，再删权限组（users → permission_groups 有外键）
            PermissionGroup.query.filter(PermissionGroup.name.in_(
                ('AUDITP0仅编辑组', 'AUDITP0范围组'))).delete(synchronize_session=False)
            db.session.commit()


@item('AUDITP1')
def check_audit_p1():
    """全面审计 P1 修复回归（2026-10-13）：

    · M-3 宿舍导入错误日志补 `dormitory.import` 权限（原只需登录即可下载学号/姓名明细）
    · H-2 跨库写事务边界：
        - 学生导入改两阶段提交 + 补偿（宿舍库失败 → 回退本次插入的学生行）
        - 考务名单多库写入显式 try/rollback + 报错
        - 毕业归档：主库 commit 失败时按水位撤销本次历史归档
    """
    from app.models import Student, PermissionGroup, OperationLog
    import app.modules.system.routes.students as stu_mod

    # —— M-3：无宿舍权限的账号下载导入错误日志 → 403 ——
    with app.app_context():
        pg = PermissionGroup.query.filter_by(name='AUDITP1无宿舍权限组').first()
        if not pg:
            pg = PermissionGroup(name='AUDITP1无宿舍权限组', scope_type='none',
                                 role='staff', description='审计用例：无宿舍权限')
            db.session.add(pg)
            db.session.flush()
            pg.set_menu_keys(['workbench.notifications_view'])
        u = User.query.filter_by(username='audit_noperm').first()
        if not u:
            u = User(username='audit_noperm', real_name='审计无权限', role='staff',
                     permission_group_id=pg.id, must_change_pwd=False)
            u.set_password(TEST_PWD)
            db.session.add(u)
        db.session.commit()

    with app.test_client() as c:
        login(c, 'audit_noperm', TEST_PWD)
        r = c.get('/statistics/download-errors/deadbeef')
        case('审计P1：无 dormitory.import 下载宿舍错误日志被拒（403）',
             r.status_code == 403, str(r.status_code))
        c.get('/logout')
        login(c, 'ac_admin', TEST_PWD)
        r = c.get('/statistics/download-errors/deadbeef')
        case('审计P1：有权限账号不受影响（不 403）',
             r.status_code != 403, str(r.status_code))

    # —— H-2①：学生导入两阶段提交 + 补偿（模拟宿舍库提交失败）——
    NEW_NO = 'AUDITP1H2'
    with app.app_context():
        Student.query.filter_by(student_number=NEW_NO).delete(synchronize_session=False)
        db.session.commit()
        stu = Student(student_number=NEW_NO, name='审计H2学生', gender='男',
                      grade='2099级', class_name='01班')
        payload = [(stu, {'boarding_type': '住校', 'day_student_type': '',
                          'textbook': '', 'teacher_notes': ''})]
        calls = {'n': 0}
        real_commit = db.session.commit

        def _flaky():
            calls['n'] += 1
            if calls['n'] == 2:          # ①主库成功，②宿舍库炸
                raise RuntimeError('模拟宿舍库提交失败')
            return real_commit()

        db.session.commit = _flaky
        raised = False
        try:
            stu_mod._save_new_students(payload)
        except RuntimeError:
            raised = True
        finally:
            db.session.commit = real_commit
        case('审计P1：H-2 宿舍库失败时导入整体报错（不静默半成功）', raised, 'raised')
        case('审计P1：H-2 已插入的学生行被补偿回退（不留半成品）',
             Student.query.filter_by(student_number=NEW_NO).count() == 0, 'rolled back')

    # —— H-2②③：静态确认另两处也加了事务边界 ——
    gm = open(os.path.join(BASE, 'app/modules/system/routes/grade_mgmt.py'),
              encoding='utf-8').read()
    ea = open(os.path.join(BASE, 'app/modules/grades/routes/exam_affairs.py'),
              encoding='utf-8').read()
    case('审计P1：H-2 毕业归档有水位补偿（_undo_history_archive）',
         '_undo_history_archive' in gm and '_history_watermark' in gm, 'grade_mgmt')
    case('审计P1：H-2 考务多库写有 try/rollback + 报错',
         '已回滚' in ea and 'db.session.rollback()' in ea, 'exam_affairs')

    # 清理
    with app.app_context():
        Student.query.filter_by(student_number=NEW_NO).delete(synchronize_session=False)
        u = User.query.filter_by(username='audit_noperm').first()
        if u:
            OperationLog.query.filter_by(user_id=u.id).delete(synchronize_session=False)
            db.session.delete(u)
        db.session.commit()
        pg = PermissionGroup.query.filter_by(name='AUDITP1无宿舍权限组').first()
        if pg and not User.query.filter_by(permission_group_id=pg.id).first():
            db.session.delete(pg)
        db.session.commit()


@item('AUDITP2')
def check_audit_p2_upload_guard():
    """全面审计 M-6（2026-10-13）：7 处 Excel 上传统一走 `upload_guard`。

    活体验证成绩导入入口（旧实现直接读流交给 openpyxl）：
      ① `.html` 后缀 → 被白名单/危险类型拒绝；
      ② 伪装成 `.xlsx` 但内容不是 zip 的文件 → 被 magic 嗅探拒绝；
      ③ 真 xlsx → 不误伤（不出现 guard 的拒绝文案）。
    另做静态断言：7 个入口都必须调用 validate_upload，防止回退成"只看后缀"。
    """
    import io as _io
    import openpyxl
    from datetime import date as _date
    from app.models.grades import Exam

    with app.app_context():
        exam = Exam.query.filter_by(name='AUDITP2上传考试').first()
        if not exam:
            exam = Exam(name='AUDITP2上传考试', grade='2099级',
                        exam_date=_date(2026, 6, 1), exam_type='AUDITP2')
            db.session.add(exam)
            db.session.commit()
        exam_id = exam.id

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        csrf = get_csrf(c, '/grades/exams')
        url = f'/grades/exams/{exam_id}/import/upload'
        base = {'csrf_token': csrf, 'mode': 'A', 'remove_missing': '0'}

        r = c.post(url, data=dict(base, file=(_io.BytesIO(b'<html>x</html>'), 'evil.html')),
                   content_type='multipart/form-data', follow_redirects=True)
        html = r.get_data(as_text=True)
        case('审计P2：.html 后缀上传被拒（危险类型）',
             r.status_code == 200 and ('不允许上传 .html' in html
                                       or '不允许的文件类型' in html), 'html')
        r = c.post(url, data=dict(base, file=(_io.BytesIO(b'<script>x</script>'),
                                              'fake.xlsx')),
                   content_type='multipart/form-data', follow_redirects=True)
        html = r.get_data(as_text=True)
        case('审计P2：伪装 .xlsx（内容非 zip）被 magic 嗅探拒绝',
             r.status_code == 200 and '文件内容与' in html, 'magic')

        buf = _io.BytesIO()
        wb = openpyxl.Workbook()
        wb.active['A1'] = '学号'
        wb.save(buf)
        buf.seek(0)
        r = c.post(url, data=dict(base, file=(buf, 'ok.xlsx')),
                   content_type='multipart/form-data', follow_redirects=True)
        html = r.get_data(as_text=True)
        case('审计P2：真 xlsx 不被上传校验误伤',
             r.status_code == 200 and '文件内容与' not in html
             and '不允许的文件类型' not in html, 'control')
        c.get('/logout')

    guarded = []
    for rel in ('app/modules/grades/routes/exams.py',
                'app/modules/academic/routes/timetable.py',
                'app/modules/academic/routes/teachers.py',
                'app/modules/academic/routes/schedule.py',
                'app/modules/grades/routes/teachers.py',
                'app/modules/dormitory/routes/dashboard.py',
                'app/modules/dormitory/routes/statistics.py'):
        with open(os.path.join(BASE, rel.replace('/', os.sep)), encoding='utf-8') as f:
            if 'validate_upload' not in f.read():
                guarded.append(rel)
    case('审计P2：7 处上传入口全部接入 upload_guard（无遗漏）',
         not guarded, '; '.join(guarded))

    with app.app_context():
        Exam.query.filter_by(name='AUDITP2上传考试').delete(synchronize_session=False)
        db.session.commit()


@item('PORTRAITLIST')
def check_portrait_list_filter():
    """学生画像列表过滤（2026-10-13 修复）：**学籍为空（NULL）的学生必须可见**。

    原实现 `enrollment_status.notin_(['已转出','离校'])` 在 SQL 三值逻辑下
    `NULL NOT IN (...)` 结果为 NULL → 整行被剔除（主库 2114 人中 1419 人学籍为空，
    列表只剩 695 人，表现为"管理员只能看部分学生"）；且排除名单取值与字典不符
    （学籍实际值是「学籍已转出」，「已转出/离校」是班级取值）。本用例锁死修复口径。
    """
    from app.models import Student
    # 独立年级，避免分页（20/页）把样本挤出第一页
    GRADE = '2099级'
    with app.app_context():
        for num, name, cls, status in (('PL000N', '画像空学籍', '01班', None),
                                       ('PL000C', '画像班级转出', '已转出', None),
                                       ('PL000X', '画像学籍转出', '已转出', '学籍已转出')):
            if not Student.query.filter_by(student_number=num).first():
                db.session.add(Student(student_number=num, name=name, gender='男',
                                       grade=GRADE, class_name=cls,
                                       enrollment_status=status))
        db.session.commit()

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get('/portrait/', query_string={'grade': GRADE})
        html = r.get_data(as_text=True) if r.status_code == 200 else ''
        case('画像列表：管理员打开 200', r.status_code == 200, str(r.status_code))
        case('画像列表：学籍为空的学生可见（NULL 不再被整行剔除）',
             '画像空学籍' in html, 'null-status')
        m = re.search(r'共\s*(\d+)\s*名学生', html)
        case('画像列表：总数只算有效学生（学籍空 1 人 + 学籍已转出/班级已转出排除）',
             bool(m) and m.group(1) == '1', m.group(0) if m else 'no count')
        case('画像列表：学籍已转出的学生排除', '画像学籍转出' not in html, 'enroll-out')
        case('画像列表：班级已转出（学籍为空）的学生排除', '画像班级转出' not in html, 'class-out')

    with app.app_context():   # 清理样本
        Student.query.filter(Student.student_number.in_(
            ['PL000N', 'PL000C', 'PL000X'])).delete(synchronize_session=False)
        db.session.commit()


@item('PERIODLABEL')
def check_period_label():
    """节次显示口径（2026-10-13 用户实测）：`period_number` 只是**行号**（1=早读），
    展示必须用 `period_defs.period_name` —— 教师工作台「今日课程」原先直接印 number，
    于是「第7节」的课显示成「第 8 节」（整节次错一位）。"""
    from app.utils.period_label import period_label, period_names

    defaults = {p['period_number']: p['period_name'] for p in get_default_periods()}

    with app.app_context():
        case('节次显示名：period_label(8) 用 PeriodDef 名（不是「第8节」）',
             period_label(8, TS_ID) == defaults[8],
             '%s / 期望 %s' % (period_label(8, TS_ID), defaults[8]))
        case('节次显示名：1 号位是早读（按名显示）',
             period_label(1, TS_ID) == defaults[1], str(period_label(1, TS_ID)))
        case('节次显示名：无定义回落「第N节」、0 / 空值返回空串',
             period_label(99, TS_ID) == '第99节' and period_label(0, TS_ID) == '',
             '%s / %s' % (period_label(99, TS_ID), period_label(0, TS_ID)))
        case('节次显示名：period_names 返回完整映射（供前端 JS 用）',
             period_names(TS_ID).get(8) == defaults[8],
             str(len(period_names(TS_ID))))

        # 端到端：造一位教师 + 今天第 8 号位（=第7节）的课，看工作台卡片怎么显示
        u = User.query.filter_by(username='pl_teacher').first()
        if not u:
            u = User(username='pl_teacher', real_name='节次测试教师', role='teacher',
                     must_change_pwd=False)
            u.set_password(TEST_PWD)
            db.session.add(u)
            db.session.flush()
        t = Teacher.query.filter_by(teacher_uid='TPL001').first()
        if not t:
            t = Teacher(teacher_uid='TPL001', name='节次测试教师', subject='信息技术',
                        status='active', user_id=u.id)
            db.session.add(t)
            db.session.commit()
        uid = u.id
        today_wd = date.today().isoweekday()
        ent = ScheduleEntry.query.filter_by(term_schedule_id=TS_ID, teacher_uid='TPL001',
                                            weekday=today_wd, period_number=8).first()
        if not ent:
            ent = ScheduleEntry(term_schedule_id=TS_ID, grade='高一', class_name='09班',
                                weekday=today_wd, period_number=8, subject='信息技术',
                                teacher_uid='TPL001', teacher_name='节次测试教师',
                                week_range='1-20', entry_type='normal')
            db.session.add(ent)
            db.session.commit()
        ent_id = ent.id

    with app.test_client() as c:
        login(c, 'pl_teacher', TEST_PWD)
        r = c.get('/workbench/')
        html = r.get_data(as_text=True) if r.status_code == 200 else ''
        case('今日课程：工作台 200', r.status_code == 200, str(r.status_code))
        case('今日课程：第 8 号位显示为 PeriodDef 名（第7节），不再出现「第 8 节」',
             '信息技术' in html and defaults[8] in html and '第 8 节' not in html,
             '期望 %s' % defaults[8])

    with app.app_context():   # 清理
        ScheduleEntry.query.filter_by(id=ent_id).delete()
        db.session.commit()


@item('SWAPCONFIRM')
def check_swap_need_partner_confirm():
    """与同事换上课时间时的「无需对方同意确认」选项：**默认勾选**（不勾选才需要对方答复）。"""
    from app.models.timetable import ScheduleEntry, ScheduleSwap
    from app.modules.academic.services import swap_service

    class _Admin:
        role = 'admin'
        id = 1

        def has_perm(self, k):
            return True

    def _mk(g, cn, wd, pn, subj, tuid, tname):
        e = ScheduleEntry(term_schedule_id=TS_ID, grade=g, class_name=cn,
                          weekday=wd, period_number=pn, subject=subj,
                          teacher_uid=tuid, teacher_name=tname,
                          week_range='1-20', entry_type='normal')
        db.session.add(e)
        return e

    admin = _Admin()
    entry_ids, batch_ids = [], []
    with app.app_context():
        m1 = _mk('高二', '01班', 1, 1, '语文', 'CF0001', '甲老师')
        m2 = _mk('高二', '01班', 2, 1, '语文', 'CF0001', '甲老师')
        p1 = _mk('高二', '02班', 3, 5, '数学', 'CF0002', '乙老师')
        p2 = _mk('高二', '03班', 4, 5, '英语', 'CF0003', '丙老师')
        db.session.commit()
        entry_ids += [m1.id, m2.id, p1.id, p2.id]

        # ① 默认（不传 need_partner_confirm）→ 免确认：直接进教务审批队列
        ok1, msg1, res1 = swap_service.apply_swap_batch(
            applicant_uid='CF0001', applicant_name='甲老师',
            items=[{'entry_id': m1.id, 'new_weekday': 3, 'new_period': 5,
                    'swap_date': date(2026, 11, 11),
                    'partner_entry_id': p1.id, 'swap_class': False}],
            is_permanent=False, reason='免确认回归')
        b1 = res1.get('batch_id')
        batch_ids.append(b1)
        rows1 = ScheduleSwap.query.filter_by(batch_id=b1).all()
        case('免确认（默认）：落库且 partner_status=agreed / need_partner_confirm=False',
             bool(ok1 and res1.get('created') == 1 and rows1
                 and rows1[0].partner_status == 'agreed'
                 and rows1[0].need_partner_confirm is False), f'{msg1} {res1}')
        case('免确认（默认）：可直进教务审批（不被「待对方确认」卡住）',
             bool(rows1) and swap_service.can_review(rows1[0], admin), 'can_review')
        case('对调只换上课时间：swap_class 恒为 False（换班上课已删除）',
             bool(rows1) and rows1[0].swap_time is True
             and rows1[0].swap_class is False,
             f'{[(r.swap_time, r.swap_class) for r in rows1]}')

        # ② 显式勾选「需要对方同意确认」→ pending，未答复前不可审
        ok2, msg2, res2 = swap_service.apply_swap_batch(
            applicant_uid='CF0001', applicant_name='甲老师',
            items=[{'entry_id': m2.id, 'new_weekday': 4, 'new_period': 5,
                    'swap_date': date(2026, 11, 12),
                'partner_entry_id': p2.id, 'swap_class': False,
                'need_partner_confirm': True}],
            is_permanent=False, reason='需确认回归')
        b2 = res2.get('batch_id')
        batch_ids.append(b2)
        r2 = db.session.get(ScheduleSwap, res2.get('rep_id'))
        case('需对方确认：partner_status=pending，未答复前不可审',
             bool(ok2 and r2 and r2.partner_status == 'pending'
                  and not swap_service.can_review(r2, admin)), f'{msg2} {res2}')
        swap_service.respond_exchange(r2.id, 'agree', 1, '丙老师')
        db.session.expire_all()
        r2 = db.session.get(ScheduleSwap, r2.id)
        case('需对方确认：答复同意后进入教务审批队列',
             swap_service.can_review(r2, admin), f'{r2.partner_status}')

        # ── 清理 ──
        for eid in entry_ids:
            e = db.session.get(ScheduleEntry, eid)
            if e:
                db.session.delete(e)
        for t in ScheduleEntry.query.filter(
                ScheduleEntry.note.like('%换课%'), ScheduleEntry.note.isnot(None)).all():
            db.session.delete(t)
        for b in batch_ids:
            for s in ScheduleSwap.query.filter_by(batch_id=b).all():
                db.session.delete(s)
        db.session.commit()


@item('WBCLASSSCH')
def check_workbench_class_schedule():
    """班主任工作台「我的班级课表」：所带班级解析（多班）+ 班级课表取数。"""
    from app.models import User, UserClassLink
    from app.models.timetable import ScheduleEntry
    from app.modules.academic.services import schedule_service
    from app.modules.workbench.routes import _homeroom_classes

    with app.app_context():
        e0 = ScheduleEntry.query.filter_by(term_schedule_id=TS_ID).first()
        case('准备：本学期课表里有可用班级',
             bool(e0 and e0.grade and e0.class_name), f'{e0}')
        if not e0:
            return
        user = User.query.first()
        link = UserClassLink(user_id=user.id, grade=e0.grade,
                             class_name=e0.class_name)
        db.session.add(link)
        db.session.commit()
        try:
            pairs = _homeroom_classes(user)
            case('班主任所带班级：取 user_class_links（一人可管多班）',
                 (e0.grade, e0.class_name) in pairs, f'{pairs}')
        finally:
            db.session.delete(link)
            db.session.commit()
        case('未配置班级时：不臆造班级（返回空）',
             _homeroom_classes(user) == [], f'{_homeroom_classes(user)}')
        view = schedule_service.get_class_view(TS_ID, e0.grade, e0.class_name)
        case('班级课表取数：grid + 条目数（工作台卡片数据源）',
             bool(view.get('grid')) and (view.get('total') or 0) > 0,
             f'total={view.get("total")}')


@item('SWAPCHAIN')
def check_swap_chain():
    """调课：分级审批（经过谁）+ 调休（日期 + 那天周几的课）+ 目标候选检测。"""
    from app.models import PermissionGroup, User
    from app.models.academic import Teacher
    from app.models.timetable import ScheduleSwap

    with app.app_context():
        u = _urls()
        from flask import url_for
        apply_url = url_for('academic.swap_apply')
        g_teacher = PermissionGroup.query.filter_by(name='任课教师组').first()
        g_leader = PermissionGroup.query.filter_by(name='年级长组').first()
        # 跨 app_context 复用：只留 id（ORM 对象出上下文后会 detached）
        g_teacher_id = g_teacher.id if g_teacher else None
        g_leader_id = g_leader.id if g_leader else None
        applier = User(username='sw_teacher', real_name='张语文', role='teacher',
                       permission_group_id=g_teacher.id if g_teacher else None,
                       must_change_pwd=False)
        applier.set_password(TEST_PWD)
        # 年级长组 scope_type='grade'：必须带年级才代表"审本年级"，
        # 否则数据范围为空集（与成绩模块 visible_grades 口径一致，见 2026-10-10 加固）
        leader = User(username='sw_leader', real_name='年级长', role='grade_leader',
                      grade='高一',
                      permission_group_id=g_leader.id if g_leader else None,
                      must_change_pwd=False)
        leader.set_password(TEST_PWD)
        db.session.add_all([applier, leader])
        db.session.commit()
        # 任课教师的数据范围：必须关联任教班级才允许提交（与线上权限规则一致）
        from app.models import UserClassLink
        db.session.add(UserClassLink(user_id=applier.id, grade='高一',
                                     class_name='01班'))
        db.session.commit()
        # 取该教师在该班的一节课（与上面 UserClassLink 的数据范围一致）
        entry = (ScheduleEntry.query.filter_by(term_schedule_id=TS_ID,
                                               teacher_uid='T900001',
                                               grade='高一', class_name='01班')
                 .order_by(ScheduleEntry.period_number).first())
        entry_id = entry.id
        # 目标时段：找一个"本班 + 本人都不冲突"的（周几, 节次）
        from app.modules.academic.services import swap_service as ss
        slots = ss.get_available_slots(TS_ID, entry.grade, entry.class_name,
                                       entry.teacher_uid, exclude_entry_id=entry_id)
        pick = slots[0] if slots else None

    if pick is None:
        case('存在可用目标时段（前置条件）', False, 'no free slot')
        return

    with app.test_client() as c:
        login(c, 'sw_teacher', TEST_PWD)
        token = get_csrf(c, apply_url)
        html = c.get(apply_url).get_data(as_text=True)
        case('申请页展示审批链路（调课审批 → 课表管理）',
             '调课审批' in html and '课表管理' in html, '链路未展示')
        case('申请页有"那天上的是周几的课"', 'srcWeekday' in html and 'tgtWeekday' in html)
        # 调休：日期是明天，但那天上的是"周三"的课（source_weekday=3）
        from datetime import date as _d, timedelta as _td
        tomorrow = _d.today() + _td(days=1)
        c.post(apply_url, data={'csrf_token': token, 'entry_id': entry_id,
                                'source_date': tomorrow.isoformat(),
                                'source_weekday': 3,
                                'swap_date': tomorrow.isoformat(),
                                'new_weekday': pick['weekday'],
                                'new_period': pick['period_number'],
                                'is_permanent': '', 'new_room': '',
                                'reason': '调休测试：周六上周三的课'},
               follow_redirects=False)
        with app.app_context():
            sw = (ScheduleSwap.query.filter_by(applicant_name='张语文')
                  .order_by(ScheduleSwap.id.desc()).first())
            case('申请落库：原课日期 + 那天周几的课（调休）独立记录',
                 bool(sw) and sw.source_date == tomorrow and sw.source_weekday == 3,
                 f'{sw.source_date if sw else None}/{sw.source_weekday if sw else None}')
            case('新建申请停在第一级审批', bool(sw) and sw.status == 'pending'
                 and (sw.approval_step or 0) == 0,
                 f'{sw.status if sw else None}/{sw.approval_step if sw else None}')
            # 数据范围加固（2026-10-10）：外年级的年级长不得审批本年级调课
            from app.modules.academic.services import swap_service as _ss
            other_leader = User(username='sw_leader2', real_name='高二年级长',
                                role='grade_leader', grade='高二',
                                permission_group_id=g_leader_id,
                                must_change_pwd=False)
            other_leader.set_password(TEST_PWD)
            db.session.add(other_leader)
            db.session.commit()
            here_leader = User.query.filter_by(username='sw_leader').first()
            case('本年级年级长可审批本年级调课', _ss.can_review(sw, here_leader))
            case('外年级年级长不能审批本年级调课（数据范围）',
                 not _ss.can_review(sw, other_leader))
            sid = sw.id if sw else 0

    with app.test_client() as c:
        login(c, 'sw_leader', TEST_PWD)
        approve_url = u['insp']  # 占位，下面用真实 URL
        from flask import url_for as _uf
        with app.app_context():
            approve_url = _uf('academic.swap_approve', swap_id=sid)
            detail_url = _uf('academic.swap_detail', swap_id=sid)
        t2 = get_csrf(c, u['home'])
        r = c.post(approve_url, data={'review_note': '年级同意'},
                   headers={'X-CSRFToken': t2})
        d1 = r.get_json() or {}
        case('一级审批（年级长）通过', bool(d1.get('success')), str(d1)[:120])
        with app.app_context():
            sw = db.session.get(ScheduleSwap, sid)
            case('一级通过后仍待下一级（不是直接通过）',
                 sw.status == 'pending' and sw.approval_step == 1,
                 f'{sw.status}/{sw.approval_step}')
            case('审批轨迹记下谁审的', len(sw.approvals()) == 1
                 and sw.approvals()[0]['user_name'] == '年级长', str(sw.approvals())[:120])
        r = c.post(approve_url, data={'review_note': '再审一次'},
                   headers={'X-CSRFToken': t2})
        case('同一级不能重复审批（403）', r.status_code == 403, str(r.status_code))
        detail_html = c.get(detail_url).get_data(as_text=True)
        case('详情页展示审批链路与已审环节',
             '审批链路' in detail_html and '年级长' in detail_html, '链路未渲染')

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        t3 = get_csrf(c, u['insp'])
        r = c.post(approve_url, data={'review_note': '教务同意'},
                   headers={'X-CSRFToken': t3})
        d2 = r.get_json() or {}
        case('终审（课表管理）通过', bool(d2.get('success')), str(d2)[:120])
        with app.app_context():
            sw = db.session.get(ScheduleSwap, sid)
            case('两级都通过 → 状态为已通过（待执行）', sw.status == 'approved',
                 sw.status)
            case('审批轨迹两条（谁 → 谁都留痕）', len(sw.approvals()) == 2,
                 str(len(sw.approvals())))
        # 目标候选接口：返回的每个时段都带可用性标记，冲突的带原因
        slots_url = None
        with app.app_context():
            slots_url = _uf('academic.api_swap_available_slots')
        r = c.get(slots_url, query_string={'entry_id': entry_id})
        js = r.get_json() or {}
        data = js.get('data') or {}
        slist = data.get('slots') or []
        case('目标时段接口返回候选', bool(slist), str(js)[:100])
        case('候选带可用标记，冲突的写明原因',
             all(('available' in s) for s in slist)
             and all(s.get('conflict_desc') for s in slist if not s.get('available')),
             '缺少 available / conflict_desc')

    # ⑤ 放宽口径：教师账号即使没有"任教班级"数据范围，也能为自己任教的课提交申请；
    #    但不是自己的课、又没有班级关联时，仍旧拒绝（没有全放开）。
    with app.app_context():
        t3 = User(username='sw_t3', real_name='王英语', role='teacher',
                  permission_group_id=g_teacher_id, must_change_pwd=False)
        t3.set_password(TEST_PWD)
        db.session.add(t3)
        db.session.commit()
        own = (ScheduleEntry.query.filter_by(term_schedule_id=TS_ID,
                                             teacher_uid='T900003')
               .order_by(ScheduleEntry.period_number).first())
        other = (ScheduleEntry.query.filter_by(term_schedule_id=TS_ID,
                                               teacher_uid='T900002')
                 .order_by(ScheduleEntry.period_number).first())
        s_a = ss.get_available_slots(TS_ID, own.grade, own.class_name,
                                     own.teacher_uid, exclude_entry_id=own.id)
        s_b = ss.get_available_slots(TS_ID, other.grade, other.class_name,
                                     other.teacher_uid, exclude_entry_id=other.id)
        pick_a = s_a[0] if s_a else None
        pick_b = s_b[0] if s_b else None
        own_id, other_id = own.id, other.id
        from flask import url_for as _u2
        apply_url = _u2('academic.swap_apply')

    if not (pick_a and pick_b):
        case('存在可用目标时段（放宽口径前置条件）', False, 'no free slot')
        return

    day = (_d.today() + _td(days=1)).isoformat()
    with app.test_client() as c:
        login(c, 'sw_t3', TEST_PWD)
        token = get_csrf(c, apply_url)
        base = {'csrf_token': token, 'source_date': day, 'swap_date': day,
                'is_permanent': '', 'new_room': '', 'reason': '本人任教的课'}
        c.post(apply_url, data=dict(base, entry_id=own_id,
                                    new_weekday=pick_a['weekday'],
                                    new_period=pick_a['period_number']),
               follow_redirects=False)
        with app.app_context():
            ok_own = ScheduleSwap.query.filter_by(
                applicant_name='王英语', original_entry_id=own_id).count()
        case('无班级关联也能申请"本人任教的课"', ok_own == 1, str(ok_own))

        c.post(apply_url, data=dict(base, entry_id=other_id,
                                    new_weekday=pick_b['weekday'],
                                    new_period=pick_b['period_number']),
               follow_redirects=False)
        with app.app_context():
            ok_other = ScheduleSwap.query.filter_by(
                applicant_name='王英语', original_entry_id=other_id).count()
        case('别人的课 + 无班级关联 → 仍然拒绝（没有全放开）', ok_other == 0,
             str(ok_other))
        with app.app_context():
            for _r in ScheduleSwap.query.filter_by(applicant_name='王英语').all():
                db.session.delete(_r)
            db.session.commit()


@item('SWAPBULK')
def check_swap_bulk_optional_period():
    """统一调课：目标星期/节次可留空（沿用各自原值），三者全空被拒（2026-10-10）。"""
    from app.models.timetable import ScheduleSwap
    from app.modules.academic.services import swap_service

    with app.app_context():
        entry = (ScheduleEntry.query
                 .filter_by(term_schedule_id=TS_ID, grade='高一', class_name='01班',
                            is_deleted=False)
                 .order_by(ScheduleEntry.weekday, ScheduleEntry.period_number).first())
        eid = entry.id
        orig_slot = (f"{swap_service.WEEKDAY_NAMES.get(entry.weekday, '')}"
                     f"第{entry.period_number}节")

        def bulk(**kw):
            params = dict(applicant_uid='T900001', applicant_name='王英语',
                          entry_ids=[eid], new_weekday=None, new_period=None,
                          new_room=None, swap_date=None, is_permanent=True,
                          reason='统一调课用例')
            params.update(kw)
            return swap_service.bulk_apply_swap(**params)

        ok, msg, _ = bulk()
        case('统一调课：星期/节次/教室全空被拒', not ok and '至少' in (msg or ''), msg)
        case('全空被拒时不写库',
             ScheduleSwap.query.filter_by(swap_type='bulk',
                                          original_entry_id=eid).count() == 0)

        ok2, msg2, _ = bulk(new_room='综合楼305')
        case('只换教室（星期/节次留空）可提交', ok2, msg2)
        sw = (ScheduleSwap.query.filter_by(swap_type='bulk', original_entry_id=eid)
              .order_by(ScheduleSwap.id.desc()).first())
        case('留空维度落库为 NULL（执行时沿用原值）',
             bool(sw) and sw.new_weekday is None and sw.new_period is None
             and sw.new_room == '综合楼305',
             str(sw and (sw.new_weekday, sw.new_period, sw.new_room)))
        d = swap_service._decorate_swap(sw) if sw else {}
        case('详情展示实际落点并标注「沿用」',
             bool(sw) and d.get('target_slot') == orig_slot
             and '沿用' in (d.get('target_keep_text') or ''),
             f"{d.get('target_slot')} / {d.get('target_keep_text')}")

        used = {e.period_number for e in ScheduleEntry.query.filter_by(
            term_schedule_id=TS_ID, grade='高一', class_name='01班',
            weekday=entry.weekday, is_deleted=False).all()}
        free = next((p for p in range(1, 9) if p not in used), None)
        if free:
            ok3, msg3, _ = bulk(new_period=free)
            case('只给目标节次（星期留空）不被必填拦截',
                 ok3 or ('至少' not in (msg3 or '')), msg3)

        for _r in ScheduleSwap.query.filter_by(swap_type='bulk',
                                               original_entry_id=eid).all():
            db.session.delete(_r)
        db.session.commit()


@item('ACHEXTRA')
def check_achievement_pdf():
    """业绩库：填表即上传 PDF 附件 + 材料分类 + 按类别的动态字段。"""
    from app.models.academic import AchievementAttachment, TeacherAchievement
    from app.modules.academic.services import achievement_service as ach_svc

    pdf = (b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n'
           b'trailer\n<< /Root 1 0 R >>\n%%EOF')
    with app.app_context():
        u = _urls()
        from flask import url_for
        add_url = url_for('academic.achievements_add')
        t = Teacher.query.filter_by(teacher_uid='T900001').first()
        tuid = t.teacher_uid

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, u['ach'])
        # ① 课题类：带动态字段 + 一份"立项通知书"PDF
        c.post(add_url,
               data={'csrf_token': token, 'teacher_uid': tuid, 'category': 'course',
                     'title': '省级课题《高中数学分层教学》', 'level': '省级',
                     'obtain_date': date.today().isoformat(), 'issuer': '省教科院',
                     'x_course_no': 'KT2026-018', 'x_start_date': '2026-03-01',
                     'x_role': '主持', 'note': '', 'doc_type': 'notice',
                     'files': (io.BytesIO(pdf), '立项通知书.pdf')},
               content_type='multipart/form-data', follow_redirects=True)
        with app.app_context():
            rec = (TeacherAchievement.query
                   .filter_by(title='省级课题《高中数学分层教学》').first())
            case('业绩录入成功', bool(rec))
            extra = rec.extra() if rec else {}
            case('按类别的动态字段已保存（课题编号/立项时间/角色）',
                 extra.get('course_no') == 'KT2026-018'
                 and extra.get('start_date') == '2026-03-01'
                 and extra.get('role') == '主持', str(extra))
            atts = ach_svc.list_attachments(rec) if rec else []
            case('填表时上传的 PDF 已保存', len(atts) == 1 and atts[0].ext == 'pdf',
                 str([a.ext for a in atts]))
            case('附件带材料分类（立项通知书）',
                 bool(atts) and atts[0].doc_type == 'notice',
                 str(atts[0].doc_type if atts else None))
            rid = rec.id if rec else 0

        # ② 非 PDF 一律拒绝（同一条业绩再传 txt）
        token = get_csrf(c, u['ach'])
        c.post(add_url,
               data={'csrf_token': token, 'teacher_uid': tuid, 'category': 'other',
                     'title': '附非PDF测试', 'level': '', 'obtain_date': '',
                     'issuer': '', 'note': '', 'doc_type': 'other',
                     'files': (io.BytesIO(b'plain text'), '说明.txt')},
               content_type='multipart/form-data', follow_redirects=True)
        with app.app_context():
            rec2 = TeacherAchievement.query.filter_by(title='附非PDF测试').first()
            case('非 PDF 附件被拒绝（业绩仍录入，附件不落库）',
                 bool(rec2) and len(ach_svc.list_attachments(rec2)) == 0,
                 str(len(ach_svc.list_attachments(rec2)) if rec2 else -1))
            rid2 = rec2.id if rec2 else 0
        # ③ 详情接口能拿到动态字段与附件分类
        with app.app_context():
            from flask import url_for
            detail_url = url_for('academic.achievement_detail', aid=rid)
        js = c.get(detail_url).get_json() or {}
        d = js.get('data') or {}
        case('详情返回动态字段（供抽屉展示）',
             any(p.get('label') == '课题编号' for p in (d.get('extra_pairs') or [])),
             str(d.get('extra_pairs'))[:120])
        case('详情返回附件材料分类',
             any(a.get('doc_type_text') for a in (d.get('attachments') or [])),
             str(d.get('attachments'))[:120])
        # ③b 编辑业绩（2026-10-10 新增功能）：改信息 + 动态字段 + 标签
        with app.app_context():
            from flask import url_for as _u3
            edit_url = _u3('academic.achievements_edit', aid=rid)
        token = get_csrf(c, u['ach'])
        c.post(edit_url, data={'csrf_token': token, 'title': '省级课题《分层教学》',
                               'category': 'course', 'level': '国家级',
                               'obtain_date': '2026-05-01', 'issuer': '省教科院',
                               'x_course_no': 'KT2026-019', 'x_role': '参与',
                               'tags': '课题立项,教学成果', 'note': '已编辑'},
               follow_redirects=True)
        with app.app_context():
            rec3 = db.session.get(TeacherAchievement, rid)
            case('编辑业绩：标题 / 级别 / 获得时间已更新',
                 bool(rec3) and rec3.title == '省级课题《分层教学》'
                 and rec3.level == '国家级'
                 and (rec3.obtain_date.isoformat() if rec3.obtain_date else '') == '2026-05-01',
                 f'{rec3.title if rec3 else None}/{rec3.level if rec3 else None}')
            case('编辑业绩：按类别的动态字段被覆盖更新',
                 bool(rec3) and rec3.extra().get('course_no') == 'KT2026-019'
                 and rec3.extra().get('role') == '参与',
                 str(rec3.extra() if rec3 else None))
            case('编辑业绩：标签已更新',
                 bool(rec3) and '教学成果' in (rec3.tags or ''),
                 (rec3.tags if rec3 else '')[:60])

        # ④ PDF-only 白名单
        case('附件白名单已统一为 PDF', ach_svc.ALLOWED_EXTS == ['pdf'],
             str(ach_svc.ALLOWED_EXTS))
        # 清理本项造的业绩与附件：其它用例按"库里业绩条数/第一条"断言，不能污染
        with app.app_context():
            for _rid in (rid, rid2):
                if not _rid:
                    continue
                _rec = db.session.get(TeacherAchievement, _rid)
                if not _rec:
                    continue
                for _a in ach_svc.list_attachments(_rec):
                    ach_svc.delete_attachment(_a)
                db.session.delete(_rec)
            db.session.commit()


@item('SCOPE')
def check_teaching_scope():
    """统一班级 / 任课数据源：班级档案启用标记 + 成绩管理任课映射 + 课表兜底。"""
    from app.models import ClassProfile
    from app.modules.academic.services import swap_service as _ss
    from app.modules.academic.services import teaching_scope_service as ts

    with app.app_context():
        pairs = ts.active_class_pairs()
        case('启用班级取自班级档案',
             ('高一', '01班') in pairs and ('高一', '02班') in pairs, str(pairs))
        # 停用某个班 → 不进启用清单（下拉不再塞几百个历史班）
        cp = ClassProfile.query.filter_by(grade='高一', class_name='02班').first()
        cp.is_active = False
        db.session.commit()
        case('停用后不在启用清单', ('高一', '02班') not in ts.active_class_pairs())
        cand = ts.class_candidates(TS_ID)
        case('课表里有课的班仍被兜底保留（不丢班）',
             '02班' in cand['grade_classes'].get('高一', []), str(cand)[:120])
        cp.is_active = True
        db.session.commit()
        # 教师任课：成绩管理无映射时回退课表
        scope = ts.teacher_teaching_scope('T900001', TS_ID)
        case('教师任课范围（无映射时回退课表）',
             any(g == '高一' and c == '01班' for g, c, _s in scope), str(scope)[:120])
        teachers = ts.teachers_of_class('高一', '01班', TS_ID)
        case('班级任课教师（回退课表）',
             any(t[0] == 'T900001' and t[1] == '张语文' for t in teachers),
             str(teachers)[:120])
        opts = _ss.get_class_options(TS_ID)
        case('调课班级选项结构完整（grades + grade_classes）',
             'grades' in opts and 'grade_classes' in opts and '高一' in opts['grades'],
             str(opts)[:120])


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


@item('FORM2ACH')
def check_form_to_achievement():
    """表单收集 → 教师业绩库闭环（2026-10-10）：

    一次收集 = 一轮（可发起多轮）；审核通过按模板上配好的映射自动为每个教师
    生成一条业绩；业绩库可按来源/轮次筛选，详情能回看该次提交的答案与附件。
    """
    from flask import url_for

    from werkzeug.security import generate_password_hash

    from app.models.academic import FormRound
    from app.modules.academic.services import (achievement_service as ach_svc,
                                              form_service)

    def _submit(tpl_id, uid, name):
        """模拟教师提交：submitter_uid 故意传 users.id（历史 bug 形态）。"""
        return form_service.submit_form(
            tpl_id,
            {'submitter_type': 'teacher', 'submitter_id': uid,
             'submitter_name': name, 'submitter_uid': str(uid)},
            {}, {})

    with app.app_context():
        admin = User.query.filter_by(username='ac_admin').first()
        t = Teacher(teacher_uid='TFORM01', name='表单教师', status='active',
                    user_id=admin.id)
        db.session.add(t)
        db.session.commit()
        tuid, tname, admin_id = t.teacher_uid, t.name, admin.id

        # 1) 发起一次收集：开启计入业绩（类别=课题 / 级别=校级 / 名称=模板标题）
        tpl = form_service.create_form(
            title='课题立项材料收集', description='', category=None,
            target_type='teachers', target_scope=None, start_time=None,
            deadline=None, max_file_size=10, allow_multiple=False,
            questions_data=[{'question_type': 'text', 'title': '课题名称',
                             'required': True}],
            created_by=admin_id,
            ach={'to_achievement': True, 'ach_category': 'course',
                 'ach_level': '校级', 'ach_title_mode': 'template',
                 'ach_tags': '课题,省级'})
        form_service.publish_form(tpl.id)
        tpl_id = tpl.id

        rounds = form_service.list_rounds(tpl_id)
        case('发布即开启第 1 轮', len(rounds) == 1 and rounds[0].round_no == 1,
             str([r.round_no for r in rounds]))
        r1_id = rounds[0].id

        sub = _submit(tpl_id, admin_id, tname)
        case('提交者 uid 归一到教师名单（不再存 users.id）',
             sub.submitter_uid == tuid, f'{sub.submitter_uid} != {tuid}')
        case('提交归属当前轮次', sub.round_id == r1_id)

        # 2) 审核通过 → 自动入账
        # 2026-10-19 第十三轮审计：审核人不得是提交人本人（新增规则），
        # 这里临时建一个审核人账号代替原来的 admin 自审。
        reviewer = User(username='qa_form_reviewer', real_name='表单审核人',
                        role='staff', permission_group_id=1, is_active=True,
                        password_hash=generate_password_hash('Qa#2026'))
        db.session.add(reviewer)
        db.session.commit()
        reviewer_id = reviewer.id
        form_service.review_submission(sub.id, 'approved', reviewer_id, '材料齐全')
        rec = TeacherAchievement.query.filter_by(source_type='form',
                                                 source_id=sub.id).first()
        case('审核通过自动生成业绩', rec is not None)
        case('业绩字段取自模板映射',
             bool(rec) and rec.category == 'course' and rec.level == '校级'
             and rec.title == '课题立项材料收集' and rec.teacher_uid == tuid,
             f'{rec.category if rec else None}/{rec.level if rec else None}')
        case('入账即已通过（不再走二次审核）',
             bool(rec) and rec.status == 'approved')
        case('来源标注所属轮次',
             bool(rec) and '第1轮' in (rec.source_label or ''),
             (rec.source_label if rec else ''))
        case('标签来自模板', bool(rec) and rec.tags and '课题' in rec.tags,
             (rec.tags if rec else ''))

        # 3) 幂等：重复入账不产生第二条
        rec2, msg = ach_svc.create_from_submission(sub, reviewer_id=admin_id)
        same = TeacherAchievement.query.filter_by(source_type='form',
                                                  source_id=sub.id).count()
        case('重复入账幂等（不产生重复业绩）',
             bool(rec2) and rec2.id == rec.id and same == 1, f'{msg}/{same}')

        # 4) 发起新一轮：上一轮自动结束，同一教师可以再交
        r2 = form_service.start_new_round(tpl_id, name='第二学期',
                                          created_by=admin_id)
        r1 = db.session.get(FormRound, r1_id)
        case('发起新一轮且上一轮自动结束',
             r2.round_no == 2 and r1.status == 'closed',
             f'{r2.round_no}/{r1.status}')
        sub2 = _submit(tpl_id, admin_id, tname)
        case('跨轮次可再次提交（不再被判重复）', sub2.round_id == r2.id)
        form_service.review_submission(sub2.id, 'approved', reviewer_id, '')
        case('新一轮提交同样入账',
             TeacherAchievement.query.filter_by(source_round_id=r2.id).count() == 1)

        # 5) 未开启计入业绩的收集：审核通过也不写业绩库
        tpl2 = form_service.create_form(
            title='培训需求调查', description='', category=None,
            target_type='teachers', target_scope=None, start_time=None,
            deadline=None, max_file_size=10, allow_multiple=False,
            questions_data=[{'question_type': 'text', 'title': '需求'}],
            created_by=admin_id, ach={'to_achievement': False})
        form_service.publish_form(tpl2.id)
        sub3 = _submit(tpl2.id, admin_id, tname)
        form_service.review_submission(sub3.id, 'approved', reviewer_id, '')
        case('未开启计入的收集不写业绩',
             TeacherAchievement.query.filter_by(source_type='form',
                                                source_id=sub3.id).count() == 0)

        # 6) 驳回后本轮可重交（重交产生新提交，原记录保留审核意见）
        tpl3 = form_service.create_form(
            title='荣誉证书收集', description='', category=None,
            target_type='teachers', target_scope=None, start_time=None,
            deadline=None, max_file_size=10, allow_multiple=False,
            questions_data=[{'question_type': 'text', 'title': '荣誉名称'}],
            created_by=admin_id,
            ach={'to_achievement': True, 'ach_category': 'honor'})
        form_service.publish_form(tpl3.id)
        sub4 = _submit(tpl3.id, admin_id, tname)
        form_service.review_submission(sub4.id, 'rejected', reviewer_id, '材料不全')
        el_r = form_service.check_eligibility(tpl3.id, admin_id, 'teacher')
        case('被驳回后可重交（资格检查放行）',
             el_r['eligible'] and not el_r['already_submitted'],
             str(el_r.get('reason')))
        sub5 = _submit(tpl3.id, admin_id, tname)
        case('重交生成新提交且归本轮',
             sub5.id != sub4.id and sub5.round_id == sub4.round_id)

        # 7) 限时收集：未开始不可交 / 已截止不可交 / 轮次时间窗优先于模板
        from datetime import datetime as _dt, timedelta as _td
        _now = _dt.now()
        tpl4 = form_service.create_form(
            title='限时材料收集', description='', category=None,
            target_type='teachers', target_scope=None,
            start_time=_now + _td(days=1), deadline=None, max_file_size=10,
            allow_multiple=False,
            questions_data=[{'question_type': 'text', 'title': '材料'}],
            created_by=admin_id, ach={'to_achievement': False})
        form_service.publish_form(tpl4.id)
        el4 = form_service.check_eligibility(tpl4.id, admin_id, 'teacher')
        case('未开始的收集不可填写（reason=before）', el4['reason'] == 'before',
             str(el4.get('reason')))
        case('未开始的收集在填写列表标注「未开始」',
             any(it['tpl'].id == tpl4.id and it['state'] == 'before'
                 for it in form_service.fill_items(admin_id, 'teacher')))
        try:
            _submit(tpl4.id, admin_id, tname)
            case('未开始提交被拒', False, '未抛错')
        except ValueError as e:
            case('未开始提交被拒', '尚未开始' in str(e), str(e))

        r4 = form_service.list_rounds(tpl4.id)[0]
        form_service.update_round_window(r4.id, start_time=_now - _td(hours=1))
        el4b = form_service.check_eligibility(tpl4.id, admin_id, 'teacher')
        case('轮次时间窗覆盖模板（调整后即可交）', el4b['eligible'] is True,
             str(el4b.get('reason')))

        form_service.update_round_window(r4.id, deadline=_now - _td(hours=1))
        el5 = form_service.check_eligibility(tpl4.id, admin_id, 'teacher')
        case('已截止的收集不可填写（reason=expired）', el5['reason'] == 'expired',
             str(el5.get('reason')))
        case('已截止的收集不出现在填写列表',
             all(it['tpl'].id != tpl4.id
                 for it in form_service.fill_items(admin_id, 'teacher')))
        form_service.update_round_window(r4.id, deadline=_now + _td(days=3))
        case('延长截止后恢复可填',
             form_service.check_eligibility(tpl4.id, admin_id, 'teacher')['eligible'] is True)

        # 8) 关闭收集 → 重新开放（沿用原轮次 / 作为新一轮）
        form_service.close_round(r4.id)
        try:
            form_service.update_round_window(r4.id, deadline=_now + _td(days=1))
            case('已结束轮次不可直接调整时间', False, '未抛错')
        except ValueError as e:
            case('已结束轮次不可直接调整时间', '先重新开放' in str(e), str(e))
        form_service.reopen_round(r4.id, deadline=_now + _td(days=1))
        case('重新开放已结束轮次', db.session.get(FormRound, r4.id).status == 'open')
        form_service.close_round(r4.id)
        form_service.close_form(tpl4.id)
        case('收集可关闭（模板状态）',
             form_service.get_form_detail(tpl4.id).status == 'closed')
        rnd_re = form_service.reopen_form(tpl4.id, deadline=_now + _td(days=2))
        case('重新开放（沿用原轮次）同时放开模板',
             rnd_re.id == r4.id
             and form_service.get_form_detail(tpl4.id).status == 'open'
             and rnd_re.status == 'open')
        rnd_new = form_service.reopen_form(tpl4.id, mode='new', round_name='补充轮',
                                           deadline=_now + _td(days=5))
        case('重开为新一轮：轮号递增且上一轮自动结束',
             rnd_new.round_no == r4.round_no + 1
             and db.session.get(FormRound, r4.id).status == 'closed')

        # 9) 定向学科收集：只对指定学科教师开放（可见性与提交双重拦截）
        db.session.add(Teacher(teacher_uid='TFORM02', name='数学教师',
                               subject='数学', status='active'))
        db.session.add(Teacher(teacher_uid='TFORM03', name='语文教师',
                               subject='语文', status='active'))
        db.session.commit()
        tpl5 = form_service.create_form(
            title='数学组材料收集', description='', category=None,
            target_type='subject', target_scope='{"subjects": ["数学"]}',
            start_time=None, deadline=None, max_file_size=10,
            allow_multiple=False,
            questions_data=[{'question_type': 'text', 'title': '材料'}],
            created_by=admin_id, ach={'to_achievement': False})
        form_service.publish_form(tpl5.id)
        case('定向学科：数学教师可见',
             any(i['tpl'].id == tpl5.id
                 for i in form_service.fill_items(admin_id, 'teacher',
                                                  submitter_subject='数学')))
        case('定向学科：其它学科教师不可见',
             all(i['tpl'].id != tpl5.id
                 for i in form_service.fill_items(admin_id, 'teacher',
                                                  submitter_subject='语文')))
        try:
            form_service.submit_form(
                tpl5.id,
                {'submitter_type': 'teacher', 'submitter_id': admin_id,
                 'submitter_name': tname, 'submitter_uid': tuid,
                 'submitter_subject': '语文'}, {}, {})
            case('定向学科：范围外提交被拒', False, '未抛错')
        except ValueError as e:
            case('定向学科：范围外提交被拒', '不在本次收集范围内' in str(e), str(e))

        rec_id = rec.id if rec else 0
        r2_id = r2.id
        tpl4_id = tpl4.id
        ach_url = url_for('academic.achievements_page')
        detail_url = url_for('academic.achievement_detail', aid=rec_id)
        create_url = url_for('academic.form_create')
        subs_url = url_for('academic.form_submissions', form_id=tpl_id)

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        # 页面能渲染（防止模板变量/语法错误导致 500）
        case('表单创建页含「计入教师业绩库」配置区',
             '计入教师业绩库' in c.get(create_url).get_data(as_text=True))
        subs_html = c.get(subs_url).get_data(as_text=True)
        case('提交列表页含轮次切换与发起新一轮',
             'roundSelect' in subs_html and 'newRoundModal' in subs_html)
        html = c.get(ach_url, query_string={'source': 'form'}).get_data(as_text=True)
        case('业绩库按来源筛选（只看表单收集）',
             '课题立项材料收集' in html and '第1轮' in html)
        html2 = c.get(ach_url, query_string={'round': r2_id}).get_data(as_text=True)
        case('业绩库按轮次筛选（第 2 轮）', '第二学期' in html2)
        js = (c.get(detail_url).get_json() or {}).get('data') or {}
        case('业绩详情带来源与「该次提交」回看',
             js.get('source_type') == 'form'
             and (js.get('submission') or {}).get('items') is not None)
        # 2026-10-10 新增：收集侧轮次操作区 / 重开入口 / 业绩库「发起收集」闭环
        detail_html = c.get(subs_url).get_data(as_text=True)
        case('提交列表页含轮次操作区（收集轮次/调整时间）',
             '收集轮次' in detail_html and '调整时间' in detail_html)
        list_html = c.get(url_for('academic.form_list')).get_data(as_text=True)
        case('表单列表含重新开放弹窗与当前轮时间窗', 'reopenFormModal' in list_html)
        fill_html = c.get(url_for('academic.form_fill_list')).get_data(as_text=True)
        case('填写列表按本轮呈现（已提交徽章）', '已提交' in fill_html)
        ach_html = c.get(ach_url).get_data(as_text=True)
        case('业绩库含「发起收集」与进行中收集区',
             '发起收集' in ach_html and '进行中的业绩收集' in ach_html,
             '发起收集' if '发起收集' not in ach_html else '进行中的业绩收集 缺失')
        # 按轮口径的三个页面渲染（汇总/未交/填写）
        summary_html = c.get(url_for('academic.form_summary_page',
                                     form_id=tpl_id)).get_data(as_text=True)
        case('汇总看板按本轮口径渲染', '本轮：' in summary_html)
        miss_html = c.get(url_for('academic.form_missing_page',
                                  form_id=tpl_id)).get_data(as_text=True)
        case('未交名单页按本轮口径渲染', '本轮未提交名单' in miss_html)
        fill_page = c.get(url_for('academic.form_fill',
                                  form_id=tpl4_id)).get_data(as_text=True)
        case('填写页渲染当前轮时间窗', '提交答案' in fill_page and '第2轮' in fill_page,
             str(len(fill_page)))


@item('FORMHTTP')
def check_form_submit_http():
    """表单提交的 **HTTP 路由** 回归（2026-10-12 新增）。

    背景：上面的 FORM2ACH 直接调 `form_service.submit_form`，绕过了 HTTP 路由，
    于是 `forms.py` 里 `current_user.name`（User 模型无该字段）这类**只在路由层
    才触发**的缺陷从未被覆盖——该 bug 会让提交表单直接 500。本项走真实路由。
    """
    from flask import url_for

    from app.models.academic import FormSubmission
    from app.modules.academic.services import form_service

    with app.app_context():
        admin = User.query.filter_by(username='ac_admin').first()
        tpl = form_service.create_form(
            title='HTTP 提交回归', description='', category=None,
            target_type='all', target_scope=None, start_time=None,
            deadline=None, max_file_size=10, allow_multiple=False,
            questions_data=[{'question_type': 'text', 'title': '说明',
                             'required': False}], created_by=admin.id)
        form_service.publish_form(tpl.id)
        tpl_id = tpl.id
        qid = tpl.questions[0].id if tpl.questions else None

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, url_for('academic.form_fill', form_id=tpl_id))
        r = c.post(url_for('academic.form_submit', form_id=tpl_id),
                   data={'csrf_token': token, f'q_{qid}': 'HTTP 提交内容'})
        case('HTTP 提交表单不报 500（路由层字段正确）',
             r.status_code != 500, f'HTTP {r.status_code}')

    with app.app_context():
        sub = FormSubmission.query.filter_by(template_id=tpl_id).first()
        case('HTTP 提交已落库且提交人姓名取自 real_name',
             sub is not None and sub.submitter_name == '教务主任',
             f'submitter_name={sub.submitter_name if sub else None}')


@item('FORM_NOTIFY')
def check_form_notify():
    """收集通知闭环（2026-10-10）：

    发布收集 / 发起新一轮 → 应填教师收到通知（站内相对链接、材料收集分类）；
    审核通过 / 驳回 → 提交人收到通知（含原因与重交入口）；
    催交个人化、按轮归属、未读不重复推送、无账号教师明确提示；
    通知中心可按「材料收集」筛选，并可把通知从自己的收件箱移除。
    """
    from flask import url_for
    from werkzeug.security import generate_password_hash
    from app.models.notification import (Notification, NotificationRecipient,
                                         CATEGORY_LABELS)
    from app.modules.academic.services import form_service, form_summary_service

    with app.app_context():
        admin = User.query.filter_by(username='ac_admin').first()
        if not Teacher.query.filter_by(teacher_uid='TNT01').first():
            db.session.add(Teacher(teacher_uid='TNT01', name='通知教师甲',
                                   status='active', user_id=admin.id))
        if not Teacher.query.filter_by(teacher_uid='TNT02').first():
            db.session.add(Teacher(teacher_uid='TNT02', name='通知教师乙',
                                   status='active'))
        db.session.commit()

        tpl = form_service.create_form(
            title='通知联动材料收集', description='请提交证明材料', category=None,
            target_type='teachers', target_scope=None, start_time=None,
            deadline=None, max_file_size=10, allow_multiple=False,
            questions_data=[{'question_type': 'text', 'title': '材料名称',
                             'required': True}],
            created_by=admin.id,
            ach={'to_achievement': True, 'ach_category': 'honor'})
        tpl_id = tpl.id

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, '/academic/forms')
        r = c.post(url_for('academic.form_publish', form_id=tpl_id),
                   data={'csrf_token': token}, follow_redirects=True)
        page = r.get_data(as_text=True)
        case('发布收集后提示已通知应填人员', '已通知' in page, page[-160:])
        case('无登录账号的教师被提示线下告知', '无登录账号' in page)

    with app.app_context():
        admin = User.query.filter_by(username='ac_admin').first()  # 重新绑定会话
        n_open = (Notification.query.filter_by(biz_type='form_open', biz_id=tpl_id)
                  .order_by(Notification.id.desc()).first())
        open_rows = (NotificationRecipient.query
                     .filter_by(notification_id=(n_open.id if n_open else 0)).all())
        open_ids = {x.user_id for x in open_rows}
        case('发布通知覆盖应填教师（有账号投账号、无账号投名单）',
             n_open is not None and admin.id in open_ids and any(i < 0 for i in open_ids),
             f'{len(open_rows)} 人 / {sorted(open_ids)[:6]}')
        case('发布通知归入「材料收集」分类且链接为站内相对路径',
             n_open is not None and n_open.category == 'collect'
             and (n_open.link_url or '').startswith('/academic/forms/'),
             f'{getattr(n_open, "category", None)}|{getattr(n_open, "link_url", None)}')
        case('分类字典含「材料收集」', CATEGORY_LABELS.get('collect') == '材料收集')

        # 甲：提交 → 审核通过 → 收到通知（含入账说明）
        # 2026-10-19 第十三轮审计：审核人不能是提交人本人，另建一个审核账号
        _rv = User.query.filter_by(username='qa_notice_reviewer').first()
        if _rv is None:
            _rv = User(username='qa_notice_reviewer', real_name='通知审核人',
                       role='staff', permission_group_id=1, is_active=True,
                       password_hash=generate_password_hash('Qa#2026'))
            db.session.add(_rv)
            db.session.commit()
        sub = form_service.submit_form(
            tpl_id, {'submitter_type': 'teacher', 'submitter_id': admin.id,
                     'submitter_name': '通知教师甲', 'submitter_uid': 'TNT01'}, {}, {})
        form_service.review_submission(sub.id, 'approved', _rv.id, '材料齐全')
        n_ok = Notification.query.filter_by(biz_type='form_review',
                                            biz_id=sub.id).first()
        case('审核通过后通知提交人且写明已入账',
             n_ok is not None and '通过' in (n_ok.title or '')
             and '业绩库' in (n_ok.content or ''),
             (n_ok.content if n_ok else '无通知'))

        # 乙（无账号）：提交 → 驳回 → 收到通知，且计入「未交（待重交）」
        sub2 = form_service.submit_form(
            tpl_id, {'submitter_type': 'teacher', 'submitter_id': 999999,
                     'submitter_name': '通知教师乙', 'submitter_uid': 'TNT02'}, {}, {})
        form_service.review_submission(sub2.id, 'rejected', admin.id,
                                       '证书扫描件不清晰')
        n_rej = Notification.query.filter_by(biz_type='form_review',
                                             biz_id=sub2.id).first()
        case('驳回后通知提交人（含原因与重新提交提示）',
             n_rej is not None and '不清晰' in (n_rej.content or '')
             and '重新提交' in (n_rej.content or ''),
             (n_rej.content if n_rej else '无通知'))
        missing_uids = {m['uid'] for m in form_summary_service.get_all_missing(tpl_id)}
        case('被驳回的人计入未交（需重交）', 'TNT02' in missing_uids,
             str(sorted(missing_uids))[:160])

        # 催交：按轮归属 + 个人化 + 未读去重 + 无账号提示
        ok, msg, info = form_summary_service.remind_submitters(
            tpl_id, all_missing=True, operator=admin)
        rnd_now = form_service.current_round(form_service.get_form_detail(tpl_id))
        n_rem = (Notification.query.filter_by(biz_type='form_remind',
                                              biz_id=rnd_now.id)
                 .order_by(Notification.id.desc()).first())
        rem_rows = (NotificationRecipient.query
                    .filter_by(notification_id=(n_rem.id if n_rem else 0)).all())
        rem_uids = {x.user_uid for x in rem_rows}
        case('催交通知按轮次归属（biz_id=轮次 id）', n_rem is not None and ok, msg)
        case('催交覆盖被驳回待重交的教师', 'TNT02' in rem_uids, str(sorted(rem_uids))[:160])
        case('催交文案个人化（不列他人名单）',
             n_rem is not None and '通知教师甲' not in (n_rem.content or '')
             and '通知教师乙' not in (n_rem.content or ''))
        # 注：service 层测试无请求上下文，Flask 会用 SERVER_NAME 生成绝对地址；
        # 真实请求内（教务点催交）生成的是站内相对路径，这里只校验指向填写页。
        case('催交链接指向站内填写页',
             n_rem is not None and '/academic/forms/' in (n_rem.link_url or ''),
             str(getattr(n_rem, 'link_url', None)))
        case('无账号收件人计数随通知结果返回', info.get('accountless', 0) >= 1,
             str(info.get('accountless')))
        ok2, msg2, info2 = form_summary_service.remind_submitters(
            tpl_id, all_missing=True, operator=admin)
        case('同一轮次未读催交不重复推送',
             (not ok2) and info2.get('skipped', 0) >= 1, msg2)

        nid = n_ok.id if n_ok else (n_open.id if n_open else 0)
        rnd_id = rnd_now.id

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        token = get_csrf(c, '/academic/forms')
        r2 = c.post(url_for('academic.form_round_new', form_id=tpl_id),
                    data={'csrf_token': token, 'name': '第二学期'},
                    follow_redirects=True)
        case('发起新一轮后再次通知应填人员', '已通知' in r2.get_data(as_text=True))
        nlist = c.get(url_for('notifications.notifications_page'),
                      query_string={'category': 'collect'}).get_data(as_text=True)
        case('通知中心可按「材料收集」筛选',
             '材料收集' in nlist and '通知联动材料收集' in nlist)
        case('通知中心提供「从我的收件箱移除」入口', 'removeNotification' in nlist)
        cnt0 = (c.get(url_for('notifications.unread_count_api')).get_json()
                or {}).get('count')
        rr = c.post(url_for('notifications.notification_delete_mine',
                            notification_id=nid), data={'csrf_token': token})
        body = rr.get_json() or {}
        case('可从我的收件箱移除通知',
             rr.status_code == 200 and body.get('ok') is True, str(rr.status_code))
        cnt1 = (c.get(url_for('notifications.unread_count_api')).get_json()
                or {}).get('count')
        case('移除后未读数下降', (cnt1 or 0) < (cnt0 or 0), f'{cnt0} -> {cnt1}')

        # 路由层通知路径：结束本轮 → 重新开放（提醒未交者）；延长截止（提醒未交者）
        c.post(url_for('academic.form_round_close', round_id=rnd_id),
               data={'csrf_token': token})
        rr2 = c.post(url_for('academic.form_round_reopen', round_id=rnd_id),
                     data={'csrf_token': token, 'deadline': '2030-01-01T00:00'},
                     follow_redirects=True)
        case('重新开放轮次后提醒本轮未交者',
             rr2.status_code == 200 and '已通知' in rr2.get_data(as_text=True))
        rr3 = c.post(url_for('academic.form_round_window', round_id=rnd_id),
                     data={'csrf_token': token, 'deadline': '2030-06-01T00:00'},
                     follow_redirects=True)
        case('延长截止时间后提醒本轮未交者',
             rr3.status_code == 200 and '已通知' in rr3.get_data(as_text=True))

        # 用户实测报障回归：通知中心「全部已读」曾因表单缺 CSRF token 直接 400
        r_all = c.post(url_for('notifications.notification_read_all'),
                       data={'csrf_token': token}, follow_redirects=True)
        case('全部已读不再报 400', r_all.status_code == 200, str(r_all.status_code))
        cnt_all = (c.get(url_for('notifications.unread_count_api')).get_json()
                   or {}).get('count')
        case('全部已读后未读数归零', cnt_all == 0, str(cnt_all))


# ── 入口 ────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
# 2026-10-12 用户报障三件套：清空学期课表 / 导入识别精准度 / 任课映射核对
# ══════════════════════════════════════════════════════════════════════════════
@item('FIX1012')
def check_clear_parse_mapping_1012():
    """① 学期管理页有「清空课表」入口（输入学期名二次确认，软删除留痕）；
    ② 解析修复：合写学科（地政）+ 顿号多教师、一格两门课各自带周次；
    ③ 任课映射核对：只提示不改映射，无映射/映射是别人/姓氏缩写三种结论。
    """
    import io as _io

    from flask import url_for
    from openpyxl import Workbook

    from app.models.grades import TeacherSubjectLink
    from app.modules.academic.services import schedule_matrix_import as mi
    from app.modules.academic.services import schedule_service as svc

    # ── ① 解析：合写学科 + 顿号多教师（原来只留第一位教师）──────────────
    items = mi.parse_cell_content('地政\n孟科岐、陈丽莉')
    case('解析：合写「地政」+ 顿号双师 → 两科两师一一配对',
         len(items) == 2
         and {(i['subject'], i['teacher_name']) for i in items}
         == {('地理', '孟科岐'), ('政治', '陈丽莉')}, str(items))
    case('解析：完整学科词不被拆（生物 ≠ 生 + 物）',
         mi.parse_cell_content('生物\n张语文')[0]['subject'] == '生物')

    # ── ② 解析：空行分隔的一格两门课（原来丢第二条 + 周次串味）──────────
    two = mi.parse_cell_content('信息技术(双周)\n张真\n\n心理(单周)\n冯阳')
    case('解析：一格两门课（空行分隔）不再丢第二条', len(two) == 2, str(two))
    if len(two) == 2:
        case('解析：两门课各带自己的周次（双周 / 单周，不串味）',
             two[0]['week_range'] == '双周' and two[1]['week_range'] == '单周',
             str([i['week_range'] for i in two]))
        case('解析：第二门学科归一（心理 → 心理健康）',
             two[1]['subject'] == '心理健康', two[1]['subject'])

    # ── ②′ 单双周交替的三种常见写法（2026-10-12 用户报"还有类似的单双周设置课程"）──
    for _txt, _desc in [
        ('信息技术(双周)\n何向前\n\n心理(单周)\n冯阳', '空行分隔'),
        ('语文(单周)\n张三\n数学(双周)\n李四', '无空行分隔'),
        ('信息技术(双周) 心理(单周)\n何向前 冯阳', '两门课挤一行各带标注'),
    ]:
        _r = mi.parse_cell_content(_txt)
        case(f'解析：单双周交替（{_desc}）→ 两门课都在且周次正确',
             len(_r) == 2 and {i['week_range'] for i in _r} == {'单周', '双周'},
             f'{_desc}: {_r}')

    # ②″ 单双周两条同格可以一起入库（周次不重叠，不算班级冲突）
    with app.app_context():
        _ts = svc.create_schedule('回归-单双周', '2099-2100', '第一学期')
        _ok1, _r1 = svc.add_entry(_ts.id, '高一', '01班', 1, 5, '信息技术',
                                  teacher_name='何向前', week_range='双周')
        _ok2, _r2 = svc.add_entry(_ts.id, '高一', '01班', 1, 5, '心理健康',
                                  teacher_name='冯阳', week_range='单周')
        case('单双周：同格两条都能入库（不判班级冲突）', _ok1 and _ok2, f'{_r1} | {_r2}')
        _rows = ScheduleEntry.query.filter_by(term_schedule_id=_ts.id, grade='高一',
                                              class_name='01班', weekday=1,
                                              period_number=5, is_deleted=False).all()
        case('单双周：同格两条都在，周次分别为 单周 / 双周',
             len(_rows) == 2 and {r.week_range for r in _rows} == {'单周', '双周'},
             str([r.week_range for r in _rows]))
        svc.delete_schedule(_ts.id)

    # ── ③ 任课映射核对：只读不改 + 三种结论 ────────────────────────────
    with app.app_context():
        before_links = TeacherSubjectLink.query.count()
        entries = [{'grade': '高一', 'class_name': '01班', 'subject': '语文',
                    'teacher_name': '张语文', 'teacher_uid': None,
                    'weekday': 1, 'period_number': 2}]
        m0 = svc.check_teacher_mapping(entries)
        case('映射核对：主科条目被核对（checked=1）', m0['checked'] == 1, str(m0['checked']))
        case('映射核对：该班该科无映射 → no_link',
             {i['kind'] for i in m0['issues']} == {'no_link'}, str(m0['issues']))
        # 2026-10-15 用户口径「教师映射扩容，增加综合组」：体育等综合组六科**已进映射体系**，
        # 同样参与核对（旧口径是"体育不参与核对"，此处按新口径反向断言）
        _m_sport = svc.check_teacher_mapping([dict(entries[0], subject='体育')])
        case('映射核对：综合组科目（体育）参与核对 → no_link',
             _m_sport['checked'] == 1
             and {i['kind'] for i in _m_sport['issues']} == {'no_link'}, str(_m_sport))
        _m_alias = svc.check_teacher_mapping([dict(entries[0], subject='心理健康')])
        case('映射核对：综合组科名归一后仍参与核对（心理健康→心理）',
             _m_alias['checked'] == 1
             and {i['kind'] for i in _m_alias['issues']} == {'no_link'}, str(_m_alias))
        case('映射核对：非映射科目（自习）仍跳过，不刷噪音',
             svc.check_teacher_mapping([dict(entries[0], subject='自习')])['checked'] == 0)

        # 造映射：高一01班语文 → 临时账号「陈丽莉」（并绑一条教师名单记录，用于测编号替换）
        tmp_user = User(username='t_map1012', real_name='陈丽莉', role='teacher',
                        must_change_pwd=False)
        tmp_user.set_password(TEST_PWD)
        db.session.add(tmp_user)
        db.session.flush()
        tmp_teacher = Teacher(teacher_uid='T910012', name='陈丽莉', subject='语文',
                              status='active', user_id=tmp_user.id)
        db.session.add(tmp_teacher)
        link = TeacherSubjectLink(grade='高一', class_name='01班', subject='语文',
                                  user_id=tmp_user.id, active=True)
        db.session.add(link)
        db.session.commit()
        m1 = svc.check_teacher_mapping(entries)
        case('映射核对：映射的是别人 → mismatch',
             {i['kind'] for i in m1['issues']} == {'mismatch'}, str(m1['issues']))
        m2 = svc.check_teacher_mapping([dict(entries[0], teacher_name='陈')])
        case('映射核对：课表写姓氏 → 疑似（abbrev）而非硬判不一致',
             {i['kind'] for i in m2['issues']} == {'abbrev'}, str(m2['issues']))
        m3 = svc.check_teacher_mapping([dict(entries[0], teacher_name='陈丽莉')])
        case('映射核对：姓名一致 → 无告警', not m3['issues'], str(m3['issues']))

        # 「按映射更正」：只改导入草稿，映射本身不动
        fake = {'sheets': [{'grade': '高一', 'class_name': '01班', 'entries': [
            {'subject': '语文', 'teacher_name': '张语文', 'teacher_uid': 'T900001'},
            {'subject': '数学', 'teacher_name': '李数学', 'teacher_uid': 'T900002'},
        ]}]}
        fix = svc.apply_mapping_fix(fake, '高一', '01班', '语文')
        elist = fake['sheets'][0]['entries']
        case('更正：按映射换成映射那位的姓名与教师编号',
             fix['fixed'] == 1 and elist[0]['teacher_name'] == '陈丽莉'
             and elist[0]['teacher_uid'] == 'T910012', str(fix))
        case('更正：同班其它科目不受影响', elist[1]['teacher_name'] == '李数学')
        case('更正：该班该科无映射时拒绝更正（引导去映射页）',
             svc.apply_mapping_fix(fake, '高一', '01班', '英语')['reason'] == 'no_link')
        case('更正：批量只处理「疑似 / 映射是别人」，不碰 no_link',
             svc.apply_all_mapping_fixes(fake, [
                 {'grade': '高一', 'class_name': '01班', 'subject': '语文',
                  'kind': 'abbrev'},
                 {'grade': '高一', 'class_name': '01班', 'subject': '英语',
                  'kind': 'no_link'},
             ]) == 1)
        db.session.delete(link)
        db.session.delete(tmp_teacher)
        db.session.delete(tmp_user)
        db.session.commit()
        case('映射核对：全程只读（映射条数复原）',
             TeacherSubjectLink.query.count() == before_links)

    # ── ④ 清空课表：入口 + 二次确认 + 软删除 + 留痕（自建临时学期，不动夹具）──
    with app.app_context():
        tmp_ts = svc.create_schedule('回归-清空学期', '2099-2100', '第一学期')
        tid = tmp_ts.id
        svc.add_entry(tid, '高一', '01班', 1, 2, '语文', teacher_name='张语文',
                      week_range='1-20')
        svc.add_entry(tid, '高一', '01班', 1, 3, '数学', teacher_name='李数学',
                      week_range='1-20')
        n0 = ScheduleEntry.query.filter_by(term_schedule_id=tid,
                                           is_deleted=False).count()
        manage_url = url_for('academic.schedule_manage')
        clear_url = url_for('academic.schedule_clear_entries', sid=tid)
    case('清空：临时学期已建 2 条课', n0 == 2, str(n0))

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get(manage_url).get_data(as_text=True)
        case('清空入口：学期管理页有「清空课表」与确认弹窗',
             '清空课表' in html and 'clearEntriesModal' in html)

        token = get_csrf(c, manage_url)
        r1 = c.post(clear_url, data={'csrf_token': token, 'confirm_name': '瞎写的名字'},
                    follow_redirects=True)
        case('清空：确认名不符 → 拒绝并提示',
             '未清空' in r1.get_data(as_text=True))
        with app.app_context():
            case('清空：被拒后条目原样保留',
                 ScheduleEntry.query.filter_by(term_schedule_id=tid,
                                               is_deleted=False).count() == 2)

        token2 = get_csrf(c, manage_url)
        r2 = c.post(clear_url,
                    data={'csrf_token': token2, 'confirm_name': '回归-清空学期'},
                    follow_redirects=True)
        html2 = r2.get_data(as_text=True)
        case('清空：确认名正确 → 执行成功', r2.status_code == 200 and '已清空' in html2,
             str(r2.status_code))
        with app.app_context():
            left = ScheduleEntry.query.filter_by(term_schedule_id=tid,
                                                 is_deleted=False).count()
            case('清空：条目全部软删除（在用 0 条）', left == 0, str(left))
            case('清空：变更历史留痕一条（action=clear）',
                 ScheduleVersion.query.filter_by(term_schedule_id=tid,
                                                 action='clear').count() == 1)
            case('清空：学期本身与作息时间保留',
                 db.session.get(TermSchedule, tid) is not None
                 and PeriodDef.query.filter_by(term_schedule_id=tid).count() > 0)
            svc.delete_schedule(tid)          # 清理临时学期（draft 可物理删）

    # ── ⑤ 预览页渲染核对卡片与「按映射更正」操作入口（HTTP）────────────
    with app.app_context():
        smart_url = url_for('academic.schedule_smart_import', sid=TS_ID)
        fix_url = url_for('academic.schedule_smart_mapping_fix', sid=TS_ID)
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        wb = Workbook()
        ws = wb.active
        ws.title = '高一1班'
        ws.append(['节次', '周一', '周二'])          # 表头需 ≥2 个星期列才会被识别
        ws.append(['第1节', '语文\n张语文', '数学\n李数学'])
        buf = _io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        tk = get_csrf(c, smart_url)
        r = c.post(smart_url, data={'csrf_token': tk, 'file': (buf, 'map.xlsx')},
                   content_type='multipart/form-data')
        html3 = r.get_data(as_text=True)
        case('预览页：渲染「任课教师映射核对」卡片',
             '任课教师映射核对' in html3, str(r.status_code))
        case('预览页：无映射的班科给「去设置映射」入口', '去设置映射' in html3)
        # 注意：无请求上下文时 url_for 生成绝对 URL（http://localhost/...），页面里是相对路径
        case('预览页：更正操作指向 mapping-fix 路由',
             fix_url.replace('http://localhost', '') in html3, fix_url)
        case('预览页：头部有「重新核对」', '重新核对' in html3)


# ══════════════════════════════════════════════════════════════════════════════
# 混合班并行走班（2026-10-12 用户说明：一格俩学科俩老师，两拨学生同时走班）
# ══════════════════════════════════════════════════════════════════════════════
@item('PARA')
def check_parallel_walk_class():
    """「地政\n张葛、时康莉」= 混合班并行课：解析拆两科两师 → 打教学班标记 →
    同格两条都能入库；同名教学班跨班同格不算教师冲突（一次走班课占多班课表）。"""
    import io as _io

    from openpyxl import Workbook

    from app.models.timetable import get_default_periods
    from app.modules.academic.services import schedule_matrix_import as mi
    from app.modules.academic.services import schedule_service as svc

    class _P:
        def __init__(self, d):
            self.period_name = d['period_name']
            self.period_number = d['period_number']
            self.period_type = d.get('period_type')

    # ① 解析：一格拆成两科两师，且带原格标签
    items = mi.parse_cell_content('地政\n张葛、时康莉')
    case('并行走班：一格拆成两科两师', len(items) == 2, str(items))
    case('并行走班：两条都带 parallel_tag（原格标签「地政」）',
         all(i.get('parallel_tag') == '地政' for i in items), str(items))

    # ② 全校总课表导入：组装层自动打「教学班」（地政·地理 / 地政·政治）
    periods = [_P(d) for d in get_default_periods()]
    wb = Workbook()
    ws = wb.active
    ws.title = '全校总课表'
    ws.append(['星期', '节次', '高二1班', '高二2班'])
    ws.append(['星期一', '1节', '地政\n张葛、时康莉', '地政\n张葛、时康莉'])
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    data = mi.parse_school_workbook(buf, periods, ['高二'], [])
    case('并行走班：解析出两个班', len(data['sheets']) == 2, str(len(data['sheets'])))
    entries = [e for sh in data['sheets'] for e in sh['entries']]
    case('并行走班：条目自动带教学班标记（地政·地理 / 地政·政治）',
         {e['teaching_class'] for e in entries} == {'地政·地理', '地政·政治'},
         str([e['teaching_class'] for e in entries]))

    # ③ 写入：同格两条都进（不再被班级冲突拦掉）
    with app.app_context():
        ts = svc.create_schedule('回归-并行走班', '2099-2100', '第一学期')
        tid = ts.id
        res = svc.batch_add_entries(tid, entries)
        case('并行走班：两条同时段并行导入成功（2 班 × 2 条）',
             res['success'] == 4 and res['failed'] == 0, str(res))
        rows = ScheduleEntry.query.filter_by(term_schedule_id=tid, grade='高二',
                                             class_name='01班', weekday=1,
                                             period_number=2, is_deleted=False).all()
        case('并行走班：同班同格两条都在', len(rows) == 2, str(len(rows)))
        case('并行走班：两条各带自己的教学班',
             {r.teaching_class for r in rows} == {'地政·地理', '地政·政治'},
             str([r.teaching_class for r in rows]))

        # ④ 同名教学班跨班同格 → 不算教师冲突（一次走班课占多个班的课表）
        ok1, r1 = svc.add_entry(tid, '高二', '01班', 3, 3, '政治', teacher_uid='T900001',
                                teacher_name='张语文', teaching_class='地政·政治',
                                week_range='1-18')
        ok2, r2 = svc.add_entry(tid, '高二', '02班', 3, 3, '政治', teacher_uid='T900001',
                                teacher_name='张语文', teaching_class='地政·政治',
                                week_range='1-18')
        case('并行走班：同一教学班跨班同格不算教师冲突（两条都进）',
             ok1 and ok2, f'{r1} | {r2}')
        # 反例：不同教学班、同教师同时段 → 仍然要判教师冲突
        ok3, msg3 = svc.add_entry(tid, '高二', '03班', 3, 3, '政治', teacher_uid='T900001',
                                  teacher_name='张语文', teaching_class='地政·政治2',
                                  week_range='1-18')
        case('并行走班：不同教学班同教师同时段仍判冲突',
             not ok3 and '教师冲突' in str(msg3), str(msg3))
        svc.delete_schedule(tid)


# ══════════════════════════════════════════════════════════════════════════════
# 方案 · 历史记录（任课映射 / 学科组长，2026-10-14）
# ══════════════════════════════════════════════════════════════════════════════
@item('PLANMGR')
def check_plan_manager():
    """右上角「历史记录」＝使用历史方案 / 使用其他方案 / 新建方案。

    - 任课映射：方案是 TeacherSubjectLink 的留档，切换＝把该方案物化回主表
      （主表列与语义不变，成绩 / 教务 / 考务等读取点不受影响）；
    - 学科组长：方案＝学年 × 学期，切换只影响页面（登记本身按学期并存）；
    - 当前方案不可删、同一学年学期不可重复建方案。
    """
    from app.models.academic import SubjectLeaderPlan
    from app.models.grades import (TeacherLinkPlan, TeacherLinkPlanRow,
                                   TeacherSubjectLink)

    with app.app_context():
        # 临时库默认没有任课映射（也没有学生班级），这里造两条教师账号 + 三条映射，
        # 让"方案 = 映射留档 / 切换 = 物化回主表"这条链路是真的被验证到。
        t1 = User(username='plan_r1', real_name='方案回归甲', role='teacher',
                  must_change_pwd=False)
        t2 = User(username='plan_r2', real_name='方案回归乙', role='teacher',
                  must_change_pwd=False)
        t1.set_password(TEST_PWD)
        t2.set_password(TEST_PWD)
        db.session.add_all([t1, t2])
        db.session.flush()
        seed_uids = [t1.id, t2.id]
        seeded_cells = [('高一', '01班', '语文', t1.id),
                        ('高一', '01班', '数学', t2.id),
                        ('高二', '02班', '语文', t2.id)]
        for _g, _c, _s, _u in seeded_cells:
            db.session.add(TeacherSubjectLink(grade=_g, class_name=_c, subject=_s,
                                              user_id=_u, active=True))
        db.session.commit()
        base_links = TeacherSubjectLink.query.filter_by(active=True).count()
        base_leaders = SubjectLeader.query.count()

    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        csrf = get_csrf(c, '/academic/leaders')
        hdrs = {'X-CSRFToken': csrf}

        def jpost(url, payload):
            return c.post(url, data=json.dumps(payload), content_type='application/json',
                          headers=hdrs)

        # 页面入口
        html = c.get('/academic/leaders').get_data(as_text=True)
        case('组长页：右上「历史记录」入口存在',
             'id="ldHistory"' in html and 'ldPlanModal' in html)
        case('组长页：方案下拉（学年学期即方案）', 'id="ldPlan"' in html)
        html = c.get('/academic/teacher-links').get_data(as_text=True)
        case('映射页（教务侧）：「历史记录」入口 + 当前方案徽标',
             'id="tcHistory"' in html and 'planModal' in html and '当前方案' in html)
        case('映射页（成绩侧）可访问', c.get('/grades/teachers').status_code == 200)

        # ── 学科组长方案：新建 → 重命名 → 使用 → 删除 ──
        r = jpost('/academic/leaders/plans/create',
                  {'name': '回归·2098-2099学年 第一学期', 'school_year': '2098-2099',
                   'term': '第一学期', 'copy_from': 'blank', 'use_now': False})
        body = r.get_json() or {}
        case('组长：新建方案成功', r.status_code == 200 and body.get('success'), str(body))
        pid = (body.get('data') or {}).get('id')
        r = jpost('/academic/leaders/plans/create',
                  {'school_year': '2098-2099', 'term': '第一学期', 'copy_from': 'blank'})
        case('组长：同一学年学期重复新建被拒（400）', r.status_code == 400, str(r.status_code))
        r = jpost('/academic/leaders/plans/rename', {'plan_id': pid, 'name': '回归·改名后'})
        case('组长：重命名成功', r.status_code == 200 and (r.get_json() or {}).get('success'))
        r = jpost('/academic/leaders/plans/use', {'plan_id': pid})
        j = r.get_json() or {}
        case('组长：使用方案返回页面跳转地址',
             r.status_code == 200
             and '/academic/leaders' in ((j.get('data') or {}).get('redirect') or ''),
             str(j))
        with app.app_context():
            case('组长：切换后当前方案唯一',
                 SubjectLeaderPlan.query.filter_by(is_current=True).count() == 1)
        case('组长：删除方案成功',
             jpost('/academic/leaders/plans/delete', {'plan_id': pid}).status_code == 200)
        with app.app_context():
            case('组长：方案操作不动组长登记数据', SubjectLeader.query.count() == base_leaders)

        # ── 任课映射方案：复制当前 → 切换 → 切回 → 删除 ──
        r = c.get('/academic/teacher-links/plans')
        items = (r.get_json() or {}).get('data', {}).get('items', [])
        case('映射：方案清单可用且条数＝主表有效映射',
             r.status_code == 200 and bool(items) and items[0]['count'] == base_links,
             f'{r.status_code} {items[:1]}')
        cur_id = items[0]['id'] if items else None
        r = jpost('/academic/teacher-links/plans/create',
                  {'name': '回归·映射副本', 'copy_from': 'current', 'use_now': False})
        body = r.get_json() or {}
        pid2 = (body.get('data') or {}).get('id')
        case('映射：新建（复制当前）成功', r.status_code == 200 and body.get('success'),
             str(body))
        r = c.get('/academic/teacher-links/plans')
        row = next((i for i in (r.get_json() or {}).get('data', {}).get('items', [])
                    if i['id'] == pid2), {})
        case('映射：副本条数与主表一致', row.get('count') == base_links, str(row.get('count')))
        case('映射：切换方案成功（物化回主表）',
             jpost('/academic/teacher-links/plans/use',
                   {'plan_id': pid2}).status_code == 200)
        with app.app_context():
            case('映射：切换后主表有效映射数不变',
                 TeacherSubjectLink.query.filter_by(active=True).count() == base_links)
            case('映射：当前方案唯一',
                 TeacherLinkPlan.query.filter_by(is_current=True).count() == 1)
        case('映射：当前生效方案不可删除（400）',
             jpost('/academic/teacher-links/plans/delete',
                   {'plan_id': pid2}).status_code == 400)
        jpost('/academic/teacher-links/plans/use', {'plan_id': cur_id})
        case('映射：删除历史方案成功',
             jpost('/academic/teacher-links/plans/delete',
                   {'plan_id': pid2}).status_code == 200)
        case('映射：删除不存在的方案返回 400',
             jpost('/academic/teacher-links/plans/delete',
                   {'plan_id': pid2}).status_code == 400)

        # 保存矩阵 → 当前方案快照同步（快照必须等于主表有效映射）
        with app.app_context():
            from app.modules.grades.services.scope import _all_active_grades
            g = (_all_active_grades() or [''])[0]
            cid = TeacherLinkPlan.query.filter_by(is_current=True).first().id
        if g:
            r = jpost('/academic/teacher-links/api/save', {'grade': g, 'cells': {}})
            with app.app_context():
                rows = TeacherLinkPlanRow.query.filter_by(plan_id=cid).count()
                act = TeacherSubjectLink.query.filter_by(active=True).count()
            case('映射：保存后当前方案快照与主表一致',
                 r.status_code == 200 and rows == act, f'{rows} vs {act}')

        # 空白方案「立即使用」→ 主表清空；切回原方案 → 完全恢复（历史方案真的可用）
        if base_links:
            r = jpost('/academic/teacher-links/plans/create',
                      {'name': '回归·空白方案', 'copy_from': 'blank', 'use_now': True})
            pid3 = ((r.get_json() or {}).get('data') or {}).get('id')
            with app.app_context():
                emptied = TeacherSubjectLink.query.filter_by(active=True).count()
            case('映射：空白方案启用后主表无有效映射', emptied == 0, str(emptied))
            jpost('/academic/teacher-links/plans/use', {'plan_id': cur_id})
            with app.app_context():
                restored = TeacherSubjectLink.query.filter_by(active=True).count()
            case('映射：切回原方案后主表完全恢复（历史方案可用）',
                 restored == base_links, f'{restored} vs {base_links}')
            jpost('/academic/teacher-links/plans/delete', {'plan_id': pid3})

    # ── 清场：删除本用例造的映射 / 账号 / 方案，避免影响其它项 ──
    with app.app_context():
        for _g, _c, _s, _u in seeded_cells:
            row = (TeacherSubjectLink.query
                   .filter_by(grade=_g, class_name=_c, subject=_s).first())
            if row:
                db.session.delete(row)
        for _uid in seed_uids:
            u = User.query.get(_uid)
            if u:
                db.session.delete(u)
        for p in TeacherLinkPlan.query.filter(TeacherLinkPlan.name.like('回归·%')).all():
            TeacherLinkPlanRow.query.filter_by(plan_id=p.id).delete(synchronize_session=False)
            db.session.delete(p)
        db.session.commit()


@item('AUDITP3')
def check_audit_p3_inspection_swap_kind():
    """查课「单节总览」发起调课必须按**调课**落库（2026-10-14 第三轮审计 SEC-02）。

    修复前：`academic/routes/inspection.py` 的 quick-swap 把 `kind='cover'` 写死，
    用户选「调课(exchange)」也按「代课」落库 —— 对方课条目不校验、`exchange_kind`
    记错、通知文案变成"请你代课"。
    """
    import inspect as _inspect

    from app.modules.academic.services import swap_service

    path = os.path.join(BASE, 'app', 'modules', 'academic', 'routes', 'inspection.py')
    with open(path, encoding='utf-8') as f:
        src = f.read()
    case('AUDITP3 源码：quick-swap 透传已校验的 kind',
         'items=[item], kind=kind, is_permanent=is_permanent' in src)
    case('AUDITP3 源码：不再写死 kind=\'cover\'',
         "items=[item], kind='cover'" not in src)
    # 契约锁：服务端确实按传入 kind 落库（exchange_kind=kind）
    svc_src = _inspect.getsource(swap_service.apply_exchange_batch)
    case('AUDITP3 服务：exchange_kind 记录传入的 kind', 'exchange_kind=kind' in svc_src)


@item('EDUSTAT')
def check_edu_stat():
    """教育统计（2026-10-20）：基表口径一致性 + 页面表格结构 + 路由注册。

    用户口径：**不导出 Excel**，页面表格与样表同格式、复制粘贴即可。
    因此这里锁「口径之间必须自洽」+「7 张表都在页面上且带复制按钮」。
    """
    from app.modules.edustat.services import edu_stat_service as S

    with app.app_context():
        d = S.collect(2026)
        case('EDUSTAT 学年→年级映射（一/二/三/毕业）',
             d['grades'] == {'g1': '2026级', 'g2': '2025级',
                             'g3': '2024级', 'graduated': '2023级'},
             str(d['grades']))
        cs = d['class_size']
        st = d['student_types']
        case('EDUSTAT 2108 班数 = 各档之和',
             cs['class_count'] == sum(cs['total'].values()),
             f"{cs['class_count']} vs {sum(cs['total'].values())}")
        case('EDUSTAT 3118 在校生 = 三个年级之和',
             st['enrolled_total']['total']
             == sum(st['enrolled'][g]['total']
                    for g in (d['grades']['g1'], d['grades']['g2'], d['grades']['g3'])))
        case('EDUSTAT 3118 招生数 = 一年级人数',
             st['new_students'] == st['enrolled'][d['grades']['g1']]['total'])
        case('EDUSTAT 3119 分年龄合计 = 在校生（无缺证件时）',
             d['student_ages']['unknown'] > 0
             or d['student_ages']['totals']['enrolled'] == st['enrolled_total']['total'])
        case('EDUSTAT 4149 教职工总数 = 在职教师数',
             d['teacher_summary']['total'] == d['quality']['teachers_total'])
        case('EDUSTAT 4153 年龄合计 = 有身份证的教师数',
             d['teacher_ages']['totals']['total']
             == d['teacher_summary']['total'] - d['teacher_ages']['unknown'])
        # 4156 行 = 总计 / #女 / #少数民族 / 各学历（与样表行序一致）
        rows = [r[0] for r in d['teacher_by_subject']['rows']]
        case('EDUSTAT 4156 行含总计/#女/#少数民族/学历',
             rows[:3] == ['总计', '#女', '#少数民族']
             and '本科' in rows, str(rows))
        case('EDUSTAT 4156 #女 行合计 = 4153 女教师数',
             d['teacher_by_subject']['rows'][1][1]['合计']
             == d['teacher_ages']['totals']['female'])

        rules = {r.rule for r in app.url_map.iter_rules()}
        case('EDUSTAT 路由已注册（统计页 + 校舍资产）',
             '/system/edu-stat' in rules and '/system/edu-assets' in rules,
             str(sorted(r for r in rules if 'edu' in r)))
        case('EDUSTAT 不再提供 Excel 导出路由（用户口径：复制粘贴）',
             '/system/edu-stat/export' not in rules)

        # 页面结构：7 张表 + 每表一个复制按钮 + eduCopy 脚本
        with open(os.path.join(BASE, 'app', 'templates', 'system',
                               'edu_stat.html'), encoding='utf-8') as f:
            html = f.read()
        tids = ['t2108', 't3118', 't3119', 't3045', 't4149', 't4153', 't4156']
        case('EDUSTAT 页面含 7 张基表', all(('id="%s"' % t) in html for t in tids),
             str([t for t in tids if ('id="%s"' % t) not in html]))
        case('EDUSTAT 每张表都有复制按钮',
             html.count("copy_btn('") >= 7 and 'function eduCopy' in html)
        case('EDUSTAT 3118 表头含合并单元格（在校生数 colspan=3）',
             'colspan="3"' in html and 'rowspan="2"' in html)
        # 2026-10-20 用户隐私口径：只填出生日期即可算年龄，不必收身份证
        from datetime import date as _date

        from app.modules.edustat.services.edu_stat_service import _t_age

        class _TBirth(object):
            birth_date = _date(1990, 5, 1)
            id_card_enc = None

        class _TBoth(object):
            birth_date = _date(1990, 5, 1)
            id_card_enc = None      # 生日优先，身份证缺失也照算

        case('EDUSTAT 只填生日即可算年龄（无需身份证）',
             _t_age(_TBirth(), _date(2026, 9, 1)) == 36,
             str(_t_age(_TBirth(), _date(2026, 9, 1))))
        case('EDUSTAT 年龄按生日优先（身份证为空也不影响）',
             _t_age(_TBoth(), _date(2026, 9, 1)) == 36)

        # 模型与迁移清单一致：校舍资产表按学年唯一
        from app.models import EduStatAsset
        case('EDUSTAT 校舍资产按学年唯一',
             EduStatAsset.__table__.c.school_year.unique is True)
        # 教师页可录入出生日期（教育统计的年龄主口径）
        with open(os.path.join(BASE, 'app', 'templates', 'academic',
                               'teacher_edit.html'), encoding='utf-8') as f:
            tpl = f.read()
        case('EDUSTAT 教师编辑页含出生日期字段', 'name="birth_date"' in tpl)
        case('EDUSTAT 身份证在教师页标为选填', '选填' in tpl)


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

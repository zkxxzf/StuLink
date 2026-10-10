# -*- coding: utf-8 -*-
"""StuLink 教务模块回归测试（课表 / 总课表 / 备课组长 / 任课教师映射 / 工作台 / 名单 / 查课 / 业绩）。

设计同 `tests/sec_regression.py`：临时目录 + 临时 SQLite 库 + create_app，不触碰本地 data/。
按项号运行：`python tests/academic_regression.py SCHED OVERVIEW`（不带参数跑全量）。

覆盖的是 2026-09-25 ~ 10-10 这批教务改造的关键行为：
- 网格视觉（学科配色/吸附/拖拽属性）、拖拽换格接口与冲突拒绝
- 全校总课表（分块/过滤/Excel/分享）
- 任课教师映射：教务侧 /academic/teacher-links 与成绩侧同源（教师-学科关系的唯一依据）
- 任课安排下线核查：/academic/duty* 一律 404（2026-10-10 整功能移除）
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
        'overview_index': url_for('academic.schedule_overview_index'),
        'timetable': url_for('academic.schedule_timetable', sid=TS_ID),
        'timetable_index': url_for('academic.schedule_timetable_index'),
        'smart_import': url_for('academic.schedule_smart_import', sid=TS_ID),
        'smart_template': url_for('academic.schedule_smart_template', sid=TS_ID),
        'achievements': url_for('academic.achievements_page'),
        # 2026-10-10：任课安排（/academic/duty*）已下线 ——
        # 教师-学科关系以「任课教师映射」为唯一依据，故此处不再取 duty 地址
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
        'insp_live': url_for('academic.inspection_live_schedule'),
        'insp_mark': url_for('academic.inspection_mark'),
        'insp_stats': url_for('academic.inspection_stats'),
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
        case('总课表不显示教室（room=1 也不渲染）',
             '101' not in r.get_data(as_text=True))
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
    """2026-10-10：作息表已并入「节次配置」——旧地址重定向，视图内容在节次配置页。"""
    with app.app_context():
        u = _urls()
        from flask import url_for
        periods_url = url_for('academic.schedule_periods', sid=TS_ID)
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        r = c.get(u['timetable'], follow_redirects=False)
        case('旧作息表地址重定向到节次配置', r.status_code in (301, 302)
             and r.headers.get('Location', '').endswith(f'/schedule/{TS_ID}/periods'),
             f"{r.status_code} {r.headers.get('Location')}")
        r = c.get(periods_url)
        html = r.get_data(as_text=True)
        case('节次配置页 200（含作息表视图）', r.status_code == 200, str(r.status_code))
        case('含正课/早读/晚自习分类',
             '正课' in html and '晚自习' in html and '早读' in html)
        case('含时长与统计', '分钟' in html and '小时' in html)
        case('内嵌作息表视图', '作息时间表' in html)
        r = c.get(u['timetable_index'], follow_redirects=False)
        case('作息表入口重定向到节次配置', r.status_code in (301, 302)
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

    # 页面级：节次配置页不再出现「一天最多」字样，且给出全局作息入口
    with app.test_client() as c:
        login(c, 'ac_admin', TEST_PWD)
        html = c.get(periods_url).get_data(as_text=True)
        case('节次配置页不再写「一天最多 13 节」',
             '一天最多' not in html and '数量不限' in html)
        case('节次配置页给出「存为全局作息」入口',
             '/periods/global-save' in html)
        case('节次配置页有「时长」列与 40/45/50 快捷设置',
             'period-dur-sel' in html and '改时长' in html)
        case('节次配置页写明常规时长口径（早读40/正课45/晚自习50）',
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
        case('草稿学期：节次配置页 200（草稿提示条可渲染）',
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
        case('学期管理页：启用中的学期说明"自动归档"机制',
             '切换启用其他学期时自动归档' in html)

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
             ('教务工作台', '待办事项', '教务主流程', '最近动态')))
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
        case('标记改版：页面写明 单击/双击/右键',
             '单击=正常' in html and '双击=迟到' in html and '右键=缺课' in html)
        case('原「标记面板」五大结果按钮已移除', 'data-result=' not in html)

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
        form_service.review_submission(sub.id, 'approved', admin_id, '材料齐全')
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
        form_service.review_submission(sub2.id, 'approved', admin_id, '')
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
        form_service.review_submission(sub3.id, 'approved', admin_id, '')
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
        form_service.review_submission(sub4.id, 'rejected', admin_id, '材料不全')
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


@item('FORM_NOTIFY')
def check_form_notify():
    """收集通知闭环（2026-10-10）：

    发布收集 / 发起新一轮 → 应填教师收到通知（站内相对链接、材料收集分类）；
    审核通过 / 驳回 → 提交人收到通知（含原因与重交入口）；
    催交个人化、按轮归属、未读不重复推送、无账号教师明确提示；
    通知中心可按「材料收集」筛选，并可把通知从自己的收件箱移除。
    """
    from flask import url_for
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
        sub = form_service.submit_form(
            tpl_id, {'submitter_type': 'teacher', 'submitter_id': admin.id,
                     'submitter_name': '通知教师甲', 'submitter_uid': 'TNT01'}, {}, {})
        form_service.review_submission(sub.id, 'approved', admin.id, '材料齐全')
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

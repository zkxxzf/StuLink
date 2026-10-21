# -*- coding: utf-8 -*-
"""限时练冒烟（2026-10-21 审计批次新增）。

覆盖四块：
1. 分析页口径：普通班不去差、卓越班剔末尾 2 人、合计=各班去差后合并；
2. 分析页结构：表头在 `<thead>`（否则「下载图片」取不到表头，导出空图）；
3. 导出：含「不分班」成绩行时不 500（rank_class 缺失兜底）；
4. 仪表盘：入口板 / KPI / 动态都含限时练，只有限时练时 has_data 也为 True；
5. 四个页签 + 学生详情可达。

用法：py -3.12 scripts/smoke/smoke_timed.py
"""
import io
import os
import re
import sys
import tempfile
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_TMP = tempfile.mkdtemp(prefix='stulink_timed_smoke_')

import config as config_mod                                       # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = (
    'sqlite:///' + os.path.join(_TMP, 'system.db'))
_DB = {
    'dormitory': 'dormitory', 'history': 'history', 'grades': 'grades',
    'points': 'points', 'academic': 'academic', 'inspection': 'inspection',
    'achievement': 'achievement', 'forms': 'forms', 'system': 'system',
    'portrait': 'portrait', 'morality': 'morality', 'timed': 'timed',
    'timetable': 'timetable',
}
config_mod.Config.SQLALCHEMY_BINDS = {
    k: 'sqlite:///' + os.path.join(_TMP, v + '.db') for k, v in _DB.items()}
config_mod.Config.SECRET_KEY = 'timed-smoke'

from app import create_app                                        # noqa: E402
from app.extensions import db                                     # noqa: E402
from app.models import ClassProfile, User                         # noqa: E402
from app.models.timed import (DEFAULT_TIMED_LAYERS, TimedBandTemplate,  # noqa: E402
                              TimedExam, TimedScore)
from app.modules.grades.services import timed_analysis, timed_service  # noqa: E402

app = create_app()
OK = FAIL = 0


def check(name, cond, extra=''):
    global OK, FAIL
    if cond:
        OK += 1
        print('[PASS] %s' % name)
    else:
        FAIL += 1
        print('[FAIL] %s  %s' % (name, extra))


def seed():
    """造数：01 班 30 人（51..80 分，普通班）、02 班 6 人（卓越班）、1 条不分班"""
    adm = User(username='timedadm', real_name='限时练冒烟管理员',
               role='admin', must_change_pwd=False)
    adm.set_password('TimedAdm#2026')
    db.session.add(adm)
    db.session.add(ClassProfile(grade='2024级', class_name='02班',
                                class_type='卓越班'))
    exam = TimedExam(name='2024级20260901限时练No.1', grade='2024级',
                     exam_date=date(2026, 9, 1), term='2026-2027',
                     status='imported')
    db.session.add(exam)
    tpl = TimedBandTemplate(name='限时练名次分层', is_builtin=True)
    tpl.set_layers(list(DEFAULT_TIMED_LAYERS))
    db.session.add(tpl)
    db.session.flush()
    exam.template_id = tpl.id
    rows = [('01班', '2401%02d' % i, 51 + i) for i in range(30)]
    rows += [('02班', '2411%02d' % i, s)
             for i, s in enumerate([95, 85, 75, 65, 55, 45])]
    rows.append(('不分班', '2499', 88))
    for cls, no, sc in rows:
        db.session.add(TimedScore(
            exam_id=exam.id, student_no=no, student_name=no, grade='2024级',
            class_name=cls, subject='语文', score=sc))
    # 第二科（数学）：只给 01 班，用来验证「任课教师只出所教科目的列」
    for i in range(30):
        db.session.add(TimedScore(
            exam_id=exam.id, student_no='2401%02d' % i,
            student_name='2401%02d' % i, grade='2024级', class_name='01班',
            subject='数学', score=40 + i))
    db.session.commit()
    timed_service.recalc_subject_ranks(exam.id, '语文')
    timed_service.recalc_subject_ranks(exam.id, '数学')
    db.session.commit()
    return exam, tpl


def check_analysis(exam, tpl):
    a = timed_analysis.subject_analysis(exam, '语文', tpl)
    rows = {r['class_name']: r for r in a['rows']}
    check('不分班 不进班级统计', '不分班' not in rows, list(rows))
    check('01班 普通班不去差（trim_n=0）', rows['01班']['trim_n'] == 0,
          rows['01班']['trim_n'])
    check('01班 去差均分=65.5', rows['01班']['trim_avg'] == 65.5,
          rows['01班']['trim_avg'])
    check('02班 卓越班剔末尾 2 人', rows['02班']['trim_n'] == 2,
          rows['02班']['trim_n'])
    check('02班 去差均分=80.0（去掉 45/55）', rows['02班']['trim_avg'] == 80.0,
          rows['02班']['trim_avg'])
    check('合计 去差均分=67.2（各班去差后合并）',
          a['total']['trim_avg'] == 67.2, a['total']['trim_avg'])
    check('行里不再有 max/min 字段',
          'max' not in rows['01班'] and 'min' not in rows['01班'])


def check_scope(exam, tpl):
    """2026-10-21 权限收口：任课教师只本学科 / 班主任只本班 / 他班明细被拒。

    这是上一轮审计里两个 P1 的回归用例：此前限时练逐生层对任何持
    `grades.view` 的账号全开，任课教师能看到全年级全部科目和总分。
    """
    from app.models import UserClassLink
    from app.models.grades import TeacherSubjectLink

    tea = User(username='timedtea', real_name='限时练任课', role='teacher',
               must_change_pwd=False)
    tea.set_password('TimedTea#2026')
    bzr = User(username='timedbzr', real_name='限时练班主任',
               role='homeroom_teacher', must_change_pwd=False)
    bzr.set_password('TimedBzr#2026')
    db.session.add_all([tea, bzr])
    db.session.flush()
    db.session.add(TeacherSubjectLink(user_id=tea.id, grade='2024级',
                                      subject='语文', class_name='01班',
                                      active=True))
    db.session.add(UserClassLink(user_id=bzr.id, grade='2024级',
                                 class_name='02班'))
    db.session.commit()

    # ---- 任课教师（语文 × 2024级）----
    m = timed_analysis.summary_matrix(exam, '', page=None, user=tea)
    check('任课：可见本年级全部班级（年级×学科口径）',
          {r['class_name'] for r in m['rows']} == {'01班', '02班', '—'},
          {r['class_name'] for r in m['rows']})
    check('任课：表头只有所教科目', m['subjects'] == ['语文'], m['subjects'])
    check('任课：不显示总分列', m['total_visible'] is False)
    check('任课：不显示班级名次', m['rank_c_visible'] is False)
    check('任课：保留总分年级名次', m['rank_g_visible'] is True)
    check('任课：行里没有 total 键', all('total' not in r for r in m['rows']))
    check('任课：行里没有数学分', all('数学' not in r['scores'] for r in m['rows']))

    a = timed_analysis.subject_analysis(exam, '语文', tpl, user=tea)
    check('任课：本学科分析有各班', len(a['rows']) == 2, len(a['rows']))
    a2 = timed_analysis.subject_analysis(exam, '数学', tpl, user=tea)
    check('任课：非任教学科分析为空', a2['rows'] == [], len(a2['rows']))

    d = timed_analysis.student_detail(exam, '240100', user=tea)
    check('任课：学生详情只出所教科', d['subjects'] == ['语文'], d['subjects'])

    tr = timed_analysis.student_trends(exam, user=tea)
    check('任课：T 分走势只出所教科', tr['subjects'] == ['语文'], tr['subjects'])

    # ---- 班主任（02班）----
    m2 = timed_analysis.summary_matrix(exam, '', page=None, user=bzr)
    check('班主任：只看到本班', {r['class_name'] for r in m2['rows']} == {'02班'},
          {r['class_name'] for r in m2['rows']})
    check('班主任：可见总分', m2['total_visible'] is True
          and all('total' in r for r in m2['rows']))
    check('班主任：可见本班名次', m2['rank_c_visible'] is True)
    try:
        timed_analysis.student_detail(exam, '240100', user=bzr)
        check('班主任：他班学生详情被拒', False)
    except PermissionError:
        check('班主任：他班学生详情被拒', True)
    d2 = timed_analysis.student_detail(exam, '241100', user=bzr)
    check('班主任：本班学生详情可看', d2['student_no'] == '241100')

    # ---- 管理员（不裁剪）----
    adm = User.query.filter_by(username='timedadm').first()
    ma = timed_analysis.summary_matrix(exam, '', page=None, user=adm)
    check('管理员：全部班级与全部科目',
          {r['class_name'] for r in ma['rows']} == {'01班', '02班', '—'}
          # 2026-10-21：列固定展示全部 9 科（本场没导入的科也留列，显示「—」）
          and ma['subjects'] == ['语文', '数学', '外语', '物理', '历史',
                                 '化学', '生物', '政治', '地理'],
          (ma['subjects'], {r['class_name'] for r in ma['rows']}))


def login(c):
    r0 = c.get('/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                  r0.get_data(as_text=True))
    return c.post('/login', data={
        'csrf_token': m.group(1) if m else '',
        'username': 'timedadm', 'password': 'TimedAdm#2026'},
        follow_redirects=True)


def check_pages(c, tid):
    r = c.get('/grades/timed/%s?tab=analysis' % tid)
    html = r.get_data(as_text=True)
    check('分析页 200', r.status_code == 200, r.status_code)
    check('分析页无最高分/最低分',
          '最高分' not in html and '最低分' not in html)
    check('分析页有去差均分', '去差均分' in html)
    check('两张表都有 thead（否则下载图片是空图）',
          html.count('<thead>') >= 2, html.count('<thead>'))
    # 2026-10-21 用户口径：一科一张图，卡片头部两个动作（复制 / 下载），
    # 不再按单张表各给一个按钮
    check('一科两个动作按钮且只在卡片头部',
          html.count('data-timed-action') == html.count('card mb-3') * 2,
          '%s/%s' % (html.count('data-timed-action'),
                     html.count('card mb-3')))
    check('复制 + 下载两个动作都在',
          'data-timed-action="copy"' in html and
          'data-timed-action="download"' in html)
    check('整科图片脚本已引入', 'js/timed_tbl_canvas.js' in html)
    check('整科图片元信息指向本科卡片与两张表',
          'data-card="subj1"' in html and 'id="subj1"' in html and
          'data-cnt-table="cntTable1"' in html and
          'data-avg-table="avgTable1"' in html)

    r = c.get('/grades/timed/%s/export.xlsx' % tid)
    check('导出 xlsx 200（含不分班行不 500）',
          r.status_code == 200 and len(r.data) > 2000,
          '%s/%s' % (r.status_code, len(r.data)))

    for tab in ('students', 'trends', 'import', 'analysis'):
        r = c.get('/grades/timed/%s?tab=%s' % (tid, tab))
        check('页签 %s 200' % tab, r.status_code == 200, r.status_code)

    # 2026-10-21：切班级/翻页走局部刷新（?partial=1 只回 #timedPane）
    for tab in ('students', 'trends'):
        r = c.get('/grades/timed/%s?tab=%s&partial=1' % (tid, tab))
        frag = r.get_data(as_text=True)
        check('%s 局部片段 200 且只含面板' % tab,
              r.status_code == 200 and 'id="timedPane"' in frag
              and '<html' not in frag.lower(), r.status_code)
        check('%s 局部片段带切班链接' % tab, 'data-pane-nav' in frag)

    # 2026-10-21：长表往下翻时表头吸顶（JS 克隆 fixed 表头）
    r = c.get('/static/js/timed_pane.js')
    check('timed_pane.js 含吸顶表头逻辑',
          r.status_code == 200 and b'timed-sticky-head' in r.data
          and b'syncSticky' in r.data, r.status_code)
    # 2026-10-21：油猴脚本传 CSV（read_upload 里转 xlsx），走模板导入同一条通路
    imp = c.get('/grades/timed/%s?tab=import' % tid).get_data(as_text=True)
    tk = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', imp)
    csv_bytes = ('考号,姓名,班级,分数\n'
                 '240100,甲,01班,91\n').encode('utf-8-sig')
    r = c.post('/grades/timed/%s/import/upload' % tid,
               data={'csrf_token': tk.group(1) if tk else '',
                     'subject': '语文',
                     'file': (io.BytesIO(csv_bytes), 'zhixue_语文.csv')},
               content_type='multipart/form-data', follow_redirects=True)
    page = r.get_data(as_text=True)
    check('CSV 上传走通模板导入（不报无法读取 Excel）',
          r.status_code == 200 and '无法读取 Excel' not in page, r.status_code)

    r = c.get('/grades/timed/%s/student/240100' % tid)
    check('学生详情 200', r.status_code == 200, r.status_code)
    detail = r.get_data(as_text=True)
    # 2026-10-21 用户口径：逐场成绩按日期倒序、10 条一页
    check('学生详情逐场成绩倒序 + 分页',
          '按日期倒序' in detail and '第 1/' in detail)

    r = c.get('/grades/')
    html = r.get_data(as_text=True)
    check('仪表盘入口板含 /grades/timed', '/grades/timed' in html)
    d = c.get('/grades/api/dashboard').get_json() or {}
    check('只有限时练时 has_data=True', d.get('has_data') is True,
          d.get('has_data'))
    check('动态含限时练源',
          any(x.get('type') == 'timed' for x in (d.get('activity') or [])),
          [x.get('type') for x in (d.get('activity') or [])][:5])
    cards = ((d.get('kpi') or {}).get('stage_cards') or [])
    check('KPI 限时练卡可点',
          any((x.get('url') or '').endswith('/grades/timed') for x in cards),
          cards)


with app.app_context():
    _exam, _tpl = seed()
    check_analysis(_exam, _tpl)
    check_scope(_exam, _tpl)
    _eid = _exam.id

# 注意：test_client 内部会自己推/弹 app context，外层**不要**再 push，
# 否则退出时 ContextVar 错位（RuntimeError: Working outside of application context）
with app.test_client() as _c:
    login(_c)
    check_pages(_c, _eid)

print('\n=== %d passed, %d failed ===' % (OK, FAIL))
sys.exit(1 if FAIL else 0)

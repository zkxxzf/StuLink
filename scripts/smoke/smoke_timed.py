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
    exam = TimedExam(name='2024级 No.1', grade='2024级',
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
    db.session.commit()
    timed_service.recalc_subject_ranks(exam.id, '语文')
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

    r = c.get('/grades/timed/%s/export.xlsx' % tid)
    check('导出 xlsx 200（含不分班行不 500）',
          r.status_code == 200 and len(r.data) > 2000,
          '%s/%s' % (r.status_code, len(r.data)))

    for tab in ('students', 'trends', 'import', 'analysis'):
        r = c.get('/grades/timed/%s?tab=%s' % (tid, tab))
        check('页签 %s 200' % tab, r.status_code == 200, r.status_code)
    r = c.get('/grades/timed/%s/student/240100' % tid)
    check('学生详情 200', r.status_code == 200, r.status_code)

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
    _eid = _exam.id

# 注意：test_client 内部会自己推/弹 app context，外层**不要**再 push，
# 否则退出时 ContextVar 错位（RuntimeError: Working outside of application context）
with app.test_client() as _c:
    login(_c)
    check_pages(_c, _eid)

print('\n=== %d passed, %d failed ===' % (OK, FAIL))
sys.exit(1 if FAIL else 0)

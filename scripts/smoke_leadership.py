# -*- coding: utf-8 -*-
"""领导视图回归冒烟（脚本级）：
- 分科考试：物理方向 01(强基)/02(卓越) + 历史方向 03(卓越) → 去差/小计/总计口径
- 全科考试：无方向 3 班 9 科 → 单组 + 全年级行（无方向小计）
- 方向筛选、未划线报错、Excel 导出、页面与导航入口可达
口径断言按手算值逐项核对（去差剔卓越班总分末尾 2 人、上线按全体参考人数）。
用法：python scripts/smoke_leadership.py
"""
import io
import os
import re
import sys
import tempfile
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_ld_check_')

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
config_mod.Config.SECRET_KEY = 'ld-check-secret'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Student, User  # noqa: E402
from app.models.grades import Exam, ExamScore, ExamBand, TOTAL_SUBJECT  # noqa: E402

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


def mk_scores(exam_id, rows, grade):
    """rows: [(no, name, cls, ctype, dir_, sel, {sub: sc} 或 总分)}"""
    for no, name, cls, ctype, dir_, sel, subs in rows:
        for sub, sc in subs.items():
            db.session.add(ExamScore(
                exam_id=exam_id, student_no=no, student_name=name, grade=grade,
                class_name=cls, class_type=ctype, direction=dir_,
                subject_selection=sel, subject=sub, score=sc))


def mk_bands(exam_id, rows):
    """rows: [(direction, subject, [(seq, name, lower)])]"""
    for direction, subject, bands in rows:
        for seq, name, lower in bands:
            db.session.add(ExamBand(
                exam_id=exam_id, direction=direction, subject=subject, seq=seq,
                name=name, lower_mode='score', lower_value=lower))


with app.app_context():
    # 建表由 create_app() 内部初始化完成（与 smoke_grades 同一模式）
    adm = User(username='ldadm', real_name='领导视图管理员', role='admin',
               must_change_pwd=False)
    adm.set_password('LdAdm#2026')
    db.session.add(adm)

    # ================= 分科考试（2024 级） =================
    ex1 = Exam(name='2026学年第一学期期中考', exam_type='期中考', grade='2024级',
               exam_date=date(2026, 10, 1), term='2026-2027', status='imported')
    db.session.add(ex1)
    db.session.flush()

    # 学生（主库）
    for i, (no, nm, cls, sel) in enumerate([
            ('2401', '物一', '01班', '物化生'), ('2402', '物二', '01班', '物化生'),
            ('2403', '物三', '01班', '物化生'), ('2404', '物四', '01班', '物化生'),
            ('2405', '物五', '02班', '物化生'), ('2406', '物六', '02班', '物化生'),
            ('2407', '物七', '02班', '物化生'), ('2408', '物八', '02班', '物化生'),
            ('2409', '历一', '03班', '史政地'), ('2410', '历二', '03班', '史政地'),
            ('2411', '历三', '03班', '史政地')]):
        db.session.add(Student(student_number=no, name=nm, gender='男' if i % 2 else '女',
                               grade='2024级', class_name=cls, subject_selection=sel))

    # 成绩：01班强基（不去差）、02班卓越（去差剔总分末尾 2 人）、03班卓越（剔 1 人）
    mk_scores(ex1.id, [
        ('2401', '物一', '01班', '强基班', '物理', '物化生',
         {TOTAL_SUBJECT: 600, '语文': 110, '数学': 130, '物理': 90}),
        ('2402', '物二', '01班', '强基班', '物理', '物化生',
         {TOTAL_SUBJECT: 550, '语文': 100, '数学': 120, '物理': 85}),
        ('2403', '物三', '01班', '强基班', '物理', '物化生',
         {TOTAL_SUBJECT: 500, '语文': 90, '数学': 110, '物理': 80}),
        ('2404', '物四', '01班', '强基班', '物理', '物化生',
         {TOTAL_SUBJECT: 450, '语文': 80, '数学': 100, '物理': 75}),
        ('2405', '物五', '02班', '卓越班', '物理', '物化生',
         {TOTAL_SUBJECT: 620, '语文': 130, '数学': 140, '物理': 95}),
        ('2406', '物六', '02班', '卓越班', '物理', '物化生',
         {TOTAL_SUBJECT: 580, '语文': 120, '数学': 130, '物理': 90}),
        ('2407', '物七', '02班', '卓越班', '物理', '物化生',
         {TOTAL_SUBJECT: 520, '语文': 110, '数学': 120, '物理': 85}),
        ('2408', '物八', '02班', '卓越班', '物理', '物化生',
         {TOTAL_SUBJECT: 460, '语文': 100, '数学': 110, '物理': 80}),
        ('2409', '历一', '03班', '卓越班', '历史', '史政地',
         {TOTAL_SUBJECT: 560, '语文': 105, '数学': 115, '历史': 88}),
        ('2410', '历二', '03班', '卓越班', '历史', '史政地',
         {TOTAL_SUBJECT: 520, '语文': 95, '数学': 105, '历史': 82}),
        ('2411', '历三', '03班', '卓越班', '历史', '史政地',
         {TOTAL_SUBJECT: 480, '语文': 85, '数学': 95, '历史': 76}),
    ], '2024级')

    mk_bands(ex1.id, [
        ('物理', TOTAL_SUBJECT, [(1, '特控', 580), (2, '本科', 500)]),
        ('物理', '语文', [(1, '特控', 100), (2, '本科', 90)]),
        ('物理', '数学', [(1, '特控', 120), (2, '本科', 105)]),
        ('物理', '物理', [(1, '特控', 88), (2, '本科', 82)]),
        ('历史', TOTAL_SUBJECT, [(1, '特控', 540), (2, '本科', 470)]),
        ('历史', '语文', [(1, '特控', 100), (2, '本科', 90)]),
        ('历史', '数学', [(1, '特控', 110), (2, '本科', 100)]),
        ('历史', '历史', [(1, '特控', 84), (2, '本科', 78)]),
    ])

    # ================= 全科考试（2025 级，无方向，9 科） =================
    ex2 = Exam(name='2026学年第一学期月考', exam_type='月考', grade='2025级',
               exam_date=date(2026, 9, 20), term='2026-2027', status='imported')
    db.session.add(ex2)
    db.session.flush()

    base9 = {'语文': 100, '数学': 110, '外语': 120, '物理': 70, '化学': 80,
             '生物': 75, '政治': 65, '历史': 72, '地理': 78}
    for i, (no, nm, cls) in enumerate([('2501', '全一', '01班'), ('2502', '全二', '01班'),
                                       ('2503', '全三', '02班'), ('2504', '全四', '02班'),
                                       ('2505', '全五', '03班')]):
        db.session.add(Student(student_number=no, name=nm, gender='男', grade='2025级',
                               class_name=cls, subject_selection='全科'))
        add = 10 if i % 2 else 0
        row = {k: v + add for k, v in base9.items()}
        row[TOTAL_SUBJECT] = sum(row.values())
        mk_scores(ex2.id, [(no, nm, cls, '', '', '全科', row)], '2025级')

    mk_bands(ex2.id, [
        ('', TOTAL_SUBJECT, [(1, '特控', 850), (2, '本科', 700)]),
    ] + [( '', sub, [(1, '特控', v + 5), (2, '本科', v - 10)])
         for sub, v in base9.items()])

    # ================= 未划线考试 =================
    ex3 = Exam(name='未划线考试', exam_type='月考', grade='2025级',
               exam_date=date(2026, 9, 1), term='2026-2027', status='imported')
    db.session.add(ex3)
    db.session.flush()
    mk_scores(ex3.id, [('2501', '全一', '01班', '', '', '全科',
                        {TOTAL_SUBJECT: 700, '语文': 95})], '2025级')

    db.session.commit()
    e1, e2, e3 = ex1.id, ex2.id, ex3.id
    print('== 数据就绪 ==', e1, e2, e3)

    # ================= 服务层直算断言 =================
    from app.modules.grades.services import leadership_service as ld

    d1 = ld.leadership_report(e1)
    check('分科：无 error', 'error' not in d1, str(d1.get('error')))
    check('分科：mode=split', d1.get('mode') == 'split', str(d1.get('mode')))
    check('分科：列=总分+语文/数学/物理/历史并集',
          d1['subjects'] == ['总分', '语文', '数学', '物理', '历史'],
          str(d1['subjects']))
    check('分科：科目数（不含总分）=4', d1['subject_count'] == 4,
          str(d1['subject_count']))
    check('分科：组数=2（物理/历史）', len(d1['groups']) == 2,
          str([g['key'] for g in d1['groups']]))
    check('分科：层名 特控/本科', d1['l1_name'] == '特控' and d1['l2_name'] == '本科',
          f"{d1['l1_name']}/{d1['l2_name']}")

    g_phy, g_his = d1['groups'][0], d1['groups'][1]
    check('物理组 2 个班', len(g_phy['rows']) == 2)
    r01, r02 = g_phy['rows']
    check('01班：班型快照=强基班', r01['class_type'] == '强基班', r01['class_type'])
    check('01班：方向=物理', r01['direction'] == '物理')
    c = r01['cells']
    check('01班 语文 去差均分=95.0（强基不去差）', c['语文']['trim_avg'] == 95.0,
          str(c['语文']['trim_avg']))
    check('01班 语文 特控=2/50%', c['语文']['l1_n'] == 2 and c['语文']['l1_rate'] == 50,
          f"{c['语文']['l1_n']}/{c['语文']['l1_rate']}")
    check('01班 语文 本科=3/75%', c['语文']['l2_n'] == 3 and c['语文']['l2_rate'] == 75,
          f"{c['语文']['l2_n']}/{c['语文']['l2_rate']}")
    check('01班 总分 去差均分=525.0', c[TOTAL_SUBJECT]['trim_avg'] == 525.0,
          str(c[TOTAL_SUBJECT]['trim_avg']))
    check('01班 总分 特控=1/25%（600≥580）',
          c[TOTAL_SUBJECT]['l1_n'] == 1 and c[TOTAL_SUBJECT]['l1_rate'] == 25,
          f"{c[TOTAL_SUBJECT]['l1_n']}/{c[TOTAL_SUBJECT]['l1_rate']}")
    check('01班 历史科 无成绩=—（None）',
          c['历史']['trim_avg'] is None and c['历史']['l1_n'] is None,
          str(c['历史']))

    check('02班：班型快照=卓越班', r02['class_type'] == '卓越班')
    c2 = r02['cells']
    check('02班 语文 去差均分=125.0（剔总分末尾2人后）', c2['语文']['trim_avg'] == 125.0,
          str(c2['语文']['trim_avg']))
    check('02班 语文 特控=4/100%（上线按全体算）',
          c2['语文']['l1_n'] == 4 and c2['语文']['l1_rate'] == 100,
          f"{c2['语文']['l1_n']}/{c2['语文']['l1_rate']}")
    check('02班 总分 去差均分=600.0', c2[TOTAL_SUBJECT]['trim_avg'] == 600.0,
          str(c2[TOTAL_SUBJECT]['trim_avg']))
    check('02班 总分 本科=3/75%（620/580/520 ≥500）',
          c2[TOTAL_SUBJECT]['l2_n'] == 3 and c2[TOTAL_SUBJECT]['l2_rate'] == 75,
          f"{c2[TOTAL_SUBJECT]['l2_n']}/{c2[TOTAL_SUBJECT]['l2_rate']}")

    sub_phy = g_phy['subtotal']
    check('物理小计：标签=物理全年级', sub_phy and sub_phy['class_name'] == '物理全年级',
          str(sub_phy and sub_phy['class_name']))
    check('物理小计：人数=8', sub_phy['count'] == 8, str(sub_phy['count']))
    check('物理小计 总分 去差均分=550.0（合并重算）',
          sub_phy['cells'][TOTAL_SUBJECT]['trim_avg'] == 550.0,
          str(sub_phy['cells'][TOTAL_SUBJECT]['trim_avg']))
    check('物理小计 总分 本科=6/75%（含 520）',
          sub_phy['cells'][TOTAL_SUBJECT]['l2_n'] == 6
          and sub_phy['cells'][TOTAL_SUBJECT]['l2_rate'] == 75,
          f"{sub_phy['cells'][TOTAL_SUBJECT]['l2_n']}/"
          f"{sub_phy['cells'][TOTAL_SUBJECT]['l2_rate']}")

    check('历史组 1 个班', len(g_his['rows']) == 1 and g_his['rows'][0]['class_name'] == '03班')
    r03 = g_his['rows'][0]
    check('03班 历史科 去差均分=88.0（卓越班剔总分末尾2人，仅剩2409）',
          r03['cells']['历史']['trim_avg'] == 88.0,
          str(r03['cells']['历史']['trim_avg']))
    check('03班 历史科 本科=2/67%',
          r03['cells']['历史']['l2_n'] == 2 and r03['cells']['历史']['l2_rate'] == 67,
          f"{r03['cells']['历史']['l2_n']}/{r03['cells']['历史']['l2_rate']}")

    gr = d1['grand']
    check('全年级行存在', gr and gr['class_name'] == '全年级')
    check('全年级：人数=11', gr['count'] == 11, str(gr['count']))
    check('全年级 总分 去差均分=551.4（跨方向合并）',
          gr['cells'][TOTAL_SUBJECT]['trim_avg'] == 551.4,
          str(gr['cells'][TOTAL_SUBJECT]['trim_avg']))
    check('全年级 总分 特控=4/36%（逐生按各自方向线，含卡线580）',
          gr['cells'][TOTAL_SUBJECT]['l1_n'] == 4
          and gr['cells'][TOTAL_SUBJECT]['l1_rate'] == 36,
          f"{gr['cells'][TOTAL_SUBJECT]['l1_n']}/{gr['cells'][TOTAL_SUBJECT]['l1_rate']}")
    check('全年级 总分 本科=9/82%',
          gr['cells'][TOTAL_SUBJECT]['l2_n'] == 9
          and gr['cells'][TOTAL_SUBJECT]['l2_rate'] == 82,
          f"{gr['cells'][TOTAL_SUBJECT]['l2_n']}/{gr['cells'][TOTAL_SUBJECT]['l2_rate']}")

    # 方向筛选
    dp = ld.leadership_report(e1, '物理')
    check('筛选物理：仅 1 组 2 班', len(dp['groups']) == 1 and len(dp['groups'][0]['rows']) == 2)
    check('筛选物理：不出全年级行', dp['grand'] is None)

    # 全科考试
    d2 = ld.leadership_report(e2)
    check('全科：mode=all', d2.get('mode') == 'all', str(d2.get('mode')))
    check('全科：1 组 3 班', len(d2['groups']) == 1 and len(d2['groups'][0]['rows']) == 3)
    check('全科：不出方向小计', d2['groups'][0]['subtotal'] is None)
    check('全科：9 科列 + 总分列', len(d2['subjects']) == 10 and d2['subject_count'] == 9,
          str(d2['subjects']))
    check('全科：有全年级行', d2['grand'] is not None)
    rr01 = d2['groups'][0]['rows'][0]
    check('全科 01班 语文 去差均分=105.0（无卓越去差）',
          rr01['cells']['语文']['trim_avg'] == 105.0,
          str(rr01['cells']['语文']['trim_avg']))
    check('全科 01班 语文 特控=1/50%（105 线，110 过）',
          rr01['cells']['语文']['l1_n'] == 1 and rr01['cells']['语文']['l1_rate'] == 50,
          f"{rr01['cells']['语文']['l1_n']}/{rr01['cells']['语文']['l1_rate']}")
    check('全科 全年级 人数=5', d2['grand']['count'] == 5, str(d2['grand']['count']))

    # 未划线
    d3 = ld.leadership_report(e3)
    check('未划线：返回 error 提示', 'error' in d3 and '划线' in d3['error'],
          str(d3.get('error')))

# ================= HTTP 层断言 =================
with app.test_client() as c:
    html = c.get('/login').get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', html)
    tok = m.group(1) if m else ''
    r = c.post('/login', data={'csrf_token': tok, 'username': 'ldadm',
                               'password': 'LdAdm#2026'}, follow_redirects=False)
    check('管理员登录成功', r.status_code in (301, 302), str(r.status_code))

    rp = c.get('/grades/leadership')
    check('页面 /grades/leadership 200', rp.status_code == 200, str(rp.status_code))
    check('页面含标题「领导视图」', '领导视图' in rp.get_data(as_text=True))

    ra = c.get(f'/grades/api/leadership/report?exam_id={e1}')
    j = ra.get_json() or {}
    check('接口 200 且 success', ra.status_code == 200 and j.get('success') is True,
          f'{ra.status_code} {str(j)[:120]}')
    check('接口返回 groups/grand', bool(j.get('data', {}).get('groups'))
          and bool(j.get('data', {}).get('grand')))

    rn = c.get(f'/grades/api/leadership/report?exam_id={e3}')
    jn = rn.get_json() or {}
    check('未划线考试接口 success=false 且有提示',
          jn.get('success') is False and '划线' in (jn.get('message') or ''),
          str(jn)[:140])

    # 导航入口
    rh = c.get('/')
    check('侧边栏含「领导视图」入口', '领导视图' in rh.get_data(as_text=True))

    # Excel 导出
    rx = c.get(f'/grades/api/leadership/export?exam_id={e1}')
    check('导出 xlsx 200', rx.status_code == 200, str(rx.status_code))
    check('导出 Content-Type xlsx',
          'spreadsheetml' in (rx.headers.get('Content-Type') or ''),
          rx.headers.get('Content-Type') or '')
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(rx.data))
        ws = wb.active
        heads = [ws.cell(row=1, column=i).value for i in range(1, 9)]
        check('导出表头正确（年级/班级/…/总分组）',
              heads[:6] == ['年级', '班级', '班级人数', '方向', '属性', '班主任']
              and heads[6] == '总分', str(heads))
        check('导出含小计行', any(ws.cell(row=r, column=2).value == '物理全年级'
                                 for r in range(1, ws.max_row + 1)))
        check('导出含全年级行', any(ws.cell(row=r, column=2).value == '全年级'
                                   for r in range(1, ws.max_row + 1)))
    except Exception as e:  # noqa: BLE001
        check('导出文件可被 openpyxl 打开', False, str(e))

print(f'\n===== 领导视图验证：{ok} 通过 / {fail} 失败 =====')
sys.exit(1 if fail else 0)

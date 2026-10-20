# -*- coding: utf-8 -*-
"""成绩模块端到端冒烟测试：使用临时目录数据库，不影响本地 data/"""
import io
import os
import sys
import tempfile
import json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

_TMP = tempfile.mkdtemp(prefix='stulink_grades_smoke_')

# 覆写数据目录：隔离测试
import config as config_mod
config_mod.BASE_DIR = _TMP
for k in ('SQLALCHEMY_DATABASE_URI', 'SQLALCHEMY_BINDS'):
    pass
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
    # 2026-10-10：补 system 绑定 —— 启动建表会遍历 'system'，缺它会有 WARN 噪音
    'system': 'sqlite:///' + os.path.join(_TMP, 'system.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
    'timetable': 'sqlite:///' + os.path.join(_TMP, 'timetable.db'),
    'morality': 'sqlite:///' + os.path.join(_TMP, 'morality.db'),
}
# config._get_secret_key 基于 BASE_DIR 读取密钥文件，临时目录下会自动生成
config_mod.Config.SECRET_KEY = 'smoke-test-secret'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Student, User, PermissionGroup, UserClassLink, UserDataScope  # noqa: E402
from app.models.grades import Exam, ExamScore, ExamBand, TeacherSubjectLink, SUBJECTS  # noqa: E402

app = create_app()
ok = 0
fail = 0


def check(name, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'[PASS] {name}')
    else:
        fail += 1
        print(f'[FAIL] {name} {extra}')


with app.app_context():
    # ---- 造基础数据：学生（2025级 01班史政地 02班物化生） ----
    students = []
    for i in range(1, 13):
        if i <= 6:
            stu = Student(student_number=f'202500{i:02d}', name=f'历生{i:02d}',
                          gender='男', grade='2025级', class_name='01班',
                          subject_selection='史政地')
        else:
            stu = Student(student_number=f'202501{i - 6:02d}', name=f'物生{i:02d}',
                          gender='女', grade='2025级', class_name='02班',
                          subject_selection='物化生')
        students.append(stu)
        db.session.add(stu)
    db.session.commit()
    print('== 学生就绪 ==')

with app.app_context():
    # H-1 之后内置 admin 的初始口令是随机生成的（不再有 admin123），
    # 冒烟脚本改为自建一个管理员账号，避免依赖默认口令。
    # 2026-10-12 P0-2：create_app 的 INIT 会灌演示 teacher_subject_links
    # （user_id=1..7），恰好与首个新建账号 id 相撞 → visible_grades 非空、
    # admin 范围用例"假绿"（曾掩盖 visible_classes 空集把 admin 关死的问题）。
    # 冒烟账号必须是**无任何映射的裸 admin**，先清演示链接再建账号。
    TeacherSubjectLink.query.delete()
    UserClassLink.query.delete()
    UserDataScope.query.delete()
    db.session.commit()
    if not User.query.filter_by(username='smokeadm').first():
        _adm = User(username='smokeadm', real_name='冒烟管理员', role='admin',
                    must_change_pwd=False)
        _adm.set_password('SmokeAdm#2026')
        db.session.add(_adm)
        db.session.commit()

    # ---- v1.19.28.0 考务名单用例：上一场（2025001 标记不参考）+ 本场 ----
    from datetime import date as _date
    from app.models.grades import ExamAffair, AffairStudent, AffairRoom
    _prev_af = ExamAffair(name='冒烟上一场', grade='2025级', exam_date=_date(2019, 1, 5),
                          status='arranged')
    _cur_af = ExamAffair(name='冒烟本场', grade='2025级', exam_date=_date(2019, 2, 5),
                         status='draft')
    db.session.add_all([_prev_af, _cur_af])
    db.session.commit()
    db.session.add(AffairStudent(affair_id=_prev_af.id, student_no='20250001',
                                 name='历生01', class_name='01班', is_attend=False))
    # v1.19.49.38 容量用例：上一场 东211=44；本场同房间初始 30
    db.session.add(AffairRoom(affair_id=_prev_af.id, room_no='01', location='东211',
                              subject='默认', capacity=44))
    db.session.add(AffairRoom(affair_id=_cur_af.id, room_no='01', location='东211',
                              subject='默认', capacity=30))
    db.session.commit()
    _AFF_CUR = _cur_af.id

with app.test_client() as c:
    # ---- 登录管理员（先取 CSRF token） ----
    r0 = c.get('/login')
    import re
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
    csrf = m.group(1) if m else ''
    r = c.post('/login', data={'csrf_token': csrf, 'username': 'smokeadm',
                               'password': 'SmokeAdm#2026'}, follow_redirects=True)
    # M-2：登录后会话重建，登录前取的 token 已失效，需重新取
    m2 = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', r.get_data(as_text=True))
    if m2:
        csrf = m2.group(1)
    _html = r.get_data(as_text=True)
    check('admin 登录', r.status_code == 200 and '退出' in _html
          and '登录失败' not in _html, f'csrf={csrf!r} html={_html[:300]!r}')

    # ---- 建考试（名称=年级+日期自动由前端填充；后端接受任意 name） ----
    r = c.post('/grades/exams/new', data={
        'csrf_token': csrf, 'grade': '2025级', 'exam_date': '2026-01-15',
        'name': '2025级2026-01-15', 'exam_type': '期末'}, follow_redirects=True)
    _ce = r.get_data(as_text=True)
    # 说明：原断言检查空列表文案「再导入成绩」，列表非空时反而不含该文案（断言写反了）；
    # 改为断言「考试已创建」提示 + 新考试名出现在考试列表中（与 master 的修正保持一致）。
    check('新建考试', r.status_code == 200 and '考试已创建' in _ce
          and '2025级2026-01-15' in _ce,
          f'status={r.status_code} html={_ce[:200]!r}')

    with app.app_context():
        exam = Exam.query.first()
        check('考试落库', exam is not None and exam.grade == '2025级'
              and exam.name == '2025级2026-01-15')
        eid = exam.id

    # ---- 下载模板 ----
    r = c.get('/grades/template.xlsx')
    check('模板下载', r.status_code == 200 and r.data[:2] == b'PK')

    # ---- 构造成绩 Excel（模板 A 全列，01班史政地、02班物化生） ----
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['学号', '姓名', '年级', '班级', '总分', '语文', '数学', '外语',
               '物理', '历史', '化学', '生物', '政治', '地理'])
    # 01班 历史方向（物理/化学/生物 空）；总分=六科和（历史93+政治88+地理86+语外数）
    hist_rows = [
        ('20250001', '历生01', 612.5, [119.5, 125, 121, None, 93, None, None, 88, 86]),
        ('20250002', '历生02', 590.0, [110, 118, 115, None, 88, None, None, 92, 87]),
        ('20250003', '历生03', 571.5, [108, 110, 112, None, 85, None, None, 80, 76.5]),
        ('20250004', '历生04', 553.0, [102, 105, 108, None, 82, None, None, 78, 78]),
        ('20250005', '历生05', 531.0, [99, 100, 104, None, 78, None, None, 76, 74]),
        ('20250006', '历生06', 512.0, [95, 96, 100, None, 75, None, None, 72, 74]),
    ]
    phys_rows = [
        ('20250101', '物生01', 641.0, [118, 142, 121, 90, None, 88, 82, None, None]),
        ('20250102', '物生02', 610.5, [112, 130, 118, 84, None, 82, 76, None, None]),
        ('20250103', '物生03', 588.0, [108, 124, 115, 79, None, 78, 72, None, None]),
        ('20250104', '物生04', 566.5, [104, 116, 112, 74, None, 74, 68, None, None]),
        ('20250105', '物生05', 541.0, [100, 108, 108, 70, None, 70, 62, None, None]),
        ('20250106', '物生06', 522.0, [96, 102, 104, 68, None, 66, 60, None, None]),
    ]
    for no, name, total, vals in hist_rows + phys_rows:
        ws.append([no, name, '2025级', '01班' if no.startswith('202500') else '02班',
                   total] + vals)
    data_io = io.BytesIO()
    wb.save(data_io)
    data_io.seek(0)

    # ---- 上传解析 ----
    r = c.post(f'/grades/exams/{eid}/import/upload',
               data={'mode': 'A', 'csrf_token': csrf, 'file': (data_io, '期中成绩.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    html = r.get_data(as_text=True)
    check('上传解析报告', r.status_code == 200 and '可导入' in html and '确认导入' in html)

    # ---- 确认导入 ----
    with app.app_context():
        token = Exam.query.get(eid).import_token
    r = c.post(f'/grades/exams/{eid}/import/confirm', data={'csrf_token': csrf, 'token': token},
               follow_redirects=True)
    check('确认导入', r.status_code == 200 and '新增 12 人' in r.get_data(as_text=True))

    with app.app_context():
        n_total = ExamScore.query.filter_by(exam_id=eid, subject='总分').count()
        n_all = ExamScore.query.filter_by(exam_id=eid).count()
        check('成绩行数（12生×7行）', n_total == 12 and n_all == 12 * 7, f'{n_total}/{n_all}')
        top = ExamScore.query.filter_by(exam_id=eid, subject='总分') \
            .order_by(ExamScore.score.desc()).first()
        check('排名第1为最高分', top.student_no == '20250101' and top.rank_dir == 1,
              f'{top.student_no} rank={top.rank_dir}')
        # 历史方向最高
        htop = ExamScore.query.filter_by(exam_id=eid, subject='总分', direction='历史') \
            .order_by(ExamScore.rank_dir).first()
        check('历史方向第1', htop.student_no == '20250001' and htop.rank_dir == 1)
        check('考试状态 imported', Exam.query.get(eid).status == 'imported')

    # ---- 分析 API ----
    r = c.get(f'/grades/api/options')
    check('options API', r.status_code == 200 and r.get_json()['success'])
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    js = r.get_json()
    tables = js['data']['tables']
    check('年级分析 API 表', r.status_code == 200 and 'class_summary' in tables
          and len(tables['class_summary']['rows']) == 2)
    charts = js['data']['charts']
    # 2026-10-19 用户口径：「历次考试总览下面的东西全部删了」——
    # 历次考试总览表 + 其后全部图表（均分柱/箱线/分层率/堆叠/趋势折线）已下线
    check('年级分析 API 已无图表区（历次总览及以下已删）',
          charts == {} and 'trend_overview' not in tables,
          f'charts={sorted(charts)} tables={sorted(tables)}')
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=01%E7%8F%AD')
    js = r.get_json()
    t2 = js['data']['tables']
    # 2026-10-19 用户口径：删除「班级学生历次成绩明细表」；
    # 「班级学生各科成绩表」保留但只出表格（noChart）
    check('班级分析 API', 'class_meta' in t2 and 'student_detail' not in t2
          and len(t2['student_scores']['rows']) == 6
          and t2['student_scores'].get('noChart') is True,
          f"tables={sorted(t2)}")
    r = c.get(f'/grades/api/analysis/subject?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6')
    js = r.get_json()
    check('学科分析 API（含热力）',
          'class_compare' in js['data']['tables'] and 'heatmap' in js['data']['charts'])

    # ---- 班级对比分析（第五 tab） ----
    from urllib.parse import quote
    pair = quote('01班') + ',' + quote('02班')
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}&classes={pair}')
    js = r.get_json()
    ct = js.get('data', {}).get('tables', {}) if js.get('success') else {}
    check('班级对比 API（两班）', r.status_code == 200 and 'cmp_overview' in ct
          and len(ct['cmp_overview']['rows']) == 2, str(r.get_data(as_text=True)[:200]))
    check('班级对比：各班核心指标含极差/分差',
          ct.get('cmp_overview', {}).get('rows') and
          'range' in ct['cmp_overview']['rows'][0] and 'diff' in ct['cmp_overview']['rows'][0])
    # 2026-10-15 口径：及格率/优秀率按本场划线（最高/最低层；四层模板=优秀/及格层），
    # 未划线不出这两张表 → 这里只断言科目横表；划线后的断言见「分层模板应用」之后
    check('班级对比：科目横表 + 图表规格（未划线时不出划线率表）',
          'cmp_subject' in ct and bool(ct['cmp_subject'].get('chartHint'))
          and 'cmp_pass' not in ct and 'cmp_good' not in ct, str(list(ct.keys())))
    check('班级对比：堆叠分段表图表规格',
          bool(ct.get('cmp_score_seg', {}).get('chartHint'))
          and ct['cmp_score_seg']['chartHint']['type'] == 'stack')
    check('班级对比：五数概括 + 箱线图规格',
          'cmp_box' in ct and bool(ct['cmp_box'].get('chartHint'))
          and ct['cmp_box']['chartHint']['type'] == 'boxplot')
    # 01班只考史政地、02班只考物化生 → 科目对比表中未考班级该科应为空
    rows_s = ct.get('cmp_subject', {}).get('rows', [])
    hist_row = next((x for x in rows_s if x['subject'] == '历史'), None)
    check('班级对比：未考科目置空（历史仅01班有分）',
          hist_row is not None and hist_row['c0'] is not None and hist_row['c1'] is None,
          str(hist_row))
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}&classes={quote("01班")}')
    check('班级对比：不足两个班返回 400', r.status_code == 400)
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}'
              f'&classes={quote("01班")},{quote("不存在的班")}')
    check('班级对比：过滤不存在班级后 400', r.status_code == 400)
    r = c.get(f'/grades/export/compare?exam_id={eid}&classes={pair}')
    check('导出班级对比 Excel', r.status_code == 200 and r.data[:2] == b'PK')

    # ---- 分层模板应用（2026-10-15：all_subjects 连单科线一起划，率列表头按层名） ----
    r = c.post('/grades/api/bands/apply', json={'exam_id': eid, 'template': '4',
                                                'both': True, 'all_subjects': True},
               headers={'X-CSRFToken': csrf})
    check('分层模板应用', r.status_code == 200 and r.get_json()['success'],
          f'status={r.status_code} body={r.get_data(as_text=True)[:300]}')
    with app.app_context():
        bands = ExamBand.query.filter_by(exam_id=eid, direction='物理',
                                         subject='总分').order_by(ExamBand.seq).all()
        check('分层落库4层', len(bands) == 4 and bands[-1].lower_value == 0)
        # 20% 比例 → 物理 6 人 → 第2名分数（ceil(6*0.2)=2 → 610.5）
        check('比例换算正确', abs(bands[0].lower_value - 610.5) < 0.01,
              f"lower={bands[0].lower_value}")
    # 2026-10-15 组合维度（划线页「划线维度」= 按方向 / 物化生…）：非法组合必须被拒
    r = c.post('/grades/api/bands/batch',
               json={'exam_id': eid, 'mode': 'score', 'scope': 'per_subject',
                     'layers': [{'seq': 1, 'name': '特控线',
                                 'values': {'无此组合|总分': 500}}],
                     'selection': '无此组合'},
               headers={'X-CSRFToken': csrf})
    check('组合维度：非法组合被拒（400）', r.status_code == 400, f'status={r.status_code}')
    r = c.get(f'/grades/api/bands/matrix?exam_id={eid}')
    _mx = r.get_json()['data']
    _sels = sorted({b['selection'] for _rows in _mx.values() for b in _rows
                    if b.get('selection')})
    if _sels:
        _d = c.get(f'/grades/api/bands/matrix?exam_id={eid}&selection={_sels[0]}').get_json()['data']
        check('组合维度：键空间=组合|科目 且不带 selection 字段（可编辑）',
              any(k.startswith(_sels[0] + '|') for k in _d)
              and all('selection' not in b for _rows in _d.values() for b in _rows),
              f'sels={_sels} keys={sorted(_d)[:5]}')
    else:
        check('组合维度：本场无组合线，跳过键空间断言', True)
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    rows = r.get_json()['data']['tables']['class_summary']['rows']
    # 2026-10-19：分层上线统计已并入汇总表（累计口径列 p_层名 / r_层名），
    # 不再是单独的 layer_pass 表、也不再出 l_0 计数列
    check('分层上线列进入汇总表',
          'p_优秀' in rows[0] and rows[0]['p_优秀'] is not None, str(rows[0]))
    check('不再单独出分层上线表',
          'layer_pass' not in r.get_json()['data']['tables'],
          str(list(r.get_json()['data']['tables'])))
    # 累计上线人数校验：物理(02班)6 人，前20%线=610.5 → 优秀 2 人；历史(01班)前20%=590 → 优秀 2 人
    r2 = {row['class_name']: row for row in rows}
    check('分层上线人数正确（累计口径，优秀层=前20%）',
          r2['01班'].get('p_优秀') == 2 and r2['02班'].get('p_优秀') == 2,
          str({k: v.get('p_优秀') for k, v in r2.items()}))
    check('汇总表末行为年级总计',
          rows[-1]['class_name'] == '年级总计', str(rows[-1]['class_name']))
    # 2026-10-15 口径：划线后「及格率/优秀率」列 = 及格层/优秀层的**单科上线率**
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    tt = r.get_json()['data']['tables']['subject_total']
    cols = [x['label'] for x in tt['columns']]
    check('科目总表率列按划线层名（四层模板 → 及格率%/优秀率%）',
          '及格率%' in cols and '优秀率%' in cols
          and any(x.get('pass_rate') is not None for x in tt['rows']), str(cols))
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}&classes={pair}')
    ctb = r.get_json()['data']['tables']
    check('划线后对比页出及格率/优秀率横表（表名/列名=层名）',
          'cmp_pass' in ctb and 'cmp_good' in ctb
          and ctb['cmp_pass']['title'] == '对比班各科及格率%'
          and ctb['cmp_good']['title'] == '对比班各科优秀率%',
          str(list(ctb.keys())))

    # ---- 分批补导（模式 A 只更新 02班 数学；历史方向不动） ----
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.append(['学号', '姓名', '年级', '班级', '数学'])
    ws2.append(['20250101', '物生01', '2025级', '02班', 145])
    io2 = io.BytesIO()
    wb2.save(io2)
    io2.seek(0)
    r = c.post(f'/grades/exams/{eid}/import/upload',
               data={'mode': 'A', 'csrf_token': csrf, 'file': (io2, '补数学.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    html = r.get_data(as_text=True)
    with app.app_context():
        d2 = json.loads(Exam.query.get(eid).import_draft)
        check('分批补导解析（差异预览：更新1条）',
              r.status_code == 200 and d2['diff']['updated_rows'] == 1,
              str(d2['diff']))
    with app.app_context():
        token = Exam.query.get(eid).import_token
    c.post(f'/grades/exams/{eid}/import/confirm', data={'csrf_token': csrf, 'token': token},
           follow_redirects=True)
    with app.app_context():
        m = ExamScore.query.filter_by(exam_id=eid, student_no='20250101',
                                      subject='数学').first()
        total = ExamScore.query.filter_by(exam_id=eid, student_no='20250101',
                                          subject='总分').first()
        check('覆盖生效+总分重算', m.score == 145
              and abs(total.score - (118 + 145 + 121 + 90 + 88 + 82)) < 0.01,
              f'math={m.score} total={total.score}')

    # ---- 教师映射 + 自动开户（含班主任列：正班/副班1 与班型设置同步） ----
    wb3 = openpyxl.Workbook()
    ws3 = wb3.active
    ws3.append(['年级', '班级', '选科', '正班', '副班1',
                '语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理'])
    ws3.append(['2025级', '01班', '史政地', '班正师', '班副师',
                '王老师', '李老师', '张老师', '', '', '', '赵老师', '钱老师', '孙老师'])
    ws3.append(['2025级', '02班', '物化生', '物正师', '物副师',
                '王老师', '李老师', '张老师', '赵老师', '周老师', '吴老师', '', '', ''])
    io3 = io.BytesIO()
    wb3.save(io3)
    io3.seek(0)
    r = c.post('/grades/teachers/import/upload',
               data={'csrf_token': csrf, 'file': (io3, '教师安排表.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    html = r.get_data(as_text=True)
    check('教师安排表解析预览（任课+班主任）', '自动开户' in html and '班主任' in html)
    with app.app_context():
        # 无账号教师 8 位：王/李/张/赵/钱/孙/周/吴 → 全部 to_create
        preview = len(TeacherSubjectLink.query.all())
        check('尚未写库', preview == 0)
    # 取暂存 token（进程内 _DRAFT）
    from app.modules.grades.routes import teachers as teachers_routes
    ttoken = list(teachers_routes._DRAFT.keys())[-1]
    check('教师导入暂存 token', bool(ttoken))
    r = c.post('/grades/teachers/import/confirm', data={'csrf_token': csrf, 'token': ttoken},
               follow_redirects=True)
    html = r.get_data(as_text=True)
    check('教师导入确认（含自动开户）', '导入完成' in html and '新开账号' in html,
          f'body={html[:200]!r}')
    with app.app_context():
        n_link = TeacherSubjectLink.query.filter_by(active=True).count()
        names_8 = ['王老师', '李老师', '张老师', '赵老师', '钱老师', '孙老师', '周老师', '吴老师']
        n_users = User.query.filter(User.real_name.in_(names_8)).count()
        check('映射绑定（12条班科）', n_link == 12, f'links={n_link}')
        check('任课教师开户去重（8 个）', n_users == 8, f'users={n_users}')
        # 班主任同步（与 class-profile/UserClassLink 同源）
        from app.models import UserClassLink as UCL
        heads = User.query.filter(User.real_name.in_(['班正师', '班副师', '物正师', '物副师'])).all()
        check('班主任自动开户（4 个）', len(heads) == 4, f'heads={len(heads)}')
        check('班主任角色与组正确',
              all(h.role == 'homeroom_teacher' and h.permission_group
                  and h.permission_group.name == '班主任组' for h in heads))
        ucl = UCL.query.all()
        check('UserClassLink 已同步（4 条）',
              len(ucl) == 4 and all(l.grade == '2025级' for l in ucl),
              str([(l.class_name) for l in ucl]))
        # 班主任可看本班全部成绩：登录验证
        bz = User.query.filter_by(real_name='班正师').first()
        bz.set_password('pw123456')
        db.session.commit()
        # 姓名锁定校验
        import re
        un = User.query.filter_by(real_name='王老师').first()
        check('新账号已绑定任课教师组', un.permission_group is not None
              and un.permission_group.role == 'teacher')
        pwd_user = User.query.filter_by(real_name='李老师').first()
        check('初始密码可登录（强制改密标记）', pwd_user.must_change_pwd is True)
        check('姓名锁定：用户名拼音或 ts 前缀', un.username.startswith('ts')
              or bool(re.match(r'^[a-z0-9]+$', un.username)), un.username)

    # ---- 教师分析 API（admin 全量） ----
    r = c.get(f'/grades/api/analysis/teacher?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6')
    js = r.get_json()
    check('教师分析 API', 'teacher_data' in js['data']['tables']
          and js['data']['tables']['teacher_data']['rows']
          and len(js['data']['tables']['teacher_data']['rows']) == 2)

    # ---- 方向筛选（B1） ----
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}&direction=%E5%8E%86%E5%8F%B2')  # 历史
    js = r.get_json()['data']
    rows = js['tables']['class_summary']['rows']
    # 2026-10-19：合并表末行固定是「年级总计」，班级行要排除它再断言
    cls_rows = [x for x in rows if x['class_name'] != '年级总计']
    subs = js['tables']['subject_total']['rows']
    check('方向筛选-历史：仅历史班',
          len(cls_rows) == 1 and cls_rows[0]['class_name'] == '01班'
          and cls_rows[0]['count'] == 6, str(rows))
    check('方向筛选-历史：无物理科', all(x['subject'] != '物理' for x in subs))
    check('方向筛选-历史：均值口径', cls_rows[0]['avg'] == 561.7,
          str(cls_rows[0]['avg']))
    # 2026-10-19：箱线图随「历次总览以下」一并下线，这里不再断言 charts['box']
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}&direction=%E7%89%A9%E7%90%86')  # 物理
    js = r.get_json()['data']
    rows = js['tables']['class_summary']['rows']
    cls_rows = [x for x in rows if x['class_name'] != '年级总计']
    subs = js['tables']['subject_total']['rows']
    check('方向筛选-物理：仅物理班且含物理科',
          len(cls_rows) == 1 and cls_rows[0]['class_name'] == '02班'
          and any(x['subject'] == '物理' for x in subs)
          and all(x['subject'] != '历史' for x in subs))
    r = c.get(f'/grades/api/analysis/subject?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6'
              + '&direction=%E5%8E%86%E5%8F%B2')
    t1 = r.get_json()['data']['tables']['class_compare']['rows']
    check('方向筛选-学科：仅历史班数据', len(t1) == 1 and t1[0]['class_name'] == '01班',
          str(t1))
    # 分层表方向列（subject T3）在过滤时只输出该方向
    t3 = r.get_json()['data']['tables']['layer_score']['rows']
    check('方向筛选-学科：分层仅历史方向', t3 and all(x['direction'] == '历史' for x in t3))

    # ---- 导出 Excel ----
    r = c.get(f'/grades/export/grade?exam_id={eid}')
    check('导出年级分析 Excel', r.status_code == 200 and r.data[:2] == b'PK')

    # ---- 页面可访问 ----
    # 2026-10-13：/grades/ 由「重定向到历次考试列表」改为成绩模块默认落地页「首页仪表盘」
    # （routes/dashboard.py::dashboard），旧的重定向断言随之作废。
    r = c.get('/grades/')
    check('页面 /grades/（首页仪表盘）',
          r.status_code == 200 and '首页仪表盘' in r.get_data(as_text=True),
          str(r.status_code))
    for url in ('/grades/exams', f'/grades/exams/{eid}',
                f'/grades/exams/{eid}/import', f'/grades/exams/{eid}/bands',
                '/grades/teachers', '/grades/teachers/import'):
        r = c.get(url)
        check(f'页面 {url}', r.status_code == 200, str(r.status_code))

    # ---- 临界生对比：班级筛选（cls；总分行口径＝考试当时班级，按方向收敛，非法值＝全部） ----
    nl = c.get(f'/grades/exams/{eid}/near-line').get_data(as_text=True)
    check('临界生页：班级筛选下拉出现且列出本方向班级',
          'name="cls"' in nl and '全部班级' in nl and '<option value="02班"' in nl,
          nl[:200])
    check('临界生页：方向内的班级才进下拉（默认物理 → 不下拉 01班）',
          '<option value="01班"' not in nl)
    nl_cls = c.get(f'/grades/exams/{eid}/near-line?cls={quote("02班")}'
                   '&above=10&below=20').get_data(as_text=True)
    check('临界生页：选中班级后只显示该班（表头标注「仅 X」）',
          re.search(r'仅\s*02班', nl_cls) is not None and 'name="cls"' in nl_cls)
    hrefs = re.findall(r'href="([^"]*near-line\?[^"]*)"', nl_cls)
    check('临界生页：清除班级链接保留分数区间（且不带 cls）',
          any('cls=' not in h and 'above=10' in h and 'below=20' in h for h in hrefs),
          str(hrefs))
    check('临界生页：班级对比里的班名可点该班下钻',
          any('cls=02' in h for h in hrefs), str(hrefs))
    nl_hist = c.get(f'/grades/exams/{eid}/near-line?direction={quote("历史")}'
                    f'&cls={quote("01班")}').get_data(as_text=True)
    check('临界生页：切历史方向后下拉/筛选跟着换（01班生效）',
          '<option value="01班" selected' in nl_hist and re.search(r'仅\s*01班', nl_hist))
    nl_bad = c.get(f'/grades/exams/{eid}/near-line?cls={quote("不存在的班")}') \
        .get_data(as_text=True)
    check('临界生页：非法班级回退为全部（不残留「仅」标注）',
          'name="cls"' in nl_bad and re.search(r'仅\s*不存在的班', nl_bad) is None)
    # ---- 明细分页（展示层）：行带 nl-row 标记、分页条在卡片脚、页码 scope 随筛选变化 ----
    check('临界生页：明细行带分页标记且分页条就位（每页可选「全部」）',
          'id="nlBody"' in nl and '<tr class="nl-row">' in nl
          and 'id="nlPager"' in nl and 'id="nlInfo"' in nl and 'value="all"' in nl)
    scope_all = re.findall(r'data-scope="([^"]*)"', nl)
    scope_cls = re.findall(r'data-scope="([^"]*)"', nl_cls)
    check('临界生页：页码按筛选条件分区（班级进 scope → 换班/换方向即回第 1 页）',
          bool(scope_all) and scope_all[0].startswith('||')
          and bool(scope_cls) and scope_cls[0].startswith('02班|'),
          f'{scope_all[:1]} / {scope_cls[:1]}')
    nl_empty = c.get(f'/grades/exams/{eid}/near-line?above=0&below=0').get_data(as_text=True)
    check('临界生页：区间内没人时不渲染分页条',
          '<tr class="nl-row">' in nl_empty or 'id="nlPager"' not in nl_empty)

    # ---- 成绩汇报区：AI 一句话分析入口（2026-10-13，工具调用 → HTML 报告） ----
    hr = c.get(f'/grades/report?exam={eid}').get_data(as_text=True)
    check('汇报区：AI 一句话分析入口就位',
          'id="rpAiCard"' in hr and 'id="rpAiQ"' in hr and 'id="rpAiRun"' in hr
          and 'rp-ai-chip' in hr and 'js/report_ai.js' in hr)

    # ---- v1.19.49.38 排序三角：统一在文字后面 + 未点选也看得清 + 右对齐列仍与数字对齐 ----
    html = c.get(f'/grades/exams/{eid}').get_data(as_text=True)
    check('表头：三角统一在文字后面',
          all((lbl + '<i') in html for lbl in
              ('学号', '姓名', '方向排', '进退步', '班排', '总分', '语文')))
    check('表头：三角不再出现在文字左侧',
          all(('></i>' + lbl) not in html for lbl in ('总分', '方向排', '班排', '语文')))
    check('表头：未点选三角不再用极淡的 opacity-25',
          'bi-caret-down opacity-25' not in html and 'bi-caret-down sort-off' in html)
    check('表头：右对齐列三角绝对定位进预留位',
          '#scoreTable th.text-end a.th-sort i { position: absolute;' in html)
    check('表头：表头与数据单元同宽预留三角位',
          'td.c-rank, #scoreTable td.c-total, #scoreTable td.c-sub { padding-right: 12px; }' in html)
    check('页头：默认排序/改分说明整行已移除',
          '默认按' not in html and '恢复默认排序' not in html
          and '点击单科分数可修改' not in html)
    # ---- v1.19.49.38 侧栏「成绩分析」标题本身即入口（直接进总览），展开/收起交给箭头 ----
    check('侧栏：成绩分析标题指向总览（全年级对比）',
          'class="efn-group-title"' in html
          and ('/grades/global-compare?exam=%d' % eid) in html
          and '<a class="efn-group"' not in html)
    check('侧栏：分析组展开仍由箭头负责',
          'data-bs-toggle="collapse" href="#efnAnalysis"' in html)

    # ---- v1.19.49.38 表头/数字严格对齐 + 名次列次序 + 进退步悬浮说明 ----
    # th 内若残留空白，右对齐时行尾空格不会被折掉，会把表头文字往左推几个像素 → 与数字错开
    _thead = html.split('<thead')[1].split('</thead>')[0]
    check('表头：th 内部无空白', '</a></th>' in _thead and re.search(r'\s</th>', _thead) is None)
    check('名次列次序：方向排 → 进退步 → 班排',
          _thead.index('方向排') < _thead.index('进退步') < _thead.index('班排'))
    check('数据单元：右对齐名次为一行无空白',
          re.search(r'<td class="text-end c-rank">\d+</td>', html) is not None)
    check('进退步悬浮：写明同方向进退步', 'title="同方向进退步：' in html)

    # ---- v1.19.49.38 导航高亮唯一性 + 分析子模块「考试态」锁本场 ----
    hb = c.get(f'/grades/exams/{eid}/bands').get_data(as_text=True)
    # 2026-10-19：侧栏去掉「划线分层」独立项，划线页归到「数据导入」段
    check('导航：划线页高亮「数据导入」且不再有划线分层项',
          re.search(r'class="efn-item active" data-nav="import"', hb) is not None
          and 'data-nav="bands"' not in hb)
    ha = c.get(f'/grades/analysis?exam={eid}').get_data(as_text=True)
    check('分析页（考试态）隐藏年级/考试下拉',
          'id="gGrade" class="form-select form-select-sm w-auto d-none"' in ha
          and 'id="gExam" class="form-select form-select-sm w-auto d-none"' in ha)
    check('分析页（考试态）带入本场考试', f'id="gInitialExam" class="d-none">{eid}<' in ha)
    hg = c.get(f'/grades/global-compare?exam={eid}').get_data(as_text=True)
    check('总览页（考试态）认 ?exam 且隐藏考试下拉',
          f'data-exam-id="{eid}"' in hg
          and 'id="ldExam" class="form-select form-select-sm w-auto d-none"' in hg)
    hp = c.get(f'/grades/pivot?exam={eid}').get_data(as_text=True)
    check('自由表（考试态）带入本场考试且隐藏年级/考试',
          f'id="pvInitialExam" class="d-none">{eid}<' in hp
          and 'id="pvGrade" class="form-select form-select-sm w-auto d-none"' in hp)
    hn = c.get('/grades/analysis').get_data(as_text=True)
    check('非考试态：年级下拉仍可选',
          'id="gGrade" class="form-select form-select-sm w-auto d-none"' not in hn)

    # ---- v1.19.49.38 考务步骤① 名单维护：只读名单 + 沿用上一场不参考 + 不参考排最上 ----
    r = c.post(f'/grades/affairs/{_AFF_CUR}/students/sync', headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('考务同步：自动沿用上一场不参考 1 人',
          d and d.get('ok') and d.get('matched_non_attend') == 1, d)
    h2 = c.get(f'/grades/affairs/{_AFF_CUR}').get_data(as_text=True)
    tb2 = h2.split('id="stuTable"')[1].split('</table>')[0]
    hd2 = tb2.split('</thead>')[0]
    check('考务步骤①：无手动新增/编辑',
          'onclick="openStuModal' not in h2 and 'id="stuModal"' not in h2
          and '>编辑</button>' not in h2)
    # ---- v1.19.49.38 考务步骤① 左右分栏（左=参加/右=不参加）+ 名单统计 ----
    check('考务步骤①：左右分栏（参加 / 不参加 两张表）',
          '参加考试' in h2 and '不参加考试' in h2
          and 'id="stuTable"' in h2 and 'id="stuSkipTable"' in h2)
    # 2026-10-14 用户口径：左表加「固定考场/座号」输入列（混编排考场用），故只断言没有
    # 考号 / 参考列；右表的学生不参与编排，不应出现这一列
    check('考务步骤①：左表含名单列 + 固定考场/座号（无考号/参考）',
          all(k in hd2 for k in ('学号', '姓名', '班级', '选科组合', '固定考场/座号'))
          and '考号' not in hd2 and '参考' not in hd2, hd2[:200])
    ts2 = h2.split('id="stuSkipTable"')[1].split('</table>')[0]
    check('考务步骤①：右表（不参加）无固定考场列',
          '固定考场' not in ts2.split('</thead>')[0])
    check('考务步骤①：不参加的学生在右表（不在左表）',
          '20250001' in ts2 and '20250001' not in tb2)
    check('考务步骤①：不参加学生整行标黄',
          'class="table-warning"' in ts2, ts2[:200])
    check('考务步骤①：两侧可按「参加/不参加」互移',
          'toggleAttend(' in h2 and '→ 不参加' in h2 and '← 参加' in h2)
    # 2026-10-14 用户口径：统计压成顶部一行（实际/参加/不参加）+「各班明细」折叠表（含实际人数）
    check('考务步骤①：名单统计一行 + 各班明细',
          all(k in h2 for k in ('实际', '参加', '不参加', '各班明细', '实际人数')))
    # 2026-10-14 用户口径（二次修订）：筛选控件**并进「参加考试」卡片头**（与计数同一行，
    # 不再单独占一行），同一条同时作用于左右两栏。
    # 2026-10-14 第四轮审计：原断言要求"卡片头不含搜索框/提示另收一行"，与二次修订后的
    # 实现相反（恒失败）；按当前实现更新，并保留"一条、两侧同时生效"的语义检查。
    _ch1 = h2.split('id="stuTableBadge"')[1].split('</div>')[0]
    check('考务步骤①：筛选控件并进卡片头（一条、两侧同时生效）',
          'id="fltClass"' in _ch1 and 'id="fltStu"' in _ch1
          and '同时作用于左右两栏' in h2 and 'id="stuSkipTable"' in h2)
    # 排序：两侧均按 班级 → 姓氏（拼音）；fixture 中班级与学号/姓名同序，故断言学号升序
    _ln = re.findall(r'<td>(\d{8})</td>', tb2)
    _rn = re.findall(r'<td>(\d{8})</td>', ts2)
    check('考务步骤①：两侧按班级·姓氏排序（学号升序）',
          _ln == sorted(_ln) and _rn == sorted(_rn)
          and _rn[:1] == ['20250001'] and '20250001' not in _ln,
          (_ln[:3], _rn[:3]))
    # 2026-10-13：断言改成**匹配规则形态**（是否用 --bs-table-bg 决定首列底色），
    # 不再比对整条 fallback 字面量 —— 兜底值从 --bs-body-bg 改成 --sl-surface 后
    # 这条断言就恒失败（表在白卡片上，兜底用面板色才不会让首列发灰）。
    check('首列 sticky 背景跟随行状态（整行标黄含学号列）',
          re.search(r'tbody\s+td:first-child\s*\{\s*background:\s*var\(--bs-table-bg', h2)
          is not None)

    # ---- v1.19.36.0 步骤② 考场容量：批量设置 / 沿用上一场（同房间）----
    check('步骤② 有容量批量入口',
          '批量设置容量' in h2 and '带入上次容量' in h2)
    # v1.19.49.38：这两个按钮属于「本批次考场」侧 → 应在右卡片头内（不在顶部工具栏）
    # 2026-10-14：2026-10-14 用户口径把「考场模板下拉 + 一键带入」也挪进这一行后，
    #   原 900 字符窗口在「带入上次容量」之前就截断了（假失败）→ 改为按右卡片头截取
    _rh = h2.split('id="roomStat"')[1].split('</div>')[0]
    check('步骤②：容量按钮已移到「本批次考场」卡片头',
          '批量设置容量' in _rh and '带入上次容量' in _rh
          and 'setAllCapacity()' in _rh)
    # v1.19.49.38 考务页单独的 AI 导入入口已下线（统一走每场考试「AI 智能导入」）
    check('步骤②：考务页 AI 导入入口已下线',
          '导入考生安排' not in h2
          and ('/grades/affairs/%d/ai-import' % _AFF_CUR) not in h2)
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/capacity',
               data={'mode': 'all', 'capacity': '36'}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('容量批量设置（全 36）', d and d.get('ok') and d.get('capacity') == 36, d)
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/capacity',
               data={'mode': 'all', 'capacity': '0'}, headers={'X-CSRFToken': csrf})
    check('容量越界被拒', r.get_json().get('ok') is False, r.get_json())
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/capacity',
               data={'mode': 'prev'}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('沿用上一场容量（同房间）',
          d and d.get('ok') and d.get('updated') == 1 and d.get('src') == '冒烟上一场', d)
    with app.app_context():
        got = {x.location: x.capacity
               for x in AffairRoom.query.filter_by(affair_id=_AFF_CUR).all()}
        check('容量落库 = 上一场 44', got == {'东211': 44}, got)

    # ---- 2026-10-14 步骤② 考场编号可直接改：查重 + 同步名单（考场号/固定考场/考号）与监考 ----
    from app.models.grades import AffairInvigilation, AffairSchedule
    with app.app_context():
        _r01 = AffairRoom.query.filter_by(affair_id=_AFF_CUR, room_no='01').first()
        _r02 = AffairRoom(affair_id=_AFF_CUR, room_no='02', location='东215',
                          subject='默认', capacity=30)
        db.session.add(_r02)
        _sch = AffairSchedule(affair_id=_AFF_CUR, exam_date=_date(2019, 2, 5),
                              subject='数学', sort_order=1)
        db.session.add(_sch)
        db.session.flush()
        # 复用同步进来的 20250001（uq_affair_stu 下同批次不可重复建行），改成已编排状态
        _st = AffairStudent.query.filter_by(affair_id=_AFF_CUR,
                                           student_no='20250001').first()
        _st.room_no, _st.seat_no = '01', 3
        _st.fixed_room, _st.fixed_seat = '01', 3
        _st.exam_number = '17010103'
        db.session.add(AffairInvigilation(affair_id=_AFF_CUR, schedule_id=_sch.id,
                                          room_no='01', slot=1, name='测试监考甲'))
        # 两个考场各挂一格监考：互换编号时同场次的 01/02 两格会互相撞唯一键
        # （uq_affair_invigilation 含 room_no），专门用来兜住「监考表也要两阶段改」
        db.session.add(AffairInvigilation(affair_id=_AFF_CUR, schedule_id=_sch.id,
                                          room_no='02', slot=1, name='测试监考乙'))
        db.session.commit()
        _RID01, _RID02, _SCHID = _r01.id, _r02.id, _sch.id
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/{_RID01}/patch',
               data={'room_no': '03'}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('步骤② 改考场编号：改成功并回传同步数',
          d.get('ok') is True and d.get('room_no') == '03' and d.get('renamed') is True
          and d.get('students') == 1 and d.get('invigs') == 1, d)
    with app.app_context():
        _st2 = AffairStudent.query.filter_by(affair_id=_AFF_CUR,
                                            student_no='20250001').first()
        _iv = AffairInvigilation.query.filter_by(affair_id=_AFF_CUR, schedule_id=_SCHID,
                                                 name='测试监考甲').first()
        check('改编号：学生考场号 / 固定考场 / 考号一并同步',
              _st2.room_no == '03' and _st2.fixed_room == '03'
              and _st2.exam_number == '17010303',
              (_st2.room_no, _st2.fixed_room, _st2.exam_number))
        check('改编号：监考安排同步到新编号', _iv is not None and _iv.room_no == '03',
              _iv and _iv.room_no)
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/{_RID02}/patch',
               data={'room_no': '03'}, headers={'X-CSRFToken': csrf})
    check('改编号：同批次重号被拒', r.get_json().get('ok') is False, r.get_json())
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/{_RID02}/patch',
               data={'room_no': '   '}, headers={'X-CSRFToken': csrf})
    check('改编号：空编号被拒', r.get_json().get('ok') is False, r.get_json())
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/{_RID02}/patch',
               data={'room_no': '5'}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('改编号：纯数字补零为两位（5 → 05）',
          d.get('ok') is True and d.get('room_no') == '05', d)
    h3 = c.get(f'/grades/affairs/{_AFF_CUR}').get_data(as_text=True)
    _rb = h3.split('id="roomTable"')[1].split('</tbody>')[0]
    # 只取编号输入框那一段（科目/容量仍是 onchange 即存）；不能用 split('rm-no')[1]——
    # 「清空」按钮的类名 rm-no-x 也含 rm-no，会把这一段截断
    _i = _rb.find('rm-no"')
    _rn = _rb[_i:_rb.index('</td>', _i)]
    check('步骤② 考场编号渲染为草稿输入框（改/清空不即时提交）',
          'py-0 rm-no' in _rb and 'oninput="_roomNoInput(this)"' in _rn
          and 'onchange="saveRoomField(this)"' not in _rn)
    check('步骤② 有「保存编号」按钮 + 离开页面前提醒',
          'id="saveNoBtn"' in h3 and '保存编号' in h3
          and 'beforeunload' in h3 and '_roomNoDirty()' in h3)
    # 2026-10-14 用户口径：编号行内给「清空」小按钮（×）；② 的「导入 Excel / 手动新增」上移到
    # 「编排容量」那一行（#capActs，仅步骤② 显示，由 _syncCapBar 切显隐）
    # 2026-10-14 用户口径（二次修订）：编号清空要「一键全部清空」——卡片头「清空编号」按钮 +
    # 前端 `clearAllRoomNo()`（只写草稿），行内不再放 ×
    check('步骤② 编号行内只有输入框（行内 × 已撤）',
          'rm-no-x' not in _rb and 'clearRoomNo' not in h3)
    check('步骤② 卡片头有「清空编号」（一键全清、只写草稿）',
          'clearAllRoomNo()' in h3 and '清空编号' in h3
          and "r.room_no = '';" in h3 and '_roomNoSync()' in h3
          and h3.index('clearAllRoomNo()') < h3.index('id="roomTable"'),
          '按钮不在卡片头（跑到表格里了）')
    _t2 = h3.split('id="t2"')[1].split('步骤③')[0]
    check('步骤② 的「导入 Excel / 手动新增」已上移到「编排容量」那行（按钮在提示前）',
          'id="capActs"' in h3 and 'importRooms(this)' in h3 and 'openRoomModal()' in h3
          and h3.index('id="capActs"') < h3.index('bi-clipboard-data')
          and 'id="roomFile"' not in _t2, repr(_t2[:120]))
    check('步骤② 顶部按钮仍只在② 显示（_syncCapBar 按页签切显隐）',
          "getElementById('capActs')" in h3 and "tgt !== '#t2'" in h3)
    # 2026-10-14 用户口径：考场要能「上移 / 下移」按序走 —— 行序即编号序，上下移 = 与相邻行
    # **交换编号**（草稿），复用「保存编号」提交（后端两阶段写入）
    check('步骤② 每行有「上移 / 下移」（moveRoom + 草稿交换编号）',
          'rm-up' in _rb and 'rm-dn' in _rb and 'onclick="moveRoom(' in _rb
          and 'function moveRoom(' in h3 and 'function _sortBatchRooms(' in h3
          and "a.room_no = b.room_no" in h3 and 'b.room_no = t;' in h3, repr(_rb[:120]))
    check('步骤② 首行/末行的上移/下移会禁用（_roomNoSync 里就地切 disabled）',
          'up.disabled = (i === 0)' in h3 and 'dn.disabled = (i === trs.length - 1)' in h3)

    # ---- 2026-10-14 考场维护：分组内「上移 / 下移」+ 顺序落库 + 一键带入沿用顺序 ----
    from app.models.grades import AffairRoomLib, ExamRoomGroup, ExamRoomGroupMap
    with app.app_context():
        _grp = ExamRoomGroup(name='冒烟排序组')
        db.session.add(_grp)
        db.session.flush()
        _lib4 = []
        for _nm in ('排序东1', '排序东2', '排序东3', '排序东4'):
            _l = AffairRoomLib(location=_nm, capacity=30)
            db.session.add(_l)
            db.session.flush()
            _lib4.append(_l)
        # 故意让 sort_order 与 id 顺序**不一致**：东3(10) → 东1(20) → 东2(30)
        for _i, _idx in enumerate((2, 0, 1)):
            db.session.add(ExamRoomGroupMap(group_id=_grp.id, lib_id=_lib4[_idx].id,
                                            sort_order=(_i + 1) * 10))
        _af2 = ExamAffair(name='冒烟排序带入场', grade='2025级', exam_date=_date(2019, 2, 6),
                          status='draft')
        db.session.add(_af2)
        db.session.commit()
        _GID, _LIDS, _AFF2 = _grp.id, [x.id for x in _lib4], _af2.id
    r = c.post(f'/grades/affairs/{_AFF2}/rooms/from-lib-batch',
               data={'group_id': _GID}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    _added = {x['location']: x['room_no'] for x in (d.get('added') or [])}
    check('一键带入：按分组排好的顺序生成编号（东3→01 / 东1→02 / 东2→03）',
          d.get('ok') is True
          and _added == {'排序东3': '01', '排序东1': '02', '排序东2': '03'}, _added)
    # 上移第 3 位（东2）→ 顺序变成 东3、东2、东1
    r = c.post(f'/grades/rooms/groups/{_GID}/reorder',
               data={'lib_id': _LIDS[1], 'dir': -1}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('考场维护：分组内「上移」返回新顺序（东3、东2、东1）',
          d.get('ok') is True and d.get('order') == [_LIDS[2], _LIDS[1], _LIDS[0]], d)
    with app.app_context():
        _gr = (ExamRoomGroupMap.query.filter_by(group_id=_GID)
               .order_by(ExamRoomGroupMap.sort_order, ExamRoomGroupMap.id).all())
        check('考场维护：顺序落库为 10 / 20 / 30（无并列，界面照它渲染）',
              [x.lib_id for x in _gr] == [_LIDS[2], _LIDS[1], _LIDS[0]]
              and [x.sort_order for x in _gr] == [10, 20, 30],
              [(x.lib_id, x.sort_order) for x in _gr])
    r = c.post(f'/grades/rooms/groups/{_GID}/reorder',
               data={'lib_id': _LIDS[2], 'dir': -1}, headers={'X-CSRFToken': csrf})
    check('考场维护：已在最前时上移被拒', r.get_json().get('ok') is False, r.get_json())
    r = c.post(f'/grades/rooms/groups/{_GID}/reorder',
               data={'lib_id': 99999999, 'dir': 1}, headers={'X-CSRFToken': csrf})
    check('考场维护：不在该分组的房间不能被排序',
          r.get_json().get('ok') is False, r.get_json())
    # 新房间加入分组（set_groups）：不能把已排好的顺序冲掉，新房间排到末尾（40）
    c.post(f'/grades/rooms/{_LIDS[3]}/groups', data={'group_ids': str(_GID)},
           headers={'X-CSRFToken': csrf})
    with app.app_context():
        _gr2 = (ExamRoomGroupMap.query.filter_by(group_id=_GID)
                .order_by(ExamRoomGroupMap.sort_order, ExamRoomGroupMap.id).all())
        check('考场维护：新加入分组的房间排到末尾（不打乱已有顺序）',
              [x.lib_id for x in _gr2] == [_LIDS[2], _LIDS[1], _LIDS[0], _LIDS[3]]
              and _gr2[-1].sort_order == 40,
              [(x.lib_id, x.sort_order) for x in _gr2])
    h5 = c.get('/grades/rooms').get_data(as_text=True)
    check('考场维护：页面带出组内顺序 + 右侧列表「上移 / 下移」按钮',
          'lib_ids' in h5 and 'moveRoomInGroup(' in h5 and 'rm-up' in h5
          and '/reorder' in h5 and 'bi-arrow-down' in h5)

    # ---- 2026-10-14 「保存编号」：整批提交（互换 / 重号拒绝不落库 / 空号自动补最小空号） ----
    # 此刻：东211 = 03（学生 20250001 + 监考甲）、东215 = 05（监考乙）
    _items = json.dumps([{'id': _RID01, 'room_no': '05'}, {'id': _RID02, 'room_no': '03'}])
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/rename',
               data={'items': _items}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('保存编号：支持两考场互换编号',
          d.get('ok') is True and d.get('changed') == 2, d)
    with app.app_context():
        _swap = {x.id: x.room_no
                 for x in AffairRoom.query.filter_by(affair_id=_AFF_CUR).all()}
        _st3 = AffairStudent.query.filter_by(affair_id=_AFF_CUR,
                                            student_no='20250001').first()
        _iv3 = {x.name: x.room_no for x in AffairInvigilation.query.filter_by(
            affair_id=_AFF_CUR, schedule_id=_SCHID).all()}
        check('保存编号：互换后落库正确（东211=05 / 东215=03）',
              _swap.get(_RID01) == '05' and _swap.get(_RID02) == '03', _swap)
        check('保存编号：互换后名单与考号同步（考场 05 + 考号 17010503）',
              _st3.room_no == '05' and _st3.exam_number == '17010503',
              (_st3.room_no, _st3.exam_number))
        # 两个考场的监考格子对调（唯一键 uq_affair_invigilation 含 room_no，必须两阶段改）
        check('保存编号：互换后监考表两格都对调（甲→05 / 乙→03）',
              _iv3.get('测试监考甲') == '05' and _iv3.get('测试监考乙') == '03', _iv3)
    _items = json.dumps([{'id': _RID01, 'room_no': '03'}, {'id': _RID02, 'room_no': '03'}])
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/rename',
               data={'items': _items}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('保存编号：重号整批拒绝并给出占用的房间',
          d.get('ok') is False and '重复' in (d.get('msg') or ''), d)
    with app.app_context():
        _swap2 = {x.id: x.room_no
                  for x in AffairRoom.query.filter_by(affair_id=_AFF_CUR).all()}
        check('保存编号：重号时不落库（编号仍为 05 / 03）',
              _swap2.get(_RID01) == '05' and _swap2.get(_RID02) == '03', _swap2)
    _items = json.dumps([{'id': _RID01, 'room_no': '  '}])
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/rename',
               data={'items': _items}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('保存编号：空号自动补最小空号（→ 01）',
          d.get('ok') is True and [x.get('room_no') for x in (d.get('filled') or [])] == ['01'], d)
    with app.app_context():
        _st4 = AffairStudent.query.filter_by(affair_id=_AFF_CUR,
                                            student_no='20250001').first()
        check('保存编号：自动补号后名单 / 考号同步（01 / 17010103）',
              _st4.room_no == '01' and _st4.exam_number == '17010103',
              (_st4.room_no, _st4.exam_number))
    _items = json.dumps([{'id': 99999999, 'room_no': '07'}])
    r = c.post(f'/grades/affairs/{_AFF_CUR}/rooms/rename',
               data={'items': _items}, headers={'X-CSRFToken': csrf})
    check('保存编号：非本批次考场被拒', r.get_json().get('ok') is False, r.get_json())

    # ---- v1.19.49.38 任课教师映射：下拉按姓名首字母排序 + 保存不再静默失效 ----
    # 排序键与后端一致：(拼音首字母, 全拼, 姓名)；无 pypinyin 时后端退化为账号（本身即拼音）
    from app.modules.grades.services.user_account import pinyin_of as _py_of

    def _nkey(u):
        p = (_py_of(u['name']) if u.get('name') else None)
        p = (p or '').lower() or (u.get('username') or '').lower() or (u.get('name') or '')
        return (p[:1], p, u.get('name') or '')

    _t = c.get('/grades/api/teachers', query_string={'grade': '2025级'}).get_json()
    _tusers = (_t.get('data') or {}).get('users') or []
    _keys = [_nkey(u) for u in _tusers]
    check('教师下拉：按姓名首字母（拼音）排序', bool(_keys) and _keys == sorted(_keys),
          _keys[:6])
    _b = json.dumps({'grade': '2025级', 'cells': {'02班|地理': str(_tusers[0]['id'])}})
    r = c.post('/grades/api/teachers/save', data=_b, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('教师映射保存：表单型 JSON 也生效（原静默失效）',
          d.get('success') is True and (d.get('changes') or {}).get('bound') == 1, d)
    # 还原（把刚绑定的单元格清空 → 停用），避免影响后续用例
    c.post('/grades/api/teachers/save',
           data=json.dumps({'grade': '2025级', 'cells': {'02班|地理': ''}}),
           headers={'X-CSRFToken': csrf})

    # ==================== 角色权限矩阵（9.2 后端强制） ====================
    from app.models import UserClassLink
    from app.models.user import User as U

    def login_as(username):
        c.get('/logout', follow_redirects=True)
        r0 = c.get('/login')
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
        rr = c.post('/login', data={'csrf_token': m.group(1) if m else '',
                                    'username': username, 'password': 'pw123456'},
                    follow_redirects=True)
        # M-2：会话在登录时重建，写操作前需重新取 token
        mm = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', rr.get_data(as_text=True))
        return mm.group(1) if mm else ''

    def logout():
        c.get('/logout', follow_redirects=True)

    with app.app_context():
        grp = {g.name: g for g in PermissionGroup.query.all()}
        # H-1：新建账号默认 must_change_pwd=True（会被强制改密拦截），
        # 冒烟脚本里这些账号要直接参与业务验证，显式置为 False。
        h = User(username='banzhu01', real_name='测试班主任', role='homeroom_teacher',
                 grade='2025级', class_name='01班', permission_group=grp['班主任组'],
                 must_change_pwd=False)
        h.set_password('pw123456')
        db.session.add(h)
        db.session.flush()
        db.session.add(UserClassLink(user_id=h.id, grade='2025级', class_name='01班'))
        t = User(username='renke01', real_name='测试任课教师', role='teacher',
                 permission_group=grp['任课教师组'], must_change_pwd=False)
        t.set_password('pw123456')
        db.session.add(t)
        db.session.flush()
        gld = User(username='nianji01', real_name='测试年级长', role='grade_leader',
                   grade='2025级', permission_group=grp['年级长组'],
                   must_change_pwd=False)
        gld.set_password('pw123456')
        db.session.add(gld)
        db.session.flush()
        # 复用已导入的 01班数学 映射指向测试任课教师
        link01 = (TeacherSubjectLink.query
                  .filter_by(grade='2025级', class_name='01班', subject='数学').first())
        link01.user_id = t.id
        db.session.commit()
        tid = t.id

    # 班主任：仅 class tab，且只本班
    login_as('banzhu01')
    r = c.get('/grades/api/options')
    tabs = r.get_json()['data']['tabs']
    # 2026-10-15 用户口径：班主任除本班外，还要能看「所在年级的成绩分析」
    check('班主任 tabs=班级+年级分析', tabs == {'grade': True, 'class': True,
                                            'subject': False, 'teacher': False,
                                            'compare': False}, str(tabs))
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=01%E7%8F%AD')
    check('班主任可看本班分析', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=02%E7%8F%AD')
    check('班主任不可看别班（403）', r.status_code == 403)
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    check('班主任可看年级分析（200，仅本年级）', r.status_code == 200, r.status_code)
    _pair2 = quote('01班') + ',' + quote('02班')
    r = c.get(f'/grades/api/analysis/compare?exam_id={eid}&classes={_pair2}')
    check('班主任不可看班级对比（403）', r.status_code == 403)
    r = c.get(f'/grades/export/compare?exam_id={eid}&classes={_pair2}')
    check('班主任不可导出班级对比（403）', r.status_code == 403)
    # 2026-10-15：年级分析已对班主任 / 任课教师开放，导出与其保持一致（本文件既定口径"能看就能导"）
    r = c.get(f'/grades/export/grade?exam_id={eid}')
    check('班主任可导出年级分析（200）', r.status_code == 200, r.status_code)
    r = c.get(f'/grades/export/class?exam_id={eid}&class_name=02%E7%8F%AD')
    check('班主任不可导出别班明细（403）', r.status_code == 403, r.status_code)
    logout()

    # 任课教师：仅 teacher tab（本人 01班数学）
    login_as('renke01')
    r = c.get('/grades/api/options')
    tabs = r.get_json()['data']['tabs']
    # 2026-10-15 用户口径：任课教师也要能看「所在年级的成绩分析」+ 学科分析
    check('教师 tabs=年级+学科+本人', tabs == {'grade': True, 'class': False,
                                          'subject': True, 'teacher': True,
                                          'compare': False}, str(tabs))
    r = c.get(f'/grades/api/analysis/teacher?exam_id={eid}&subject=%E6%95%B0%E5%AD%A6')
    js = r.get_json()['data']['tables']['teacher_data']
    check('教师仅见本人数据', js['rows'] and js['rows'][0]['teacher'] == '测试任课教师'
          and js['rows'][0]['class_name'] == '01班', str(js['rows']))
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    check('教师可看年级分析（200，仅本年级）', r.status_code == 200, r.status_code)
    logout()

    # 年级长：全部 tab（仅本年级考试）
    login_as('nianji01')
    r = c.get(f'/grades/api/analysis/grade?exam_id={eid}')
    check('年级长可看年级分析', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/subject?exam_id={eid}&subject=%E8%AF%AD%E6%96%87')
    check('年级长可看学科分析', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/teacher?exam_id={eid}')
    check('年级长可看教师分析（本年级12条）',
          r.status_code == 200 and len(r.get_json()['data']['tables']['teacher_data']['rows']) == 12)
    logout()

    # ===== 导入产生的班主任：可查看本班全部科目成绩（需求） =====
    with app.app_context():
        bzu = User.query.filter_by(real_name='班正师').first()
        bz_name = bzu.username
        # H-1：导入教师现在使用随机一次性口令 + 强制改密，
        # 脚本需要已知口令才能登录，这里显式重置。
        bzu.set_password('pw123456')
        bzu.must_change_pwd = False
        db.session.commit()
    login_as(bz_name)
    r = c.get('/grades/api/options')
    tabs = r.get_json()['data']['tabs']
    check('导入班主任 tabs=班级+年级分析', tabs == {'grade': True, 'class': True,
                                             'subject': False, 'teacher': False,
                                             'compare': False},
          str(tabs))
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=01%E7%8F%AD')
    check('导入班主任可看本班全部科目（200）', r.status_code == 200)
    r = c.get(f'/grades/api/analysis/class?exam_id={eid}&class_name=02%E7%8F%AD')
    check('导入班主任不可看别班（403）', r.status_code == 403)
    logout()

    # ===== 班主任换人同步：移除不在本次名单的旧班主任并降级 =====
    def login_admin():
        c.get('/logout', follow_redirects=True)
        r0 = c.get('/login')
        m2 = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
        rr = c.post('/login', data={'csrf_token': m2.group(1) if m2 else '',
                                    'username': 'smokeadm', 'password': 'SmokeAdm#2026'},
                    follow_redirects=True)
        # M-2：登录后会话重建，需重新取 token
        mm = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', rr.get_data(as_text=True))
        return mm.group(1) if mm else (m2.group(1) if m2 else '')

    acsrf = login_admin()
    wb4 = openpyxl.Workbook()
    ws4 = wb4.active
    ws4.append(['年级', '班级', '选科', '正班', '副班1',
                '语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理'])
    ws4.append(['2025级', '01班', '史政地', '班正师', '',
                '王老师', '李老师', '张老师', '', '', '', '赵老师', '钱老师', '孙老师'])
    io4 = io.BytesIO()
    wb4.save(io4)
    io4.seek(0)
    c.post('/grades/teachers/import/upload',
           data={'csrf_token': acsrf, 'file': (io4, '安排表v2.xlsx')},
           content_type='multipart/form-data', follow_redirects=True)
    from app.modules.grades.routes import teachers as teachers_routes
    t2 = list(teachers_routes._DRAFT.keys())[-1]
    c.post('/grades/teachers/import/confirm', data={'csrf_token': acsrf, 'token': t2},
           follow_redirects=True)
    with app.app_context():
        from app.models import UserClassLink as UCL
        bf = User.query.filter_by(real_name='班副师').first()
        bz2 = User.query.filter_by(real_name='班正师').first()
        wz = User.query.filter_by(real_name='物正师').first()
        check('换人同步：班副师关联已移除',
              UCL.query.filter_by(user_id=bf.id).count() == 0)
        check('换人同步：班副师降为任课教师',
              bf.role == 'teacher' and bf.permission_group.name == '任课教师组',
              f'{bf.role}/{bf.permission_group.name if bf.permission_group else None}')
        check('换人同步：正班保留、他班不受影响',
              UCL.query.filter_by(user_id=bz2.id, class_name='01班').count() == 1
              and UCL.query.filter_by(user_id=wz.id, class_name='02班').count() == 1)

    # ==================== AI 分析（mock 转发，不真外呼） ====================
    import json as _json
    from app.modules.grades.services import ai_service as ai_svc
    # M-2：前面切换过登录身份（会话重建），写操作前重新取 CSRF token
    _rr = c.get('/grades/exams')
    _mm = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', _rr.get_data(as_text=True))
    if _mm:
        csrf = _mm.group(1)
    ai_calls = []

    def fake_call_llm(cfg, messages):
        ai_calls.append({'cfg': cfg, 'messages': messages})
        return True, '# AI 测试报告\n\n## 总体情况\n- 参考人数 12\n- 均分正常'

    ai_svc.call_llm = fake_call_llm

    # 1) 无 Key 时 analyze → 400
    r = c.post('/grades/ai/analyze', json={'exam_id': eid}, headers={'X-CSRFToken': csrf})
    check('AI：未配置 Key 返回 400', r.status_code == 400
          and 'API Key' in r.get_json()['message'], str(r.status_code))

    # 2) 个人 Key：加密存储 + 掩码不回显明文
    r = c.post('/grades/ai/key', json={'provider': 'deepseek',
                                       'api_key': 'sk-test-secret-12345678'},
               headers={'X-CSRFToken': csrf})
    check('AI：个人 Key 保存', r.status_code == 200 and r.get_json()['success'])
    r = c.get('/grades/ai/key')
    d = r.get_json()['data']
    check('AI：掩码回显不含明文', d.get('configured') and '****' in d.get('masked', '')
          and 'sk-test-secret' not in d.get('masked', ''), str(d))
    with app.app_context():
        from app.models.grades import AiKey as AiK
        row = AiK.query.first()
        check('AI：库中密文非明文', row is not None
              and 'sk-test-secret' not in (row.api_key_enc or ''))

    # 3) 发送范围预览（权限收敛）
    r = c.get(f'/grades/ai/scope?exam_id={eid}')
    s = r.get_json()['data']
    check('AI：admin 范围=全年级12人', s['scope_students'] == 12
          and s['scope_desc'] == '2025级全年级' and s['key_source'] == 'personal')

    # 4) analyze（admin，mock）：payload 全量、报告落库
    r = c.post('/grades/ai/analyze', json={'exam_id': eid}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('AI：admin 生成成功', r.status_code == 200 and d['success']
          and 'AI 测试报告' in d['data']['content'])
    rid_admin = d['data']['report_id']
    check('AI：报告落库', rid_admin is not None)
    last = ai_calls[-1]
    payload = _json.loads(last['messages'][1]['content'].split('\n\n')[-1])
    check('AI：admin payload 12 名学生', len(payload['exam']['students']) == 12)
    check('AI：无上一场考试时 prev 为空', payload['prev'] is None)

    # 5) 全局兜底 Key：admin 配置，教师无需个人 Key
    r = c.post('/grades/ai/global-key', json={'provider': 'deepseek',
                                              'api_key': 'sk-global-abcdef'},
               headers={'X-CSRFToken': csrf})
    check('AI：全局 Key 保存', r.status_code == 200)
    # 恢复 01班数学 映射给测试任课教师（换人同步用例把其覆盖回李老师）
    with app.app_context():
        lk = (TeacherSubjectLink.query.filter_by(grade='2025级', class_name='01班',
                                                 subject='数学').first())
        ru = User.query.filter_by(username='renke01').first()
        lk.user_id = ru.id
        db.session.commit()

    # 6) 任课教师：仅本人班科（01班·数学），payload 科目仅数学
    csrf = login_as('renke01') or csrf   # M-2：登录后会话重建，token 需换新
    r = c.get(f'/grades/ai/scope?exam_id={eid}')
    s = r.get_json()['data']
    check('AI：教师范围=01班', s['scope_students'] == 6
          and s['scope_desc'] == '本人任课：01班' and s['key_source'] == 'global', str(s))
    r = c.post('/grades/ai/analyze', json={'exam_id': eid}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('AI：教师经全局 Key 生成', r.status_code == 200 and d['success'])
    rid_teacher = d['data']['report_id']
    last = ai_calls[-1]
    payload = _json.loads(last['messages'][1]['content'].split('\n\n')[-1])
    stu0 = payload['exam']['students'][0]
    check('AI：教师 payload 仅6人且科目仅数学', len(payload['exam']['students']) == 6
          and list(stu0.get('subjects', {}).keys()) == ['数学'], str(stu0))
    # 2026-10-15 用户拍板「不能把学生的总分发了，可以发名次」：payload 只带名次类字段，
    # 逐生总分（total）一律不发（改动前这里是 `'total' in stu0` —— 旧口径断言）
    check('AI：教师发送的是原始成绩字段（名次类，且已无 total）',
          'rank_dir' in stu0 and 'rank_class' in stu0 and 'name' in stu0
          and 'no' in stu0 and 'total' not in stu0, str(sorted(stu0)))
    logout()

    # 恢复测试班主任身份（换人同步用例将其随旧班主任一并移除/降级）
    with app.app_context():
        bz = User.query.filter_by(username='banzhu01').first()
        bz.role = 'homeroom_teacher'
        from app.models import PermissionGroup as PG
        from app.models import UserClassLink as UCL2
        bz.permission_group_id = PG.query.filter_by(name='班主任组').first().id
        bz.grade = '2025级'
        bz.class_name = '01班'
        if not UCL2.query.filter_by(user_id=bz.id, grade='2025级',
                                    class_name='01班').first():
            db.session.add(UCL2(user_id=bz.id, grade='2025级', class_name='01班'))
        db.session.commit()

    # 7) 班主任：仅本班
    login_as('banzhu01')
    r = c.get(f'/grades/ai/scope?exam_id={eid}')
    s = r.get_json()['data']
    check('AI：班主任范围=本班', s['scope_students'] == 6
          and s['scope_desc'] == '所辖班级：01班', str(s))
    logout()

    # 8) AI 分析列表权限：本人可见、admin 全量、跨用户 403
    login_as('renke01')
    rows = (c.get('/grades/ai/tasks').get_json() or {}).get('data') or []
    check('AI：教师工作台列表仅本人（历史报告以负数 id 呈现）',
          len(rows) == 1 and rows[0]['id'] == -rid_teacher, str(rows)[:200])
    r = c.get(f'/grades/ai/reports/{rid_admin}')
    check('AI：教师不可看他人报告（403）', r.status_code == 403)
    logout()
    acsrf = login_admin()
    # 2026-10-18 用户最终口径：AI 分析的报告/对话/任务**仅生成者本人可见，管理员也不例外**
    # （不设 ?scope=all 之类的审计后门）。
    rows = (c.get('/grades/ai/tasks').get_json() or {}).get('data') or []
    check('AI：admin 列表不含他人历史报告（仅本人可见）',
          not any((x.get('id') or 0) == -rid_teacher for x in rows), str(rows)[:200])
    r = c.get(f'/grades/ai/reports/{rid_teacher}')
    check('AI：admin 读他人报告被拒（403）', r.status_code == 403)
    rows = (c.get('/grades/ai/tasks?scope=all').get_json() or {}).get('data') or []
    check('AI：admin 加 ?scope=all 也不再放行他人（无审计后门）',
          not any((x.get('id') or 0) == -rid_teacher for x in rows), str(rows)[:200])
    # 9) 删除：管理员不能删他人的；本人可删自己的
    r = c.post(f'/grades/ai/reports/{rid_teacher}/delete', headers={'X-CSRFToken': acsrf})
    check('AI：admin 删他人报告被拒（403）', r.status_code == 403)
    with app.app_context():
        from app.models.grades import AiReport as AiR
        check('AI：他人报告未被删除', AiR.query.get(rid_teacher) is not None)
    csrf_t = login_as('renke01')
    r = c.post(f'/grades/ai/reports/{rid_teacher}/delete', headers={'X-CSRFToken': csrf_t})
    check('AI：本人可删自己的报告', r.status_code == 200)
    with app.app_context():
        from app.models.grades import AiReport as AiR
        check('AI：本人删除生效', AiR.query.get(rid_teacher) is None)
    logout()

    # ==================== AI 一句话分析（服务端任务 + 后台执行，mock 模型） ====================
    import time as _time
    from app.modules.grades.services import ai_tools as _tools
    csrf = login_admin()
    rounds = []

    def fake_chat(cfg, messages, **kw):
        """第 1 轮让模型调工具；第 2 轮返回带脏标签的 HTML（验证消毒）。"""
        rounds.append({'tools': kw.get('tools'), 'messages': messages})
        if len(rounds) == 1:
            return True, {'choices': [{'message': {
                'content': '',
                'tool_calls': [{'id': 'call_1', 'type': 'function', 'function': {
                    'name': 'class_summary',
                    'arguments': _json.dumps({'exam_id': eid})}}]}}]}, 0.1
        return True, {'choices': [{'message': {'content': (
            '<h5>结论</h5><p class="text-danger">01班总分最高</p>'
            '<script>alert(1)</script><a href="javascript:alert(1)">x</a>'
            '<img src=x onerror=alert(1)>'
            '<table class="table table-sm"><tr><td colspan="2">a</td></tr></table>')}}]}, 0.1

    ai_svc._chat_completions = fake_chat
    r = c.post('/grades/ai/ask', json={'exam_id': eid, 'question': '分析各班总分差距'},
               headers={'X-CSRFToken': csrf})
    d = r.get_json()
    check('AI任务：提交只建任务并返回 task_id',
          r.status_code == 200 and d.get('success') and d['data'].get('task_id'), str(d)[:200])
    tid = (d.get('data') or {}).get('task_id')

    status, detail = 'running', {}
    for _ in range(80):                     # 后台线程异步：轮询到结束（上限 ~16s）
        _time.sleep(0.2)
        rr = c.get(f'/grades/ai/tasks/{tid}')
        detail = (rr.get_json() or {}).get('data') or {}
        status = detail.get('status') or ''
        if status != 'running':
            break
    check('AI任务：后台执行完成', status == 'done', f'{status} {detail.get("error")}')
    check('AI任务：模型收到工具清单', bool(rounds) and bool(rounds[0]['tools'])
          and any(t['function']['name'] == 'near_line' for t in rounds[0]['tools']))
    check('AI任务：工具结果已回灌模型',
          any(m.get('role') == 'tool' and 'class_name' in (m.get('content') or '')
              for m in (rounds[1]['messages'] if len(rounds) > 1 else [])))
    check('AI任务：进度可回放（工具事件）',
          any(ev.get('type') == 'tool' and ev.get('name') == 'class_summary'
              for ev in detail.get('events') or []), str(detail.get('events'))[:200])
    html_out = detail.get('html') or ''
    check('AI任务：报告已消毒',
          '<h5>' in html_out and 'text-danger' in html_out and 'colspan' in html_out
          and '<script' not in html_out.lower() and 'onerror' not in html_out.lower()
          and 'javascript:' not in html_out.lower() and '<img' not in html_out.lower())
    check('AI任务：落库 kind=html + trace',
          bool(detail.get('report_id'))
          and 'class_summary' in _json.dumps(detail.get('trace') or []))
    r = c.get(f'/grades/ai/tasks/{tid}/stream')
    body = r.get_data(as_text=True)
    check('AI任务：SSE 订阅可用且能补发',
          r.status_code == 200 and 'text/event-stream' in (r.headers.get('Content-Type') or '')
          and '"type": "done"' in body, body[:200])
    rows = (c.get('/grades/ai/tasks?limit=50').get_json() or {}).get('data') or []
    check('AI任务：列表含本任务', any(x.get('id') == tid for x in rows), str(rows)[:200])
    check('AI任务：历史报告以负数 id 兼容进同一列表',
          any((x.get('id') or 0) < 0 for x in rows))
    hr = c.get(f'/grades/ai?exam={eid}')
    htxt = hr.get_data(as_text=True)
    check('AI任务：工作台页面元素齐备',
          hr.status_code == 200 and 'id="aiTaskList"' in htxt and 'id="aiQ"' in htxt
          and 'id="aiSubmit"' in htxt and 'id="aiStudioCfg"' in htxt
          and 'js/ai_task_ui.js' in htxt and 'js/ai_studio.js' in htxt)
    _ai_miss = [k for k in ('id="aiGenPanel"', 'id="aiGenToggle"', 'id="aiCopyPrompt"',
                            'id="aiStop"', 'id="aiReedit"', 'js/ai_prompt_gen.js')
                if k not in htxt]
    check('AI任务：工作台含提示词生成器与停止/重新编辑入口', not _ai_miss, str(_ai_miss))
    # 2026-10-14 用户口径：年级内置在考试里，面板不再有年级下拉（范围只在场次内收窄）
    check('提示词生成器：面板不再出现年级下拉（年级内置考试）',
          'id="aiGenGrade"' not in htxt)
    check('AI任务：旧历史页 302 到工作台',
          c.get('/grades/ai/reports').status_code in (301, 302))
    r = c.post(f'/grades/ai/tasks/{tid}/delete', headers={'X-CSRFToken': csrf})
    check('AI任务：可删除已结束的任务', r.status_code == 200)

    # ---- 提示词生成器（2026-10-14：范围/侧重/输出格式 → 拼装 / 模板 / 停止 / 回填） ----
    r = c.get(f'/grades/ai/prompt/options?exam_id={eid}')
    d = r.get_json()
    opt = (d or {}).get('data') or {}
    check('提示词生成器：选项接口返回年级/班级/学科/侧重',
          r.status_code == 200 and d.get('success')
          and '2025级' in (opt.get('grades') or [])
          and '01班' in (opt.get('classes') or [])
          and len(opt.get('subjects') or []) >= 2
          and '总分' not in (opt.get('subjects') or [])
          and len(opt.get('focus') or []) >= 5 and len(opt.get('output') or []) >= 2,
          str(opt)[:200])

    r = c.post('/grades/ai/prompt/build', json={
        'exam_id': eid,
        'gen': {'classes': ['01班'], 'subjects': ['数学'], 'focus': 'near_line',
                'output': 'brief', 'extra': '只陈述客观数据'}},
        headers={'X-CSRFToken': csrf})
    d = r.get_json()
    prompt = ((d or {}).get('data') or {}).get('prompt') or ''
    check('提示词生成器：拼装含范围/侧重/输出/数据纪律',
          r.status_code == 200 and '仅统计以下班级：01班' in prompt
          and '仅统计以下学科：数学' in prompt and '临界生' in prompt
          and '简要小结' in prompt and '只陈述客观数据' in prompt and '禁止编造' in prompt,
          prompt[:200])

    r = c.post('/grades/ai/prompt/build', json={
        'exam_id': eid, 'gen': {'classes': ['不存在的班']}},
        headers={'X-CSRFToken': csrf})
    check('提示词生成器：不存在的班级被拒（400）',
          r.status_code == 400 and '不在本场考试' in ((r.get_json() or {}).get('message') or ''))

    r = c.post('/grades/ai/prompt/templates', json={
        'name': '冒烟模板', 'gen': {'classes': ['01班'], 'focus': 'overall',
                                    'output': 'html', 'extra': 'x'},
        'prompt': '测试提示词'}, headers={'X-CSRFToken': csrf})
    d = r.get_json()
    tpl_id = ((d or {}).get('data') or {}).get('id')
    check('提示词生成器：模板保存', r.status_code == 200 and bool(tpl_id), str(d)[:200])
    rows = (c.get('/grades/ai/prompt/templates').get_json() or {}).get('data') or []
    check('提示词生成器：模板列表含刚保存的（选项无损）',
          any(x.get('id') == tpl_id and x.get('name') == '冒烟模板'
              and (x.get('payload') or {}).get('classes') == ['01班'] for x in rows),
          str(rows)[:200])
    r = c.post(f'/grades/ai/prompt/templates/{tpl_id}/delete',
               headers={'X-CSRFToken': csrf})
    check('提示词生成器：模板删除', r.status_code == 200)
    rows = (c.get('/grades/ai/prompt/templates').get_json() or {}).get('data') or []
    check('提示词生成器：删除后列表不含', all(x.get('id') != tpl_id for x in rows))

    # gen 留档 + 详情回填（「重新编辑提示词」的数据源）
    r = c.post('/grades/ai/ask', json={
        'exam_id': eid, 'question': '各班数学对比',
        'gen': {'classes': ['01班'], 'subjects': ['数学'], 'focus': 'class_diff',
                'output': 'html', 'extra': ''}},
        headers={'X-CSRFToken': csrf})
    tid2 = ((r.get_json() or {}).get('data') or {}).get('task_id')
    det = {}
    for _ in range(60):
        _time.sleep(0.2)
        det = (c.get(f'/grades/ai/tasks/{tid2}').get_json() or {}).get('data') or {}
        if det.get('status') != 'running':
            break
    check('提示词生成器：任务详情可回填 gen + 提示词',
          det.get('status') == 'done' and (det.get('gen') or {}).get('classes') == ['01班']
          and (det.get('gen') or {}).get('focus') == 'class_diff'
          and det.get('question') == '各班数学对比',
          str(det.get('gen'))[:200])
    r = c.post(f'/grades/ai/tasks/{tid2}/stop', headers={'X-CSRFToken': csrf})
    check('提示词生成器：已结束任务不可再停止（400）', r.status_code == 400)
    from app.modules.grades.services import ai_task_service as _ats
    check('停止：不在执行中的任务拒绝标记', _ats.request_stop(999999) is False)
    c.post(f'/grades/ai/tasks/{tid2}/delete', headers={'X-CSRFToken': csrf})

    # 汇报区：生成器面板 + 停止/重新编辑入口
    hr = c.get(f'/grades/report?exam={eid}').get_data(as_text=True)
    check('汇报区：提示词生成器 + 停止/重新编辑入口就位',
          'id="rpGenPanel"' in hr and 'id="rpGenToggle"' in hr
          and 'id="rpAiStop"' in hr and 'id="rpAiReedit"' in hr
          and 'js/ai_prompt_gen.js' in hr)
    check('汇报区：生成器面板同样无年级下拉', 'id="rpGenGrade"' not in hr)
    logout()

    # ---- 工具层权限收窄（单元级：直接打 dispatch） ----
    with app.app_context():
        lk = TeacherSubjectLink.query.filter_by(grade='2025级', class_name='01班',
                                                subject='数学').first()
        ru = User.query.filter_by(username='renke01').first()
        if lk:
            lk.user_id = ru.id
            db.session.commit()
        okc, text, summ = _tools.dispatch('class_summary', {}, ru, eid)
        d = _json.loads(text) if okc else {}
        rows = d.get('rows') or []
        classes = [x['class_name'] for x in rows]
        # 2026-10-15 用户口径：年级成绩分析结果公开 → 任课教师也拿到全年级各班 + 年级基准
        check('AI工具：任课教师拿到年级各班（聚合公开）',
              okc and len(classes) >= 2, f'{okc} {classes} {text[:200]}')
        check('AI工具：班级汇总含年级基准与班级位次',
              (d.get('grade_baseline') or {}).get('avg') is not None
              and all(r.get('class_rank') for r in rows), text[:200])
        check('AI工具：每行标明能否下钻逐生（detail_visible）',
              all(isinstance(r.get('detail_visible'), bool) for r in rows)
              and any(r.get('detail_visible') for r in rows), text[:200])
        # R1（2026-10-15）：任课＝年级×学科 → 同年级他班**所教学科**可查，但无总分；
        # 他科必须仍被拒（负向对照保留，防止"年级×学科"被误读成"整年级全科"）
        okd2, textd2, _sd2 = _tools.dispatch('class_detail',
                                             {'class_name': '02班', 'subject': '数学'},
                                             ru, eid)
        d2 = _json.loads(textd2) if okd2 else {}
        r2 = d2.get('students') or []
        check('AI工具：他班逐生明细＝仅所教学科、无总分（年级×学科）',
              okd2 and bool(r2)
              and all(set(x.get('scores') or {}) <= {'数学'} and 'total_score' not in x
                      for x in r2), textd2[:140])
        okd3, textd3, _sd3 = _tools.dispatch('class_detail',
                                             {'class_name': '02班', 'subject': '语文'},
                                             ru, eid)
        check('AI工具：他班非所教学科仍被拒（负向对照）',
              not okd3 and '权限' in textd3, textd3[:140])
        okc2, text2, _s2 = _tools.dispatch('student_trend', {'student_no': '20250101'}, ru, eid)
        check('AI工具：越权查他班学生被拒', not okc2 and '权限不足' in text2, text2[:120])
        okc3, text3, _s3 = _tools.dispatch('no_such_tool', {}, ru, eid)
        # 2026-10-18：报错文案改为"工具名不存在 + 可用工具清单"，让模型下一轮能自我纠正
        check('AI工具：未知工具被拒并给出可用工具清单',
              not okc3 and '工具名不存在' in text3 and 'exam_overview' in text3,
              text3[:120])
        # 2026-10-18「找不到考试」修复：模型猜错 exam_id 时按当前考试兜底
        okc5, text5, summ5 = _tools.dispatch('exam_overview', {'exam_id': 'exam_ui_x'},
                                             ru, eid)
        check('AI工具：乱 string exam_id 自动回退当前考试',
              okc5 and '已按当前考试' in (summ5 or ''), summ5)
        okc4, text4, _s4 = _tools.dispatch('list_my_score_scope', {}, ru, eid)
        check('AI工具：范围说明可用',
              okc4 and '2025级' in text4 and '数学' in text4, text4[:160])

    # ---- 2026-10-15 用户口径：成绩单 / 临界生"只展示他们班的" ----
    # 口径：可用班 = 账号可见班 ∩ 总分口径已授权（班主任本班 / 年级长及以上 / 校级）；
    # 仅任课映射的班不含总分口径 → 不计入（成绩单含总分与排名，不能靠它绕过总分口径）。
    with app.app_context():
        from app.modules.grades.services import scoresheet
        from app.modules.grades.services.report_warning_service import near_line_report
        exam_obj = Exam.query.get(eid)
        bz_user = User.query.filter_by(username='banzhu01').first()
        adm_user = User.query.filter_by(username='admin').first()
        bz_allowed, bz_cls = scoresheet.visible(bz_user, exam_obj)
        check('成绩单范围：班主任可用班级＝本班',
              bz_allowed == {'01班'} and bz_cls == ['01班'], f'{bz_allowed} {bz_cls}')
        t_allowed, t_cls = scoresheet.visible(ru, exam_obj)
        check('成绩单范围：任课班不含总分口径 → 无可用班（fail-closed）',
              t_allowed == set() and t_cls == [], f'{t_allowed} {t_cls}')
        a_allowed, a_cls = scoresheet.visible(adm_user, exam_obj)
        check('成绩单范围：管理员不限班', a_allowed is None and '01班' in a_cls)
        nl_all = near_line_report(eid, '', '', 10, 20)
        nl_bz = near_line_report(eid, '', '', 10, 20, allowed_classes=bz_allowed)
        nl_teach = near_line_report(eid, '', '', 10, 20, allowed_classes=t_allowed)
        all_cls = {(r.get('class_name') or '') for r in (nl_all.get('rows') or [])}
        bz_rows = nl_bz.get('rows') or []
        check('临界生：不限班时名单含数据（对照；未划线则跳过）',
              bool(nl_all.get('error')) or len(all_cls) >= 1,
              f'err={nl_all.get("error")} classes={sorted(all_cls)}')
        # 按班收窄必须真的生效（否则"只含本班"可能只是因为名单本来就只到本班）
        _other = sorted(all_cls - {'01班'})
        if _other:
            nl_other = near_line_report(eid, '', '', 10, 20, allowed_classes={_other[0]})
            _other_rows = nl_other.get('rows') or []
            check('临界生：按班收窄后只含该班（对照）',
                  bool(_other_rows) and all((r.get('class_name') or '') == _other[0]
                                            for r in _other_rows),
                  f'目标={_other[0]} rows={len(_other_rows)}')
        check('临界生：班主任名单只含本班（无跨班泄漏）',
              bool(nl_bz.get('error')) or all((r.get('class_name') or '') == '01班'
                                             for r in bz_rows),
              f'err={nl_bz.get("error")} rows={len(bz_rows)}')
        check('临界生：任课教师名单为空（fail-closed）',
              not (nl_teach.get('rows') or []), str(nl_teach.get('error') or '空'))

    # ---- 与 MCP 服务同一份实现的两个工具 + 口径一致性（防两套封装漂移） ----
    with app.app_context():
        from app.models import User as U2
        from mcp_server.tools import scores as _mcp
        admin_user = U2.query.filter_by(username='admin').first()
        okc, text, _s = _tools.dispatch('list_classes', {'exam_id': eid}, admin_user)
        check('AI工具：班级清单（直接复用 MCP 的 list_classes）',
              okc and '01班' in text and 'visible_subjects' in text, f'{okc} {text[:120]}')
        okd, textd, _sd = _tools.dispatch('class_detail',
                                          {'exam_id': eid, 'class_name': '01班'}, admin_user)
        check('AI工具：逐生明细（直接复用 MCP 的 get_class_score_detail）',
              okd and '"students"' in textd and '"student_no"' in textd, f'{okd} {textd[:120]}')
        web, _sw = _tools.HANDLERS['class_summary'](admin_user, exam_id=eid)
        mcp = _mcp._class_scores(admin_user, eid, '')
        check('AI工具：Web 与 MCP 同口径（班级数 + 首班均分逐项一致）',
              len(web['rows']) == len(mcp['classes'])
              and web['rows'][0].get('avg') == mcp['classes'][0].get('avg'),
              f"{len(web['rows'])}/{len(mcp['classes'])} "
              f"{web['rows'][0].get('avg')}/{mcp['classes'][0].get('avg')}")

    # ---- 收敛：轮数用尽时必须"只准出报告"（tool_choice=none），不再无限磨蹭 ----
    with app.app_context():
        from app.modules.grades.services import ai_agent as _agent
        from app.models import User as U3
        from app.models.grades import Exam as _Exam
        admin_user = U3.query.filter_by(username='admin').first()
        _exam = _Exam.query.get(eid)
        choices = []

        def fake_converge(cfg, messages, **kw):
            choices.append(kw.get('tool_choice'))
            if len(choices) == 1:
                return True, {'choices': [{'message': {'content': '', 'tool_calls': [
                    {'id': 'c1', 'type': 'function',
                     'function': {'name': 'class_summary',
                                  'arguments': _json.dumps({'exam_id': eid})}}]}}]}, 0.1
            return True, {'choices': [{'message': {'content': '<h5>收敛</h5>'}}]}, 0.1

        ai_svc._chat_completions = fake_converge
        evs = list(_agent.run({'provider': 'deepseek', 'model': 'x', 'api_key': 'k'},
                              admin_user, _exam, '各班差距', max_rounds=2))
        check('AI收敛：最后一轮带 tool_choice=none（强制出报告）',
              choices == [None, 'none'], str(choices))
        check('AI收敛：仍产出 html 报告',
              any(e.get('type') == 'html' and '<h5>' in (e.get('text') or '') for e in evs))

    # ---- 2026-10-13 成绩证明矩阵（单元级）：缺 term 的历史考试不能丢 ----
    # 回归的是「勾选多场考试 → 生成 PDF 只有一场」：历史导入的 exam.term 为 NULL，
    # 旧实现要求 term 非空才纳入矩阵，结果 2024/2025 级学生的历次考试被整体丢弃。
    with app.app_context():
        from datetime import date as _date
        from types import SimpleNamespace as _NS
        from app.modules.grades.services import student_service as _ss

        def _mk(i, d, ty, term=None):
            return _NS(id=i, name=f'X{i}', exam_date=_date(*d), exam_type=ty,
                       term=term, grade='2025级')

        _ex = [_mk(1, (2025, 9, 20), '月考'),        # term 缺失（历史数据）
               _mk(2, (2026, 1, 10), '期末'),
               _mk(3, (2026, 3, 20), '月考', '2025-2026')]
        _default = _ss.build_cert_matrix('2025级', _ex, {}, {}, None)
        check('证明矩阵：缺 term 的历史考试不被丢弃（默认口径每时段一场）',
              len(_default['rows']) == 2 and _default['rep_ids'] == [2, 3],
              str(_default['rep_ids']))
        _pick = _ss.build_cert_matrix('2025级', _ex, {}, {}, {1, 2})
        check('证明矩阵：同段勾选多场全部进矩阵（不再只留一场）',
              len(_pick['rows']) == 2 and _pick['exam_ids'] == [1, 2]
              and _pick['chosen'] is True, str(_pick['exam_ids']))
        check('证明矩阵：行标签＝年级学期口径（高一上·月考）',
              _pick['rows'][0]['exam_label'].startswith('高一上·月考'),
              _pick['rows'][0]['exam_label'])

print(f'\n===== 冒烟测试完成：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

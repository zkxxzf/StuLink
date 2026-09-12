# -*- coding: utf-8 -*-
"""本地演示数据：向本地 data/*.db 写入一场演示考试（2025级 01班历史 + 05班物理 真实学生）
走与线上一致的 HTTP 业务流（登录→建考试→上传→确认→分层），数据可随时在页面删除。
"""
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Student  # noqa: E402
from app.models.grades import Exam, ExamScore  # noqa: E402

app = create_app()

with app.app_context():
    # 幂等补种成绩字典/默认权限组
    from scripts.add_grades_dicts import seed_dicts, sync_default_groups
    for line in seed_dicts():
        print(' ', line)
    for line in sync_default_groups():
        print(' ', line)

    # 取真实学生：2025级 01班（史政地）+ 05班（物化生），剔除无选科
    rows = (Student.query.filter(Student.grade == '2025级',
                                 Student.class_name.in_(['01班', '05班']))
            .order_by(Student.class_name, Student.student_number).all())
    students = [s for s in rows if s.subject_selection and s.subject_selection != '不分班']
    print(f'演示学生：{len(students)} 人（01班 {sum(1 for s in students if s.class_name == "01班")} / '
          f'05班 {sum(1 for s in students if s.class_name == "05班")}）')

    import random
    rnd = random.Random(20260904)

    def score(base, span):
        return round(max(0, min(150, base + rnd.uniform(-span, span))), 1)

    def gen_subjects(s):
        sel = s.subject_selection
        out = {sub: None for sub in ['语文', '数学', '外语', '物理', '历史',
                                     '化学', '生物', '政治', '地理']}
        # 语数外 100-135 左右；方向科与再选科 55-95
        out['语文'] = score(112, 16)
        out['数学'] = score(108, 22)
        out['外语'] = score(110, 18)
        first = '物理' if sel.startswith('物') else '历史'
        out[first] = score(76, 14)
        for ch in sel[1:]:
            name = {'化': '化学', '生': '生物', '政': '政治', '地': '地理'}[ch]
            out[name] = score(74, 14)
        return out

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['学号', '姓名', '年级', '班级', '总分', '语文', '数学', '外语',
               '物理', '历史', '化学', '生物', '政治', '地理'])
    for s in students:
        vals = gen_subjects(s)
        # 总分列留空：走系统按应考 6 科累加路径
        ws.append([str(s.student_number), s.name, '2025级', s.class_name, '',
                   vals['语文'], vals['数学'], vals['外语'], vals['物理'],
                   vals['历史'], vals['化学'], vals['生物'], vals['政治'], vals['地理']])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    print('成绩 Excel 已生成')

with app.test_client() as c:
    r0 = c.get('/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r0.get_data(as_text=True))
    csrf = m.group(1)
    c.post('/login', data={'csrf_token': csrf, 'username': 'admin', 'password': 'admin123'},
           follow_redirects=True)

    # 建考试（名称=年级+日期）
    r = c.post('/grades/exams/new', data={'csrf_token': csrf, 'grade': '2025级',
                                          'exam_date': '2026-09-04',
                                          'name': '2025级2026-09-04',
                                          'exam_type': '月考'}, follow_redirects=True)
    assert '导入成绩' in r.get_data(as_text=True), '建考试失败'
    with app.app_context():
        eid = Exam.query.order_by(Exam.id.desc()).first().id
    print(f'考试已创建 id={eid}')

    # 上传
    buf.seek(0)
    r = c.post(f'/grades/exams/{eid}/import/upload',
               data={'mode': 'A', 'csrf_token': csrf, 'file': (buf, '演示成绩.xlsx')},
               content_type='multipart/form-data', follow_redirects=True)
    assert '确认导入' in r.get_data(as_text=True), '上传解析失败'
    with app.app_context():
        token = Exam.query.get(eid).import_token
    # 确认导入
    r = c.post(f'/grades/exams/{eid}/import/confirm',
               data={'csrf_token': csrf, 'token': token}, follow_redirects=True)
    assert '导入完成' in r.get_data(as_text=True), '确认导入失败'
    # 分层（四层模板应用到全部方向）
    r = c.post('/grades/api/bands/apply', json={'exam_id': eid, 'template': '4',
                                                'both': True},
               headers={'X-CSRFToken': csrf})
    assert r.get_json()['success'], '分层失败'
    with app.app_context():
        n = ExamScore.query.filter_by(exam_id=eid, subject='总分').count()
        status = Exam.query.get(eid).status
    print(f'演示考试就绪：{n} 名参考学生，状态={status}，分层=四层模板，exam_id={eid}')
    print('页面可在浏览器访问 http://localhost:5000/grades/ 查看（考试选择：2025级2026-09-04）')

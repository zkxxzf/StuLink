# -*- coding: utf-8 -*-
"""考务环节④⑤⑥⑦ 冒烟（脚本级）：任课核对 / 考试时间表 / 监考编排 / 监考表导出

覆盖：
- 环节④ 首次自动带入当前任课映射；保存/同步矩阵
- 环节⑤ 时间表新增/保存；导入 Excel 表头容错
- 环节⑥ 教师池（含无账号的校外监考员）；自动编排**同科回避**、每人每场次只监一个考场
- 环节⑦ 导出监考表 xlsx / pdf
- 页面 /grades/affairs/<aid> 200 且含 7 环节

用法：python scripts/smoke/smoke_invig.py
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
import json
import os
import re
import sys
import tempfile
from datetime import date

BASE = _ROOT
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_invig_check_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    k: 'sqlite:///' + os.path.join(_TMP, k + '.db')
    for k in ('dormitory', 'history', 'grades', 'points', 'academic', 'inspection',
              'achievement', 'forms', 'system', 'portrait', 'timetable', 'morality')
}
config_mod.Config.SECRET_KEY = 'invig-check-secret'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Student, User  # noqa: E402
from app.models.grades import (ExamAffair, AffairRoom, AffairStudent, TeacherSubjectLink,  # noqa: E402
                               AffairTeacher, AffairSchedule, AffairInvigilator,
                               AffairInvigilation, Exam, AffairInvigExclude)

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


with app.app_context():
    adm = User(username='invadm', real_name='考务管理员', role='admin', must_change_pwd=False)
    adm.set_password('InvAdm#2026')
    db.session.add(adm)

    # 教师（含学科）
    teachers = {}
    for uname, nm in [('t_chinese', '张语文'), ('t_math', '李数学'),
                      ('t_chem', '王化学'), ('t_phys', '赵物理')]:
        u = User(username=uname, real_name=nm, role='teacher', is_active=True,
                 must_change_pwd=False)
        u.set_password('x')
        db.session.add(u)
        teachers[uname] = u
    db.session.flush()

    # 学生（01/02 班）
    for i in range(8):
        db.session.add(Student(student_number=f'250{i}', name=f'学生{i}',
                               gender='男', grade='2025级',
                               class_name='01班' if i < 4 else '02班',
                               subject_selection='物化生'))

    # 任课映射：01班 语文=张、数学=李、化学=王；02班 语文=张、物理=赵
    links = [
        ('01班', '语文', teachers['t_chinese'].id),
        ('01班', '数学', teachers['t_math'].id),
        ('01班', '化学', teachers['t_chem'].id),
        ('02班', '语文', teachers['t_chinese'].id),
        ('02班', '物理', teachers['t_phys'].id),
    ]
    for cls, sub, uid in links:
        db.session.add(TeacherSubjectLink(grade='2025级', class_name=cls,
                                          subject=sub, user_id=uid, active=True))

    # v1.19.47.3 页头「本场考试」信息：绑一场考试（名字 / 性质 / 阶段 / 时间）
    # v1.19.47.3 侧栏 7 环节也依赖 affair.exam_id 反查考试 → 必须先建考试再建批次
    _exam = Exam(name='2025级2026-2027学年第一学期期中考（冒烟）', exam_kind='联考',
                 exam_type='期中考', grade='2025级', exam_date=date(2026, 10, 8),
                 term='2026-2027', status='imported')
    db.session.add(_exam)
    db.session.flush()

    affair = ExamAffair(name='冒烟考务', grade='2025级', exam_date=date(2026, 10, 8),
                        exam_id=_exam.id, status='draft')
    db.session.add(affair)
    db.session.flush()
    aid = affair.id

    # 考场 ×2
    for rn, loc in [('01', '教学楼A-101'), ('02', '教学楼A-102')]:
        db.session.add(AffairRoom(affair_id=aid, room_no=rn, location=loc,
                                  subject='默认', capacity=30))
    # 名单（参加考试）
    for i in range(8):
        db.session.add(AffairStudent(affair_id=aid, student_no=f'250{i}',
                                     name=f'学生{i}', class_name='01班' if i < 4 else '02班',
                                     subject_selection='物化生', is_attend=True))
    db.session.commit()
    print('== 数据就绪 affair_id=%d ==' % aid)

# ================= HTTP 层 =================
with app.test_client() as c:
    html = c.get('/login').get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', html)
    tok = m.group(1) if m else ''
    r = c.post('/login', data={'csrf_token': tok, 'username': 'invadm',
                               'password': 'InvAdm#2026'}, follow_redirects=False)
    check('管理员登录成功', r.status_code in (301, 302), str(r.status_code))
    # 登录后会话重建，CSRF 需重新取
    m2 = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', c.get('/').get_data(as_text=True))
    csrf = m2.group(1) if m2 else tok
    H = {'X-CSRFToken': csrf}

    # ---- 页面：7 环节 ----
    rp = c.get(f'/grades/affairs/{aid}')
    ph = rp.get_data(as_text=True)
    check('考务页面 200', rp.status_code == 200, str(rp.status_code))
    check('页面含 7 个环节标签',
          all(x in ph for x in ['① 学生名单', '② 考场设置', '③ 编排考号',
                                '④ 核对任课教师', '⑤ 考试时间表', '⑥ 编排监考表', '⑦ 导出']))
    check('页面只保留一个监考表导出入口（含工作量统计；无 PDF / 长表）',
          '导出监考表（含工作量统计）' in ph
          and '监考表 PDF' not in ph and '导出监考表 Excel' not in ph)
    # 2026-10-14 用户口径：监考表**不要列表视图** —— 只留「教师安排（学校格式）」矩阵，
    # 切换按钮 / 长表 DOM（#invigListWrap、#invigTable）与 renderInvigTable 都必须消失
    check('页面含监考矩阵视图与工作量统计入口',
          'renderInvigMatrix' in ph and '工作量统计' in ph and 'wlModal' in ph)
    check('环节⑥ 只有学校格式矩阵一种视图（无列表视图 / 无切换按钮）',
          '列表视图' not in ph and 'renderInvigTable' not in ph
          and 'invigListWrap' not in ph and 'invigTable' not in ph
          and 'name="invigView"' not in ph and 'applyInvigView' not in ph)
    # v1.19.47.3 页头与去重
    check('重复步骤条已删除（无 step-pill）', 'step-pill' not in ph)
    check('页头显示本场考试名字', '2025级2026-2027学年第一学期期中考（冒烟）' in ph)
    check('页头显示考试类型（性质/阶段）', '联考' in ph and '期中考' in ph)
    check('页头显示考试时间', '2026-10-08' in ph)
    # 2026-10-19 审计：断言原先查整页，但顶部导航的 exam-topbar-meta 也按
    # 设计显示「年级·性质·阶段」串 → 整页永远命中。改为只看侧栏标题元素。
    _tm = re.search(r'class="efn-title"[^>]*>(.*?)</div>', ph, re.S)
    _side_title = re.sub(r'\s+', ' ', _tm.group(1)).strip() if _tm else ''
    check('侧栏标题=考试名字（非「年级·性质·阶段·日期」串）',
          '2025级2026-2027学年第一学期期中考（冒烟）' in _side_title
          and '2025级·联考·期中考' not in _side_title)
    # v1.19.47.3 点环节标签不滚动：改用 history.replaceState 写 hash（不再直接赋 location.hash）
    check('点环节标签不滚动（用 history.replaceState）', 'history.replaceState' in ph)
    # v1.19.47.3 侧栏 7 环节：断言**渲染产物**（affair_steps 是 Jinja 变量名，
    # 不会出现在 HTML 里，故改数圆点 / 数「环节」文案）。
    # 默认不带 hash 打开 → 当前环节 = 第 1 步：恰好 1 个圆点带 on，其余 6 个既非 on 也非 done
    #（此刻 4/5/6 环节无数据），以此证明「只亮当前、不把前面的全亮」。
    _n_dot = ph.count('class="efn-dot')
    check('侧栏渲染 7 个环节圆点', _n_dot == 7, f'efn-dot 数={_n_dot}')
    check('侧栏圆点只亮当前 1 个（其余未完成不亮）',
          ph.count('efn-dot on"') == 1 and ph.count('efn-dot on done"') == 0,
          f'on={ph.count("efn-dot on"+chr(34))}')
    check('侧栏标注 7 环节', '/7 环节' in ph)
    check('侧栏圆点带环节标题（鼠标悬浮可见第几步叫什么）', '环节1 学生名单' in ph)
    check('选科明细默认展开（collapse show）', 'collapse show' in ph)
    check('改容量实时刷新选科明细（refreshStats）', 'refreshStats' in ph)
    check('侧栏圆点只亮当前环节（syncSidebarSteps）', 'syncSidebarSteps' in ph)
    # 2026-10-13 用户口径：⑤ 点「新增场次」不再给空白 yyyy/mm/dd —— 页面下发默认日期（本场考试日期）
    # 与每天场次上限，新行日期由 nextSchedDate() 给出（延续上一场 → 缺省考试日期 → 满员顺延）
    check('环节⑤ 新增场次默认日期已下发（本场 2026-10-08 / 每天 6 场）',
          "SCHED_DEFAULT_DATE = '2026-10-08'" in ph
          and 'const SCHED_DAILY_MAX = 6' in ph
          and 'function nextSchedDate()' in ph
          and 'sc-date" value="\' + nextSchedDate()' in ph)
    # 2026-10-14 用户口径：⑤ 内置每科时长（语文 2.5h / 数学·英语 2h / 其它 75min）→
    # 选科目自动带出结束时间；另有「按科目补时间」一键补齐入口
    _m = re.search(r'const SCHED_DURATION = (\{.*?\});', ph)
    _dur = json.loads(_m.group(1)) if _m else {}
    check('环节⑤ 内置每科时长已下发（语文 150 / 数学·英语 120 分钟）',
          _dur.get('语文') == 150 and _dur.get('数学') == 120 and _dur.get('外语') == 120,
          str(_dur))
    check('环节⑤ 自动算时间的常量 / 函数 / 一键补齐按钮都在页面上',
          "const SCHED_DEFAULT_START = '08:00'" in ph
          and 'const SCHED_DURATION_FALLBACK = 75' in ph
          and 'function schedDurationMin' in ph
          and 'function prevEndTime' in ph
          and 'function autoSchedTimes' in ph
          and 'function autoFillAllSchedTimes' in ph
          and '按科目补时间' in ph)
    # 2026-10-14 用户口径：⑤ 列序 = 日期 → 考试科目 → 开始 → 结束（先选科目，时间自动算）
    check('环节⑤ 表头列序：日期 → 科目 → 开始 → 结束 → 选科 → 备注',
          '<th style="width:150px">考试日期</th><th style="width:140px">考试科目</th>' in ph
          and '<th style="width:110px">开始</th><th style="width:110px">结束</th>' in ph)
    check('环节⑤ 新增行按同列序拼接（日期 → 科目下拉 → 开始/结束）',
          re.search(r"sc-date\" value=\"' \+ nextSchedDate\(\) \+ '\"></td>'\s*"
                    r"\+ '<td><div class=\"dropdown\">", ph) is not None)
    # 2026-10-13 用户口径：⑥ 教师池去掉「不监考」开关（改按学科分组展示），
    # 排除名单的老师 / 场次两侧都能多选，一次批量加
    check('环节⑥ 教师池按学科分组、已无「不监考」开关',
          'renderPoolBySubject' in ph and 'toggleExclude' not in ph)
    check('环节⑥ 排除名单多选控件（老师菜单 / 场次菜单 / 批量接口）',
          'exclWhoMenu' in ph and 'exclSchMenu' in ph
          and "invig/exclude/batch" in ph and 'function exclOnPick' in ph)

    # v1.19.47.3 实时统计接口：改容量后选科明细即时反映
    r = c.get(f'/grades/affairs/{aid}/stats')
    js = r.get_json() or {}
    check('实时统计接口 200', r.status_code == 200, str(r.status_code))
    check('实时统计：2 个考场 / 容量 60', js.get('rooms') == 2 and js.get('capacity') == 60,
          str({k: js.get(k) for k in ('rooms', 'capacity')}))
    check('实时统计：选科明细含 物化生 8 人', (js.get('per_sel') or {}).get('物化生', {}).get('students') == 8,
          str(js.get('per_sel')))

    # ---- 环节④ ----
    r = c.get(f'/grades/affairs/{aid}/teachers')
    d = r.get_json()
    check('环节④ 自动带入任课映射（首次自动 sync）',
          d.get('success') and len(d['data']['cells']) == 5,
          str(d.get('data', {}).get('cells', {}).keys()))
    check('环节④ 班级=01/02班', d['data']['classes'] == ['01班', '02班'],
          str(d['data']['classes']))

    # 保存：01班 化学 改派为 李数学（模拟临时换老师）
    with app.app_context():
        math_uid = User.query.filter_by(username='t_math').first().id
    r = c.post(f'/grades/affairs/{aid}/teachers/save', json={'cells': {'01班|化学': math_uid}},
               headers=H)
    check('环节④ 保存矩阵 ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))
    with app.app_context():
        t = AffairTeacher.query.filter_by(affair_id=aid, class_name='01班', subject='化学').first()
        check('环节④ 化学改由李数学（source=manual）',
              t and t.user_id == math_uid and t.source == 'manual')

    # 同步回填（覆盖）
    r = c.post(f'/grades/affairs/{aid}/teachers/sync', headers=H)
    check('环节④ 重新同步覆盖', (r.get_json() or {}).get('ok') is True)
    with app.app_context():
        t = AffairTeacher.query.filter_by(affair_id=aid, class_name='01班', subject='化学').first()
        check('环节④ 同步后化学回到王化学', t and t.teacher_name == '王化学', str(t and t.teacher_name))

    # ---- 环节⑤ ----
    r = c.post(f'/grades/affairs/{aid}/schedules/save',
               data={'subject': '语文', 'exam_date': '2026-10-08',
                     'start_time': '09:00', 'end_time': '10:30'}, headers=H)
    check('环节⑤ 新增场次 语文', (r.get_json() or {}).get('ok') is True)
    r = c.post(f'/grades/affairs/{aid}/schedules/save',
               data={'subject': '化学', 'exam_date': '2026-10-08',
                     'start_time': '14:00', 'end_time': '15:30'}, headers=H)
    check('环节⑤ 新增场次 化学', (r.get_json() or {}).get('ok') is True)
    # 导入 Excel（表头容错：日期/开始/结束/科目）
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['日期', '开始时间', '结束时间', '科目', '备注'])
    ws.append(['2026-10-09', '09:00', '10:30', '数学', ''])
    ws.append([None, None, None, '', '无科目应跳过'])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    r = c.post(f'/grades/affairs/{aid}/schedules/import',
               data={'file': (buf, 'sched.xlsx')},
               content_type='multipart/form-data', headers=H)
    jd = r.get_json() or {}
    check('环节⑤ 导入 Excel（容错表头，跳过空科目）',
          jd.get('ok') and jd.get('added') == 1 and jd.get('skipped') == 1, str(jd))
    with app.app_context():
        check('环节⑤ 场次数=3', AffairSchedule.query.filter_by(affair_id=aid).count() == 3)

    # ---- 环节⑥ 其他监考员（无账号） ----
    r = c.post(f'/grades/affairs/{aid}/invig/others/add',
               data={'name': '校外陈老师', 'subject': '外语'}, headers=H)
    check('环节⑥ 添加无账号校外监考员', (r.get_json() or {}).get('ok') is True, str(r.get_json()))
    with app.app_context():
        o = AffairInvigilator.query.filter_by(affair_id=aid, name='校外陈老师').first()
        # 「其他监考员」表刻意不含 user_id：校外老师无账号，仅本次考试使用
        check('环节⑥ 校外监考员落库（无账号字段）',
              o is not None and not hasattr(o, 'user_id'))

    # 设置每考场 2 人
    r = c.post(f'/grades/affairs/{aid}/invig/settings', data={'slots': 2}, headers=H)
    check('环节⑥ 设置每考场 2 人', (r.get_json() or {}).get('slots') == 2)

    # 自动编排
    r = c.post(f'/grades/affairs/{aid}/invig/auto', headers=H)
    jd = r.get_json() or {}
    check('环节⑥ 自动编排成功', jd.get('ok') is True, str(jd))
    check('环节⑥ 编排规模=3场×2考场×2人=12格', jd.get('rows') == 12, str(jd))
    # 2026-10-19：均衡口径改为"累计时长"，编排结果必须带回 balance 指标
    _bal = jd.get('balance') or {}
    check('环节⑥ 编排返回时长均衡指标（人均 = 总时长/人数，极差 = 最长-最短）',
          _bal.get('mean') > 0
          and _bal.get('spread') == _bal.get('max', 0) - _bal.get('min', 0),
          str(_bal))
    check('环节⑥ 首次编排不再因"旧安排为空"误报归档失败',
          '归档失败' not in str(jd.get('msg') or ''), str(jd.get('msg')))

    with app.app_context():
        scheds = {s.id: s for s in AffairSchedule.query.filter_by(affair_id=aid).all()}
        ivs = AffairInvigilation.query.filter_by(affair_id=aid).all()
        # 同科回避：化学场次不得出现 王化学
        chem_ids = [s.id for s in scheds.values() if s.subject == '化学']
        chem_names = {iv.name for iv in ivs if iv.schedule_id in chem_ids}
        check('环节⑥ 化学场次回避化学老师（王化学不排）',
              '王化学' not in chem_names, str(chem_names))
        wang = User.query.filter_by(username='t_chem').first()
        # 语文场次不得出现 张语文（教语文）
        ch_ids = [s.id for s in scheds.values() if s.subject == '语文']
        ch_names = {iv.name for iv in ivs if iv.schedule_id in ch_ids}
        check('环节⑥ 语文场次回避语文老师（张语文不排）',
              '张语文' not in ch_names, str(ch_names))
        # 每人每场次只监一个考场
        dup = False
        for sid in scheds:
            names = [iv.name for iv in ivs if iv.schedule_id == sid]
            if len(names) != len(set(names)):
                dup = True
        check('环节⑥ 同一场次一人只监一个考场', not dup)
        # 每考场恰好 2 位（本场监考位填满）
        check('环节⑥ 每考场填满 2 位',
              all(len([iv for iv in ivs if iv.schedule_id == s.id and iv.room_no == rn]) == 2
                  for s in scheds.values() for rn in ('01', '02')))

    # 2026-10-19 用户口径：同科回避 / 首场优先班主任改成页面勾选（不再写死）
    _opts = jd.get('opts') or {}
    check('环节⑥ 编排返回两个勾选状态（缺省都开）',
          _opts.get('avoid_same') is True and _opts.get('head_first') is True,
          str(_opts))
    r = c.post(f'/grades/affairs/{aid}/invig/auto',
               data={'avoid_same': '0', 'head_first': '0'}, headers=H)
    jo = r.get_json() or {}
    check('环节⑥ 可关闭同科回避与首场偏好（avoid_same=0 / head_first=0）',
          jo.get('ok') is True
          and (jo.get('opts') or {}).get('avoid_same') is False
          and (jo.get('opts') or {}).get('head_first') is False, str(jo.get('opts')))
    # 恢复缺省编排，后面的手动指派 / 工作量断言按这份结果走
    r = c.post(f'/grades/affairs/{aid}/invig/auto', headers=H)
    check('环节⑥ 恢复缺省编排（12 格）', (r.get_json() or {}).get('rows') == 12,
          str(r.get_json()))

    # 纯函数：首场格带优先名单时，优先人先拿格；不是硬约束（优先人接不了照样排）
    from app.modules.grades.services.invig_balance import Cell as _Cell, best_assign as _ba
    _cs = [_Cell(('s1', '01'), 's1', 60, frozenset({'甲', '乙', '丙', '丁'}),
                 frozenset({'丙', '丁'})),
           _Cell(('s1', '02'), 's1', 60, frozenset({'甲', '乙', '丙', '丁'}),
                 frozenset({'丙', '丁'}))]
    _plan, _gaps, _st = _ba(_cs, ['甲', '乙', '丙', '丁'], lambda a, b: False)
    check('环节⑥ 首场优先：优先人先拿到格',
          {n for _, n in _plan} == {'丙', '丁'}, str(_plan))
    _cs2 = [_Cell(('s2', '01'), 's2', 60, frozenset({'甲', '乙'}), frozenset({'丙'}))]
    _p2, _g2, _s2 = _ba(_cs2, ['甲', '乙', '丙'], lambda a, b: False)
    check('环节⑥ 首场优先不是硬约束（优先人不在候选里也能排）',
          len(_p2) == 1 and not _g2, str(_p2))

    # 班主任识别口径：role=班主任 与 UserClassLink 挂班 两种都算（多身份账号）
    from app.models import UserClassLink
    from app.modules.grades.routes.affair_invig import _head_teacher_names
    with app.app_context():
        _h1 = User(username='invhead1', real_name='钱班主任', role='homeroom_teacher',
                   is_active=True, must_change_pwd=False)
        _h1.set_password('x')
        _h2 = User(username='invhead2', real_name='孙副班', role='teacher',
                   is_active=True, must_change_pwd=False)
        _h2.set_password('x')
        db.session.add_all([_h1, _h2])
        db.session.flush()
        db.session.add(UserClassLink(user_id=_h2.id, grade='2025级', class_name='01班'))
        db.session.commit()
        _h1_id, _h2_id = _h1.id, _h2.id
        _phys_id = User.query.filter_by(username='t_phys').first().id
    _heads = _head_teacher_names([
        {'name': '钱班主任', 'user_id': _h1_id},
        {'name': '孙副班', 'user_id': _h2_id},
        {'name': '赵物理', 'user_id': _phys_id},
        {'name': '校外无账号', 'user_id': None},
    ])
    check('环节⑥ 班主任识别（role=班主任 / UserClassLink 挂班；无账号不算）',
          _heads == {'钱班主任', '孙副班'}, str(_heads))

    # 手动指定：化学场次给 王化学 → 应被回避拒绝
    with app.app_context():
        chem_sched = AffairSchedule.query.filter_by(affair_id=aid, subject='化学').first()
        csid = chem_sched.id
    r = c.post(f'/grades/affairs/{aid}/invig/set',
               data={'schedule_id': csid, 'room_no': '01', 'slot': 1, 'name': '王化学'},
               headers=H)
    check('环节⑥ 手动指派回避教师被拒', (r.get_json() or {}).get('ok') is False,
          str(r.get_json()))
    # 手动指定：派一个从未被编排的校外监考员 → 允许
    # 2026-10-13 审计修复：v1.19.49.41 起手动指派与自动编排共用
    # 「一人一场次一考场」硬约束，而自动编排已把 12 格填满，
    # 已入池的老师（含校外陈老师）都已被排到某场某考场 —— 再派他们都会被拒
    # （这是产品的正确行为，不是缺陷）。
    # 因此新增一个未入池的校外监考员，先清一格再派，验证手动指派链路可用。
    r = c.post(f'/grades/affairs/{aid}/invig/others/add',
               data={'name': '校外刘老师', 'subject': '地理'}, headers=H)
    check('环节⑥ 追加第二位校外监考员', (r.get_json() or {}).get('ok') is True,
          str(r.get_json()))
    with app.app_context():
        chem_sched2 = AffairSchedule.query.filter_by(affair_id=aid, subject='化学').first()
        chsid = chem_sched2.id
    r = c.post(f'/grades/affairs/{aid}/invig/set',
               data={'schedule_id': chsid, 'room_no': '02', 'slot': 2, 'name': ''},
               headers=H)
    check('环节⑥ 手动清空格成功', (r.get_json() or {}).get('ok') is True,
          str(r.get_json()))
    r = c.post(f'/grades/affairs/{aid}/invig/set',
               data={'schedule_id': chsid, 'room_no': '02', 'slot': 2, 'name': '校外刘老师'},
               headers=H)
    check('环节⑥ 手动指派校外监考员成功', (r.get_json() or {}).get('ok') is True,
          str(r.get_json()))

    # ---- 环节⑦ 导出（2026-10-13 用户口径：只保留**一个**监考表导出 = 教师安排矩阵 + 工作量统计） ----
    r = c.get(f'/grades/affairs/{aid}/export/invig')
    check('环节⑦ 监考表 xlsx 200（合并版）', r.status_code == 200, str(r.status_code))
    try:
        wb = openpyxl.load_workbook(io.BytesIO(r.data))
        check('环节⑦ 只有一个工作表（监考表）', wb.sheetnames == ['监考表'], str(wb.sheetnames))
        ws = wb['监考表']
        check('环节⑦ 标题写明「监考表」（同一张表内含工作量）',
              '监考表' in str(ws.cell(row=1, column=1).value or ''),
              str(ws.cell(row=1, column=1).value))
        _hdr = [ws.cell(row=5, column=i).value for i in range(1, 5)]
        # 2026-10-16 用户口径「监考表缺个科目」：左侧固定列 3 → 4（末列为「科目」）
        check('环节⑦ 上段矩阵列头（考场号/容纳人数/考场位置/科目）',
              _hdr == ['考场号', '容纳人数', '考场位置', '科目'], str(_hdr))
        _subj = ws.cell(row=6, column=4).value
        check('环节⑦ 科目列有值（默认/空 → 显示「全科」）',
              bool(_subj) and str(_subj) != '默认', str(_subj))
        check('环节⑦ 日期层=10月8日（周四）',
              ws.cell(row=2, column=5).value == '10月8日（周四）',
              str(ws.cell(row=2, column=5).value))
        check('环节⑦ 上午/下午层（09:00→上午 / 14:00→下午）',
              ws.cell(row=3, column=5).value == '上午'
              and ws.cell(row=3, column=6).value == '下午',
              f"{ws.cell(row=3, column=5).value}/{ws.cell(row=3, column=6).value}")
        _cell = ws.cell(row=6, column=5).value
        check('环节⑦ 一格并排 2 位监考（、连接）',
              bool(_cell) and '、' in str(_cell), str(_cell))
        # 下段：工作量统计（与矩阵同一张表）
        _wl_head = None
        for _r in range(1, ws.max_row + 1):
            if ws.cell(row=_r, column=3).value == '监考格数':
                _wl_head = _r
                break
        check('环节⑦ 同表下段有工作量统计表头', _wl_head is not None, f'rows={ws.max_row}')
        if _wl_head:
            _h1 = [ws.cell(row=_wl_head, column=i).value for i in range(1, 7)]
            check('环节⑦ 工作量表头（教师/身份/格数/分钟/小时/缺时间格数）',
                  _h1 == ['教师', '身份', '监考格数', '监考时长（分钟）',
                          '监考时长（小时）', '缺时间格数'], str(_h1))
            _last = [ws.cell(row=ws.max_row, column=i).value for i in range(1, 7)]
            check('环节⑦ 工作量合计行（合计 / 12 格 / 1080 分钟 / 18 小时）',
                  _last[0] == '合计' and _last[2] == 12 and _last[3] == 1080
                  and _last[4] == 18.0, str(_last))
    except Exception as e:  # noqa: BLE001
        check('环节⑦ 监考表 xlsx 可打开', False, str(e))
    # 用户口径：四个入口已合并成一个，旧的三个一律 404（防止误恢复）
    for _old in ('/export/invig/pdf', '/export/invig/matrix', '/export/invig/workload'):
        _rr = c.get(f'/grades/affairs/{aid}{_old}')
        check(f'环节⑦ 旧导出入口 {_old} 已下线（404）', _rr.status_code == 404,
              str(_rr.status_code))
    # 工作量 JSON 接口（弹窗数据源）保留
    r = c.get(f'/grades/affairs/{aid}/invig/workload')
    wl = (r.get_json() or {}).get('data') or {}
    check('环节⑥ 工作量接口 200 且有可计时场次',
          r.status_code == 200 and wl.get('has_time') is True and len(wl.get('items', [])) > 0,
          str((r.status_code, wl.get('has_time'))))
    check('环节⑥ 工作量合计 = 12 格 / 1080 分钟',
          wl.get('total_cells') == 12 and wl.get('total_minutes') == 1080,
          str((wl.get('total_cells'), wl.get('total_minutes'))))
    check('环节⑥ 工作量：每人 minutes = 格数 × 90，且明细逐格齐全',
          all(it['minutes'] == it['cells'] * 90 and len(it['detail']) == it['cells']
              for it in wl.get('items', [])),
          str([(it['name'], it['cells'], it['minutes']) for it in wl.get('items', [])]))
    # 2026-10-19：弹窗底部要报「人均 / 最长 / 最短 / 极差」，供用户直接判断公不公平
    _wb = wl.get('balance') or {}
    _n = len(wl.get('items') or []) or 1
    check('环节⑥ 工作量返回均衡指标（人均 / 极差与逐人时长自洽）',
          _wb.get('spread') == _wb.get('max', 0) - _wb.get('min', 0)
          and _wb.get('mean') == round(wl.get('total_minutes', 0) / _n),
          str(_wb))


    # ---- 环节⑥ 排除名单：批量（多老师 × 多场次，2026-10-13 用户口径） ----
    # 放在导出之后跑：本段会重跑自动编排（会被排除名单影响），不干扰上面既有的编排/导出行数断言
    with app.app_context():
        _sids = [s.id for s in AffairSchedule.query.filter_by(affair_id=aid)
                 .order_by(AffairSchedule.id).limit(2).all()]
    _batch = {'names': ['张语文', '李数学'],
              'sids': [str(_sids[0]), str(_sids[1])]}   # 同名多值 = 多选（werkzeug 会展开成重复字段）
    r = c.post(f'/grades/affairs/{aid}/invig/exclude/batch', data=_batch, headers=H)
    jd = r.get_json() or {}
    check('环节⑥ 批量排除 2 人 × 2 场 = 4 条',
          jd.get('ok') and jd.get('added') == 4, str(jd))
    r = c.post(f'/grades/affairs/{aid}/invig/exclude/batch', data=_batch, headers=H)
    jd = r.get_json() or {}
    check('环节⑥ 重复批量排除跳过（added=0 / skipped=4）',
          jd.get('ok') and jd.get('added') == 0 and jd.get('skipped') == 4, str(jd))
    r = c.post(f'/grades/affairs/{aid}/invig/exclude/batch',
               data={'names': '查无此人', 'sids': '0'}, headers=H)
    check('环节⑥ 批量排除拒收池外老师', (r.get_json() or {}).get('ok') is False)
    r = c.post(f'/grades/affairs/{aid}/invig/exclude/batch',
               data={'names': '王化学', 'sids': '0'}, headers=H)
    jd = r.get_json() or {}
    check('环节⑥ 整批次批量排除（1 人，whole=True）',
          jd.get('ok') and jd.get('added') == 1 and jd.get('whole') is True, str(jd))
    with app.app_context():
        check('环节⑥ 排除名单落库共 5 条',
              AffairInvigExclude.query.filter_by(affair_id=aid).count() == 5)
        _rno = AffairRoom.query.filter_by(affair_id=aid).order_by(
            AffairRoom.room_no).first().room_no
    # 排除名单是硬约束：手动指派同样被拒（整批不监考 / 该场次不监考）
    r = c.post(f'/grades/affairs/{aid}/invig/set',
               data={'schedule_id': _sids[0], 'room_no': _rno, 'slot': 1, 'name': '王化学'},
               headers=H)
    jd = r.get_json() or {}
    check('环节⑥ 整批排除的老师不能再被手动指派',
          jd.get('ok') is False and '不监考' in (jd.get('msg') or ''), str(jd))
    r = c.post(f'/grades/affairs/{aid}/invig/set',
               data={'schedule_id': _sids[0], 'room_no': _rno, 'slot': 1, 'name': '李数学'},
               headers=H)
    jd = r.get_json() or {}
    check('环节⑥ 被排掉该场次的老师不能再被手动指派',
          jd.get('ok') is False and '不监考' in (jd.get('msg') or ''), str(jd))
    # 排除后可用教师变少：自动编排应明确报「缺 N 格」，而不是静默留空
    r = c.post(f'/grades/affairs/{aid}/invig/auto', headers=H)
    jd = r.get_json() or {}
    check('环节⑥ 排除后容量不足：自动编排 400 + 缺格说明',
          r.status_code == 400 and jd.get('ok') is False and '格' in (jd.get('msg') or ''),
          str(jd))

    # ==================== 环节⑤ 内置每科时长：自动算结束时间（2026-10-14 用户口径） ====================
    # 规则：语文 150 分钟 · 数学/英语（外语）120 分钟 · 其它 75 分钟；一场多科取最长。
    #      手填：结束留空 → 开始 + 时长；开始也留空 → 整行留空（时间可留空）。
    #      导入（Excel / 粘贴）：只补「有开始、缺结束」，不替用户猜开始时间。
    with app.app_context():
        _a2 = ExamAffair(name='时长冒烟', grade='2025级', exam_date=date(2026, 10, 8),
                         status='draft')
        db.session.add(_a2)
        db.session.commit()
        aid2 = _a2.id

    def _save_sched(subj, start='', end='', day='2026-10-08'):
        _r = c.post(f'/grades/affairs/{aid2}/schedules/save',
                    data={'subject': subj, 'exam_date': day,
                          'start_time': start, 'end_time': end}, headers=H)
        _j = _r.get_json() or {}
        assert _j.get('ok'), _j
        return _j['id']

    def _times(sid):
        with app.app_context():
            _s = AffairSchedule.query.get(sid)
            return (_s.start_time or '', _s.end_time or '', _s.subject)

    _s = _save_sched('语文', '09:00')
    check('⑤ 语文 09:00 无结束 → 按 2.5 小时补 11:30', _times(_s) == ('09:00', '11:30', '语文'),
          str(_times(_s)))
    _s = _save_sched('数学', '14:00')
    check('⑤ 数学 14:00 无结束 → 按 2 小时补 16:00', _times(_s) == ('14:00', '16:00', '数学'),
          str(_times(_s)))
    _s = _save_sched('化学', '08:00')
    check('⑤ 化学（其它科）08:00 → 按 75 分钟补 09:15',
          _times(_s) == ('08:00', '09:15', '化学'), str(_times(_s)))
    _s = _save_sched('物理、语文', '09:00')
    check('⑤ 一场多科（物理+语文）取最长 → 09:00-11:30',
          _times(_s) == ('09:00', '11:30', '物理、语文'), str(_times(_s)))
    _s = _save_sched('语文')
    check('⑤ 开始也留空 → 整行保持留空（时间仍可不填）',
          _times(_s) == ('', '', '语文'), str(_times(_s)))
    _s = _save_sched('语文', '10:00', '10:30')
    check('⑤ 用户手填了结束 → 不覆盖（10:00-10:30）',
          _times(_s) == ('10:00', '10:30', '语文'), str(_times(_s)))
    # 粘贴导入：只给「科目 + 开始」→ 补结束；只给科目 → 不猜开始
    r = c.post(f'/grades/affairs/{aid2}/schedules/paste',
               data={'text': '化学\t11:00\n历史'}, headers=H)
    jd = r.get_json() or {}
    check('⑤ 粘贴导入：有开始缺结束 → 自动补（并回报 auto_end=1）',
          jd.get('ok') and jd.get('added') == 2 and jd.get('auto_end') == 1, str(jd))
    with app.app_context():
        _chem = AffairSchedule.query.filter_by(affair_id=aid2, subject='化学').order_by(
            AffairSchedule.id.desc()).first()
        _hist = AffairSchedule.query.filter_by(affair_id=aid2, subject='历史').first()
        check('⑤ 粘贴导入 化学 11:00 → 结束 12:15',
              _chem and (_chem.start_time, _chem.end_time) == ('11:00', '12:15'),
              f'{_chem and (_chem.start_time, _chem.end_time)}')
        check('⑤ 粘贴导入只写科目 → 不替用户猜开始时间（都留空）',
              _hist and (_hist.start_time or '', _hist.end_time or '') == ('', ''),
              f'{_hist and (_hist.start_time, _hist.end_time)}')
    # Excel 导入：只给开始时间列 → 同样自动补结束
    _wb = openpyxl.Workbook()
    _ws = _wb.active
    _ws.append(['考试日期', '开始时间', '结束时间', '考试科目'])
    _ws.append(['2026-10-09', '09:00', None, '外语'])
    _buf = io.BytesIO()
    _wb.save(_buf)
    _buf.seek(0)
    r = c.post(f'/grades/affairs/{aid2}/schedules/import',
               data={'file': (_buf, 'sched2.xlsx')},
               content_type='multipart/form-data', headers=H)
    jd = r.get_json() or {}
    check('⑤ Excel 导入：外语 09:00 → 结束 11:00（2 小时）+ auto_end=1',
          jd.get('ok') and jd.get('added') == 1 and jd.get('auto_end') == 1, str(jd))
    with app.app_context():
        _eng = AffairSchedule.query.filter_by(affair_id=aid2, subject='外语').first()
        check('⑤ Excel 导入落库 09:00-11:00',
              _eng and (_eng.start_time, _eng.end_time) == ('09:00', '11:00'),
              f'{_eng and (_eng.start_time, _eng.end_time)}')
    # 下载模板的示例行也按内置时长（语文 09:00-11:30 / 数学 14:00-16:00）
    r = c.get(f'/grades/affairs/{aid2}/schedules/template')
    _wb2 = openpyxl.load_workbook(io.BytesIO(r.data))
    _ws2 = _wb2.active
    _head = [(_ws2.cell(row=1, column=j).value or '') for j in range(1, 7)]
    # 2026-10-14 用户口径：模板列序与页面⑤ 对齐（日期 → 科目 → 开始 → 结束 → 选科 → 备注）；
    # 断言按**表头名**取列，列序再调整也不会误报
    def _tpl(row, name):
        return _ws2.cell(row=row, column=_head.index(name) + 1).value
    check('⑤ 下载模板表头与页面⑤ 同列序（日期→科目→开始→结束→选科→备注）',
          _head == ['考试日期', '考试科目', '开始时间', '结束时间', '选科', '备注'], str(_head))
    _r2 = tuple(_tpl(2, x) for x in ('考试日期', '考试科目', '开始时间', '结束时间'))
    _r3 = tuple(_tpl(3, x) for x in ('考试日期', '考试科目', '开始时间', '结束时间'))
    check('⑤ 下载模板示例行按内置时长（语文 09:00-11:30 / 数学 14:00-16:00）',
          _r2 == ('2026-10-08', '语文', '09:00', '11:30')
          and _r3 == ('2026-10-08', '数学', '14:00', '16:00'), str([_r2, _r3]))

print(f'\n===== 考务监考冒烟：{ok} 通过 / {fail} 失败 =====')
sys.exit(1 if fail else 0)

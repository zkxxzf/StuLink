# -*- coding: utf-8 -*-
"""真实浏览器点击回归：调课列表 / 详情页的 详情 · 通过 · 驳回 · 执行 四个按钮。

为什么需要它：`tests/academic_regression.py` 只到 HTTP 层，验证不了"按钮点了没反应"
这类前端问题（2026-10-10 用户实测报障——操作列内联 `event.stopPropagation()` 把
document 委托事件拦死了，通过/驳回/撤销/执行全点不动）。

跑法：py -3.12 tests/qa_20261010/e2e_swap_list_buttons.py
（用系统 Edge：playwright `channel='msedge'`，无需下载浏览器；自建独立临时库 +
本地服务，跑完删库，不碰 data/）
"""
import os
import sys
import tempfile
import threading
import time
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_e2e_')

import config as config_mod  # noqa: E402
config_mod.BASE_DIR = _TMP
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_TMP, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_TMP, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_TMP, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_TMP, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_TMP, 'points.db'),
    'academic': 'sqlite:///' + os.path.join(_TMP, 'academic.db'),
    'inspection': 'sqlite:///' + os.path.join(_TMP, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_TMP, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_TMP, 'forms.db'),
    'portrait': 'sqlite:///' + os.path.join(_TMP, 'portrait.db'),
    'system': 'sqlite:///' + os.path.join(_TMP, 'system.db'),
    'timetable': 'sqlite:///' + os.path.join(_TMP, 'timetable.db'),
}
config_mod.Config.SECRET_KEY = 'e2e-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')
config_mod.Config.SERVER_NAME = '127.0.0.1:5057'

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import PermissionGroup, User  # noqa: E402
from app.models.timetable import (PeriodDef, ScheduleEntry, ScheduleSwap,  # noqa: E402
                                  TermSchedule, get_default_periods)

PORT = 5057
PWD = 'E2E#2026x'
_ok = _fail = 0


def case(name, cond, extra=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f'[PASS] {name}')
    else:
        _fail += 1
        print(f'[FAIL] {name} {extra}')


app = create_app()
with app.app_context():
    db.create_all()
    grp = PermissionGroup.query.filter_by(name='管理员组').first()
    admin = User(username='e2e_admin', real_name='端到端', role='admin',
                 must_change_pwd=False,
                 permission_group_id=grp.id if grp else None)
    admin.set_password(PWD)
    db.session.add(admin)
    ts = TermSchedule(name='E2E 学期', school_year='2026-2027', term='第一学期',
                      status='active', is_current=True,
                      start_date=date(2026, 9, 1), total_weeks=20)
    db.session.add(ts)
    db.session.flush()
    for p in get_default_periods():
        db.session.add(PeriodDef(term_schedule_id=ts.id, **p))
    entries = []
    for g, cn, pn, subj in (('高一', '01班', 5, '语文'), ('高一', '02班', 5, '数学'),
                            ('高二', '01班', 6, '英语')):
        e = ScheduleEntry(term_schedule_id=ts.id, grade=g, class_name=cn, weekday=2,
                          period_number=pn, subject=subj, week_range='1-20',
                          entry_type='normal')
        db.session.add(e)
        entries.append(e)
    db.session.commit()
    from app.modules.academic.services import swap_service
    ok, msg, res = swap_service.bulk_apply_swap(
        applicant_uid='e2e_wang', applicant_name='王老师',
        entry_ids=[e.id for e in entries], new_weekday=6, new_period=6,
        is_permanent=True, reason='E2E 真实点击')
    batch_id = res.get('batch_id')
    entry_ids = [e.id for e in entries]      # 跨 app_context 只用快照值，别碰 ORM 对象
    _l, _pg = swap_service.get_swap_list(schedule_id=ts.id, is_reviewer=True)
    swap_id = _l[0]['id']
    print(f'[seed] {msg} batch={batch_id} 代表记录={swap_id}')

from werkzeug.serving import make_server  # noqa: E402

srv = make_server('127.0.0.1', PORT, app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.6)

try:
    from playwright.sync_api import sync_playwright

    dialogs, js_errors, bad_resources = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page()
        page.set_default_timeout(15000)
        page.on('dialog', lambda d: (dialogs.append(d.message), d.accept()))
        page.on('pageerror', lambda e: js_errors.append(str(e)))

        def _on_console(m):
            if m.type != 'error':
                return
            url = (m.location or {}).get('url', '') if m.location else ''
            if 'favicon' in url:      # 没有 favicon 只是浏览器惯例请求，不算页面问题
                return
            js_errors.append(f'{m.text} @ {url}')

        page.on('console', _on_console)
        # 资源 404（favicon 除外）也算问题：静态文件缺失会让整页 JS 死掉
        page.on('response', lambda r: bad_resources.append(f'{r.status} {r.url}')
                if r.status >= 400 else None)

        # ① 登录
        page.goto(f'http://127.0.0.1:{PORT}/login')
        page.fill('input[name=username]', 'e2e_admin')
        page.fill('input[name=password]', PWD)
        page.click('button[type=submit]')
        page.wait_for_load_state('networkidle')
        case('登录成功（跳离登录页）', '/login' not in page.url, page.url)

        page.goto(f'http://127.0.0.1:{PORT}/academic/swap')
        page.wait_for_selector('.swap-row')
        case('列表页：整批只一行 + 标注共 3 门', page.locator('.swap-row').count() == 1
             and page.locator('[data-batch="3"]').count() == 1,
             f"rows={page.locator('.swap-row').count()}")
        real_404 = [b for b in bad_resources if 'favicon' not in b]
        case('列表页：无 JS 运行时报错 / 资源 404',
             not js_errors and not real_404,
             f'js={js_errors} 404={bad_resources}'[:220])
        case('列表页：四个按钮都在',
             page.locator('.row-actions a').count() == 1
             and page.locator('.row-actions .btn-approve').count() == 1
             and page.locator('.row-actions .btn-reject').count() == 1,
             f"a={page.locator('.row-actions a').count()}")

        # ② 详情按钮
        page.click('.row-actions a')
        page.wait_for_load_state('networkidle')
        case('点「详情」能进详情页（真实跳转）',
             f'/academic/swap/{swap_id}' in page.url, page.url)
        case('详情页列出本批 3 门课', page.locator('text=统一调课批次').count() >= 1
             and page.locator('text=本批共 3 门课').count() >= 1)

        # ③ 通过按钮（一级）
        page.goto(f'http://127.0.0.1:{PORT}/academic/swap')
        page.wait_for_selector('.btn-approve')
        dialogs.clear()
        page.click('.row-actions .btn-approve')
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(600)   # 用 playwright 自己的等待，才会派发 dialog 事件
        case('点「通过」有确认框（不再点不动）',
             any('确认通过该统一调课批次（共 3 门课）' in m for m in dialogs), str(dialogs))
        with app.app_context():
            steps = [s.approval_step for s in
                     ScheduleSwap.query.filter_by(batch_id=batch_id).all()]
            case('点一次「通过」整批推进到第 2 级',
                 len(steps) == 3 and all(x == 1 for x in steps), str(steps))

        # ④ 终审：从**详情页**点通过（另一个页面的按钮也要能点）
        page.goto(f'http://127.0.0.1:{PORT}/academic/swap/{swap_id}')
        page.wait_for_selector('.btn-approve')
        page.click('.btn-approve')
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(600)   # 用 playwright 自己的等待，才会派发 dialog 事件
        with app.app_context():
            st = [s.status for s in ScheduleSwap.query.filter_by(batch_id=batch_id).all()]
            case('详情页「通过（整批 3 门）」也能点，整批 approved',
                 all(x == 'approved' for x in st) and len(st) == 3, str(st))
        page.goto(f'http://127.0.0.1:{PORT}/academic/swap')
        page.wait_for_selector('.btn-execute')
        case('已通过的行显示「执行整批」',
             '执行整批' in page.inner_text('.row-actions'))
        dialogs.clear()
        page.click('.row-actions .btn-execute')
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(900)
        case('点「执行整批」有确认框并整批执行',
             any('确认执行该统一调课批次（共 3 门课）' in m for m in dialogs)
             and any('已执行该批次 3 门课' in m for m in dialogs), str(dialogs))
        with app.app_context():
            st = [s.status for s in ScheduleSwap.query.filter_by(batch_id=batch_id).all()]
            newn = ScheduleEntry.query.filter(
                ScheduleEntry.original_entry_id.in_(entry_ids),
                ScheduleEntry.is_deleted.is_(False)).count()
            case('执行落库：3 条 executed + 新课表 3 条',
                 all(x == 'executed' for x in st) and newn == 3, f'{st} new={newn}')

        # ⑤ 第 4 个按钮「驳回」（弹模态框填理由）—— 另起一批验证
        with app.app_context():
            e2 = []
            for g, cn, pn, subj in (('高一', '01班', 4, '化学'), ('高一', '02班', 4, '生物')):
                e = ScheduleEntry(term_schedule_id=1, grade=g, class_name=cn, weekday=4,
                                  period_number=pn, subject=subj, week_range='1-20',
                                  entry_type='normal')
                db.session.add(e)
                e2.append(e)
            db.session.commit()
            _ok2, _m2, res2 = swap_service.bulk_apply_swap(
                applicant_uid='e2e_wang', applicant_name='王老师',
                entry_ids=[e.id for e in e2], new_weekday=7, new_period=10,
                is_permanent=True, reason='E2E 驳回')
            batch2 = res2.get('batch_id')
        page.goto(f'http://127.0.0.1:{PORT}/academic/swap')
        page.wait_for_selector('.btn-reject')
        page.click('.row-actions .btn-reject')
        page.wait_for_selector('#rejectModal.show')
        page.fill('#rejectNote', 'E2E 整批驳回')
        dialogs.clear()
        page.click('#rejectConfirm')
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(900)
        with app.app_context():
            st2 = [s.status for s in ScheduleSwap.query.filter_by(batch_id=batch2).all()]
            case('点「驳回」弹框填理由后整批 rejected',
                 len(st2) == 2 and all(x == 'rejected' for x in st2),
                 f'{st2}')
            case('驳回后弹窗回执说明整批（2 门课）',
                 any('已驳回该统一调课批次（2 门课）' in m for m in dialogs),
                 str(dialogs))
        browser.close()
finally:
    srv.shutdown()
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)

print(f'\n===== 真实浏览器点击验证：通过 {_ok} / 失败 {_fail} =====')
sys.exit(1 if _fail else 0)

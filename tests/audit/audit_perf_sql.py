
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 E1：SQL 条数与耗时（找 N+1）"""
import sys
import time

sys.path.insert(0, '.')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
cnt = {'n': 0}


def _hook(*args, **kwargs):
    cnt['n'] += 1


with a.app_context():
    from sqlalchemy import event
    for eng in db.engines.values():
        event.listen(eng, 'before_cursor_execute', _hook)
    u = User.query.filter_by(username='admin').first()
    uid, fp = u.id, fingerprint(u)
    from app.models.grades import Exam, ExamAffair
    exam = Exam.query.order_by(Exam.id.desc()).first()
    eid = exam.id
    aff = ExamAffair.query.order_by(ExamAffair.id.desc()).first()
    aid = aff.id
    print(f'考试 #{eid} {exam.name}；考务批次 #{aid} {aff.name}')

PAGES = [
    ('首页', '/'),
    ('学生列表', '/students/?page=1'),
    ('学生列表(带搜索)', '/students/?kw=2026'),
    ('成绩分析', '/grades/'),
    ('成绩报表', f'/grades/exams/{eid}/scores'),
    ('考务列表', '/grades/affairs'),
    ('考务详情(大名单)', f'/grades/affairs/{aid}'),
    ('考务编排', f'/grades/affairs/{aid}/arrange'),
    ('宿舍房间', '/rooms/'),
    ('宿舍看板', '/dashboard/'),
    ('用户管理', '/users/'),
    ('字典管理', '/dictionary/'),
    ('通知中心', '/notifications/'),
    ('AI 工作台', '/grades/ai'),
    ('报修/工作台', '/workbench/'),
]

rows = []
with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    for name, url in PAGES:
        try:
            cnt['n'] = 0
            t0 = time.perf_counter()
            r = c.get(url)
            dt = (time.perf_counter() - t0) * 1000
            rows.append((name, url, r.status_code, dt, cnt['n'], len(r.data) // 1024))
        except Exception as e:  # noqa: BLE001
            rows.append((name, url, 'ERR', 0, 0, 0))
            print(f'  {name} 异常 {type(e).__name__}')

rows.sort(key=lambda x: -x[4])
print(f'\n{"页面":<16}{"状态":<6}{"耗时ms":>8}{"SQL":>6}{"KB":>6}')
for name, url, sc, dt, q, kb in rows:
    flag = '  <== SQL 偏多' if q > 40 else ''
    print(f'{name:<16}{str(sc):<6}{dt:>8.0f}{q:>6}{kb:>6}{flag}')
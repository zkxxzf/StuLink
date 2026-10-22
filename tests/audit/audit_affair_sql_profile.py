
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 E3：考务详情页 106 条 SQL 的来源（按模板归类）"""
import sys
from collections import Counter

sys.path.insert(0, '.')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
seen = []


def hook(conn, cursor, statement, parameters, context, executemany):
    seen.append(' '.join(statement.split())[:110])


with a.app_context():
    from sqlalchemy import event
    from app.models.grades import ExamAffair
    for eng in db.engines.values():
        event.listen(eng, 'before_cursor_execute', hook)
    u = User.query.filter_by(username='admin').first()
    uid, fp = u.id, fingerprint(u)
    aff = ExamAffair.query.order_by(ExamAffair.id.desc()).first()
    aid = aff.id
    print(f'考务批次 #{aid} {aff.name}')

with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    seen.clear()
    r = c.get(f'/grades/affairs/{aid}')
    print(f'HTTP {r.status_code}，SQL {len(seen)} 条，{len(r.data)//1024} KB\n')

print('=== SQL 模板 TOP 10（重复次数） ===')
for stmt, n in Counter(seen).most_common(10):
    print(f'  {n:>3}×  {stmt}')
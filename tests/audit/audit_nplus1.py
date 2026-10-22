
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 E2：验证用户列表 N+1 已消除（对比 SQL 条数）"""
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
n = {'c': 0}


def hook(*args, **kwargs):
    n['c'] += 1


with a.app_context():
    from sqlalchemy import event
    for eng in db.engines.values():
        event.listen(eng, 'before_cursor_execute', hook)
    u = User.query.filter_by(username='admin').first()
    uid, fp = u.id, fingerprint(u)

with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    n['c'] = 0
    r = c.get('/users/')
    body = r.get_data(as_text=True)
    sql = n['c']
    print(f'/users/ HTTP {r.status_code}，SQL {sql} 条，{len(r.data)//1024} KB')

with a.app_context():
    total = User.query.count()
    print(f'用户数 {total}')
    # 模板渲染完整性：分组与用户名都在
    with_names = sum(1 for u in User.query.limit(50) if u.username in body)
    print(f'前 50 个用户名出现在页面：{with_names}')
    ok = sql < 20 and with_names >= 45
    print('PASS' if ok else 'FAIL',
          '用户列表 N+1 已消除' if ok else '仍有问题')
    sys.exit(0 if ok else 1)
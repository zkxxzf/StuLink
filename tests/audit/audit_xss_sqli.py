
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 C1：反射型 XSS + SQL 注入实测（真实请求，看响应是否原样回显）"""
import sys
import time

sys.path.insert(0, '.')

from app import create_app  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
XSS = [
    '<script>alert(1)</script>',
    '"><svg/onload=alert(1)>',
    "'><img src=x onerror=alert(1)>",
]
SQLI = ["' OR '1'='1", "1' UNION SELECT 1,2,3--", "%' AND 1=1--", "'; DROP TABLE x--"]

TARGETS = [
    ('学生列表', '/students/?kw={v}'),
    ('学生搜索页', '/students/search?kw={v}'),
    ('宿舍查询', '/dormitory/search?kw={v}'),
    ('字典管理', '/dictionary/?kw={v}'),
    ('用户管理', '/users/?kw={v}'),
    ('考务列表', '/grades/affairs?kw={v}'),
]

with a.app_context():
    u = User.query.filter_by(username='admin').first()
    uid, fp = u.id, fingerprint(u)

xss_hits, sqli_hits = [], []
with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp

    for label, tpl in TARGETS:
        for payload in XSS:
            url = tpl.format(v=payload)
            r = c.get(url)
            body = r.get_data(as_text=True)
            if payload in body:
                xss_hits.append((label, payload, r.status_code))
        for payload in SQLI:
            url = tpl.format(v=payload)
            t0 = time.perf_counter()
            try:
                r = c.get(url)
                dt = (time.perf_counter() - t0) * 1000
            except Exception as e:  # noqa: BLE001
                sqli_hits.append((label, payload, f'异常 {type(e).__name__}'))
                continue
            if r.status_code >= 500 or dt > 3000:
                sqli_hits.append((label, payload, f'HTTP {r.status_code} / {dt:.0f}ms'))

print('=== 反射 XSS 实测 ===')
if xss_hits:
    for label, payload, sc in xss_hits:
        print(f'  !! {label} 原样回显 {payload}  (HTTP {sc})')
else:
    print('  无（所有 payload 均被转义或未回显）')

print('\n=== SQL 注入实测 ===')
if sqli_hits:
    for label, payload, info in sqli_hits:
        print(f'  !! {label} {payload} → {info}')
else:
    print('  无（无 500、无异常延迟）')

# 排序参数是常见的拼接点
print('\n=== 排序/分页参数注入 ===')
with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    for url in ('/students/?sort=id desc,(select 1)&order=desc',
                '/grades/?sort=total_score%20desc,1&dir=asc',
                '/students/?page=1%20OR%201=1'):
        try:
            r = c.get(url)
            print(f'  {r.status_code}  {url[:70]}')
        except Exception as e:  # noqa: BLE001
            print(f'  !! 异常 {type(e).__name__}  {url[:70]}')
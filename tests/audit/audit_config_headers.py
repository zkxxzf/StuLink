
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 A：配置与响应头（DEBUG/密钥/cookie/CSP/点击劫持/版本泄露）"""
from app import create_app  # noqa: E402

a = create_app()
print('=== 关键配置 ===')
for k in ('DEBUG', 'TESTING', 'SESSION_COOKIE_SECURE', 'SESSION_COOKIE_HTTPONLY',
          'SESSION_COOKIE_SAMESITE', 'REMEMBER_COOKIE_HTTPONLY',
          'PERMANENT_SESSION_LIFETIME', 'WTF_CSRF_ENABLED', 'MAX_CONTENT_LENGTH',
          'PREFERRED_URL_SCHEME', 'JSON_AS_ASCII'):
    print(f'  {k}: {a.config.get(k)}')
sk = a.config.get('SECRET_KEY')
print(f'  SECRET_KEY: {"未设置" if not sk else str(sk)[:4] + "…(" + str(len(str(sk))) + " 字符)"}')
print(f'  绑定的库: {list(a.config.get("SQLALCHEMY_BINDS", {}).keys())}')

with a.test_client() as c:
    for path in ('/welcome', '/login'):
        r = c.get(path)
        print(f'\n=== {path} → {r.status_code} 响应头 ===')
        for h in ('Server', 'X-Frame-Options', 'X-Content-Type-Options',
                  'Content-Security-Policy', 'Referrer-Policy',
                  'Strict-Transport-Security', 'X-XSS-Protection',
                  'Permissions-Policy'):
            print(f'  {h}: {r.headers.get(h)}')
        print(f'  Set-Cookie: {(r.headers.get("Set-Cookie") or "（无）")[:140]}')

    # 版本号是否暴露在页面（攻击者可据此找漏洞）
    html = c.get('/welcome').get_data(as_text=True)
    import re
    m = re.search(r'v\d+\.\d+\.\d+[\w.]*', html)
    print(f'\n页面版本号泄露: {m.group(0) if m else "无"}')
    # 是否暴露堆栈（错误页）
    r = c.get('/grades/affairs/999999')
    body = r.get_data(as_text=True)
    leaks = [w for w in ('Traceback', 'sqlalchemy', 'werkzeug', 'File "') if w in body]
    print(f'错误页暴露技术栈: {leaks or "无"} (HTTP {r.status_code})')
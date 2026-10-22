# -*- coding: utf-8 -*-
"""诊断：liuwen 打开学生编辑页的真实响应（是否 200 / 是否含表单）"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from app import create_app  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
with a.app_context():
    u = User.query.filter_by(username='liuwen').first()
    uid, fp = u.id, fingerprint(u)
    from app.models.student import Student
    s = Student.query.filter_by(grade='2024级', class_name='01班').first()
    sid, phone = s.id, (s.phone1 or '')

with a.test_client() as c:
    with c.session_transaction() as sess:
        sess['_user_id'] = str(uid)
        sess['_fresh'] = True
        sess['_pwd_fp'] = fp
    r = c.get(f'/students/{sid}/edit')
    html = r.get_data(as_text=True)
    print(f'HTTP {r.status_code}，{len(html)} 字节')
    print(f'  含 <form:', '<form' in html)
    print(f'  含 name="phone1":', 'name="phone1"' in html)
    print(f'  含 data-phone-locked:', 'data-phone-locked' in html)
    print(f'  含完整号码 {phone}:', phone in html)
    print(f'  含脱敏 number:', 'phone1' in html)
    if r.status_code == 302:
        print('  Location:', r.headers.get('Location'))

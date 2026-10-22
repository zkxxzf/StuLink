# -*- coding: utf-8 -*-
"""诊断：licancan 的课表权限/可见年级，以及字典删除 500 的堆栈"""
import os
import sys
import traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import re  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
a.config['PROPAGATE_EXCEPTIONS'] = True

with a.app_context():
    from app.modules.academic.services.access_scope import visible_academic_grades
    u = User.query.filter_by(username='licancan').first()
    print(f'licancan id={u.id} role={u.role}')
    for p in ('academic.timetable', 'academic.view', 'academic.edit'):
        print(f'  has_perm({p}) = {u.has_perm(p)}')
    print(f'  visible_academic_grades = {visible_academic_grades(u)}')
    from app.modules.grades.services.scope import visible_grades
    print(f'  visible_grades = {visible_grades(u)}')
    uid, fp = u.id, fingerprint(u)
    from app.models.dictionary import DictCategory, DictItem
    cat = DictCategory.query.first()
    tmp = DictItem(category_id=cat.id, value='TMP_AUDIT_DICT2', sort_order=999,
                   is_active=True)
    db.session.add(tmp)
    db.session.commit()
    item_id = tmp.id
    print(f'  临时字典项 {item_id}，category.name={getattr(cat, "name", "无此字段")}')

with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    html = c.get('/academic/timetable').get_data(as_text=True)
    m = re.search(r'csrfToken\s*=\s*"([^"]+)"', html)
    csrf = m.group(1) if m else ''
    print(f'  csrf={"有" if csrf else "无"}')
    try:
        r = c.post(f'/dictionary/{item_id}/delete', headers={'X-CSRFToken': csrf})
        print(f'  删除字典 → HTTP {r.status_code}')
    except Exception:  # noqa: BLE001
        print('  删除字典抛异常：')
        traceback.print_exc(limit=6)

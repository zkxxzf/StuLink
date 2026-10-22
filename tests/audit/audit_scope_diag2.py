# -*- coding: utf-8 -*-
"""诊断 2：谁有课表权限、admin 删字典为何 500"""
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
    rows = [(u.username, u.role, sorted(visible_academic_grades(u) or ['ALL']))
            for u in User.query.all() if u.has_perm('academic.timetable')]
    print(f'有 academic.timetable 的账号 {len(rows)} 个：')
    for r in rows[:10]:
        print('   ', r)
    if not rows:
        print('   （无普通账号，只有管理员链路可用）')

    admin = User.query.filter_by(username='admin').first()
    uid, fp = admin.id, fingerprint(admin)

with a.test_client() as c:
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    html = c.get('/dictionary/').get_data(as_text=True)
    m = re.search(r'csrfToken\s*=\s*"([^"]+)"', html)
    csrf = m.group(1) if m else ''
    from app.models.dictionary import DictCategory, DictItem
    with a.app_context():
        cat = DictCategory.query.first()
        tmp = DictItem(category_id=cat.id, value='TMP_AUDIT_DICT3',
                       sort_order=999, is_active=True)
        db.session.add(tmp)
        db.session.commit()
        item_id = tmp.id
    print(f'临时字典项 {item_id}')
    try:
        r = c.post(f'/dictionary/{item_id}/delete', headers={'X-CSRFToken': csrf})
        print(f'删除字典 → HTTP {r.status_code}')
    except Exception:  # noqa: BLE001
        print('删除抛异常（前 8 帧）：')
        traceback.print_exc(limit=8)

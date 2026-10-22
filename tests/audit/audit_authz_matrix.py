
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 B3：多角色越权实测（IDOR + 写操作 + 只读角色）"""
import re
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402  临时靶子需要 add/commit
from app.models import User  # noqa: E402
from app.utils.session_guard import fingerprint  # noqa: E402

a = create_app()
a.login_manager.session_protection = None
findings = []


def login(c, username):
    with a.app_context():
        u = User.query.filter_by(username=username).first()
        if not u:
            return None
        uid, fp = u.id, fingerprint(u)
    with c.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
        s['_pwd_fp'] = fp
    html = c.get('/grades/').get_data(as_text=True)
    m = re.search(r'csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


def probe(c, csrf, label, method, url, data=None, expect_block=True,
          must_not_contain=None):
    kw = {'headers': {'X-CSRFToken': csrf or ''}}
    if method == 'GET':
        r = c.get(url)
    else:
        r = c.post(url, data=data or {}, **kw)
    body = r.get_data(as_text=True)
    leak = bool(must_not_contain) and must_not_contain in body
    risky = (r.status_code == 200) and leak
    tag = 'LEAK' if risky else ('blocked' if r.status_code in (302, 400, 403, 404) else 'ok')
    print(f'  {r.status_code:<5}{tag:<8}{label:<34}{url}')
    if risky:
        findings.append(f'{label} | {method} {url} | HTTP 200 且含「{must_not_contain}」')
    return r, body


# 2026-10-18：所有写探测都打**临时靶子**，绝不动真实学生 / 真实考试。
# 之前直接对 578（真实学生）发改名/删除/转班、对考试 34 发改名 —— 一旦某个
# 校验有洞，真实数据就被改了且无法恢复。
with a.app_context():
    from app.models.student import Student
    victim = Student.query.filter_by(student_number='TMP_AUTHZ_VICTIM').first()
    if victim is None:
        victim = Student(student_number='TMP_AUTHZ_VICTIM', name='审计越权靶子',
                         gender='男', grade='2024级', class_name='02班')
        db.session.add(victim)
        db.session.commit()
    VSID, VNAME = victim.id, victim.name
    not_exam, not_room, not_user = 999999, 9999999, 9999999
print(f'   临时靶子学生 #{VSID}（{VNAME}，2024级02班）')

print(f'=== 1) 班主任 liuwen（2024级01班）看/改别班学生 {VSID} ===')
with a.test_client() as c:
    csrf = login(c, 'liuwen')
    print(f'  csrf={"有" if csrf else "无"}')
    probe(c, csrf, '别班学生详情', 'GET', f'/students/{VSID}', must_not_contain=VNAME)
    probe(c, csrf, '别班学生编辑页', 'GET', f'/students/{VSID}/edit',
          must_not_contain=VNAME)
    probe(c, csrf, '别班学生编辑提交', 'POST', f'/students/{VSID}/edit',
          data={'name': '被篡改'}, expect_block=True)
    probe(c, csrf, '别班学生详情接口', 'GET', f'/students/{VSID}?format=json',
          must_not_contain=VNAME)
    probe(c, csrf, '学生列表按别班筛选', 'GET',
          '/students/?grade=2024级&class_name=02班', must_not_contain=VNAME)
    probe(c, csrf, '别班成绩单', 'GET', f'/grades/exams/{not_exam}/scores?class=02班',
          must_not_contain=VNAME)
    probe(c, csrf, '考试配置修改(不存在考试)', 'POST',
          f'/grades/exams/{not_exam}/update', data={'name': '篡改'})

print('\n=== 2) 任课教师 licancan 访问别班学生 ===')
with a.test_client() as c:
    csrf = login(c, 'licancan')
    probe(c, csrf, '别班学生详情(任课)', 'GET', f'/students/{VSID}',
          must_not_contain=VNAME)
    probe(c, csrf, '改分接口(任课)', 'POST',
          f'/grades/exams/{not_exam}/score/{not_exam}', data={'score': 150})

print('\n=== 3) 全校只读 bzr001 的写操作（均打不存在的 id） ===')
with a.test_client() as c:
    csrf = login(c, 'bzr001')
    probe(c, csrf, '删宿舍(只读)', 'POST', f'/rooms/{not_room}/delete')
    probe(c, csrf, '重置密码(只读)', 'POST', f'/users/{not_user}/reset-password')
    probe(c, csrf, '改用户(只读)', 'POST', f'/users/{not_user}/edit',
          data={'real_name': 'x'})
    probe(c, csrf, '删学生(只读)', 'POST', f'/students/{not_room}/delete')
    probe(c, csrf, '删权限组(只读)', 'POST', f'/perm-groups/{not_room}/delete')
    probe(c, csrf, '删宿舍分配(只读)', 'POST', '/dormitory/assignments/unassign',
          data={'ids': str(not_room)})

print('\n=== 4) 班主任删除别班学生（应仅限本班） ===')
with a.test_client() as c:
    csrf = login(c, 'liuwen')
    probe(c, csrf, '删别班学生', 'POST', f'/students/{VSID}/delete')
    probe(c, csrf, '转班别班学生', 'POST', f'/students/{VSID}/transfer',
          data={'class_name': '03班'})

# 清理临时靶子（用独立连接，避免同 session 先读后写的 SQLite 锁问题）
import sqlite3  # noqa: E402

_con = sqlite3.connect('data/system.db', timeout=15)
_con.execute('DELETE FROM students WHERE student_number=?', ('TMP_AUTHZ_VICTIM',))
_con.commit()
_con.close()

print('\n===== 越权发现 =====')
if findings:
    for f in findings:
        print('  !!', f)
else:
    print('  无（全部被拦或未泄露目标数据）')
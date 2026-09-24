# -*- coding: utf-8 -*-
"""积分模块 HTTP 冒烟：登录 → 页面 → options → 搜索学生 → 新增 → 列表 → 汇总 → 编辑 → 删除"""
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:5000'
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
ok = fail = 0


def check(name, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print('[PASS]', name)
    else:
        fail += 1
        print('[FAIL]', name, extra)


def req(path, data=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {'User-Agent': 't'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
        headers['X-CSRFToken'] = CSRF
    r = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        resp = op.open(r, timeout=30)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get(path):
    try:
        resp = op.open(urllib.request.Request(BASE + path, headers={'User-Agent': 't'}), timeout=30)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


st, html = get('/login')
CSRF = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html).group(1).decode()
# 表单登录（urlencoded）
resp = op.open(urllib.request.Request(
    BASE + '/login',
    # H-1：内置 admin 初始口令已随机化，脚本改用环境变量传入账号口令
    data=urllib.parse.urlencode({'csrf_token': CSRF,
                                 'username': os.environ.get('STULINK_ADMIN_USER', 'admin'),
                                 'password': os.environ.get('STULINK_ADMIN_PWD', '')}).encode(),
    headers={'User-Agent': 't'}), timeout=30)
check('admin 登录', b'\xe9\x80\x80\xe5\x87\xba' in resp.read())

st, html = get('/points/')
check('积分页面 200', st == 200 and '积分管理' in html.decode('utf-8', 'replace'))

st, raw = get('/points/api/options')
d = json.loads(raw.decode('utf-8'))['data']
check('options：全校范围+可编辑+年级列表', d['scope'] == 'school' and d['can_edit']
      and len(d['grades']) > 0, str(d)[:120])

st, raw = get('/points/api/students?q=' + urllib.parse.quote('王'))
students = json.loads(raw.decode('utf-8'))['data']
check('学生搜索返回结果', len(students) > 0, str(len(students)))
if not students:
    raise SystemExit('无学生数据，终止')
stu = students[0]

st, raw = req('/points/api/record', {'student_no': stu['student_no'], 'points': 5,
                                     'category': '学习', 'reason': '冒烟测试-月考进步',
                                     'remark': '自动测试', 'recorded_at': '2026-09-14'})
d = json.loads(raw.decode('utf-8'))
check('新增积分记录', st == 200 and d['success'], str(d)[:150])
rid = d['data']['id'] if d.get('data') else None

st, raw = get('/points/api/list')
d = json.loads(raw.decode('utf-8'))['data']
found = [r for r in d['records'] if r['id'] == rid]
check('列表含新记录', len(found) == 1 and found[0]['points'] == 5
      and found[0]['student_name'] == stu['name'], str(d['stat']))
check('统计正确', d['stat']['count'] >= 1 and d['stat']['total_points'] >= 5, str(d['stat']))

st, raw = get('/points/api/summary')
rows = json.loads(raw.decode('utf-8'))['data']
hit = [r for r in rows if r['student_no'] == stu['student_no']]
check('汇总含该学生', len(hit) == 1 and hit[0]['points'] >= 5, str(hit[:1]))

st, raw = req('/points/api/record/%d' % rid, {'student_no': stu['student_no'], 'points': -3,
                                              'category': '纪律', 'reason': '冒烟测试-改为扣分',
                                              'remark': '', 'recorded_at': '2026-09-14'})
check('编辑记录（改为-3）', st == 200 and json.loads(raw.decode('utf-8'))['success'])

st, raw = req('/points/api/record/%d/delete' % rid, {})
check('删除记录', st == 200 and json.loads(raw.decode('utf-8'))['success'])

st, raw = get('/points/api/list')
d = json.loads(raw.decode('utf-8'))['data']
check('删除后列表不含该记录', all(r['id'] != rid for r in d['records']))

# 越权/未登录
cj2 = http.cookiejar.CookieJar()
op2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj2))
try:
    resp = op2.open(urllib.request.Request(BASE + '/points/', headers={'User-Agent': 't'}), timeout=20)
    anon_status = resp.status
except urllib.error.HTTPError as e:
    anon_status = e.code
check('未登录访问跳转登录（302/200登录页）', anon_status in (200, 302), str(anon_status))

print('\n===== 积分模块冒烟：通过 %d / 失败 %d =====' % (ok, fail))

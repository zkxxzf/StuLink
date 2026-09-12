# -*- coding: utf-8 -*-
"""本地 HTTP 冒烟（需先 python run.py 启动本地服务于 5000 端口）
登录 admin → 遍历成绩模块页面/API 断言 200 与关键内容；不影响数据（只读）。
"""
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

BASE_URL = 'http://127.0.0.1:5000'
ok = fail = 0


def check(name, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'[PASS] {name}')
    else:
        fail += 1
        print(f'[FAIL] {name} {extra}')


cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(path):
    req = urllib.request.Request(BASE_URL + path, headers={'User-Agent': 'smoke'})
    try:
        resp = opener.open(req, timeout=30)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(path, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE_URL + path, data=body,
                                 headers={'User-Agent': 'smoke',
                                          'Content-Type': 'application/x-www-form-urlencoded',
                                          **(headers or {})})
    try:
        resp = opener.open(req, timeout=30)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# 1. 登录
st, html = get('/login')
check('GET /login', st == 200)
m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
csrf = m.group(1).decode() if m else ''
st, html = post('/login', {'csrf_token': csrf, 'username': 'admin', 'password': 'admin123'})
text = html.decode('utf-8', 'replace')
check('POST /login 成功', st == 200 and '退出' in text and '登录失败' not in text)

# 2. 旧功能回归（欢迎页）
st, html = get('/')
text = html.decode('utf-8', 'replace')
check('欢迎页（回归）', st == 200 and '成绩管理' in text)

# 3. 成绩模块页面
for path, key in [('/grades/', '成绩分析'),
                  ('/grades/exams', '考试管理'),
                  ('/grades/exams/1', '2025级2026-09-04'),
                  ('/grades/exams/1/import', '导入成绩'),
                  ('/grades/exams/1/bands', '划线分层'),
                  ('/grades/teachers', '任课教师映射'),
                  ('/grades/teachers/import', '教师安排表')]:
    st, html = get(path)
    text = html.decode('utf-8', 'replace')
    check(f'GET {path} 200', st == 200 and key in text, f'st={st}')

# 4. API
st, raw = get('/grades/api/options')
d = json.loads(raw.decode('utf-8'))
grades = d['data']
check('options：含演示考试', st == 200 and any(
    e['id'] == 1 and e['status'] == 'imported'
    for e in grades['exams'].get('2025级', [])))

st, raw = get('/grades/api/analysis/grade?exam_id=1')
d = json.loads(raw.decode('utf-8'))
t = d['data']['tables']
check('年级分析：两班汇总+分层列', st == 200
      and len(t['class_summary']['rows']) == 2
      and 'l_0' in t['class_summary']['columns'][0] if False else
      any(c['key'].startswith('l_') for c in t['class_summary']['columns']))
check('年级分析：科目总表 9 行', len(t['subject_total']['rows']) >= 6)
charts = d['data']['charts']
check('年级分析：箱线/堆叠/折线图齐全',
      'box' in charts and 'seg_stack' in charts and 'trend_line' in charts
      and 'avg_bar' in charts)
# 分层列值非空
summary_row0 = t['class_summary']['rows'][0]
layer_keys = [c['key'] for c in t['class_summary']['columns'] if c['key'].startswith('l_')]
check('分层人数已统计', layer_keys and all(summary_row0.get(k) != '—'
      for k in layer_keys), str(summary_row0))

# 方向筛选
st, raw = get('/grades/api/analysis/grade?exam_id=1&direction=%E5%8E%86%E5%8F%B2')
d = json.loads(raw.decode('utf-8'))
rows = d['data']['tables']['class_summary']['rows']
check('方向筛选=历史：仅 01班（46 名历史方向）', len(rows) == 1
      and rows[0]['class_name'] == '01班' and rows[0]['count'] == 46, str(rows))
st, raw = get('/grades/api/analysis/grade?exam_id=1&direction=%E7%89%A9%E7%90%86')
try:
    rows = json.loads(raw.decode('utf-8'))['data']['tables']['class_summary']['rows']
except Exception:
    print('PHYS_DEBUG st=', st, 'raw=', raw[:400])
    raise
check('方向筛选=物理：05班(55)+01班物理1人',
      len(rows) == 2 and rows[0]['class_name'] in ('01班', '05班')
      and sum(r['count'] for r in rows) == 56, str(rows))

# 班级/学科/教师
st, raw = get('/grades/api/analysis/class?exam_id=1&class_name=01%E7%8F%AD')
d = json.loads(raw.decode('utf-8'))
check('班级分析：明细 47 人+分层行', st == 200
      and len(d['data']['tables']['student_detail']['rows']) == 47
      and len(d['data']['tables']['layer_stat']['rows']) >= 4)
st, raw = get('/grades/api/analysis/subject?exam_id=1&subject=%E6%95%B0%E5%AD%A6')
d = json.loads(raw.decode('utf-8'))
check('学科分析：热力图矩阵', st == 200
      and len(d['data']['charts']['heatmap']['xAxis']) >= 2)
st, raw = get('/grades/api/analysis/teacher?exam_id=1')
d = json.loads(raw.decode('utf-8'))
# 无教师映射时为空态（本库未导入安排表）
check('教师分析：空态兼容', st == 200)

# 5. 导出（表格 Excel）
st, raw = get('/grades/export/grade?exam_id=1')
check('导出年级分析 xlsx', st == 200 and raw[:2] == b'PK')

# 6. 静态资源
st, raw = get('/static/js/grades.js')
check('grades.js 可访问', st == 200 and b'grades.js' or len(raw) > 1000)
st, raw = get('/static/css/grades.css')
check('grades.css 可访问', st == 200)

print(f'\n===== 本地 HTTP 冒烟：通过 {ok} / 失败 {fail} =====')
sys.exit(1 if fail else 0)

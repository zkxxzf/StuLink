# -*- coding: utf-8 -*-
"""用真实《教师安排表.xlsx》（含班主任列，2024/2025/2026 三级段）对本地库做端到端导入演示：
任课绑定 + 班主任自动开户 + 与 班型设置(class-profile) 的 UserClassLink 同步。
"""
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

BASE_URL = 'http://127.0.0.1:5000'
FILE = r'd:\Users\lenovo\Desktop\StuLink学生数据20260813\教师安排表.xlsx'

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(path):
    try:
        r = opener.open(urllib.request.Request(BASE_URL + path, headers={'User-Agent': 't'}), timeout=60)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def post(path, data=None, files=None, headers=None):
    body = urllib.parse.urlencode(data or {}).encode()
    h = {'User-Agent': 't', **(headers or {})}
    req = urllib.request.Request(BASE_URL + path, data=body, headers=h)
    if files:
        # multipart/form-data 手工构造
        boundary = '----smokeboundary123'
        parts = []
        for k, v in (data or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
        fname = files[0]
        with open(files[1], 'rb') as f:
            fdata = f.read()
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                     f'filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n')
        body = ''.join(parts).encode() + fdata + f'\r\n--{boundary}--\r\n'.encode()
        h['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        req = urllib.request.Request(BASE_URL + path, data=body, headers=h)
    try:
        r = opener.open(req, timeout=120)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# 登录
st, html = get('/login')
m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', html)
csrf = m.group(1).decode()
st, _ = post('/login', {'csrf_token': csrf, 'username': 'admin', 'password': 'admin123'})
assert '退出' in _.decode('utf-8', 'replace') or st == 200, '登录失败'

# 上传真实安排表
st, html = post('/grades/teachers/import/upload',
                data={'csrf_token': csrf}, files=('教师安排表.xlsx', FILE))
text = html.decode('utf-8', 'replace')
print('上传状态:', st)
print('页面含 班主任/任课 标记:', '班主任' in text and '任课' in text)
print('自动开户标记:', '自动开户' in text)
# 统计表内行数
print('任课记录数/班主任人次（从页面抓取）:', re.findall(r'任课记录\s*(\d+).*?班主任\s*(\d+)', text))

# 确认导入（token 从页面）
m = re.search(r'name="token" value="([0-9a-f-]+)"', text)
if not m:
    print('未找到 token，页面前 500 字：', text[:500])
    sys.exit(2)
token = m.group(1)
st, html = post('/grades/teachers/import/confirm',
                data={'csrf_token': csrf, 'token': token})
text = html.decode('utf-8', 'replace')
print('确认导入状态:', st)
for kw in ['导入完成', '班主任同步', '移除旧班主任', '新开账号']:
    print(f'  含[{kw}]:', kw in text)

# 与 class-profile（班型设置）同步校验：每班班主任 API
st, raw = get('/class-profile/teachers/2025级/01班')
try:
    d = json.loads(raw.decode('utf-8'))
    print('class-profile 01班 班主任:', d.get('teachers'))
except Exception as e:
    print('class-profile API 异常:', st, raw[:200])

print('完成。浏览器可访问 /class-profile/（班型设置）查看各班主任，以及 /grades/teachers 查看任课矩阵。')

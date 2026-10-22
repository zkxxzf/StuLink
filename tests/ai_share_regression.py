# -*- coding: utf-8 -*-
"""AI 报告分享链接回归（2026-10-22）。

覆盖的口径（改动分享链路前先跑这个）：
  1. 只有报告所有者能生成/查询/撤销链接，别人（含无 grades.view 账号）一律 403；
  2. 未登录打开 `/s/<token>` → 跳登录（带 next），登录后自动回到分享页；
  3. 登录账号（不要求成绩权限）可看报告，但看不到站内导航/提问区/其它报告；
  4. 一份报告同时只有一条有效链接（重复生成复用同一 token）；
  5. 撤销 / 到期 / 报告删除 → 410 + 对应文案；
  6. 访问计数同一会话只计一次；响应带 noindex 与 no-store；
  7. 分享页对报告正文再做一次白名单消毒（`<script>` 不会出现）。

运行：python tests/ai_share_regression.py
"""
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_TMP = tempfile.mkdtemp(prefix='stulink_ai_share_')

import config as config_mod  # noqa: E402

config_mod.BASE_DIR = _TMP
_DATA = os.path.join(_TMP, 'data')
os.makedirs(_DATA, exist_ok=True)
config_mod.Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(_DATA, 'system.db')
config_mod.Config.SQLALCHEMY_BINDS = {
    'dormitory': 'sqlite:///' + os.path.join(_DATA, 'dormitory.db'),
    'history': 'sqlite:///' + os.path.join(_DATA, 'history.db'),
    'grades': 'sqlite:///' + os.path.join(_DATA, 'grades.db'),
    'points': 'sqlite:///' + os.path.join(_DATA, 'points.db'),
    'morality': 'sqlite:///' + os.path.join(_TMP, 'morality.db'),
    'academic': 'sqlite:///' + os.path.join(_DATA, 'academic.db'),
    'inspection': 'sqlite:///' + os.path.join(_DATA, 'inspection.db'),
    'achievement': 'sqlite:///' + os.path.join(_DATA, 'achievement.db'),
    'forms': 'sqlite:///' + os.path.join(_DATA, 'forms.db'),
    'portrait': 'sqlite:///' + os.path.join(_DATA, 'portrait.db'),
    'system': 'sqlite:///' + os.path.join(_DATA, 'system.db'),
    'timetable': 'sqlite:///' + os.path.join(_DATA, 'timetable.db'),
    'timed': 'sqlite:///' + os.path.join(_DATA, 'timed.db'),
}
config_mod.Config.SECRET_KEY = 'ai-share-regression-secret'
config_mod.Config.UPLOAD_FOLDER = os.path.join(_TMP, 'uploads')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402
from app.models.grades import AiReport, AiReportShare, AiTask, Exam  # noqa: E402

app = create_app()
PWD = 'ShareTest#2026'
_ok = 0
_fail = 0


def case(name, cond, extra=''):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f'[PASS] {name}')
    else:
        _fail += 1
        print(f'[FAIL] {name} {extra}')


def get_csrf(c, path='/login'):
    html = c.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'var\s+csrfToken\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ''


def login(c, username, password):
    csrf = get_csrf(c)
    return c.post('/login', data={'csrf_token': csrf, 'username': username,
                                  'password': password}, follow_redirects=True)


def seed():
    """两个账号（owner=admin，reader=普通教师，无权限组）+ 一场考试 + 两份报告。"""
    with app.app_context():
        db.create_all()
        admin = User(username='share_owner', real_name='分享者', role='admin',
                     must_change_pwd=False)
        admin.set_password(PWD)
        reader = User(username='share_reader', real_name='查看者', role='teacher',
                      must_change_pwd=False)
        reader.set_password(PWD)
        db.session.add_all([admin, reader])
        db.session.commit()
        exam = Exam(name='分享回归测试考试', grade='2024级', exam_date=date(2026, 10, 1),
                    exam_type='月考', status='imported')
        db.session.add(exam)
        db.session.commit()
        html = ('<h4>高三年级成绩分析</h4><p>本班均分 <strong>512.4</strong>。</p>'
                '<h5>关键结论</h5><ul><li>物理方向较强</li></ul>'
                '<table><thead><tr><th>班级</th><th>均分</th></tr></thead>'
                '<tbody><tr><td>01班</td><td>520</td></tr></tbody></table>'
                '<script>alert(1)</script>')
        rep = AiReport(user_id=admin.id, exam_id=exam.id, grade='2024级',
                       scope_desc='全年级', content=html, kind='html',
                       provider='deepseek', model='test-model')
        md = AiReport(user_id=admin.id, exam_id=exam.id, grade='2024级',
                      scope_desc='全年级', content='# 旧口径报告\n\n- 第一条结论',
                      kind='md')
        # 2026-10-22 用户截图里的历史脏数据：kind 标成 html、正文却是 Markdown
        dirty = AiReport(user_id=admin.id, exam_id=exam.id, grade='2024级',
                         scope_desc='全年级',
                         content='# 2026级 10月月考（高一上）整体分析\n\n'
                                 '**数据完整性**：9 科全部导入。\n\n'
                                 '| 指标 | 数值 |\n|---|---|\n| 参考人数 | 513 |',
                         kind='html')
        task = AiTask(user_id=admin.id, exam_id=exam.id, question='回归任务',
                      status='done', stage='已完成', report_id=rep.id,
                      finished_at=datetime.now())
        db.session.add_all([rep, md, dirty, task])
        db.session.commit()
        return {'admin': admin.id, 'reader': reader.id, 'exam': exam.id,
                'rid': rep.id, 'mrid': md.id, 'drid': dirty.id, 'tid': task.id}


IDS = seed()


def rid_of(key):
    return IDS[key]


def main():
    # ---------- 1. 所有者生成链接 ----------
    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        csrf = get_csrf(c, '/grades/ai')
        r = c.post(f'/grades/ai/reports/{IDS["rid"]}/share',
                   json={'days': 30}, headers={'X-CSRFToken': csrf})
        data = (r.get_json(silent=True) or {}).get('data') or {}
        share = data.get('share') or {}
        path = share.get('path') or ''
        token = share.get('token') or ''
        case('所有者可生成分享链接', r.status_code == 200 and path.startswith('/s/'), r.status_code)
        case('生成结果带到期时间（30 天）', bool(share.get('expires_at')), share)
        case('生成结果不是永久链接', share.get('permanent') is False, share)

        # 重复生成 → 复用同一 token（一个报告只有一条有效链接）
        r2 = c.post(f'/grades/ai/reports/{IDS["rid"]}/share',
                    json={'days': 90}, headers={'X-CSRFToken': csrf})
        share2 = ((r2.get_json(silent=True) or {}).get('data') or {}).get('share') or {}
        case('重复分享复用同一条链接', share2.get('token') == token,
             f'{token} vs {share2.get("token")}')
        case('重复分享不新建', ((r2.get_json(silent=True) or {}).get('data') or {})
             .get('created') is False)

        # 状态查询
        r3 = c.get(f'/grades/ai/reports/{IDS["rid"]}/share')
        st = ((r3.get_json(silent=True) or {}).get('data') or {}).get('share') or {}
        case('状态查询返回同一链接', st.get('token') == token)

    # ---------- 2. 未登录打开 ----------
    with app.test_client() as anon:
        r = anon.get(path, follow_redirects=False)
        loc = r.headers.get('Location', '')
        case('未登录打开分享链接 → 302 去登录', r.status_code == 302, r.status_code)
        case('登录后能回到原链接（next 带 token）',
             '/login' in loc and token in loc, loc)
        # 端到端：带着 next 登录 → 应 302 回分享页（真实浏览器表单 action 带当前 query）
        csrf = get_csrf(anon)
        lr = anon.post('/login?next=' + path, data={
            'csrf_token': csrf, 'username': 'share_reader', 'password': PWD},
            follow_redirects=False)
        case('登录成功后跳回分享链接',
             lr.status_code == 302 and lr.headers.get('Location', '').endswith(path),
             f'{lr.status_code} {lr.headers.get("Location", "")}')

    # ---------- 3. 普通登录账号（无 grades.view）查看 ----------
    with app.test_client() as c2:
        login(c2, 'share_reader', PWD)
        csrf2 = get_csrf(c2, '/')
        # B 无权管理 AI 报告（带 CSRF，排除 400 掩盖 403）
        r_deny = c2.post(f'/grades/ai/reports/{IDS["rid"]}/share',
                         json={'days': 7}, headers={'X-CSRFToken': csrf2})
        case('他人不能生成别人的分享链接（403）', r_deny.status_code == 403, r_deny.status_code)
        r_rev = c2.post(f'/grades/ai/reports/{IDS["rid"]}/share/revoke',
                        headers={'X-CSRFToken': csrf2})
        case('他人不能撤销别人的分享链接（403）', r_rev.status_code == 403, r_rev.status_code)
        r_get = c2.get(f'/grades/ai/reports/{IDS["rid"]}/share')
        case('他人不能查询别人的分享状态（403）', r_get.status_code == 403, r_get.status_code)

        r = c2.get(path)
        html = r.get_data(as_text=True)
        case('登录账号（无成绩权限）可打开分享页', r.status_code == 200, r.status_code)
        case('分享页含报告正文', '高三年级成绩分析' in html and '512.4' in html)
        case('分享页含报告表格', '<table>' in html and '01班' in html)
        case('分享页不含站内导航/工作台', '我的 AI 分析' not in html and 'AI 分析工作台' not in html)
        case('分享页剥掉 script（兜底消毒）', '<script>alert' not in html)
        case('分享页带 noindex', 'noindex' in (r.headers.get('X-Robots-Tag') or ''))
        case('分享页不缓存', 'no-store' in (r.headers.get('Cache-Control') or ''))
        case('分享页不泄露 token 之外的链接', '/grades/ai/tasks' not in html)
        # 计数：同会话再打开一次不加
        c2.get(path)
        with app.app_context():
            n1 = db.session.get(AiReportShare, st.get('id')).view_count
        case('同一会话重复打开只计一次', n1 == 1, n1)

    # 另一个会话（新 client）再打开 → 计数 +1
    with app.test_client() as c3:
        login(c3, 'share_owner', PWD)
        c3.get(path)
        with app.app_context():
            n2 = db.session.get(AiReportShare, st.get('id')).view_count
        case('不同会话打开累计计数', n2 >= 2, n2)

    # ---------- 4. Markdown（旧口径）报告 ----------
    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        csrf = get_csrf(c, '/grades/ai')
        r = c.post(f'/grades/ai/reports/{IDS["mrid"]}/share',
                   json={'days': 0}, headers={'X-CSRFToken': csrf})
        mpath = (((r.get_json(silent=True) or {}).get('data') or {}).get('share') or {}).get('path')
        case('永久链接（长期）', bool(mpath))
        rm = c.get(mpath)
        mhtml = rm.get_data(as_text=True)
        m = re.search(r'id="shareMdData">(.*?)</script>', mhtml, re.S)
        md_text = ''
        if m:
            try:
                md_text = json.loads(m.group(1))
            except Exception:              # noqa: BLE001
                md_text = ''
        case('md 报告走 JSON 数据 + 前端渲染', m is not None and '旧口径报告' in (md_text or ''),
             (m.group(0)[:60] if m else 'no shareMdData'))
        case('md 页面不直接输出 md 原文标签', '<h1>旧口径报告' not in mhtml)
        # 2026-10-22 顺手修：负数 id（旧口径报告）此前被 <int:> 路由拒掉
        rd = c.get(f'/grades/ai/tasks/-{IDS["mrid"]}')
        case('旧口径报告负数 id 详情 200', rd.status_code == 200, rd.status_code)

    # ---------- 4.5 历史脏数据：kind=html 但正文是 Markdown ----------
    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        detail = c.get(f'/grades/ai/tasks/-{IDS["drid"]}')
        d = (detail.get_json(silent=True) or {}).get('data') or {}
        h = d.get('html') or ''
        case('脏数据详情已转 HTML（不是 # 源码）',
             detail.status_code == 200 and '<h4' in h and '<table' in h
             and '# 2026级' not in h, h[:80])
        csrf = get_csrf(c, '/grades/ai')
        r = c.post(f'/grades/ai/reports/{IDS["drid"]}/share',
                   json={'days': 7}, headers={'X-CSRFToken': csrf})
        p = (((r.get_json(silent=True) or {}).get('data') or {})
             .get('share') or {}).get('path')
        page = c.get(p).get_data(as_text=True) if p else ''
        case('脏数据分享页已转 HTML',
             '<h4' in page and '<table' in page and '# 2026级' not in page)

    # ---------- 5. 撤销 ----------
    # 注意：test_client 不要嵌套 with（外层 client 的 app context 会让内层 client 的
    # 会话/current_user 串味，登录会被会话校验踢回 /login），一律先退出再建。
    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        csrf = get_csrf(c, '/grades/ai')
        r = c.post(f'/grades/ai/reports/{IDS["rid"]}/share/revoke',
                   headers={'X-CSRFToken': csrf})
        case('撤销成功', r.status_code == 200 and (r.get_json(silent=True) or {}).get('success'))

    with app.test_client() as c2:
        login(c2, 'share_reader', PWD)
        rv = c2.get(path)
        case('撤销后访问 410', rv.status_code == 410, rv.status_code)
        case('撤销后提示"已失效"', '已失效' in rv.get_data(as_text=True))

    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        csrf = get_csrf(c, '/grades/ai')
        # 重新生成 → 新 token
        r2 = c.post(f'/grades/ai/reports/{IDS["rid"]}/share',
                    json={'days': 7}, headers={'X-CSRFToken': csrf})
        nt = (((r2.get_json(silent=True) or {}).get('data') or {}).get('share') or {}).get('token')
        case('撤销后重新生成得到新 token', nt and nt != token)
        path2 = f'/s/{nt}'

    # ---------- 6. 到期 ----------
    with app.app_context():
        sh = AiReportShare.query.filter_by(token=nt).first()
        sh.expires_at = datetime.now() - timedelta(days=1)
        db.session.commit()
    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        re_ = c.get(path2)
        case('过期链接 410', re_.status_code == 410, re_.status_code)
        case('过期提示"已过期"', '过期' in re_.get_data(as_text=True))

    # ---------- 7. 删除报告 → 分享链接一起清理 ----------
    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        csrf = get_csrf(c, '/grades/ai')
        c.post(f'/grades/ai/reports/{IDS["rid"]}/delete', headers={'X-CSRFToken': csrf})
        with app.app_context():
            left = AiReportShare.query.filter_by(report_id=IDS['rid']).count()
        case('删除报告后分享行被清除', left == 0, left)
        rd = c.get(path2)
        case('删除报告后旧链接 410', rd.status_code == 410, rd.status_code)
        with app.app_context():
            db.session.expire_all()
            t = db.session.get(AiTask, IDS['tid'])
        case('删除报告后关联任务解除指向',
             t is not None and t.report_id is None,
             (t.report_id, t.status, t.stage) if t else None)
        d = (c.get(f'/grades/ai/tasks/{IDS["tid"]}').get_json(silent=True)
             or {}).get('data') or {}
        case('删除报告后任务详情提示报告已丢失',
             d.get('report_missing') is True, d.get('report_missing'))

    # ---------- 8. 不存在的 token ----------
    with app.test_client() as c:
        login(c, 'share_owner', PWD)
        r = c.get('/s/this-token-does-not-exist')
        case('不存在的 token 410', r.status_code == 410, r.status_code)
        case('不存在提示"链接不存在"', '不存在' in r.get_data(as_text=True))

    print(f'\n合计：PASS {_ok} / FAIL {_fail}')
    return 0 if _fail == 0 else 1


if __name__ == '__main__':
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(code)

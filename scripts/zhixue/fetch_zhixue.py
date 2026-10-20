# StuLink v1.19.49.43  2026-10-15
# 智学网（zhixue.com）成绩自动下载 —— 校级/教务账号
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""智学网成绩自动下载（只负责登录 + 点导出 + 存原始文件，不做任何加工）。

设计前提
--------
智学网登录存在 RSA 加密、初始密码姓名验证、图形/滑块验证码等不确定环节，
直接写死 HTTP 接口必然随版本失效。因此本脚本走「真实浏览器 + 复用登录态」：
  1) 第一次人工登录一次，脚本把 storage_state（Cookie/localStorage）存档；
  2) 之后每次运行复用存档，自动进成绩页 → 选考试 → 点导出 → 收文件。
页面结构（考试条目、导出按钮）也可能改版，所以选择器全部外置到 config.json，
并提供 --explore 模式把页面上的可点元素 dump 出来，方便改版后重新校准。

「学生成绩」页的导出按钮要过手机短信验证（点导出即弹验证框），故另给
--scrape：不进导出流程，直接在「学生成绩」页上**逐页读取表格**（左表 4 列
固定：序号/准考证号/班级/姓名；右表 4×科目数 列：分数/联考名次/校次/班次），
翻页控件 = .page-box.report 的页码（当前页 class=on），末页无「下一页」。
每页 60 行，DOM 全量渲染（横向溢出不影响读取）。

用法（Windows 用 py -3.12，浏览器复用本机 Edge）
----------------------------------------------
  # 1. 首次：打开浏览器人工登录（含验证码/姓名验证），登录成功后回控制台回车存档
  python scripts/zhixue/fetch_zhixue.py --login
  python scripts/zhixue/fetch_zhixue.py --login --username 手机号 --password 密码

  # 2. 探查：打开成绩页，人手动点到「某场考试成绩」页面，回车 → 导出可点元素清单
  python scripts/zhixue/fetch_zhixue.py --explore --url https://www.zhixue.com/...

  # 3. 列出识别到的考试（不下载）
  python scripts/zhixue/fetch_zhixue.py --list

  # 4. 下载（全部 / 按关键字 / 最近 N 场）
  python scripts/zhixue/fetch_zhixue.py --download
  python scripts/zhixue/fetch_zhixue.py --download --exam 期中 --latest 3
  python scripts/zhixue/fetch_zhixue.py --download --force      # 忽略已下载记录

  # 5. 逐页爬「学生成绩」表格（绕开需要手机验证的导出）
  python scripts/zhixue/fetch_zhixue.py --scrape --latest 1
  python scripts/zhixue/fetch_zhixue.py --scrape --exam 期末 --cdp http://127.0.0.1:9222
  python scripts/zhixue/fetch_zhixue.py --scrape --url '<studentScore 页 URL>'  # 单页直爬

产物
----
  data/zhixue/raw/<考试名>/<时间戳>.xlsx   原始导出件，原样归档不加工
  data/zhixue/scraped/<考试名>.json        学生成绩页逐页爬取结果（结构化 JSON）
  data/zhixue/manifest.json                已下载/已爬取台账（去重、可审计）
  data/zhixue/.state/storage_state.json    登录态（data/ 已在 .gitignore，勿外传）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    # 只用控制台自身编码（改 encoding='utf-8' 会让 GBK 控制台整片乱码），
    # 仅放宽错误策略，避免生僻字直接 UnicodeEncodeError 崩掉整个流程
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / 'data' / 'zhixue'
RAW = DATA / 'raw'
SCRAPED = DATA / 'scraped'
STATE_DIR = DATA / '.state'
STATE_FILE = STATE_DIR / 'storage_state.json'
MANIFEST = DATA / 'manifest.json'
CONFIG_FILE = HERE / 'config.json'

DEFAULT_CONFIG = {
    "base_url": "https://www.zhixue.com",
    "login_url": "https://www.zhixue.com/login.html",
    "grade_url": "",
    "browser_channel": "msedge",
    "headless": False,
    # 选择器：按顺序尝试，第一个「存在且可见」的生效；支持 CSS、"css" 与 {"text": "文案"}
    "selectors": {
        "exam_item": [],
        "exam_open": [".view-btn"],
        "exam_link": [],
        "export_button": [{"css": "a.one-key-export"}, {"text": "一键导出"}],
    },
    "wait_timeout_ms": 15000,
    "post_click_wait_ms": 2500,
}


# ---------------------------------------------------------------- 基础工具
def log(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding='utf-8')
        log(f'已生成默认配置：{CONFIG_FILE}（请先填 grade_url）')
    # utf-8-sig：兼容记事本另存后带 BOM 的配置文件
    return json.loads(CONFIG_FILE.read_text(encoding='utf-8-sig'))


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding='utf-8'))
    return {}


def save_manifest(m):
    DATA.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')


def safe_name(s, limit=60):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', (s or '').strip())
    return (s[:limit] or '未命名考试')


def item_name(text):
    """列表项 inner_text → 干净的考试名（保留「类型 标题」，丢掉元信息行）"""
    keep = []
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        if (line.startswith('考试时间') or line == '查看报告'
                or '阅卷进行中' in line or line.startswith('生成时间')):
            continue
        keep.append(line)
    return safe_name(' '.join(keep[:2]))


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 浏览器
def launch(cfg, headless=None, cdp=None):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    if cdp:                                  # 附着真实浏览器（绕自动化风控）
        browser = pw.chromium.connect_over_cdp(cdp)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        return pw, browser, context
    channel = cfg.get('browser_channel') or 'msedge'
    try:
        browser = pw.chromium.launch(channel=channel,
                                     headless=bool(headless if headless is not None
                                                   else cfg.get('headless', False)))
    except Exception as e:                                  # 没装 Edge 时回退自带内核
        log(f'用 {channel} 启动失败（{e}），回退默认 chromium')
        browser = pw.chromium.launch(headless=bool(headless if headless is not None
                                                   else cfg.get('headless', False)))
    ctx_kwargs = {'accept_downloads': True}
    if STATE_FILE.exists():
        ctx_kwargs['storage_state'] = str(STATE_FILE)
    context = browser.new_context(**ctx_kwargs)
    return pw, browser, context


def looks_like_login(page):
    url = (page.url or '').lower()
    if 'login' in url:
        return True
    try:
        body = page.inner_text('body')[:500]
    except Exception:
        return False
    return ('请输入密码' in body) or ('立即注册' in body and '登录' in body)


def frames_of(page):
    """主文档 + 所有 iframe（智学网大量页面嵌在 iframe 里）"""
    return [page.main_frame] + [f for f in page.frames if f is not page.main_frame]


def find_visible(page, candidates):
    """按候选选择器在当前页所有 frame 里找第一个可见元素。
    候选写法：'css 选择器' 或 {'text': '按钮文案'}"""
    for cand in candidates or []:
        for frame in frames_of(page):
            try:
                if isinstance(cand, dict):
                    if 'text' in cand:
                        loc = frame.get_by_text(cand['text'], exact=False).first
                    elif 'css' in cand:
                        loc = frame.locator(cand['css']).first
                    else:
                        continue
                else:
                    loc = frame.locator(cand).first
                if loc.count() and loc.is_visible():
                    return frame, loc
            except Exception:
                continue
    return None, None


def click_export(page, cfg):
    """点导出按钮。必须等按钮真出现，且不用宽松文案兜底 ——
    智学网报告页顶部有一段含「导出」二字的合规警告，宽松匹配会点错到它。"""
    cands = list(cfg['selectors'].get('export_button') or [])
    frame, loc = wait_find(page, cands, cfg)
    if not loc:
        frame, loc = wait_find(page, [{'text': '一键导出'}], cfg)
    if not loc:
        raise RuntimeError('没找到导出按钮，请跑 --explore 校准 selectors.export_button')
    log(f'点导出按钮：{loc.inner_text()[:30]!r}')
    loc.click()
    return frame


# ---------------------------------------------------------------- 子命令
def has_any_cookie(context):
    try:
        return any(c.get('value') for c in context.cookies())
    except Exception:
        return False


def cmd_login(cfg, args):
    pw, browser, context = launch(cfg, headless=False, cdp=args.cdp)
    page = context.new_page()
    page.goto(cfg.get('login_url') or DEFAULT_CONFIG['login_url'])
    log('浏览器已打开登录页：登录成功后会自动存档，不用管这个控制台窗口。')
    if args.username:
        for sel, val in (('input[type=text]', args.username),
                         ('input[name*=user i]', args.username),
                         ('input[type=password]', args.password or '')):
            try:
                if page.locator(sel).count():
                    page.locator(sel).first.fill(val)
            except Exception:
                pass
        log('已尝试填入账号密码；验证码/姓名验证请手动完成。')
    # 自动等待登录成功（不要求用户回到控制台按回车 —— 之前那样太容易踩空）
    deadline = time.time() + int(getattr(args, 'login_timeout', 600) or 600)
    ok = False
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        try:
            if not looks_like_login(page) and has_any_cookie(context):
                page.wait_for_timeout(2500)          # 等跳转稳定再判定
                if not looks_like_login(page):
                    ok = True
                    break
        except Exception:
            continue
    if not ok:
        browser.close()
        pw.stop()
        raise SystemExit('等待登录超时，没检测到登录成功；重跑一次 --login 即可')
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(STATE_FILE))
    log(f'登录态已保存：{STATE_FILE}')
    log('提示：该文件含 Cookie，等同账号凭据，勿外传（data/ 已在 .gitignore）。')
    browser.close()
    pw.stop()


def cmd_explore(cfg, args):
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    if not url:
        raise SystemExit('需要 --url 或先在 config.json 填 grade_url')
    pw, browser, context = launch(cfg, headless=False, cdp=args.cdp)
    page = context.new_page()
    page.goto(url)
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请先跑 --login')
    input('>>> 请手动点到「某场考试的成绩页面」，停住后回到这里按回车... ')
    out = {'url': page.url, 'frames': [], 'clickables': []}
    for i, f in enumerate(frames_of(page)):
        out['frames'].append({'idx': i, 'url': f.url})
        try:
            els = f.locator('a,button,[onclick],[class*=btn i],[class*=export i]')
            for j in range(min(els.count(), 200)):
                el = els.nth(j)
                try:
                    if not el.is_visible():
                        continue
                    txt = (el.inner_text() or '').strip().replace('\n', ' ')[:40]
                    cls = el.get_attribute('class') or ''
                    eid = el.get_attribute('id') or ''
                    out['clickables'].append({
                        'frame': i, 'tag': el.evaluate('e=>e.tagName'), 'text': txt,
                        'id': eid, 'class': cls[:80],
                        'css': (f"#{eid}" if eid else
                                ('.' + cls.split()[0] if cls else '')),
                    })
                except Exception:
                    continue
        except Exception:
            continue
    DATA.mkdir(parents=True, exist_ok=True)
    dump = DATA / 'explore_dump.json'
    shot = DATA / 'explore.png'
    dump.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    page.screenshot(path=str(shot), full_page=True)
    log(f'已导出可点元素清单：{dump}（截图 {shot}）')
    log('从中挑考试条目/导出按钮的 css 或 text，填进 config.json 的 selectors。')
    browser.close()
    pw.stop()


def list_exams(page, cfg):
    """在成绩页里找考试条目，返回 (所在 frame, CSS 选择器, [(名称, 序号)])"""
    cands = cfg['selectors'].get('exam_item') or []
    if not cands:
        raise SystemExit('config.json 的 selectors.exam_item 为空，请先跑 --explore 校准')
    sel = cands[0]
    if not isinstance(sel, str):
        raise SystemExit('selectors.exam_item[0] 必须是 CSS 选择器（如 ".exam-item"）')
    # SPA 列表是异步渲染的，轮询等它出现（否则会误判成"没识别到"）
    wait_ms = int(cfg.get('wait_timeout_ms', 15000))
    deadline = time.time() + wait_ms / 1000
    while True:
        for frame in frames_of(page):
            try:
                loc = frame.locator(sel)
                n = loc.count()
                if not n:
                    continue
                items = []
                for i in range(n):
                    try:
                        items.append((item_name(loc.nth(i).inner_text()), i))
                    except Exception:
                        continue
                return frame, sel, items
            except Exception:
                continue
        if time.time() >= deadline:
            raise SystemExit('没识别到考试条目（等了 %ds），请跑 --explore 校准 '
                             'selectors.exam_item' % (wait_ms // 1000))
        page.wait_for_timeout(600)


def apply_filters(page, cfg, filters):
    """在列表页按文案点选筛选控件（年级/学期下拉、标签等），可多次"""
    wait = int(cfg.get('post_click_wait_ms', 2500))
    for f in filters or []:
        _, loc = wait_find(page, [{'text': f}], cfg)
        if loc:                       # 胶囊是切换按钮，已选中就别再点（再点会取消筛选）
            try:
                tokens = set((loc.get_attribute('class') or '').split())
                if tokens & {'active', 'on', 'selected', 'checked', 'is-active'}:
                    log(f'筛选已处于选中状态，跳过：{f}')
                    continue
            except Exception:
                pass
        if not loc:
            log(f'没找到筛选控件：{f}（继续）')
            continue
        try:
            loc.click()
            page.wait_for_timeout(max(wait, 3000))
            log(f'已点筛选：{f}')
        except Exception as e:
            log(f'点筛选失败 {f}：{type(e).__name__}: {e}')


def wait_find(page, candidates, cfg):
    """轮询等控件出现（SPA 渲染慢，直接查会误判成「没有」）"""
    deadline = time.time() + int(cfg.get('wait_timeout_ms', 15000)) / 1000
    while True:
        frame, loc = find_visible(page, candidates)
        if loc:
            return frame, loc
        if time.time() >= deadline:
            return None, None
        page.wait_for_timeout(500)


def find_select_input(page, cfg, current):
    """轮询找 value == current 的 el-select 输入框"""
    deadline = time.time() + int(cfg.get('wait_timeout_ms', 15000)) / 1000
    while True:
        for frame in frames_of(page):
            try:
                inputs = frame.locator('.el-select input.el-input__inner')
                for i in range(inputs.count()):
                    el = inputs.nth(i)
                    try:
                        if ((el.input_value() or '').strip() == current
                                and el.is_visible()):
                            return el
                    except Exception:
                        continue
            except Exception:
                continue
        if time.time() >= deadline:
            return None
        page.wait_for_timeout(500)


def apply_select(page, cfg, specs):
    """驱动 Element UI 下拉：spec 形如 '2026-2027学年=2025-2026学年'（当前值=目标值）"""
    wait = int(cfg.get('post_click_wait_ms', 2500))
    for spec in specs or []:
        if '=' not in spec:
            log(f'--select 格式应为 当前值=目标值，跳过：{spec}')
            continue
        cur, target = (x.strip() for x in spec.split('=', 1))
        el = find_select_input(page, cfg, cur)
        if el is None:
            # 同 URL 的 SPA 跳转不会重置 Vue 状态，可能本来就已在目标学年/学期
            if find_select_input(page, cfg, target) is not None:
                log(f'下拉已是 {target}，跳过')
                continue
            log(f'没找到当前值为 {cur!r} 的下拉，跳过')
            continue
        try:
            el.click()
        except Exception as e:
            log(f'点开下拉 {cur!r} 失败：{type(e).__name__}: {e}')
            continue
        page.wait_for_timeout(700)
        opts = page.locator('.el-select-dropdown:visible .el-select-dropdown__item')
        hit = False
        for j in range(opts.count()):
            try:
                if (opts.nth(j).inner_text() or '').strip() == target:
                    opts.nth(j).click()
                    hit = True
                    break
            except Exception:
                continue
        if hit:
            # 切学年后列表要重新拉数据，2.5s 不够（实测要 5s 左右）
            page.wait_for_timeout(max(wait, 5000))
            log(f'下拉已切换：{cur} → {target}')
        else:
            page.keyboard.press('Escape')
            log(f'下拉 {cur} 里没有选项 {target!r}')


def cmd_dump_nav(cfg, args):
    """免交互：登录后打开页面，把所有可见链接 dump 出来，用来定位成绩页地址"""
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    pw, browser, context = launch(cfg, headless=args.headless, cdp=args.cdp)
    page = context.new_page()
    page.goto(url)
    page.wait_for_timeout(max(5000, int(cfg.get('post_click_wait_ms', 2500)) * 2))
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请先跑 --login')
    out = {'url': page.url, 'links': []}
    sels = 'a,button,[role=menuitem],li[onclick],nav a,[class*=menu] a,[class*=nav] a'
    for f in frames_of(page):
        try:
            els = f.locator(sels)
            for j in range(min(els.count(), 400)):
                el = els.nth(j)
                try:
                    if not el.is_visible():
                        continue
                    txt = (el.inner_text() or '').strip().replace('\n', ' ')[:50]
                    if not txt:
                        continue
                    href = el.get_attribute('href') or ''
                    if href.startswith('javascript:'):
                        href = 'js'
                    out['links'].append({'text': txt, 'href': href})
                except Exception:
                    continue
        except Exception:
            continue
    DATA.mkdir(parents=True, exist_ok=True)
    dump = DATA / 'nav_dump.json'
    dump.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    page.screenshot(path=str(DATA / 'nav.png'), full_page=True)
    log(f'已导出 {len(out["links"])} 条链接：{dump}')


def cmd_list(cfg, args):
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    if not url:
        raise SystemExit('需要 --url 或先在 config.json 填 grade_url')
    pw, browser, context = launch(cfg, headless=args.headless, cdp=args.cdp)
    page = context.new_page()
    page.goto(url)
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请先跑 --login')
    apply_select(page, cfg, args.select)
    apply_filters(page, cfg, args.filter)
    _, sel, items = list_exams(page, cfg)
    log(f'识别到 {len(items)} 场考试：')
    for name, i in items:
        print(f'  [{i}] {name}')
    browser.close()
    pw.stop()


def open_report(page, context, cfg):
    """点「查看报告」并返回 (报告页, 是否新开的标签)"""
    _, btn = wait_find(page, cfg['selectors'].get('exam_open')
                       or [{'text': '查看报告'}], cfg)
    if not btn:
        raise RuntimeError('没找到「查看报告」按钮')
    before_urls = set(p.url for p in context.pages
                      if 'scoreAnalysis' in (p.url or ''))
    btn.click()
    deadline = time.time() + 30
    while time.time() < deadline:
        for p in context.pages:
            u = p.url or ''
            if 'scoreAnalysis' in u and u not in before_urls:
                return p, True                    # 新标签页里打开的报告
        if 'scoreAnalysis' in (page.url or ''):
            return page, False                   # 同标签跳转
        page.wait_for_timeout(1000)
    # 兜底：可能复用了此前已打开的同 URL 标签
    for p in context.pages:
        if 'scoreAnalysis' in (p.url or ''):
            return p, False
    raise RuntimeError('点「查看报告」后没找到报告页')


def _watch_loop(cfg, context):
    """盯着浏览器下载 + 兜底扫描系统下载目录，收到表格就归档（Ctrl+C 结束）"""
    EXTS = ('.xls', '.xlsx', '.zip', '.csv')
    manifest = load_manifest()

    def archive(src, orig_name):
        try:
            stem = Path(orig_name).stem or 'download'
            dest_dir = RAW / safe_name(stem)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f'{datetime.now():%Y%m%d_%H%M%S}{Path(orig_name).suffix}'
            if Path(src).resolve() == dest.resolve():
                return
            dest.write_bytes(Path(src).read_bytes())
            size = dest.stat().st_size
            if size < 1024:
                log(f'文件过小（{size}B），先留档：{dest}')
            manifest[stem] = {
                'file': str(dest.relative_to(ROOT)).replace('\\', '/'),
                'original_name': orig_name,
                'sha1': sha1_of(dest),
                'size': size,
                'downloaded_at': datetime.now().isoformat(timespec='seconds'),
            }
            save_manifest(manifest)
            log(f'已归档：{dest}（{size // 1024}KB）')
        except Exception as e:
            log(f'归档失败 {orig_name}：{type(e).__name__}: {e}')

    def on_dl(d):
        try:
            fn = d.suggested_filename or 'download.bin'
            if Path(fn).suffix.lower() not in EXTS:   # 别把用户的其它下载也收进来
                log(f'跳过非表格类下载（不归档）：{fn}')
                return
            archive(d.path(), fn)
        except Exception as e:
            log(f'下载事件处理失败：{e}')

    context.on('download', on_dl)

    dl_dir = Path(cfg.get('download_dir') or (Path.home() / 'Downloads'))
    since = time.time()
    log(f'同时兜底扫描下载目录：{dl_dir}')
    try:
        while True:
            time.sleep(3)
            try:
                if dl_dir.exists():
                    for f in dl_dir.iterdir():
                        if (f.is_file() and f.suffix.lower() in EXTS
                                and f.stat().st_mtime >= since
                                and not f.name.endswith('.crdownload')):
                            archive(f, f.name)
                            since = time.time()
            except Exception:
                pass
    except KeyboardInterrupt:
        log('已停止收件。')


def cmd_watch(cfg, args):
    """纯收件：人工在浏览器里随便操作，脚本只负责把表格下载归档"""
    pw, browser, context = launch(cfg, headless=False, cdp=args.cdp)
    log('收件中：请在浏览器里完成导出（手机验证后若提示"稍后查看"，就去取件）。')
    try:
        _watch_loop(cfg, context)
    finally:
        browser.close()
        pw.stop()


def cmd_scores(cfg, args):
    """导航到「学生成绩」页，交给人工过手机验证；下载自动归档。
    —— 学生成绩明细导出强制手机短信验证，这一步脚本无法也不应代劳。"""
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    pw, browser, context = launch(cfg, headless=False, cdp=args.cdp)
    page = context.new_page()
    page.on('dialog', lambda d: d.accept())
    page.goto(url)
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请先跑 --login')
    apply_select(page, cfg, args.select)
    apply_filters(page, cfg, args.filter)
    frame, sel, items = list_exams(page, cfg)
    if args.exam:
        items = [it for it in items if args.exam in it[0]]
    if args.latest and args.latest > 0:
        items = items[:args.latest]
    if not items:
        raise SystemExit('没有匹配的考试，先跑 --list 看看')
    name, idx = items[0]
    log(f'目标考试：{name}')

    item = frame.locator(sel).nth(idx)
    btn = item.locator((cfg['selectors'].get('exam_open') or ['.view-btn'])[0]).first
    before_urls = set(p.url for p in context.pages
                      if 'scoreAnalysis' in (p.url or ''))
    btn.click()
    rp = None
    deadline = time.time() + 40
    while time.time() < deadline and rp is None:
        for p in context.pages:
            u = p.url or ''
            if ('scoreAnalysis' in u or 'studentScore' in u) and u not in before_urls:
                rp = p
                break
        if rp is None:
            page.wait_for_timeout(1500)
    if rp is None:
        raise SystemExit('没打开报告页')
    rp.wait_for_load_state('domcontentloaded')
    rp.wait_for_timeout(10000)
    lk = rp.get_by_text('学生成绩', exact=True)
    if lk.count():
        try:
            lk.first.click()
            rp.wait_for_timeout(9000)
        except Exception as e:
            log(f'点「学生成绩」失败：{e}')
    log('已到学生成绩页 ▶ 请点页面上的「导出」，按提示输入手机号与验证码；')
    log('  验证后若提示"稍后查看导出结果"，就到导出记录里取件，脚本会自动归档。')
    try:
        _watch_loop(cfg, context)
    finally:
        browser.close()
        pw.stop()


# ---------------------------------------------------------------- 学生成绩页逐页爬取
# 2026-10-15 实测结构（改版后重跑探测校准；导出要手机验证，故直接读表格）：
#   .sr-table2.more   表头区：table.tb-pdt（序号/准考证号/班级/姓名）
#                     + table.tb-num 两行表头：第一行=组名（th 带 colspan=每组列数），
#                       第二行=列名。列数随考试而变：普通考试 4 列/组
#                       （分数/联考名次/校次/班次），3+1+2 为 7 列/组
#                       （分数/校次/校方向名次/校选考名次/班次/班方向名次/班选考名次）
#   .sr-table2        数据区（不带 more）：table.tb-pdt 固定 4 列
#                     + table.tb-num 组×列 全量渲染，每页 60 行
#   .page-box.report  翻页：a=页码（当前页 class=on），「下一页」末页时消失
_SCORE_READ_JS = r"""
() => {
    const vis = e => e && e.offsetParent !== null;
    const txt = e => ((e.innerText || '').trim()).replace(/\s+/g, ' ').slice(0, 40);
    const cls = e => (e.className || '').toString();
    const tables = [...document.querySelectorAll('table')].filter(vis);
    const byRows = arr => arr.sort((a, b) =>
        b.querySelectorAll('tr').length - a.querySelectorAll('tr').length);
    const rowsOf = t => {
        if (!t) return [];
        return [...t.querySelectorAll('tr')]
            .filter(tr => tr.querySelector('td'))
            .map(tr => [...tr.querySelectorAll('td')].map(txt));
    };
    const pdt = byRows(tables.filter(t => cls(t).includes('tb-pdt')))[0] || null;
    // 「tb-num」精确匹配（class 含 tb-num 但排除 tb-num-new —— 布局B 的「操作」列表）
    const isNum = t => cls(t).split(/\s+/).includes('tb-num');
    const nums = byRows(tables.filter(isNum));
    const num = nums[0] || null;
    const numHead = nums[nums.length - 1] || null;
    // 表头两行：第一行=组名（th 带 colspan），第二行=列名（分数/校次/…）。
    // 组的列数随考试而变（普通考试 4 列/组；3+1+2 是 7 列/组），按 colspan 动态解析
    const headTrs = numHead ? [...numHead.querySelectorAll('tr')] : [];
    const groups = [];
    if (headTrs.length >= 2) {
        const nameCells = [...headTrs[0].querySelectorAll('th,td')];
        const colNames = [...headTrs[1].querySelectorAll('th,td')].map(txt);
        const spans = nameCells.map(c =>
            parseInt(c.getAttribute('colspan') || '0', 10) || 0);
        const spanOk = spans.length > 0 && spans.every(s => s > 0)
            && spans.reduce((a, b) => a + b, 0) === colNames.length;
        let off = 0;
        nameCells.forEach((c, i) => {
            const n = spanOk ? spans[i]
                : Math.floor(colNames.length / (nameCells.length || 1));
            groups.push({name: txt(c), cols: colNames.slice(off, off + n)});
            off += n;
        });
    }
    const subjects = groups.map(g => g.name);
    const on = document.querySelector('.page-box.report a.on');
    const nextA = [...document.querySelectorAll('.page-box.report a')]
        .filter(a => (a.innerText || '').trim() === '下一页' && a.offsetParent !== null);
    const gm = (document.body.innerText || '').match(/年级\s*[：:]\s*([^\s]{1,12}年级)/);
    return {
        subjects: subjects,
        groups: groups,
        left: rowsOf(pdt),
        right: rowsOf(num),
        grade: gm ? gm[1] : '',
        page_on: on ? (on.innerText || '').trim() : '',
        next_exists: nextA.length > 0,
    };
}
"""

_READY_JS = r"""
() => {
    const vis = e => e && e.offsetParent !== null;
    const clsOf = e => (e.className || '').toString().split(/\s+/);
    const tables = [...document.querySelectorAll('table')].filter(vis);
    const rowsN = t => t.querySelectorAll('tr').length;
    const numTabs = tables.filter(t => clsOf(t).includes('tb-num'));
    const head = numTabs.length
        ? numTabs.reduce((a, b) => rowsN(a) <= rowsN(b) ? a : b) : null;
    const n_th = head ? head.querySelectorAll('th').length : 0;
    const pdts = tables.filter(t => clsOf(t).includes('tb-pdt'));
    const pdt = pdts.length
        ? pdts.reduce((a, b) => rowsN(a) >= rowsN(b) ? a : b) : null;
    const rows = pdt
        ? [...pdt.querySelectorAll('tr')].filter(tr => tr.querySelector('td')).length : 0;
    const n_tabs = document.querySelectorAll('.rank-num a.subjectStyle').length;
    return {n_th, rows, n_tabs};
}
"""

_FIRST_ROW_JS = r"""
() => {
    const vis = e => e && e.offsetParent !== null;
    const ts = [...document.querySelectorAll('table.tb-pdt')].filter(vis)
        .sort((a, b) => b.querySelectorAll('tr').length - a.querySelectorAll('tr').length);
    const tr = ts[0] ? ts[0].querySelector('tr') : null;
    return tr ? (tr.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 60) : '';
}
"""


def wait_score_table(page, cfg, timeout_ms=None):
    """轮询等学生成绩数据表就绪。两种布局都认：
    布局A（全科并排）：表头 th 多（≥8）；布局B（单科视图）：有科目标签（≥2）。
    只查行数不够 —— 路由切换瞬间旧视图 DOM 还在，会误判成「已就绪」。"""
    deadline = time.time() + (timeout_ms or int(cfg.get('wait_timeout_ms', 15000))) / 1000
    while True:
        info = page.evaluate(_READY_JS)
        if info['rows'] > 0 and (info['n_th'] >= 8 or info['n_tabs'] >= 2):
            return info['rows']
        if time.time() >= deadline:
            raise RuntimeError(
                f'学生成绩表格未就绪（表头 th={info["n_th"]}，科目标签={info["n_tabs"]}，'
                f'数据行={info["rows"]}；可能页面还在加载或已改版）')
        page.wait_for_timeout(500)


def enter_student_scores(rp, cfg):
    """报告页 → 点「学生成绩」标签 → 等表格出现（同标签 SPA 路由切换）"""
    wait_ms = int(cfg.get('wait_timeout_ms', 15000))
    if 'studentScore' not in (rp.url or ''):
        deadline = time.time() + wait_ms / 1000
        clicked = False
        while time.time() < deadline and not clicked:
            try:
                loc = rp.get_by_text('学生成绩', exact=True)
                for i in range(loc.count()):
                    el = loc.nth(i)
                    if el.is_visible():
                        el.click()
                        clicked = True
                        log('已点「学生成绩」标签')
                        break
            except Exception:
                pass
            if not clicked:
                rp.wait_for_timeout(600)
        if not clicked:
            raise RuntimeError('没找到「学生成绩」标签')
    deadline = time.time() + wait_ms / 1000
    while time.time() < deadline:
        if 'studentScore' in (rp.url or ''):
            try:
                wait_score_table(rp, cfg, timeout_ms=3000)
                return
            except RuntimeError:
                pass
        rp.wait_for_timeout(500)
    raise RuntimeError('点了「学生成绩」但表格没出来（超时）')


def build_score_rows(groups, left, right):
    """左表 4 列（序号/准考证号/班级/姓名）+ 右表按「组×列名」动态映射成一行一条记录。
    组的列数由表头 colspan 决定，随考试而变（普通考试 4 列/组、3+1+2 为 7 列/组）"""
    rows = []
    for i, l in enumerate(left):
        if len(l) < 4 or not any(l):
            continue
        r = right[i] if i < len(right) else []
        item = {'seq': l[0], 'exam_no': l[1], 'class_name': l[2], 'name': l[3],
                'scores': {}}
        off = 0
        for g in groups:
            cols = g.get('cols') or []
            seg = r[off:off + len(cols)]
            off += len(cols)
            if cols:
                item['scores'][g.get('name') or '?'] = {
                    c: (seg[k] if k < len(seg) else '') for k, c in enumerate(cols)}
        rows.append(item)
    return rows


def scrape_all_score_pages(rp, cfg, max_pages=0):
    """逐页读表格直到末页；返回 {subjects, groups, grade, page_size, pages, rows[, error]}"""
    result = {'subjects': [], 'groups': [], 'grade': '', 'pages': [],
              'rows': [], 'page_size': 0}
    seen = set()
    dup = 0
    page_no = 0
    limit = max_pages if max_pages and max_pages > 0 else 200

    def _read_stable(prev_first):
        """等表格稳定：有数据、首行与上一页不同、且连续两次读数一致。
        翻页时表格会先清空再重填，只等「首行变化」会读到空页并整页丢数据。"""
        deadline = time.time() + 40
        last_sig = None
        still_old = 0
        while time.time() < deadline:
            try:
                d = rp.evaluate(_SCORE_READ_JS) or {}
            except Exception:
                d = {}
            left = d.get('left') or []
            first = (left[0][1] if left and len(left[0]) > 1 else '') or ''
            sig = (len(left), first)
            if left and first and first != prev_first:
                if last_sig == sig:
                    return d, first, True
                last_sig = sig
            else:
                last_sig = None
                if left and first == prev_first:
                    still_old += 1
                    if still_old >= 4:          # 点了下一页但没动 = 已到末页
                        return None, first, False
            rp.wait_for_timeout(700)
        return None, '', False

    prev_first = ''
    while page_no < limit:
        data, first, ok = _read_stable(prev_first)
        if not ok:
            d = rp.evaluate(_SCORE_READ_JS) or {}
            left = d.get('left') or []
            cur_first = (left[0][1] if left and len(left[0]) > 1 else '') or ''
            if left and cur_first == prev_first:
                break                            # 翻页没生效 = 末页，正常结束
            result['error'] = '翻页后读不到数据（超时），已保留已抓内容'
            break
        prev_first = first
        page_no += 1
        if not result['subjects']:
            result['subjects'] = data['subjects']
            result['groups'] = data.get('groups') or []
            result['grade'] = data['grade']
            result['page_size'] = len(data['left'])
        elif data['subjects'] and data['subjects'] != result['subjects']:
            log(f'警告：第 {page_no} 页表头与首页不一致，按首页口径解析')
        rows = build_score_rows(result['groups'], data['left'], data['right'])
        for r in rows:
            key = (r['seq'], r['exam_no'], r['name'])
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            result['rows'].append(r)
        result['pages'].append({'page': page_no, 'page_on': data['page_on'],
                                'rows': len(rows)})
        log(f'第 {page_no} 页（页码标记 {data["page_on"] or "?"}）读到 {len(rows)} 行，'
            f'累计 {len(result["rows"])} 行')
        if not data['next_exists']:
            break
        a = rp.locator('.page-box.report a:has-text("下一页")')
        if not a.count():
            break
        try:
            a.first.click()
        except Exception as e:
            result['error'] = f'第 {page_no} 页后翻页失败：{type(e).__name__}: {e}'
            break
    if dup:
        log(f'注意：去重跳过 {dup} 行（翻页等待不足时可能出现）')
    return result


def _wait_score_changed(rp, prev_first, timeout=25):
    """布局B 翻页后等数据更新：首行（准考证号）变化且连续两次读数一致"""
    deadline = time.time() + timeout
    last_sig = None
    while time.time() < deadline:
        rp.wait_for_timeout(500)
        try:
            d = rp.evaluate(_SCORE_READ_JS) or {}
        except Exception:
            continue
        left = d.get('left') or []
        first = (left[0][1] if left and len(left[0]) > 1 else '') or ''
        if left and first and first != prev_first:
            sig = (len(left), first)
            if last_sig == sig:
                return d
            last_sig = sig
        else:
            last_sig = None
    return None


def scrape_single_subject(rp, cfg, max_pages=0):
    """布局B（单科视图）：逐个点科目标签、每科翻页，按（准考证号,姓名）把各科数据
    合并到同一批学生行 —— 各科行序不同（按科排序），不能按行号合并。"""
    subs = rp.evaluate("""() => [...document.querySelectorAll('.rank-num a.subjectStyle')]
        .map(a => (a.innerText || '').trim()).filter(Boolean)""")
    if not subs:
        raise RuntimeError('没找到科目标签（布局B）')
    log(f'单科视图，共 {len(subs)} 科：{"、".join(subs)}')
    result = {'subjects': list(subs), 'groups': [], 'grade': '', 'pages': [],
              'rows': [], 'page_size': 0, 'layout': 'single-subject'}
    students = {}
    limit = max_pages if max_pages and max_pages > 0 else 200
    tabs = rp.locator('.rank-num a.subjectStyle')
    for si, subj in enumerate(subs):
        try:
            tabs.nth(si).click()
        except Exception as e:
            log(f'  {subj}：点标签失败 {type(e).__name__}: {e}')
            continue
        rp.wait_for_timeout(1200)
        # 保险：切科后若停留在他页，先回第 1 页（否则会漏数据）
        rp.evaluate("""() => {
            const on = document.querySelector('.page-box.report a.on');
            if (on && (on.innerText || '').trim() !== '1') {
                const a = [...document.querySelectorAll('.page-box.report a')]
                    .find(x => (x.innerText || '').trim() === '1');
                if (a) a.click();
            }
        }""")
        # 等表格切到该科且读数稳定（连续两次一致）
        data = None
        prev_sig = None
        deadline = time.time() + int(cfg.get('wait_timeout_ms', 15000)) / 1000 + 10
        while time.time() < deadline:
            try:
                d = rp.evaluate(_SCORE_READ_JS) or {}
            except Exception:
                d = {}
            names = [g.get('name') for g in (d.get('groups') or [])]
            left = d.get('left') or []
            sig = (names[0] if names else '', len(left),
                   (left[0][1] if left and len(left[0]) > 1 else ''))
            if names and names[0] == subj and left:
                if prev_sig is not None and sig == prev_sig:
                    data = d
                    break
                prev_sig = sig
            else:
                prev_sig = None
            rp.wait_for_timeout(700)
        if not data:
            log(f'  {subj}：切科后没读到数据，跳过')
            continue
        cols = (data['groups'][0].get('cols') if data.get('groups') else []) or []
        result['groups'].append({'name': subj, 'cols': cols})
        result['grade'] = result['grade'] or data.get('grade') or ''
        page_no = 0
        prev_first = ''
        while page_no < limit:
            left = data.get('left') or []
            if not left:
                break
            first = (left[0][1] if len(left[0]) > 1 else '') or ''
            if page_no and first == prev_first:
                break                              # 翻页没生效 = 末页
            page_no += 1
            prev_first = first
            rows = build_score_rows(data['groups'], left, data.get('right') or [])
            for r in rows:
                key = (r['exam_no'], r['name'])
                it = students.get(key)
                if it is None:
                    it = {'seq': r['seq'], 'exam_no': r['exam_no'],
                          'class_name': r['class_name'], 'name': r['name'],
                          'scores': {}}
                    students[key] = it
                for k, v in r['scores'].items():
                    it['scores'].setdefault(k, {}).update(v)
            log(f'  {subj} 第 {page_no} 页：{len(rows)} 行（学生累计 {len(students)}）')
            if not data.get('next_exists'):
                break
            a = rp.locator('.page-box.report a:has-text("下一页")')
            if not a.count():
                break
            try:
                a.first.click()
            except Exception as e:
                result['error'] = f'{subj} 翻页点击失败：{type(e).__name__}: {e}'
                break
            nd = _wait_score_changed(rp, first, timeout=25)
            if not nd:
                break                              # 没更新 = 末页
            data = nd
        result['pages'].append({'subject': subj, 'pages': page_no})
    result['rows'] = list(students.values())
    result['page_size'] = len(result['rows'])
    if not result['rows'] and not result.get('error'):
        result['error'] = '单科视图没抓到任何行'
    return result


def scrape_student_scores(rp, cfg, max_pages=0):
    """按页面布局自动分派：有科目标签 → 单科视图（B）；否则全科并排（A）"""
    try:
        n_tabs = rp.evaluate(
            "() => document.querySelectorAll('.rank-num a.subjectStyle').length")
    except Exception:
        n_tabs = 0
    if n_tabs >= 2:
        return scrape_single_subject(rp, cfg, max_pages)
    return scrape_all_score_pages(rp, cfg, max_pages)


def save_scrape(name, page, result, manifest):
    slug = safe_name(name)
    dest = SCRAPED / f'{slug}.json'
    payload = {
        'exam': name,
        'url': page.url,
        'scraped_at': datetime.now().isoformat(timespec='seconds'),
        'grade': result.get('grade', ''),
        'layout': result.get('layout', 'all-subjects'),
        'page_size': result.get('page_size', 0),
        'pages': len(result.get('pages') or []),
        'subjects': result.get('subjects') or [],
        'groups': result.get('groups') or [],
        'row_count': len(result.get('rows') or []),
        'rows': result.get('rows') or [],
    }
    if result.get('error'):
        payload['error'] = result['error']
    SCRAPED.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    # 顺手生成 XLSX（教务直接打开看；openpyxl 缺失/出错不影响 JSON 归档）
    # 表头按 groups 动态生成：第一行组名（跨列合并）+ 第二行列名
    xlsx = None
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '学生成绩'
        groups = payload['groups']
        head1 = ['序号', '准考证号', '班级', '姓名']
        head2 = ['', '', '', '']
        for g in groups:
            n = max(1, len(g.get('cols') or []))
            head1 += [g.get('name') or ''] + [''] * (n - 1)
            head2 += list(g.get('cols') or [])
        ws.append(head1)
        ws.append(head2)
        col = 4                                    # 前 4 列是固定的学生信息列
        for g in groups:
            n = max(1, len(g.get('cols') or []))
            if n > 1:
                ws.merge_cells(start_row=1, start_column=col + 1,
                               end_row=1, end_column=col + n)
            col += n
        for r in payload['rows']:
            line = [r['seq'], r['exam_no'], r['class_name'], r['name']]
            for g in groups:
                s = r['scores'].get(g.get('name') or '') or {}
                for c in (g.get('cols') or []):
                    line.append(s.get(c, ''))
            ws.append(line)
        xlsx = dest.with_suffix('.xlsx')
        wb.save(str(xlsx))
    except Exception as e:
        log(f'XLSX 生成失败（JSON 已保存）：{type(e).__name__}: {e}')
    manifest[f'student-scores:{name}'] = {
        'file': str(dest.relative_to(ROOT)).replace('\\', '/'),
        'xlsx': str(xlsx.relative_to(ROOT)).replace('\\', '/') if xlsx else '',
        'rows': payload['row_count'],
        'pages': payload['pages'],
        'subjects': payload['subjects'],
        'scraped_at': payload['scraped_at'],
        'source_url': page.url,
    }
    save_manifest(manifest)
    tail = f'（未完整：{payload["error"]}）' if payload.get('error') else ''
    log(f'已保存：{dest}（{payload["row_count"]} 行 / {payload["pages"]} 页）{tail}')


def exam_display_name(item):
    """reportlist 条目 → 显示名（类型 + 名称 + 日期，保证唯一可辨识）"""
    d = item.get('data') or {}
    tp = (d.get('markingExamTypeName') or '').strip()
    nm = (d.get('examName') or '').strip()
    name = ' '.join(p for p in (tp, nm) if p) or (d.get('examId') or '未命名考试')
    ts = d.get('examDateTime')
    if ts:
        try:
            name += f'（{datetime.fromtimestamp(ts / 1000):%Y-%m-%d}）'
        except Exception:
            pass
    return name


def collect_exams(page, cfg, args):
    """让列表页自己发 reportlist 请求，从响应里拿考试清单（比点 DOM 稳）。
    返回 [(显示名, exam_id, school_id), ...]"""
    got = []

    def on_resp(r):
        if 'api/reportlist' in (r.url or ''):
            try:
                got.append(json.loads(r.text()))
            except Exception:
                pass

    page.on('response', on_resp)
    apply_select(page, cfg, args.select)
    apply_filters(page, cfg, args.filter)
    deadline = time.time() + 20
    while time.time() < deadline and not got:
        page.wait_for_timeout(500)
    if not got:
        return []
    res = got[-1].get('result') or {}
    school_id = res.get('schoolId') or ''
    out = []
    for item in res.get('reportList') or []:
        d = item.get('data') or {}
        eid = d.get('examId')
        if eid:
            out.append((exam_display_name(item), eid,
                        school_id or d.get('schoolId') or ''))
    return out


def cmd_scrape(cfg, args):
    """逐页读「学生成绩」表格（不点导出，绕开手机短信验证）并归档 JSON+XLSX。

    路线：列表页 reportlist 接口拿 examId → 直接打开报告页 URL（点列表条目上的
    「查看报告」在部分考试上不跳转，实测不可靠）→ 点「学生成绩」→ 逐页翻表格。"""
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    if not url:
        raise SystemExit('需要 --url 或先在 config.json 填 grade_url')
    if not STATE_FILE.exists() and not args.cdp:
        raise SystemExit('没有登录态，请先跑 --login（或用 --cdp 附着已登录的浏览器）')

    pw, browser, context = launch(cfg, headless=args.headless, cdp=args.cdp)
    page = context.new_page()
    page.on('dialog', lambda d: d.accept())
    manifest = load_manifest()

    try:
        if 'studentScore' in url:                 # 直接给「学生成绩」页地址：只爬这一页
            page.goto(url)
            if looks_like_login(page):
                raise SystemExit('登录态已失效，请先跑 --login')
            wait_score_table(page, cfg)
            result = scrape_student_scores(page, cfg, args.max_pages)
            save_scrape('（URL 指定页）', page, result, manifest)
            return

        page.goto(url)
        if looks_like_login(page):
            raise SystemExit('登录态已失效，请先跑 --login')
        exams = collect_exams(page, cfg, args)
        if not exams:
            raise SystemExit('没取到考试清单（reportlist 接口无响应）；'
                             '也可用 --url 直接给「学生成绩」页地址')
        if args.exam:
            exams = [e for e in exams if args.exam in e[0]]
            log(f'按关键字 {args.exam!r} 过滤后 {len(exams)} 场')
        if args.latest and args.latest > 0:
            exams = exams[:args.latest]
        log(f'待爬取 {len(exams)} 场考试')

        base = (cfg.get('base_url') or 'https://www.zhixue.com').rstrip('/')
        for name, exam_id, school_id in exams:
            key = f'student-scores:{name}'
            if not args.force and key in manifest:
                log(f'跳过（已爬过，--force 可重爬）：{name}')
                continue
            report_url = (f'{base}/htm-freshreport/#/school/scoreAnalysis'
                          f'?examId={exam_id}&schoolId={school_id}')
            log(f'抓取：{name}')
            rp = context.new_page()               # 每场独立标签，爬完即关
            rp.on('dialog', lambda d: d.accept())
            try:
                rp.goto(report_url)
                if looks_like_login(rp):
                    raise RuntimeError('登录态已失效')
                enter_student_scores(rp, cfg)
                wait_score_table(rp, cfg, timeout_ms=20000)
                result = scrape_student_scores(rp, cfg, args.max_pages)
                save_scrape(name, rp, result, manifest)
            except Exception as e:
                log(f'失败：{name} —— {type(e).__name__}: {e}')
                try:
                    DATA.mkdir(parents=True, exist_ok=True)
                    rp.screenshot(path=str(DATA / f'scrape_fail_{safe_name(name, 20)}.png'),
                                  full_page=False)
                except Exception:
                    pass
            finally:
                try:
                    rp.close()
                except Exception:
                    pass
    finally:
        save_manifest(manifest)
        log(f'完成，台账：{MANIFEST}')
        browser.close()
        pw.stop()


def cmd_download(cfg, args):
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    if not url:
        raise SystemExit('需要 --url 或先在 config.json 填 grade_url')
    if not STATE_FILE.exists() and not args.cdp:
        raise SystemExit('没有登录态，请先跑 --login（或用 --cdp 附着已登录的浏览器）')

    pw, browser, context = launch(cfg, headless=args.headless, cdp=args.cdp)
    page = context.new_page()
    page.on('dialog', lambda d: d.accept())
    manifest = load_manifest()
    timeout = int(cfg.get('wait_timeout_ms', 15000))

    page.goto(url)
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请重新跑 --login')
    apply_select(page, cfg, args.select)
    apply_filters(page, cfg, args.filter)
    frame, sel, items = list_exams(page, cfg)
    if args.exam:
        items = [it for it in items if args.exam in it[0]]
        log(f'按关键字 {args.exam!r} 过滤后 {len(items)} 场')
    if args.latest and args.latest > 0:
        items = items[:args.latest]
    log(f'待处理 {len(items)} 场考试')

    for name, idx in items:
        if not args.force and name in manifest:
            log(f'跳过（已下载过）：{name}')
            continue
        try:
            # 「查看报告」开新标签时列表页不受影响，可直接接着点下一条；
            # 只有同标签跳转才需要回列表页并重放筛选（select/filter 已做成幂等跳过）
            if 'scoreAnalysis' in (page.url or ''):
                page.goto(url)
                page.wait_for_load_state('domcontentloaded')
                apply_select(page, cfg, args.select)
                apply_filters(page, cfg, args.filter)
                frame, sel, cur = list_exams(page, cfg)
                idx = next((i for n, i in cur if n == name), idx)
            el = frame.locator(sel).nth(idx)
            if not el.count():
                log(f'条目消失，跳过：{name}')
                continue
            el.click()
            page.wait_for_timeout(int(cfg.get('post_click_wait_ms', 2500)))
            # 智学网：点条目旁的「查看报告」会打开报告页（可能新标签，也可能同标签）
            rp, opened_new = open_report(page, context, cfg)
            rp.wait_for_timeout(3000)
            with rp.expect_download(timeout=timeout) as dl_info:
                click_export(rp, cfg)
            dl = dl_info.value
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            dest_dir = RAW / name
            dest_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(dl.suggested_filename or 'export.xlsx').suffix or '.xlsx'
            dest = dest_dir / f'{ts}{suffix}'
            dl.save_as(str(dest))
            size = dest.stat().st_size
            if size < 1024:
                log(f'文件过小（{size}B），疑似失败：{dest}')
                continue
            manifest[name] = {
                'file': str(dest.relative_to(ROOT)).replace('\\', '/'),
                'original_name': dl.suggested_filename,
                'sha1': sha1_of(dest),
                'size': size,
                'source_url': rp.url,
                'downloaded_at': datetime.now().isoformat(timespec='seconds'),
            }
            save_manifest(manifest)
            log(f'已保存：{dest}（{size // 1024}KB）')
            if opened_new:                      # 只关自己开的标签，不动用户已有页面
                try:
                    rp.close()
                except Exception:
                    pass
        except Exception as e:
            log(f'失败：{name} —— {type(e).__name__}: {e}')
            try:
                DATA.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(DATA / f'fail_{safe_name(name, 20)}.png'),
                                full_page=True)
            except Exception:
                pass

    save_manifest(manifest)
    log(f'完成，台账：{MANIFEST}')
    browser.close()
    pw.stop()


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description='智学网成绩自动下载（原始件归档）')
    ap.add_argument('--login', action='store_true', help='人工登录一次并保存登录态')
    ap.add_argument('--explore', action='store_true', help='探查页面可点元素，校准选择器')
    ap.add_argument('--list', action='store_true', help='列出识别到的考试')
    ap.add_argument('--download', action='store_true', help='下载成绩导出件')
    ap.add_argument('--watch', action='store_true',
                    help='人工操作、脚本收件（用于需手机验证的「学生成绩」导出）')
    ap.add_argument('--scores', action='store_true',
                    help='导航到「学生成绩」页后转入收件（导出需人工过手机验证）')
    ap.add_argument('--scrape', action='store_true',
                    help='逐页读「学生成绩」表格并归档 JSON（不点导出，免手机验证）')
    ap.add_argument('--max-pages', type=int, default=0,
                    help='--scrape 时最多翻 N 页（调试用，0=直到末页）')
    ap.add_argument('--dump-nav', action='store_true', help='免交互导出页面所有链接（定位成绩页）')
    ap.add_argument('--set-grade-url', metavar='URL', help='把成绩页地址写进 config.json')
    ap.add_argument('--url', help='覆盖 grade_url')
    ap.add_argument('--exam', help='考试名关键字过滤')
    ap.add_argument('--filter', action='append', default=[],
                    help='列表页筛选控件文案，可多次（如 --filter 高一 --filter 上学期）')
    ap.add_argument('--select', action='append', default=[],
                    help='驱动下拉：当前值=目标值，可多次（如 --select 2026-2027学年=2025-2026学年）')
    ap.add_argument('--latest', type=int, default=0, help='只处理列表前 N 场')
    ap.add_argument('--force', action='store_true', help='忽略已下载记录')
    ap.add_argument('--headless', action='store_true', help='无头模式（可能被风控拦截）')
    ap.add_argument('--cdp', help='附着真实浏览器，如 http://127.0.0.1:9222（绕自动化风控）')
    ap.add_argument('--username')
    ap.add_argument('--password')
    ap.add_argument('--login-timeout', type=int, default=600,
                    help='等待人工登录的秒数，默认 600')
    args = ap.parse_args()

    cfg = load_config()
    for k, v in DEFAULT_CONFIG.items():                    # 补齐新增默认项
        if k not in cfg:
            cfg[k] = v
    cfg.setdefault('selectors', {})
    for k, v in DEFAULT_CONFIG['selectors'].items():
        cfg['selectors'].setdefault(k, v)

    if args.set_grade_url:
        cfg['grade_url'] = args.set_grade_url
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        log(f'grade_url 已写入 config.json：{args.set_grade_url}')
        return
    if args.login:
        cmd_login(cfg, args)
    elif args.explore:
        cmd_explore(cfg, args)
    elif args.dump_nav:
        cmd_dump_nav(cfg, args)
    elif args.list:
        cmd_list(cfg, args)
    elif args.download:
        cmd_download(cfg, args)
    elif args.watch:
        cmd_watch(cfg, args)
    elif args.scores:
        cmd_scores(cfg, args)
    elif args.scrape:
        cmd_scrape(cfg, args)
    else:
        ap.print_help()


if __name__ == '__main__':
    main()

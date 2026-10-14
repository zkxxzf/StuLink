# StuLink v1.19.49.32  2026-10-15
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

产物
----
  data/zhixue/raw/<考试名>/<时间戳>.xlsx   原始导出件，原样归档不加工
  data/zhixue/manifest.json                已下载台账（去重、可审计）
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
    # 选择器：按顺序尝试，第一个「存在且可见」的生效；支持 CSS 与 {"text": "文案"} 两种写法
    "selectors": {
        "exam_item": [],
        "exam_link": [],
        "export_button": [{"text": "导出"}, {"text": "导出Excel"}, {"text": "下载成绩"}],
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


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 浏览器
def launch(cfg, headless=None):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
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
                if isinstance(cand, dict) and 'text' in cand:
                    loc = frame.get_by_text(cand['text'], exact=False).first
                else:
                    loc = frame.locator(cand).first
                if loc.count() and loc.is_visible():
                    return frame, loc
            except Exception:
                continue
    return None, None


def click_export(page, cfg):
    """点导出按钮；优先配置的选择器，其次按常见文案兜底"""
    cands = list(cfg['selectors'].get('export_button') or [])
    cands += [{"text": t} for t in ('导出', '导出Excel', '下载成绩单', '下载', '成绩导出')]
    frame, loc = find_visible(page, cands)
    if not loc:
        raise RuntimeError('没找到导出按钮，请跑 --explore 校准 selectors.export_button')
    log(f'点导出按钮：{loc.inner_text()[:30]!r}')
    loc.click()
    return frame


# ---------------------------------------------------------------- 子命令
def cmd_login(cfg, args):
    pw, browser, context = launch(cfg, headless=False)
    page = context.new_page()
    page.goto(cfg.get('login_url') or DEFAULT_CONFIG['login_url'])
    log('浏览器已打开登录页。')
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
    input('>>> 请在浏览器里完成登录，成功后回到这里按回车存档... ')
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
    pw, browser, context = launch(cfg, headless=False)
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
    for frame in frames_of(page):
        try:
            loc = frame.locator(sel)
            n = loc.count()
            if not n:
                continue
            items = []
            for i in range(n):
                try:
                    items.append((safe_name(loc.nth(i).inner_text()), i))
                except Exception:
                    continue
            return frame, sel, items
        except Exception:
            continue
    raise SystemExit('没识别到考试条目，请跑 --explore 校准 selectors.exam_item')


def apply_filters(page, cfg, filters):
    """在列表页按文案点选筛选控件（年级/学期下拉、标签等），可多次"""
    wait = int(cfg.get('post_click_wait_ms', 2500))
    for f in filters or []:
        _, loc = find_visible(page, [{'text': f}])
        if not loc:
            log(f'没找到筛选控件：{f}（继续）')
            continue
        try:
            loc.click()
            page.wait_for_timeout(wait)
            log(f'已点筛选：{f}')
        except Exception as e:
            log(f'点筛选失败 {f}：{type(e).__name__}: {e}')


def cmd_dump_nav(cfg, args):
    """免交互：登录后打开页面，把所有可见链接 dump 出来，用来定位成绩页地址"""
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    pw, browser, context = launch(cfg, headless=args.headless)
    page = context.new_page()
    page.goto(url)
    page.wait_for_timeout(int(cfg.get('post_click_wait_ms', 2500)))
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请先跑 --login')
    out = {'url': page.url, 'links': []}
    for f in frames_of(page):
        try:
            els = f.locator('a[href]')
            for j in range(min(els.count(), 400)):
                el = els.nth(j)
                try:
                    if not el.is_visible():
                        continue
                    txt = (el.inner_text() or '').strip().replace('\n', ' ')[:50]
                    if not txt:
                        continue
                    out['links'].append({'text': txt, 'href': el.get_attribute('href')})
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
    pw, browser, context = launch(cfg, headless=args.headless)
    page = context.new_page()
    page.goto(url)
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请先跑 --login')
    apply_filters(page, cfg, args.filter)
    _, sel, items = list_exams(page, cfg)
    log(f'识别到 {len(items)} 场考试：')
    for name, i in items:
        print(f'  [{i}] {name}')
    browser.close()
    pw.stop()


def cmd_download(cfg, args):
    url = args.url or cfg.get('grade_url') or cfg.get('base_url')
    if not url:
        raise SystemExit('需要 --url 或先在 config.json 填 grade_url')
    if not STATE_FILE.exists():
        raise SystemExit('没有登录态，请先跑 --login')

    pw, browser, context = launch(cfg, headless=args.headless)
    page = context.new_page()
    page.on('dialog', lambda d: d.accept())
    manifest = load_manifest()
    timeout = int(cfg.get('wait_timeout_ms', 15000))

    page.goto(url)
    if looks_like_login(page):
        raise SystemExit('登录态已失效，请重新跑 --login')
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
            # 每场都重新进列表页：筛选会丢，所以重放筛选并按名称重新定位条目
            page.goto(url)
            page.wait_for_load_state('domcontentloaded')
            apply_filters(page, cfg, args.filter)
            frame, sel, cur = list_exams(page, cfg)
            idx = next((i for n, i in cur if n == name), idx)
            el = frame.locator(sel).nth(idx)
            if not el.count():
                log(f'条目消失，跳过：{name}')
                continue
            el.click()
            page.wait_for_timeout(int(cfg.get('post_click_wait_ms', 2500)))
            # 二跳：考试卡片 → 成绩明细页（配了 exam_link 才跳，很多站点点条目即直达）
            _, lk = find_visible(page, cfg['selectors'].get('exam_link') or [])
            if lk:
                try:
                    lk.click()
                    page.wait_for_timeout(int(cfg.get('post_click_wait_ms', 2500)))
                except Exception:
                    pass
            with page.expect_download(timeout=timeout) as dl_info:
                click_export(page, cfg)
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
                'sha1': sha1_of(dest),
                'size': size,
                'source_url': page.url,
                'downloaded_at': datetime.now().isoformat(timespec='seconds'),
            }
            save_manifest(manifest)
            log(f'已保存：{dest}（{size // 1024}KB）')
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
    ap.add_argument('--dump-nav', action='store_true', help='免交互导出页面所有链接（定位成绩页）')
    ap.add_argument('--set-grade-url', metavar='URL', help='把成绩页地址写进 config.json')
    ap.add_argument('--url', help='覆盖 grade_url')
    ap.add_argument('--exam', help='考试名关键字过滤')
    ap.add_argument('--filter', action='append', default=[],
                    help='列表页筛选控件文案，可多次（如 --filter 高一 --filter 上学期）')
    ap.add_argument('--latest', type=int, default=0, help='只处理列表前 N 场')
    ap.add_argument('--force', action='store_true', help='忽略已下载记录')
    ap.add_argument('--headless', action='store_true', help='无头模式（可能被风控拦截）')
    ap.add_argument('--username')
    ap.add_argument('--password')
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
    else:
        ap.print_help()


if __name__ == '__main__':
    main()

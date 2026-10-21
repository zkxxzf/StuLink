# StuLink v1.19.52.64  2026-10-20
# 复用用户本机 Edge 登录态的浏览器会话（只附着，不登录）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""复用用户自己 Edge 的登录态来访问智学网。

为什么这么做：智学网登录有极验风控 + 手机短信验证，脚本自己登录
必被封（60500）；用户日常 Edge 里已经登录过，直接借用这份登录态即可。

实现路线（不打扰用户正在开着的 Edge）：
  1) 把用户默认 profile 的 Cookie / Local State / Local Storage
     复制到 `data/zhixue/.edge-live`（真实 Edge 内核 + 真登录态）；
  2) 用 `--remote-debugging-port=9222` 启动这个独立 profile 的 Edge；
  3) Playwright `connect_over_cdp` 附着上去，后续全部走真实浏览器。

不用默认 profile 目录直接开端口，是因为 Edge 136+ 明确禁止对
默认 profile 启用远程调试（会被静默忽略）。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / 'data' / 'zhixue'
LIVE_DIR = DATA / '.edge-live'
CDP_PORT = 9222
EDGE_EXES = (
    Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'),
    Path(r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'),
)
# 需要同步的「登录态」文件/目录（相对默认 user-data-dir）
SYNC_FILES = (
    ('Local State', 'Local State'),
    ('Default/Network/Cookies', 'Default/Network/Cookies'),
    ('Default/Network/Cookies-journal', 'Default/Network/Cookies-journal'),
    ('Default/Preferences', 'Default/Preferences'),
)
SYNC_DIRS = (
    ('Default/Local Storage', 'Default/Local Storage'),
    ('Default/Session Storage', 'Default/Session Storage'),
)


def log(msg):
    """统一日志（带时间戳，便于和抓取台账对照）"""
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def user_data_dir():
    """用户默认的 Edge 用户数据目录"""
    base = os.environ.get('LOCALAPPDATA') or ''
    return Path(base) / 'Microsoft' / 'Edge' / 'User Data'


def edge_exe():
    """定位 msedge.exe（找不到直接报错，避免静默走错浏览器）"""
    for p in EDGE_EXES:
        if p.exists():
            return p
    raise RuntimeError('没找到 msedge.exe，请确认本机安装了 Edge')


def cdp_version(port=CDP_PORT, timeout=1.5):
    """探测调试端口：返回版本字典，未开启返回 None"""
    url = f'http://127.0.0.1:{port}/json/version'
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:
        return None


def _copy_locked(src: Path, dst: Path):
    """复制可能被 Edge 独占的文件：先常规复制，失败则用 SQLite 只读快照"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
        return True
    except OSError:
        pass
    try:
        con = sqlite3.connect(f'file:{src.as_posix()}?mode=ro&immutable=1',
                              uri=True)
        out = sqlite3.connect(str(dst))
        con.backup(out)
        out.close()
        con.close()
        return True
    except Exception:
        return False


def _copy_tree(src: Path, dst: Path):
    """逐文件尽力复制（leveldb 正在被 Edge 写，个别文件锁住就跳过）"""
    copied = failed = 0
    for item in src.rglob('*'):
        if item.is_dir():
            continue
        rel = item.relative_to(src)
        if rel.name in ('LOCK',) or rel.suffix == '.lock':
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(item, target)
            copied += 1
        except OSError:
            failed += 1
    return copied, failed


def sync_profile(verbose=True):
    """把用户默认 profile 的登录态同步到 .edge-live（返回成功项数）

    注意：Cookie 文件里通常只有持久化项，**会话 cookie 不落盘**；
    智学网真正能恢复登录的往往是 Local Storage 里的 token，所以
    leveldb 目录必须逐文件复制（整体 copytree 会因一个文件锁住全丢）。
    """
    src = user_data_dir()
    if not (src / 'Default').exists():
        raise RuntimeError(f'默认 Edge 数据目录异常：{src}')
    ok = 0
    for a, b in SYNC_FILES:
        s = src / a
        if s.exists() and _copy_locked(s, LIVE_DIR / b):
            ok += 1
    for a, b in SYNC_DIRS:
        s = src / a
        if not s.exists():
            continue
        copied, failed = _copy_tree(s, LIVE_DIR / b)
        if copied:
            ok += 1
        if verbose and failed:
            log(f'  {b}：复制 {copied} 个文件，跳过锁定 {failed} 个')
    if verbose:
        log(f'已同步本机 Edge 登录态 {ok} 项 → {LIVE_DIR.name}')
    return ok


def start_edge(port=CDP_PORT, profile=None):
    """以独立 profile 启动带调试端口的 Edge（后台，不抢占当前窗口）"""
    profile = Path(profile or LIVE_DIR)
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        str(edge_exe()),
        f'--remote-debugging-port={port}',
        '--remote-allow-origins=*',
        f'--user-data-dir={profile}',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=msEdgeSidebarV2',
    ]
    log(f'启动 Edge（调试端口 {port}）…')
    subprocess.Popen(args, close_fds=True)
    return profile


def wait_cdp(port=CDP_PORT, timeout=45):
    """等调试端口就绪"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = cdp_version(port)
        if info:
            return info
        time.sleep(0.6)
    return None


def looks_like_login(page):
    """粗判当前页是否落在登录页（首页导航也有「登录」字样，不能只看文案）"""
    url = (page.url or '').lower()
    if 'login' in url:
        return True
    try:
        if page.locator('input[type=password]').count():
            return True
        body = page.inner_text('body')[:600]
    except Exception:
        return False
    return '请输入密码' in body or '忘记密码' in body


def attach(refresh_login=True, port=CDP_PORT, force_new=False, profile=None):
    """附着到复用登录态的 Edge。

    profile 为空时用 `.edge-live`（从默认 profile 同步登录态）；
    也可以传已有的 `.edge-profile`（此前人工登录过的真实 Edge）。
    返回 (playwright, browser, context)；调用方**不要** browser.close()，
    否则会把用户这个浏览器窗口一起关掉，正确做法是 page.close()
    后 pw.stop()。
    """
    from playwright.sync_api import sync_playwright
    if force_new or not cdp_version(port):
        if refresh_login and not profile:
            sync_profile()
        start_edge(port, profile)
        if not wait_cdp(port):
            raise RuntimeError('Edge 调试端口没起来，请手动关掉残留窗口后重试')
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
    context = (browser.contexts[0] if browser.contexts
               else browser.new_context())
    return pw, browser, context


def ensure_login(page, timeout_s=180):
    """确保页面已登录：已登录返回 True；未登录则提示用户在窗口里登录。

    返回 False 表示超时仍未登录（调用方应中止，不要尝试自动登录）。
    """
    if not looks_like_login(page):
        return True
    log('注意：登录态失效，请在弹出的 Edge 窗口里登录一次（脚本会等）')
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not looks_like_login(page):
            log('登录成功，继续')
            return True
        page.wait_for_timeout(2000)
    return False

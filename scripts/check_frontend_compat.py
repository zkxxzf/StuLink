# -*- coding: utf-8 -*-
"""前端兼容自检：手写 JS 不得使用 ES2020+ 语法（第 4 项自检）。

背景（2026-10-14 第三轮审计 UI-07 的守卫）
------------------------------------------
本仓库承诺**手写 JS 不用 ES2020+ 语法**：校内大量终端是旧微信内置浏览器（X5）/ 老版
Chrome，一处可选链（问号 + 点）就会让**整段内联脚本 SyntaxError**，页面交互全失效。
该问题被修过一次又回归过（`app/templates/portrait/detail.html` 三处），所以固化成自检。

扫描范围
--------
- `app/templates/**/*.html` 里的内联 `<script>` 块（`<script src=...>` 外链不计）；
- `app/static/js/*.js`（**排除** `*.min.js` 第三方压缩库，它们自带降级构建）。

判定口径
--------
先剥掉**注释与字符串字面量**（避免注释/文案里的示例误报，`\\n` 计数保留以便报行号），
再匹配下列 token，命中任一即失败（退出码 1）：

    ?.        ??        ?.(可选链/空值合并)      .replaceAll(
    Object.fromEntries   .flatMap(               .at(
    structuredClone      globalThis              Promise.allSettled
    matchAll(

用法
----
    py -3.12 scripts/check_frontend_compat.py        # 0=通过 / 1=有命中
"""
import glob
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ES2020+（X5 / 老 Chrome 不安全）token。注意：Object.entries/values 属 ES2017，
# 目标终端支持，刻意不入列。
TOKENS = ('?.', '??', '.replaceAll(', 'Object.fromEntries', '.flatMap(', '.at(',
          'structuredClone', 'globalThis', 'Promise.allSettled', 'matchAll(')

_SCRIPT_RE = re.compile(r'<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S | re.I)


def strip_js(code):
    """剥掉 // 与 /* */ 注释、'…' “…” `…` 字符串（保留换行数，便于报行号）。"""
    out = []
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ''
        if ch == '/' and nxt == '/':
            j = code.find('\n', i)
            i = n if j < 0 else j
            continue
        if ch == '/' and nxt == '*':
            j = code.find('*/', i + 2)
            seg = code[i:n] if j < 0 else code[i:j + 2]
            out.append('\n' * seg.count('\n'))
            i = n if j < 0 else j + 2
            continue
        if ch in ('"', "'", '`'):
            quote, j, buf = ch, i + 1, []
            while j < n:
                if code[j] == '\\':
                    buf.append(code[j:j + 2])
                    j += 2
                    continue
                if code[j] == quote:
                    j += 1
                    break
                buf.append(code[j])
                j += 1
            out.append('""' + '\n' * ''.join(buf).count('\n'))
            i = j
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def targets():
    """(路径, 待扫描的 JS 代码) 列表"""
    items = []
    for f in sorted(glob.glob(os.path.join(BASE, 'app', 'templates', '**', '*.html'),
                              recursive=True)):
        raw = open(f, encoding='utf-8', errors='replace').read()
        code = '\n'.join(m.group(1) for m in _SCRIPT_RE.finditer(raw))
        if code.strip():
            items.append((os.path.relpath(f, BASE), code))
    for f in sorted(glob.glob(os.path.join(BASE, 'app', 'static', 'js', '*.js'))):
        if f.endswith('.min.js'):
            continue
        items.append((os.path.relpath(f, BASE),
                      open(f, encoding='utf-8', errors='replace').read()))
    return items


def main():
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:  # noqa: BLE001
        pass
    hits = []
    for rel, code in targets():
        clean = strip_js(code)
        for tok in TOKENS:
            start = 0
            while True:
                idx = clean.find(tok, start)
                if idx < 0:
                    break
                line = clean.count('\n', 0, idx) + 1
                hits.append((rel, line, tok))
                start = idx + len(tok)
    total_files = len(targets())
    if not hits:
        print('[OK]   前端兼容自检：%d 个文件（模板内联脚本 + 手写 JS），'
              'ES2020+ 语法 0 命中' % total_files)
        return 0
    print('[FAIL] 前端兼容自检：命中 %d 处 ES2020+ 语法（旧微信 X5 会整段脚本报错）'
          % len(hits))
    for rel, line, tok in hits[:40]:
        print('   %s:%d  →  %s' % (rel, line, tok))
    if len(hits) > 40:
        print('   … 其余 %d 处略' % (len(hits) - 40))
    print('   改法：可选链改显式判空（if (el) el.xx）、空值合并改 || 或三元、'
          'replaceAll 改 replace + 全局正则、at(-1) 改 [len-1] 等。')
    return 1


if __name__ == '__main__':
    sys.exit(main())

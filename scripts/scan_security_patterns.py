# -*- coding: utf-8 -*-
"""安全模式自检扫描（清单 R-8）。

用途：提交前/PR 里跑一遍，防止把已确认的漏洞模式重新引入代码库。
只做**正则静态扫描**，不执行业务代码；命中即非 0 退出。

扫描项（与 00-security-todo-merged.md 第 5 节「回归红线」一一对应）：
  1. raw SQL 拼接：text(f"...") / execute(f"...") 中含有 request / 格式化变量
  2. 路径来自请求：send_file / send_from_directory / open(...) 里出现 request.*
  3. 新增 csrf.exempt
  4. 模板新增 |safe / Markup(用户输入) / render_template_string
  5. 前端新增裸 innerHTML 拼接（未走 escHtml）
  6. 内联事件处理器里出现 '{{ ... }}'（M-10）

运行：python scripts/scan_security_patterns.py
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'data', 'logs',
             '.codebuddy', 'instance', 'venv', '.venv'}

RULES = [
    {
        'id': 'SQL-拼接',
        'files': ('.py',),
        'pattern': re.compile(r'(text\(\s*f|execute\(\s*f)[^)]*(request\.|\{.*(args|form|values))'),
        'msg': 'raw SQL 拼接了请求参数（必须绑定参数）',
    },
    {
        'id': '路径-请求参数',
        'files': ('.py',),
        'pattern': re.compile(
            r'(?<![\w.])(send_file|send_from_directory|open)\([^)]*request\.'),
        'msg': '文件读写路径混入了 request.*（存在任意文件读风险）',
    },
    {
        'id': 'CSRF-豁免',
        'files': ('.py',),
        'pattern': re.compile(r'csrf\.exempt'),
        'msg': '出现 csrf.exempt（除非有充分理由并在 PR 说明）',
    },
    {
        'id': '模板-不安全输出',
        'files': ('.html',),
        'pattern': re.compile(r'\|\s*safe\b|Markup\(|render_template_string'),
        'msg': '模板出现 |safe / Markup / render_template_string',
    },
    {
        # 只报「拼接了动态内容」的 innerHTML：右值含 + 拼接或模板字符串，且未走 escHtml
        'id': '前端-裸innerHTML',
        'files': ('.js',),
        'pattern': re.compile(
            r'\.innerHTML\s*=\s*(?![^\n]*escHtml)(?=[^\n]*(\+|\$\{|`))'),
        'msg': '前端出现未走 escHtml 的动态 innerHTML 拼接',
    },
    {
        'id': '模板-内联处理器变量',
        'files': ('.html',),
        'pattern': re.compile(r'on(?:click|change|submit|input|mouseover)\s*=\s*"[^"]*\'\{\{'),
        'msg': "内联事件处理器里用单引号包裹模板变量（应改 |tojson）",
    },
]

# 白名单：历史遗留且已确认无害的文件（清单第 6 节已复核：不可注入）
WHITELIST = {
    'app/templates/base.html',                 # X-CSRFToken 常量注入
    'app/templates/grades/_cert_body.html',    # |safe 用于已转义的证明正文（v1.17.0 复核）
    # 以下两处为多行模板字符串拼接，插值处已用 escapeAttr / esc：
    'app/static/js/academic_forms.js',
    'app/static/js/report.js',
}

# 第三方库文件跳过（压缩/构建产物，非本项目代码）
THIRD_PARTY_SUFFIX = ('.min.js', '.bundle.js', '.min.css')


def _is_third_party(rel):
    return rel.endswith(THIRD_PARTY_SUFFIX) or '/static/vendor/' in rel


def iter_files():
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(('.py', '.html', '.js')):
                yield os.path.join(root, fn)


def main():
    hits = []
    for path in iter_files():
        rel = os.path.relpath(path, BASE).replace('\\', '/')
        if (rel in WHITELIST or _is_third_party(rel)
                or rel.startswith('scripts/scan_security_patterns')):
            continue
        ext = os.path.splitext(path)[1]
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for ln, line in enumerate(f, 1):
                    for rule in RULES:
                        if ext not in rule['files']:
                            continue
                        if rule['pattern'].search(line):
                            hits.append((rule['id'], rel, ln,
                                         line.strip()[:120], rule['msg']))
        except Exception:  # noqa: BLE001
            continue

    if not hits:
        print('[OK] 安全模式扫描通过：0 处命中')
        return 0
    print(f'[WARN] 安全模式扫描命中 {len(hits)} 处：')
    for rid, rel, ln, text, msg in hits:
        print(f'  - [{rid}] {rel}:{ln}  {msg}')
        print(f'      {text}')
    return 1


if __name__ == '__main__':
    sys.exit(main())

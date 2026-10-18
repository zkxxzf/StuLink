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
  7. 实机 MCP 令牌泄漏到其它文件（副本/备份/归档/文档）——2026-10-13 新增：
     当时在 `tests/_archive/**/backup_mcp_server_*/` 里发现一份带同一令牌的 config.json
     副本（该副本未被 .gitignore 覆盖，一次 `git add -A` 就会进云端）

运行：python scripts/scan_security_patterns.py
"""
import json
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


# 行内豁免：命中行末尾写 `# sec-allow: <规则id>` 即跳过该条（须同行写清理由）。
# 用于"规则要求有充分理由并说明"的场景（如小程序接口豁免 CSRF 改走令牌鉴权），
# 比整文件进 WHITELIST 精确，不会顺带放过该文件的其它问题。
_INLINE_ALLOW = re.compile(r'#\s*sec-allow:\s*([\w\u4e00-\u9fa5\-]+)')


def _allowed_inline(line, rule_id):
    """命中行是否带本规则的豁免标记。"""
    m = _INLINE_ALLOW.search(line)
    return bool(m) and m.group(1) == rule_id


def iter_files():
    for root, dirs, files in os.walk(BASE):
        # 备份快照（backup_* 目录）不属于现行代码：扫描目标是现网 .py/.html/.js，
        # 备份里保留的历史写法不应计数（tests/qa_*/backup_before_delete 曾导致 R-8 假阳性）。
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS and not d.startswith('backup')]
        for fn in files:
            if fn.endswith(('.py', '.html', '.js')):
                yield os.path.join(root, fn)


LIVE_CONFIG_REL = 'mcp_server/config.json'
# 令牌字面量：JSON 里 token 值至少有这么长（占位符 <...> 不会命中）
_TOKEN_FIELD = re.compile(r'"token"\s*:\s*"([A-Za-z0-9_\-]{16,})"')


def _live_token():
    """读实机 MCP 令牌（config.json 允许 // 注释行）；读不到返回空串。"""
    path = os.path.join(BASE, LIVE_CONFIG_REL)
    if not os.path.exists(path):
        return ''
    try:
        with open(path, encoding='utf-8') as f:
            text = '\n'.join(l for l in f if not l.lstrip().startswith('//'))
        return (json.loads(text).get('token') or '').strip()
    except Exception:                      # noqa: BLE001  配置读不到就不做字面值比对
        return ''


def scan_token_leaks():
    """实机 MCP 令牌是否出现在别的文件里（副本 / 备份 / 归档 / 文档 / 日志）。

    两类命中：
      ① 按**实机令牌字面值**全仓搜（精准，零误报）；
      ② 任何非实机配置的 `*config*.json` 里出现 ≥16 位的 token 字段（覆盖令牌已轮换、
         旧副本仍在的情况）。
    `backup*` 目录在这里**不跳过**——这正是要抓的东西。
    """
    token = _live_token()
    hits = []
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, BASE).replace('\\', '/')
            if rel == LIVE_CONFIG_REL or _is_third_party(rel):
                continue
            try:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
                with open(path, 'rb') as f:
                    raw = f.read()
            except OSError:
                continue
            msg = ('该文件含实机 MCP 令牌，禁止提交/外发（令牌只留在 '
                   + LIVE_CONFIG_REL + '）')
            rid = ''
            if token and len(token) >= 12 and token.encode('utf-8') in raw:
                rid = '令牌-副本泄漏'
            elif (fn.lower().endswith('.json') and 'config' in fn.lower()
                    and _TOKEN_FIELD.search(raw.decode('utf-8', 'ignore'))):
                rid = '令牌-遗留副本'
                msg = ('该配置副本里含 token 字段（可能是旧令牌），禁止提交；'
                       '确认无用就删掉')
            if rid:
                hits.append((rid, rel, 1, '（已按令牌内容命中，值不打印）', msg))
    return hits


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
                            if _allowed_inline(line, rule['id']):
                                continue
                            hits.append((rule['id'], rel, ln,
                                         line.strip()[:120], rule['msg']))
        except Exception:  # noqa: BLE001
            continue

    hits.extend(scan_token_leaks())

    if not hits:
        print(f'[OK] 安全模式扫描通过：0 处命中（含令牌副本检查，'
              f'实机令牌只存在于 {LIVE_CONFIG_REL}）')
        return 0
    print(f'[WARN] 安全模式扫描命中 {len(hits)} 处：')
    for rid, rel, ln, text, msg in hits:
        print(f'  - [{rid}] {rel}:{ln}  {msg}')
        print(f'      {text}')
    return 1


if __name__ == '__main__':
    sys.exit(main())

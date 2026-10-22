# -*- coding: utf-8 -*-
"""导出全站路由快照，供逐路由审计使用（2026-10-22 审计工具）。

用法：
    py -3.12 tests/dump_all_routes.py            # 打印全部
    py -3.12 tests/dump_all_routes.py timed      # 只看含关键字的

每行：方法  规则  端点  权限装饰器（取视图源码里第一个 perm_required/login_required）
输出同时落盘 tests/_local/_routes_all.txt，便于 diff 前后变化。
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from app import create_app  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '_local', '_routes_all.txt')
os.makedirs(os.path.dirname(OUT), exist_ok=True)


def view_source(app, endpoint):
    """取视图函数源码片段（用于读权限装饰器）。"""
    try:
        fn = app.view_functions[endpoint]
        import inspect
        return inspect.getsource(fn)
    except Exception:
        return ''


def perms_of(src):
    """从源码里抓权限/登录装饰器，按出现顺序返回简写列表。"""
    out = []
    for m in re.finditer(r'@(perm_required|login_required|admin_required|'
                         r'csrf\.\w+)', src):
        name = m.group(1)
        if name == 'perm_required':
            seg = src[m.end():m.end() + 80]
            m2 = re.match(r"\('([^']+)'", seg)
            out.append('perm:' + (m2.group(1) if m2 else '?'))
        else:
            out.append(name)
    return ','.join(out) or '-'


def main():
    app = create_app()
    kw = sys.argv[1] if len(sys.argv) > 1 else ''
    rows = []
    for rule in sorted(app.url_map.iter_rules(),
                       key=lambda r: (str(r.rule))):
        if rule.rule.startswith('/static'):
            continue
        methods = ','.join(sorted(m for m in rule.methods
                                  if m not in ('HEAD', 'OPTIONS')))
        src = view_source(app, rule.endpoint)
        rows.append('%s\t%s\t%s\t%s' % (methods, rule.rule, rule.endpoint,
                                        perms_of(src)))
    shown = [r for r in rows if kw in r] if kw else rows
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(rows))
    print('全部 %d 条，本次显示 %d 条 -> %s' % (len(rows), len(shown), OUT))
    print('\n'.join(shown))


if __name__ == '__main__':
    main()

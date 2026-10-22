# -*- coding: utf-8 -*-
"""P2 自检：禁止业务代码直接使用不安全 XML 解析器。

规则：app/、mcp_server/、scripts/ 下出现 `import xml.etree` /
`from xml.etree` / `import lxml` / `from lxml` 即失败（tests 除外）。
需要 XML 时改用 app/utils/xml_safe.py（defusedxml）。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ('app', 'mcp_server', 'scripts')
PATTERN = re.compile(
    r'^\s*(?:from\s+(?:xml\.etree|lxml)|import\s+(?:xml\.etree|lxml))\b',
    re.MULTILINE)


def main():
    hits = []
    for base in DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames
                           if d not in ('__pycache__', '.venv', 'node_modules')]
            for name in filenames:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(dirpath, name)
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue
                try:
                    with open(path, encoding='utf-8') as f:
                        text = f.read()
                except OSError:
                    continue
                for m in PATTERN.finditer(text):
                    line = text[:m.start()].count('\n') + 1
                    hits.append(f'{os.path.relpath(path, ROOT)}:{line}  {m.group(0).strip()}')
    if hits:
        print('发现不安全 XML 解析器（请改用 app/utils/xml_safe.py）：')
        for h in hits:
            print('  -', h)
        return 1
    print('XML 解析自检：未发现直接使用 xml.etree / lxml。')
    return 0


if __name__ == '__main__':
    sys.exit(main())

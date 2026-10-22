
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)          # 审计脚本按项目根定位 app 包与 data/*.db

# -*- coding: utf-8 -*-
"""审计 F1：备份可读性 + 实际安装版本"""
import os
import sqlite3
import subprocess
import sys

print('=== 备份完整性 ===')
for f in ('data/grades.db.bak_band20260712_20261016_112813',
          'data/grades.db.bak-20261015_215132_before_g2_analysis',
          'data/system.db.bak-20261015_134425'):
    if not os.path.exists(f):
        print(f'  {os.path.basename(f)}: 不存在')
        continue
    try:
        c = sqlite3.connect(f)
        r = c.execute('PRAGMA integrity_check').fetchone()[0]
        n = (c.execute('SELECT COUNT(*) FROM exam_scores').fetchone()[0]
             if 'grades' in f else '-')
        c.close()
        print(f'  {os.path.basename(f)}: {r}｜exam_scores={n}')
    except Exception as e:  # noqa: BLE001
        print(f'  ERR {f} {e}')

print('\n=== 实际安装版本（与 requirements 对照） ===')
out = subprocess.run([sys.executable, '-m', 'pip', 'list', '--format=freeze'],
                     capture_output=True, text=True).stdout
KEY = ('flask', 'werkzeug', 'sqlalchemy', 'jinja2', 'cryptography', 'reportlab',
       'openpyxl', 'waitress', 'wtf', 'login', 'pptx')
for line in out.splitlines():
    if any(k in line.lower() for k in KEY):
        print('  ', line)

print('\n=== 备份脚本/定时任务 ===')
import glob
for p in glob.glob('scripts/**/*backup*', recursive=True) + glob.glob('deploy/*.sh'):
    print('  ', p)
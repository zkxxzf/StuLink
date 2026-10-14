#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移清单核对：列出 scripts/ 下所有库结构脚本及其执行状态（配合 docs/数据库迁移清单.md）。

用法
----
    py -3.12 scripts/migration_status.py                    # 核对本地 data/
    py -3.12 scripts/migration_status.py --data-dir D:\\nas_data
    py -3.12 scripts/migration_status.py --md > 清单.md      # 输出 Markdown 表格
    py -3.12 scripts/migration_status.py --pending           # 只列待执行 / 需人工核对的

判定口径
--------
- **✅ 已执行**：脚本带 `_mig_log` 登记机制，且目标库 `system.db.migration_log` 里有记录；
- **⬜ 待执行**：带登记机制但库里没记录（跑一次就会登记）；
- **❔ 人工核对**：老脚本没有登记机制（2026-10-12 之前写的），只能靠
  `scripts/check_columns.py` / `check_indexes.py` 反查列与索引，或按文档记录人工确认。

注意：本工具**只读**，不执行任何迁移、不改任何库。
"""
import argparse
import glob
import os
import re
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA = os.path.join(BASE_DIR, 'data')

# 库结构脚本的扫描范围（迁移 + 建表/建索引/权限类脚本）
PATTERNS = ('scripts/migrate*.py', 'scripts/add_*.py', 'scripts/backfill*.py')

_DOC_RE = re.compile(r'"""(.*?)"""', re.S)
_DATE_RE = re.compile(r'_(\d{8})\.py$')


def _doc_summary(path):
    """取脚本 docstring 的第一段有效文字作为「作用」说明。"""
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read(6000)
    except Exception:  # noqa: BLE001  读不到就留空，不影响清单
        return '', ''
    m = _DOC_RE.search(text)
    summary = ''
    if m:
        for line in m.group(1).splitlines():
            line = line.strip().strip('。')
            if line:
                summary = line
                break
    return summary, text


def _scan():
    out = []
    for pat in PATTERNS:
        for p in sorted(glob.glob(os.path.join(BASE_DIR, pat))):
            rel = 'scripts/' + os.path.basename(p)
            summary, text = _doc_summary(p)
            m = _DATE_RE.search(os.path.basename(p))
            out.append({
                'script': rel,
                'date': m.group(1) if m else '',
                'summary': summary,
                'auto': ('_mig_log' in text and 'is_applied' in text),
            })
    out.sort(key=lambda x: (x['date'] or '00000000', x['script']))
    return out


def _logged(data_dir):
    """目标库 migration_log 里已登记的脚本名集合（库/表不存在时返回空集）。"""
    db = os.path.join(data_dir, 'system.db')
    if not os.path.exists(db):
        return None
    try:
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute('SELECT script FROM migration_log').fetchall()
        finally:
            conn.close()
        return {r[0] for r in rows}
    except Exception:  # noqa: BLE001  表不存在等一律按「空」处理
        return set()


def main():
    ap = argparse.ArgumentParser(description='迁移清单核对（只读）')
    ap.add_argument('--data-dir', default=DEFAULT_DATA, help='数据目录（默认 ./data）')
    ap.add_argument('--md', action='store_true', help='输出 Markdown 表格（可直接粘进文档）')
    ap.add_argument('--pending', action='store_true', help='只列待执行 / 需人工核对的')
    args = ap.parse_args()

    scripts = _scan()
    logged = _logged(args.data_dir)
    unknown = logged is None

    for s in scripts:
        if not s['auto']:
            s['status'] = '人工核对'
        elif unknown:
            s['status'] = '待执行（未连库判定）'
        elif os.path.basename(s['script']) in logged:
            s['status'] = '已执行'
        else:
            s['status'] = '待执行'

    rows = scripts
    if args.pending:
        rows = [s for s in rows if s['status'] != '已执行']

    if args.md:
        print('| 脚本 | 日期 | 作用 | 状态 |')
        print('|------|------|------|------|')
        for s in rows:
            print('| `%s` | %s | %s | %s |' % (
                s['script'], s['date'] or '—', s['summary'] or '—', s['status']))
        return 0

    if unknown:
        print('[WARN] %s 下没有 system.db（或读不到），登记状态无法判定，按「待执行」列出'
              % args.data_dir)
    total = len(scripts)
    done = sum(1 for s in scripts if s['status'] == '已执行')
    todo = sum(1 for s in scripts if s['status'].startswith('待执行'))
    manual = sum(1 for s in scripts if s['status'] == '人工核对')
    print('迁移脚本 %d 个：已登记 %d / 待执行 %d / 人工核对 %d'
          % (total, done, todo, manual))
    print('-' * 78)
    for s in rows:
        print('%-48s %-10s %s' % (s['script'], s['date'] or '—', s['status']))
        if s['summary']:
            print('    ' + s['summary'][:76])
    print('-' * 78)
    print('提醒：状态以 docs/数据库迁移清单.md 为真值；新迁移请同步登记到该文档。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""P1 M-06：操作日志保留/归档。

默认 dry-run；--apply --confirm DELETE 才执行：
1) 把早于保留期的行导出为 JSONL（data/backups/operation_logs_<ts>.jsonl）；
2) 导出成功后再从 operation_logs 删除；
3) 最近 7 天永不删除。

用法：
    py -3.12 scripts/ops/prune_operation_logs.py
    py -3.12 scripts/ops/prune_operation_logs.py --days 180 --apply --confirm DELETE
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DB = os.path.join(ROOT, 'data', 'system.db')
BACKUP_DIR = os.path.join(ROOT, 'data', 'backups')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=180)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--confirm', default='')
    args = ap.parse_args()

    if args.days < 7:
        print('拒绝：--days 不能小于 7（最近 7 天永不删除）。')
        return 2
    if args.apply and args.confirm != 'DELETE':
        print('拒绝执行：--apply 必须带 --confirm DELETE。')
        return 2
    if not os.path.exists(DB):
        print(f'库不存在：{DB}')
        return 1

    cutoff = (datetime.now() - timedelta(days=args.days)).strftime(
        '%Y-%m-%d %H:%M:%S')
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        total = con.execute('SELECT COUNT(*) FROM operation_logs').fetchone()[0]
        rows = con.execute(
            'SELECT * FROM operation_logs WHERE created_at < ? '
            'ORDER BY id', (cutoff,)).fetchall()
        print(f'== 操作日志保留（保留 {args.days} 天，最近 7 天不动）==')
        print(f'  当前 {total} 行；早于 {cutoff} 的 {len(rows)} 行')
        if rows:
            print(f'  最早 id={rows[0]["id"]} {rows[0]["created_at"]}；'
                  f'最新待删 id={rows[-1]["id"]} {rows[-1]["created_at"]}')
        if not args.apply:
            print('\n预览模式，未改动。执行：--apply --confirm DELETE')
            return 0
        if not rows:
            print('没有需要归档的行。')
            return 0

        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(BACKUP_DIR, f'operation_logs_{ts}.jsonl')
        with open(out, 'w', encoding='utf-8') as f:
            for row in rows:
                f.write(json.dumps(dict(row), ensure_ascii=False) + '\n')
        print(f'  已导出：{out}（{len(rows)} 行）')

        con.execute('DELETE FROM operation_logs WHERE created_at < ?', (cutoff,))
        con.commit()
        print(f'  已删除 {len(rows)} 行；导出文件保留，可人工恢复。')
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())

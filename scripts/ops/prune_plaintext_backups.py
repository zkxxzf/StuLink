# -*- coding: utf-8 -*-
"""H-08：明文数据库备份与教师口令清单治理。

默认 dry-run，只打印清单与 sha256；--apply --confirm DELETE 才把文件
移入 data/backup/_quarantine_<时间戳>/（不是直接删除，保留人工复核窗口）。

规则：
- 按「目录 + 备份前缀」保留最新 N 份（默认 3），其余移入隔离区；
- 绝不处理当前 .db / .db-wal / .db-shm；
- data/教师登录信息_*.xlsx 属凭据文件，默认不动，需 --include-credentials；
- 删除凭据文件**不会**让账号口令失效，脚本会提示仍需强制重置的账号数。

用法：
    py -3.12 scripts/ops/prune_plaintext_backups.py
    py -3.12 scripts/ops/prune_plaintext_backups.py --apply --confirm DELETE
    py -3.12 scripts/ops/prune_plaintext_backups.py --keep 5 --include-credentials --apply --confirm DELETE
"""
import argparse
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DATA = os.path.join(ROOT, 'data')
BACKUP = os.path.join(DATA, 'backup')
SYSTEM_DB = os.path.join(DATA, 'system.db')

_CRED_PATTERN = os.path.join(DATA, '教师登录信息_*.xlsx')


def _sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ''


def _is_current_db(name):
    return (name.endswith('.db') or name.endswith('.db-wal')
            or name.endswith('.db-shm'))


def _backup_prefix(name):
    """grades.db.bak-2026... → grades.db；无 .bak- 返回空。"""
    if '.bak-' not in name:
        return ''
    return name.split('.bak-', 1)[0]


def scan(keep):
    """返回 (to_move, keepers, credentials)。"""
    files = []
    for pattern in (os.path.join(DATA, '*.bak-*'),
                    os.path.join(BACKUP, '*')):
        for path in glob.glob(pattern):
            if not os.path.isfile(path):
                continue
            name = os.path.basename(path)
            if _is_current_db(name):
                continue
            if name.startswith('_quarantine_') or name.startswith('_prune_manifest_'):
                continue
            prefix = _backup_prefix(name)
            if not prefix:
                continue
            files.append({'path': path, 'dir': os.path.dirname(path),
                          'prefix': prefix, 'size': os.path.getsize(path),
                          'mtime': os.path.getmtime(path),
                          'sha256': _sha256(path)})
    groups = {}
    for item in files:
        groups.setdefault((item['dir'], item['prefix']), []).append(item)
    to_move, keepers = [], []
    for _key, items in groups.items():
        items.sort(key=lambda x: x['mtime'], reverse=True)
        keepers.extend(items[:keep])
        to_move.extend(items[keep:])
    credentials = [{'path': p, 'size': os.path.getsize(p),
                    'mtime': os.path.getmtime(p), 'sha256': _sha256(p)}
                   for p in glob.glob(_CRED_PATTERN) if os.path.isfile(p)]
    return to_move, keepers, credentials


def _must_change_count():
    if not os.path.exists(SYSTEM_DB):
        return None
    try:
        con = sqlite3.connect(f'file:{SYSTEM_DB}?mode=ro', uri=True)
        try:
            row = con.execute(
                'SELECT COUNT(*) FROM users WHERE must_change_pwd=1').fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', type=int, default=3)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--confirm', default='')
    ap.add_argument('--include-credentials', action='store_true')
    args = ap.parse_args()

    if args.apply and args.confirm != 'DELETE':
        print('拒绝执行：--apply 必须带 --confirm DELETE。')
        return 2
    if args.keep < 1:
        print('--keep 至少为 1。')
        return 2

    to_move, keepers, credentials = scan(args.keep)
    total = sum(x['size'] for x in to_move)
    print('== 明文备份治理 ==')
    print(f'  保留（每目录每前缀最新 {args.keep} 份）：{len(keepers)} 个')
    print(f'  待隔离：{len(to_move)} 个，约 {total / 1024 / 1024:.1f} MB')
    for item in to_move[:20]:
        print(f'    {os.path.relpath(item["path"], ROOT)}  '
              f'{item["size"] / 1024 / 1024:.1f} MB  sha={item["sha256"][:12]}...')
    if len(to_move) > 20:
        print(f'    ... 另有 {len(to_move) - 20} 个')

    if credentials:
        print(f'  凭据文件（教师初始口令清单）：{len(credentials)} 个'
              f'（默认不动，--include-credentials 才处理）')
        for item in credentials:
            print(f'    {os.path.relpath(item["path"], ROOT)}')
        n = _must_change_count()
        if n is not None:
            print(f'    提示：仍有 {n} 个账号 must_change_pwd=1；'
                  '删除 xlsx 不会让这些口令失效，建议强制重置。')

    if not args.apply:
        print('\n预览模式，未移动任何文件。')
        print('执行：--apply --confirm DELETE'
              + (' --include-credentials' if args.include_credentials else ''))
        return 0

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    quarantine = os.path.join(BACKUP, f'_quarantine_{stamp}')
    os.makedirs(quarantine, exist_ok=True)
    manifest = {
        'created_at': stamp,
        'keep': args.keep,
        'files': to_move + (credentials if args.include_credentials else []),
    }
    manifest_path = os.path.join(BACKUP, f'_prune_manifest_{stamp}.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    moved = 0
    for item in to_move:
        rel = os.path.relpath(item['path'], DATA)
        dst = os.path.join(quarantine, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(item['path'], dst)
        moved += 1
    if args.include_credentials:
        for item in credentials:
            dst = os.path.join(quarantine, os.path.basename(item['path']))
            shutil.move(item['path'], dst)
            moved += 1

    print(f'\n已隔离 {moved} 个文件到：{quarantine}')
    print(f'清单：{manifest_path}')
    print('确认无需恢复后，可人工删除隔离区。')
    return 0


if __name__ == '__main__':
    sys.exit(main())

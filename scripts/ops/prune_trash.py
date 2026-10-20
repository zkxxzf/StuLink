# -*- coding: utf-8 -*-
"""P2：回收站到期清理（默认 dry-run）。

规则：
- 只删 `expires_at < now` 的归档；`expires_at` 为空的历史行不自动删；
- 删除前把元数据导出为 JSONL（data/backups/trash_<ts>.jsonl），不含 payload；
- `--apply --confirm DELETE` 才执行；默认不删已恢复记录（--include-restored 可含）。

用法：
    py -3.12 scripts/ops/prune_trash.py
    py -3.12 scripts/ops/prune_trash.py --apply --confirm DELETE
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from app import create_app  # noqa: E402
from app.utils.trash import purge_expired_records  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--confirm', default='')
    ap.add_argument('--include-restored', action='store_true')
    ap.add_argument('--export-dir', default=os.path.join(
        ROOT, 'data', 'backups'))
    args = ap.parse_args()

    if args.apply and args.confirm != 'DELETE':
        print('拒绝执行：--apply 必须带 --confirm DELETE。')
        return 2

    app = create_app()
    with app.app_context():
        result = purge_expired_records(
            dry_run=not args.apply,
            export_dir=args.export_dir if args.apply else None,
            include_restored=args.include_restored)
        print('== 回收站到期清理 ==')
        print(f'  命中 {result["count"]} 条；'
              f'{"已删除" if args.apply else "预览（未删除）"} {result["deleted"]} 条')
        for row in result['sample']:
            print(f'    #{row["id"]} [{row["module"]}/{row["kind"]}] '
                  f'{row["summary"]} 过期={row["expires_at"]}')
        if result.get('error'):
            print(f'  错误：{result["error"]}')
            return 1
        if not args.apply:
            print('\n预览模式，未改动。执行：--apply --confirm DELETE')
        elif result.get('export'):
            print(f'  元数据已导出：{result["export"]}')
        return 0


if __name__ == '__main__':
    sys.exit(main())

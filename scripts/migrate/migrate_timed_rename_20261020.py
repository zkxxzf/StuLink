# StuLink v1.19.49.49  2026-10-20
# 限时练场次改名：`年级 No.N`（去掉「第X次」「标准化作业」等字眼）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""把限时练场次名统一成 `2026级 No.1` 这种形式（迁移 A43）。

用户口径（2026-10-20 修正）：名字只留 **届 + No.序号**，年级学期不进名字
（它是列表里的标签：高一上/高一下/高二上/高二下/高三上/高三下）。
序号按**日期升序**在届内连续（同日按 id），因为名字里已没有学期可区分档位。

2026-10-21 从 `scripts/zhixue/` 移到 `scripts/migrate/`：它是改库脚本，
跟其它迁移同一命名/登记/扫描口径（`check_migrations.py` 只认
`scripts/{migrate,fix,backfill,apply,seed}/`），`migration_status.py`
的 `--pending` 也才能看到它。

用法：
    py -3.12 -u scripts/migrate/migrate_timed_rename_20261020.py            # 预览
    py -3.12 -u scripts/migrate/migrate_timed_rename_20261020.py --apply    # 写库
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))       # _db_backup / _mig_log

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models.timed import TimedExam                        # noqa: E402
from app.modules.grades.services import timed_service         # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='限时练场次改名')
    ap.add_argument('--apply', action='store_true', help='真正写库')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        groups = defaultdict(list)
        for e in TimedExam.query.all():
            groups[e.grade].append(e)
        total = 0
        for grade, rows in sorted(groups.items()):
            if not grade:
                continue
            rows.sort(key=lambda e: (e.exam_date, e.id))
            print(f'\n{grade}（{len(rows)} 场）')
            for idx, e in enumerate(rows, start=1):
                new = f'{grade} No.{idx}'
                tag = e.semester_label()
                if (e.name or '') != new:
                    print(f'  {e.exam_date} [{tag}] {e.name[:30]:30s} → {new}')
                    total += 1
                    if args.apply:
                        e.name = new
                else:
                    print(f'  {e.exam_date} [{tag}] {new}（已是）')
        print(f'\n共 {total} 场需要改名')
        if not args.apply:
            print('[dry-run] 未写库；确认后加 --apply')
            return
        # A43 是改库操作：写前备份 timed.db，写完登记迁移日志（2026-10-21 审计补齐）
        from _db_backup import backup_db
        backup_db(str(ROOT / 'data' / 'timed.db'),
                  tag='before_timed_rename_20261020')
        db.session.commit()
        print('[APPLIED] 已提交')
        try:
            import _mig_log
            _mig_log.record(Path(__file__).name,
                            note=f'限时练场次改名为「年级 No.N」，共 {total} 场')
        except Exception as exc:                # noqa: BLE001
            print(f'[WARN] 迁移登记失败：{exc}')


if __name__ == '__main__':
    main()

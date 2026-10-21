# StuLink v1.19.49.50  2026-10-21
# 限时练场次改名：`2025级20260905限时练No.17`
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""把限时练场次名统一成 `2025级20260905限时练No.17`（迁移 A44）。

用户口径（2026-10-21，取代 A43 的「2025级 No.1」）：
名称 = **届 + 8 位日期 + 限时练 + No.序号**，序号按届内日期升序
连续（同日按 id），跨学期不重置；年级学期仍只作列表标签。

格式的唯一维护点是 `timed_naming.exam_name()`，本脚本只负责
把存量数据按同一口径刷一遍（导入 / 新建流程已自动调用它）。

用法：
    py -3.12 -u scripts/migrate/migrate_timed_rename_format_20261021.py
    py -3.12 -u scripts/migrate/migrate_timed_rename_format_20261021.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:                                       # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))       # _db_backup / _mig_log

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models.timed import TimedExam                        # noqa: E402
from app.modules.grades.services.timed_naming import exam_name  # noqa: E402


def plan_changes():
    """算出所有需要改名的场次：[(exam, new_name), ...]（不改对象）"""
    groups = defaultdict(list)
    for exam in TimedExam.query.all():
        groups[exam.grade].append(exam)
    out = []
    for grade, rows in sorted(groups.items()):
        rows.sort(key=lambda e: (e.exam_date, e.id))
        for idx, exam in enumerate(rows, start=1):
            new = exam_name(grade, exam.exam_date, idx)
            if (exam.name or '') != new:
                out.append((exam, new))
    return out


def print_report(changes):
    """打印逐场差异（Dry-run 与执行前都看这一份）"""
    for exam, new in changes:
        old = (exam.name or '')[:38]
        print(f'  #{exam.id:<4} {exam.grade} {exam.exam_date} '
              f'{old:38s} → {new}')
    print(f'\n共 {len(changes)} 场需要改名')


def main():
    ap = argparse.ArgumentParser(description='限时练场次改名（A44）')
    ap.add_argument('--apply', action='store_true', help='真正写库')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        changes = plan_changes()
        print_report(changes)
        if not changes:
            print('[SKIP] 名称已符合口径')
            return
        if not args.apply:
            print('[dry-run] 未写库；确认后加 --apply')
            return
        # 改库前备份 timed.db（与 A43 同口径，备份失败直接抛错中止）
        from _db_backup import backup_db
        backup_db(str(ROOT / 'data' / 'timed.db'),
                  tag='before_timed_rename_format_20261021')
        for exam, new in changes:
            exam.name = new
        db.session.commit()
        print('[APPLIED] 已提交')
        try:
            import _mig_log
            _mig_log.record(
                Path(__file__).name,
                note=f'限时练改名「届+日期+限时练No.N」，共 {len(changes)} 场')
        except Exception as exc:                        # noqa: BLE001
            print(f'[WARN] 迁移登记失败：{exc}')


if __name__ == '__main__':
    main()

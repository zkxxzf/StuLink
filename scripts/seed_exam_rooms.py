# -*- coding: utf-8 -*-
# StuLink v1.18.7.0 2026-09-30
# 考务考场房间库种子：全校可用考场（2~5 层，每层 11 间，共 44 间）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""考场房间库批量导入（幂等，可按楼层/方向复跑）

学校可作为考场的房间（用户 2026-09-29 口径）：以 2 层为例，房间号上下层对应，
每层 11 间——
    北：202 204 207 209
    南：227 225 222 220 218
    东：211 215
共 2~5 层，即 11 × 4 = 44 间。

用法：
    python -m scripts.seed_exam_rooms                      # dry-run（只报告）
    python -m scripts.seed_exam_rooms --apply              # 新增 44 间（保留既有）
    python -m scripts.seed_exam_rooms --apply --replace    # 先清空旧库再新增 44 间
    python -m scripts.seed_exam_rooms --apply --capacity 25

说明：
- 按 location 唯一约束去重，重复执行不会产生重复行；
- `--replace` 只清 `affair_room_libs`（房间库），**不动** `affair_rooms`（各批次已编排
  的考场是独立快照，location/capacity 自带副本），因此历史批次的考场与考号不受影响；
- 默认容量 30 座（国标考场常规间距），后续可在「考场设置」页逐间调整。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                      # noqa: E402
from app.extensions import db                   # noqa: E402
from app.models.grades import AffairRoomLib     # noqa: E402

# 每层固定的房间末两位（按用户给出的顺序：北 → 南 → 东）
WINGS = [
    ('北', (2, 4, 7, 9)),
    ('南', (27, 25, 22, 20, 18)),
    ('东', (11, 15)),
]
FLOORS = (2, 3, 4, 5)
DEFAULT_CAPACITY = 30


def build_rooms(capacity: int = DEFAULT_CAPACITY):
    """生成 (location, capacity, note) 列表：44 间，按楼层→方向→房号排序"""
    rooms = []
    for fl in FLOORS:
        for wing, suffixes in WINGS:
            for s in suffixes:
                rooms.append((f'{wing}{fl}{s:02d}', capacity, f'{fl}楼·{wing}侧'))
    return rooms


def main():
    ap = argparse.ArgumentParser(description='批量导入考场房间库（2~5 层，每层 11 间）')
    ap.add_argument('--apply', action='store_true', help='实际写库；缺省为 dry-run')
    ap.add_argument('--replace', action='store_true',
                    help='先清空现有房间库再导入（不影响各批次已编排的考场）')
    ap.add_argument('--capacity', type=int, default=DEFAULT_CAPACITY,
                    help=f'每间默认容量（缺省 {DEFAULT_CAPACITY}）')
    args = ap.parse_args()

    rooms = build_rooms(args.capacity)
    app = create_app()
    with app.app_context():
        existing = {r.location: r for r in AffairRoomLib.query.all()}
        print(f'现有房间库: {len(existing)} 条；本次清单: {len(rooms)} 间')

        removed = 0
        if args.replace and existing:
            if not args.apply:
                print(f'[DRY-RUN] 将删除现有 {len(existing)} 条：{sorted(existing)}')
            else:
                for r in existing.values():
                    db.session.delete(r)
                removed = len(existing)
                AffairRoomLib.query.session.flush()
                existing = {}

        added = updated = skipped = 0
        for loc, cap, note in rooms:
            row = existing.get(loc)
            if row:
                if row.capacity != cap or (row.note or '') != note:
                    if args.apply:
                        row.capacity = cap
                        row.note = note
                    updated += 1
                else:
                    skipped += 1
                continue
            if args.apply:
                db.session.add(AffairRoomLib(location=loc, capacity=cap, note=note))
            added += 1

        if args.apply:
            db.session.commit()
            total = AffairRoomLib.query.count()
            cap_sum = sum(r.capacity or 0 for r in AffairRoomLib.query.all())
        else:
            db.session.rollback()
            total = len(existing) + added
            cap_sum = sum(c for _, c, _ in rooms)

        tag = 'APPLIED' if args.apply else 'DRY-RUN（未写库）'
        print(f'\n===== {tag} =====')
        if removed:
            print(f'删除旧库: {removed} 条')
        print(f'新增 {added} | 更新 {updated} | 已存在跳过 {skipped}')
        print(f'导入后房间库共 {total} 间，总容量 {cap_sum} 座')
        print('\n前 12 间预览：')
        for loc, cap, note in rooms[:12]:
            print(f'  {loc:<8} {cap} 座  {note}')
        if not args.apply:
            print('\n确认无误后执行：python -m scripts.seed_exam_rooms --apply --replace')


if __name__ == '__main__':
    main()

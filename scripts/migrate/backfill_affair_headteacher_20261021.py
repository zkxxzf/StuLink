# StuLink v1.19.49.52 2026-10-21
# 考务环节④ 任课表回填「班主任」列（历史批次）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""给历史考务批次补「班主任」列（迁移 A44）。

背景（2026-10-21 用户口径）：环节④ 核对任课教师表原先只有「班级 × 科目」，
班主任这一列被漏掉了 —— 但班主任和任课教师同级别，负责本班总分。
本次代码已恢复该列（新批次带入 / 重新带入都会写），老批次需要本脚本补齐。

数据来源（按优先级，**只补不覆盖**，人工改派过的行不会被改）：
1. 本批次关联考试（`exam_affairs.exam_id`）的任课快照 `exam_teacher_links`
   里 subject='班主任' 的行 —— 即**考试当时**的班主任；
2. 快照没有时，用当前 `user_class_links`（该年级）的班主任名单（标记为推测）。

范围：只补本批次已经存在的班级（`affair_teachers` ∪ `affair_students` 的班级），
不会给批次外班级凭空加行。

用法：
    py -3.12 -u scripts/migrate/backfill_affair_headteacher_20261021.py          # 预览
    py -3.12 -u scripts/migrate/backfill_affair_headteacher_20261021.py --apply  # 写库
"""
from __future__ import annotations

import argparse
import sys
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
from app.models.grades import (AffairStudent, AffairTeacher,   # noqa: E402
                               ExamAffair, ExamTeacherLink)
from app.modules.grades.services.teacher_snapshot_service import (  # noqa: E402
    HEAD_SUBJECT, current_headteacher_rows)


def _classes_of(affair):
    """本批次已有的班级（任课核对行 ∪ 名单），不凭空扩班"""
    names = {t.class_name for t in
             AffairTeacher.query.filter_by(affair_id=affair.id).all() if t.class_name}
    names |= {s.class_name for s in
              AffairStudent.query.filter_by(affair_id=affair.id).all() if s.class_name}
    return names


def main():
    ap = argparse.ArgumentParser(description='回填考务批次任课表的班主任列')
    ap.add_argument('--apply', action='store_true', help='真正写库')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        affairs = ExamAffair.query.order_by(ExamAffair.id).all()
        print(f'考务批次：{len(affairs)} 个')

        # 当前班主任（按年级缓存一次）
        _cur_cache = {}

        def _current(grade):
            if grade not in _cur_cache:
                _cur_cache[grade] = {r['class_name']: r
                                     for r in current_headteacher_rows(grade)}
            return _cur_cache[grade]

        total_new = total_batches = 0
        for a in affairs:
            classes = _classes_of(a)
            if not classes:
                continue
            existing = {t.class_name: t for t in
                        AffairTeacher.query.filter_by(
                            affair_id=a.id, subject=HEAD_SUBJECT).all()}
            # 1) 本场考试快照（考试当时的班主任）
            snap = {}
            if a.exam_id:
                for r in ExamTeacherLink.query.filter_by(
                        exam_id=a.exam_id, subject=HEAD_SUBJECT).all():
                    if r.teacher_name:
                        snap[r.class_name] = r
            cur = _current(a.grade)
            todo = []
            for cls in sorted(classes):
                if cls in existing:
                    continue
                src = snap.get(cls)
                if src is not None:
                    todo.append({'class_name': cls,
                                 'user_id': src.user_id,
                                 'teacher_name': src.teacher_name,
                                 'source': 'backfill'})
                elif cls in cur:
                    todo.append({'class_name': cls,
                                 'user_id': cur[cls].get('user_id'),
                                 'teacher_name': cur[cls]['teacher_name'],
                                 'source': 'backfill'})
            if not todo:
                continue
            total_batches += 1
            total_new += len(todo)
            print(f'\n批次 #{a.id} {a.grade} {a.name}')
            for t in todo:
                hit = '快照' if t['class_name'] in snap else '当前名单'
                print(f"  + {t['class_name']:6s} {t['teacher_name']}（{hit}）")
                if args.apply:
                    db.session.add(AffairTeacher(
                        affair_id=a.id, grade=a.grade, class_name=t['class_name'],
                        subject=HEAD_SUBJECT, user_id=t['user_id'],
                        teacher_name=t['teacher_name'], source=t['source']))

        print(f'\n需补 {total_new} 行（{total_batches} 个批次）')
        if not args.apply:
            print('[dry-run] 未写库；确认后加 --apply')
            return
        # 写前备份 grades.db（affair_teachers 在 grades 库）
        from _db_backup import backup_db
        backup_db(str(ROOT / 'data' / 'grades.db'),
                  tag='before_affair_headteacher_20261021')
        db.session.commit()
        print('[APPLIED] 已提交')
        try:
            import _mig_log
            _mig_log.record(Path(__file__).name,
                            note=f'考务环节④ 任课表补班主任列：{total_new} 行 / '
                                 f'{total_batches} 个批次')
        except Exception as exc:                # noqa: BLE001
            print(f'[WARN] 迁移登记失败：{exc}')


if __name__ == '__main__':
    main()

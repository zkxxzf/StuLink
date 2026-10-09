# -*- coding: utf-8 -*-
# StuLink v1.18.8.0 2026-10-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""班型层次与高一「全科」初始化（幂等，默认 dry-run）

背景（用户口径 2026-09-30）：
- 高一入校尚未选科，选科组合应为「全科」；需在选科字典中单独增加该项
- 班型分三层次预留：强基班 > 卓越班 > 普通班；当前除强基班外一律记为卓越班，
  「普通班」只入字典备用（暂不分配到任何班级）
- 高一（入学年份最大的一届）的在班学生打上「全科」标签；「不分班」学生不打

本次执行范围：
1. 字典补全：subject += 全科（置顶）；class_type += 普通班
2. ClassProfile 补全与分档：以「学生表实际数字教学班 ∪ 已有记录」为准，
   缺失记录按 (grade, class_name) 创建；class_type 为空 → 卓越班（不覆盖已有值）
3. 高一教学班学生 subject_selection 为空 → 全科

用法：
    python -m scripts.setup_class_tiers              # dry-run 预览
    python -m scripts.setup_class_tiers --apply      # 实际写库
    python -m scripts.setup_class_tiers --apply --grade 2025级   # 指定年级打全科

幂等：重复执行不会重复插入字典项、不会覆盖已有班型、不会改已有选科。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models import ClassProfile, DictCategory, DictItem, Student  # noqa: E402
from app.modules.grades.utils import is_teaching_class        # noqa: E402

FULL_SUBJECT = '全科'
TIER_TOP = '强基班'
TIER_MID = '卓越班'
TIER_BASE = '普通班'


def _top_grade():
    """高一 = 入学年份最大的一届（如 2026级）"""
    grades = [r[0] for r in db.session.query(Student.grade).distinct().all() if r[0]]
    def _year(g):
        digits = ''.join(ch for ch in str(g) if ch.isdigit())
        return int(digits) if digits else 0
    return max(grades, key=_year) if grades else ''


def ensure_dict_item(category_code, value, sort_order=None, apply=False):
    """幂等补字典项；返回 'added' / 'exists' / 'no-category'"""
    cat = DictCategory.query.filter_by(code=category_code).first()
    if not cat:
        return 'no-category'
    existing = DictItem.query.filter_by(category_id=cat.id, value=value).first()
    if existing:
        if not existing.is_active and apply:
            existing.is_active = True
        return 'exists'
    if apply:
        db.session.add(DictItem(category_id=cat.id, value=value,
                                sort_order=(sort_order if sort_order is not None else 0),
                                is_active=True))
    return 'added'


def main():
    ap = argparse.ArgumentParser(description='班型层次与高一全科初始化（幂等）')
    ap.add_argument('--apply', action='store_true', help='实际写库；缺省为 dry-run')
    ap.add_argument('--grade', default='', help='打「全科」的年级；缺省=入学年份最大的一届')
    ap.add_argument('--grades', default='',
                    help='限定处理的年级，逗号分隔；缺省=已存在班型记录的年级')
    args = ap.parse_args()
    tag = 'APPLIED' if args.apply else 'DRY-RUN（未写库）'

    app = create_app()
    with app.app_context():
        grade = args.grade.strip() or _top_grade()
        # 处理范围：缺省仅限“已有班型记录”的年级，避免为无关年级凭空造记录
        active = [g.strip() for g in args.grades.split(',') if g.strip()]
        if not active:
            active = sorted({p.grade for p in ClassProfile.query.all() if p.grade})
        print(f'目标年级（打全科）: {grade}')
        print(f'处理年级（班型）  : {active}')
        print(f'模式: {tag}\n')

        # ---- 1. 字典补全 ----
        print('=== 1) 字典补全 ===')
        for code, value, order in (('subject', FULL_SUBJECT, -1),
                                   ('class_type', TIER_BASE, None)):
            r = ensure_dict_item(code, value, order, apply=args.apply)
            print(f'  [{code}] {value}: {r}')
        if args.apply:
            db.session.commit()

        # ---- 2. ClassProfile 补全与分档 ----
        print('\n=== 2) ClassProfile 补全与分档 ===')
        # 以学生表实际教学班 ∪ 已有记录为准
        rows = db.session.query(Student.grade, Student.class_name).distinct().all()
        wanted = set()
        for g, cls in rows:
            if g and is_teaching_class(cls):
                wanted.add((g, cls))
        for p in ClassProfile.query.all():
            wanted.add((p.grade, p.class_name))

        created, promoted, kept = [], [], []
        skipped_grades = set()
        for g, cls in sorted(wanted):
            if g not in active:
                skipped_grades.add(g)
                continue
            p = ClassProfile.query.filter_by(grade=g, class_name=cls).first()
            if p is None:
                if args.apply:
                    db.session.add(ClassProfile(grade=g, class_name=cls,
                                                class_type=TIER_MID))
                created.append(f'{g}{cls}')
                continue
            if not (p.class_type or '').strip():
                if args.apply:
                    p.class_type = TIER_MID
                promoted.append(f'{g}{cls}')
            else:
                kept.append(f'{g}{cls}={p.class_type}')
        if args.apply:
            db.session.commit()
        print(f'  新建记录并置{TIER_MID}: {len(created)} 个')
        for x in created:
            print(f'    + {x}')
        print(f'  空班型 → {TIER_MID}: {len(promoted)} 个')
        for x in promoted:
            print(f'    ~ {x}')
        print(f'  已有班型（不动）: {len(kept)} 个')
        for x in kept:
            print(f'    = {x}')
        if skipped_grades:
            print(f'  未处理年级（无班型记录，需显式 --grades 才处理）: '
                  f'{sorted(skipped_grades)}')

        # ---- 3. 高一打全科 ----
        print(f'\n=== 3) {grade} 教学班学生 → {FULL_SUBJECT} ===')
        n_all = Student.query.filter_by(grade=grade).count()
        n_teach = 0
        n_empty = 0
        n_set = 0
        for s in Student.query.filter_by(grade=grade).all():
            if not is_teaching_class(s.class_name):
                continue
            n_teach += 1
            if not (s.subject_selection or '').strip():
                n_empty += 1
                if args.apply:
                    s.subject_selection = FULL_SUBJECT
                    n_set += 1
        if args.apply:
            db.session.commit()
        print(f'  {grade} 共 {n_all} 人；教学班 {n_teach} 人；'
              f'选科为空 {n_empty} 人 → 已置 {FULL_SUBJECT} {n_set if args.apply else n_empty} 人')
        skipped = n_all - n_teach
        print(f'  跳过（非教学班，如“不分班”）{skipped} 人')

        # ---- 4. 结果核对 ----
        print('\n=== 4) 结果核对 ===')
        sub = DictCategory.query.filter_by(code='subject').first()
        if sub:
            vals = [i.value for i in DictItem.query.filter_by(category_id=sub.id)
                    .order_by(DictItem.sort_order, DictItem.id).all()]
            print(f'  [subject] {len(vals)} 项: {vals}')
        ct = DictCategory.query.filter_by(code='class_type').first()
        if ct:
            vals = [i.value for i in DictItem.query.filter_by(category_id=ct.id)
                    .order_by(DictItem.sort_order, DictItem.id).all()]
            print(f'  [class_type] {len(vals)} 项: {vals}')
        from collections import Counter
        cnt = Counter((p.class_type or '（空）') for p in ClassProfile.query.all())
        print(f'  ClassProfile 共 {ClassProfile.query.count()} 条；班型分布: {dict(cnt)}')
        if not args.apply:
            print('\n确认无误后执行：python -m scripts.setup_class_tiers --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())

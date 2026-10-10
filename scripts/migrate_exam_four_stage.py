# -*- coding: utf-8 -*-
# StuLink v1.19.0 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""四段式重构 一期迁移：考试两维度 + 赋分/原始双轨 + 考号

做四件事（全部幂等，可重复执行）：
  1. exams 加列：exam_kind（性质）/ score_mode（分数口径）
  2. exam_scores 加列：exam_no（考号）/ raw_score（原始分）+ 考号索引
  3. 字典：新增 exam_kind（联考/本校考/小测验）；exam_type 补阶段项（限时练/高三一测/高三二测）
  4. 老数据回填：按考试名推断 exam_kind 与阶段 exam_type；score_mode 默认 converted
     （历史 2024/2025 级成绩本来就是赋分，与默认一致）

用法：
    python -m scripts.migrate_exam_four_stage            # dry-run：只检查、不改库
    python -m scripts.migrate_exam_four_stage --apply    # 实际执行
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRADES_DB = os.path.join(BASE, 'data', 'grades.db')

# (表, 列, DDL 类型)
NEW_COLUMNS = [
    ('exams', 'exam_kind', 'VARCHAR(20)'),
    ('exams', 'score_mode', "VARCHAR(10) DEFAULT 'converted'"),
    ('exam_scores', 'exam_no', 'VARCHAR(20)'),
    ('exam_scores', 'raw_score', 'FLOAT'),
]
NEW_INDEXES = [
    ('idx_scores_exam_no', 'exam_scores', 'exam_id, exam_no'),
]

# 阶段（exam_type）标准取值
STAGE_ITEMS = [('月考', 1), ('期中考试', 2), ('期末考试', 3), ('限时练', 4),
               ('高三一测', 5), ('高三二测', 6)]
# 性质（exam_kind）标准取值
KIND_ITEMS = [('联考', 1), ('本校考', 2), ('小测验', 3)]

# 老数据回填：按考试名判断性质 / 阶段（顺序敏感，先具体后笼统）
NAME_RULES = [
    ('限时练', '小测验', '限时练'),
    ('联考', '联考', '月考'),
    ('期中', '本校考', '期中考试'),
    ('期末', '本校考', '期末考试'),
    ('月考', '本校考', '月考'),
]


def _columns(cur, table):
    return [r[1] for r in cur.execute(f'PRAGMA table_info({table})').fetchall()]


def schema_step(apply: bool, out: list):
    if not os.path.exists(GRADES_DB):
        out.append('[跳过] 未找到 %s（首次启动会自动建表）' % GRADES_DB)
        return 0
    conn = sqlite3.connect(GRADES_DB)
    cur = conn.cursor()
    n = 0
    for table, col, ddl in NEW_COLUMNS:
        have = _columns(cur, table)
        if col in have:
            out.append('  [已有] %s.%s' % (table, col))
            continue
        out.append('  [新增列] %s.%s %s' % (table, col, ddl))
        if apply:
            cur.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
        n += 1
    for idx, table, cols in NEW_INDEXES:
        have = [r[1] for r in cur.execute(
            "SELECT type, name FROM sqlite_master WHERE type='index'").fetchall()]
        if idx in have:
            out.append('  [已有] 索引 %s' % idx)
            continue
        out.append('  [新增索引] %s ON %s(%s)' % (idx, table, cols))
        if apply:
            cur.execute(f'CREATE INDEX IF NOT EXISTS {idx} ON {table}({cols})')
        n += 1
    if apply:
        conn.commit()
    conn.close()
    return n


def data_step(apply: bool, out: list):
    """字典补项 + 老数据回填（需 ORM）"""
    from app import create_app
    from app.extensions import db
    from app.models import DictCategory, DictItem

    app = create_app()
    changed = 0
    with app.app_context():
        # --- 字典：exam_kind（新类别）---
        cat = DictCategory.query.filter_by(code='exam_kind').first()
        if cat is None:
            out.append('  [新增字典类别] exam_kind（考试性质）')
            if apply:
                cat = DictCategory(code='exam_kind', name='考试性质')
                db.session.add(cat)
                db.session.flush()
            changed += 1
        if cat is not None or apply:
            have = {i.value for i in (cat.items.all() if cat else [])}
            for v, so in KIND_ITEMS:
                if v in have:
                    continue
                out.append('    [字典项] exam_kind += %s' % v)
                if apply:
                    db.session.add(DictItem(category_id=cat.id, value=v,
                                            sort_order=so, is_active=True))
                changed += 1
        # --- 字典：exam_type 补阶段项 ---
        cat2 = DictCategory.query.filter_by(code='exam_type').first()
        if cat2 is not None:
            have2 = {i.value for i in cat2.items.all()}
            for v, so in STAGE_ITEMS:
                if v in have2:
                    continue
                out.append('    [字典项] exam_type += %s' % v)
                if apply:
                    db.session.add(DictItem(category_id=cat2.id, value=v,
                                            sort_order=so, is_active=True))
                changed += 1
        else:
            out.append('  [警告] 未找到 exam_type 字典类别，跳过补项')
        # 停用被取代的旧阶段值（期中→期中考试 / 期末→期末考试；联考已升为“性质”维度）
        if cat2 is not None:
            for old in ('期中', '期末', '联考'):
                it = next((i for i in cat2.items.all()
                           if i.value == old and i.is_active), None)
                if it is None:
                    continue
                out.append('    [停用字典项] exam_type 停用 %s' % old)
                if apply:
                    it.is_active = False
                changed += 1
        # 字典项先落库：下面的回填走独立 sqlite3 连接，不能跟着 ORM 一起提交
        if apply:
            db.session.commit()

        # --- 老数据回填：exam_kind / score_mode / exam_type ---
        # 用原生 SQL：dry-run 时新列可能尚不存在，用 ORM 会报 no such column
        out.append('  --- 老考试回填 ---')
        conn = sqlite3.connect(GRADES_DB)
        cur = conn.cursor()
        cols = _columns(cur, 'exams')
        has_kind, has_mode = 'exam_kind' in cols, 'score_mode' in cols
        rows = cur.execute(
            'SELECT id, name, exam_type%s%s FROM exams ORDER BY id'
            % (', exam_kind' if has_kind else ', NULL',
               ', score_mode' if has_mode else ', NULL')).fetchall()
        stage_values = [r[2] for r in NAME_RULES]
        for eid, nm, etype, ekind, emode in rows:
            nm, etype = nm or '', (etype or '')
            ekind, emode = (ekind or '').strip(), (emode or '').strip()
            new_kind, new_stage = None, None
            for kw, kind, stage in NAME_RULES:
                if kw in nm:
                    new_kind, new_stage = kind, stage
                    break
            need, sets = [], []
            if not ekind and new_kind:
                need.append('性质=%s' % new_kind)
                if has_kind:
                    sets.append(('exam_kind', new_kind))
            if new_stage and etype not in stage_values:
                need.append('阶段=%s（原 %s）' % (new_stage, etype or '空'))
                sets.append(('exam_type', new_stage))
            if emode not in ('converted', 'raw') and has_mode:
                need.append('分数口径=赋分')
                sets.append(('score_mode', 'converted'))
            if not need:
                continue
            out.append('  #%-3s %-30s -> %s' % (eid, nm[:28], '，'.join(need)))
            if apply and sets:
                cur.execute('UPDATE exams SET %s WHERE id=?'
                            % ', '.join('%s=?' % c for c, _v in sets),
                            [v for _c, v in sets] + [eid])
            changed += 1
        if apply:
            conn.commit()
        conn.close()
    return changed


def main():
    ap = argparse.ArgumentParser(description='四段式重构一期迁移（幂等）')
    ap.add_argument('--apply', action='store_true', help='实际写库；缺省 dry-run')
    args = ap.parse_args()
    apply = args.apply

    print('模式: %s\n' % ('APPLIED' if apply else 'DRY-RUN（未改库）'))
    out = []
    print('=== 1) 表结构 ===')
    n1 = schema_step(apply, out)
    print('\n'.join(out))
    out.clear()
    print('\n=== 2) 字典与老数据 ===')
    n2 = data_step(apply, out)
    print('\n'.join(out))
    print('\n合计：结构改动 %d 项，数据改动 %d 项' % (n1, n2))
    if not apply:
        print('\n确认无误后执行：python -m scripts.migrate_exam_four_stage --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""迁移：综合组组长归并为「整组一位」（2026-10-14 用户口径）。

用户口径（"综合组是一个组，所以他们只有一个学科组长……不分高一、高二、高三，
直接整一个学科组长"）：整个综合组（体育 / 音乐 / 美术 / 信息技术 / 通用技术 / 心理）
**只设 1 名组长**，落库 `subject='综合组'` / `grade=''` / `category='综合组组长'`。

本脚本处理**旧形态存量**（该口径二次修订前，六科按「学科 × 年级」各存记录）：
- 同一 (学年, 学期) 内已存在 `subject='综合组'` 的记录 → 删除旧的六科记录；
- 否则把六科记录里**优先教研组长、其次 updated_at 最新**的一条改写成综合组形态，
  其余删除（新口径下页面 / 导出 / 徽章只认那一条，留多条就是幽灵行）。
- 无旧形态记录时**空跑**（打印跳过），可安全重复执行。

⚠ 归并会丢弃多余的历史记录 —— 改库前自动备份 academic.db。
用法：`python scripts/migrate/migrate_leader_group_single_20261014.py`
"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys

sys.path.insert(0, _ROOT)

SCRIPT_NAME = os.path.basename(__file__)


def _record(note):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _mig_log
        _mig_log.record(SCRIPT_NAME, note=note)
    except Exception as e:  # noqa: BLE001  登记失败不影响迁移
        print(f'[WARN] 迁移登记不可用（仅不登记）：{e}')


def main():
    from flask import current_app
    from app import create_app
    from app.extensions import db
    from app.models.academic import (COMPREHENSIVE_GROUP, COMPREHENSIVE_SUBJECTS,
                                     LEADER_CATEGORY_COMPREHENSIVE,
                                     LEADER_CATEGORY_RESEARCH, SubjectLeader)

    app = create_app()
    with app.app_context():
        old = (SubjectLeader.query
               .filter(SubjectLeader.subject.in_(list(COMPREHENSIVE_SUBJECTS)))
               .all())
        if not old:
            print('[SKIP] 无旧形态综合组记录（无需归并）')
            _record('无旧形态记录（空跑）')
            return 0

        uri = (current_app.config.get('SQLALCHEMY_BINDS', {}) or {}).get('academic', '')
        path = uri.replace('sqlite:///', '')
        if path and os.path.exists(path):
            from scripts._db_backup import backup_db
            backup_db(path)

        buckets = {}
        for ld in old:
            buckets.setdefault((ld.school_year, ld.term or ''), []).append(ld)

        merged = removed = 0
        for (year, term), rows in sorted(buckets.items()):
            cur = SubjectLeader.query.filter_by(school_year=year, term=term,
                                                subject=COMPREHENSIVE_GROUP).first()
            if cur is None:
                keep = next((r for r in rows
                             if (r.category or '') == LEADER_CATEGORY_RESEARCH), None)
                if keep is None:
                    keep = sorted(rows,
                                  key=lambda r: (r.updated_at or r.created_at),
                                  reverse=True)[0]
                print(f'[MERGE] {year} {term or "整学年"}：{keep.subject} / '
                      f'{keep.grade or "全校"} / {keep.category} → 综合组组长'
                      f'（{keep.leader_name}）')
                keep.subject = COMPREHENSIVE_GROUP
                keep.grade = ''
                keep.category = LEADER_CATEGORY_COMPREHENSIVE
                rows = [r for r in rows if r.id != keep.id]
                merged += 1
            for r in rows:
                print(f'[DEL  ] {year} {term or "整学年"}：{r.subject} / '
                      f'{r.grade or "全校"} / {r.category}（{r.leader_name}）')
                db.session.delete(r)
                removed += 1
        db.session.commit()
        print(f'[DONE] 归并 {merged} 条 / 删除多余 {removed} 条')
        _record(f'综合组归并为整组一位（归并 {merged} / 删除 {removed}）')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())

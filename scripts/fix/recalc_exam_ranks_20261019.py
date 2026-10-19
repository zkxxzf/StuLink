# -*- coding: utf-8 -*-
"""2026-10-19 第十一轮审计：重算历史考试的并列名次（1,1,3 跳号）。

## 为什么需要跑

`app/modules/grades/services/ranking.py::_rank_map` 原先用 ``rank += 1``
（密集名次 1,1,2），与文件头注释、``honor_rank``（按位置 1,1,3）以及全系统
「同分同名次、后延跳号」口径冲突。代码已修正为 ``rank = idx + 1``；但历史
考库存的 ``rank_dir`` / ``rank_class`` / ``move_rank`` 仍是旧值，需要重算一次。

## 用法

::

    py -3.12 scripts/fix/recalc_exam_ranks_20261019.py            # 预览
    py -3.12 scripts/fix/recalc_exam_ranks_20261019.py --apply    # 写库

## 安全

- 默认 dry-run，只统计「会变的名次行数」，不写库。
- ``--apply`` 前自动备份 ``grades.db``（保留最近若干份）。
- 幂等：同值重跑不会产生变化。
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

from app import create_app                                  # noqa: E402
from app.extensions import db                                # noqa: E402
from app.models.grades import Exam, ExamScore                # noqa: E402
from app.modules.grades.services.ranking import (            # noqa: E402
    _rank_map,
    recalc_exam,
)

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv

try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（不影响重算）：{_e}')


def _group(rows, key_fn):
    groups = {}
    for r in rows:
        if r.score is None:
            continue
        groups.setdefault(key_fn(r), []).append(r)
    return groups


def count_changes(exam_id):
    """只读统计：按新算法应与现库不同的行数（rank_dir + rank_class）。"""
    rows = ExamScore.query.filter_by(exam_id=exam_id).all()
    by_id = {r.id: r for r in rows}
    changed = 0
    for key_fn, col in ((lambda r: (r.subject, r.direction), 'rank_dir'),
                        (lambda r: (r.subject, r.direction, r.class_name),
                         'rank_class')):
        for g_rows in _group(rows, key_fn).values():
            for rid, rank in _rank_map(g_rows).items():
                if getattr(by_id[rid], col) != rank:
                    changed += 1
    return changed


def main():
    a = create_app()
    with a.app_context():
        exams = Exam.query.order_by(Exam.id).all()
        todo = []
        total = 0
        for exam in exams:
            n = count_changes(exam.id)
            if n:
                todo.append((exam, n))
                total += n
        print(f'  共 {len(exams)} 场考试；需要重算 {len(todo)} 场，'
              f'涉及 {total} 个名次值')
        for exam, n in todo[:20]:
            title = (exam.name or exam.title_with_dimensions() or '')
            print(f'    #{exam.id} {title[:42]}：{n} 行')
        if len(todo) > 20:
            print(f'    …其余 {len(todo) - 20} 场略')

        if not todo:
            print('\n  已全部是 1,1,3 口径，无需重算。')
            return
        if not APPLY:
            print('\n  预览模式，未改动。加 --apply 执行。')
            return
        if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
            print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过重算')
            return

        # 2026-10-19 第十三轮审计：原传相对文件名 'grades.db'，
        # backup_db 找不到文件会静默「跳过」并继续写库（等于没备份）。
        # 改为绝对路径，且备份失败一律中止（fail-closed）。
        from config import BASE_DIR
        from scripts._db_backup import backup_db
        db_file = _os.path.join(BASE_DIR, 'data', 'grades.db')
        backup = backup_db(db_file, tag='before_recalc_ranks_20261019')
        if not backup or not backup[0][1]:
            print(f'  [ABORT] 备份失败（{db_file}），已中止，未写库')
            return
        print(f'  已备份：{backup[0][1]}')

        for exam, _n in todo:
            recalc_exam(exam.id)
            db.session.commit()
        print(f'  完成：重算 {len(todo)} 场')
        if _mig_log is not None:
            try:
                _mig_log.record(
                    SCRIPT_NAME,
                    note='按 1,1,3 跳号口径重算全部考试的方向/班排名')
            except Exception as _e:  # noqa: BLE001
                print(f'[WARN] 迁移登记失败（不影响结果）：{_e}')


if __name__ == '__main__':
    main()

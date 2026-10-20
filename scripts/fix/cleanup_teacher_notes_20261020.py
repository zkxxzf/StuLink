# -*- coding: utf-8 -*-
"""2026-10-20 教师档案清理（用户口径）。

1. 清掉历史回填备注「由 scripts.sync_users_to_teachers 回填」——备注栏只留业务信息；
2. 「外籍」统一填「否」（`is_foreign = 0`）：全校无外籍教师，未填会让
   教基4149 的「#外籍人员」处于未填状态。模型侧已加 `default=False`，
   本脚本只补存量。

## 用法

::

    py -3.12 scripts/fix/cleanup_teacher_notes_20261020.py            # 预览
    py -3.12 scripts/fix/cleanup_teacher_notes_20261020.py --apply    # 写库

## 安全

- 默认 dry-run；``--apply`` 前自动备份 ``academic.db``；
- 幂等：备注已清、外籍已填的行不再变化。
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
from app.models.academic import Teacher                      # noqa: E402

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv
SYNC_NOTE = '由 scripts.sync_users_to_teachers 回填'

try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（不影响执行）：{_e}')


def main():
    app = create_app()
    with app.app_context():
        if _mig_log and _mig_log.is_applied(SCRIPT_NAME):
            print(f'[SKIP] {SCRIPT_NAME} 已执行过（登记表已有记录）')
            return 0

        n_note = Teacher.query.filter(Teacher.note == SYNC_NOTE).count()
        n_foreign = Teacher.query.filter(Teacher.is_foreign.is_(None)).count()
        print('== 教师档案清理 ==')
        print(f'  回填备注待清：{n_note} 条（{SYNC_NOTE}）')
        print(f'  外籍待补「否」：{n_foreign} 条')

        if not APPLY:
            print('\n[DRY-RUN] 未写库；确认无误后加 --apply')
            return 0

        from _db_backup import backup_db
        backup_db(os.path.join(_ROOT, 'data', 'academic.db'),
                  tag='before_teacher_cleanup_20261020')
        Teacher.query.filter(Teacher.note == SYNC_NOTE).update(
            {'note': None}, synchronize_session=False)
        Teacher.query.filter(Teacher.is_foreign.is_(None)).update(
            {'is_foreign': False}, synchronize_session=False)
        db.session.commit()
        if _mig_log:
            _mig_log.record(SCRIPT_NAME,
                            note=f'清回填备注 {n_note}、外籍补否 {n_foreign}')
        print(f'\n[APPLIED] 清备注 {n_note} 条、外籍补否 {n_foreign} 条')
        return 0


if __name__ == '__main__':
    sys.exit(main())

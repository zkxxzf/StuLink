# -*- coding: utf-8 -*-
"""2026-10-20 删除两个不再需要的账号（用户点名）。

- `bzr001`（实名「班主任」）：早期占位账号（`school_viewer`，2026-08-26 建），
  不是自然人；对应的教师记录 `T4QXH3QGT` 也是占位数据，一并删除。
- `zhoujili`（周继莉）：本轮为《教师基本信息表》补录教师时自动开的号；
  只删账号，**保留教师记录**（人仍在名册，`teachers.user_id` 置空）。

## 处理范围

- `system.db / users`：删除这两行；
- `system.db / user_permission_groups`：随 ORM 级联删除（delete-orphan）；
- `academic.db / teachers`：「班主任」整行删除；「周继莉」`user_id` 置 NULL；
- `system.db / operation_logs`：删除这两行账号**自身的**日志（bzr001 只有
  2026-08-26 的 3 条登录/登出，无业务动作）；`user_id` 上有 `NO ACTION` 外键，
  不先清日志会 `FOREIGN KEY constraint failed`。

## 用法

::

    py -3.12 scripts/fix/remove_placeholder_users_20261020.py            # 预览
    py -3.12 scripts/fix/remove_placeholder_users_20261020.py --apply    # 执行

## 安全

- 默认 dry-run；``--apply`` 前自动备份 ``system.db`` 与 ``academic.db``；
- 执行前校验「用户名 + 实名」双匹配，任一不符即中止；
- 幂等：账号已不存在时跳过。
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
from app.models import User                                  # noqa: E402
from app.models.academic import Teacher                      # noqa: E402
from app.models.operation_log import OperationLog            # noqa: E402

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv

# (用户名, 实名, 是否连教师记录一起删)
TARGETS = [
    ('bzr001', '班主任', True),
    ('zhoujili', '周继莉', False),
]

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

        plan = []
        for username, real_name, drop_teacher in TARGETS:
            u = User.query.filter_by(username=username).first()
            if u is None:
                print(f'[SKIP] {username} 不存在')
                continue
            if (u.real_name or '').strip() != real_name:
                print(f'[ABORT] {username} 的实名是「{u.real_name}」，与预期「{real_name}」不符')
                return 3
            t = Teacher.query.filter_by(user_id=u.id).first()
            n_log = OperationLog.query.filter_by(user_id=u.id).count()
            plan.append((u, t, drop_teacher, n_log))

        print('== 删除账号 ==')
        for u, t, drop_teacher, n_log in plan:
            print(f'  - {u.username}（{u.real_name}，id={u.id}，{u.role}）'
                  f'；教师记录：{t.name if t else "无"}'
                  f'{"（一并删除）" if (t and drop_teacher) else "（仅解绑）" if t else ""}'
                  f'；自身日志：{n_log} 条（一并删除）')

        if not APPLY:
            print('\n[DRY-RUN] 未删库；确认无误后加 --apply')
            return 0

        from _db_backup import backup_db
        backup_db([os.path.join(_ROOT, 'data', 'system.db'),
                   os.path.join(_ROOT, 'data', 'academic.db')],
                  tag='before_remove_users_20261020')

        for u, t, drop_teacher, n_log in plan:
            if n_log:
                OperationLog.query.filter_by(user_id=u.id).delete(
                    synchronize_session=False)
                print(f'[删除] 账号自身日志 {n_log} 条')
            if t is not None:
                if drop_teacher:
                    db.session.delete(t)
                    print(f'[删除] 教师记录 {t.name}（{t.teacher_uid}）')
                else:
                    t.user_id = None
                    print(f'[解绑] 教师记录 {t.name}（{t.teacher_uid}）')
            db.session.delete(u)
            print(f'[删除] 账号 {u.username}（{u.real_name}）')
        db.session.commit()
        if _mig_log:
            _mig_log.record(SCRIPT_NAME, note='删除占位账号 bzr001 与 zhoujili')
        print(f'\n[APPLIED] 已处理 {len(plan)} 个账号')
        return 0


if __name__ == '__main__':
    sys.exit(main())

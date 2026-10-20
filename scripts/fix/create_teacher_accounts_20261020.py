# -*- coding: utf-8 -*-
"""2026-10-20 为新增教师批量开登录账号（初始密码清单导出）。

## 背景

`import_teacher_roster_20261020.py` 补录了 41 位教师，但系统里本就没有他们的
登录账号（该批次账号绑定数 = 0）。本脚本为「教师记录存在、`user_id` 为空」的
人开号：

- 用户名：姓名全拼（`user_account.gen_username`，重名自动追加 01/02…），
  与现有 94 位教师账号口径一致（不使用手机号）；
- 初始密码：8 位随机字母数字（`user_account.random_password`），
  `must_change_pwd=True`，首次登录强制改密；
- 角色：`teacher`（对应「教师」权限组，与导入向导自动开户一致）；
- 结果写入 `data/教师初始账号_YYYYmmdd_HHMMSS.csv`（UTF-8 BOM，含明文初始密码，
  仅落 `data/`，该目录不入版本控制）。

## 用法

::

    py -3.12 scripts/fix/create_teacher_accounts_20261020.py            # 预览
    py -3.12 scripts/fix/create_teacher_accounts_20261020.py --apply    # 开号并导出密码

## 安全

- 默认 dry-run，只列出将开的用户名，不产生密码、不写库；
- ``--apply`` 前自动备份 ``system.db``（账号）与 ``academic.db``（绑定关系）；
- 幂等：已有 `user_id` 的教师跳过；重复执行不会重复开号。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, _ROOT)

from app import create_app                                  # noqa: E402
from app.extensions import db                                # noqa: E402
from app.models import User                                  # noqa: E402
from app.models.academic import Teacher                      # noqa: E402
from app.modules.grades.services import user_account         # noqa: E402

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv

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

        teachers = (Teacher.query.filter(Teacher.user_id.is_(None))
                    .order_by(Teacher.id).all())
        plan, skipped = [], []
        for t in teachers:
            same = User.query.filter_by(real_name=t.name).all()
            if same:
                skipped.append((t.name, f'已有同名账号 {same[0].username}，请人工绑定'))
                continue
            username = user_account.gen_username(t.name)
            if not username:
                skipped.append((t.name, '无法生成拼音用户名，请手工开户'))
                continue
            plan.append((t, username))

        print('== 教师开号 ==')
        print(f'  待开号：{len(plan)} 人')
        for t, username in plan:
            print(f'    - {t.name} -> {username}（{t.teacher_uid}）')
        if skipped:
            print(f'  跳过：{len(skipped)} 人')
            for n, why in skipped:
                print(f'    - {n}：{why}')

        if not APPLY:
            print('\n[DRY-RUN] 未开号；确认无误后加 --apply')
            return 0

        from _db_backup import backup_db
        backup_db([os.path.join(_ROOT, 'data', 'system.db'),
                   os.path.join(_ROOT, 'data', 'academic.db')],
                  tag='before_teacher_accounts_20261020')

        rows, failed = [], []
        for t, username in plan:
            try:
                user, pwd = user_account.create_teacher_account(
                    t.name, username, role='teacher')
            except Exception as exc:  # noqa: BLE001
                failed.append((t.name, str(exc)))
                continue
            t.user_id = user.id
            rows.append({'name': t.name, 'username': user.username,
                         'password': pwd, 'teacher_uid': t.teacher_uid})
        db.session.commit()
        if _mig_log:
            _mig_log.record(SCRIPT_NAME, note=f'教师开号 {len(rows)} 个，'
                                              f'跳过 {len(skipped) + len(failed)}')

        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = os.path.join(_ROOT, 'data', f'教师初始账号_{stamp}.csv')
        with open(out, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['姓名', '登录用户名', '初始密码', '教师编号'])
            for r in rows:
                w.writerow([r['name'], r['username'], r['password'],
                            r['teacher_uid']])

        print(f'\n[APPLIED] 已开号 {len(rows)} 个，失败 {len(failed)} 个')
        for n, why in failed:
            print(f'    - {n}：{why}')
        print(f'初始密码清单：{out}')
        return 0


if __name__ == '__main__':
    sys.exit(main())

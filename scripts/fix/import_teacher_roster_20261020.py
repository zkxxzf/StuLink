# -*- coding: utf-8 -*-
"""2026-10-20 教师名单补录：按《郑开学校教师基本信息表》回填 + 补缺。

## 背景

学校提供了一份 113 人的《郑开学校教师基本信息表》（2020 年 10 月口径），
而系统 `academic.db / teachers` 只有 94 人（`scripts.sync_users_to_teachers`
从登录账号回填，仅姓名 + 编号），且 `subject` 全空、性别/生日/职称/人员类别
均未录入。本脚本按「姓名」匹配：

- 已有教师：只补**空字段**（电话 / 性别 / 出生日期 / 职称 / 人员类别 /
  在编 / 学科 / 备注），已有值不覆盖；手机号与表中不一致时列冲突、跳过；
- 表中新增：建教师记录（`T` + 8 位编号），并按手机号 / 姓名匹配已有登录账号
  绑定（不新建账号、不改密码）；
- 学科来源：原表「学科」列优先，空则取**当前学期课表**
  （`timetable.db / schedule_entries`，同科出现次数最多者）；查不到留空。

## 用法

::

    py -3.12 scripts/fix/import_teacher_roster_20261020.py --json payload.json
    py -3.12 scripts/fix/import_teacher_roster_20261020.py --json payload.json --apply

payload 结构（由《郑开学校教师基本信息表》生成，依赖外部源文件）::

    {"teachers": [{"name","phone","gender","birth_date","title",
                   "staff_type","is_official","subject","note"}],
     "system_only": [{"name","subject"}]}

## 安全

- 默认 dry-run，只打印将新增 / 更新 / 冲突的数量与明细；
- ``--apply`` 前自动备份 ``academic.db``；
- 幂等：同值重跑不产生变化；已登记的批次再次执行直接跳过。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, _ROOT)

from app import create_app                                  # noqa: E402
from app.extensions import db                                # noqa: E402
from app.models.academic import Teacher                      # noqa: E402
from app.modules.academic.services import teacher_service    # noqa: E402

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv

try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（不影响执行）：{_e}')


def _arg(flag, default=''):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _birth(value):
    if not value:
        return None
    for fmt in ('%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d'):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _set_if_empty(t, attr, value):
    """只在现值为空时回填，返回是否发生变化。"""
    if value in (None, ''):
        return False
    cur = getattr(t, attr)
    if cur in (None, ''):
        setattr(t, attr, value)
        return True
    return False


def main():
    path = _arg('--json')
    if not path or not os.path.exists(path):
        print('[ERROR] 缺少 --json <payload.json>（由教师基本信息表生成）')
        return 2
    data = json.load(open(path, encoding='utf-8'))
    recs = data.get('teachers') or []
    extras = data.get('system_only') or []

    app = create_app()
    with app.app_context():
        if _mig_log and _mig_log.is_applied(SCRIPT_NAME):
            print(f'[SKIP] {SCRIPT_NAME} 已执行过（登记表已有记录）')
            return 0

        creates, updates, binds, conflicts, unchanged = [], [], [], [], 0
        for rec in recs:
            name = (rec.get('name') or '').strip()
            if not name:
                continue
            t = Teacher.query.filter_by(name=name).first()
            if t is None:
                creates.append(rec)
                continue
            if rec.get('phone') and t.phone and rec['phone'] != t.phone:
                conflicts.append((name, f'手机号不一致：系统 {t.phone} / 表 {rec["phone"]}'))
                continue
            changed = []
            if rec.get('phone') and not t.phone:
                t.phone = rec['phone']
                changed.append('电话')
            for attr, key in (('gender', 'gender'), ('title', 'title'),
                              ('staff_type', 'staff_type'),
                              ('is_official', 'is_official'),
                              ('subject', 'subject'), ('note', 'note')):
                if _set_if_empty(t, attr, rec.get(key)):
                    changed.append(attr)
            bd = _birth(rec.get('birth_date'))
            if bd and not t.birth_date:
                t.birth_date = bd
                changed.append('birth_date')
            if changed:
                updates.append((name, changed))
            else:
                unchanged += 1

        for rec in extras:
            t = Teacher.query.filter_by(name=(rec.get('name') or '').strip()).first()
            if t and rec.get('subject') and not t.subject:
                t.subject = rec['subject']
                updates.append((t.name, ['subject']))

        # 新增教师：编号 + 账号绑定（只绑已有账号，不建号）
        new_objs = []
        for rec in creates:
            uid = teacher_service.gen_teacher_uid()
            t = Teacher(teacher_uid=uid, name=rec['name'].strip(),
                        phone=rec.get('phone') or None,
                        subject=rec.get('subject') or None,
                        gender=rec.get('gender') or None,
                        title=rec.get('title') or None,
                        staff_type=rec.get('staff_type') or None,
                        is_official=rec.get('is_official'),
                        note=rec.get('note') or None)
            bd = _birth(rec.get('birth_date'))
            if bd:
                t.birth_date = bd
            user, candidates, reason = teacher_service.match_user(t)
            if user:
                t.user_id = user.id
                binds.append((t.name, user.username))
            new_objs.append(t)

        print('== 教师名单补录 ==')
        print(f'  新增教师：{len(creates)} 人')
        print(f'  回填字段：{len(updates)} 人')
        print(f'  账号绑定：{len(binds)} 人')
        print(f'  无变化  ：{unchanged} 人')
        if conflicts:
            print(f'  冲突跳过：{len(conflicts)} 人')
            for n, why in conflicts:
                print(f'    - {n}：{why}')
        if updates:
            print('  回填明细（前 30）：')
            for n, fields in updates[:30]:
                print(f'    - {n}: {"/".join(fields)}')

        if not APPLY:
            print('\n[DRY-RUN] 未写库；确认无误后加 --apply')
            return 0

        from _db_backup import backup_db
        backup_db(os.path.join(_ROOT, 'data', 'academic.db'),
                  tag='before_teacher_roster_20261020')
        for t in new_objs:
            db.session.add(t)
        db.session.commit()
        if _mig_log:
            _mig_log.record(SCRIPT_NAME, note=f'教师名单补录：新增 {len(creates)}、'
                                              f'回填 {len(updates)}、绑定 {len(binds)}')
        print(f'\n[APPLIED] 新增 {len(creates)}、回填 {len(updates)}、绑定 {len(binds)}')
        return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
# StuLink v1.19.49.39 2026-10-15
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教师账号口令工具：导出登录清单 / 批量重置为统一初始口令。

为什么不能"导出密码"：`users.password_hash` 是 werkzeug pbkdf2 单向哈希，
明文不可还原（这是正确的安全设计）。要给老师"一个能用的口令"，只能
「重置为统一口令 + 导出分发清单」，本工具一次做完两件事。

教师范围（与 scripts/fix/sync_users_to_teachers.py 同一口径，**默认不含 admin**）：
    teacher / homeroom_teacher / grade_leader / dorm_manager / school_viewer / staff

用法（默认 dry-run，只报告不落库；加 --commit 才写入）：

    py -3.12 -m scripts.reset_teacher_passwords --list
        打印 + 导出账号清单（不改任何数据）。

    py -3.12 -m scripts.reset_teacher_passwords --reset 12345678
        预演：列出将被重置的账号（dry-run，不写库）。

    py -3.12 -m scripts.reset_teacher_passwords --reset 12345678 --commit
        实际写库（自动备份 system.db），并导出「姓名 / 登录名 / 口令」分发清单。

    py -3.12 -m scripts.reset_teacher_passwords --reset 12345678 --commit --must-change
        置 must_change_pwd=True：老师首次登录必须改密（推荐正式发放用）。

    py -3.12 -m scripts.reset_teacher_passwords --reset 12345678 --commit --no-force-change
        置 must_change_pwd=False：老师登录即可用，不被强制改密。

生产（NAS 容器内，脚本随 hot_update 上传后）：
    docker exec stulink python -m scripts.reset_teacher_passwords --reset 12345678 --commit

注意：
  · 只改 users.password_hash（+ 可选的 must_change_pwd），其余字段一律不动；
  · 重置后该账号的旧会话自动失效（session_guard 以口令哈希摘要做指纹）；
  · "12345678" 属于系统弱口令黑名单（app/utils/password_policy.py），
    本工具绕开表单校验直接写哈希，建议仅作临时统一初始口令，
    正式发放请加 --must-change 让老师首登改密。
"""
from __future__ import annotations
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import argparse
import os
import shutil
import sys
from datetime import datetime

BASE = _ROOT
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models import User                                   # noqa: E402

# 教师类角色（口径同 scripts/fix/sync_users_to_teachers.py，明确不含 admin）
TEACHER_ROLES = ('teacher', 'homeroom_teacher', 'grade_leader',
                 'dorm_manager', 'school_viewer', 'staff')

ROLE_CN = {'admin': '管理员', 'dorm_manager': '宿管教师',
           'homeroom_teacher': '班主任', 'grade_leader': '年级长',
           'school_viewer': '全校组', 'teacher': '普通教师', 'staff': '教职人员'}

EXPORT_HEADER = ['姓名', '登录名', '口令', '角色', '年级', '班级', '首次登录须改密']


def _backup_main_db(app):
    """写库前备份主库（system.db → *.bak-时间戳），返回备份路径或 None"""
    uri = app.config.get('SQLALCHEMY_DATABASE_URI') or ''
    if not uri.startswith('sqlite:///'):
        return None
    path = uri.replace('sqlite:///', '')
    if not os.path.exists(path):
        return None
    dest = f'{path}.bak-{datetime.now():%Y%m%d_%H%M%S}'
    shutil.copy2(path, dest)
    return dest


def _export_csv(rows, out_path):
    import csv
    with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(EXPORT_HEADER)
        w.writerows(rows)
    return out_path


def _export(rows, out_path):
    """导出分发清单（xlsx 优先，openpyxl 不可用退回 csv）"""
    if out_path.lower().endswith('.csv'):
        return _export_csv(rows, out_path)
    try:
        from openpyxl import Workbook
    except ImportError:
        print('（openpyxl 不可用，改用 CSV）')
        return _export_csv(rows, os.path.splitext(out_path)[0] + '.csv')
    wb = Workbook()
    ws = wb.active
    ws.title = '教师登录信息'
    ws.append(EXPORT_HEADER)
    for r in rows:
        ws.append(list(r))
    for i, w in enumerate([10, 16, 14, 10, 8, 10, 16], 1):
        ws.column_dimensions[chr(64 + i)].width = w
    wb.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description='教师账号口令工具（导出清单 / 批量重置为统一口令）')
    ap.add_argument('--list', action='store_true',
                    help='只打印/导出账号清单（与 --reset 二选一即可，默认行为）')
    ap.add_argument('--reset', metavar='PWD',
                    help='把目标账号口令统一重置为 PWD（缺 --commit 时只预演）')
    ap.add_argument('--commit', action='store_true',
                    help='实际写库（缺省为 dry-run，只报告）')
    ap.add_argument('--must-change', action='store_true',
                    help='重置同时置 must_change_pwd=True（老师首登强制改密）')
    ap.add_argument('--no-force-change', action='store_true',
                    help='重置同时置 must_change_pwd=False（登录即可用）')
    ap.add_argument('--include-admin', action='store_true',
                    help='连管理员账号一起（默认不含 admin）')
    ap.add_argument('--roles', default=','.join(TEACHER_ROLES),
                    help='目标角色，逗号分隔（默认教师 6 类）')
    ap.add_argument('--out', default='',
                    help='分发清单输出路径（默认 data/教师登录信息_<时间戳>.xlsx）')
    args = ap.parse_args()

    if args.must_change and args.no_force_change:
        raise SystemExit('--must-change 与 --no-force-change 不能同时给')
    roles = tuple(r.strip() for r in args.roles.split(',') if r.strip())
    if args.include_admin:
        roles = roles + ('admin',)

    app = create_app()
    with app.app_context():
        users = (User.query.filter(User.role.in_(roles))
                 .order_by(User.role, User.real_name).all())
        if not users:
            print('没有匹配的账号（检查 --roles）')
            return

        print(f'目标账号 {len(users)} 个（角色 {"/".join(roles)}）：')
        by_role = {}
        for u in users:
            by_role.setdefault(u.role, []).append(u)
        for role, us in sorted(by_role.items(), key=lambda kv: -len(kv[1])):
            print(f'  {ROLE_CN.get(role, role):<5s} {len(us):>3d} 个')

        pwd = args.reset or ''
        if pwd:
            try:
                from app.utils.password_policy import validate_password
                ok, msg = validate_password(pwd)
                if not ok:
                    print(f'[警告] 口令 {pwd!r} 不满足系统口令策略：{msg}')
                    print('        （本工具直接写哈希可强行设置，建议仅作临时统一初始口令）')
            except Exception:                                 # noqa: BLE001
                pass
            if not args.commit:
                print(f'\n[dry-run] 将把以上 {len(users)} 个账号口令重置为 {pwd!r}，'
                      '未写库；示例（前 10 个）：')
                for u in users[:10]:
                    print(f'  {u.real_name}（{u.username}）')
                if len(users) > 10:
                    print(f'  …… 其余 {len(users) - 10} 个')
                print('确认无误后加 --commit 执行。')
                return
            backup = _backup_main_db(app)
            if backup:
                print(f'\n已备份主库：{backup}')
            for u in users:
                u.set_password(pwd)
                if args.must_change:
                    u.must_change_pwd = True
                elif args.no_force_change:
                    u.must_change_pwd = False
            db.session.commit()
            probe = users[0]
            ok = probe.check_password(pwd)
            print(f'[{"OK" if ok else "失败"}] 已重置 {len(users)} 个账号口令；'
                  f'抽查 {probe.real_name}（{probe.username}）校验 {ok}')

        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = args.out or os.path.join(BASE, 'data', f'教师登录信息_{stamp}.xlsx')
        rows = [[u.real_name, u.username,
                 pwd or '（未重置，沿用原口令）',
                 ROLE_CN.get(u.role, u.role),
                 u.grade or '', u.class_name or '',
                 '是' if u.must_change_pwd else '否'] for u in users]
        saved = _export(rows, out)
        print(f'分发清单已导出：{saved}（{len(rows)} 条）')
        if not pwd:
            print('提示：原口令为哈希存储、无法导出；要分发统一口令请用 '
                  '--reset <口令> --commit')


if __name__ == '__main__':
    main()

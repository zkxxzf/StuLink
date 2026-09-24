# -*- coding: utf-8 -*-
# StuLink v1.18.2.1 2026-09-24
# 一次性回填：把 system.users 中的"教师类账号"同步进 academic.teachers
#   （使 /users/ 教师管理 与 /academic/teachers 教师名单 数据一致）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""
用法（本地）：
    python -m scripts.sync_users_to_teachers            # dry-run（默认，只报告不落库）
    python -m scripts.sync_users_to_teachers --commit   # 实际写入
    python -m scripts.sync_users_to_teachers --commit --csv report.csv

规则（与产品规范一致）：
1) **管理员（role=admin）不是教师**，永不同步进 academic.teachers
2) 教师类角色：teacher / homeroom_teacher / grade_leader / dorm_manager / school_viewer / staff
   —— 这些都会回填
3) 匹配优先级（避免重复建条）：
   a) academic.teachers.user_id == users.id → 已关联，跳过
   b) academic.teachers.phone == users.username 且 username 像手机号 → 补 user_id
   c) academic.teachers.name == users.real_name 且 user_id 为空 → 补 user_id
   d) 否则新建一条 Teacher（uid 自动生成 / phone 若 username 是手机号则填 / status=active）
4) 幂等：多次运行结果一致（第二次基本全 skip）
5) 已存在但 user_id 关联到已被删除的 users → 清空该 user_id（供下次重跑再匹配）
"""
from __future__ import annotations

import argparse
import csv
import re
import sys

from app import create_app
from app.extensions import db
from app.models import User
from app.models.academic import Teacher
from app.modules.academic.services import teacher_service

PHONE_RE = re.compile(r'^1[3-9]\d{9}$')

# 视为"教师"的角色（明确不含 admin）
TEACHER_ROLES = ('teacher', 'homeroom_teacher', 'grade_leader',
                 'dorm_manager', 'school_viewer', 'staff')


def _row(uid, action, detail):
    return {'user_uid': uid, 'action': action, 'detail': detail}


def sync(commit: bool) -> tuple[list, dict]:
    """返回 (变更明细列表, 汇总计数 dict)"""
    report, stats = [], {'insert': 0, 'link_by_phone': 0, 'link_by_name': 0,
                        'skip': 0, 'orphan_cleared': 0}
    # 一次性把已存在的 users.id 索引出来（避免每行都查询）
    all_user_ids = {u.id for u in User.query.with_entities(User.id).all()}
    for u in User.query.filter(User.role.in_(TEACHER_ROLES)).order_by(User.real_name).all():
        phone_like = u.username if PHONE_RE.match(u.username or '') else None

        # a) 已按 user_id 关联
        ex = Teacher.query.filter_by(user_id=u.id).first()
        if ex:
            report.append(_row(u.id, 'skip', f'{u.real_name} 已在 teachers#{ex.id}({ex.teacher_uid})'))
            stats['skip'] += 1
            continue

        # b) 按 phone 匹配未关联的 teacher
        if phone_like:
            m = Teacher.query.filter_by(phone=phone_like, user_id=None).first()
            if m:
                m.user_id = u.id
                stats['link_by_phone'] += 1
                report.append(_row(u.id, 'link_by_phone',
                                   f'{u.real_name} ↔ teachers#{m.id}({m.teacher_uid}) 手机号匹配'))
                continue

        # c) 按 name 匹配未关联的 teacher
        m = Teacher.query.filter_by(name=u.real_name, user_id=None).first()
        if m:
            m.user_id = u.id
            # 若该 Teacher 之前无 phone，把 username 里的手机号补进去
            if phone_like and not m.phone:
                m.phone = phone_like
            stats['link_by_name'] += 1
            report.append(_row(u.id, 'link_by_name',
                               f'{u.real_name} ↔ teachers#{m.id}({m.teacher_uid}) 姓名匹配'))
            continue

        # d) 新建
        uid = teacher_service.gen_teacher_uid()
        t = Teacher(teacher_uid=uid, name=u.real_name, phone=phone_like,
                    subject=None, status='active', user_id=u.id,
                    note='由 scripts.sync_users_to_teachers 回填')
        db.session.add(t)
        stats['insert'] += 1
        report.append(_row(u.id, 'insert', f'{u.real_name} → 新 teachers({uid})'))

    # 清理"孤儿 user_id"（关联到已不存在的 users 记录）
    for t in Teacher.query.filter(Teacher.user_id.isnot(None)).all():
        if t.user_id not in all_user_ids:
            t.user_id = None
            stats['orphan_cleared'] += 1
            report.append(_row(t.id, 'orphan_cleared',
                               f'teachers#{t.id}({t.teacher_uid}) 原 user_id 不存在，已清空'))

    if commit:
        db.session.commit()
    else:
        db.session.rollback()
    return report, stats


def main():
    ap = argparse.ArgumentParser(description='把 users 中的教师类账号回填到 academic.teachers')
    ap.add_argument('--commit', action='store_true', help='实际写入；缺省为 dry-run')
    ap.add_argument('--csv', help='把变更明细导出为 CSV 便于审阅')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        total = User.query.count()
        teachers = User.query.filter(User.role.in_(TEACHER_ROLES)).count()
        admin = User.query.filter_by(role='admin').count()
        ex = Teacher.query.count()
        print(f'当前状态: users 总 {total} / 教师类 {teachers} / 管理员 {admin}；teachers 表 {ex}')
        report, stats = sync(commit=args.commit)
        tag = 'COMMIT' if args.commit else 'DRY-RUN'
        print(f'\n===== {tag} 结果 =====')
        for k, v in stats.items():
            print(f'  {k:15s} {v}')
        # 打印前 20 条明细
        for r in report[:20]:
            print(f'  [{r["action"]:15s}] user#{r["user_uid"]}  {r["detail"]}')
        if len(report) > 20:
            print(f'  ... 共 {len(report)} 条，完整清单见 --csv 输出')
        if args.csv:
            with open(args.csv, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, fieldnames=['user_uid', 'action', 'detail'])
                w.writeheader()
                w.writerows(report)
            print(f'\nCSV 已写入 {args.csv}')
        if not args.commit:
            print('\n（预演模式未写库；如需实际执行加 --commit）')
    print('SYNC_DONE')


if __name__ == '__main__':
    sys.exit(main())

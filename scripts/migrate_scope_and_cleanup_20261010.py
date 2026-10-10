# -*- coding: utf-8 -*-
"""迁移/报告：教师班级范围自动生成 + 历史膨胀数据清理（2026-10-10）。

一、user_class_links 由任课映射自动生成（**默认执行，只新增不删**）
    现在只有 9 条（9 位班主任手工关联），36 位任课教师没有班级范围，导致权限判定
    （如"能不能申请调课"）只能靠放宽规则兜底。这里按成绩管理的任课映射
    （teacher_subject_links：谁教哪班）为每个教师生成班级关联，三边（教务/成绩/权限）
    就此对齐。幂等：已存在的 (user_id, 年级, 班) 跳过。

二、历史 / 批量导入的膨胀数据清理（**默认 dry-run，加 --apply 才真删**）
    - 未启用班级（class_profiles.is_active=0）
    - 2099级 测试残留账号（users / 关联的 teachers）
    - 落在未启用班级里的学生（students）：**只统计、不删除**（跨库无外键约束，
      删学生属不可逆的数据破坏，见下方 ① 处说明）
    执行前会再次整目录备份。

用法：
    python scripts/migrate_scope_and_cleanup_20261010.py          # 生成范围 + 只打印清单
    python scripts/migrate_scope_and_cleanup_20261010.py --apply   # 连清理一起执行
"""
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.modules.academic.services import teaching_scope_service as ts  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, 'data')
BACKUP_DIR = os.path.join(BASE, 'data_backup_20261010')
APPLY = '--apply' in sys.argv


def backup():
    if os.path.exists(BACKUP_DIR):
        print('[OK] 备份已存在：', BACKUP_DIR)
        return
    shutil.copytree(DATA_DIR, BACKUP_DIR)
    print('[OK] 已备份 data/ →', BACKUP_DIR)


def gen_user_class_links():
    """按任课映射为每个教师生成班级关联（只新增）。"""
    eng = db.engines['system']
    cols = {c['name'] for c in sa.inspect(eng).get_columns('user_class_links')}
    if not {'user_id', 'grade', 'class_name'} <= cols:
        print('[SKIP] user_class_links 结构不符：', sorted(cols))
        return
    active = set(ts.active_class_pairs())
    with db.engines['grades'].begin() as c:
        rows = c.exec_driver_sql(
            'SELECT user_id, grade, class_name FROM teacher_subject_links '
            'WHERE COALESCE(active,1)=1').fetchall()
    with eng.begin() as c:
        have = {(r[0], r[1], r[2]) for r in c.exec_driver_sql(
            'SELECT user_id, grade, class_name FROM user_class_links')}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    to_add = []
    seen = set()
    for u, g, cn in rows:
        if not u or (g, cn) not in active:
            continue
        key = (u, g, cn)
        if key in have or key in seen:
            continue
        seen.add(key)
        to_add.append((u, g, cn))
    extra = ', created_at' if 'created_at' in cols else ''
    extra_v = ', ?' if 'created_at' in cols else ''
    with eng.begin() as c:
        for u, g, cn in to_add:
            args = (u, g, cn) + ((now,) if extra else ())
            c.exec_driver_sql(
                'INSERT INTO user_class_links (user_id, grade, class_name%s) VALUES (?,?,?%s)'
                % (extra, extra_v), args)
    print('[OK] 教师班级关联：新增 %d 条（原有 %d 条）' % (len(to_add), len(have)))


def cleanup_report(apply_now):
    """打印清理清单；apply_now 为真才执行删除。"""
    sys_eng = db.engines['system']
    with sys_eng.begin() as c:
        inactive_cls = c.exec_driver_sql(
            'SELECT COUNT(*) FROM class_profiles WHERE COALESCE(is_active,1)=0').scalar()
        stu = c.exec_driver_sql(
            'SELECT COUNT(*) FROM students s WHERE NOT EXISTS (SELECT 1 FROM '
            'class_profiles p WHERE p.grade=s.grade AND p.class_name=s.class_name '
            'AND COALESCE(p.is_active,1)=1)').scalar()
        users_2099 = c.exec_driver_sql(
            "SELECT id FROM users WHERE grade='2099级'").fetchall()
    print('\n【清理清单】' + ('（本次执行删除）' if apply_now else '（dry-run，未删除）'))
    print('   未启用班级档案：%d 条' % inactive_cls)
    print('   落在未启用班级的学生：%d 条' % stu)
    print('   2099级账号：%d 个' % len(users_2099))
    if not apply_now:
        print('   → 确认无误后执行：python scripts/migrate_scope_and_cleanup_20261010.py --apply')
        return
    # ① 学生：**一律保留，绝不删除**。
    #    原实现直接 DELETE 未启用班级里的学生，并假设"会被业务表外键拦住从而保留"——
    #    但本项目按设计文档 2.2「跨库快照关联、不建跨库外键」，students 属 system.db，
    #    考勤/成绩/宿舍/积分分属各库，SQLite 跨库外键本就不生效，该假设不成立，
    #    一旦 --apply 会真删学生并留下孤儿数据。删学生属不可逆的数据破坏，
    #    此处只报告不删除；确需清理请先人工核对引用清单。
    print('   [SKIP] 学生记录一律保留（跨库无外键约束，删除会毁业务数据）')
    # ② 班级关联里指向未启用班的，先清掉（避免外键挡住删班级）
    try:
        with sys_eng.begin() as c:
            c.exec_driver_sql(
                'DELETE FROM user_class_links WHERE rowid IN ('
                'SELECT l.rowid FROM user_class_links l WHERE NOT EXISTS ('
                'SELECT 1 FROM class_profiles p WHERE p.grade=l.grade '
                'AND p.class_name=l.class_name AND COALESCE(p.is_active,1)=1))')
    except Exception as e:  # noqa: BLE001
        print('[WARN] 班级关联清理跳过：', str(e)[:70])
    # ③ 未启用班级档案
    try:
        with sys_eng.begin() as c:
            c.exec_driver_sql('DELETE FROM class_profiles WHERE COALESCE(is_active,1)=0')
    except Exception as e:  # noqa: BLE001
        print('[WARN] 班级档案被外键引用，已保留：', str(e)[:70])
    # ④ 2099级 测试账号（先清关联再删人）
    uids = [r[0] for r in users_2099]
    if uids:
        try:
            with sys_eng.begin() as c:
                c.exec_driver_sql('DELETE FROM user_class_links WHERE user_id IN (%s)'
                                  % ','.join('?' * len(uids)), tuple(uids))
                c.exec_driver_sql('DELETE FROM users WHERE id IN (%s)'
                                  % ','.join('?' * len(uids)), tuple(uids))
        except Exception as e:  # noqa: BLE001
            print('[WARN] 2099级账号删除失败：', str(e)[:70])
    try:
        with db.engines['academic'].begin() as c:
            c.exec_driver_sql('DELETE FROM teachers WHERE user_id NOT IN '
                              '(SELECT id FROM users) AND user_id IS NOT NULL')
    except Exception as e:  # noqa: BLE001
        print('[WARN] 孤儿教师清理跳过：', str(e)[:70])
    with sys_eng.begin() as c:
        print('[OK] 删除完成 → 班级档案剩 %d，学生剩 %d，用户剩 %d' % (
            c.exec_driver_sql('SELECT COUNT(*) FROM class_profiles').scalar(),
            c.exec_driver_sql('SELECT COUNT(*) FROM students').scalar(),
            c.exec_driver_sql('SELECT COUNT(*) FROM users').scalar()))


def main():
    app = create_app()
    with app.app_context():
        backup()
        gen_user_class_links()
        cleanup_report(APPLY)
        return 0


if __name__ == '__main__':
    sys.exit(main())

"""P4 存量库迁移：权限组结构升级（多身份改造）。

运行：python scripts/migrate_perm_multi_20261014.py
（新装库无需运行：PERMISSION_GROUPS 种子已是 11 组基线）

本脚本做什么（幂等、改库前备份）：
  1. 回填/同步范围类型：为每个组设置 scope_kind（由旧 scope_type 映射），
     并保证旧 scope_type 与新 scope_kind 一致（旧读取方不受影响）。
  2. 删除已废弃系统组：校长 / 副校长 / 备课组长 / 教研组长 / 学生发展中心
     （**组内还有用户则跳过**，避免误伤 —— 请先手工转移）。
  3. 「考务数据管理员」→「考试管理员」（有同名目标则转移用户后删除源组）。
  4. 补齐缺失的系统组（生活处 / 政教主任 / 政教员 等），**不覆盖已有组**。

明确不做（按项目约定，见《权限体系说明》§8.6）：
  · 不批量覆盖既有权限组的 menu_keys / 范围配置（存量库与种子已整体脱节，
    批量覆盖会改变现网行为，须由管理员在权限页逐组核对后调整）。

改 system.db 前经 scripts/_db_backup.backup_db 备份（与项目约定一致）。
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_mod
from app import create_app, PERMISSION_GROUPS
from app.extensions import db
from app.models import PermissionGroup, User
from app.models.permission_group import KIND_TO_LEGACY, LEGACY_TO_KIND
from scripts._db_backup import backup_db

DEPRECATED = ['校长', '副校长', '备课组长', '教研组长', '学生发展中心']
RENAME = ('考务数据管理员', '考试管理员')

# permission_groups 新增列（SQLite 的 create_all 不会给已存在的表补列，须显式 ALTER）
NEW_COLUMNS = (('scope_kind', 'VARCHAR(16)'),
               ('scope_grades', 'TEXT'),
               ('scope_classes', 'TEXT'))


def ensure_columns():
    """在 create_app 之前补列（否则启动时查询 PermissionGroup 会报 no such column）。"""
    db_path = config_mod.Config.SQLALCHEMY_DATABASE_URI.replace('sqlite:///', '')
    if not os.path.exists(db_path):
        return
    con = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in con.execute('PRAGMA table_info(permission_groups)')}
        if not cols:
            return          # 表还不存在（新装库），交给 create_all 建表
        missing = [(n, d) for n, d in NEW_COLUMNS if n not in cols]
        if not missing:
            return
        backup_db(db_path)
        for name, ddl in missing:
            con.execute(f'ALTER TABLE permission_groups ADD COLUMN {name} {ddl}')
        con.commit()
        print(f'[OK] permission_groups 补列：{[n for n, _ in missing]}')
    finally:
        con.close()


def main():
    ensure_columns()
    app = create_app()
    with app.app_context():
        db.create_all()
        print('[OK] 数据表已确保（含 scope_kind 列）')

        changes = []

        # 1) 范围类型回填/同步
        for g in PermissionGroup.query.order_by(PermissionGroup.id).all():
            if not (g.scope_kind or '').strip():
                g.scope_kind = LEGACY_TO_KIND.get(g.scope_type or 'none', 'none')
                changes.append(f'[kind] {g.name}: scope_kind ← {g.scope_kind}'
                               f'（由 scope_type={g.scope_type}）')
            new_st = KIND_TO_LEGACY.get(g.effective_kind, 'none')
            if g.scope_type != new_st:
                changes.append(f'[kind] {g.name}: scope_type {g.scope_type} → {new_st}')
                g.scope_type = new_st

        # 2) 删除废弃组（组内无用户才删）
        for name in DEPRECATED:
            g = PermissionGroup.query.filter_by(name=name).first()
            if not g:
                continue
            n = User.query.filter_by(permission_group_id=g.id).count()
            if n:
                changes.append(f'[SKIP] {name}: 组内仍有 {n} 个用户，未删除（请先转移）')
                continue
            changes.append(f'[DEL] 删除废弃组 {name}')
            db.session.delete(g)

        # 3) 考务数据管理员 → 考试管理员
        src = PermissionGroup.query.filter_by(name=RENAME[0]).first()
        if src:
            dst = PermissionGroup.query.filter_by(name=RENAME[1]).first()
            if dst and dst.id != src.id:
                moved = (User.query.filter_by(permission_group_id=src.id)
                         .update({'permission_group_id': dst.id}))
                changes.append(f'[MERGE] {RENAME[0]} → {RENAME[1]}（转移用户 {moved}）')
                db.session.delete(src)
            else:
                src.name = RENAME[1]
                src.scope_kind = 'pick_grades'
                src.scope_type = 'grade'
                changes.append(f'[RENAME] {RENAME[0]} → {RENAME[1]}')

        # 4) 补齐缺失的系统组（不覆盖已有）
        existing = {g.name for g in PermissionGroup.query.all()}
        for p in PERMISSION_GROUPS:
            if p['name'] in existing:
                continue
            g = PermissionGroup(name=p['name'], role=p['role'],
                                scope_type=p['scope_type'],
                                scope_kind=p.get('scope_kind'),
                                description=p['description'])
            g.set_menu_keys(p['menu_keys'])
            db.session.add(g)
            changes.append(f'[ADD] 新增系统组 {p["name"]}（范围 {p.get("scope_kind") or p["scope_type"]}）')

        if not changes:
            print('[SKIP] 已是最新结构，无需改动')
            return

        from flask import current_app
        db_path = current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', '')
        if os.path.exists(db_path):
            backup_db(db_path)
            print('[OK] 已备份 system.db')

        db.session.commit()
        print(f'[DONE] 共 {len(changes)} 项变更：')
        for c in changes:
            print('   ', c)

        print('\n[GROUPS] 迁移后权限组：')
        for g in PermissionGroup.query.order_by(PermissionGroup.id).all():
            n = User.query.filter_by(permission_group_id=g.id).count()
            print(f'   [{g.id}] {g.name}  范围={g.effective_kind}({g.scope_type})  '
                  f'用户数={n}')


if __name__ == '__main__':
    main()

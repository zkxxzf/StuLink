#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本：通知系统 v2.0 收件人表改造（system.db）

内容（全部幂等，可安全重复运行）：
1. CREATE TABLE IF NOT EXISTS notification_recipients（收件人表）
2. notifications 表增量 ALTER TABLE ADD COLUMN（PRAGMA table_info 判断列已存在则跳过）：
   target_uids / recipient_count / read_count / category / biz_type / biz_id / link_url
3. 创建唯一约束索引与复合索引（CREATE ... IF NOT EXISTS）
4. 回填历史数据：按旧可见性逻辑（target_type + target_scope）把每条通知展开成
   收件人行；已读状态从 notification_reads 回填。已存在的 (notification_id, user_id)
   跳过（INSERT OR IGNORE），重复运行不产生重复行。
5. 回填 recipient_count / read_count 快照
6. 打印统计

旧可见性语义（与 app/modules/notifications/services/notification_service.py
的 _user_matches_target 完全一致，保证迁移前后每个用户可见集不变）：
- target_type='all' 或未知类型 → 全体用户
- 'grade' → users.grade ∈ scope['grades']
- 'class' → users.class_name ∈ scope['classes']
- 'role'  → users.role ∈ scope['roles']
- 'users'（新类型，历史数据不存在）→ target_uids 中列出的 username

用法：python scripts/migrate_notification_recipients.py
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
system_db = os.path.join(BASE, 'data', 'system.db')

NEW_COLUMNS = [
    ('target_uids', 'TEXT'),
    ('recipient_count', 'INTEGER DEFAULT 0'),
    ('read_count', 'INTEGER DEFAULT 0'),
    ('category', 'VARCHAR(20)'),
    ('biz_type', 'VARCHAR(30)'),
    ('biz_id', 'INTEGER'),
    ('link_url', 'VARCHAR(200)'),
]


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _existing_columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}


def create_table(conn):
    if _table_exists(conn, 'notification_recipients'):
        print('[SKIP] notification_recipients 表已存在')
    else:
        print('[CREATE] notification_recipients 表...')
        conn.execute('''
            CREATE TABLE notification_recipients (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id INTEGER      NOT NULL,
                user_id         INTEGER      NOT NULL,
                user_uid        VARCHAR(16),
                user_name       VARCHAR(50),
                is_read         BOOLEAN      DEFAULT 0,
                read_at         DATETIME,
                is_deleted      BOOLEAN      DEFAULT 0,
                created_at      DATETIME,
                FOREIGN KEY (notification_id) REFERENCES notifications (id)
            )
        ''')
        conn.commit()
        print('[OK] notification_recipients 表创建成功')


def add_columns(conn):
    have = _existing_columns(conn, 'notifications')
    added = 0
    for col, ddl in NEW_COLUMNS:
        if col in have:
            print(f'[SKIP] notifications.{col} 列已存在')
            continue
        conn.execute(f'ALTER TABLE notifications ADD COLUMN {col} {ddl}')
        added += 1
        print(f'[ADD]  notifications.{col} {ddl}')
    if added:
        conn.commit()
    return added


def create_indexes(conn):
    stmts = [
        # 唯一约束：同一通知同一收件人只有一行（幂等回填依赖它）
        ('uq_notif_recipient',
         'CREATE UNIQUE INDEX IF NOT EXISTS uq_notif_recipient '
         'ON notification_recipients (notification_id, user_id)'),
        # 未读数查询关键路径复合索引
        ('idx_notif_recip_user_read',
         'CREATE INDEX IF NOT EXISTS idx_notif_recip_user_read '
         'ON notification_recipients (user_id, is_read, is_deleted)'),
        ('ix_notification_recipients_notification_id',
         'CREATE INDEX IF NOT EXISTS ix_notification_recipients_notification_id '
         'ON notification_recipients (notification_id)'),
        ('ix_notification_recipients_user_id',
         'CREATE INDEX IF NOT EXISTS ix_notification_recipients_user_id '
         'ON notification_recipients (user_id)'),
        ('ix_notification_recipients_is_read',
         'CREATE INDEX IF NOT EXISTS ix_notification_recipients_is_read '
         'ON notification_recipients (is_read)'),
    ]
    for name, sql in stmts:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        conn.execute(sql)
        print(f'[SKIP] 索引 {name} 已存在' if exists else f'[CREATE] 索引 {name}')
    conn.commit()


def _parse_scope(raw):
    try:
        scope = json.loads(raw or '{}')
    except (json.JSONDecodeError, TypeError):
        scope = {}
    return scope if isinstance(scope, dict) else {}


def _match_users(target_type, scope, target_uids_raw, users):
    """复刻旧 _user_matches_target 语义，返回匹配的用户行列表"""
    tt = (target_type or 'all').strip()
    if tt == 'all':
        return list(users)
    if tt == 'users':
        try:
            uids = set(json.loads(target_uids_raw or '[]'))
        except (json.JSONDecodeError, TypeError):
            uids = set()
        return [u for u in users if u['username'] in uids]
    if tt == 'grade':
        grades = scope.get('grades') or []
        return [u for u in users if (u['grade'] or '') in grades]
    if tt == 'class':
        classes = scope.get('classes') or []
        return [u for u in users if (u['class_name'] or '') in classes]
    if tt == 'role':
        roles = scope.get('roles') or []
        return [u for u in users if (u['role'] or '') in roles]
    # 未知类型：旧逻辑对所有人可见
    return list(users)


def backfill(conn):
    users = [
        {'id': r[0], 'username': r[1], 'real_name': r[2], 'role': r[3],
         'grade': r[4], 'class_name': r[5]}
        for r in conn.execute(
            'SELECT id, username, real_name, role, grade, class_name FROM users')
    ]
    reads = {}  # (notification_id, user_id) -> read_at
    for nid, uid, read_at in conn.execute(
            'SELECT notification_id, user_id, read_at FROM notification_reads'):
        reads[(nid, uid)] = read_at

    notifs = conn.execute(
        'SELECT id, target_type, target_scope, target_uids FROM notifications'
    ).fetchall()

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
    total_new, total_skip, total_read = 0, 0, 0
    for nid, tt, scope_raw, uids_raw in notifs:
        scope = _parse_scope(scope_raw)
        matched = _match_users(tt, scope, uids_raw, users)
        new = skip = read_n = 0
        for u in matched:
            is_read, read_at = (1, reads[(nid, u['id'])]) if (nid, u['id']) in reads else (0, None)
            cur = conn.execute(
                '''INSERT OR IGNORE INTO notification_recipients
                   (notification_id, user_id, user_uid, user_name,
                    is_read, read_at, is_deleted, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 0, ?)''',
                (nid, u['id'], u['username'], u['real_name'], is_read, read_at, now))
            if cur.rowcount > 0:
                new += 1
                read_n += is_read
            else:
                skip += 1
                # 已存在的行也要保证已读状态不丢失（首次回填后被中断的场景）
                if is_read:
                    conn.execute(
                        '''UPDATE notification_recipients
                           SET is_read=1, read_at=COALESCE(read_at, ?)
                           WHERE notification_id=? AND user_id=? AND is_read=0''',
                        (read_at, nid, u['id']))
        print(f'  通知 #{nid} [{tt}]：匹配 {len(matched)} 人，新增 {new}，跳过 {skip}')
        total_new += new
        total_skip += skip
        total_read += read_n

    # 回填快照计数（含 is_deleted 行不计入，与列表口径一致）
    conn.execute('''
        UPDATE notifications SET
            recipient_count = (SELECT COUNT(*) FROM notification_recipients r
                               WHERE r.notification_id = notifications.id
                                 AND r.is_deleted = 0),
            read_count = (SELECT COUNT(*) FROM notification_recipients r
                          WHERE r.notification_id = notifications.id
                            AND r.is_deleted = 0 AND r.is_read = 1)
    ''')
    conn.commit()
    return len(notifs), total_new, total_skip, total_read


def main():
    print('=== 迁移脚本：通知系统 v2.0 收件人表 ===')
    print(f'system 库: {system_db}')
    if not os.path.exists(system_db):
        print('[ERROR] system.db 不存在，请先初始化数据库')
        sys.exit(1)
    conn = sqlite3.connect(system_db)
    try:
        print('\n-- 1) 建表 --')
        create_table(conn)
        print('\n-- 2) notifications 增量列 --')
        add_columns(conn)
        print('\n-- 3) 索引 --')
        create_indexes(conn)
        print('\n-- 4) 回填历史收件人 --')
        n_notif, n_new, n_skip, n_read = backfill(conn)
        print('\n=== 统计 ===')
        print(f'处理通知数：{n_notif}')
        print(f'新增收件人行：{n_new}')
        print(f'跳过（已存在）：{n_skip}')
        print(f'回填已读收件人：{n_read}')
        total = conn.execute('SELECT COUNT(*) FROM notification_recipients').fetchone()[0]
        print(f'notification_recipients 当前总行数：{total}')
        print('=== 迁移完成（幂等，可重复运行） ===')
    except Exception as e:
        print(f'[ERROR] 迁移失败: {e}')
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本：为 graduated_students 表添加 id_card_number 字段并填充历史数据"""
import sqlite3
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
system_db = os.path.join(BASE, 'data', 'system.db')
history_db = os.path.join(BASE, 'data', 'history.db')


def main():
    print('=== 迁移脚本：添加身份证号字段到历史归档 ===')
    print(f'系统库: {system_db}')
    print(f'历史库: {history_db}')
    print()

    if not os.path.exists(system_db):
        print('错误: 系统数据库不存在')
        sys.exit(1)
    if not os.path.exists(history_db):
        print('错误: 历史数据库不存在')
        sys.exit(1)

    sys_conn = sqlite3.connect(system_db)
    hist_conn = sqlite3.connect(history_db)

    try:
        cursor = hist_conn.execute("PRAGMA table_info(graduated_students)")
        columns = [col[1] for col in cursor.fetchall()]
        print(f'当前 graduated_students 表字段: {columns}')

        if 'id_card_number' not in columns:
            print('添加 id_card_number 字段...')
            hist_conn.execute('ALTER TABLE graduated_students ADD COLUMN id_card_number TEXT')
            hist_conn.commit()
            print('字段添加成功')
        else:
            print('id_card_number 字段已存在，跳过添加')

        cursor = hist_conn.execute('SELECT COUNT(*) FROM graduated_students WHERE id_card_number IS NULL OR id_card_number = ""')
        count = cursor.fetchone()[0]
        print(f'需要填充身份证号的记录数: {count}')

        if count > 0:
            cursor = hist_conn.execute('SELECT original_id, student_number FROM graduated_students WHERE id_card_number IS NULL OR id_card_number = ""')
            hist_students = cursor.fetchall()
            print(f'正在从系统库查询 {len(hist_students)} 条记录的身份证号...')

            original_ids = [h[0] for h in hist_students]
            ph = ','.join('?' * len(original_ids))
            cursor = sys_conn.execute(f'SELECT id, id_card_number FROM students WHERE id IN ({ph})', original_ids)
            sys_students = cursor.fetchall()
            id_map = {s[0]: s[1] for s in sys_students}

            updated = 0
            for original_id, student_number in hist_students:
                id_card = id_map.get(original_id)
                if id_card:
                    hist_conn.execute(
                        'UPDATE graduated_students SET id_card_number = ? WHERE original_id = ?',
                        (id_card, original_id)
                    )
                    updated += 1

            hist_conn.commit()
            print(f'已填充 {updated} 条记录的身份证号（密文存储）')

            if updated != len(hist_students):
                print(f'注意: {len(hist_students) - updated} 条记录未找到对应数据（可能已被删除）')
        else:
            print('所有记录已有身份证号数据，跳过填充')

        print()
        print('=== 迁移完成 ===')

    except Exception as e:
        print(f'迁移失败: {e}')
        hist_conn.rollback()
        sys.exit(1)
    finally:
        sys_conn.close()
        hist_conn.close()


if __name__ == '__main__':
    main()

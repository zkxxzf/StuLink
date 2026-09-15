"""选科组合规范化迁移（v1.12.3）

目标：
1. 字典表 subject（选科）按「物理方向 6 种 + 历史方向 6 种」的顺序重排
2. 统一同义异写：物政地 → 物地政、史政地 → 史地政
3. 同步已落库的业务数据（学生选科、班型选科组合、成绩/考务快照中的选科）

说明：
- 幂等：可重复执行
- 执行前自动备份 system.db / grades.db（同目录 .bak-<时间戳>）
- 毕业归档库 history.db 属于历史快照，保持原样不改写（保真）

用法：
    python scripts/migrate_subject_combination_v1123.py
"""
import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

SUBJECTS = [
    '物化生', '物化地', '物化政', '物生地', '物生政', '物地政',
    '史化生', '史化地', '史化政', '史生地', '史生政', '史地政',
]
RENAMES = {'物政地': '物地政', '史政地': '史地政'}


def backup(path):
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = f'{path}.bak-{stamp}'
    shutil.copy2(path, dst)
    return dst


def table_has_column(conn, table, column):
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
    return column in cols


def update_values(conn, table, column, renames):
    """把表中旧写法更新为新写法，返回 (行数, 明细)"""
    if not table_has_column(conn, table, column):
        return 0, []
    detail = []
    total = 0
    for old, new in renames.items():
        cur = conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (new, old))
        if cur.rowcount:
            detail.append(f'{old}→{new}: {cur.rowcount}')
            total += cur.rowcount
    return total, detail


def migrate_dict(conn):
    """重排并重命名字典表 subject 分类"""
    row = conn.execute(
        "SELECT id FROM dict_categories WHERE code='subject'").fetchone()
    if not row:
        return ['未找到 subject 字典分类，跳过']
    cat_id = row[0]
    logs = []

    # 1) 重命名
    for old, new in RENAMES.items():
        exists_new = conn.execute(
            'SELECT 1 FROM dict_items WHERE category_id=? AND value=?',
            (cat_id, new)).fetchone()
        if exists_new:
            cur = conn.execute(
                'DELETE FROM dict_items WHERE category_id=? AND value=?', (cat_id, old))
            if cur.rowcount:
                logs.append(f'字典删除重复项 {old}（{new} 已存在）')
        else:
            cur = conn.execute(
                'UPDATE dict_items SET value=? WHERE category_id=? AND value=?',
                (new, cat_id, old))
            if cur.rowcount:
                logs.append(f'字典重命名 {old}→{new}')

    # 2) 按新顺序设置 sort_order（未在列表中的项排到末尾）
    known = {v: i for i, v in enumerate(SUBJECTS)}
    items = conn.execute(
        'SELECT id, value FROM dict_items WHERE category_id=?', (cat_id,)).fetchall()
    tail = len(SUBJECTS)
    for item_id, value in items:
        order = known.get(value)
        if order is None:
            order = tail
            tail += 1
        conn.execute('UPDATE dict_items SET sort_order=? WHERE id=?', (order, item_id))
    logs.append(f'字典排序完成（共 {len(items)} 项）')

    # 3) 缺失的补上
    existing = {v for _, v in items}
    for i, v in enumerate(SUBJECTS):
        if v not in existing:
            conn.execute(
                'INSERT INTO dict_items (category_id, value, sort_order, is_active) '
                'VALUES (?, ?, ?, 1)', (cat_id, v, i))
            logs.append(f'字典补充缺失项 {v}')
    return logs


def main():
    system_db = os.path.join(DATA_DIR, 'system.db')
    grades_db = os.path.join(DATA_DIR, 'grades.db')

    print('=== 备份 ===')
    for p in (system_db, grades_db):
        b = backup(p)
        print(f'  {os.path.basename(p)} -> {os.path.basename(b) if b else "不存在，跳过"}')

    print('\n=== system.db ===')
    if os.path.exists(system_db):
        conn = sqlite3.connect(system_db)
        for line in migrate_dict(conn):
            print('  ', line)
        for table, column in [('students', 'subject_selection'),
                              ('class_subjects', 'subject_value')]:
            n, detail = update_values(conn, table, column, RENAMES)
            print(f'  {table}.{column}: {n} 行更新 {detail}')
        conn.commit()
        conn.close()
    else:
        print('  不存在，跳过')

    print('\n=== grades.db ===')
    if os.path.exists(grades_db):
        conn = sqlite3.connect(grades_db)
        for table, column in [('exam_scores', 'subject_selection'),
                              ('affair_students', 'subject_selection')]:
            n, detail = update_values(conn, table, column, RENAMES)
            print(f'  {table}.{column}: {n} 行更新 {detail}')
        conn.commit()
        conn.close()
    else:
        print('  不存在，跳过')

    print('\n完成。请重启应用使字典缓存生效。')


if __name__ == '__main__':
    main()

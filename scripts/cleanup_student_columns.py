import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from sqlalchemy import text

app = create_app()

with app.app_context():
    print("=== 清理 students 表中的旧住宿字段 ===")
    
    print("\n1. 检查字段是否存在...")
    
    check_columns_sql = text("""
        PRAGMA table_info(students);
    """)
    columns = db.session.execute(check_columns_sql).fetchall()
    column_names = [col[1] for col in columns]
    
    old_columns = ['boarding_type', 'day_student_type', 'textbook', 'teacher_notes']
    
    print(f"当前表字段: {column_names}")
    
    for col in old_columns:
        if col in column_names:
            print(f"  ✓ {col} 存在")
        else:
            print(f"  ✗ {col} 不存在")
    
    print("\n2. 备份旧数据...")
    
    backup_sql = text("""
        CREATE TABLE IF NOT EXISTS students_backup (
            id INTEGER PRIMARY KEY,
            boarding_type TEXT,
            day_student_type TEXT,
            textbook TEXT,
            teacher_notes TEXT
        );
    """)
    db.session.execute(backup_sql)
    
    insert_backup_sql = text("""
        INSERT OR IGNORE INTO students_backup (id, boarding_type, day_student_type, textbook, teacher_notes)
        SELECT id, boarding_type, day_student_type, textbook, teacher_notes FROM students;
    """)
    db.session.execute(insert_backup_sql)
    db.session.commit()
    
    backup_count = db.session.execute(text("SELECT COUNT(*) FROM students_backup")).scalar()
    print(f"  已备份 {backup_count} 条记录")
    
    print("\n3. 删除相关索引...")
    
    indexes = db.session.execute(text("PRAGMA index_list(students);")).fetchall()
    index_names = [idx[1] for idx in indexes]
    print(f"  当前索引: {index_names}")
    
    for idx_name in index_names:
        if any(col in idx_name.lower() for col in old_columns):
            drop_idx_sql = text(f"DROP INDEX IF EXISTS {idx_name};")
            db.session.execute(drop_idx_sql)
            db.session.commit()
            print(f"  ✓ 已删除索引 {idx_name}")
    
    known_indexes = ['idx_student_boarding', 'idx_student_day_type', 'idx_student_textbook']
    for idx_name in known_indexes:
        try:
            drop_idx_sql = text(f"DROP INDEX IF EXISTS {idx_name};")
            db.session.execute(drop_idx_sql)
            db.session.commit()
            print(f"  ✓ 已删除索引 {idx_name}")
        except Exception:
            print(f"  - 索引 {idx_name} 不存在")
    
    print("\n4. 删除旧字段...")
    
    for col in old_columns:
        if col in column_names:
            drop_sql = text(f"ALTER TABLE students DROP COLUMN {col};")
            db.session.execute(drop_sql)
            db.session.commit()
            print(f"  ✓ 已删除 {col}")
        else:
            print(f"  - {col} 已不存在，跳过")
    
    print("\n5. 验证结果...")
    
    columns = db.session.execute(check_columns_sql).fetchall()
    column_names = [col[1] for col in columns]
    
    print(f"清理后表字段: {column_names}")
    
    print("\n=== 清理完成 ===")
    print("\n注意：")
    print("1. 备份数据保存在 students_backup 表中")
    print("2. 如需恢复，可从 students_backup 表中查询")
    print("3. 确认数据迁移正确后，可手动删除 students_backup 表")
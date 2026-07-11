"""
数据库迁移脚本：为学生表添加毕业学校相关字段
- graduation_school_code: 毕业学校代码（如0440）
- graduation_school: 毕业学校名称
"""
# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from sqlalchemy import text, inspect


def migrate():
    """执行数据库迁移"""
    app = create_app()
    
    with app.app_context():
        # 检查字段是否已存在
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('students')]
        
        has_code = 'graduation_school_code' in columns
        has_name = 'graduation_school' in columns
        
        if has_code and has_name:
            print("[OK] 毕业学校字段已存在，无需迁移")
            return
        
        print("开始添加毕业学校字段...")
        
        # 添加毕业学校代码字段
        if not has_code:
            db.session.execute(
                text("ALTER TABLE students ADD COLUMN graduation_school_code VARCHAR(10)")
            )
            print("[OK] 添加 graduation_school_code 字段成功")
        
        # 添加毕业学校名称字段
        if not has_name:
            db.session.execute(
                text("ALTER TABLE students ADD COLUMN graduation_school VARCHAR(100)")
            )
            print("[OK] 添加 graduation_school 字段成功")
        
        db.session.commit()
        print("\n迁移完成！")


if __name__ == '__main__':
    try:
        migrate()
    except Exception as e:
        print(f"[ERROR] 迁移失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)



# StuLink v1.11.0 2026-09-14
# band_templates 增加 exam_types_json 列（模板适用考试类型），幂等可重复执行
# 说明：db.create_all() 只建新表、不会给已有表加列，故需要本迁移
import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  'data', 'grades.db')


def main():
    if not os.path.exists(DB):
        print('未找到数据库：', DB)
        return
    c = sqlite3.connect(DB)
    cols = [r[1] for r in c.execute('PRAGMA table_info(band_templates)')]
    if not cols:
        print('band_templates 表不存在，无需迁移（create_all 会按模型建表）')
        return
    if 'exam_types_json' in cols:
        print('exam_types_json 已存在，跳过')
    else:
        c.execute('ALTER TABLE band_templates ADD COLUMN exam_types_json TEXT')
        c.commit()
        print('已添加 exam_types_json 列')
    for r in c.execute('SELECT id, name, exam_types_json FROM band_templates ORDER BY id'):
        print(r)
    c.close()


if __name__ == '__main__':
    main()

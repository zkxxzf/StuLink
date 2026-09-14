# StuLink v1.12.1 2026-09-14
# 考务改造迁移：exam_affairs 加 3 个编排增强列 + 确保考场房间库表存在（grades.db）
# 说明：db.create_all() 只建新表、不给已有表加列，故需要本迁移；幂等可重复执行
import sqlite3
import os

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  'data', 'grades.db')

# v1.12.1 新增列：编排分组模式 / 非参考学生处理 / 选科考号前后缀 JSON
NEW_COLS = [
    ('selection_mode', "VARCHAR(10) DEFAULT 'selected'"),   # selected 按选科分组 / plain 不选科
    ('non_attend_mode', "VARCHAR(10) DEFAULT 'skip'"),      # skip 不安排 / tail 同选科尾场
    ('subject_prefixes', 'TEXT'),                            # {"物化生":{"prefix":"1701","suffix":""}}
]


def main():
    if not os.path.exists(DB):
        print('未找到数据库：', DB)
        return
    c = sqlite3.connect(DB)
    cols = [r[1] for r in c.execute('PRAGMA table_info(exam_affairs)')]
    if not cols:
        print('exam_affairs 表不存在，create_all 首次启动会按模型建表')
        return
    for name, ddl in NEW_COLS:
        if name in cols:
            print(f'{name} 已存在，跳过')
        else:
            c.execute(f'ALTER TABLE exam_affairs ADD COLUMN {name} {ddl}')
            print(f'已添加 {name} 列')
    c.commit()
    c.close()
    # 建考场房间库新表（create_all 幂等，只建缺失的表）
    from app import create_app
    from app.extensions import db
    from app.models.grades import AffairRoomLib  # noqa: F401 触发表注册
    app = create_app()
    with app.app_context():
        db.create_all()
    print('affair_room_libs 表已确保存在，v1.12.1 考务迁移完成')


if __name__ == '__main__':
    main()

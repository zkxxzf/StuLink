# StuLink v1.12.0 2026-09-14
# 考务管理：新建 exam_affairs / affair_rooms / affair_students 三张表（grades.db）
# 说明：db.create_all() 会按模型建新表（不会动已有表），幂等可重复执行。
from app import create_app
from app.extensions import db
from app.models.grades import ExamAffair, AffairRoom, AffairStudent  # noqa: F401 触发表注册

app = create_app()

with app.app_context():
    # 建全部绑定中的新表（create_all 只建缺失的表，不会动已有表）
    db.create_all()
    # 校验三张表是否存在（注意：这些表在 grades 绑定库，需查对应引擎）
    from sqlalchemy import inspect
    grades_engine = db.engines.get('grades') if hasattr(db, 'engines') else db.engine
    insp = inspect(grades_engine)
    for t in ('exam_affairs', 'affair_rooms', 'affair_students'):
        print(t, 'OK' if insp.has_table(t) else 'MISSING')
    print('考务表迁移完成')

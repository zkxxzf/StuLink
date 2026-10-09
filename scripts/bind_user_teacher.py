# -*- coding: utf-8 -*-
"""把一个教务教师记录绑定到指定登录账号（初始化/演示用，可回滚）。

用法：
    python scripts/bind_user_teacher.py <username> <teacher_uid|教师姓名> [--move-links]

说明：
    - 绑定后（teachers.user_id = 该账号 id），教师本人登录即可在「教师工作台」
      看到该教师的课表（按 teacher_uid 取，无需迁移）与业绩；
    - --move-links：把该教师**原账号**的任课映射（grades.teacher_subject_links.user_id）
      一并迁到新账号 —— 否则「我的成绩」会是空的。注意这会让原账号失去任教范围，
      考试任课快照（exam_teacher_links）保持不动（那是"当时任课教师"的历史记录）；
    - 改库前自动备份 academic.db / grades.db（项目约定，见 scripts/_db_backup.py）。
"""
import argparse
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _db_backup import backup_db  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

DATA_DIR = os.path.join(BASE, 'data')


def main():
    ap = argparse.ArgumentParser(description='把教师记录绑定到登录账号')
    ap.add_argument('username', help='要绑定的登录账号（users.username）')
    ap.add_argument('teacher', help='教师编号（teacher_uid）或唯一姓名')
    ap.add_argument('--move-links', action='store_true',
                    help='同时把该教师原账号的任课映射迁到新账号')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        from app.models import User
        from app.models.academic import Teacher
        from app.models.grades import TeacherSubjectLink

        user = User.query.filter_by(username=args.username).first()
        if not user:
            print(f'[FAIL] 账号不存在：{args.username}')
            return 1

        t = Teacher.query.filter_by(teacher_uid=args.teacher).first()
        if not t:
            rows = Teacher.query.filter_by(name=args.teacher).all()
            if len(rows) != 1:
                print(f'[FAIL] 教师不存在或姓名不唯一：{args.teacher}（匹配 {len(rows)} 条）')
                return 1
            t = rows[0]

        old_uid = t.user_id
        print(f'账号：{user.username}（{user.real_name}）id={user.id}')
        print(f'教师：{t.teacher_uid} {t.name} 当前 user_id={old_uid}')

        if old_uid == user.id:
            print('[SKIP] 该教师已绑定此账号')
        else:
            backup_db([os.path.join(DATA_DIR, 'academic.db'),
                       os.path.join(DATA_DIR, 'grades.db')])
            t.user_id = user.id
            t.updated_at = datetime.now()
            db.session.commit()
            print(f'[OK] teachers.user_id：{old_uid} -> {user.id}')

        if not args.move_links:
            return 0
        if not old_uid or old_uid == user.id:
            print(f'[INFO] 无可迁移的任课映射（原 user_id={old_uid}）')
            return 0
        n = (TeacherSubjectLink.query.filter_by(user_id=old_uid)
             .update({'user_id': user.id}, synchronize_session=False))
        db.session.commit()
        print(f'[OK] 任课映射迁移 {n} 条：user_id {old_uid} -> {user.id}')
        return 0


if __name__ == '__main__':
    sys.exit(main())

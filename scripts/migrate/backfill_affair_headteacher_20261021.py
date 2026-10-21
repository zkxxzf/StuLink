# StuLink v1.19.49.53 2026-10-21
# 考务环节④ 任课表「班主任」三列（正班 / 副班1 / 副班2）回填（迁移 A44）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""给考务批次补「班主任」三列（迁移 A44）。

背景（2026-10-21 用户口径，第二版）：环节④ 核对任课教师表原先只有「班级 × 科目」。
班主任与任课教师同级别（负责本班总分），且**不是一个人，而是三个位次**：
正班 / 副班1 / 副班2 —— 必须分三列，不能把三个人挤进一格。

本脚本做两件事（**只补不覆盖**，人工改派过的格不动）：
1. **拆分历史合并值**：旧版 A44 写过 `subject='班主任'`、姓名用顿号连接的合并行，
   这里按原顺序拆成 正班→副班1→副班2 三行并删除合并行
   （原顺序来自 UserClassLink 登记先后＝教师安排表的正班/副班1/副班2 列序）；
2. **补齐缺失位次**：没有任何班主任行的班，优先用本批次关联考试的任课快照
   （考试当时），无快照再回落当前 `user_class_links`。

范围：只处理本批次已有班级（`affair_teachers` ∪ `affair_students`）。

用法：
    py -3.12 -u scripts/migrate/backfill_affair_headteacher_20261021.py          # 预览
    py -3.12 -u scripts/migrate/backfill_affair_headteacher_20261021.py --apply  # 写库
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))       # _db_backup / _mig_log

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models import User                                   # noqa: E402
from app.models.grades import (AffairStudent, AffairTeacher,   # noqa: E402
                               ExamAffair, ExamTeacherLink)
from app.modules.grades.services.teacher_snapshot_service import (  # noqa: E402
    HEAD_SUBJECT, HOMEROOM_POSITIONS, current_homeroom_positions)


def _classes_of(affair):
    """本批次已有的班级（任课核对行 ∪ 名单），不凭空扩班"""
    names = {t.class_name for t in
             AffairTeacher.query.filter_by(affair_id=affair.id).all() if t.class_name}
    names |= {s.class_name for s in
              AffairStudent.query.filter_by(affair_id=affair.id).all() if s.class_name}
    return names


def _split_names(text):
    """「张三、李四、王五」→ ['张三', '李四', '王五']（去空、去重、保序）"""
    out = []
    for part in str(text or '').replace('，', '、').replace(',', '、').split('、'):
        nm = part.strip()
        if nm and nm not in out:
            out.append(nm)
    return out


def main():
    ap = argparse.ArgumentParser(description='回填考务批次任课表的班主任三列')
    ap.add_argument('--apply', action='store_true', help='真正写库')
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        affairs = ExamAffair.query.order_by(ExamAffair.id).all()
        print(f'考务批次：{len(affairs)} 个')

        # 姓名 → 账号（同名多账号时留空，不猜）
        name_to_uid = {}
        for u in User.query.filter_by(is_active=True).all():
            if not u.real_name:
                continue
            name_to_uid.setdefault(u.real_name, []).append(u.id)

        def uid_of(name):
            ids = name_to_uid.get(name) or []
            return ids[0] if len(ids) == 1 else None

        n_split = n_fill = n_del = 0
        for a in affairs:
            classes = _classes_of(a)
            if not classes:
                continue
            rows = AffairTeacher.query.filter_by(affair_id=a.id).all()
            have = {(r.class_name, r.subject) for r in rows}
            touched = []

            # 1) 拆分旧的合并列「班主任」
            for r in [x for x in rows if x.subject == HEAD_SUBJECT]:
                names = _split_names(r.teacher_name)
                for idx, nm in enumerate(names[:len(HOMEROOM_POSITIONS)]):
                    pos = HOMEROOM_POSITIONS[idx]
                    if (r.class_name, pos) in have:
                        continue
                    have.add((r.class_name, pos))
                    touched.append((r.class_name, pos, nm, uid_of(nm)))
                    n_split += 1
                    if args.apply:
                        db.session.add(AffairTeacher(
                            affair_id=a.id, grade=a.grade,
                            class_name=r.class_name, subject=pos,
                            user_id=uid_of(nm), teacher_name=nm,
                            source='backfill'))
                if args.apply:
                    db.session.delete(r)
                n_del += 1

            # 2) 完全没有班主任行的班：用考试快照（当时）→ 否则当前名单
            need = [c for c in sorted(classes) if (c, HOMEROOM_POSITIONS[0]) not in have
                    and (c, HEAD_SUBJECT) not in have]
            if need:
                snap = {}
                if a.exam_id:
                    for r in ExamTeacherLink.query.filter_by(
                            exam_id=a.exam_id, subject=HEAD_SUBJECT).all():
                        names = _split_names(r.teacher_name)
                        if names:
                            snap[r.class_name] = names
                cur = {}
                for x in current_homeroom_positions(a.grade, classes=set(need)):
                    cur.setdefault(x['class_name'], []).append(x)
                for cls in need:
                    if cls in snap:
                        pairs = [(HOMEROOM_POSITIONS[i], nm)
                                 for i, nm in enumerate(snap[cls][:3])]
                    elif cls in cur:
                        pairs = [(x['subject'], x['teacher_name']) for x in cur[cls]]
                    else:
                        continue
                    for pos, nm in pairs:
                        if (cls, pos) in have:
                            continue
                        have.add((cls, pos))
                        touched.append((cls, pos, nm, uid_of(nm)))
                        n_fill += 1
                        if args.apply:
                            db.session.add(AffairTeacher(
                                affair_id=a.id, grade=a.grade, class_name=cls,
                                subject=pos, user_id=uid_of(nm), teacher_name=nm,
                                source='backfill'))

            if touched:
                print(f'\n批次 #{a.id} {a.grade} {a.name}')
                for cls, pos, nm, uid in touched:
                    print(f'  + {cls:6s} {pos:4s} {nm}（uid={uid}）')

        print(f'\n拆分合并行 {n_del} 条 / 新增 {n_split + n_fill} 行')
        if not args.apply:
            print('[dry-run] 未写库；确认后加 --apply')
            return
        from _db_backup import backup_db
        backup_db(str(ROOT / 'data' / 'grades.db'),
                  tag='before_affair_headteacher_pos_20261021')
        db.session.commit()
        print('[APPLIED] 已提交')
        try:
            import _mig_log
            _mig_log.record(Path(__file__).name,
                            note=f'考务环节④ 班主任拆三列：拆 {n_del} / 补 '
                                 f'{n_split + n_fill} 行')
        except Exception as exc:                # noqa: BLE001
            print(f'[WARN] 迁移登记失败：{exc}')


if __name__ == '__main__':
    main()

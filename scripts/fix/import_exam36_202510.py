"""一次性：按「考试当时班级」重导入 2025 级高一上第一次月考（exam 36）。

源为标准导入表 data/import_staging/kdocs_2025级_202510第一次月考No1.xlsx
（由金山文档「年级排名后 / 202510第一次月考No1」转换，班级取「学生信息表」）。

本场是高一上不分科统一考试：导入后清空选科/方向快照，
让 rank_dir 退回全年级统一排名（否则会按现在的高二选科分组，口径失真）。

用法：
    py -3.12 scripts/fix/import_exam36_202510.py           # 预览（只解析，不改库）
    py -3.12 scripts/fix/import_exam36_202510.py --apply    # 备份后落库 + 重算排名

2026-10-20 审计加固：本脚本会**整场重置** exam 36（mode='B' 先清后导），并清空
该场全部 direction / subject_selection。原实现无任何门控，误跑即改生产数据。
现按本项目修数据脚本的统一约定：
  ① 默认 dry-run，必须显式 --apply；
  ② 改库前 backup_db 备份 grades.db，备份失败即中止；
  ③ verify 有 FAIL 时退出码非 0（便于执行器/CI 发现口径不符）。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from app import create_app                                       # noqa: E402
from app.extensions import db                                    # noqa: E402
from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT     # noqa: E402
from app.modules.grades.services import (                        # noqa: E402
    import_service, ranking, store_service)
from app.modules.grades.services import (                        # noqa: E402
    teacher_snapshot_service as tss)

APPLY = '--apply' in sys.argv
EXAM_ID = 36
XLSX = ROOT / 'data' / 'import_staging' / (
    'kdocs_2025级_202510第一次月考No1.xlsx')
# (学号, 科目, 金山文档校排, 金山文档班排)
CHECKS = [
    ('20251109', TOTAL_SUBJECT, 1, 1),
    ('20252275', TOTAL_SUBJECT, 2, 1),
    ('20251109', '语文', 18, 4),
    ('20251109', '数学', 54, 8),
    ('20251109', '外语', 137, 27),
    ('20251109', '生物', 2, 1),
    ('20251109', '政治', 2, 1),
    ('20252275', '外语', 2, 1),
    ('20252275', '物理', 296, 26),
]


def reimport(exam) -> None:
    """按模式 B 整场重置导入，并清空方向快照。"""
    with open(XLSX, 'rb') as fh:
        parsed = import_service.parse_score_excel(fh, exam)
    print('解析行数=%d 错误=%d 统计=%s'
          % (len(parsed['rows']), len(parsed.get('errors') or []),
             parsed.get('stats')))
    summary = store_service.apply_import(exam, parsed, mode='B')
    db.session.flush()
    n = ExamScore.query.filter_by(exam_id=exam.id).update(
        {ExamScore.direction: '', ExamScore.subject_selection: ''},
        synchronize_session='auto')
    db.session.expire_all()
    exam.status = 'imported'
    db.session.flush()
    n_rank = ranking.recalc_exam(exam.id)
    n_tch = tss.snapshot_exam(exam, source='import')
    db.session.commit()
    print('导入：%s' % summary)
    print('清空方向 %d 行 | 重算排名 %d 行 | 任课快照 %d 条'
          % (n, n_rank, n_tch))


def verify(exam) -> int:
    """打印班级/排名与金山文档的对照结果；返回 FAIL 条数。"""
    ok = bad = 0
    for no, sub, want_dir, want_cls in CHECKS:
        r = ExamScore.query.filter_by(
            exam_id=exam.id, student_no=no, subject=sub).first()
        good = (r is not None and r.rank_dir == want_dir
                and r.rank_class == want_cls)
        ok, bad = (ok + 1, bad) if good else (ok, bad + 1)
        print('%s %s %-3s 校排 %s/%s 班排 %s/%s 班级=%s'
              % ('PASS' if good else 'FAIL', no, sub,
                 want_dir, r.rank_dir if r else None,
                 want_cls, r.rank_class if r else None,
                 r.class_name if r else None))
    total = ExamScore.query.filter_by(
        exam_id=exam.id, subject=TOTAL_SUBJECT).count()
    rows = ExamScore.query.filter_by(exam_id=exam.id).count()
    print('总分行 %d / 成绩行 %d' % (total, rows))
    print('排名对照 PASS=%d FAIL=%d' % (ok, bad))
    return bad


def main() -> int:
    """入口。返回 0=成功 / 1=失败或预览 / 2=拒绝执行。"""
    sys.stdout.reconfigure(encoding='utf-8')
    app = create_app()
    with app.app_context():
        exam = db.session.get(Exam, EXAM_ID)
        if exam is None:
            print('考试 %s 不存在' % EXAM_ID)
            return 1
        if not XLSX.exists():
            print('找不到源文件：%s' % XLSX)
            return 1
        print('考试：%s / %s / %s' % (exam.id, exam.name, exam.exam_date))
        if not APPLY:
            # 预览：只解析源表，不碰数据库
            with open(XLSX, 'rb') as fh:
                parsed = import_service.parse_score_excel(fh, exam)
            old_rows = ExamScore.query.filter_by(exam_id=EXAM_ID).count()
            print('预览：解析行数=%d 错误=%d 统计=%s'
                  % (len(parsed['rows']), len(parsed.get('errors') or []),
                     parsed.get('stats')))
            print('预览：将整场重置 exam %d（现有 %d 行，含总分行），'
                  '并清空该场 direction / subject_selection' % (EXAM_ID, old_rows))
            print('\n预览模式，未改动。执行：--apply')
            return 0
        from _db_backup import backup_db
        pairs = backup_db(str(ROOT / 'data' / 'grades.db'),
                          tag='before_import_exam36')
        if not pairs or not pairs[0][1]:
            print('中止：grades.db 备份失败，未执行导入。')
            return 2
        print('已备份：%s' % os.path.basename(pairs[0][1]))
        reimport(exam)
        bad = verify(exam)
        return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())

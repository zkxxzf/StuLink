# -*- coding: utf-8 -*-
"""
批量重算考试排名（方向排名/班排名/进退步）

背景：2026-09-12 修复了 ranking.py 的排名 bug（修复前 rank_dir 实际存的是班内排名）。
若库中存在修复前导入的考试，其方向排名需要重算。当前真实库暂无成绩数据，
本脚本供日后导入数据后按需使用。

用法:
    python scripts/recalc_exam_ranks.py            # 实际执行重算
    python scripts/recalc_exam_ranks.py --dry      # 仅列出考试及受影响情况，不修改数据

判定方式：总分行中 rank_dir==rank_class 的比例（全相等且人数>0 视为需重算，
但同方向学生恰好全部同班时属正常现象，重算本身幂等无害）
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import create_app
from app.extensions import db
from app.models.grades import Exam, ExamScore
from app.modules.grades.services import ranking

TOTAL = '总分'


def affected_exams():
    """返回 (全部有成绩的考试, 疑似受历史bug影响的考试id列表)"""
    exams = (Exam.query
             .filter(Exam.id.in_(db.session.query(ExamScore.exam_id).distinct()))
             .order_by(Exam.id).all())
    suspicious = []
    for e in exams:
        n = ExamScore.query.filter_by(exam_id=e.id, subject=TOTAL).count()
        same = (ExamScore.query
                .filter(ExamScore.exam_id == e.id, ExamScore.subject == TOTAL,
                        ExamScore.rank_dir == ExamScore.rank_class,
                        ExamScore.rank_dir.isnot(None))
                .count())
        if n > 0 and same == n:
            suspicious.append(e.id)
    return exams, suspicious


def main():
    dry = '--dry' in sys.argv
    app = create_app()
    with app.app_context():
        exams, suspicious = affected_exams()
        print(f'共 {len(exams)} 场有成绩的考试，疑似需重算 {len(suspicious)} 场')
        if dry:
            for e in exams:
                n = ExamScore.query.filter_by(exam_id=e.id).count()
                mark = ' ← 疑似需重算' if e.id in suspicious else ''
                print(f'  [DRY] exam_id={e.id} {e.grade} {e.name} 成绩行={n}{mark}')
            print('\nDRY 模式未修改数据；去掉 --dry 执行重算')
            return
        # 幂等操作：对全部考试重算（含疑似与正常，确保口径一致）
        for e in exams:
            n = ranking.recalc_exam(e.id)
            db.session.commit()
            print(f'  [OK] exam_id={e.id} {e.grade} {e.name} 重算 {n} 行')
        print('全部重算完成')


if __name__ == '__main__':
    main()

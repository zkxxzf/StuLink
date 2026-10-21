# StuLink v1.19.50.62  2026-10-15
# 把智学网转换好的成绩 Excel 导入 StuLink（新建考试 + 写入成绩 + 重算排名 + 任课快照）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""把 `to_stulink_excel.py` 产出的导入表写进 StuLink。

流程与网页端「成绩导入」完全一致（复用同一套服务）：
    parse_score_excel → store_service.apply_import → ranking.recalc_exam
    → teacher_snapshot_service.snapshot_exam → exam.status='imported'

默认 **dry-run**（只解析、打印识别结果，不写库）；确认无误再加 `--apply`。

用法：
    # 预览
    python scripts/zhixue/import_to_stulink.py --xlsx <导入表.xlsx> \
        --grade 2025级 --name "2025级202602期末（高一上）" --date 2026-02-01 --type 期末考
    # 真正写库
    python scripts/zhixue/import_to_stulink.py ... --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import create_app                                       # noqa: E402
from app.extensions import db                                    # noqa: E402
from app.models.grades import Exam                               # noqa: E402
from app.modules.grades.services import (                        # noqa: E402
    import_service, ranking, store_service)
from app.modules.grades.services import teacher_snapshot_service as tss   # noqa: E402
from app.modules.grades.utils import term_of_date                # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='智学网成绩 → StuLink 导入')
    ap.add_argument('--xlsx', required=True, help='to_stulink_excel.py 产出的导入表')
    ap.add_argument('--grade', default='2025级')
    ap.add_argument('--name', required=True, help='考试名称（同年级重名会阻断）')
    ap.add_argument('--date', required=True, help='考试日期 YYYY-MM-DD')
    ap.add_argument('--kind', default='本校考', help='考试性质（联考/本校考/小测验）')
    ap.add_argument('--type', default='期末考',
                    help='阶段（期末考/期中考/月考/限时练/高三一测/高三二测）')
    ap.add_argument('--mode', default='A', choices=['A', 'B'],
                    help='A=增量覆盖（推荐首次），B=清空本场后导入')
    ap.add_argument('--operator', type=int, default=0,
                    help='操作人用户 id（默认取库里最小 id 的用户）')
    ap.add_argument('--apply', action='store_true', help='真正写库（默认只预览）')
    args = ap.parse_args()

    exam_date = date.fromisoformat(args.date)
    app = create_app()
    with app.app_context():
        operator_id = args.operator
        if not operator_id:
            from app.models import User
            u = User.query.order_by(User.id).first()
            operator_id = u.id if u else None
            print(f'操作人：{getattr(u, "real_name", None) or getattr(u, "username", "?")}'
                  f'（id={operator_id}）')

        exam = Exam.query.filter_by(grade=args.grade, name=args.name).first()
        if exam:
            print(f'已存在同年级同名考试 id={exam.id}（{exam.exam_date}）→ 复用它')
            created = False
        else:
            exam = Exam(grade=args.grade, name=args.name, exam_date=exam_date,
                        exam_kind=args.kind, exam_type=args.type,
                        term=term_of_date(exam_date), operator_id=operator_id)
            created = True
            if args.apply:
                db.session.add(exam)
                db.session.commit()
                print(f'已新建考试 id={exam.id}：{args.grade} / {args.name} / '
                      f'{exam_date} / {args.kind} / {args.type} / {term_of_date(exam_date)}')
            else:
                print(f'[dry-run] 将新建考试：{args.grade} / {args.name} / '
                      f'{exam_date} / {args.kind} / {args.type} / {term_of_date(exam_date)}')

        with open(args.xlsx, 'rb') as fh:
            parsed = import_service.parse_score_excel(fh, exam)
        h = parsed['headers']
        print(f"解析：template={parsed.get('template')} "
              f"科目={h.get('subjects')} 含总分={'total' in h} 含考号={'exam_no' in h}")
        print(f"行数={len(parsed['rows'])} 错误={len(parsed.get('errors') or [])} "
              f"统计={parsed.get('stats')}")
        for e in (parsed.get('errors') or [])[:10]:
            print('   ! ', e)
        if parsed.get('stats', {}).get('error_rows'):
            print('⚠ 存在报错行，先处理再 --apply')

        if not args.apply:
            print('\n[dry-run] 未写库。确认无误后加 --apply 执行。')
            return

        summary = store_service.apply_import(exam, parsed, mode=args.mode)
        n_rank = ranking.recalc_exam(exam.id)
        n_tch = tss.snapshot_exam(exam, source='import')
        exam.status = 'imported'
        exam.import_info = json.dumps({
            'fname': Path(args.xlsx).name, 'mode': args.mode, 'summary': summary,
            'errors': len(parsed.get('errors') or []),
            'teacher_snapshot': n_tch, 'operator': 'scripts/zhixue/import_to_stulink.py',
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }, ensure_ascii=False)
        db.session.commit()
        print(f'\n导入完成：{json.dumps(summary, ensure_ascii=False)}')
        print(f'排名重算 {n_rank} 行 | 任课快照 {n_tch} 条 | 考试状态 → imported')
        if created:
            print(f'考试 id={exam.id}（{exam.title_with_dimensions()}）')


if __name__ == '__main__':
    main()

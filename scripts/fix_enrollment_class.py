# -*- coding: utf-8 -*-
# StuLink v1.18.8.0 2026-10-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学籍驱动的班级归置（幂等，默认 dry-run）

用户口径（2026-09-30）：
  1. 学籍 =「学籍已转出」 → 班级改为「已转出」
  2. 学籍 =「在籍不在校」 → 班级改为「不分班」
  3. 2026级「不分班」学生：学籍为录取批次（一批二志/空）→ 统一改为「在籍不在校」
     （仅限「不分班」学生；2026级在班学生的学籍字段保持录取批次不动）

规则 1/2 覆盖全年级，但**默认跳过已毕业年级**（避免动到已归档数据），可用 --grades 覆盖。
每次班级归置都会写入变迁日志（history.db::student_change_log），保证学生页面可追溯。

用法：
    python -m scripts.fix_enrollment_class                 # dry-run 预览
    python -m scripts.fix_enrollment_class --apply         # 实际写库
    python -m scripts.fix_enrollment_class --grades 2023级 # 显式包含已毕业年级
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                                     # noqa: E402
from app.extensions import db                                  # noqa: E402
from app.models import Student                                 # noqa: E402
from app.utils.helpers import (get_graduated_grades, write_change_log,   # noqa: E402
                               student_location_desc, location_desc)

ST_WITHDRAWN = '学籍已转出'
ST_NOT_INSCHOOL = '在籍不在校'
CLS_WITHDRAWN = '已转出'
CLS_NO_CLASS = '不分班'
BATCH_GRADE = '2026级'


def main():
    ap = argparse.ArgumentParser(description='学籍驱动的班级归置（幂等）')
    ap.add_argument('--apply', action='store_true', help='实际写库；缺省为 dry-run')
    ap.add_argument('--grades', default='',
                    help='限定年级（逗号分隔）；缺省=全部未毕业年级')
    args = ap.parse_args()
    tag = 'APPLIED' if args.apply else 'DRY-RUN（未写库）'

    app = create_app()
    with app.app_context():
        graduated = set(get_graduated_grades() or [])
        if args.grades.strip():
            scope = [g.strip() for g in args.grades.split(',') if g.strip()]
        else:
            scope = sorted({s.grade for s in Student.query.all() if s.grade
                            and s.grade not in graduated})
        print(f'处理年级: {scope}（已毕业 {sorted(graduated)} 默认跳过）')
        print(f'模式: {tag}\n')

        n1 = n2 = n3 = 0
        log_rows = []      # (学生, type, old, new, detail)

        for s in Student.query.order_by(Student.grade, Student.student_number).all():
            if s.grade not in scope:
                continue
            st = (s.enrollment_status or '').strip()
            cls = (s.class_name or '').strip()

            # ---- 规则 1：学籍已转出 → 班级「已转出」----
            if st == ST_WITHDRAWN and cls != CLS_WITHDRAWN:
                old = student_location_desc(s)
                # 显式计算目标描述（不依赖字段赋值，dry-run 也能看到真实结果）
                new = location_desc(s.grade, CLS_WITHDRAWN)
                if args.apply:
                    s.class_name = CLS_WITHDRAWN
                log_rows.append((s, 'withdraw', old, new, '学籍已转出 → 班级归置为「已转出」'))
                n1 += 1
                continue

            # ---- 规则 2：在籍不在校 → 班级「不分班」----
            if st == ST_NOT_INSCHOOL and cls != CLS_NO_CLASS:
                old = student_location_desc(s)
                new = location_desc(s.grade, CLS_NO_CLASS)
                if args.apply:
                    s.class_name = CLS_NO_CLASS
                log_rows.append((s, 'transfer', old, new,
                                 '学籍为「在籍不在校」→ 班级归置为「不分班」'))
                n2 += 1
                continue

            # ---- 规则 3：2026级「不分班」学生的学籍统一为 在籍不在校 ----
            if s.grade == BATCH_GRADE and cls == CLS_NO_CLASS and st != ST_NOT_INSCHOOL:
                if args.apply:
                    s.enrollment_status = ST_NOT_INSCHOOL
                log_rows.append((s, 'enrollment', st or '（空）', ST_NOT_INSCHOOL,
                                 '学籍口径统一（原为录取批次，不分班学生应记在籍不在校）'))
                n3 += 1

        print('=== 待处理明细 ===')
        for s, t, old, new, det in log_rows:
            print(f'  {s.grade}{s.student_number or ""} {s.name} [{t}] {old} → {new}')
        print(f'\n规则1 学籍已转出→已转出 : {n1} 人')
        print(f'规则2 在籍不在校→不分班 : {n2} 人')
        print(f'规则3 2026级不分班学籍   : {n3} 人')
        print(f'合计 {len(log_rows)} 条变更')

        if args.apply:
            db.session.commit()
            # 写变迁日志（班级类 + 学籍类）
            try:
                for s, t, old, new, det in log_rows:
                    write_change_log(t,
                                     [{'id': s.id, 'student_number': s.student_number or '',
                                       'name': s.name}],
                                     old_value=old, new_value=new, detail=det,
                                     operator_name='系统归置')
            except Exception as e:
                print(f'  [警告] 变迁日志写入异常: {e}')
            print('\n已写库并记录变迁日志。')
        else:
            db.session.rollback()
            print('\n确认无误后执行：python -m scripts.fix_enrollment_class --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())

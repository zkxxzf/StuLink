# -*- coding: utf-8 -*-
"""2026-10-20 学生表新增「智学网考号」列，并从成绩库按学号回填。

- `students.zhixue_no` VARCHAR(20) + 普通索引（可空）；
- 回填来源：`grades.db` 里**最近一场有考号的考试**，按 `student_no` 匹配
  `students.student_number`（2026 级 202610 月考：513 人，一人一号，全部匹配）；
- **只填空**：学生已有考号的不覆盖；
- 智学网考号是学生档案上的固定号，与每场考试可能重排的
  `exam_scores.exam_no` 不是一回事。

## 用法

    py -3.12 scripts/migrate/migrate_student_zhixue_no_20261020.py           # 预览
    py -3.12 scripts/migrate/migrate_student_zhixue_no_20261020.py --apply   # 加列 + 回填

## 安全

- 幂等：列 / 索引已存在则跳过；
- `--apply` 前自动备份 system.db；`_mig_log` 登记；
- 拒绝把同一考号写到两个学生上（撞号则跳过并报告）。
"""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sqlite3
import sys

sys.path.insert(0, _ROOT)
sys.path.insert(0, _SCRIPTS)

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv
SYS_DB = os.path.join(_ROOT, 'data', 'system.db')
GRD_DB = os.path.join(_ROOT, 'data', 'grades.db')


def _latest_exam_with_no(con):
    """最近一场「有考号」的考试 id 与名称"""
    row = con.execute(
        "SELECT e.id, e.name, e.exam_date FROM exams e "
        "JOIN exam_scores s ON s.exam_id = e.id "
        "WHERE s.exam_no IS NOT NULL AND s.exam_no <> '' "
        "GROUP BY e.id ORDER BY e.exam_date DESC, e.id DESC LIMIT 1").fetchone()
    return row


def main():
    if not os.path.exists(SYS_DB):
        print(f'[ERROR] 找不到 {SYS_DB}')
        return 1
    con = sqlite3.connect(SYS_DB)
    cols = {r[1] for r in con.execute('PRAGMA table_info(students)')}
    has_col = 'zhixue_no' in cols
    idx = {r[1] for r in con.execute('PRAGMA index_list(students)')}
    has_idx = 'ix_students_zhixue_no' in idx
    filled = 0
    if has_col:
        filled = con.execute(
            "SELECT COUNT(*) FROM students WHERE zhixue_no IS NOT NULL "
            "AND zhixue_no <> ''").fetchone()[0]

    # 回填计划（预览）
    plan, taken, no_student, dup_in_exam = [], set(), 0, 0
    exam = None
    if os.path.exists(GRD_DB):
        g = sqlite3.connect(GRD_DB)
        exam = _latest_exam_with_no(g)
        if exam:
            rows = g.execute(
                "SELECT DISTINCT student_no, exam_no FROM exam_scores "
                "WHERE exam_id=? AND exam_no IS NOT NULL AND exam_no<>''",
                (exam[0],)).fetchall()
            seen = {}
            for sno, no in rows:
                if no in seen and seen[no] != sno:
                    dup_in_exam += 1
                    continue
                seen[no] = sno
            if has_col:
                nums = {r[0]: r[1] for r in con.execute(
                    "SELECT student_number, COALESCE(zhixue_no, '') "
                    "FROM students")}
            else:
                nums = {r[0]: '' for r in con.execute(
                    "SELECT student_number FROM students")}
            for sno, no in rows:
                if sno not in nums:
                    no_student += 1
                    continue
                if nums[sno]:
                    continue          # 已有值，不覆盖
                if no in taken:
                    dup_in_exam += 1
                    continue
                taken.add(no)
                plan.append((sno, no))
        g.close()

    print('== students.zhixue_no（智学网考号）==')
    print('  列存在:', has_col, '| 索引:', has_idx, '| 已填:', filled)
    if exam:
        print(f'  回填来源：exam {exam[0]} {exam[1]}（{exam[2]}）')
        print(f'  可回填：{len(plan)} 行 | 学号不在学生表：{no_student}'
              f' | 撞号跳过：{dup_in_exam}')
        for sno, no in plan[:8]:
            print(f'    {sno} → {no}')
    else:
        print('  未找到带考号的考试（只加列，不回填）')

    if has_col and has_idx and not plan:
        print('  无需变更')
        con.close()
        return 0
    if not APPLY:
        print('\n[DRY-RUN] 未改库；确认后加 --apply')
        con.close()
        return 0

    from _db_backup import backup_db
    backup_db(SYS_DB, tag='before_student_zhixue_no_20261020')
    if not has_col:
        con.execute('ALTER TABLE students ADD COLUMN zhixue_no VARCHAR(20)')
        print('  + students.zhixue_no VARCHAR(20)')
    if not has_idx:
        con.execute('CREATE INDEX IF NOT EXISTS ix_students_zhixue_no '
                    'ON students(zhixue_no)')
        print('  + 索引 ix_students_zhixue_no')
    for sno, no in plan:
        con.execute('UPDATE students SET zhixue_no=? WHERE student_number=?',
                    (no, sno))
    if plan:
        print(f'  回填 {len(plan)} 行')
    con.commit()
    con.close()

    try:
        import _mig_log
        _mig_log.record(SCRIPT_NAME,
                        note=f'students.zhixue_no，回填 {len(plan)} 行'
                             f'（来源 exam {exam[0] if exam else "-"}）')
    except Exception as _e:  # noqa: BLE001
        print(f'[WARN] 迁移登记失败：{_e}')
    print('\n[APPLIED] 完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())

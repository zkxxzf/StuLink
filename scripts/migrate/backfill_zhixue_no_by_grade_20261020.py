# -*- coding: utf-8 -*-
"""按年级回填 students.zhixue_no（智学网固定考号）。

背景：`migrate_student_zhixue_no_20261020.py` 只取全局最近一场带考号的
考试，结果只填了 2026 级；2024/2025 级仍为空，导致智学网成绩无法按
考号回挂（只能靠姓名猜，违反「不做姓名匹配」的口径）。

本脚本对**每个年级**分别取该届最近一场带考号的考试，按
`exam_scores.student_no → students.student_number` 回填；另外可选从
`data/zhixue/学生对应.txt`（学号<TAB>自定义考号<TAB>姓名）补。

## 用法

    py -3.12 scripts/migrate/backfill_zhixue_no_by_grade_20261020.py
    py -3.12 scripts/migrate/backfill_zhixue_no_by_grade_20261020.py --apply

## 安全

- **只填空不覆盖**；同一考号撞两个学生则跳过并报告；
- `--apply` 前自动备份 system.db，并写 `_mig_log`；
- 幂等：再跑一次没有新增即不改库。
"""
import os
import sqlite3
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRIPT_NAME = os.path.basename(__file__)
APPLY = '--apply' in sys.argv
SYS_DB = os.path.join(_ROOT, 'data', 'system.db')
GRD_DB = os.path.join(_ROOT, 'data', 'grades.db')
MAP_FILE = os.path.join(_ROOT, 'data', 'zhixue', '学生对应.txt')
GRADES = ('2024级', '2025级', '2026级')


def latest_exam_with_no(con, grade):
    """该年级最近一场带考号的考试"""
    return con.execute(
        "SELECT e.id, e.name, e.exam_date FROM exams e "
        "JOIN exam_scores s ON s.exam_id = e.id "
        "WHERE e.grade=? AND s.exam_no IS NOT NULL AND s.exam_no <> '' "
        "GROUP BY e.id ORDER BY e.exam_date DESC, e.id DESC LIMIT 1",
        (grade,)).fetchone()


def exam_pairs(con, exam_id):
    """考试里的 (student_no, exam_no)，同考号多学号视为异常并丢弃"""
    rows = con.execute(
        "SELECT DISTINCT student_no, exam_no FROM exam_scores "
        "WHERE exam_id=? AND exam_no IS NOT NULL AND exam_no<>''",
        (exam_id,)).fetchall()
    seen, out = {}, []
    for sno, no in rows:
        if no in seen and seen[no] != sno:
            continue
        seen[no] = sno
        out.append((sno, no))
    return out


def map_pairs(path):
    """对应表 → [(学号, 考号)]"""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding='utf-8-sig') as fh:
        for line in fh:
            parts = [p.strip() for p in line.rstrip('\n').split('\t')]
            if len(parts) >= 2 and parts[0] and parts[1]:
                out.append((parts[0], parts[1]))
    return out


def main():
    if not os.path.exists(SYS_DB):
        print(f'[ERROR] 找不到 {SYS_DB}')
        return 1
    con = sqlite3.connect(SYS_DB)
    cols = {r[1] for r in con.execute('PRAGMA table_info(students)')}
    if 'zhixue_no' not in cols:
        print('[ERROR] 缺 students.zhixue_no 列，先跑 migrate_student_zhixue_no_20261020')
        con.close()
        return 1

    have = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT student_number, COALESCE(zhixue_no, ''), COALESCE(grade, '') "
        "FROM students")}
    taken = {no: sno for sno, (no, _g) in have.items() if no}
    plan, report = [], []
    g = sqlite3.connect(GRD_DB) if os.path.exists(GRD_DB) else None
    for grade in GRADES:
        exam = latest_exam_with_no(g, grade) if g else None
        pairs = exam_pairs(g, exam[0]) if exam else []
        src = f'exam {exam[0]}（{exam[2]}）' if exam else '无考试'
        added = skipped = missing = 0
        for sno, no in pairs:
            if sno not in have:
                missing += 1
                continue
            cur, stu_grade = have[sno]
            if cur:
                skipped += 1
                continue
            if no in taken:
                continue
            taken[no] = sno
            plan.append((sno, no))
            have[sno] = (no, stu_grade)
            added += 1
        report.append((grade, src, len(pairs), added, skipped, missing))
    if g:
        g.close()

    # 补充来源：人工维护的对应表（只补仍为空的学生）
    map_added = 0
    for sno, no in map_pairs(MAP_FILE):
        if sno not in have or have[sno][0] or no in taken:
            continue
        taken[no] = sno
        plan.append((sno, no))
        have[sno] = (no, have[sno][1])
        map_added += 1

    print('== 按年级回填 students.zhixue_no ==')
    for grade, src, total, added, skipped, missing in report:
        print(f'  {grade}：来源 {src} | 考号 {total} 条 | 新增 {added}'
              f' | 已有跳过 {skipped} | 学号不在册 {missing}')
    print(f'  对应表补录：{map_added} 条')
    print(f'  合计待写入：{len(plan)} 条')
    if not plan:
        print('  无需变更')
        con.close()
        return 0
    if not APPLY:
        print('\n[DRY-RUN] 未改库；确认后加 --apply')
        con.close()
        return 0

    from _db_backup import backup_db
    backup_db(SYS_DB, tag='before_backfill_zhixue_no_by_grade_20261020')
    for sno, no in plan:
        con.execute('UPDATE students SET zhixue_no=? WHERE student_number=? '
                    'AND (zhixue_no IS NULL OR zhixue_no = ?)', (no, sno, ''))
    con.commit()
    con.close()
    try:
        import _mig_log
        _mig_log.record(SCRIPT_NAME, note=f'按年级回填 zhixue_no {len(plan)} 行')
    except Exception as exc:                       # noqa: BLE001
        print(f'[WARN] 迁移登记失败：{exc}')
    print(f'\n[APPLIED] 回填 {len(plan)} 行')
    return 0


if __name__ == '__main__':
    sys.exit(main())

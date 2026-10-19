# -*- coding: utf-8 -*-
"""迁移 grades.db：新建 exam_teacher_links 表（考试任课快照）

背景：TeacherSubjectLink 只存"当前"映射，教师一换历史考试的教师维度分析全部错位。
本表把每场考试的任课教师映射（班级 × 科目 × 教师，当时状态）定格，分析读快照。

幂等：表已存在则跳过。填充由「成绩导入」自动完成（source=import）；
历史考试可由 Excel 任课表回填（source=excel）。
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

BASE = _ROOT
DB = os.path.join(BASE, 'data', 'grades.db')

# 2026-10-14 第四轮审计（CONS-02）：补迁移登记（只登记不早退）
SCRIPT_NAME = os.path.basename(__file__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')


def _record():
    if _mig_log:
        _mig_log.record(SCRIPT_NAME, note='grades.db 新建考试任课快照表 exam_teacher_links')


DDL = """
CREATE TABLE IF NOT EXISTS exam_teacher_links (
    id INTEGER NOT NULL,
    exam_id INTEGER NOT NULL,
    grade VARCHAR(10) NOT NULL,
    class_name VARCHAR(10) NOT NULL,
    subject VARCHAR(10) NOT NULL,
    user_id INTEGER,
    teacher_name VARCHAR(50),
    source VARCHAR(10),
    snapshot_at DATETIME,
    PRIMARY KEY (id),
    CONSTRAINT uq_exam_cls_subj UNIQUE (exam_id, class_name, subject)
)
"""

IDX = 'CREATE INDEX IF NOT EXISTS idx_exam_teacher_exam ON exam_teacher_links (exam_id)'


def main():
    if not os.path.exists(DB):
        print(f'未找到 {DB}，跳过（首次启动会自动建表）')
        _record()
        return 0
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    exists = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='exam_teacher_links'"
    ).fetchone()
    if exists:
        n = cur.execute('SELECT count(1) FROM exam_teacher_links').fetchone()[0]
        print(f'exam_teacher_links 已存在，跳过（当前 {n} 行）')
        conn.close()
        _record()
        return 0
    try:
        cur.execute(DDL)
        cur.execute(IDX)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f'迁移失败，已回滚：{e}')
        conn.close()
        return 1
    ok = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='exam_teacher_links'"
    ).fetchone()
    conn.close()
    if not ok:
        print('迁移异常：表未创建成功')
        return 1
    print('迁移完成：已创建 exam_teacher_links（考试任课快照表）')
    print('提示：新导入的成绩会自动写入快照；历史考试可由 Excel 任课表回填。')
    _record()
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""迁移：新建「成绩复核申请」表 score_review_requests（2026-10-15）。

用户口径：「然后申请调分可以。因为他有一个成绩复核的，这个的话就需要学生的信息、
学科、题号，然后增减分数。」

在 grades.db 新建表 `score_review_requests`（模型见 `app/models/grades.py`
的 `ScoreReviewRequest`）：教师 / 班主任提交复核申请，有 `grades.score_edit`
（改分权限）的人审批，批准时可选直接应用到成绩单（走与手动改分同一链路并留痕）。

- **幂等**：表已存在则跳过（可安全重复执行）；
- 建表前自动备份 grades.db（`scripts/_db_backup.backup_db`）；
- 支持 `STULINK_DATA_DIR` 指向非默认数据目录（沙箱验证用），默认 `data/`。

用法：`python scripts/migrate/migrate_score_review_20261015.py`
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

BASE_DIR = _ROOT
sys.path.insert(0, BASE_DIR)

SCRIPT_NAME = os.path.basename(__file__)

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS score_review_requests (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        exam_id INTEGER NOT NULL,
        exam_name VARCHAR(100),
        grade VARCHAR(10),
        class_name VARCHAR(10),
        student_no VARCHAR(20) NOT NULL,
        student_name VARCHAR(50),
        subject VARCHAR(10) NOT NULL,
        question_no VARCHAR(40),
        delta FLOAT NOT NULL,
        reason VARCHAR(500),
        status VARCHAR(12) NOT NULL,
        applicant_id INTEGER,
        applicant_name VARCHAR(50),
        created_at DATETIME,
        reviewer_id INTEGER,
        reviewer_name VARCHAR(50),
        reviewed_at DATETIME,
        review_note VARCHAR(200),
        applied BOOLEAN,
        applied_at DATETIME
    )
    """,
    'CREATE INDEX IF NOT EXISTS idx_score_review_exam_status '
    'ON score_review_requests (exam_id, status)',
    'CREATE INDEX IF NOT EXISTS idx_score_review_student '
    'ON score_review_requests (exam_id, student_no)',
    'CREATE INDEX IF NOT EXISTS idx_score_review_applicant '
    'ON score_review_requests (applicant_id, status)',
]


def _record(note):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _mig_log
        _mig_log.record(SCRIPT_NAME, note=note)
    except Exception as e:  # noqa: BLE001  登记失败绝不阻塞迁移
        print(f'[WARN] 迁移登记不可用（仅不登记）：{e}')


def main():
    data_dir = os.environ.get('STULINK_DATA_DIR') or os.path.join(BASE_DIR, 'data')
    db_path = os.path.join(data_dir, 'grades.db')
    if not os.path.exists(db_path):
        print(f'[ERR ] 找不到成绩库：{db_path}')
        return 1

    conn = sqlite3.connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='score_review_requests'").fetchone()
        if exists:
            print('[SKIP] 表 score_review_requests 已存在（无需建表）')
            _record('表已存在（空跑）')
            return 0

        try:
            from scripts._db_backup import backup_db
            backup_db(db_path)
        except Exception as e:  # noqa: BLE001
            print(f'[WARN] 备份失败（继续建表）：{e}')

        for ddl in _DDL:
            conn.execute(ddl)
        conn.commit()
        print('[DONE] 已创建表 score_review_requests（含 3 个索引）')
    finally:
        conn.close()

    _record('新建成绩复核申请表 score_review_requests')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

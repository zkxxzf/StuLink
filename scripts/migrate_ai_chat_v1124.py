"""AI 多轮对话与图表数据迁移（v1.12.4）

目标：
1. ai_reports 增加 charts 列（图表数据 JSON，本地统计，随报告固化）
2. 新建 ai_chat_messages 表（多轮对话上下文）

说明：
- 幂等：已存在则跳过
- 执行前自动备份 grades.db（同目录 .bak-<时间戳>）
- 新部署环境由 db.create_all() 自动建表，本脚本仅用于升级已有库

用法：
    python scripts/migrate_ai_chat_v1124.py
"""
import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE_DIR, 'data', 'grades.db')


def backup(path):
    if not os.path.exists(path):
        return None
    dst = f"{path}.bak-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(path, dst)
    return dst


def main():
    if not os.path.exists(DB):
        print('grades.db 不存在，跳过')
        return
    print('备份 ->', os.path.basename(backup(DB)))

    conn = sqlite3.connect(DB)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(ai_reports)')]
    if 'charts' in cols:
        print('ai_reports.charts 已存在，跳过')
    else:
        conn.execute('ALTER TABLE ai_reports ADD COLUMN charts TEXT')
        print('ai_reports.charts 已添加')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS ai_chat_messages (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            exam_id INTEGER NOT NULL,
            role VARCHAR(10) NOT NULL,
            content TEXT,
            provider VARCHAR(20),
            model VARCHAR(50),
            created_at DATETIME
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ai_chat_user_exam '
                 'ON ai_chat_messages (user_id, exam_id)')
    print('ai_chat_messages 表已就绪')
    conn.commit()
    conn.close()
    print('完成。请重启应用。')


if __name__ == '__main__':
    main()

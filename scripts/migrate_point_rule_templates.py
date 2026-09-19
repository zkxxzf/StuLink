#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本：创建 point_rule_templates 表并插入默认规则模板"""
import sqlite3
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
points_db = os.path.join(BASE, 'data', 'points.db')

DEFAULT_RULES = [
    # (name, category, default_points, description)
    ('课堂表现优秀', '学习', 5, '课堂积极回答问题、表现突出'),
    ('作业完成优秀', '学习', 3, '作业质量高、按时完成'),
    ('月考进步显著', '学习', 10, '月考成绩进步明显'),
    ('考试作弊', '纪律', -10, '考试作弊行为'),
    ('上课违纪', '纪律', -5, '上课讲话、玩手机等违纪行为'),
    ('迟到早退', '纪律', -2, '上课或集会迟到早退'),
    ('宿舍卫生优秀', '卫生', 3, '宿舍卫生检查优秀'),
    ('宿舍卫生不合格', '卫生', -3, '宿舍卫生检查不合格'),
    ('参加活动获奖', '活动', 5, '参加学校活动获奖'),
    ('志愿服务', '活动', 3, '参加志愿服务活动'),
    ('好人好事', '其他', 2, '拾金不昧、助人为乐等'),
    ('损坏公物', '其他', -5, '故意损坏学校公物'),
]


def main():
    print('=== 迁移脚本：创建积分规则模板表 ===')
    print(f'积分库: {points_db}')
    print()

    if not os.path.exists(points_db):
        print('错误: 积分数据库不存在')
        sys.exit(1)

    conn = sqlite3.connect(points_db)
    try:
        # 检查表是否存在
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='point_rule_templates'")
        if cursor.fetchone():
            print('point_rule_templates 表已存在')
            # 检查是否有数据
            cursor = conn.execute('SELECT COUNT(*) FROM point_rule_templates')
            count = cursor.fetchone()[0]
            if count > 0:
                print(f'表中已有 {count} 条规则模板，跳过插入')
                return
        else:
            print('创建 point_rule_templates 表...')
            conn.execute('''
                CREATE TABLE point_rule_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(50) NOT NULL,
                    category VARCHAR(20) NOT NULL,
                    default_points INTEGER NOT NULL,
                    description VARCHAR(200),
                    is_active BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            print('表创建成功')

        # 插入默认规则
        print('插入默认规则模板...')
        for name, category, points, desc in DEFAULT_RULES:
            conn.execute(
                'INSERT INTO point_rule_templates (name, category, default_points, description) VALUES (?, ?, ?, ?)',
                (name, category, points, desc)
            )
        conn.commit()
        print(f'已插入 {len(DEFAULT_RULES)} 条默认规则模板')
        print()
        print('=== 迁移完成 ===')

    except Exception as e:
        print(f'迁移失败: {e}')
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()

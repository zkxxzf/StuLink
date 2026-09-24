# StuLink v1.12.2 2026-09-14
# 成绩证明加密防伪码迁移：certificates 加 nonce/sig 两列 + 确保签名密钥表存在（grades.db）
# 说明：db.create_all() 只建新表、不给已有表加列，故需本迁移；幂等可重复执行。
#   旧记录 nonce/sig 为空 → 核验页按「早期版本码」处理（向后兼容，不报错）。
import sqlite3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  'data', 'grades.db')

# v1.12.2 新增列：随机码 + HMAC 签名段
NEW_COLS = [
    ('nonce', 'VARCHAR(16)'),   # 防伪码随机段（8 位）
    ('sig', 'VARCHAR(40)'),     # HMAC-SHA256 签名段（16 位 hex，预留余量）
]


def main():
    if not os.path.exists(DB):
        print('未找到数据库：', DB)
        return
    c = sqlite3.connect(DB)
    cols = [r[1] for r in c.execute('PRAGMA table_info(certificates)')]
    if not cols:
        print('certificates 表不存在，create_all 首次启动会按模型建表')
    else:
        from _ddl_guard import assert_ident   # R-11
        for name, ddl in NEW_COLS:
            if name in cols:
                print(f'{name} 已存在，跳过')
            else:
                assert_ident(name, '列名')
                c.execute(f'ALTER TABLE certificates ADD COLUMN {name} {ddl}')
                print(f'已添加 {name} 列')
    c.commit()
    c.close()
    # 建签名密钥新表（create_all 幂等，只建缺失的表）
    from app import create_app
    from app.extensions import db
    from app.models.grades import CertSigningKey  # noqa: F401 触发表注册
    app = create_app()
    with app.app_context():
        db.create_all()
    print('cert_signing_keys 表已确保存在，v1.12.2 加密防伪码迁移完成')


if __name__ == '__main__':
    main()

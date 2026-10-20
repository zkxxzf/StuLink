# -*- coding: utf-8 -*-
"""2026-10-19：把 students.phone1 / phone2 从明文改为随机 IV 密文 + 填盲索引。

方案（用户 2026-10-19 定）：方案 C —— 随机 IV 加密 + HMAC 盲索引
- 密文：encrypt_rand(value)，同样号码每次密文都不同，抗枚举对照
- 索引：phone1_idx / phone2_idx = HMAC-SHA256(规范化号码)，只支持精确等值查询
- 透明：模型用 property 加解密，页面/导出/导入无感

幂等：已经是密文的行（不是 11 位纯数字）跳过。
用法：
    py -3.12 scripts/migrate/migrate_encrypt_student_phone_20261019.py          # 预览
    py -3.12 scripts/migrate/migrate_encrypt_student_phone_20261019.py --apply  # 执行
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

APPLY = '--apply' in sys.argv
DB = 'data/system.db'
SCRIPT_NAME = os.path.basename(__file__)

# 2026-10-19 审计：迁移必须登记（本脚本原先没有 _mig_log，生产漏跑不可查）
for _p in (_ROOT, os.path.join(_ROOT, 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')


def ensure_columns():
    """加 phone1_idx / phone2_idx 列（SQLAlchemy create_all 不会给已有表加列）"""
    con = sqlite3.connect(DB, timeout=15)
    cur = con.cursor()
    have = {r[1] for r in cur.execute('PRAGMA table_info(students)')}
    for col in ('phone1_idx', 'phone2_idx'):
        if col not in have:
            cur.execute(f'ALTER TABLE students ADD COLUMN {col} TEXT')
            print(f'  + 列 {col}')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_student_phone1 '
                'ON students(phone1_idx)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_student_phone2 '
                'ON students(phone2_idx)')
    con.commit()
    con.close()


def main():
    from app import create_app
    from app.extensions import db
    from app.models.student import Student
    from app.utils.crypto import encrypt_rand, blind_index

    if not APPLY:
        # 预览：直连库按**库里原始存储值**统计，不依赖新增的 idx 列 → 预览零结构改动
        con = sqlite3.connect(DB, timeout=15)
        try:
            rows = con.execute('SELECT phone1, phone2 FROM students').fetchall()
        finally:
            con.close()
        cnt = sum(1 for a_, b_ in rows if _is_plain(a_) or _is_plain(b_))
        print(f'  共 {len(rows)} 名学生，待加密 {cnt} 行')
        print('\n  预览模式，未改动（表结构亦未动）。加 --apply 执行。')
        return

    if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
        return

    _backup()
    a = create_app()
    with a.app_context():
        # 只有真正执行时才补列：ORM 查询会带上 phone1_idx/phone2_idx，缺列会直接报错
        ensure_columns()
        rows = Student.query.all()
        todo = []
        for s in rows:
            # 2026-10-19 修复：幂等判据用**库里原始存储值**（_phone1_encrypted），
            # 而不是 property 解密后的明文 —— 后者对已加密行同样返回 11 位明文，
            # 会让每次重跑都判定「待加密」并全表重加密（与注释「已是密文跳过」不符）。
            raw1, raw2 = s._phone1_encrypted, s._phone2_encrypted
            n1, n2 = _is_plain(raw1), _is_plain(raw2)
            if n1 or n2:
                todo.append((s, raw1, raw2, n1, n2))
        print(f'  共 {len(rows)} 名学生，需要加密 {len(todo)} 行')

        n = 0
        for s, p1, p2, n1, n2 in todo:
            # 直接写内部列属性，避免 property 读到已加密值时被二次处理
            if n1:
                s._phone1_encrypted = encrypt_rand(p1)
                s.phone1_idx = blind_index(p1)
            if n2:
                s._phone2_encrypted = encrypt_rand(p2)
                s.phone2_idx = blind_index(p2)
            n += 1
            if n % 500 == 0:
                db.session.commit()
                print(f'    已处理 {n} 行')
        db.session.commit()
        print(f'\n  完成：加密 {n} 行')
        if _mig_log is not None:
            try:
                _mig_log.record(
                    SCRIPT_NAME,
                    note='students.phone1/phone2 明文→随机IV密文 + 盲索引回填')
            except Exception as _e:  # noqa: BLE001
                print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')

        # 抽检（只打印盲索引与密文长度，不回显明文号码，避免终端/日志泄露）
        s = Student.query.filter(Student.phone1_idx.isnot(None)).first()
        if s is not None:
            print(f'  抽检 #{s.id} {s.name}：盲索引={s.phone1_idx} '
                  f'密文长度={len(s._phone1_encrypted or "")}')


def _backup():
    """执行前备份目标库（.bak-YYYYMMDD_HHMMSS），满足「改库前先备份」约定。"""
    import shutil
    from datetime import datetime
    if not os.path.exists(DB):
        return None
    dst = '%s.bak-%s' % (DB, datetime.now().strftime('%Y%m%d_%H%M%S'))
    shutil.copy2(DB, dst)
    print(f'  已备份：{os.path.basename(dst)}')
    return dst


def _is_plain(v):
    """是否还是明文（11 位纯数字 = 未加密的手机号）"""
    v = (v or '').strip()
    return len(v) == 11 and v.isdigit()


if __name__ == '__main__':
    main()

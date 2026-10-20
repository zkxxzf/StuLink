# -*- coding: utf-8 -*-
"""2026-10-19：把 teachers.phone 从明文改为随机 IV 密文 + 填盲索引（phone_idx）。

与学生同一方案 C。注意：登录名 users.username 保持可读（它是账号标识），
本脚本只加密 teachers 表里的**展示用**号码。

用法：
    py -3.12 scripts/migrate/migrate_encrypt_teacher_phone_20261019.py
    py -3.12 scripts/migrate/migrate_encrypt_teacher_phone_20261019.py --apply
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

APPLY = '--apply' in sys.argv
DB = 'data/academic.db'
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
    con = sqlite3.connect(DB, timeout=15)
    cur = con.cursor()
    have = {r[1] for r in cur.execute('PRAGMA table_info(teachers)')}
    if 'phone_idx' not in have:
        cur.execute('ALTER TABLE teachers ADD COLUMN phone_idx TEXT')
        print('  + 列 phone_idx')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_teacher_phone '
                'ON teachers(phone_idx)')
    con.commit()
    con.close()


def _is_plain(v):
    v = (v or '').strip()
    return len(v) == 11 and v.isdigit()


def main():
    from app import create_app
    from app.extensions import db
    from app.models.academic import Teacher
    from app.utils.crypto import blind_index, encrypt_rand

    if not APPLY:
        # 预览：直连库按原始存储值统计（不依赖新增的 phone_idx 列）→ 预览零结构改动
        con = sqlite3.connect(DB, timeout=15)
        try:
            rows = con.execute('SELECT phone FROM teachers').fetchall()
        finally:
            con.close()
        cnt = sum(1 for (p_,) in rows if _is_plain(p_))
        print(f'  共 {len(rows)} 名教师，待加密 {cnt} 行')
        print('\n  预览模式，未改动（表结构亦未动）。加 --apply 执行。')
        return

    if _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
        return

    _backup()
    a = create_app()
    with a.app_context():
        # 只有真正执行时才补列（ORM 查询会带 phone_idx，缺列会直接报错）
        ensure_columns()
        rows = Teacher.query.all()
        todo = []
        for t in rows:
            # 2026-10-19 修复：幂等判据用**库里原始存储值**（_phone_encrypted），
            # 而不是 property 解密后的明文 —— 否则已加密行会被反复判为明文并重加密。
            raw = t._phone_encrypted
            if _is_plain(raw):
                todo.append((t, raw))
        print(f'  共 {len(rows)} 名教师，需要加密 {len(todo)} 行')

        for t, p in todo:
            t._phone_encrypted = encrypt_rand(p)
            t.phone_idx = blind_index(p)
        db.session.commit()
        print(f'\n  完成：加密 {len(todo)} 行')
        if _mig_log is not None:
            try:
                _mig_log.record(
                    SCRIPT_NAME,
                    note='teachers.phone 明文→随机IV密文 + 盲索引回填')
            except Exception as _e:  # noqa: BLE001
                print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')

        # 抽检（只打印盲索引与密文长度，不回显明文号码，避免终端/日志泄露）
        t = Teacher.query.filter(Teacher.phone_idx.isnot(None)).first()
        if t is not None:
            print(f'  抽检 #{t.id} {t.name}：盲索引={t.phone_idx} '
                  f'密文长度={len(t._phone_encrypted or "")}')


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


if __name__ == '__main__':
    main()

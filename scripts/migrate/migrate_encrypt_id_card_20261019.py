# -*- coding: utf-8 -*-
"""2026-10-19 H-06：身份证从确定性 IV 改为随机 IV + 盲索引。

背景：原 crypto.encrypt() 用 IV=SHA256(明文)[:16]，且 IV 随密文入库 ——
等于把可枚举的 18 位身份证做成了"可离线撞库的指纹"。本迁移：
1) students / teachers 各加 id_card_idx（HMAC-SHA256 盲索引，不可逆）；
2) 回填 idx；
3) 把存量确定性密文（或历史明文）重加密为随机 IV；
4) 旧确定性密文仍可 decrypt，但新代码不再写它。

幂等：已经是随机 IV 且 idx 已正确的行跳过。
用法：
    py -3.12 scripts/migrate/migrate_encrypt_id_card_20261019.py            # 预览
    py -3.12 scripts/migrate/migrate_encrypt_id_card_20261019.py --apply --confirm

⚠ 回滚：必须恢复迁移前 .bak-* 备份。只回滚代码而不恢复备份时，旧代码
按确定性密文查询将查不到新随机 IV 行（解密仍可用）。
"""
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
for _p in (_ROOT, os.path.join(_ROOT, 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.chdir(_ROOT)

APPLY = '--apply' in sys.argv
CONFIRM = '--confirm' in sys.argv
SCRIPT_NAME = os.path.basename(__file__)
LIMIT = 0
for _i, _a in enumerate(sys.argv):
    if _a == '--limit' and _i + 1 < len(sys.argv):
        try:
            LIMIT = int(sys.argv[_i + 1])
        except ValueError:
            LIMIT = 0

SYSTEM_DB = os.path.join(_ROOT, 'data', 'system.db')
ACADEMIC_DB = os.path.join(_ROOT, 'data', 'academic.db')

try:
    import _mig_log
except Exception as _e:  # noqa: BLE001
    _mig_log = None
    print(f'[WARN] 迁移登记模块不可用（迁移照常执行，仅不登记）：{_e}')

try:
    from _db_backup import backup_db
except Exception as _e:  # noqa: BLE001
    backup_db = None
    print(f'[WARN] 备份模块不可用（--apply 将拒绝执行）：{_e}')


def ensure_column(db_path, table, column, ddl='TEXT'):
    """加列 + 建索引（SQLite 的 ALTER 不支持 IF NOT EXISTS，先查）。"""
    if not os.path.exists(db_path):
        print(f'  [SKIP] 库不存在：{db_path}')
        return
    con = sqlite3.connect(db_path, timeout=15)
    try:
        have = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
        if column not in have:
            con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
            print(f'  + {os.path.basename(db_path)}.{table}.{column}')
        con.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_{column} '
                    f'ON {table}({column})')
        con.commit()
    finally:
        con.close()


def scan(db_path, table, enc_col):
    """返回待迁移行 [(id, plain, stored_idx, reason), ...]。"""
    from app.utils.crypto import decrypt, encrypt, id_card_index

    con = sqlite3.connect(db_path, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f'SELECT id, {enc_col} AS enc, id_card_idx AS idx FROM {table}'
        ).fetchall()
    finally:
        con.close()

    todo, skipped, broken = [], 0, 0
    for row in rows:
        enc = row['enc']
        if not enc:
            skipped += 1
            continue
        try:
            plain = decrypt(enc)
        except Exception:  # noqa: BLE001
            broken += 1
            continue
        if not plain:
            skipped += 1
            continue
        idx = id_card_index(plain)
        legacy = encrypt(plain)
        is_plaintext = (str(enc) == str(plain))
        is_legacy = (str(enc) == str(legacy))
        idx_ok = (row['idx'] or '') == (idx or '')
        if is_plaintext or is_legacy or not idx_ok:
            reason = ('plaintext' if is_plaintext
                      else 'legacy-iv' if is_legacy else 'idx-missing')
            todo.append((row['id'], plain, idx, reason))
        else:
            skipped += 1
    return todo, skipped, broken


def migrate(db_path, table, enc_col, label):
    from app.utils.crypto import encrypt_rand, id_card_index

    todo, skipped, broken = scan(db_path, table, enc_col)
    print(f'  {label}：扫描完成，待迁移 {len(todo)} 行，已合规 {skipped} 行，'
          f'解密失败 {broken} 行')
    for cid, plain, idx, reason in todo[:3]:
        # 不打印明文身份证；只显示盲索引前 12 位
        print(f'    示例 #{cid} reason={reason} idx={(idx or "")[:12]}...')
    if not APPLY:
        return 0, len(todo), broken, len(todo)
    if LIMIT:
        todo = todo[:LIMIT]

    con = sqlite3.connect(db_path, timeout=15)
    n = 0
    try:
        for cid, plain, idx, _reason in todo:
            con.execute(
                f'UPDATE {table} SET {enc_col}=?, id_card_idx=? WHERE id=?',
                (encrypt_rand(plain), id_card_index(plain), cid))
            n += 1
            if n % 500 == 0:
                con.commit()
                print(f'    已处理 {n} 行')
        con.commit()
    finally:
        con.close()
    print(f'  {label}：已迁移 {n} 行')
    return n, len(todo) - n, broken, len(todo)


def main():
    from app import create_app  # noqa: F401  仅用于导入 crypto 的密钥上下文

    if APPLY and not CONFIRM:
        print('拒绝执行：--apply 必须同时带 --confirm（先跑预览确认行数）。')
        return 2
    if APPLY and _mig_log is not None and _mig_log.is_applied(SCRIPT_NAME):
        print(f'[SKIP] {SCRIPT_NAME} 已登记为已应用，跳过迁移')
        return 0
    if APPLY and backup_db is None:
        print('拒绝执行：备份模块不可用。')
        return 2

    print('== 身份证加密迁移（确定性 IV → 随机 IV + 盲索引）==')
    ensure_column(SYSTEM_DB, 'students', 'id_card_idx')
    ensure_column(ACADEMIC_DB, 'teachers', 'id_card_idx')

    if APPLY:
        print('  先备份 system.db / academic.db ...')
        backup_db([SYSTEM_DB, ACADEMIC_DB])

    total = 0
    total_remain = 0
    total_broken = 0
    total_planned = 0
    if os.path.exists(SYSTEM_DB):
        a, b, c, d = migrate(SYSTEM_DB, 'students', 'id_card_number', 'students')
        total += a
        total_remain += b
        total_broken += c
        total_planned += d
    if os.path.exists(ACADEMIC_DB):
        a, b, c, d = migrate(ACADEMIC_DB, 'teachers', 'id_card_enc', 'teachers')
        total += a
        total_remain += b
        total_broken += c
        total_planned += d

    if total_broken:
        print(f'[WARN] 有 {total_broken} 行解密失败，未改动，请人工核对。')
    if not APPLY:
        print(f'\n预览完成：待迁移 {total_planned} 行。确认后执行：\n'
              f'  py -3.12 {os.path.basename(__file__)} --apply --confirm')
        return 0

    print(f'\n完成：迁移 {total} 行，剩余/失败 {total_remain} 行。')
    if _mig_log is not None:
        try:
            _mig_log.record(
                SCRIPT_NAME,
                note='students/teachers 身份证确定性IV→随机IV + id_card_idx 回填')
        except Exception as _e:  # noqa: BLE001
            print(f'[WARN] 迁移登记失败（不影响迁移结果）：{_e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

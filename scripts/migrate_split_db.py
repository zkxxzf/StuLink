"""数据库分库迁移 - 一键执行所有步骤"""
import sqlite3, os, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import functools
print = functools.partial(print, flush=True)  # 强制 flush

from config import BASE_DIR

BACKUP_DIR = os.path.join(BASE_DIR, 'data', 'backups')
DORM_DB = os.path.join(BASE_DIR, 'data', 'dormitory.db')
SYS_DB  = os.path.join(BASE_DIR, 'data', 'system.db')
HIST_DB = os.path.join(BASE_DIR, 'data', 'history.db')

def log(msg):
    print(f'  {msg}')

print('=' * 50)
print(' StuLink 数据库分库迁移')
print('=' * 50)

# ===== Step 0: 备份 =====
print('\n[Step 0] 备份现有数据')
os.makedirs(BACKUP_DIR, exist_ok=True)
for f in ['dormitory.db', 'history.db']:
    fp = os.path.join(BASE_DIR, 'data', f)
    if os.path.exists(fp):
        bp = os.path.join(BACKUP_DIR, f'pre_split_{f}')
        shutil.copy2(fp, bp)
        log(f'{f} → backups/pre_split_{f}')

# ===== Step 1: 创建 system.db =====
print('\n[Step 1] 创建 system.db')
src = sqlite3.connect(DORM_DB)

# 获取 dormitory.db 中所有表的建表SQL
tables = src.execute(
    "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
).fetchall()

# 需要迁移到 system.db 的表
SYSTEM_TABLES = [
    'users', 'students', 'dict_categories', 'dict_items',
    'permission_groups', 'class_profiles', 'class_subjects',
    'grade_settings', 'user_class_links', 'operation_logs'
]

# 创建 system.db
dst = sqlite3.connect(SYS_DB)
for name, sql in tables:
    if name in SYSTEM_TABLES:
        dst.execute(sql)
        log(f'创建表: {name}')

# ===== Step 2: 数据迁移 =====
print('\n[Step 2] 数据迁移 dormitory → system')
dst.execute("ATTACH DATABASE ? AS src_db", (DORM_DB,))

for name in SYSTEM_TABLES:
    count = src.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    if count == 0:
        log(f'{name}: 0 行，跳过')
        continue
    dst.execute(f"INSERT INTO {name} SELECT * FROM src_db.{name}")
    log(f'{name}: {count} 行')

dst.commit()
dst.execute("DETACH DATABASE src_db")

# ===== Step 3: 更新 history.db 加 target_type 列 =====
print('\n[Step 3] history.db 加 target_type 列')
if os.path.exists(HIST_DB):
    hist = sqlite3.connect(HIST_DB)
    cols = [r[1] for r in hist.execute("PRAGMA table_info(assignment_history)").fetchall()]
    if 'target_type' not in cols:
        hist.execute("ALTER TABLE assignment_history ADD COLUMN target_type VARCHAR(20) NOT NULL DEFAULT 'dormitory'")
        hist.execute("CREATE INDEX IF NOT EXISTS idx_ah_target_type ON assignment_history(target_type)")
        log('已添加 target_type 列')
    else:
        log('target_type 列已存在')
    hist.commit()
    hist.close()

# ===== Step 4: 清理 dormitory.db =====
print('\n[Step 4] 清理 dormitory.db 基础表')
for name in SYSTEM_TABLES:
    src.execute(f"DROP TABLE IF EXISTS {name}")
    log(f'删除: {name}')
src.commit()
src.execute("VACUUM")
src.close()

# ===== Step 5: 验证 =====
print('\n[Step 5] 验证')
errors = []
sys_c = sqlite3.connect(SYS_DB)
dorm_c = sqlite3.connect(DORM_DB)

sys_tables = set(r[0] for r in sys_c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
dorm_tables = set(r[0] for r in dorm_c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())

for t in SYSTEM_TABLES:
    if t not in sys_tables:
        errors.append(f'system.db 缺少表: {t}')
for t in SYSTEM_TABLES:
    if t in dorm_tables:
        errors.append(f'dormitory.db 仍有表: {t}')

if 'rooms' not in dorm_tables:
    errors.append('dormitory.db 缺少 rooms')
if 'bed_assignments' not in dorm_tables:
    errors.append('dormitory.db 缺少 bed_assignments')

# 数据行数对比（粗略）
for t in ['users', 'students', 'dict_categories']:
    cnt = sys_c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    log(f'system.db.{t}: {cnt} 行')

sys_c.close(); dorm_c.close()

if errors:
    print(f'\n⚠  {len(errors)} 个问题:')
    for e in errors:
        print(f'  - {e}')
else:
    print('\n✅ 迁移完成，验证通过！')

print(f'\n文件列表:')
for f in ['system.db', 'dormitory.db', 'history.db']:
    fp = os.path.join(BASE_DIR, 'data', f)
    if os.path.exists(fp):
        print(f'  {f}: {os.path.getsize(fp)//1024} KB')

print('\n下一步: 修改模型代码 (config.py, room.py, student.py)')


# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0



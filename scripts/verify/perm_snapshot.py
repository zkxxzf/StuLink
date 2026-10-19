# 只读快照工具：导出权限相关表，用于多身份改造的前后对比。
# 用法：python scripts/verify/perm_snapshot.py [data_dir] [out_json]
#   data_dir 缺省 = <repo>/data；out_json 缺省 = 打印到 stdout
# 只读（sqlite3 mode=ro），不改任何数据。
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import json
import os
import sqlite3
import sys

repo = _ROOT
data_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(repo, 'data')
out_path = sys.argv[2] if len(sys.argv) > 2 else None


def dump(db_file, sql, params=()):
    path = os.path.join(data_dir, db_file)
    if not os.path.exists(path):
        return []
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


snap = {
    'data_dir': data_dir,
    # 权限组（含 menu_keys 原始 JSON 串）
    'permission_groups': dump(
        'system.db',
        'SELECT id,name,role,scope_type,menu_keys,description '
        'FROM permission_groups ORDER BY id'),
    # 用户 → 组
    'users': dump(
        'system.db',
        'SELECT id,username,real_name,role,grade,class_name,'
        'permission_group_id,is_active FROM users ORDER BY id'),
    # 班主任带班
    'user_class_links': dump(
        'system.db',
        'SELECT id,user_id,grade,class_name FROM user_class_links ORDER BY id'),
    # 用户级数据范围（专职行政）
    'user_data_scopes': dump(
        'system.db',
        'SELECT id,user_id,grade,class_name FROM user_data_scopes ORDER BY id'),
    # 任课映射（在 grades.db）
    'teacher_subject_links': dump(
        'grades.db',
        'SELECT id,user_id,grade,class_name,subject,active '
        'FROM teacher_subject_links ORDER BY id'),
}

out = json.dumps(snap, ensure_ascii=False, indent=1)
if out_path:
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(out)
    print(f'[OK] 快照已写入 {out_path}')
    for k, v in snap.items():
        if isinstance(v, list):
            print(f'    {k}: {len(v)} 行')
else:
    print(out)

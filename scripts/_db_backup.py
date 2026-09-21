#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迁移脚本共用工具：改动 .db 之前先备份。

项目约定（与 scripts/migrate_cert_encrypt_v1125.py 同款写法）：
**任何会修改数据库文件的迁移脚本，动手前必须先备份**，出错可直接用备份还原。
备份文件名 ``<原文件>.bak-<YYYYmmdd_HHMMSS>``，同名时间戳冲突时自动加序号。

用法（在各迁移脚本里）：

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _db_backup import backup_db

    backup_db(DB_PATH)          # 单库
    backup_db([DB_A, DB_B])     # 多库，逐个备份

注意：本文件名以 ``_`` 开头只是为了避免被当成普通迁移脚本误执行，
它属于 scripts 目录中的公共模块，需要随仓库提交
（.gitignore 只忽略 scripts/_t_*.py 这类临时脚本）。
"""
import os
import shutil
from datetime import datetime


def _unique_dst(path):
    """生成不冲突的备份路径：<path>.bak-<时间戳>[_n]"""
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = f'{path}.bak-{stamp}'
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = f'{path}.bak-{stamp}_{n}'
    return dst


def _prune(path, keep):
    """只保留最近 keep 份同名备份（按修改时间排序，删更旧的）"""
    if not keep:
        return
    base = os.path.basename(path)
    d = os.path.dirname(path) or '.'
    prefix = f'{base}.bak-'
    olds = []
    for name in os.listdir(d):
        if not name.startswith(prefix):
            continue
        fp = os.path.join(d, name)
        if os.path.isfile(fp):
            olds.append((os.path.getmtime(fp), fp))
    olds.sort(reverse=True)
    for _, fp in olds[keep:]:
        try:
            os.remove(fp)
        except OSError:
            pass


def backup_db(db_paths, keep=5):
    """备份一个或多个 SQLite 库文件。

    返回 [(原路径, 备份路径 or None), ...]，库文件不存在时备份路径为 None。
    keep 为 0 时不做旧备份清理。
    """
    if isinstance(db_paths, (str, bytes, os.PathLike)):
        db_paths = [db_paths]
    result = []
    for p in db_paths:
        p = os.fspath(p)
        if not os.path.exists(p):
            print(f'[备份] 跳过（文件不存在）: {p}')
            result.append((p, None))
            continue
        dst = _unique_dst(p)
        shutil.copy2(p, dst)
        _prune(p, keep)
        print(f'[备份] {os.path.basename(p)} -> {os.path.basename(dst)}')
        result.append((p, dst))
    return result


if __name__ == '__main__':
    raise SystemExit('这是公共模块，请在其他迁移脚本里 import 使用')

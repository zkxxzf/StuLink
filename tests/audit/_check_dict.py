# -*- coding: utf-8 -*-
"""查 dict_items 在哪个库、TMP 残留多少"""
import glob
import sqlite3

for p in sorted(glob.glob('data/*.db')):
    con = sqlite3.connect(p)
    try:
        hit = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dict_items'"
        ).fetchone()
        if hit:
            vals = [r[0] for r in con.execute(
                "SELECT value FROM dict_items WHERE value LIKE 'TMP%'")]
            total = con.execute('SELECT COUNT(*) FROM dict_items').fetchone()[0]
            print(f'{p}: dict_items 共 {total} 条，TMP 残留 {len(vals)} 条 {vals}')
    finally:
        con.close()

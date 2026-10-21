# StuLink v1.19.49.47  2026-10-20
# 建立「智学网考号 ↔ 学号」对应表（姓名+班级唯一即可认，冲突一律不写）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""生成 / 补全 data/zhixue/学生对应.txt（学号<TAB>自定义考号<TAB>姓名）。

为什么需要这张表：智学网每场考试显示的是该场的**考号**，同一学生在
不同场次可能不是同一个号（2024 级 2026-10-08 那场是「2744+学号后6位」，
本次限时练是 8 位号），所以不能拿数据库里的 zhixue_no 直接匹配。

建立口径（**唯一才认，冲突不猜**）：
1. 姓名 + 规范化班级在目标年级里唯一 → 认；
2. 仅姓名唯一 → 认（班级写法差异兜底）；
3. 其余（同年级同名、找不到）→ 记入冲突清单，不写。

用法：
    py -3.12 -u scripts/zhixue/build_student_map.py
    py -3.12 -u scripts/zhixue/build_student_map.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data' / 'zhixue'
MAP_FILE = DATA / '学生对应.txt'
SYS_DB = ROOT / 'data' / 'system.db'
GRADES = ('2024级', '2025级', '2026级')


def norm_class(name):
    """「高三06班」/「高一8班」→「6班」「06班」统一成纯数字班号"""
    m = re.search(r'(\d{1,2})\s*班', name or '')
    if m:
        return str(int(m.group(1)))
    return re.sub(r'^高[一二三]?\s*[年级]*\s*', '', (name or '').strip())


def load_existing():
    """已有对应表 → {考号: (学号, 姓名)}"""
    out = {}
    if not MAP_FILE.exists():
        return out
    with open(MAP_FILE, encoding='utf-8-sig') as fh:
        for line in fh:
            parts = [p.strip() for p in line.rstrip('\n').split('\t')]
            if len(parts) >= 2 and parts[0] and parts[1]:
                out[parts[1]] = (parts[0], parts[2] if len(parts) > 2 else '')
    return out


def load_students():
    """主库学生 → {年级: [(学号, 姓名, 班号)]}"""
    con = sqlite3.connect(f'file:{SYS_DB.as_posix()}?mode=ro', uri=True)
    rows = con.execute(
        'SELECT student_number, name, COALESCE(class_name, ""), '
        'COALESCE(grade, "") FROM students').fetchall()
    con.close()
    out = defaultdict(list)
    for no, name, cls, grade in rows:
        if grade in GRADES:
            out[grade].append((no, (name or '').strip(), norm_class(cls)))
    return out


def collect_scraped():
    """抓取结果 → {年级: [(考号, 姓名, 班号)]}（去重保序）"""
    out, seen = defaultdict(list), set()
    for f in sorted(DATA.glob('timed/*.json')):
        if f.name in ('index.json', 'plan.json'):
            continue
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        grade = d.get('grade') or ''
        if grade not in GRADES:
            continue
        for r in d.get('rows') or []:
            no = str(r.get('exam_no') or '').strip()
            key = (grade, no)
            if not no or key in seen:
                continue
            seen.add(key)
            out[grade].append((no, (r.get('name') or '').strip(),
                               norm_class(r.get('class_name'))))
    return out


def resolve(candidates, students):
    """(考号,姓名,班号) → 学号；返回 (映射, 冲突列表)"""
    by_name, by_name_cls = defaultdict(list), defaultdict(list)
    for no, name, cls in students:
        by_name[name].append((no, cls))
        by_name_cls[(name, cls)].append(no)
    mapped, conflicts = {}, []
    for code, name, cls in candidates:
        hit = by_name_cls.get((name, cls)) or []
        if len(hit) == 1:
            mapped[code] = (hit[0], name)
            continue
        hit = [no for no, _c in by_name.get(name, [])]
        if len(hit) == 1:
            mapped[code] = (hit[0], name)
            continue
        conflicts.append((code, name, cls, len(hit)))
    return mapped, conflicts


def main():
    ap = argparse.ArgumentParser(description='建立智学网考号 ↔ 学号对应表')
    ap.add_argument('--apply', action='store_true', help='写回对应表')
    args = ap.parse_args()

    existing = load_existing()
    students = load_students()
    scraped = collect_scraped()
    added, conflicts_all = {}, []
    for grade in GRADES:
        mapped, conflicts = resolve(scraped.get(grade) or [], students[grade])
        new = {c: v for c, v in mapped.items() if c not in existing}
        added.update(new)
        conflicts_all.extend((grade, *c) for c in conflicts)
        print(f'{grade}：抓取考号 {len(scraped.get(grade) or [])} 个'
              f' | 可认 {len(mapped)} | 新增 {len(new)}'
              f' | 冲突 {len(conflicts)} | 学生 {len(students[grade])}')
    print(f'对应表现有 {len(existing)} 条，本次新增 {len(added)} 条')
    if conflicts_all:
        print('冲突样例（不写入，需人工核对）：')
        for grade, code, name, cls, n in conflicts_all[:10]:
            print(f'  {grade} {code} {name} {cls}班（同名 {n} 人）')
    if not args.apply or not added:
        print('[dry-run] 未写文件' if not args.apply else '无需变更')
        return
    merged = dict(existing)
    merged.update(added)
    lines = ['学号\t自定义考号\t姓名']
    for code, (no, name) in sorted(merged.items(), key=lambda x: x[1][0]):
        lines.append(f'{no}\t{code}\t{name}')
    MAP_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'已写入 {MAP_FILE}（{len(merged)} 条）')


if __name__ == '__main__':
    main()

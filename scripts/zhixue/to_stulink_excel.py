# StuLink v1.19.49.38  2026-10-15
# 智学网抓取结果 → StuLink 成绩导入 Excel
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""把 --scrape 抓到的学生成绩 JSON 转成 StuLink「成绩导入」用的 Excel。

三处口径转换（智学网 ↔ StuLink）：
  1. **学号**：智学网用的是「自定义考号」，必须用 `学生对应.txt` 映射回学号
     （表头：学号<TAB>自定义考号<TAB>姓名）
  2. **班级**：智学网「高一10班」→ 系统「10班」（系统 class_name 不带年级前缀）
  3. **科目名**：智学网「英语」→ 系统「外语」（别名表已覆盖）；
     智学网「生物学」→ 必须写成系统标准名「生物」（别名表**没有**这条，
     不改名会被整列静默忽略）

缺考（'--' / 空）一律留空，系统按「该科缺考」处理。

用法：
    python scripts/zhixue/to_stulink_excel.py                       # 转换 scraped 目录下全部
    python scripts/zhixue/to_stulink_excel.py --exam 期末            # 只转匹配的
    python scripts/zhixue/to_stulink_excel.py --check-only           # 只校验不写文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRAPED = ROOT / 'data' / 'zhixue' / 'scraped'
OUT_DIR = ROOT / 'data' / 'zhixue' / 'import'
MAP_FILE = ROOT / 'data' / 'zhixue' / '学生对应.txt'

# 输出列头：学号必须叫「学号」（导入识别硬条件）；考号列可选，用于留痕
HEAD = ['学号', '姓名', '班级', '年级', '考号', '总分',
        '语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
# 智学科目名 → 输出列名（英语交给系统别名；生物学必须改名，否则整列被忽略）
SUBJ_SRC = {'总分': '总分', '语文': '语文', '数学': '数学', '英语': '英语',
            '物理': '物理', '化学': '化学', '生物学': '生物', '政治': '政治',
            '历史': '历史', '地理': '地理'}


def log(msg):
    print(msg, flush=True)


def load_student_map(path):
    """返回 {自定义考号: (学号, 姓名)}"""
    m = {}
    with open(path, encoding='utf-8-sig') as f:
        first = f.readline()
        for line in f:
            parts = [p.strip() for p in line.rstrip('\n').split('\t')]
            if len(parts) >= 2 and parts[0] and parts[1]:
                m[parts[1]] = (parts[0], parts[2] if len(parts) > 2 else '')
    if '自定义考号' not in first and '考号' not in first:
        log(f'⚠ 注意：对应表表头是 {first.strip()!r}，按「第1列学号 / 第2列考号」解析')
    return m


def norm_class(cn):
    """「高一10班」→「10班」；已是「10班」则原样"""
    cn = (cn or '').strip()
    m = re.match(r'^高[一二三]?\s*[年级]*\s*(.+)$', cn)
    return m.group(1).strip() if m else cn


def cell_score(obj):
    """从 {'分数': '803.5', ...} 取分数；'--'/空 → None（缺考）"""
    v = str((obj or {}).get('分数', '')).strip()
    if v in ('', '--', '-', 'None'):
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def convert(data, smap, grade):
    rows, problems = [], []
    for r in data.get('rows') or []:
        exam_no = str(r.get('exam_no') or '').strip()
        hit = smap.get(exam_no)
        if not hit:
            problems.append(f'考号 {exam_no}（{r.get("name")}）不在对应表里，跳过')
            continue
        no, name_in_map = hit
        name = str(r.get('name') or '').strip()
        if name_in_map and name and name != name_in_map:
            problems.append(f'姓名不一致：表内 {name} / 对应表 {name_in_map}（学号 {no}），'
                            f'按表内保留')
        sc = r.get('scores') or {}
        rows.append([no, name, norm_class(r.get('class_name')), grade, exam_no]
                    + [cell_score(sc.get(s)) for s in SUBJ_SRC])
    return rows, problems


def write_xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '成绩'
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))


def main():
    ap = argparse.ArgumentParser(description='智学网抓取结果 → StuLink 导入 Excel')
    ap.add_argument('--exam', help='只转文件名含该关键字的考试')
    ap.add_argument('--map', default=str(MAP_FILE), help='学生对应表路径')
    ap.add_argument('--grade', default='2025级', help='年级值（系统口径如 2025级）')
    ap.add_argument('--check-only', action='store_true', help='只校验，不写文件')
    args = ap.parse_args()

    smap = load_student_map(Path(args.map))
    log(f'对应表：{len(smap)} 条（{args.map}）')

    files = sorted(SCRAPED.glob('*.json'))
    if args.exam:
        files = [f for f in files if args.exam in f.name]
    if not files:
        raise SystemExit('scraped 目录下没有匹配的 JSON')

    for f in files:
        data = json.loads(f.read_text(encoding='utf-8'))
        rows, problems = convert(data, smap, args.grade)
        log(f'\n=== {f.stem} ===')
        log(f'  抓取 {len(data.get("rows") or [])} 行 → 可用 {len(rows)} 行')
        if problems:
            log(f'  问题 {len(problems)} 条：')
            for p in problems[:12]:
                log(f'    - {p}')
            if len(problems) > 12:
                log(f'    ...另有 {len(problems) - 12} 条')
        if rows:
            cls = {}
            for r in rows:
                cls[r[2]] = cls.get(r[2], 0) + 1
            log(f'  班级分布：{dict(sorted(cls.items()))}')
            log(f'  样例：{rows[0]}')
        if not args.check_only and rows:
            dest = OUT_DIR / f'{f.stem}_导入.xlsx'
            write_xlsx(dest, rows)
            log(f'  已写出：{dest}')
    log('\n完成。')


if __name__ == '__main__':
    main()

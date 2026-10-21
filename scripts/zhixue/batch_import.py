# StuLink v1.19.54.64  2026-10-15
# 批量：scraped/*.json → 转换 → 新建考试 → 导入成绩
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""把 `--scrape` 抓到的所有考试批量导入 StuLink（一场一场建考试 + 写成绩）。

考试命名沿用系统既有习惯：`{年级}{YYYYMMDD}{阶段}（高X上/下）`
（同日同阶段多场自动加 A/B 后缀区分，避免同年级同名被阻断）。

阶段映射：周考/限时作业/标准化作业 → 限时练；月考 → 月考；
期中 → 期中考；期末 → 期末考。

默认 dry-run，只打印计划；`--apply` 才真正写库。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRAPED = ROOT / 'data' / 'zhixue' / 'scraped'
IMPORT_DIR = ROOT / 'data' / 'zhixue' / 'import'

KEY_DATE = re.compile(r'（(\d{4})-(\d{2})-(\d{2})）')
STAGE_MAP = [('周考', '限时练'), ('限时作业', '限时练'), ('限时练', '限时练'),
             ('标准化作业', '限时练'), ('月考', '月考'), ('期中', '期中考'),
             ('期末', '期末考')]
TYPE_MAP = {'限时练': '限时练', '月考': '月考', '期中考': '期中考', '期末考': '期末考'}


def log(msg):
    print(msg, flush=True)


def semester_of(grade, d: date):
    """2025级 + 2026-05-10 → 高一下（按入学年推算年级与学期）"""
    if not re.match(r'^\d{4}级$', grade or ''):
        return ''
    enter = int(grade[:4])
    start = d.year if d.month >= 9 else d.year - 1
    idx = start - enter
    name = ['高一', '高二', '高三'][idx] if 0 <= idx <= 2 else f'{idx + 1}年级'
    return f'{name}{"上" if d.month >= 9 else "下"}'


def stage_of(name):
    for kw, stage in STAGE_MAP:
        if kw in name:
            return stage
    return '月考'


def main():
    ap = argparse.ArgumentParser(description='批量导入智学网成绩到 StuLink')
    ap.add_argument('--grade', default='2025级')
    ap.add_argument('--kind', default='本校考')
    ap.add_argument('--exam', help='只处理文件名含该关键字的')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    files = sorted(SCRAPED.glob('*.json'))
    if args.exam:
        files = [f for f in files if args.exam in f.name]
    if not files:
        raise SystemExit('scraped 下没有 JSON')

    plan, used = [], {}
    for f in files:
        m = KEY_DATE.search(f.name)
        if not m:
            log(f'⚠ 文件名里没有日期，跳过：{f.name}')
            continue
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        stage = stage_of(f.name)
        base = f'{args.grade}{d:%Y%m%d}{stage}（{semester_of(args.grade, d)}）'
        seq = used.get(base, 0) + 1
        used[base] = seq
        name = base if seq == 1 else base.replace('（', f'{chr(64 + seq)}（')
        plan.append((f, name, stage, d))

    log(f'计划导入 {len(plan)} 场：')
    for f, name, stage, d in plan:
        log(f'  {d}  {name:32s} ← {f.stem}')

    if not args.apply:
        log('\n[dry-run] 未执行。加 --apply 落库。')
        return

    py = sys.executable
    for f, name, stage, d in plan:
        log(f'\n=== {name} ===')
        r = subprocess.run([py, str(HERE / 'to_stulink_excel.py'), '--exam', f.stem,
                            '--grade', args.grade], capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        xlsx = IMPORT_DIR / f'{f.stem}_导入.xlsx'
        if not xlsx.exists():
            log(f'  转换失败，跳过：\n{r.stdout[-500:]}\n{r.stderr[-300:]}')
            continue
        r2 = subprocess.run([py, str(HERE / 'import_to_stulink.py'),
                             '--xlsx', str(xlsx), '--grade', args.grade,
                             '--name', name, '--date', d.isoformat(),
                             '--type', TYPE_MAP[stage], '--kind', args.kind,
                             '--apply'], capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
        tail = [ln for ln in (r2.stdout or '').splitlines() if ln.strip()]
        for ln in tail[-4:]:
            log('  ' + ln)
        if r2.returncode != 0:
            log('  ✗ 失败：' + (r2.stderr or '')[-300:])
    log('\n全部完成。')


if __name__ == '__main__':
    main()

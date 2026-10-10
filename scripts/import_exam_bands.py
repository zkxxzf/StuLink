# -*- coding: utf-8 -*-
# StuLink v1.18.9.1 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""从历次考试 Excel 提取分数线并导入 exam_bands（幂等，默认 dry-run）

数据位置（两种版式都支持）：
  A. 「上线率 (公式)」sheet 顶部「参数设置」区（高一上）：
       参数设置 | 名次 | 总分 | 语文 | 数学 | 英语 | 物理 | 化学 | 生物
       211线   | 50  | 496 | 77 | ...
       一本线  | 252 | 441 | 66 | ...
       二本线  | 471 | 352 | 51 | ...
  B. 分科后文件在其它 sheet 的同类「*线」行（自动扫描全部 sheet 寻找）

层级映射（用户 2026-10-09 确认）：
  一本线 → 特控（seq=1）    二本线 → 本科（seq=2）
  211线 → 默认不导入（可用 --with-211 作为 seq=0 之外的额外层，需另行设计）

方向：文件名含「历史选科」→ direction='历史'；「物理选科」→ '物理'；否则 '' (双向套用)
subject：'总分' 为总分线；其余为各科单科线（系统支持独立配置）

用法：
    python -m scripts.import_exam_bands              # dry-run 预览
    python -m scripts.import_exam_bands --apply      # 写库
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl                                                    # noqa: E402

from app import create_app                                         # noqa: E402
from app.extensions import db                                      # noqa: E402
from app.models.grades import (Exam, ExamBand, SUBJECTS,           # noqa: E402
                               TOTAL_SUBJECT)

D24 = os.environ.get('STULINK_EXCEL_DIR') or r'd:\Users\lenovo\Desktop\2024级历次考试成绩'

# (考试日期前缀, 文件列表) —— 与成绩导入时的场次定义一致
EXAM_FILES = [
    ('2024-09', ['202409【24级高一第一次限时练】全部考生成绩汇总 - 公式.xlsx']),
    ('2024-10', ['202410金太阳联考-_学生成绩管理系统表 - 改进速度.xlsx']),
    ('2024-11', ['202411期中考试-_学生成绩管理系统表 - 20241113（新增大量图表）.xlsx']),
    ('2024-12', ['202412月考-_学生成绩管理系统表 - 20241224-不分科-赋分+上线率.xlsx']),
    ('2025-01', ['202501期末考试-_学生成绩管理系统表 -20250122（根据教育局最新分数线修改增加总分一本二本线）.xlsx']),
    ('2025-03', ['202503月考-历史选科-成绩分析20250324.xlsx', '202503月考-物理选科-成绩分析20250324.xlsx']),
    ('2025-04', ['202504期中-历史选科-成绩分析20250422.xlsx', '202504期中-物理选科-成绩分析20250422.xlsx']),
    ('2025-05', ['202505月考-历史选科-成绩分析20250529.xlsx', '202505月考-物理选科-成绩分析20250529.xlsx']),
    ('2025-07', ['202507期末-历史选科-成绩分析20250707.xlsx', '202507期末-物理选科-成绩分析20250707.xlsx']),
    ('2025-10', ['202510月考-历史选科-成绩分析20251019.xlsx', '202510月考-物理选科-成绩分析20251019.xlsx']),
    ('2025-11', ['202511期中-历史选科-成绩分析20251117.xlsx', '202511期中-物理选科-成绩分析20251117.xlsx']),
    ('2026-01', ['202601月考-历史选科-成绩分析20260107.xlsx', '202601月考-物理选科-成绩分析20260107.xlsx']),
    ('2026-02', ['202602期末-历史选科-成绩分析20260206（修改）.xlsx', '202602期末-物理选科-成绩分析20260206（修改）.xlsx']),
    ('2026-04', ['202604月考-历史选科-成绩分析20260409.xlsx', '202604月考-物理选科-成绩分析20260409.xlsx']),
    ('2026-05', ['202605期中-历史选科-成绩分析20260517.xlsx', '202605期中-物理选科-成绩分析20260517.xlsx']),
    ('2026-07', ['202607期末-历史选科-成绩分析20260711.xlsx', '202607期末-物理选科-成绩分析20260711.xlsx']),
    ('2026-09', ['202609月考-历史选科-成绩分析20260912-2.xlsx', '202609月考-物理选科-成绩分析20260912-2.xlsx']),
]

# 层名映射：源名 → (系统层名, 是否导入)
# v1.18.8.0 用户口径：文件里有哪一档就导入哪一档；没有 985线 就不导入（不编造、不留空占位）
LAYER_MAP = {
    '一本线': ('特控', True), '一本': ('特控', True),
    '特控线': ('特控', True), '特控': ('特控', True),
    '二本线': ('本科', True), '二本': ('本科', True),
    '本科线': ('本科', True), '本科': ('本科', True),
    '211线': ('211', True), '211': ('211', True),
}
# 规范层序（与默认模板一致）：缺档时不占用 seq，后续补录也能插到正确位置
# 用户原则：划线是考试的内置数据（像任课教师一样），seq/层名自带，不依赖模板
CANON_SEQ = {'985': 1, '211': 2, '特控': 3, '本科': 4}
SUBJECTS_SET = set(SUBJECTS) | {'英语'}     # 兼容 Excel 写「英语」而系统内部叫「外语」
SUBJECT_ALIAS = {'英语': '外语'}
LINE_ROW_RE = re.compile(r'^(211|一本|二本|特控|本科)\s*线?$')


def s_of(v):
    return str(v).strip() if v is not None else ''


def num_of(v):
    s = s_of(v).replace('，', ',')
    if not s or s in ('-', '—', '无'):
        return None
    try:
        return float(s.split('/')[0].strip())
    except ValueError:
        return None


def detect_direction(fname):
    if '历史' in fname:
        return '历史'
    if '物理' in fname:
        return '物理'
    return ''


def read_custom_params(path):
    """版式 B（主流，16/17 个文件都是这布局）：「自定义参数」的「学科分数线」区

    r21  学科分数线 | 211   | 一本/特控 | 二本/本科 | 未上线   ← 表头（列=层名）
    r22  总分      | 841   | 759     | 655
    r23… 语文/数学/英语/物理/…  → 各科单科线
    遇到「目标设定」或其它区则停（目标设定是班级目标人数，不是线）。
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    cand = [n for n in wb.sheetnames if '自定义参数' in n]
    if not cand:
        wb.close()
        return [], ''
    ws = wb[cand[0]]
    rows = [[s_of(c) for c in (r or [])]
            for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 60),
                                  values_only=True)]
    wb.close()
    hi = None
    for i, cells in enumerate(rows):
        if any(c == '学科分数线' for c in cells):
            hi = i
            break
    if hi is None:
        return [], ''
    hdr = rows[hi]
    # ❶ 该 sheet 内容不是从第 0 列开始（本例从第 5 列起），必须先找到标签列，
    #    否则用 cells[0] 判断会全部为空 → 一行都取不到
    i_lab = next((j for j, c in enumerate(hdr) if c == '学科分数线'), 0)
    # 列 -> 层名（只收能映射到系统层的列；「未上线」是兜底文本，不导入）
    lay_cols = []
    for j, c in enumerate(hdr):
        if j <= i_lab or not c:
            continue
        if c in LAYER_MAP:
            lay, keep = LAYER_MAP[c]
            lay_cols.append((j, lay, keep))
    if not lay_cols:
        return [], ''
    out = []
    for cells in rows[hi + 1:]:
        if not cells or i_lab >= len(cells) or not cells[i_lab]:
            continue
        lab = cells[i_lab]
        if '目标' in lab or lab in ('班级分类', '合计'):
            break                                  # 离开分数线区
        if lab == TOTAL_SUBJECT or '总分' in lab:
            subj = TOTAL_SUBJECT
        else:
            key = SUBJECT_ALIAS.get(lab, lab)
            subj = key if key in set(SUBJECTS) else ''
        if not subj:
            continue
        for j, lay, keep in lay_cols:
            v = num_of(cells[j]) if j < len(cells) else None
            if v is None:
                continue
            out.append({'layer': lay, 'keep': keep, 'subject': subj, 'value': v})
    return out, cand[0]


def scan_workbook(path):
    """在全部 sheet 里找「*线」参数区 → [{layer, subject, value}]"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    found, where = [], ''
    for ws in wb.worksheets:
        rows = [[s_of(c) for c in (r or [])]
                for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 60),
                                      values_only=True)]
        hi = None
        for i, cells in enumerate(rows):
            # 表头候选：含「总分」且至少 2 个科目名（或含「参数设置」）
            subj_cols = [(j, c) for j, c in enumerate(cells)
                         if c in SUBJECTS_SET or c == TOTAL_SUBJECT]
            if len(subj_cols) >= 3 and ('参数设置' in cells or '名次' in cells
                                        or TOTAL_SUBJECT in cells):
                hi = i
                break
        if hi is None:
            continue
        hdr = rows[hi]
        cols = [(j, c) for j, c in enumerate(hdr)
                if c in SUBJECTS_SET or c == TOTAL_SUBJECT]
        block = []
        for cells in rows[hi + 1:hi + 8]:
            if not cells:
                continue
            nm = cells[0]
            m = LINE_ROW_RE.match(nm)
            if not m:
                break
            layer, keep = LAYER_MAP.get(nm.replace(' ', ''), (m.group(1), True))
            vals = {(SUBJECT_ALIAS.get(c, c)): num_of(cells[j]) if j < len(cells) else None
                    for j, c in cols}
            if vals.get(TOTAL_SUBJECT) is None:
                continue
            for subj, v in vals.items():
                if v is not None and subj != '名次':
                    block.append({'layer': layer, 'keep': keep,
                                  'subject': subj, 'value': v,
                                  'rank': num_of(cells[1]) if len(cells) > 1 and hdr[1] == '名次' else None})
        if block:
            found += block
            if not where:
                where = ws.title
    wb.close()
    return found, where


def main():
    ap = argparse.ArgumentParser(description='从考试 Excel 提取分数线导入 exam_bands')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--with-211', action='store_true', help='同时导入 211 线')
    args = ap.parse_args()
    print('模式: %s\n' % ('APPLIED' if args.apply else 'DRY-RUN（未写库）'))

    app = create_app()
    with app.app_context():
        exams = Exam.query.all()
        by_date = {}
        for e in exams:
            by_date.setdefault(e.exam_date.strftime('%Y-%m'), []).append(e)
        n_exam = n_band = 0
        skipped = []
        for prefix, files in EXAM_FILES:
            exs = by_date.get(prefix) or []
            ex = exs[0] if exs else None
            if ex is None:
                skipped.append('%s 无对应考试' % prefix)
                continue
            all_rows = []
            for fn in files:
                p = os.path.join(D24, fn)
                if not os.path.exists(p):
                    skipped.append('%s 文件缺失' % fn[:24])
                    continue
                # 优先用「自定义参数」的「学科分数线」区（覆盖绝大多数文件）
                got, sheet = read_custom_params(p)
                if not got:
                    got, sheet = scan_workbook(p)          # 再试「参数设置」区
                d = detect_direction(fn)
                for r in got:
                    r['direction'] = d
                    r['sheet'] = sheet
                all_rows += got
            keep = [r for r in all_rows if r['keep'] or (args.with_211 and r['layer'] == '211')]
            if not keep:
                skipped.append('%s #%d 未找到分数线区' % (prefix, ex.id))
                print('  %s #%d %-24s  [未找到]' % (prefix, ex.id, ex.name[:22]))
                continue
            layers = sorted({r['layer'] for r in keep})
            subj_n = len({(r['direction'], r['subject']) for r in keep})
            print('  %s #%d %-24s  %-16s 层=%s 条目=%d (sheet:%s)'
                  % (prefix, ex.id, ex.name[:22], '方向' + (keep[0]['direction'] or '双向'),
                     layers, len(keep), keep[0]['sheet']))
            n_exam += 1
            n_band += len(keep)
            if args.apply:
                # 按「考试 × 方向 × 科目」整体覆盖：避免与用户手工划线已占的 seq 撞
                # UNIQUE(exam_id,direction,subject,seq)；不涉及其他方向/科目的已有线。
                cells = {(r['direction'] or '', r['subject']) for r in keep}
                for dd, ss in cells:
                    ExamBand.query.filter_by(exam_id=ex.id, direction=dd,
                                             subject=ss).delete()
                db.session.flush()
                # 同一 (direction, subject, seq) 只保留一条（防历史/物理两文件归一时重复）
                seen_cell = set()
                rows_ins = []
                for r in sorted(keep, key=lambda x: CANON_SEQ.get(x['layer'], 9)):
                    key = (r['direction'] or '', r['subject'], CANON_SEQ.get(r['layer'], 9))
                    if key in seen_cell:
                        continue
                    seen_cell.add(key)
                    rows_ins.append(r)
                for r in rows_ins:
                    db.session.add(ExamBand(
                        exam_id=ex.id, direction=r['direction'] or '',
                        subject=r['subject'], seq=CANON_SEQ.get(r['layer'], 9),
                        name=r['layer'], lower_mode='score',
                        lower_value=r['value']))
                db.session.commit()

        print('\n合计 %d 场、%d 条分数线' % (n_exam, n_band))
        if skipped:
            print('未处理 %d 项:' % len(skipped))
            for s in skipped[:12]:
                print('   -', s)
        if not args.apply:
            print('\n确认无误后执行：python -m scripts.import_exam_bands --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())

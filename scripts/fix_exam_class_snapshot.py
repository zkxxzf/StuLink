# -*- coding: utf-8 -*-
# StuLink v1.18.8.0 2026-10-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""修正历史考试的「考试当时班级」快照（t4，幂等 + dry-run）

问题：20 场考试的 exam_scores.class_name 全是 2026-09-22 导入时值
（当时的导入脚本虽从 Excel 取了班级，但平台层又用主库当前值覆盖了）。

本脚本把「分班前」的场次改为考试当时班级：
- 2024级 高一上 5 场（2024-09~2025-01，分班日 2025-02-13 之前）
  → 旧成绩 Excel 的「班级」列（考试当时导出，最权威）
- 2025级 3 场（2025-10~2025-12，分班日 2026-03-05 之前）
  → 20251219 核对名单的「分班」列

匹配方式：
- 2024级 Excel 的学号列不可靠（部分为空）→ **按姓名**在该场 exam_scores 内匹配，重名跳过
- 2025级 名单学号可靠 → 按学号匹配

随后重算受影响场次的 rank_class（班排名）；rank_dir 不受影响。

用法：
    python -m scripts.fix_exam_class_snapshot            # dry-run
    python -m scripts.fix_exam_class_snapshot --apply    # 实际写库
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl                                                    # noqa: E402

from app import create_app                                         # noqa: E402
from app.extensions import db                                      # noqa: E402
from app.models.grades import Exam, ExamScore                      # noqa: E402
from app.modules.grades.utils import normalize_class_name          # noqa: E402

# 路径可用环境变量覆盖（便于在服务器上跑）
D24 = os.environ.get('STULINK_EXCEL_DIR') or r'd:\Users\lenovo\Desktop\2024级历次考试成绩'
_ROST = os.environ.get('STULINK_ROSTER_DIR') or r'd:\Users\lenovo\Desktop'
ROSTER_25 = os.path.join(_ROST, '2025级20251219核对人员1人未入班.xlsx')

# 2024级 高一上 5 场（分班前）：(考试日期前缀, 文件名)
SRC_2024_UP = [
    ('2024-09', '202409【24级高一第一次限时练】全部考生成绩汇总 - 公式.xlsx'),
    ('2024-10', '202410金太阳联考-_学生成绩管理系统表 - 改进速度.xlsx'),
    ('2024-11', '202411期中考试-_学生成绩管理系统表 - 20241113（新增大量图表）.xlsx'),
    ('2024-12', '202412月考-_学生成绩管理系统表 - 20241224-不分科-赋分+上线率.xlsx'),
    ('2025-01', '202501期末考试-_学生成绩管理系统表 -20250122（根据教育局最新分数线修改增加总分一本二本线）.xlsx'),
]


def s_of(v):
    return str(v).strip() if v is not None else ''


def read_excel_name_class(path):
    """返回 {姓名: 班级}（班级已归一化）。表头行自动定位（含 学生姓名+班级）。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = None
    for t in ('成绩汇总表', '全部考生成绩汇总'):
        if t in wb.sheetnames:
            ws = wb[t]
            break
    if ws is None and wb.worksheets:
        ws = wb.worksheets[0]
    if ws is None:
        wb.close()
        return {}, '无 sheet'

    rows = []
    for i, r in enumerate(ws.iter_rows(min_row=1, max_row=12, values_only=True)):
        rows.append([s_of(c) for c in (r or [])])
    hdr_i, i_nm, i_cls = None, None, None
    for i, cells in enumerate(rows):
        nm_j = next((j for j, c in enumerate(cells)
                     if c in ('学生姓名', '姓名')), None)
        cls_j = next((j for j, c in enumerate(cells) if c == '班级'), None)
        if nm_j is not None and cls_j is not None:
            hdr_i, i_nm, i_cls = i, nm_j, cls_j
            break
    if hdr_i is None:
        wb.close()
        return {}, '未找到含 姓名+班级 的表头行'

    out, dup = {}, 0
    for r in ws.iter_rows(min_row=hdr_i + 2, values_only=True):
        cells = list(r or [])
        nm = s_of(cells[i_nm]) if i_nm < len(cells) else ''
        raw = cells[i_cls] if i_cls < len(cells) else ''
        if not nm:
            continue
        if raw == '总分' or nm == '总分':
            continue
        cls = normalize_class_name(raw)
        if not cls:
            continue
        if nm in out and out[nm] != cls:
            dup += 1
            continue
        out[nm] = cls
    wb.close()
    return out, f'表头第{hdr_i + 1}行 → 姓名列{i_nm}/班级列{i_cls}，共 {len(out)} 人'


def read_roster_no_class(path):
    """2025级核对名单 → {学号: 归一化班级}"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    hdr = [s_of(c.value) for c in ws[1]]
    i_no = hdr.index('学号')
    i_cls = next((j for j, h in enumerate(hdr) if h == '分班'), None)
    if i_cls is None:
        wb.close()
        return {}
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        no = s_of(r[i_no]) if i_no < len(r) else ''
        if no.endswith('.0'):
            no = no[:-2]
        if not (no and no.isdigit()):
            continue
        cls = normalize_class_name(r[i_cls]) if i_cls < len(r) else ''
        if cls:
            out[no] = cls
    wb.close()
    return out


def recalc_class_rank(exam_id):
    rows = ExamScore.query.filter_by(exam_id=exam_id).all()
    by = {}
    for r in rows:
        by.setdefault((r.subject, r.class_name or '—'), []).append(r)
    n = 0
    for _k, lst in by.items():
        lst.sort(key=lambda x: (-(x.score if x.score is not None else -1), x.id))
        for i, r in enumerate(lst, 1):
            if r.rank_class != i:
                r.rank_class = i
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description='修正历史考试的考试当时班级（幂等）')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    print('模式: %s\n' % ('APPLIED' if args.apply else 'DRY-RUN（未写库）'))

    app = create_app()
    with app.app_context():
        planned = []      # (exam, [(row, old, new)], note)
        skipped = []

        # ---------- A. 2024级 高一上：Excel 班级列（按姓名匹配） ----------
        print('=== A. 2024级 高一上（按姓名匹配 Excel 班级列）===')
        exams24 = (Exam.query.filter(Exam.grade == '2024级')
                   .order_by(Exam.exam_date).all())
        for prefix, fname in SRC_2024_UP:
            p = os.path.join(D24, fname)
            if not os.path.exists(p):
                print('  [缺文件] %s' % fname[:40])
                continue
            nm2cls, info = read_excel_name_class(p)
            ex = next((e for e in exams24
                       if e.exam_date.strftime('%Y-%m') == prefix), None)
            if ex is None:
                print('  [缺考试] %s' % prefix)
                continue
            rows = ExamScore.query.filter_by(exam_id=ex.id, subject='总分').all()
            nm2rows = {}
            for r in rows:
                nm2rows.setdefault(r.student_name or '', []).append(r)
            chg, amb, miss = [], 0, 0
            for nm, cls in nm2cls.items():
                cand = nm2rows.get(nm) or []
                if len(cand) != 1:
                    amb += 1 if cand else 0
                    miss += 0 if cand else 1
                    continue
                r = cand[0]
                if (r.class_name or '') != cls:
                    chg.append((r, r.class_name, cls))
            print('  %s #%d %s: %s；待改 %d 人（重名跳过 %d，名单外 %d）'
                  % (prefix, ex.id, ex.name[:20], info, len(chg), amb, miss))
            if chg:
                planned.append((ex, chg, 'excel'))
            else:
                skipped.append(ex)

        # ---------- B. 2025级：核对名单（按学号匹配） ----------
        print('\n=== B. 2025级（按学号匹配 20251219 名单）===')
        roster = read_roster_no_class(ROSTER_25)
        print('  名单 %d 条' % len(roster))
        exams25 = Exam.query.filter(Exam.grade == '2025级').order_by(Exam.exam_date).all()
        for ex in exams25:
            rows = ExamScore.query.filter_by(exam_id=ex.id, subject='总分').all()
            chg = []
            miss = 0
            for r in rows:
                cls = roster.get(r.student_no)
                if not cls:
                    miss += 1
                    continue
                if (r.class_name or '') != cls:
                    chg.append((r, r.class_name, cls))
            print('  #%d %s %s: 待改 %d 人（名单外 %d）'
                  % (ex.id, ex.exam_date, ex.name[:20], len(chg), miss))
            if chg:
                planned.append((ex, chg, 'roster'))
            else:
                skipped.append(ex)

        # ---------- 汇总 ----------
        total = sum(len(c) for _e, c, _s in planned)
        print('\n=== 待改明细（每场前 3 例）===')
        for ex, chg, src in planned:
            sample = '、'.join('%s:%s→%s' % (r.student_name or r.student_no,
                                            o or '空', n) for r, o, n in chg[:3])
            print('  #%d %s %s [%s] 改 %d 人 | %s'
                  % (ex.id, ex.exam_date, ex.name[:22], src, len(chg), sample))
        print('\n合计 %d 场、%d 行' % (len(planned), total))

        if args.apply:
            for _ex, chg, _s in planned:
                for r, _o, n in chg:
                    r.class_name = n
            db.session.commit()
            print('\n=== 重算班排名 ===')
            for ex, _c, _s in planned:
                k = recalc_class_rank(ex.id)
                print('  #%d %s: rank_class 更新 %d 行' % (ex.id, ex.name[:20], k))
            db.session.commit()
            print('\n已写库。')
        else:
            db.session.rollback()
            print('\n确认无误后执行：python -m scripts.fix_exam_class_snapshot --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())

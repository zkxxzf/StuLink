# -*- coding: utf-8 -*-
# StuLink v1.18.9.1 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""从旧成绩 Excel 回填「考试当时任课快照」（t5，幂等 + dry-run）

数据源（两种格式）：
  A. 「教师任课表」sheet（202409）：年级|班级|选科|班主任|副班|语文|数学|… → 一班一行
  B. 「各班成绩分析(列表)」前两行（202503 起）：第1行班级+各科教师、第2行科目名
无法识别的文件保持既有快照（多为 backfill 推测值）。

写入：exam_teacher_links，source='excel'，replace=True（覆盖本场既有快照）。
教师名 → user_id 按 User.real_name 反查；匹配不到只存姓名（不参与排名聚合）。

用法：
    python -m scripts.backfill_exam_teachers            # dry-run
    python -m scripts.backfill_exam_teachers --apply
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl                                                    # noqa: E402

from app import create_app                                         # noqa: E402
from app.extensions import db                                      # noqa: E402
from app.models import User                                        # noqa: E402
from app.models.grades import Exam, ExamTeacherLink                # noqa: E402
from app.modules.grades.utils import normalize_class_name          # noqa: E402

D24 = os.environ.get('STULINK_EXCEL_DIR') or r'd:\Users\lenovo\Desktop\2024级历次考试成绩'
SUBJ_ORDER = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
SKIP_SUBJ = {'总分', '合计', '平均分', '人数', ''}
# v1.18.9.1 班主任一并快照（与任课同表，伪科目名 '班主任'）
HEAD_SUBJECT = '班主任'
# 格式 B 里「总分」列下写的就是班主任（已验证：202503 物理 03班 总分=王金仁，
# 与当前库 03班 班主任=王金仁、郃文哲 吻合；202409 任课表也有独立「班主任」列）
HEAD_ALIAS = {'总分': HEAD_SUBJECT, '班主任': HEAD_SUBJECT}

# (考试日期前缀, 文件列表) —— 与导入时的场次定义一致
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


def is_name(v):
    """看起来像人名（排除纯数字/占位符/表头类文字）；防提取到分数线、人数等"""
    s = s_of(v)
    if not s or s in ('-', '—', '无', '0'):
        return False
    if s.replace('.', '').isdigit():
        return False
    if len(s) > 5:                      # 中文姓名最长一般 4~5 字；超过必非人名
        return False
    BAD_WORDS = ('人数', '花名册', '平均分', '最高分', '最低分', '名次', '合计',
                 '班级', '科目', '排名', '总分', '统计', '名单')
    if any(w in s for w in BAD_WORDS):
        return False
    return len(s) >= 2


# 无任课源时的借用规则（用户口径：同一学期任课安排一致）
# 2024-10 金太阳联考的文件里没有任课表 → 借用同学期的下一场（2024-11）
BORROW = {'2024-10': '2024-11'}


def s_of(v):
    return str(v).strip() if v is not None else ''


def looks_like_class(v):
    s = s_of(v)
    return bool(s) and ('班' in s or '级' in s) and normalize_class_name(s)


def read_tch_sheet(path):
    """格式 A：「教师任课表」→ [(班级, 科目, 教师)]"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    cand = [n for n in wb.sheetnames if '任课' in n]
    if not cand:
        wb.close()
        return []
    ws = wb[cand[0]]
    rows = [[s_of(c) for c in (r or [])] for r in ws.iter_rows(values_only=True)]
    wb.close()
    if len(rows) < 3:
        return []
    # 找表头行（含 班级 + 至少一个科目）
    hi = None
    for i, cells in enumerate(rows[:6]):
        if '班级' in cells and any(s in cells for s in SUBJ_ORDER):
            hi = i
            break
    if hi is None:
        return []
    hdr = rows[hi]
    i_cls = hdr.index('班级')
    subj_cols = [(j, h) for j, h in enumerate(hdr)
                 if (h in SUBJ_ORDER or h in HEAD_ALIAS) and j != i_cls]
    out = []
    for r in rows[hi + 1:]:
        if len(r) <= i_cls:
            continue
        cls = normalize_class_name(r[i_cls])
        if not cls or cls in ('不分班', '已转出'):
            continue
        for j, h in subj_cols:
            nm = s_of(r[j]) if j < len(r) else ''
            if not is_name(nm):
                continue
            sub = HEAD_ALIAS.get(h, h)
            if sub == HEAD_SUBJECT:                 # 主/副合并（唯一约束）
                exist = next((x for x in out if x[0] == cls and x[1] == HEAD_SUBJECT), None)
                if exist:
                    idx = out.index(exist)
                    if nm not in exist[2]:
                        out[idx] = (cls, HEAD_SUBJECT, exist[2] + '、' + nm)
                    continue
            out.append((cls, sub, nm))
    return out


def read_pivot_sheet(path):
    """格式 B：「各班成绩分析(列表)」前两行 → [(班级, 科目, 教师)]

    sheet 名可能用全角/半角括号，故用子串匹配。
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    cand = [n for n in wb.sheetnames if '各班成绩分析' in n]
    if not cand:
        wb.close()
        return []
    # 优先含“列表”的（分班后文件的标准位置）
    name = next((n for n in cand if '列表' in n), cand[0])
    ws = wb[name]
    # 全表扫描：找出**所有**相邻两行（上行含班级名、下行含科目名）的块
    # （一个 sheet 里每个班一个块：历史选科 2 块、物理选科 8 块）
    rows = [[s_of(c) for c in (r or [])]
            for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 400),
                                  values_only=True)]
    wb.close()
    out = []
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        if not (any(looks_like_class(x) for x in a) and any(
                x in SUBJ_ORDER or x in HEAD_ALIAS for x in b)):
            continue
        cur_cls = None
        for j in range(min(len(a), len(b))):
            c1, c2 = a[j], b[j]
            if looks_like_class(c1) and not normalize_class_name(c2):
                cur_cls = normalize_class_name(c1)
                continue
            if not cur_cls or cur_cls in ('不分班', '已转出'):
                continue
            sub = HEAD_ALIAS.get(c2, c2) if c2 in SUBJ_ORDER or c2 in HEAD_ALIAS else ''
            if sub and is_name(c1):
                if sub == HEAD_SUBJECT:
                    exist = next((x for x in out if x[0] == cur_cls and x[1] == HEAD_SUBJECT), None)
                    if exist:
                        if c1 not in exist[2]:
                            out[out.index(exist)] = (cur_cls, HEAD_SUBJECT,
                                                     exist[2] + '、' + c1)
                        continue
                out.append((cur_cls, sub, c1))
    return out


def main():
    ap = argparse.ArgumentParser(description='从旧 Excel 回填任课快照（幂等）')
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    print('模式: %s\n' % ('APPLIED' if args.apply else 'DRY-RUN（未写库）'))

    app = create_app()
    with app.app_context():
        name2uid = {u.real_name: u.id for u in User.query.all() if u.real_name}
        exams24 = Exam.query.filter(Exam.grade == '2024级').all()
        total_rows = total_exams = 0
        miss_names = {}

        for prefix, files in EXAM_FILES:
            ex = next((e for e in exams24
                       if e.exam_date.strftime('%Y-%m') == prefix), None)
            if ex is None:
                continue
            pairs = []
            used = []
            for fn in files:
                p = os.path.join(D24, fn)
                if not os.path.exists(p):
                    continue
                got = read_tch_sheet(p)
                src = 'A' if got else ''
                if not got:
                    got = read_pivot_sheet(p)
                    src = 'B' if got else ''
                if got:
                    pairs += got
                    used.append('%s(%s,%d条)' % (fn[:14], src, len(got)))
            if not pairs:
                # 无任课源 → 尝试借用同学期相邻场次的快照
                src_pref = BORROW.get(prefix)
                src_ex = next((e for e in exams24
                               if src_pref and e.exam_date.strftime('%Y-%m') == src_pref),
                              None)
                if src_ex is not None:
                    borrowed = ExamTeacherLink.query.filter_by(exam_id=src_ex.id).all()
                    if borrowed:
                        pairs = [(r.class_name, r.subject, r.teacher_name or '')
                                 for r in borrowed if r.teacher_name]
                        print('  %s #%d %s: 借用同学期 %s(#%d) 的任课 %d 条'
                              % (prefix, ex.id, ex.name[:18], src_pref, src_ex.id,
                                 len(pairs)))
                if not pairs:
                    print('  %s #%d %s: 无可用任课源，保持现状'
                          % (prefix, ex.id, ex.name[:18]))
                    continue
            # 去重（同班同科取首个）
            seen, rows = set(), []
            for cls, sub, nm in pairs:
                if (cls, sub) in seen:
                    continue
                seen.add((cls, sub))
                uid = name2uid.get(nm)
                if not uid:
                    miss_names[nm] = miss_names.get(nm, 0) + 1
                rows.append({'class_name': cls, 'subject': sub,
                             'teacher_name': nm, 'user_id': uid})
            print('  %s #%d %s: %d 条（%s）'
                  % (prefix, ex.id, ex.name[:18], len(rows), ' '.join(used)))
            total_rows += len(rows)
            total_exams += 1
            if args.apply:
                ExamTeacherLink.query.filter_by(exam_id=ex.id).delete()
                db.session.flush()
                for r in rows:
                    db.session.add(ExamTeacherLink(
                        exam_id=ex.id, grade=ex.grade,
                        class_name=r['class_name'], subject=r['subject'],
                        user_id=r['user_id'], teacher_name=r['teacher_name'],
                        source='excel'))
                db.session.commit()

        print('\n合计 %d 场、%d 条快照' % (total_exams, total_rows))
        if miss_names:
            print('未匹配到系统账号的教师名（只存姓名，不参与排名聚合）%d 个：'
                  % len(miss_names))
            for nm, n in sorted(miss_names.items(), key=lambda x: -x[1])[:15]:
                print('   %s (%d)' % (nm, n))
        if not args.apply:
            print('\n确认无误后执行：python -m scripts.backfill_exam_teachers --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())

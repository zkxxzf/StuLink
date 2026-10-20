# StuLink v1.19.49.47  2026-10-20
# 智学网抓取的限时练成绩 → StuLink 限时练（不写月考）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""把 `scrape_timed.py` 抓到的 JSON 导入 StuLink「限时练」模块。

与旧脚本（import_to_stulink.py）的区别：
- **目标表是 timed_exams / timed_scores**，普通考试（exams）一条都不碰；
- 一场限时练可能含多科，按科追加（同科重导 = 整科覆盖）；
- 学生匹配只认考号 → 学号（`timed_match`），不做姓名猜测；
- 操作人记「AI 自动导入」，不写脚本名、不写抓取地址。

用法：
    py -3.12 -u scripts/zhixue/import_to_timed.py            # 预览
    py -3.12 -u scripts/zhixue/import_to_timed.py --apply    # 写库
    py -3.12 -u scripts/zhixue/import_to_timed.py --exam 20261016 --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import create_app                                   # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models.grades import DEFAULT_FULL_MARKS, SUBJECTS     # noqa: E402
from app.models.timed import TimedExam                        # noqa: E402
from app.modules.grades.services import (                      # noqa: E402
    timed_analysis, timed_match, timed_service)
from app.modules.grades.utils import term_of_date              # noqa: E402

DATA = ROOT / 'data' / 'zhixue' / 'timed'
MAP_FILE = ROOT / 'data' / 'zhixue' / '学生对应.txt'
SUBJ_MAP = {'生物学': '生物', '英语': '外语'}
OPERATOR_TEXT = 'AI 自动导入'


def load_code_map():
    """对应表 → {智学网考号: 学号}（每场考试的考号可能不同，靠它转学号）"""
    out = {}
    if not MAP_FILE.exists():
        return out
    with open(MAP_FILE, encoding='utf-8-sig') as fh:
        for line in fh:
            parts = [p.strip() for p in line.rstrip('\n').split('\t')]
            if len(parts) >= 2 and parts[0] and parts[1]:
                out[parts[1]] = parts[0]
    return out


def norm_class(name):
    """「高三06班」→「06班」（限时练表里 class_name 不带年级前缀）"""
    import re
    return re.sub(r'^高[一二三]?\s*[年级]*\s*', '', (name or '').strip())


def parse_score(value):
    """分数文本 → float；缺考（- / -- / 空）→ None"""
    s = str(value or '').strip().replace('分', '')
    if s in ('', '-', '--', '—', '−', '－', '/', '\\'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def subject_rows(data, source_subject, system_subject, code_map):
    """把一场抓取结果的某科展开成匹配行（含满分校验）

    考号先经对应表换成学号：同一学生在不同场次的考号可能不同，
    只用 zhixue_no 会整科匹配不上（2026-10-20 实测）。
    """
    full = DEFAULT_FULL_MARKS.get(system_subject, 100)
    rows, bad = [], 0
    for i, r in enumerate(data.get('rows') or [], start=1):
        score = parse_score((r.get('scores') or {}).get(source_subject, {})
                            .get('分数'))
        if score is None:
            continue
        if score < 0 or score > full + 0.5:
            bad += 1
            continue
        code = str(r.get('exam_no') or '').strip()
        rows.append({'line': i, 'zhixue_no': code,
                     'student_no': code_map.get(code, ''),
                     'name': r.get('name') or '',
                     'class_name': norm_class(r.get('class_name')),
                     'score': score})
    return rows, bad


def find_exam(grade, exam_date, raw_name, used_ids):
    """找已有场次：同名 > 同日唯一；同一天有多场时不再复用

    used_ids 记录本次已认领的场次：智学网同一天可能有两场限时练
    （如「第一次/第二次限时作业」），若都往同一个场次写，后一场会
    把前一场整科覆盖掉。
    """
    same_day = TimedExam.query.filter_by(grade=grade,
                                         exam_date=exam_date).all()
    if not same_day:
        return None
    for e in same_day:
        if (e.name or '') == raw_name or raw_name in (e.name or ''):
            return e
    fresh = [e for e in same_day if e.id not in used_ids]
    return fresh[0] if len(fresh) == 1 else None


def get_or_create(grade, exam_date, raw_name, apply, used_ids):
    """按「年级 + 名称」取或建场次（名称 = 年级+日期+原名）"""
    name = f'{grade}{exam_date:%Y%m%d}{raw_name}'
    exam = find_exam(grade, exam_date, raw_name, used_ids)
    if exam:
        used_ids.add(exam.id)
        return exam, False
    exam = TimedExam(grade=grade, name=name, exam_date=exam_date,
                     term=term_of_date(exam_date), grade_semester='',
                     remark=OPERATOR_TEXT, operator_id=None)
    exam.template_id = timed_service.ensure_builtin_templates().id
    if apply:
        db.session.add(exam)
        db.session.flush()
        used_ids.add(exam.id)
    return exam, True


def import_one(data, apply, code_map, used_ids):
    """导入一场（多科），返回统计字典"""
    grade = data['grade']
    exam_date = date.fromisoformat(data['date'])
    exam, created = get_or_create(grade, exam_date, data['name'], apply,
                                  used_ids)
    stats = {'exam_id': exam.id if apply else 0, 'created': created,
             'subjects': {}}
    for src in data.get('subjects') or []:
        sysname = SUBJ_MAP.get(src, src)
        if sysname not in SUBJECTS:
            continue
        rows, bad = subject_rows(data, src, sysname, code_map)
        matched, errors, mstats = timed_match.match_students(rows, grade)
        stats['subjects'][sysname] = {
            'raw': len(rows), 'matched': len(matched),
            'unmatched': mstats.get('unmatched', 0), 'bad': bad}
        if not apply or not matched:
            continue
        timed_service.delete_scores_of(exam.id, sysname)
        db.session.flush()
        timed_analysis.save_subject(exam, sysname, matched)
    if apply:
        exam.status = 'imported' if stats['subjects'] else exam.status
        db.session.commit()
        # 场次名统一成「2026级 No.1」（届内按日期连续编号）
        if timed_service.renumber_grade(grade):
            db.session.commit()
    return stats


def iter_files(pattern=''):
    files = sorted(DATA.glob('*.json'))
    return [f for f in files
            if f.name not in ('index.json', 'plan.json')
            and (not pattern or pattern in f.name)]


def main():
    ap = argparse.ArgumentParser(description='智学网限时练成绩 → StuLink 限时练')
    ap.add_argument('--exam', default='', help='只导入文件名含该关键字的场次')
    ap.add_argument('--apply', action='store_true', help='真正写库（默认预览）')
    args = ap.parse_args()

    files = iter_files(args.exam)
    if not files:
        raise SystemExit(f'{DATA} 下没有可导入的 JSON')
    code_map = load_code_map()
    print(f'考号对应表：{len(code_map)} 条')
    app = create_app()
    with app.app_context():
        total = {'exams': 0, 'new': 0, 'subjects': 0, 'matched': 0,
                 'unmatched': 0}
        used_ids = set()
        for f in files:
            data = json.loads(f.read_text(encoding='utf-8'))
            stats = import_one(data, args.apply, code_map, used_ids)
            total['exams'] += 1
            total['new'] += 1 if stats['created'] else 0
            total['subjects'] += len(stats['subjects'])
            for subj, s in stats['subjects'].items():
                total['matched'] += s['matched']
                total['unmatched'] += s['unmatched']
                print(f"  {data['grade']} {data['date']} {data['name']}"
                      f" · {subj}: {s['matched']}/{s['raw']} 人"
                      f"（未匹配 {s['unmatched']}，越界 {s['bad']}）")
            print(f"[{'已导入' if args.apply else '预览'}] {f.name}"
                  f" → 场次 {stats['exam_id']}"
                  f"{'（新建）' if stats['created'] else ''}")
        print(f"\n合计：{total['exams']} 场（新建 {total['new']}）"
              f" / 科目 {total['subjects']} / 匹配 {total['matched']} 行"
              f" / 未匹配 {total['unmatched']}")
        if not args.apply:
            print('[dry-run] 未写库；确认后加 --apply')


if __name__ == '__main__':
    main()

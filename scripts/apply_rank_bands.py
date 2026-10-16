# -*- coding: utf-8 -*-
# StuLink v1.19.50.35 2026-10-15
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""按「名次档」批量划层：建一个名次分层模板，并把它套用到限时练考试。

名次档取自智学网报表「XX 名次内 上线人数」的列头：
    10 / 20 / 50 / 80 / 100 / 150 / 200 / 250 / 300 / 400 / 500 / 550

划线口径复用系统自身的 `_save_bands`（与页面「划线分层」保存完全同一条路径）：
`lower_mode='rank'` 时把「第 N 名」换算成「第 N 名的分数」再落 exam_bands
（统一存 score，与手工划线结果同构，分析链路无需改动）。

默认 **dry-run**（只预览每档换算出的分数、参考人数、跳过项）；确认无误再加 `--apply`。

用法：
    python scripts/apply_rank_bands.py                    # 预览全部限时练考试
    python scripts/apply_rank_bands.py --exam-id 26       # 只看一场
    python scripts/apply_rank_bands.py --apply            # 真正建模板 + 划线
"""
from __future__ import annotations

import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

from app import create_app                                        # noqa: E402
from app.extensions import db                                     # noqa: E402
from app.models.grades import (BandTemplate, Exam, SUBJECTS,      # noqa: E402
                               TOTAL_SUBJECT)
# 复用页面「划线分层」的写入函数：保证名次→分数换算、覆盖式写入、科目白名单
# 等口径与手工操作完全一致（自行实现会漂移）
from app.modules.grades.routes.bands import _save_bands            # noqa: E402
from app.modules.grades.services import stats_service as st        # noqa: E402

DEFAULT_RANKS = [10, 20, 50, 80, 100, 150, 200, 250, 300, 400, 500, 550]
DEFAULT_TPL_NAME = '限时练名次分层'


def layer_name(rank):
    return f'前{rank}'


def probe_scores(scores, rank):
    """本地预览：第 rank 名（1 起）的分数；人数不足取最后一名（与 _rank_to_score 同口径）"""
    desc = sorted(scores, reverse=True)
    if not desc:
        return None
    r = max(int(rank), 1)
    return desc[min(r - 1, len(desc) - 1)]


def ensure_template(name, ranks, exam_type, dry):
    tpl = BandTemplate.query.filter_by(name=name).first()
    layers = [{'name': layer_name(r), 'ratio': r} for r in ranks]
    if dry:
        print(f'[dry-run] 模板「{name}」{"将更新" if tpl else "将新建"}：'
              f'下界方式=名次，适用考试类型={exam_type}，'
              f'层={ "、".join(x["name"] for x in layers) }')
        return tpl
    if not tpl:
        mx = db.session.query(db.func.max(BandTemplate.sort_order)).scalar() or 0
        tpl = BandTemplate(name=name, sort_order=mx + 1, is_builtin=False)
    tpl.lower_mode = 'rank'
    tpl.set_layers(layers)
    tpl.set_exam_types([exam_type])
    tpl.remark = f'名次分层（{ "、".join(x["name"] for x in layers) }）'
    db.session.add(tpl)
    db.session.commit()
    print(f'模板已{"更新" if tpl.id else "新建"}：id={tpl.id} 「{tpl.name}」'
          f'（lower_mode=rank，{len(layers)} 层）')
    return tpl


def main():
    ap = argparse.ArgumentParser(description='名次分层模板：建模板 + 批量套用到限时练考试')
    ap.add_argument('--template-name', default=DEFAULT_TPL_NAME)
    ap.add_argument('--exam-type', default='限时练', help='适用考试阶段（默认限时练）')
    ap.add_argument('--exam-id', type=int, help='只处理这一场')
    ap.add_argument('--ranks', default=','.join(str(r) for r in DEFAULT_RANKS),
                    help='名次档，逗号分隔')
    ap.add_argument('--no-total', action='store_true', help='不给总分划线')
    ap.add_argument('--apply', action='store_true', help='真正写库')
    args = ap.parse_args()

    try:
        ranks = [int(x) for x in args.ranks.replace('，', ',').split(',') if x.strip()]
    except ValueError:
        raise SystemExit('--ranks 只能是逗号分隔的整数')
    if sorted(ranks) != ranks or len(set(ranks)) != len(ranks):
        raise SystemExit('--ranks 必须严格递增且不重复（seq 1 为最高层）')

    app = create_app()
    with app.app_context():
        tpl = ensure_template(args.template_name, ranks, args.exam_type, not args.apply)

        q = Exam.query
        if args.exam_id:
            q = q.filter(Exam.id == args.exam_id)
        else:
            q = q.filter(Exam.exam_type == args.exam_type)
        exams = q.order_by(Exam.exam_date, Exam.id).all()
        if not exams:
            raise SystemExit('没有匹配的考试')
        print(f'\n待划线 {len(exams)} 场（mode=rank，层数 {len(ranks)}）')

        total_ok, total_skip = 0, 0
        for exam in exams:
            edata = st.ExamData(exam.id)
            directions = edata.directions or ['']
            print(f'\n=== id={exam.id} {exam.name}（{exam.exam_date}）'
                  f' 方向={directions} ===')
            combos = []
            for d in directions:
                subs = [s for s in SUBJECTS
                        if edata.scores_of_subject(s, direction=d or None)]
                pool = ([TOTAL_SUBJECT] if not args.no_total else []) + subs
                for sub in pool:
                    combos.append((d, sub))
            if not args.apply:
                for d, sub in combos:
                    scores = ([t['score'] for t in edata.totals_of(direction=d or None)]
                              if sub == TOTAL_SUBJECT
                              else list(edata.scores_of_subject(sub, direction=d or None)))
                    preview = ' '.join(
                        f'{layer_name(r)}={probe_scores(scores, r)}' for r in ranks[:5])
                    print(f'  [dry-run] {(d or "全体")}·{sub}：{len(scores)} 人'
                          f'（{preview} …）')
                continue
            for d, sub in combos:
                payload = [{'seq': i + 1, 'name': layer_name(r),
                            'lower_mode': 'rank', 'lower_value': r}
                           for i, r in enumerate(ranks)]
                items, err = _save_bands(exam.id, d, sub, payload)
                if err:
                    total_skip += 1
                    print(f'  跳过 {(d or "全体")}·{sub}：{err}')
                    continue
                total_ok += 1
                lo = min(x['lower_value'] for x in items)
                hi = max(x['lower_value'] for x in items)
                print(f'  写入 {(d or "全体")}·{sub}：{len(items)} 层，'
                      f'分数线 {hi} ~ {lo}')
            exam.set_band_template(tpl.id, tpl.name)
            db.session.add(exam)
            db.session.commit()

        if args.apply:
            print(f'\n完成：写入 {total_ok} 个（方向×学科）组合，跳过 {total_skip} 个；'
                  f'{len(exams)} 场考试已绑定模板「{tpl.name}」')
        else:
            print('\n[dry-run] 未写库。确认无误后加 --apply。')


if __name__ == '__main__':
    main()

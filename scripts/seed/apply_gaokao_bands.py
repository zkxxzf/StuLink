# -*- coding: utf-8 -*-
# StuLink v1.19.58.64 2026-10-15
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""按「特控线 + 本科线」给考试划线（2026-10-15 用户口径，当日三次定案）。

口径（用户 2026-10-15 三次定案「我们整的有点复杂了……各个学科根据方向去按照我们的比例
50%、90% 去算，只是总分是分组合来看的」）：
- **总分**：按**选科组合**（exam_bands.selection 维度）—— 物化生 / 物化地 / 物化政 各自
  特控线率 50%；史地政特控线取**前 30 名**；**本科线一律 90%**（参考人群 = 本组合学生）；
- **其余各科（语文 / 数学 / 外语 / 物理 / 历史 / 化学 / 生物 / 政治 / 地理）**：一律按**方向**
  —— 物理方向特控线率 50%、历史方向特控线取前 30 名，本科线均 90%；
- 参考人数 < 5 的（方向/组合 × 学科）跳过（脏数据护栏），旧线一并清除。

划线写入复用页面同一条路径 `bands._save_bands`（ratio/rank 统一换算成分数落库，
覆盖式：只覆盖同 方向×组合×学科 旧线，四层旧线会被本口径替换——页面可用
「四层」模板随时重划回来）。绑定模板「特控线+本科线」供各报表层名/列使用。

默认 dry-run（只预览换算分数与参考人数）；确认后加 --apply 落库。

用法：
    python scripts/seed/apply_gaokao_bands.py                # 预览 exam 23
    python scripts/seed/apply_gaokao_bands.py --exam-id 23   # 指定考试
    python scripts/seed/apply_gaokao_bands.py --apply        # 真正划线
"""
from __future__ import annotations
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import argparse
import os
import sys

BASE = _ROOT
sys.path.insert(0, BASE)

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:
    pass

from app import create_app                                        # noqa: E402
from app.extensions import db                                     # noqa: E402
from app.models.grades import (BandTemplate, Exam, SUBJECTS,      # noqa: E402
                               TOTAL_SUBJECT)

# 复用页面「划线分层」的写入与换算：口径与手工操作完全一致
# （COMBO_SUBJECTS 是页面列开关的科目白名单；本脚本三次口径下不按组合划单科，
#   故不直接导入 —— 若日后恢复「组合级单科线」，把科目填进 SUBJECT_BY_SELECTION 即可）
from app.modules.grades.routes.bands import (_rank_to_score,      # noqa: E402
                                             _ratio_to_score,
                                             _save_bands,
                                             _scope_scores)
from app.modules.grades.services import stats_service as st       # noqa: E402
from app.modules.grades.utils import invalidate_exam_cache        # noqa: E402

TPL_NAME = '特控线+本科线'
PHYS_TEKONG = [('特控线', 'ratio', 50.0), ('本科线', 'ratio', 90.0)]
HIST_TEKONG = [('特控线', 'rank', 30.0), ('本科线', 'ratio', 90.0)]
# 组合 → 该组合的层线方案（物理类组合 50%/90%；史地政 前30/90%）
SEL_LAYERS = {
    '物化生': PHYS_TEKONG, '物化地': PHYS_TEKONG, '物化政': PHYS_TEKONG,
    '史地政': HIST_TEKONG,
}
PLAN = {
    # 组合总分线（selection 维度；direction 按该组合学生所属方向自动判定）
    'total_by_selection': dict(SEL_LAYERS),
    # 方向总分线：**不划**（2026-10-15 二次口径：总分一律按组合，史地政=历史类唯一组合）
    'total_by_direction': {},
    # 方向单科线：语文/数学/外语 + 首选科（物理/历史）
    'subject_by_direction': {
        '物理': PHYS_TEKONG,
        '历史': HIST_TEKONG,
    },
}
# 2026-10-15 三次口径：**单科一律按方向**，不再按组合（脚本层面）
# —— 页面仍保留「按组合」列开关（bands.COMBO_SUBJECTS 白名单），需要时手动勾列即可。
SUBJECT_BY_SELECTION = ()
MIN_N = 5          # 参考人数低于此的组合/科目跳过（脏数据护栏）
# 这些方向的总分**不划整体线**（由 total_by_selection 的组合线代替）：
# 把该方向总分旧的方向线（selection=''）删掉，否则层序列回落会混入旧层名
DROP_TOTAL_DIRECTION = ['物理', '历史']


def payload_of(layers):
    """[(层名, 模式, 值)] → _save_bands 的 payload（seq=1 最高层在前）"""
    return [{'seq': i + 1, 'name': n, 'lower_mode': m, 'lower_value': v}
            for i, (n, m, v) in enumerate(layers)]


def preview_line(data, direction, subject, layers, selection=''):
    """预览该（方向×[组合]×学科）各层换算分数线"""
    out = []
    for name, mode, val in layers:
        if mode == 'ratio':
            score = _ratio_to_score(data, direction, subject, val, selection)
            src = f'前{val:g}%'
        else:
            score = _rank_to_score(data, direction, subject, val, selection)
            src = f'第{int(val)}名'
        out.append(f'{name}({src})={score:g}')
    return ' / '.join(out)


def ensure_template(dry):
    layers = [{'name': '特控线', 'ratio': 50}, {'name': '本科线', 'ratio': 90}]
    tpl = BandTemplate.query.filter_by(name=TPL_NAME).first()
    if dry:
        print(f'[dry-run] 模板「{TPL_NAME}」{"将更新" if tpl else "将新建"}'
              f'：特控线(50%) + 本科线(90%)')
        return tpl
    if not tpl:
        mx = db.session.query(db.func.max(BandTemplate.sort_order)).scalar() or 0
        import json
        tpl = BandTemplate(name=TPL_NAME, sort_order=mx + 1, is_builtin=False,
                           lower_mode='ratio',
                           layers_json=json.dumps(layers, ensure_ascii=False))
        db.session.add(tpl)
        db.session.flush()
    else:
        import json
        tpl.lower_mode = 'ratio'
        tpl.layers_json = json.dumps(layers, ensure_ascii=False)
        db.session.add(tpl)
    return tpl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exam-id', type=int, default=23)
    ap.add_argument('--apply', action='store_true', help='真正落库（默认 dry-run）')
    args = ap.parse_args()
    dry = not args.apply

    app = create_app()
    with app.app_context():
        exam = Exam.query.get(args.exam_id)
        if not exam:
            print(f'考试 {args.exam_id} 不存在')
            sys.exit(1)
        data = st.ExamData(args.exam_id)
        if not data.total_rows:
            print(f'{exam.name} 暂无成绩，先导入成绩')
            sys.exit(1)
        print(f'== {exam.name}（exam {exam.id}）{"dry-run" if dry else "APPLY"} ==')
        directions = data.directions or ['']

        # 组合 → 方向（按该组合学生的方向）
        sel_dir = {}
        for t in data.totals_of():
            sel = t.get('selection') or ''
            if sel:
                sel_dir.setdefault(sel, t['direction'] or '')
        print('组合→方向：', {k: v for k, v in sorted(sel_dir.items())})

        jobs = []           # (label, direction, subject, layers, selection)
        prune = []          # (direction, subject, selection)：参考不足被跳过 → apply 时清旧线
        for sel, layers in PLAN['total_by_selection'].items():
            d = sel_dir.get(sel)
            if not d:
                print(f'[跳过] 总分×{sel}：本场无该组合学生')
                continue
            jobs.append((f'总分·{sel}', d, TOTAL_SUBJECT, layers, sel))
        for d, layers in PLAN['total_by_direction'].items():
            if d not in directions:
                print(f'[跳过] 总分×{d}：本场无该方向')
                continue
            jobs.append((f'总分·{d}', d, TOTAL_SUBJECT, layers, ''))
        # 单科：**全部按方向**（语数外 / 首选科 / 再选四科；2026-10-15 三次口径）
        for d, layers in PLAN['subject_by_direction'].items():
            if d not in directions:
                continue
            for sub in SUBJECTS:
                if sub in SUBJECT_BY_SELECTION:
                    continue            # 预留：日后要按组合划的科目放这里
                rows = data.scores_of_subject(sub, direction=d)
                if not rows:
                    continue
                if len(rows) < MIN_N:
                    print(f'[跳过] {d}·{sub}：参考仅 {len(rows)} 人（<{MIN_N}，脏数据护栏）'
                          f'；旧线将一并清除（避免页面层名混入旧层）')
                    prune.append((d, sub, ''))
                    continue
                jobs.append((f'{d}·{sub}', d, sub, layers, ''))
        # （2026-10-15 三次口径：单科不再按组合划 → 此处原「按组合的再选四科」已移除。
        #   SUBJECT_BY_SELECTION 留空即关；若日后要恢复，把科目填回该常量、并加回这段循环。）

        print(f'\n共 {len(jobs)} 个（方向/组合 × 学科）组合待划线：')
        for label, d, sub, layers, sel in jobs:
            if sub == TOTAL_SUBJECT:
                n = (sum(1 for t in data.totals_of(direction=d)
                         if t.get('selection') == sel) if sel
                     else len(data.totals_of(direction=d)))
            else:
                n = len(_scope_scores(data, d, sub, sel))
            print(f'  {label}（{n}人）：{preview_line(data, d, sub, layers, sel)}')

        if dry:
            print('\n[dry-run] 未写库。确认无误后加 --apply。')
            return

        # 第七轮审计（S4）：--apply 含三处无条件删除（方向总分旧线、组合级单科线、
        # 参考人数不足的旧线），原实现**无任何备份** → 先整库备份 grades.db
        # （与 import_bands_20260712.py 同一做法；.bak_* 已被 .gitignore 覆盖）。
        import shutil
        from datetime import datetime as _dt
        _db = os.path.join(BASE, 'data', 'grades.db')
        _bak = f'{_db}.bak_applybands_{_dt.now().strftime("%Y%m%d_%H%M%S")}'
        shutil.copy2(_db, _bak)
        print(f'已备份 grades.db → {os.path.basename(_bak)}')

        tpl = ensure_template(dry)
        # 删除被组合线代替的方向总分旧线（层序列统一走组合线）
        from app.models.grades import ExamBand
        for d in DROP_TOTAL_DIRECTION:
            if d in directions:
                ExamBand.query.filter_by(exam_id=exam.id, direction=d,
                                         subject=TOTAL_SUBJECT,
                                         selection='').delete()
                print(f'已删除 {d}·总分 旧方向线（由组合线代替）')
        # 2026-10-15 三次口径：单科一律按方向 → 清掉此前生成的**组合级单科线**
        # （selection 非空、科目非总分），否则判层「组合优先」会让这些科仍走组合线
        n_drop = (ExamBand.query
                  .filter(ExamBand.exam_id == exam.id,
                          ExamBand.subject != TOTAL_SUBJECT,
                          ExamBand.selection != '')
                  .delete(synchronize_session=False))
        print(f'已删除 组合级单科线 {n_drop} 行（单科改按方向）')
        # 参考人数不足被跳过的组合：旧线（如残留的四层线）一并清除，
        # 否则划线页会把旧层名当「模板外层」追加出来
        for d, sub, sel in prune:
            ExamBand.query.filter_by(exam_id=exam.id, direction=d, subject=sub,
                                     selection=sel).delete()
            print(f'已清除 {d}·{sel or "方向"}·{sub} 旧线（参考人数不足，不保留）')
        applied = []
        # 审计 W8-a：复用同一份 ExamData（原实现每科都重建一次全量明细），
        # 并按「每 8 个 job 提交一次」分批，缩短 SQLite 写锁持有窗口
        data_w = st.ExamData(exam.id)
        for i, (label, d, sub, layers, sel) in enumerate(jobs, 1):
            items, err = _save_bands(exam.id, d, sub, payload_of(layers), sel,
                                     data=data_w)
            if err:
                db.session.rollback()
                print(f'[失败] {label}：{err}')
                sys.exit(1)
            applied.append(label)
            if i % 8 == 0:
                db.session.commit()
        if tpl:
            exam.set_band_template(tpl.id, tpl.name)
            db.session.add(exam)
        db.session.commit()
        invalidate_exam_cache(exam.id)
        print(f'\n已写入 {len(applied)} 个组合 + 绑定模板「{TPL_NAME}」')
        # 第七轮审计（C-M1）：登记迁移（幂等；登记失败不阻塞）
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import _mig_log                                   # noqa: E402
            _mig_log.record(os.path.basename(__file__),
                            note=f'exam {exam.id} 按口径重划（组合总分线 + 方向单科线）')
        except Exception as _e:  # noqa: BLE001
            print('[WARN] 迁移登记失败（不影响结果）：', _e)

        # ---- 实算复核（用页面同一条 band_list/band_of_score 链路）----
        print('\n== 实算复核（总分各线特控/本科上线）==')
        data2 = st.ExamData(args.exam_id)
        for sel in list(PLAN['total_by_selection']) + ['历史方向']:
            if sel == '历史方向':
                d, sel_key = '历史', ''
                tot = data2.totals_of(direction=d)
            else:
                d = sel_dir.get(sel, '物理')
                sel_key = sel
                tot = [t for t in data2.totals_of(direction=d)
                       if t.get('selection') == sel]
            bands = data2.band_list(d, TOTAL_SUBJECT, sel_key)
            if not bands:
                print(f'  {sel}：无划线')
                continue
            scores = [t['score'] for t in tot if t['score'] is not None]
            detail = '、'.join(
                f'{name}上线={sum(1 for s in scores if s >= lower)}({lower:g})'
                for name, lower in bands)
            print(f'  {sel}（{len(scores)}人）：{detail}')

        # 方向级科目复核（2026-10-15 三次口径：**全部单科**都按方向）
        print('\n== 实算复核（按方向：语数外 + 首选科 + 再选四科）==')
        for d in directions:
            for sub in SUBJECTS:
                if sub in SUBJECT_BY_SELECTION:
                    continue
                rows = data2.scores_of_subject(sub, direction=d)
                bands = data2.band_list(d, sub)
                if not rows or not bands:
                    continue
                detail = '、'.join(
                    f'{name}上线={sum(1 for s in rows if s >= lower)}({lower:g})'
                    for name, lower in bands)
                print(f'  {d}·{sub}（{len(rows)}人）：{detail}')


if __name__ == '__main__':
    main()

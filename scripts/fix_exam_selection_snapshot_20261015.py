# -*- coding: utf-8 -*-
"""修正「考试选科快照」与「实考科目」不符的行（2026-10-15）

背景：`exam_scores.subject_selection / direction` 是**导入时的学生名册快照**。
学生转科后（例：01班 高紫铭 20251242、曹熙越 20252309 由物化生转入史地政），
如果导入早于名册更新，快照会停在旧选科，后果：
  - 班级分层统计表出现不该有的方向行（01班 出现「物理」+1/+1 两行）；
  - 方向排名、上线判定按错误方向的线走（他们实考的是史地政科目）。

判据（只认硬证据，避免误改）：
  1. 按场聚合该生实考科目：只出现 史/政/地 → 历史方向；只出现 物/化/生 → 物理方向；
     两侧都有（如物化地/物化政，或分班前九科统考）→ 跳过；
  2. 名册选科推出的方向必须与实考科目一致（名册与实考也矛盾的 → 只报告，交人工判断，
     常见于「考试时还在别的班」）；
  3. 快照与名册都已一致 → 跳过（幂等）。

动作：把命中行刷成名册的 `subject_selection` + 对应 `direction`，然后重算受影响场次的排名。
改动前把受影响行导出到 `data/backups/fix_exam_sel_20261015/` 便于回滚
（2026-10-16 第七轮审计 S8：原路径在仓库根 `backups/`，未被 .gitignore 覆盖，
备份里含学生姓名/学号/分数 —— 已迁到 `data/` 下并把「先备份后写库」顺序倒正）。

用法：
  py -3.12 scripts/fix_exam_selection_snapshot_20261015.py           # dry-run 预览
  py -3.12 scripts/fix_exam_selection_snapshot_20261015.py --apply    # 落库 + 重算排名
"""
import argparse
import json
import os
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from sqlalchemy import text  # noqa: E402

PHY_SUBS = {'物理', '化学', '生物'}
HIS_SUBS = {'历史', '政治', '地理'}


def _dir_of(sel):
    sel = (sel or '').strip()
    if sel.startswith('物'):
        return '物理'
    if sel.startswith('史'):
        return '历史'
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='落库并重算排名（默认 dry-run）')
    ap.add_argument('--exam', type=int, default=0, help='只处理指定考试 id')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    from app import create_app
    from app.modules.grades.services import ranking
    app = create_app()

    with app.app_context():
        from app.models.student import Student
        from app.extensions import db

        roster = {s.student_number: (s.name, s.class_name,
                                    (s.subject_selection or '').strip())
                  for s in db.session.query(Student).all()}
        eng = db.engines['grades']
        with eng.connect() as conn:
            sql = ("SELECT id, name FROM exams WHERE status='imported'")
            params = {}
            if args.exam:
                sql += ' AND id=:e'
                params['e'] = args.exam
            exams = {r[0]: r[1] for r in conn.execute(text(sql + ' ORDER BY id'), params).fetchall()}
            if not exams:
                print('没有 imported 考试，退出')
                return
            ids = tuple(exams)
            placeholders = ','.join(':e%d' % i for i in range(len(ids)))
            params2 = {'e%d' % i: eid for i, eid in enumerate(ids)}
            rows = conn.execute(text(
                "SELECT exam_id, student_no, student_name, class_name, direction, "
                "subject_selection, GROUP_CONCAT(DISTINCT subject) subs "
                "FROM exam_scores WHERE exam_id IN (%s) AND subject!='总分' "
                "GROUP BY exam_id, student_no ORDER BY exam_id, student_no"
                % placeholders), params2).fetchall()

        plan, manual = [], []
        for eid, no, nm, cls, dr, snap_sel, subs in rows:
            sub_set = set((subs or '').split(',')) - {'总分'}
            has_phy, has_his = bool(sub_set & PHY_SUBS), bool(sub_set & HIS_SUBS)
            if has_phy == has_his:        # 都有（物化地/物化政、分班前统考）或都没有 → 跳过
                continue
            exp_dir = '历史' if has_his else '物理'
            r = roster.get(no)
            rsel = r[2] if r else ''
            rdir = _dir_of(rsel)
            if rdir != exp_dir:           # 名册与实考也矛盾
                if (dr or '') == exp_dir:
                    continue              # 但快照与实考一致 → 正常的「考试当时」快照，只是名册后来变了
                manual.append((eid, no, nm, cls, dr, snap_sel or '', exp_dir, rsel, sorted(sub_set)))
                continue
            if (dr or '') == exp_dir and (snap_sel or '').strip() == rsel:
                continue                  # 已一致
            plan.append({'exam_id': eid, 'exam_name': exams[eid], 'student_no': no,
                         'student_name': nm, 'class_name': cls, 'old_dir': dr or '',
                         'old_sel': snap_sel or '', 'new_dir': exp_dir, 'new_sel': rsel,
                         'subjects': sorted(sub_set)})

        print(f'考试 {len(exams)} 场，检查 {len(rows)} 条学生场次记录')
        print(f'→ 可自动修正 {len(plan)} 条；名册与实考矛盾（需人工）{len(manual)} 条\n')
        for p in plan:
            print(f"  [改] exam {p['exam_id']:3d} {p['exam_name'][:22]:22s} "
                  f"{p['student_no']} {p['student_name']} {p['class_name']}："
                  f"{p['old_dir'] or '(空)'}·{p['old_sel'] or '(空)'} → "
                  f"{p['new_dir']}·{p['new_sel']}  （实考：{'/'.join(p['subjects'])}）")
        if manual:
            stat = defaultdict(int)
            for m in manual:
                stat[m[0]] += 1
            print('  [手工]（快照、实考、名册三者不一致，需人工判断；按场统计）')
            for eid in sorted(stat):
                print(f'    exam {eid:3d} {exams[eid][:24]:24s} {stat[eid]:3d} 条')
            print('  明细：')
            for eid, no, nm, cls, dr, snap, exp, rsel, subs in manual[:30]:
                print(f"    exam {eid:3d} {no} {nm} {cls}：快照={dr or '(空)'}·{snap or '(空)'} "
                      f"实考方向={exp} 名册={rsel or '(空)'}（实考：{'/'.join(subs)}）")
            if len(manual) > 30:
                print(f'    … 共 {len(manual)} 条，仅列前 30')

        if not plan:
            print('\n无需修正')
            return
        if not args.apply:
            print('\n（dry-run，未落库；确认无误后加 --apply）')
            return

        # ---- 落库 ----
        # 第七轮审计（S8）：备份先落盘再 UPDATE（原实现先 commit 再写 JSON，
        # JSON 失败即「已改库且无备份」）；备份目录移到 data/backups（data/ 整体
        # 在 .gitignore 内，仓库根 backups/ 曾可能被 git add 收录）。
        outdir = os.path.join(BASE, 'data', 'backups', 'fix_exam_sel_20261015')
        os.makedirs(outdir, exist_ok=True)
        backup = []
        bpath = os.path.join(outdir, 'exam_scores_backup.json')
        with eng.begin() as conn:
            for p in plan:
                old_rows = conn.execute(text(
                    "SELECT id, exam_id, student_no, student_name, class_name, subject, "
                    "subject_selection, direction, score FROM exam_scores "
                    "WHERE exam_id=:e AND student_no=:n"),
                    {'e': p['exam_id'], 'n': p['student_no']}).mappings().all()
                backup.extend([dict(r) for r in old_rows])
            # 备份先落盘（仍在同一事务内、尚未提交）：写盘失败 → 事务回滚，库不动
            with open(bpath, 'w', encoding='utf-8') as f:
                json.dump(backup, f, ensure_ascii=False, indent=1)
            for p in plan:
                conn.execute(text(
                    "UPDATE exam_scores SET subject_selection=:sel, direction=:d "
                    "WHERE exam_id=:e AND student_no=:n"),
                    {'sel': p['new_sel'], 'd': p['new_dir'],
                     'e': p['exam_id'], 'n': p['student_no']})
        print(f'\n已备份 {len(backup)} 行 → {bpath}')

        # ---- 重算受影响场次排名 ----
        from app.models.grades import Exam
        for eid in sorted({p['exam_id'] for p in plan}):
            n = ranking.recalc_exam(eid)
            ex = Exam.query.get(eid)
            if ex is not None:
                ex.status = 'imported'
            db.session.commit()
            print(f'  已重算 exam {eid}（{exams[eid][:22]}）：{n} 行排名')
        # 第七轮审计（C-M1）：登记迁移（幂等；登记失败不阻塞）
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import _mig_log                                  # noqa: E402
            _mig_log.record(os.path.basename(__file__),
                            note=f'修选科快照 {len(plan)} 条并重算排名（--apply）')
        except Exception as _e:  # noqa: BLE001
            print('[WARN] 迁移登记失败（不影响结果）：', _e)
        print('\n完成')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""录入「2025级20260712期末考（高一下）」的纯分数划线（用户 2026-10-16 提供的官方线表）。

背景：exam 33（2025级 20260712 期末）此前**一条划线都没有**，导致汇总打印版的
「进退步」（与上一场同层上线人数之差）整列算不出来。用户给出该场的纯分数划线，
本脚本按 exam_bands 现有格式（direction/subject/name/lower_mode='score'/lower_value）录入。

数据来源（用户图，纯分数）：

    物理类  特控: 总赋484 语文101 数学87 外语98 物理41 化赋80 生赋83 政赋86 地赋87
            本科: 总赋385 语文89  数学58 外语67 物理22 化赋62 生赋67 政赋75 地赋75
    历史类  特控: 总赋537.5 语文108 数学85 外语111.5 历史72 政赋90 地赋90
            本科: 总赋455.5 语文96  数学55 外语82.5  历史59 政赋81 地赋81

用法：
    py -3.12 scripts/import_bands_20260712.py            # 只看将要写入什么（dry-run）
    py -3.12 scripts/import_bands_20260712.py --apply    # 实际写入

2026-10-16 第七轮审计（C-H1/L5）修正与补充：
- 幂等口径 ＝ **整场先删后插**（`delete from exam_bands where exam_id=?`）——
  会一并清掉该场旧的方向线/组合线；如该场还有组合级总分线（apply_gaokao_bands
  生成），重跑 `scripts/apply_gaokao_bands.py --exam-id <本场> --apply` 即可补回；
- 已登记进 `docs/数据库迁移清单.md`（B 组 #37）与 `deploy/nas_migrate_pending.sh`；
  跑完在本函数末尾调 `_mig_log` 登记（幂等，登记失败不阻塞）。
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, 'data', 'grades.db')
EXAM_HINT = '20260712'          # 用来定位那一场（名字里含该日期）
BAND_TC, BAND_BK = '特控线', '本科线'

# 用户给的线表：方向 -> [(科目, 特控, 本科)]
LINES = {
    '物理': [('总分', 484, 385), ('语文', 101, 89), ('数学', 87, 58), ('外语', 98, 67),
             ('物理', 41, 22), ('化学', 80, 62), ('生物', 83, 67), ('政治', 86, 75),
             ('地理', 87, 75)],
    '历史': [('总分', 537.5, 455.5), ('语文', 108, 96), ('数学', 85, 55),
             ('外语', 111.5, 82.5), ('历史', 72, 59), ('政治', 90, 81), ('地理', 90, 81)],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际写入（默认只预览）')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    row = cur.execute('select id, name, grade, exam_date from exams where name like ?',
                      (f'%{EXAM_HINT}%',)).fetchone()
    if row is None:
        print(f'找不到名字含 {EXAM_HINT} 的考试，中止。')
        return 1
    eid = row['id']
    old = cur.execute('select count(*) from exam_bands where exam_id=?', (eid,)).fetchone()[0]
    n_stu = cur.execute("select count(*) from exam_scores where exam_id=? and subject='总分'",
                        (eid,)).fetchone()[0]
    print(f'目标考试：#{eid} {row["exam_date"]} {row["name"]}（现有划线 {old} 条，总分 {n_stu} 行）')

    plan = []
    for direction, items in LINES.items():
        for subject, tc, bk in items:
            plan.append((eid, direction, '', subject, 1, BAND_TC, 'score', float(tc)))
            plan.append((eid, direction, '', subject, 2, BAND_BK, 'score', float(bk)))
    print(f'将写入 {len(plan)} 条线（{len(LINES)} 个方向 × 各科目 × 特控/本科）')
    for p in plan[:6]:
        print('   ', p[1], p[3], p[5], p[7])
    print('    ...')

    if not args.apply:
        print('\n（预览模式）加 --apply 才会真正写入。')
        return 0

    bak = f'{DB}.bak_band20260712_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    shutil.copy2(DB, bak)
    print('\n已备份：', os.path.basename(bak))

    with con:
        cur.execute('delete from exam_bands where exam_id=?', (eid,))
        cur.executemany(
            'insert into exam_bands (exam_id, direction, selection, subject, seq, name, '
            'lower_mode, lower_value) values (?,?,?,?,?,?,?,?)', plan)
    now = cur.execute('select count(*) from exam_bands where exam_id=?', (eid,)).fetchone()[0]
    print(f'写入完成：该场划线 {old} → {now} 条')
    con.close()

    # 2026-10-16 第七轮审计（C-H1）：登记迁移（幂等；登记失败不阻塞）
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import _mig_log                                  # noqa: E402
        _mig_log.record(os.path.basename(__file__),
                        note=f'exam {eid} 纯分数划线录入（特控/本科）')
    except Exception as _e:  # noqa: BLE001
        print('[WARN] 迁移登记失败（不影响写入结果）：', _e)

    # 清掉这场（以及依赖它的下游）分析缓存，避免旧数据被继续端出去
    sys.path.insert(0, BASE)
    from app import create_app           # noqa: E402
    app = create_app()
    with app.app_context():
        from app.modules.grades.services import stats_service as st
        # 真名是 clear_exam_data_cache（本进程清了即可；**已在跑的服务进程清不到**，
        # 它的进程级缓存 900s 自然过期，或重启/改动代码触发 reload 即生效）
        try:
            st.clear_exam_data_cache(eid)
            print('已清本进程该场缓存')
        except Exception as exc:  # noqa: BLE001
            print('清缓存失败（可忽略，缓存 900s 后自然过期）：', exc)
    return 0


if __name__ == '__main__':
    sys.exit(main())

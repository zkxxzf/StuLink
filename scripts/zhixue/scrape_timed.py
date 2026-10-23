# StuLink v1.19.71.64  2026-10-20
# 抓取智学网全部「限时练 / 标准化作业 / 限时作业」成绩
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""批量抓取智学网限时练类成绩（全部年级 × 全部学期）。

识别口径（2026-10-20 用户口径）：场次名称含「限时练」「标准化作业」
「限时作业」「限时训练」中任意一个；不看 markingExamTypeName，
因为智学网会把「高三年级第三次限时练」标成「月考」。

产物：
  data/zhixue/timed/<年级> <场次>（YYYY-MM-DD）.json
  data/zhixue/timed/index.json      已抓清单（按 examId 去重）

用法：
  py -3.12 -u scripts/zhixue/scrape_timed.py --list
  py -3.12 -u scripts/zhixue/scrape_timed.py --scrape
  py -3.12 -u scripts/zhixue/scrape_timed.py --scrape --years 2025-2026
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import fetch_zhixue as fx                      # noqa: E402
import zhixue_list as zl                       # noqa: E402
from browser_session import attach, ensure_login, log  # noqa: E402

OUT = ROOT / 'data' / 'zhixue' / 'timed'
INDEX = OUT / 'index.json'
PLAN = OUT / 'plan.json'
LIST_URL = ('https://www.zhixue.com/htm-freshreport/'
            '#/schoolReportList/schoolReportListIndex')
TIMED_WORDS = ('限时练', '标准化作业', '限时作业', '限时训练')
# 只要这三届（2022/2023 级已毕业，历史上传的场次不导）
TARGET_GRADES = ('2024级', '2025级', '2026级')


def is_timed(name):
    """场次名是否属于限时练类"""
    return any(w in (name or '') for w in TIMED_WORDS)


def date_of(exam):
    """毫秒时间戳 → YYYY-MM-DD（异常返回空串）"""
    try:
        return datetime.fromtimestamp(exam['date_ms'] / 1000).strftime('%Y-%m-%d')
    except Exception:
        return ''


def pick(groups, latest=0):
    """从学年×学期结果里挑限时练场次（按日期从新到旧）

    只保留 2024/2025/2026 级；同一场次在多页/多学期重复出现时按
    examId 去重（分页会把边界条目重复带出来）。
    """
    out, seen = [], set()
    dropped = 0
    for year, sem, exams in groups:
        for ex in exams:
            if not is_timed(ex['name']) or ex['exam_id'] in seen:
                continue
            grade = zl.grade_of(ex['name'], year)
            if grade not in TARGET_GRADES:
                dropped += 1
                continue
            seen.add(ex['exam_id'])
            ex = dict(ex)
            ex['year'] = year
            ex['sem'] = sem
            ex['date'] = date_of(ex)
            ex['grade'] = grade
            out.append(ex)
    if dropped:
        log(f'已排除非目标年级（2022/2023 级等）{dropped} 场')
    out.sort(key=lambda e: e.get('date') or '', reverse=True)
    return out[:latest] if latest and latest > 0 else out


def load_index():
    if INDEX.exists():
        return json.loads(INDEX.read_text(encoding='utf-8'))
    return {}


def save_index(idx):
    """合并写入（两个抓取进程并行时，避免后写的把先写的条目覆盖掉）"""
    OUT.mkdir(parents=True, exist_ok=True)
    merged = load_index()
    merged.update(idx)
    INDEX.write_text(json.dumps(merged, ensure_ascii=False, indent=1),
                     encoding='utf-8')


def report_url(exam, base='https://www.zhixue.com'):
    """报告页地址（学生成绩页由该页点进去）"""
    return (f'{base}/htm-freshreport/#/school/scoreAnalysis'
            f'?examId={exam["exam_id"]}&schoolId={exam["school_id"]}')


def scrape_one(context, cfg, exam, max_pages=0):
    """抓一场：报告页 → 学生成绩 → 逐页读表"""
    page = context.new_page()
    page.on('dialog', lambda d: d.accept())
    try:
        page.goto(report_url(exam))
        fx.enter_student_scores(page, cfg)
        fx.wait_score_table(page, cfg, timeout_ms=25000)
        result = fx.scrape_student_scores(page, cfg, max_pages)
        return result, page.url
    finally:
        page.close()


def do_scrape(context, cfg, exams, force=False):
    """逐场抓取并写盘；返回成功/失败计数"""
    idx = load_index()
    ok = fail = skip = 0
    for exam in exams:
        label = f'{exam["grade"]} {exam["name"]}（{exam["date"]}）'
        dest = OUT / f'{fx.safe_name(label, 70)}.json'
        if not force and (dest.exists() or exam['exam_id'] in idx):
            log(f'跳过（已抓过）：{exam["name"]}')
            skip += 1
            continue
        log(f'抓取：{label}')
        try:
            result, url = scrape_one(context, cfg, exam)
        except Exception as exc:                       # noqa: BLE001
            log(f'失败：{label} —— {type(exc).__name__}: {exc}')
            fail += 1
            continue
        # 注意顺序：result 里也有 grade（页面上的「高三年级」文字），
        # 必须让 exam 的 grade（2024级）覆盖它，否则导入时按年级匹配全落空
        payload = {
            **result,
            **exam,
            'exam': label, 'url': url,
            'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'row_count': len(result.get('rows') or []),
        }
        if payload['row_count'] <= 0:
            # 2026-10-21 审计：0 行（页面改版 / 登录态半失效 / 该场真没成绩）
            # 不能落盘登记 —— 否则下次按 index 判定"已抓过"永久跳过，
            # 数据静默缺失只能人工发现。计入失败，下次自动重试。
            log(f'  [WARN] 0 行，不落盘也不登记（下次自动重抓）：{label}')
            fail += 1
            continue
        OUT.mkdir(parents=True, exist_ok=True)
        dest = OUT / f'{fx.safe_name(label, 70)}.json'
        dest.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        idx[exam['exam_id']] = {
            'file': dest.name, 'grade': exam['grade'], 'name': exam['name'],
            'date': exam['date'], 'rows': payload['row_count'],
            'subjects': result.get('subjects') or [],
        }
        save_index(idx)
        log(f'  已存 {dest.name}（{payload["row_count"]} 行 / '
            f'{len(result.get("subjects") or [])} 科）')
        ok += 1
    log(f'完成：成功 {ok} / 失败 {fail} / 跳过 {skip}')
    return ok, fail, skip


def run_import():
    """抓完自动入库（两个进程并行时只让一个写库）"""
    lock = OUT / '.import.lock'
    try:
        if lock.exists() and (datetime.now().timestamp()
                              - lock.stat().st_mtime) > 1800:
            lock.unlink()
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        log('另一个抓取进程已在导入，跳过自动导入')
        return
    try:
        log('开始导入限时练…')
        sys.argv = ['import_to_timed.py', '--apply']    # 这里必须带 --apply
        import import_to_timed
        import_to_timed.main()
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def save_plan(exams):
    """把识别到的场次清单落盘，便于断点续抓（不必每次重扫列表）"""
    OUT.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps(exams, ensure_ascii=False, indent=1),
                    encoding='utf-8')
    log(f'清单已写入 {PLAN.name}（{len(exams)} 场）')


def load_plan():
    if not PLAN.exists():
        raise SystemExit(f'没有 {PLAN}，先跑一次 --list 生成清单')
    return json.loads(PLAN.read_text(encoding='utf-8'))


def main():
    ap = argparse.ArgumentParser(description='抓取智学网限时练成绩')
    ap.add_argument('--list', action='store_true', help='只列清单不抓取')
    ap.add_argument('--scrape', action='store_true', help='抓取成绩')
    ap.add_argument('--years', default='',
                    help='限定学年，逗号分隔（默认 2024-2027 三学年）')
    ap.add_argument('--list-only-from-plan', action='store_true',
                    help='直接读 plan.json，不重新扫列表')
    ap.add_argument('--latest', type=int, default=0, help='只处理最近 N 场')
    ap.add_argument('--skip', type=int, default=0, help='清单里跳过前 N 场')
    ap.add_argument('--take', type=int, default=0, help='只处理接下来 N 场（并行分片）')
    ap.add_argument('--max-pages', type=int, default=0, help='调试：每场最多翻 N 页')
    ap.add_argument('--force', action='store_true', help='忽略已抓记录')
    ap.add_argument('--import-after', action='store_true',
                    help='抓取结束后自动导入限时练（幂等）')
    args = ap.parse_args()

    if args.list_only_from_plan:
        exams = load_plan()
        end = args.skip + args.take if args.take else None
        exams = exams[args.skip:end]
        if args.latest:
            exams = exams[:args.latest]
        log(f'从 plan.json 读到 {len(exams)} 场')
        if args.scrape:
            cfg = fx.load_config()
            pw, browser, context = attach()
            try:
                do_scrape(context, cfg, exams, args.force)
            finally:
                pw.stop()
            if args.import_after:
                run_import()
        return

    years = ([y.strip() for y in args.years.split(',') if y.strip()]
             or list(zl.DEFAULT_YEARS))
    cfg = fx.load_config()
    pw, browser, context = attach()
    page = context.new_page()
    try:
        page.goto(LIST_URL)
        page.wait_for_timeout(5000)
        if not ensure_login(page, 300):
            raise SystemExit('登录态不可用，已中止')
        groups = zl.collect(page, years)

        exams = pick(groups, args.latest)
        log(f'共识别 {len(exams)} 场限时练类考试：')
        for ex in exams:
            print(f'  {ex["grade"]} | {ex["date"]} | {ex["type"]} | '
                  f'{ex["name"]} | {ex["exam_id"]}')
        if not exams:
            raise SystemExit('没有识别到场次（检查 --years 或识别口径）')
        save_plan(exams)
        if args.scrape:
            do_scrape(context, cfg, exams, args.force)
    finally:
        page.close()
        pw.stop()                      # 不断开用户浏览器，只断自动化会话


if __name__ == '__main__':
    main()

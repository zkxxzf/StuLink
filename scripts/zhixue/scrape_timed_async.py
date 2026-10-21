# StuLink v1.19.50.59  2026-10-20
# 多标签页并发抓取智学网限时练成绩（async Playwright + 同一个 Edge）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""多标签页并发抓取限时练成绩。

与 `scrape_timed.py` 的区别：单进程、单浏览器连接、开 N 个标签页同时抓，
不靠多进程共享 CDP（多进程实测会 `Connection closed while reading from
the driver`）。抓取逻辑（点「学生成绩」→ 逐科逐页读表）与同步版一致，
产物文件名也一致，两个脚本可互相续抓。

用法：
    py -3.12 -u scripts/zhixue/scrape_timed_async.py --workers 4
    py -3.12 -u scripts/zhixue/scrape_timed_async.py --workers 4 --import-after
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from playwright.async_api import async_playwright            # noqa: E402
import fetch_zhixue as fx                                     # noqa: E402
import scrape_timed as st                                     # noqa: E402

log = fx.log
CDP = 'http://127.0.0.1:9222'
TAB_JS = """() => [...document.querySelectorAll('.rank-num a.subjectStyle')]
    .map(a => (a.innerText || '').trim()).filter(Boolean)"""
PAGE1_JS = """() => {
    const on = document.querySelector('.page-box.report a.on');
    if (on && (on.innerText || '').trim() !== '1') {
        const a = [...document.querySelectorAll('.page-box.report a')]
            .find(x => (x.innerText || '').trim() === '1');
        if (a) a.click();
    }
}"""


async def wait_ready(page, timeout_ms=30000):
    """等「学生成绩」数据表就绪（两种布局都认）"""
    deadline = time.time() + timeout_ms / 1000
    while True:
        info = await page.evaluate(fx._READY_JS)
        if info['rows'] > 0 and (info['n_th'] >= 8 or info['n_tabs'] >= 2):
            return info['rows']
        if time.time() >= deadline:
            raise RuntimeError(
                f'学生成绩表格未就绪（th={info["n_th"]} 标签={info["n_tabs"]}'
                f' 行={info["rows"]}）')
        await page.wait_for_timeout(500)


async def goto_student_scores(page, exam):
    """报告页 → 点「学生成绩」→ 等表格"""
    await page.goto(st.report_url(exam))
    deadline = time.time() + 30
    while time.time() < deadline and 'studentScore' not in page.url:
        try:
            loc = page.get_by_text('学生成绩', exact=True)
            for i in range(await loc.count()):
                el = loc.nth(i)
                if await el.is_visible():
                    await el.click()
                    break
        except Exception:
            pass
        await page.wait_for_timeout(600)
    await wait_ready(page)


async def stable_read(page, subj, timeout=30):
    """等表格切到该科且连续两次读数一致"""
    deadline = time.time() + timeout
    prev = None
    while time.time() < deadline:
        d = await page.evaluate(fx._SCORE_READ_JS) or {}
        names = [g.get('name') for g in (d.get('groups') or [])]
        left = d.get('left') or []
        if names and names[0] == subj and left:
            sig = (names[0], len(left),
                   left[0][1] if len(left[0]) > 1 else '')
            if prev == sig:
                return d
            prev = sig
        else:
            prev = None
        await page.wait_for_timeout(650)
    return None


async def wait_changed(page, prev_first, timeout=25):
    """翻页后等首行变化且稳定"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        await page.wait_for_timeout(450)
        try:
            d = await page.evaluate(fx._SCORE_READ_JS) or {}
        except Exception:
            continue
        left = d.get('left') or []
        first = left[0][1] if left and len(left[0]) > 1 else ''
        if left and first and first != prev_first:
            sig = (len(left), first)
            if last == sig:
                return d
            last = sig
        else:
            last = None
    return None


def merge_rows(students, data):
    rows = fx.build_score_rows(data['groups'], data.get('left') or [],
                               data.get('right') or [])
    for r in rows:
        key = (r['exam_no'], r['name'])
        it = students.get(key)
        if it is None:
            it = {'seq': r['seq'], 'exam_no': r['exam_no'],
                  'class_name': r['class_name'], 'name': r['name'],
                  'scores': {}}
            students[key] = it
        for k, v in r['scores'].items():
            it['scores'].setdefault(k, {}).update(v)
    return len(rows)


async def drain_pages(page, subj, data, students, limit):
    """当前科逐页读到底，返回页数"""
    page_no, prev_first = 0, ''
    while page_no < limit:
        left = data.get('left') or []
        if not left:
            break
        first = (left[0][1] if len(left[0]) > 1 else '') or ''
        if page_no and first == prev_first:
            break
        page_no += 1
        prev_first = first
        merge_rows(students, data)
        log(f'  {subj} 第 {page_no} 页（累计 {len(students)} 人）')
        if not data.get('next_exists'):
            break
        nxt = page.locator('.page-box.report a:has-text("下一页")')
        if not await nxt.count():
            break
        try:
            await nxt.first.click()
        except Exception as exc:                       # noqa: BLE001
            log(f'  {subj} 翻页点击失败：{exc}')
            break
        nd = await wait_changed(page, first)
        if not nd:
            break
        data = nd
    return page_no


async def scrape_single(page, max_pages=0):
    """布局B（单科视图）：逐科点标签、每科翻页，按（考号,姓名）合并"""
    subs = await page.evaluate(TAB_JS)
    if not subs:
        raise RuntimeError('没找到科目标签（布局B）')
    log(f'  单科视图 {len(subs)} 科')
    result = {'subjects': list(subs), 'groups': [], 'grade': '', 'pages': [],
              'rows': [], 'page_size': 0, 'layout': 'single-subject'}
    students = {}
    limit = max_pages if max_pages and max_pages > 0 else 200
    tabs = page.locator('.rank-num a.subjectStyle')
    for si, subj in enumerate(subs):
        try:
            await tabs.nth(si).click()
        except Exception as exc:                       # noqa: BLE001
            log(f'  {subj}：点标签失败 {exc}')
            continue
        await page.wait_for_timeout(1100)
        await page.evaluate(PAGE1_JS)
        data = await stable_read(page, subj)
        if not data:
            log(f'  {subj}：没读到数据，跳过')
            continue
        cols = (data['groups'][0].get('cols') if data.get('groups') else [])
        result['groups'].append({'name': subj, 'cols': cols or []})
        result['grade'] = result['grade'] or data.get('grade') or ''
        n = await drain_pages(page, subj, data, students, limit)
        result['pages'].append({'subject': subj, 'pages': n})
    result['rows'] = list(students.values())
    result['page_size'] = len(result['rows'])
    if not result['rows']:
        result['error'] = '单科视图没抓到任何行'
    return result


async def scrape_all(page, max_pages=0):
    """布局A（全科并排）：逐页读"""
    result = {'subjects': [], 'groups': [], 'grade': '', 'pages': [],
              'rows': [], 'page_size': 0, 'layout': 'all-subjects'}
    students = {}
    limit = max_pages if max_pages and max_pages > 0 else 200
    data = await page.evaluate(fx._SCORE_READ_JS) or {}
    result['groups'] = data.get('groups') or []
    result['subjects'] = [g.get('name') for g in result['groups']]
    result['grade'] = data.get('grade') or ''
    n = await drain_pages(page, '全部', data, students, limit)
    result['pages'].append({'subject': '全部', 'pages': n})
    result['rows'] = list(students.values())
    result['page_size'] = len(result['rows'])
    if not result['rows']:
        result['error'] = '全科视图没抓到任何行'
    return result


async def scrape_one(ctx, exam, max_pages=0):
    """抓一场：新标签页 → 学生成绩 → 按布局分派"""
    page = await ctx.new_page()
    page.on('dialog', lambda d: asyncio.ensure_future(d.accept()))
    try:
        await goto_student_scores(page, exam)
        n_tabs = await page.evaluate(
            "() => document.querySelectorAll('.rank-num a.subjectStyle').length")
        if n_tabs >= 2:
            result = await scrape_single(page, max_pages)
        else:
            result = await scrape_all(page, max_pages)
        return result, page.url
    finally:
        try:
            await page.close()
        except Exception:
            pass


def save_payload(exam, label, result, url):
    payload = {
        **result,
        **exam,
        'exam': label, 'url': url,
        'scraped_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'row_count': len(result.get('rows') or []),
    }
    if payload['row_count'] <= 0:
        # 2026-10-21 审计：0 行不落盘也不登记 —— 文件存在会让 run() 下次直接
        # 跳过（dest.exists()），页面改版/登录态失效造成的空结果会静默沉底
        return None
    st.OUT.mkdir(parents=True, exist_ok=True)
    dest = st.OUT / f'{fx.safe_name(label, 70)}.json'
    dest.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    st.save_index({exam['exam_id']: {
        'file': dest.name, 'grade': exam['grade'], 'name': exam['name'],
        'date': exam['date'], 'rows': payload['row_count'],
        'subjects': result.get('subjects') or [],
    }})
    return dest


async def run(exams, workers, force):
    """并发抓取；每场一个标签页"""
    todo = []
    for exam in exams:
        label = f'{exam["grade"]} {exam["name"]}（{exam["date"]}）'
        dest = st.OUT / f'{fx.safe_name(label, 70)}.json'
        if not force and dest.exists():
            log(f'跳过（已抓过）：{exam["name"]}')
            continue
        todo.append(exam)
    log(f'待抓 {len(todo)} 场，并发 {workers}')
    sem = asyncio.Semaphore(workers)
    done = {'ok': 0, 'fail': 0}
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]

        async def worker(exam):
            async with sem:
                label = f'{exam["grade"]} {exam["name"]}（{exam["date"]}）'
                log(f'抓取：{label}')
                try:
                    result, url = await asyncio.wait_for(
                        scrape_one(ctx, exam), timeout=900)
                except Exception as exc:               # noqa: BLE001
                    log(f'失败：{label} —— {type(exc).__name__}: {exc}')
                    done['fail'] += 1
                    return
                dest = save_payload(exam, label, result, url)
                if dest is None:
                    log(f'  [WARN] 0 行，不落盘（下次自动重抓）：{label}')
                    done['fail'] += 1
                    return
                log(f'  已存 {dest.name}（{result.get("page_size")} 人）')
                done['ok'] += 1

        await asyncio.gather(*[worker(e) for e in todo])
    log(f'完成：成功 {done["ok"]} / 失败 {done["fail"]}')


def main():
    ap = argparse.ArgumentParser(description='多标签页并发抓取限时练')
    ap.add_argument('--workers', type=int, default=4, help='并发标签页数')
    ap.add_argument('--skip', type=int, default=0)
    ap.add_argument('--take', type=int, default=0)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--import-after', action='store_true')
    args = ap.parse_args()

    import browser_session as bs
    try:
        pw, browser, ctx = bs.attach()
        pw.stop()                     # 浏览器保持运行，后续走 async 连接
    except Exception as exc:          # noqa: BLE001
        log(f'浏览器准备失败：{exc}')
        return
    exams = st.load_plan()
    end = args.skip + args.take if args.take else None
    exams = exams[args.skip:end]
    asyncio.run(run(exams, args.workers, args.force))
    if args.import_after:
        st.run_import()


if __name__ == '__main__':
    main()

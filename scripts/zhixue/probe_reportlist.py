# StuLink v1.19.59.64  2026-10-20
# 探查智学网「学校报告列表」的筛选项与 reportlist 接口
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""一次性探查：打印列表页筛选项 + 拦截 reportlist 请求/响应结构。

用法：py -3.12 -u scripts/zhixue/probe_reportlist.py
产物：data/zhixue/probe_reportlist.json（含筛选值与考试类型统计）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / 'data' / 'zhixue'
GRADE_URL = ('https://www.zhixue.com/htm-freshreport/'
             '#/schoolReportList/schoolReportListIndex')

sys.path.insert(0, str(HERE))
from browser_session import attach, ensure_login, log, looks_like_login  # noqa: E402


def dump_selects(page):
    """列出页面上所有 el-select 的当前值、占位符和可选文案"""
    out = []
    for frame in page.frames:
        try:
            inputs = frame.locator('.el-select input.el-input__inner')
            n = inputs.count()
        except Exception:
            continue
        for i in range(n):
            el = inputs.nth(i)
            try:
                if not el.is_visible():
                    continue
                out.append({'frame': frame.url[:80], 'idx': i,
                            'value': el.input_value(),
                            'placeholder': el.get_attribute('placeholder')})
            except Exception:
                continue
    return out


def open_options(page):
    """点开第一个下拉，把选项文案 dump 出来（供确认年级/学期取值）"""
    opts = {}
    inputs = page.locator('.el-select input.el-input__inner')
    for i in range(min(inputs.count(), 6)):
        el = inputs.nth(i)
        try:
            label = el.input_value() or el.get_attribute('placeholder') or ''
            el.click()
            page.wait_for_timeout(500)
            items = page.locator(
                '.el-select-dropdown:visible .el-select-dropdown__item')
            vals = []
            for j in range(items.count()):
                vals.append((items.nth(j).inner_text() or '').strip())
            opts[f'{i}:{label}'] = vals
            page.keyboard.press('Escape')
            page.wait_for_timeout(200)
        except Exception:
            continue
    return opts


def collect(page, bucket):
    """拦截 reportlist：记录请求体与响应轮廓"""
    def on_req(req):
        if 'api/reportlist' in (req.url or ''):
            bucket['requests'].append({'url': req.url,
                                       'method': req.method,
                                       'post': (req.post_data or '')[:2000]})

    def on_resp(resp):
        if 'api/reportlist' not in (resp.url or ''):
            return
        try:
            body = resp.json()
        except Exception:
            return
        res = body.get('result') or {}
        exams = []
        for it in res.get('reportList') or []:
            d = it.get('data') or {}
            exams.append({
                'examId': d.get('examId'),
                'examName': d.get('examName'),
                'type': d.get('markingExamTypeName'),
                'date': d.get('examDateTime'),
                'schoolId': d.get('schoolId') or res.get('schoolId'),
            })
        bucket['responses'].append({'keys': sorted(res.keys()),
                                    'count': len(exams),
                                    'exams': exams[:50]})
    page.on('request', on_req)
    page.on('response', on_resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', help='Edge user-data-dir（默认 .edge-live）')
    ap.add_argument('--url', default=GRADE_URL)
    args = ap.parse_args()
    profile = Path(args.profile).resolve() if args.profile else None
    pw, browser, context = attach(profile=profile)
    page = context.new_page()
    bucket = {'requests': [], 'responses': []}
    collect(page, bucket)
    try:
        page.goto(args.url)
        page.wait_for_timeout(6000)
        log(f'当前页：{page.url} / {page.title()}')
        log(f'判为登录页：{looks_like_login(page)}')
        try:
            keys = page.evaluate('Object.keys(localStorage)')
            log(f'localStorage 键：{keys[:20]}')
        except Exception as exc:  # noqa: BLE001
            log(f'读 localStorage 失败：{exc}')
        if not ensure_login(page, 30):
            page.screenshot(path=str(DATA / 'probe_login.png'),
                            full_page=False)
            raise SystemExit('仍未登录，已中止（截图 probe_login.png）')
        page.wait_for_timeout(3000)
        info = {
            'url': page.url,
            'title': page.title(),
            'selects': dump_selects(page),
            'options': open_options(page),
            'requests': bucket['requests'],
            'responses': bucket['responses'],
        }
        types = {}
        for resp in bucket['responses']:
            for ex in resp.get('exams') or []:
                types[ex.get('type')] = types.get(ex.get('type'), 0) + 1
        info['type_counts'] = types
        DATA.mkdir(parents=True, exist_ok=True)
        dest = DATA / 'probe_reportlist.json'
        dest.write_text(json.dumps(info, ensure_ascii=False, indent=1),
                        encoding='utf-8')
        page.screenshot(path=str(DATA / 'probe_reportlist.png'), full_page=True)
        log(f'已写入 {dest}')
        log(f'筛选项：{json.dumps(info["options"], ensure_ascii=False)[:600]}')
        log(f'类型统计：{types}')
        log(f'请求体：{json.dumps(bucket["requests"][:2], ensure_ascii=False)[:800]}')
    finally:
        page.close()
        pw.stop()


if __name__ == '__main__':
    main()

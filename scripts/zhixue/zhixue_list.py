# StuLink v1.19.59.64  2026-10-20
# 智学网「学校报告列表」：按学年 × 学期遍历并收集考试清单
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""驱动智学网报告列表页，收集「学年 × 学期」下的全部考试。

为什么要驱动页面而不是直接拼 API：reportlist 的学年/学期不体现在
请求参数里（后端按会话状态过滤），只有真的切换下拉才会拿到对应数据。
"""
from __future__ import annotations

import re
import time

from browser_session import log

YEAR_RE = re.compile(r'^\d{4}-\d{4}学年$')
SEM_RE = re.compile(r'第[一二]学期$')
DEFAULT_YEARS = ('2024-2025', '2025-2026', '2026-2027')


def _inputs(page):
    """页面上全部 el-select 输入框（按出现顺序）"""
    return page.locator('.el-select input.el-input__inner')


def find_input(page, pattern):
    """按 input 的当前值正则定位下拉框，返回索引；找不到返回 -1"""
    el = _inputs(page)
    for i in range(el.count()):
        try:
            if pattern.search(el.nth(i).input_value() or ''):
                return i
        except Exception:
            continue
    return -1


def read_options(page, index):
    """点开第 index 个下拉，读出全部选项文案（随后 Esc 关闭）"""
    if index < 0:
        return []
    opts = _open_dropdown(page, index)
    vals = []
    for j in range(opts.count()):
        try:
            vals.append((opts.nth(j).inner_text() or '').strip())
        except Exception:
            continue
    page.keyboard.press('Escape')
    page.wait_for_timeout(200)
    return [v for v in vals if v]


def _open_dropdown(page, index):
    """点开第 index 个下拉，返回可见选项 locator（点两次兜底：首次可能被
    loading 遮罩吞掉，或把已打开的下拉关掉）"""
    opts = page.locator(
        '.el-select-dropdown:visible .el-select-dropdown__item')
    for _ in range(2):
        try:
            _inputs(page).nth(index).click()
        except Exception:
            return opts
        page.wait_for_timeout(900)
        if opts.count():
            return opts
    return opts


def pick_option(page, index, target, wait_ms=0):
    """在下拉里选中 target；成功返回 True"""
    if index < 0:
        return False
    el = _inputs(page)
    try:
        if (el.nth(index).input_value() or '').strip() == target:
            return True
    except Exception:
        return False
    opts = _open_dropdown(page, index)
    for j in range(opts.count()):
        try:
            if (opts.nth(j).inner_text() or '').strip() == target:
                opts.nth(j).click()
                if wait_ms:
                    page.wait_for_timeout(wait_ms)
                return True
        except Exception:
            continue
    page.keyboard.press('Escape')
    return False


class ReportListCatcher:
    """监听 reportlist 响应，按「切换前长度」等新响应"""

    def __init__(self, page):
        self.page = page
        self.items = []
        page.on('response', self._on_response)

    def _on_response(self, resp):
        if 'api/reportlist' not in (resp.url or ''):
            return
        try:
            body = resp.json()
        except Exception:
            return
        res = (body or {}).get('result') or {}
        if not res.get('reportList'):
            return
        self.items.append(parse_exams(body))

    def wait_new(self, prev_len, timeout=25):
        """等一条新响应（没有新响应返回 None）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if len(self.items) > prev_len:
                return self.items[-1]
            self.page.wait_for_timeout(400)
        return None


def parse_exams(body):
    """reportlist 响应 → 规范化考试条目列表"""
    res = (body or {}).get('result') or {}
    school = res.get('schoolId') or ''
    out = []
    for it in res.get('reportList') or []:
        d = it.get('data') or {}
        eid = d.get('examId')
        if not eid:
            continue
        out.append({
            'exam_id': eid,
            'name': (d.get('examName') or '').strip(),
            'type': (d.get('markingExamTypeName') or '').strip(),
            'date_ms': d.get('examDateTime'),
            'school_id': d.get('schoolId') or school,
        })
    return out


def wait_input(page, pattern, timeout=25):
    """等下拉渲染完成（SPA 首屏 5 秒内可能还没挂载筛选条）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        idx = find_input(page, pattern)
        if idx >= 0:
            return idx
        page.wait_for_timeout(500)
    return -1


def drain_pages(page, catcher, max_pages=8):
    """收完当前筛选下的全部分页（列表每页 15 条，底部 el-pagination）"""
    exams = []
    for _ in range(max_pages):
        if catcher.items:
            exams.extend(catcher.items[-1])
        nxt = page.locator('.el-pagination .btn-next')
        if not nxt.count():
            break
        try:
            disabled = nxt.first.is_disabled()
        except Exception:
            disabled = False
        if disabled:
            break
        before = len(catcher.items)
        try:
            nxt.first.click()
        except Exception:
            break
        if not catcher.wait_new(before, timeout=15):
            break
        page.wait_for_timeout(900)
    return exams


def collect(page, years=DEFAULT_YEARS):
    """遍历学年 × 学期；返回 [(学年, 学期, [考试…]), …]"""
    catcher = ReportListCatcher(page)
    page.wait_for_timeout(2500)          # 首屏自带一次 reportlist
    wait_input(page, YEAR_RE)            # 等筛选条挂载
    out = []
    for year in years:
        before = len(catcher.items)
        # 选项文案带「学年」后缀（如 2025-2026学年），target 必须完全一致
        changed = pick_option(page, find_input(page, YEAR_RE),
                              f'{year}学年', wait_ms=3500)
        log(f'切学年 {year}：{changed}，响应 {len(catcher.items)} 条')
        if changed and len(catcher.items) == before:
            catcher.wait_new(before, timeout=15)
        sems = read_options(page, wait_input(page, SEM_RE))
        if not sems:
            log(f'{year}：读不到学期下拉，跳过')
            continue
        for sem in sems:
            before = len(catcher.items)
            s_idx = find_input(page, SEM_RE)
            cur = ''
            try:
                cur = (_inputs(page).nth(s_idx).input_value() or '').strip()
            except Exception:
                pass
            if cur != sem and not pick_option(page, s_idx, sem, wait_ms=2500):
                continue
            if len(catcher.items) == before:
                catcher.wait_new(before, timeout=15)
            exams = drain_pages(page, catcher)
            log(f'{year} {sem}：{len(exams)} 场')
            out.append((year, sem, exams))
    return out


def grade_of(name, year):
    """场次名 + 学年 → 年级标签（如 2025级）

    优先级：名里直接写「2024级」最可靠；否则按「高一/高二/高三」+
    学年推算（学年 2026-2027 的高一=2026级、高二=2025级、高三=2024级）。
    """
    m = re.search(r'(20\d{2})\s*级', name or '')
    if m:
        return f'{m.group(1)}级'
    m = re.search(r'高\s*([一二三])', name or '')
    if not m or not year:
        return ''
    offset = {'一': 0, '二': 1, '三': 2}[m.group(1)]
    return f'{int(year[:4]) - offset}级'

# StuLink v1.19.0 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""AI 智能导入：任意格式成绩表 → AI 识别列映射 → 规则解析全量 → 复用导入管道落库

设计要点（用户口径）：
  1. **不要求标准化模板**：任意表头、任意 sheet 布局都能进
  2. **AI 只做“列映射”这一件小事**：只把表头 + 前几行样本发给 AI，让它输出
     「哪列是学号/姓名/班级/考号/总分/各科/原始分」；**全量数据由确定性代码解析**，
     绝不逐行走 AI（避免漏行、串行、超 token）
  3. **AI 先问用户分数类型**：赋分 / 原始 / 两者都有；两个文件分别像赋分表和原始表时，
     建议一并发送（本模块支持一次上传多个文件、多 sheet 合并）
  4. 无 AI（未配置本机 Key）时：退回**手工指定列映射**，同一套解析引擎

对外接口：
    probe(stream)                      → {'sheets': [{name, header_row, headers, sample}]}
    build_ai_prompt(probe, exam)       → (system, user) 两段提示词
    parse_ai_mapping(text)             → 映射 dict（容错解析 JSON）
    rows_from_mapping(probe, mapping, score_kind) → 标准 rows（喂给 import_service/store_service）
"""
from __future__ import annotations

import json
import re

import openpyxl

from app.models.grades import SUBJECTS, TOTAL_SUBJECT

# 标准字段（AI 要从表头里找这些）
FIELD_LABELS = {
    'no': '学号（唯一，优先）',
    'name': '姓名',
    'class_name': '班级',
    'grade': '年级',
    'exam_no': '考号 / 准考证号',
    'total': '总分',
    'raw_total': '总分（原始分）',
}
# 科目别名：文件常写「英语」，系统内是「外语」
SUBJ_ALIAS = {'英语': '外语'}
RAW_SUFFIX = ('原始分', '原始成绩', '原始', '裸分')
SCORE_KINDS = ('converted', 'raw', 'both', 'unknown')
SCORE_KIND_LABEL = {'converted': '赋分成绩', 'raw': '原始成绩',
                    'both': '两者都有', 'unknown': '无法判断'}

MAX_SAMPLE_ROWS = 3          # 给 AI 看的样本行数（省 token）
MAX_SCAN_ROWS = 4000         # 单表最多解析行数（防超大文件拖垮）


def _s(v):
    return '' if v is None else str(v).strip()


def _num(v):
    s = _s(v).replace(',', '').replace('，', '')
    if not s or s in ('-', '—', '无', '/', '缺考'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def probe(stream):
    """读取工作簿结构：每个 sheet 的表头行 + 前几行样本（只给 AI 看这些）"""
    wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for i, r in enumerate(ws.iter_rows(min_row=1, max_row=MAX_SAMPLE_ROWS + 12,
                                           values_only=True)):
            cells = [_s(c) for c in (r or [])]
            if any(cells):
                rows.append((i + 1, cells))
            if len(rows) >= MAX_SAMPLE_ROWS + 8:
                break
        if not rows:
            continue
        # 表头行：取“**文本单元格最多**”的前 12 行里最早出现的一行
        # （不能只看非空个数：数据行往往非空更多，会把表头挤掉）
        def _text_score(cells):
            n = 0
            for c in cells:
                c = c.strip()
                if c and not re.match(r'^[-+]?\d+(\.\d+)?$', c):
                    n += 1
            return n

        hi, hdr = rows[0]
        best = -1
        for lineno, cells in rows[:12]:
            sc = _text_score(cells)
            if sc >= 3 and sc > best:
                best, hi, hdr = sc, lineno, cells
        sample = [cells for lineno, cells in rows if lineno > hi][:MAX_SAMPLE_ROWS]
        out.append({'name': ws.title, 'header_row': hi, 'headers': hdr,
                    'sample': sample})
    wb.close()
    return {'sheets': out}


def build_ai_prompt(probe_data, exam):
    """构造提示词：只要求 AI 输出“列映射 + 分数类型判断 + 待确认问题”"""
    parts = []
    for sh in probe_data['sheets'][:6]:
        parts.append('【工作表：%s（表头在第 %d 行）】\n表头：%s\n样本：%s'
                     % (sh['name'], sh['header_row'],
                        ' | '.join(sh['headers'][:40]),
                        json.dumps(sh['sample'], ensure_ascii=False)[:1200]))
    system = (
        '你是成绩表结构识别助手。用户会给你 Excel 的表头与前几行样本，'
        '你要判断每列对应什么字段，并判断这份数据是「赋分成绩」还是「原始成绩」。\n'
        '只输出一个 JSON，不要任何解释，格式：\n'
        '{"score_kind":"converted|raw|both|unknown",'
        '"sheets":[{"name":"工作表名","header_row":1,'
        '"cols":{"no":"学号","name":"姓名","class_name":"班级","exam_no":"考号",'
        '"total":"总分","raw_total":"总分原始分"},'
        '"subjects":{"语文":"语文列名","数学":"数学列名"},'
        '"raw_subjects":{"语文":"语文原始分列名"}}],'
        '"questions":["需要向用户确认的问题"],"notes":"补充说明"}\n'
        '规则：\n'
        '1) 值必须是原表头里的**列名原文**；找不到的字段就不要出现在 cols 里。\n'
        '2) 科目名用系统的九科名：语文/数学/外语(英语)/物理/化学/生物/政治/历史/地理。\n'
        '3) 形如「语文原始分」「总分(原始)」的列放进 raw_subjects / raw_total。\n'
        '4) score_kind：出现“赋分/等第/转换分”判 converted；只有原始分明细判 raw；'
        '两套都在判 both；无法判断判 unknown。\n'
        '5) 若表里没有学号列但有考号列，也要在 questions 里提醒。'
    )
    user = ('考试信息：年级=%s，阶段=%s。\n%s'
            % (exam.grade if exam else '', (exam.exam_type if exam else '') or '',
               '\n\n'.join(parts)))
    return system, user


def parse_ai_mapping(text):
    """从 AI 回复里取出 JSON（容错：去掉 ```json 包裹、截取首个 { 到末个 }）"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r'^```(?:json)?', '', t).strip()
    t = re.sub(r'```$', '', t).strip()
    try:
        data = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        i, j = t.find('{'), t.rfind('}')
        if i < 0 or j <= i:
            return None
        try:
            data = json.loads(t[i:j + 1])
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    kind = _s(data.get('score_kind')).lower()
    data['score_kind'] = kind if kind in SCORE_KINDS else 'unknown'
    data.setdefault('sheets', [])
    data.setdefault('questions', [])
    return data


def _col_index(headers, names):
    """在表头里按精确名/含名找列号（返回首个命中）"""
    if not names:
        return None
    for want in names:
        for i, h in enumerate(headers):
            if h == want:
                return i
    for want in names:
        for i, h in enumerate(headers):
            if want and want in h:
                return i
    return None


def rows_from_mapping(sheet, mapping, score_kind, exam):
    """按映射把某个 sheet 解析为标准 rows（与 import_service 的 rows 结构一致）

    score_kind: converted=主列是赋分 / raw=主列是原始分 / both=主列赋分+另存原始
    返回 (rows, errors)
    """
    headers = sheet['headers']
    cols = (mapping or {}).get('cols') or {}
    subj_map = (mapping or {}).get('subjects') or {}
    raw_map = (mapping or {}).get('raw_subjects') or {}
    i_no = _col_index(headers, [cols.get('no')] if cols.get('no') else
                      ['学号', '学生学号'])
    i_name = _col_index(headers, [cols.get('name')] if cols.get('name') else ['姓名'])
    i_cls = _col_index(headers, [cols.get('class_name')] if cols.get('class_name') else [])
    i_examno = _col_index(headers, [cols.get('exam_no')] if cols.get('exam_no') else
                          ['考号', '准考证号'])
    i_total = _col_index(headers, [cols.get('total')] if cols.get('total') else
                         ['总分', '总成绩'])
    i_raw_total = _col_index(headers, [cols.get('raw_total')] if cols.get('raw_total') else [])

    # 科目列（AI 给的列名 or 直接就是科目名）
    subj_cols, raw_cols = {}, {}
    for std_sub in SUBJECTS:
        want = subj_map.get(std_sub)
        cand = [want] if want else []
        cand += [std_sub] + [k for k, v in SUBJ_ALIAS.items() if v == std_sub]
        i = _col_index(headers, cand)
        if i is not None:
            subj_cols[std_sub] = i
        rwant = raw_map.get(std_sub)
        rcand = [rwant] if rwant else []
        for h in headers:
            if any(sfx in h for sfx in RAW_SUFFIX) and std_sub in h:
                rcand.append(h)
        ri = _col_index(headers, rcand)
        if ri is not None:
            raw_cols[std_sub] = ri
    if not subj_cols:
        return [], [{'line': sheet['header_row'], 'no': '',
                     'reason': '未识别到任何科目列（可在页面上手工指定列）'}]

    rows, errors = [], []
    for lineno, cells in sheet['rows']:
        if lineno <= sheet['header_row']:
            continue
        if len(rows) >= MAX_SCAN_ROWS:
            errors.append({'line': lineno, 'no': '', 'reason': '超出单表行数上限，已截断'})
            break
        get = lambda i: (cells[i] if i is not None and i < len(cells) else '')  # noqa: E731
        no = _s(get(i_no))
        subjects = {s: _num(get(i)) for s, i in subj_cols.items()}
        raw = {s: _num(get(i)) for s, i in raw_cols.items()}
        if score_kind == 'raw' and not any(v is not None for v in subjects.values()) \
                and any(v is not None for v in raw.values()):
            # 文件只有原始分：把原始分当主分
            subjects, raw = raw, {}
        if not no:
            if any(v is not None for v in subjects.values()):
                errors.append({'line': lineno, 'no': '', 'reason': '缺少学号'})
            continue
        if not any(v is not None for v in subjects.values()):
            continue                      # 全行无分 = 未参加
        tot = _num(get(i_total))
        raw_tot = _num(get(i_raw_total))
        if tot is None and score_kind in ('converted', 'both') and raw_tot is not None:
            tot = raw_tot
        rows.append({
            'no': no,
            'line': lineno,
            'name': _s(get(i_name)),
            'class_name': _s(get(i_cls)),
            'exam_no': _s(get(i_examno)),
            'subjects': subjects,
            'total': tot,
            'raw_subjects': raw,
            'raw_total': raw_tot,
        })
    return rows, errors


def read_sheet_rows(stream, sheet_name, header_row, max_rows=MAX_SCAN_ROWS):
    """按需重读某个 sheet 的全量行（probe 只存样本，解析时再取全量）"""
    wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else None
    out = []
    if ws is not None:
        for i, r in enumerate(ws.iter_rows(min_row=1, max_row=max_rows + header_row + 1,
                                           values_only=True)):
            out.append((i + 1, [_s(c) for c in (r or [])]))
    wb.close()
    return out

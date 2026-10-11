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

import io
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


def _as_stream(stream):
    """openpyxl 需要文件名或文件对象；路由传进来的是 bytes，这里统一包一层"""
    if isinstance(stream, (bytes, bytearray)):
        return io.BytesIO(bytes(stream))
    return stream


def _open_workbook(stream):
    """打开工作簿；某些导出文件 dimension 标记错误（报 A1），
    read_only 会只读到 1 列 → 检测到就退回普通模式重开。"""
    src = _as_stream(stream)
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    broken = any((getattr(ws, 'max_row', 0) or 0) <= 1
                 and (getattr(ws, 'max_column', 0) or 0) <= 1
                 for ws in wb.worksheets)
    if broken and getattr(src, 'seekable', lambda: False)():
        try:
            src.seek(0)
            wb2 = openpyxl.load_workbook(src, read_only=False, data_only=True)
            wb.close()
            return wb2
        except Exception:  # noqa: BLE001
            pass
    return wb


def _num(v):
    s = _s(v).replace(',', '').replace('，', '')
    if not s or s in ('-', '—', '无', '/', '缺考'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _row_kind(cells):
    """统计一行里的「文本格」与「纯数字格」数量（用于判定表头行）"""
    text = num = 0
    for c in cells:
        c = (c or '').strip()
        if not c:
            continue
        if re.match(r'^[-+]?\d+(\.\d+)?$', c):
            num += 1
        else:
            text += 1
    return text, num


def _compose_headers(hdr_rows):
    """把多行表头合并成「父·子」列名（父层按列向右填充）

    hdr_rows: [(lineno, cells)]，1~3 行。单行时原样返回。
    """
    if len(hdr_rows) == 1:
        return list(hdr_rows[0][1])
    n = max(len(c) for _ln, c in hdr_rows)
    parent = [''] * n
    for _ln, cells in hdr_rows[:-1]:
        cur = ''
        for j in range(n):
            t = cells[j] if j < len(cells) else ''
            if t:
                cur = t
            if cur and cur not in parent[j].split('·'):
                parent[j] = (parent[j] + '·' + cur).strip('·') if parent[j] else cur
    child = hdr_rows[-1][1]
    out = []
    for j in range(n):
        c = child[j] if j < len(child) else ''
        p = parent[j]
        out.append((p + '·' + c) if (p and c) else (p or c))
    return out


def probe(stream):
    """读取工作簿结构：每个 sheet 的表头行（支持多行合并）+ 前几行样本"""
    wb = _open_workbook(stream)
    out = []
    for ws in wb.worksheets:
        rows = []
        for i, r in enumerate(ws.iter_rows(min_row=1, max_row=MAX_SAMPLE_ROWS + 20,
                                           values_only=True)):
            cells = [_s(c) for c in (r or [])]
            rows.append((i + 1, cells))
        if not any(any(c for c in cells) for _ln, cells in rows):
            continue

        # 表头块：从首个「标签行」（文本>=2 且无纯数字）起，连续标签行（最多 3 行）
        hdr_rows = []
        for lineno, cells in rows:
            if lineno > 15 and not hdr_rows:
                break
            text, num = _row_kind(cells)
            is_label = text >= 2 and num == 0
            if not hdr_rows:
                if is_label:
                    hdr_rows.append((lineno, cells))
                continue
            if lineno == hdr_rows[-1][0] + 1 and is_label and len(hdr_rows) < 3:
                hdr_rows.append((lineno, cells))
                continue
            break
        if not hdr_rows:
            # 回退：旧启发式（前 12 行里文本最多的一行）
            text_rows = [(lineno, cells) for lineno, cells in rows[:12]]
            best, hi, hdr = -1, text_rows[0][0], text_rows[0][1]
            for lineno, cells in text_rows:
                sc = _row_kind(cells)[0]
                if sc >= 3 and sc > best:
                    best, hi, hdr = sc, lineno, cells
            hdr_rows = [(hi, hdr)]

        header_row = hdr_rows[-1][0]
        headers = _compose_headers(hdr_rows)
        sample = [cells for lineno, cells in rows if lineno > header_row][:MAX_SAMPLE_ROWS]
        out.append({'name': ws.title, 'header_row': header_row,
                    'header_rows': [ln for ln, _c in hdr_rows],
                    'headers': headers, 'sample': sample})
    wb.close()
    return {'sheets': out}


def build_ai_prompt(probe_data, exam):
    """构造提示词：只要求 AI 输出“列映射 + 分数类型判断 + 待确认问题”"""
    parts = []
    for sh in probe_data['sheets'][:6]:
        parts.append('【工作表：%s（表头在第 %s 行%s）】\n表头：%s\n样本：%s'
                     % (sh['name'], sh.get('header_row'),
                        ('，已合并多行表头' if len(sh.get('header_rows') or []) > 1 else ''),
                        ' | '.join(sh['headers'][:120]),
                        json.dumps(sh['sample'], ensure_ascii=False)[:1800]))
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
        '1) 值必须是原表头里的**列名原文**（多行表头已合并为「父·子」，用它当作列名）；'
        '找不到的字段就不要出现在 cols 里。\n'
        '2) 科目名用系统的九科名：语文/数学/外语(英语)/物理/化学/生物/政治/历史/地理。\n'
        '3) **同名两列**时：带「(赋分)」标记的进 subjects（主分），同名不带标记的进 raw_subjects（原始分）；'
        '如「化学(赋分)·分数」→ subjects.化学、「化学·分数」→ raw_subjects.化学。\n'
        '4) **名次/等级/进退步**列（如「方向全体名次」「等级」）不要映射。\n'
        '5) score_kind：出现“赋分/转换分”判 converted；只有原始分判 raw；两套都在判 both；否则 unknown。\n'
        '6) 若表里**没有学号列、只有考号列**，也要映射 cols.exam_no，并在 questions 里提醒'
        '「将按考号匹配学号（需本场考务已导入或已有考号—学号对照）」。'
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


# 忽略列：名次/等级/进退步等（不是分数）
_IGNORE_WORDS = ('名次', '排名', '校次', '班次', '等级', '等第', '进退步', '位次')
# 「赋分」标记：带此标记的科目/总分为主分，不带标记的同名列为原始分
_CONV_MARKS = ('赋分', '转换分')


def _split_label(h):
    """列名拆 (父, 子)；多行表头已合并成「父·子」"""
    if not h:
        return '', ''
    if '·' in h:
        p, c = h.rsplit('·', 1)
        return p.strip(), c.strip()
    return h.strip(), ''


def _base_subject(text):
    """从标签里取标准科目名（去括号后缀），取不到返回 ''"""
    t = re.sub(r'[（(][^）)]*[）)]', '', text or '').strip().strip('· -_')
    if not t:
        return ''
    if t in SUBJECTS:
        return t
    for k, v in SUBJ_ALIAS.items():
        if t == k or t == v:
            return v
    for s in SUBJECTS:
        if s and s in t:
            return s
    for k, v in SUBJ_ALIAS.items():
        if k in t:
            return v
    return ''


def classify_headers(headers):
    """按（可能合并的）列名归类列号：

    规则：
      · 「(赋分)」标记的科目/总分 → 主分；同名不带标记 → 原始分
      · 名次/等级/进退步列一律忽略
      · 某科目只有「原始」列时，原始列顶上作主分（兼容单轨原始表）
    返回 {'cols': {...}, 'subjects': {...}, 'raw_subjects': {...}, 'ignored': [...]}
    """
    cols, subjects, raw_subjects, ignored = {}, {}, {}, []
    for j, h in enumerate(headers):
        if not h:
            continue
        parent, child = _split_label(h)
        label = parent if child else h
        if any(w in (parent + child) for w in _IGNORE_WORDS):
            ignored.append(j)
            continue
        if '学号' in label and 'no' not in cols:
            cols['no'] = j
            continue
        if '自定义考号' in label and 'no' not in cols:
            # 本校模板里「自定义考号」列放的就是学号（优先于「准考证号」）
            cols['no'] = j
            continue
        if '姓名' in label and 'name' not in cols:
            cols['name'] = j
            continue
        if ('考号' in label or '准考证' in label) and 'exam_no' not in cols:
            cols['exam_no'] = j
            continue
        if '班级' in label and 'class_name' not in cols:
            cols['class_name'] = j
            continue
        if '选科' in label or '组合' in label:
            ignored.append(j)
            continue
        conv = any(m in label for m in _CONV_MARKS)
        if label.startswith(('总分', '总成绩')):
            key = 'total' if conv else 'raw_total'
            if key not in cols:
                cols[key] = j
            continue
        sub = _base_subject(label)
        if sub:
            (subjects if conv else raw_subjects).setdefault(sub, j)
            continue
        ignored.append(j)
    # 只有原始列、没有主分列 → 原始顶上
    for sub, j in list(raw_subjects.items()):
        if sub not in subjects:
            subjects[sub] = j
            del raw_subjects[sub]
    if 'total' not in cols and 'raw_total' in cols:
        cols['total'] = cols.pop('raw_total')
    return {'cols': cols, 'subjects': subjects, 'raw_subjects': raw_subjects,
            'ignored': ignored}


def rows_from_mapping(sheet, mapping, score_kind, exam):
    """按映射（AI/手工）＋自动归类，把某个 sheet 解析为标准 rows

    score_kind: converted=主列是赋分 / raw=主列是原始分 / both=主列赋分+另存原始
    行可以只有考号（无学号）——由调用方按考号解析学号。
    返回 (rows, errors)
    """
    headers = sheet['headers']
    auto = classify_headers(headers)
    cols = (mapping or {}).get('cols') or {}
    subj_map = (mapping or {}).get('subjects') or {}
    raw_map = (mapping or {}).get('raw_subjects') or {}

    def pick(names, default):
        i = _col_index(headers, [n for n in (names or []) if n])
        return i if i is not None else default

    i_no = pick([cols.get('no')], auto['cols'].get('no'))
    i_name = pick([cols.get('name')], auto['cols'].get('name'))
    i_cls = pick([cols.get('class_name')], auto['cols'].get('class_name'))
    i_examno = pick([cols.get('exam_no')], auto['cols'].get('exam_no'))
    i_total = pick([cols.get('total')], auto['cols'].get('total'))
    i_raw_total = pick([cols.get('raw_total')], auto['cols'].get('raw_total'))

    # 科目列：映射优先 → 自动归类 → 最后才按科目名找列
    # （不能反过来：科目名会出现在「总分(历史)(赋分)」这类组名里，模糊匹配会错列）
    subj_cols, raw_cols = {}, {}
    for std_sub in SUBJECTS:
        aliases = [std_sub] + [k for k, v in SUBJ_ALIAS.items() if v == std_sub]
        want = subj_map.get(std_sub)
        i = _col_index(headers, [want]) if want else None
        if i is None:
            i = auto['subjects'].get(std_sub)
        if i is None and not want:
            i = _col_index(headers, aliases)
        if i is not None:
            subj_cols[std_sub] = i
        rwant = raw_map.get(std_sub)
        ri = _col_index(headers, [rwant]) if rwant else None
        if ri is None:
            ri = auto['raw_subjects'].get(std_sub)
        if ri is None and not rwant:
            rcand = [h for h in headers
                     if h and any(sfx in h for sfx in RAW_SUFFIX) and std_sub in h]
            ri = _col_index(headers, rcand)
        if ri is not None:
            raw_cols[std_sub] = ri
    if not subj_cols and not raw_cols:
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
        exam_no = _s(get(i_examno))
        subjects = {s: _num(get(i)) for s, i in subj_cols.items()}
        raw = {s: _num(get(i)) for s, i in raw_cols.items()}
        if score_kind == 'raw' and not any(v is not None for v in subjects.values()) \
                and any(v is not None for v in raw.values()):
            # 文件只有原始分：把原始分当主分
            subjects, raw = raw, {}
        if not no and not exam_no:
            if any(v is not None for v in subjects.values()):
                errors.append({'line': lineno, 'no': '', 'reason': '缺少学号与考号'})
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
            'exam_no': exam_no,
            'subjects': subjects,
            'total': tot,
            'raw_subjects': raw,
            'raw_total': raw_tot,
        })
    return rows, errors


def read_sheet_rows(stream, sheet_name, header_row, max_rows=MAX_SCAN_ROWS):
    """按需重读某个 sheet 的全量行（probe 只存样本，解析时再取全量）"""
    wb = _open_workbook(stream)
    ws = wb[sheet_name] if sheet_name in wb.sheetnames else None
    out = []
    if ws is not None:
        for i, r in enumerate(ws.iter_rows(min_row=1, max_row=max_rows + header_row + 1,
                                           values_only=True)):
            out.append((i + 1, [_s(c) for c in (r or [])]))
    wb.close()
    return out

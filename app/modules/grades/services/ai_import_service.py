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
from datetime import date

import openpyxl

from app.models.grades import SUBJECTS, TOTAL_SUBJECT

# 系统内合法科目集合（用于把「英语→外语」并校验是否为真科目）
SUBJECT_SET = set(SUBJECTS) | {TOTAL_SUBJECT}

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


# ==================== 考务「学生信息表 / 考场信息表 / 编排结果」列识别（v1.19.5） ====================

AFFAIR_FIELD_LABELS = {
    # 学生（学生信息表 + 编排结果）
    'no': '学号', 'name': '姓名', 'class_name': '班级', 'selection': '选科/组合',
    'subject': '考试科目', 'attend': '是否参加',
    'fixed_room': '固定考场', 'fixed_seat': '固定座号', 'custom_number': '自定义考号',
    'room': '考场编号', 'seat': '座号', 'exam_no': '考号',
    'room_seat': '考场-座号（合并列）',
    # 考场（考场信息表）
    'room_loc': '考场位置/教室', 'room_subject': '考场科目',
    'capacity': '考场容量', 'room_prefix': '考号前缀', 'room_note': '备注',
}


def classify_affair_headers(headers):
    """按（可能合并的）列名归类考务字段 → {字段: 列号}

    学生：no 学号 / name 姓名 / class_name 班级 / selection 选科 / subject 考试科目 /
          attend 是否参加 / fixed_room 固定考场 / fixed_seat 固定座号 /
          custom_number 自定义考号 / room 考场 / seat 座号 / exam_no 考号 /
          room_seat 考场-座号(合并)
    考场：room_loc 位置 / room_subject 科目 / capacity 容量 / room_prefix 考号前缀 / room_note 备注

    注意匹配顺序：先「固定/位置/编号/前缀/自定义」，再落到「考场/考号」通用词。
    """
    out = {}
    for j, h in enumerate(headers):
        if not h:
            continue
        parent, child = _split_label(h)
        L = parent if child else h
        key = None
        if '座号' in L and ('考场' in L or '试室' in L):
            key = 'room_seat'
        elif '固定考场' in L:
            key = 'fixed_room'
        elif '固定座号' in L or '固定座位' in L:
            key = 'fixed_seat'
        elif '自定义考号' in L:
            key = 'custom_number'
        elif '考号前缀' in L or '前缀' in L:
            key = 'room_prefix'
        elif '考场位置' in L or '教室' in L or '位置' in L or '地点' in L:
            key = 'room_loc'
        elif '考场科目' in L or '适用选科' in L or '考场选科' in L:
            key = 'room_subject'
        elif '考场容量' in L or '容量' in L:
            key = 'capacity'
        elif '考场编号' in L or '考场号' in L or '考场' in L or '试室' in L:
            key = 'room'
        elif '座号' in L or '座位' in L:
            key = 'seat'
        elif '学号' in L:
            key = 'no'
        elif '考号' in L or '准考证' in L:
            key = 'exam_no'
        elif '姓名' in L:
            key = 'name'
        elif '班级' in L:
            key = 'class_name'
        elif '是否参加' in L or '是否参考' in L or L in ('参加', '参考'):
            key = 'attend'
        elif '选科' in L or '组合' in L:
            key = 'selection'
        elif '考试科目' in L or L in ('科目', '学科'):
            key = 'subject'
        elif '备注' in L:
            key = 'room_note'
        if key:
            out.setdefault(key, j)
    return out


# 座位信号：带这些列的表可当「座位表/桌签」用，用来推断每间考场的容量（最大座号）
_SEAT_FIELDS = ('room', 'seat', 'room_seat')


def has_seat_cols(headers):
    """这张表带「考场 / 座号」信息吗（→ 可作为座位表推断考场容量）"""
    af = classify_affair_headers(headers)
    return any(k in af for k in _SEAT_FIELDS)


def _clean_bad(v):
    """过滤 Excel 错误值/空（#N/A 等）"""
    t = _s(v)
    return '' if not t or t.startswith('#') else t


def rows_from_affair_mapping(sheet, mapping):
    """按映射（手工/AI）＋自动归类，把 sheet 解析为考务行：

    学生行：{no, name, class_name, selection, subject, attend, fixed_room, fixed_seat,
            custom_number, room, seat, exam_no}
    考场行：{room, room_loc, room_subject, capacity, room_prefix, room_note}
    返回 (rows, errors)
    """
    headers = sheet['headers']
    auto = classify_affair_headers(headers)
    m = mapping or {}

    def idx(key):
        v = m.get(key)
        i = _col_index(headers, [v]) if v else None
        return i if i is not None else auto.get(key)

    i_no, i_name = idx('no'), idx('name')
    i_cls, i_exam = idx('class_name'), idx('exam_no')
    i_room, i_seat, i_rs = idx('room'), idx('seat'), idx('room_seat')
    i_sel, i_att, i_loc = idx('selection'), idx('attend'), idx('room_loc')
    i_subj, i_fr, i_fs, i_cn = idx('subject'), idx('fixed_room'), idx('fixed_seat'), idx('custom_number')
    i_rsubj, i_cap, i_pre, i_note = idx('room_subject'), idx('capacity'), idx('room_prefix'), idx('room_note')
    if all(x is None for x in (i_no, i_name, i_exam, i_room, i_loc, i_cap)):
        return [], [{'line': sheet['header_row'], 'no': '',
                     'reason': '未识别到任何可用列（学号/姓名/考号/考场/位置/容量）'}]

    rows, errors = [], []
    for lineno, cells in sheet['rows']:
        if lineno <= sheet['header_row']:
            continue
        if len(rows) >= MAX_SCAN_ROWS:
            errors.append({'line': lineno, 'no': '', 'reason': '超出单表行数上限，已截断'})
            break
        get = lambda i: (cells[i] if i is not None and i < len(cells) else '')  # noqa: E731
        no = _clean_bad(get(i_no))
        name = _clean_bad(get(i_name))
        exam_no = _clean_bad(get(i_exam))
        room = _clean_bad(get(i_room))
        seat = _clean_bad(get(i_seat))
        rs = _clean_bad(get(i_rs))
        loc = _clean_bad(get(i_loc))
        cap = _num(get(i_cap))
        if rs:
            parts = rs.split('-')
            if not room:
                room = parts[0].strip()
            if not seat and len(parts) > 1:
                seat = parts[-1].strip()
        if not (no or name or exam_no or room or loc or cap):
            continue                       # 空行/表尾说明行
        att = _clean_bad(get(i_att))
        rows.append({'line': lineno, 'no': no, 'name': name,
                     'class_name': _clean_bad(get(i_cls)),
                     'exam_no': exam_no, 'room': room, 'seat': seat,
                     'selection': _clean_bad(get(i_sel)),
                     'subject': _clean_bad(get(i_subj)),
                     'attend': (att != '否') if att else True,
                     'fixed_room': _clean_bad(get(i_fr)),
                     'fixed_seat': _clean_bad(get(i_fs)),
                     'custom_number': _clean_bad(get(i_cn)),
                     'room_loc': loc,
                     'room_subject': _clean_bad(get(i_rsubj)),
                     'capacity': int(cap) if cap else None,
                     'room_prefix': _clean_bad(get(i_pre)),
                     'note': _clean_bad(get(i_note))})
    return rows, errors


def build_affair_ai_prompt(probe_data, affair):
    """考务列映射提示词（可选 AI 路径）"""
    parts = []
    for sh in probe_data['sheets'][:6]:
        parts.append('【工作表：%s（表头在第 %s 行%s）】\n表头：%s\n样本：%s'
                     % (sh['name'], sh.get('header_row'),
                        ('，已合并多行表头' if len(sh.get('header_rows') or []) > 1 else ''),
                        ' | '.join(sh['headers'][:80]),
                        json.dumps(sh['sample'], ensure_ascii=False)[:900]))
    system = (
        '你是考试考务表结构识别助手（学生信息表 / 考场信息表 / 编排结果）。\n'
        '用户给你 Excel 的表头与样本，判断每列对应什么字段。\n'
        '只输出一个 JSON，不要解释，格式：\n'
        '{"sheets":[{"name":"表名","fields":{"no":"学号","name":"姓名","class_name":"班级",'
        '"selection":"选科","subject":"考试科目","attend":"是否参加",'
        '"fixed_room":"固定考场","fixed_seat":"固定座号","custom_number":"自定义考号",'
        '"room":"考场","seat":"座号","exam_no":"考号","room_seat":"考场-座号",'
        '"room_loc":"考场位置","room_subject":"考场科目","capacity":"考场容量",'
        '"room_prefix":"考号前缀","room_note":"备注"}}],"notes":"补充说明"}\n'
        '规则：值必须是原表头里的列名原文（多行表头已合并为「父·子」）；找不到就不要出现；'
        '「考场-座号」这种合并列用 room_seat；名次/人数/统计类列不要映射。'
    )
    user = ('考务批次：年级=%s，名称=%s。\n%s'
            % (affair.grade if affair else '', affair.name if affair else '',
               '\n\n'.join(parts)))
    return system, user


# ==================== 各科分数线（→ 考试分层）（v1.19.1） ====================

def _direction_of(text):
    """从块标题里取方向（物理/历史）"""
    t = text or ''
    if '物理' in t or t.strip().startswith('理'):
        return '物理'
    if '历史' in t or t.strip().startswith('文'):
        return '历史'
    return ''


def looks_like_band_sheet(headers):
    """像「各科分数线」表：≥2 个以『线』结尾的列，且不含学号/姓名/考号"""
    labels = []
    for h in headers:
        if not h:
            continue
        _p, c = _split_label(h)
        labels.append(c or h)
    n_line = sum(1 for L in labels if L.endswith('线'))
    if n_line < 2:
        return False
    if any(('学号' in L) or ('姓名' in L) or ('考号' in L) for L in labels):
        return False
    return True


def classify_band_headers(headers):
    """把分数线表头拆成若干「块」（每块一个方向）：

    [{'direction': '物理'|'历史'|'', 'subject_col': 列号, 'bands': [(线名, 列号)]}]
    兼容：无块标题（单块）时方向为 ''；块标题形如「总分(物理)」。
    """
    blocks, order = {}, []
    for j, h in enumerate(headers):
        if not h:
            continue
        parent, child = _split_label(h)
        label = child or parent
        direction = _direction_of(parent) if child else ''
        if direction not in blocks:
            blocks[direction] = {'direction': direction, 'subject_col': None, 'bands': []}
            order.append(direction)
        b = blocks[direction]
        if '科目' in label or '学科' in label:
            b['subject_col'] = j
        elif label.endswith('线'):
            b['bands'].append((label, j))
    out = []
    for d in order:
        b = blocks[d]
        if not b['bands']:
            continue
        if b['subject_col'] is None:                 # 没有「科目」列：取最左侧分数线列的左边一列
            b['subject_col'] = max(0, min(j for _n, j in b['bands']) - 1)
        out.append(b)
    return out


def parse_bands(sheet):
    """解析分数线表 → ({(方向, 科目): [(线名, 分数)]}, errors)

    线名按文件里的列顺序；分值为空/≤0 视为「未划线」，跳过。
    """
    blocks = classify_band_headers(sheet['headers'])
    if not blocks:
        return {}, [{'line': sheet['header_row'], 'no': '',
                     'reason': '未识别到分数线列（需含「科目」列与以「线」结尾的列）'}]
    res = {}
    for lineno, cells in sheet['rows']:
        if lineno <= sheet['header_row']:
            continue
        for b in blocks:
            sc = b['subject_col']
            raw_sub = _clean_bad(cells[sc]) if (sc is not None and sc < len(cells)) else ''
            if not raw_sub:
                continue
            subject = TOTAL_SUBJECT if raw_sub in ('总分', '总成绩') else SUBJ_ALIAS.get(raw_sub, raw_sub)
            lst = res.setdefault((b['direction'], subject), [])
            for name, j in b['bands']:
                v = _num(cells[j] if j < len(cells) else None)
                if v is None or v <= 0:
                    continue
                lst.append((name, v))
    res = {k: v for k, v in res.items() if v}
    if not res:
        return {}, [{'line': sheet['header_row'], 'no': '', 'reason': '分数线表里没有有效数值'}]
    return res, []


def guess_sheet_kind(headers, rows=None):
    """猜一张工作表的用途（成绩页/考务页共用）：

    'bands' 各科分数线 ｜ 'scores' 成绩 ｜ 'exam_schedule' 考试时间表/监考表（划分场次）｜
    'affair_students' 考生安排 ｜ 'affair_rooms' 考场位置 ｜ 'skip' 不使用

    rows（可选）传入该表前若干行，用于识别「监考表 / 考试时间表」——
    这类表的时间/科目是**数据行上方的多级表头**，只看合并后的列名会漏判，
    因此必须结合行内容。
    """
    if looks_like_band_sheet(headers):
        return 'bands'
    # v1.19.47.4：监考表/时间表优先于考务判定（它同样有「考场/人数」列，容易误判成考场表）。
    # 但**含学号/姓名/考号列的表一定不是时间表**（是成绩表或名单），据此排除误判。
    if rows and looks_like_schedule_sheet(rows):
        _af0 = classify_affair_headers(headers)
        if not any(k in _af0 for k in ('no', 'name', 'exam_no')):
            return SCHEDULE_KIND
    sc = classify_headers(headers)
    if sc['subjects'] or sc['raw_subjects']:
        return 'scores'
    af = classify_affair_headers(headers)
    has_id = any(k in af for k in ('no', 'name', 'exam_no'))
    if not has_id and any(k in af for k in ('room', 'room_loc', 'capacity', 'room_prefix')):
        return 'affair_rooms'
    if has_id:
        return 'affair_students'
    return 'skip'


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


# ==================== 考试时间表 / 监考表 → 划分场次（v1.19.47.4） ====================
#
# 背景（用户口径 2026-10-12）：AI 导入页要能识别**图片**或**监考表**，
# 目的是**划分场次**（写入环节⑤ affair_schedules）。两种来源：
#   1. 图片（考试时间表 / 监考表照片）→ 交视觉模型识图，只输出**场次**
#      （日期/开始/结束/科目），全量落库由确定性代码做；
#   2. Excel 监考表 → **多级表头**（日期行 / 时间行 / 科目行）+ 数据行（考场 + 监考老师），
#      规则解析出场次，顺手把考场与监考安排也带出来（有则写环节⑥）。
#
# 设计原则与成绩导入一致：AI 只做「识图/判表」这件小事，结构化落库由代码负责。

# 场次用途的 kind 标识（前端下拉里与成绩/考务/分数线并列）
SCHEDULE_KIND = 'exam_schedule'
SCHEDULE_KIND_LABEL = '考试时间表 / 监考表（划分场次）'

# 时间区间：8:05-10:35 / 8：05—10：35 / 8:05~10:35 / 8:05至10:35
_TIME_RANGE_RE = re.compile(
    r'(\d{1,2})\s*[:：]\s*(\d{2})\s*[-–—~～至到]\s*(\d{1,2})\s*[:：]\s*(\d{2})')
# 单个时间点（用于「开始时间 / 结束时间」两列式时间表）
_TIME_ONE_RE = re.compile(r'(\d{1,2})\s*[:：]\s*(\d{2})')
# 日期：2026年10月8日 / 10月8日 / 10月08日
_DATE_CN_RE = re.compile(r'(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日')


def _norm_time(h, m):
    return '%02d:%02d' % (int(h), int(m))


def _valid_time(h, m):
    """校验时/分范围后格式化；非法返回 ''（正则本身不限制范围，"25:99" 也能匹配）"""
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return ''
    return _norm_time(h, m)


def _fmt_date(y, m, d):
    """校验并格式化日期；非法（月/日越界、2026-13-45 这类）返回 ''

    2026-10-12 审查修复 C：原实现直接 `'%04d-%02d-%02d' % (y,m,d)` 不做范围校验，
    于是 "2026-13-45" 会被原样返回，随后 `date.fromisoformat()` 抛 ValueError
    → 整个 apply 500（且该调用在写库 try 块之外）。这里统一用 date() 构造做校验。
    """
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except (ValueError, TypeError):
        return ''


def normalize_date(text, default_year=None):
    """'2026年10月8日' / '10月8日' / '2026-10-08' / '10/8' → 'YYYY-MM-DD'；识别不出或非法返回 ''"""
    t = _s(text)
    if not t:
        return ''
    m = _DATE_CN_RE.search(t)
    if m:
        y = int(m.group(1)) if m.group(1) else (default_year or date.today().year)
        return _fmt_date(y, m.group(2), m.group(3))
    s = t.replace('/', '-').replace('.', '-')
    m2 = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m2:
        return _fmt_date(m2.group(1), m2.group(2), m2.group(3))
    m3 = re.search(r'(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)', s)
    if m3:
        return _fmt_date(default_year or date.today().year, m3.group(1), m3.group(2))
    return ''


def normalize_time(text):
    """'8:05' / '08：05' → 'HH:MM'；识别不出或非法（如 25:99）返回 ''

    2026-10-12 审查修复 C：原实现不校验时/分范围，"25:99" 会被原样保留入库
    并进入监考表导出。这里做 0-23 / 0-59 校验。
    """
    m = _TIME_ONE_RE.search(_s(text))
    if not m:
        return ''
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return ''
    return _norm_time(h, mi)


def normalize_subject(text, strict=False):
    """科目归一：英语→外语、去空白与换行。

    strict=True 时只保留系统内合法科目（非科目返回 ''）；否则原样保留
    （监考表里可能出现「物理/历史」这类合并写法，保留原文供人工核对）。
    """
    t = _s(text).split('\n')[0].strip()
    t = re.sub(r'\s+', '', t)
    t = SUBJ_ALIAS.get(t, t)
    if strict:
        return t if t in SUBJECT_SET else ''
    return t


def normalize_schedule_subject(text):
    """场次科目归一：按分隔符拆开 → 别名映射 → 用「、」重新连接。

    2026-10-12 审查修复 J③：场次科目写库时若直接过 sanitize_label，会把
    「物理/历史」的斜杠剔除成「物理历史」，于是环节⑥ 的 `_split_subjects`
    （按 `[、，,/\\s]+` 拆分）拆不出，**同科回避对该场次彻底失效**。
    这里统一成白名单内允许的「、」连接，既保留多科语义又能被正确拆分。
    """
    parts = [p for p in re.split(r'[、，,/\s]+', _s(text)) if p]
    parts = [SUBJ_ALIAS.get(p, p) for p in parts]
    return '、'.join(parts)


def looks_like_schedule_sheet(rows):
    """像「考试时间表 / 监考表」吗？

    rows: [(lineno, cells)]（probe 给的前 ~23 行即可）
    三类特征（收紧口径，避免误判成绩表）：
      ① 出现时间区间 且（表里有「监考/考场」）→ 监考表；
      ② 出现时间区间 且（同时有「科目/学科」与「时间」）→ 带时间行的时间表；
      ③ 有「科目/学科」+「开始」+（「结束」或「日期」）→ 两列式考试时间表（无区间）。
    """
    blob = ' '.join(c or '' for _ln, cells in rows[:4] for c in cells)
    has_subj = ('科目' in blob or '学科' in blob)
    if has_subj and '开始' in blob and ('结束' in blob or '日期' in blob):
        return True
    saw_time = False
    for _lineno, cells in rows[:12]:
        if any(_TIME_RANGE_RE.search(c or '') for c in cells):
            saw_time = True
            break
    if not saw_time:
        return False
    if '监考' in blob or '考场' in blob:
        return True
    return has_subj and '时间' in blob


def _right_fill(cells, j):
    """合并单元格在 openpyxl 里只有左上格有值：本行取 j 及左侧最近的非空值"""
    for k in range(min(j, len(cells) - 1), -1, -1):
        v = _s(cells[k]) if k < len(cells) else ''
        if v:
            return v
    return ''


def parse_schedule_sheet(rows, default_year=None):
    """从「监考表 / 考试时间表」的行数据里提取**场次**（以及可能的考场与监考安排）。

    rows: [(lineno, cells)] 全量（含表头上方的标题行、主考行）
    返回 dict：
      sessions: [{date, start, end, subject, selection}]   # 场次（按列顺序）
      invigs:   [{session_index, room_no, slot, name}]      # 监考安排（无监考列时为空）
      rooms:    [{room, room_loc, capacity}]                # 考场（无考场列时为空）
      header_row: 识别到的表头结束行（用于前端展示）
      errors:   [str]
    """
    errs = []
    cells_by_line = {ln: cells for ln, cells in rows}
    ncol = 0
    for _ln, cells in rows:
        ncol = max(ncol, len(cells))
    if ncol == 0:
        return {'sessions': [], 'invigs': [], 'rooms': [], 'header_row': 0,
                'errors': ['表格为空']}

    def cell(ln, j):
        cells = cells_by_line.get(ln) or []
        return _s(cells[j]) if j < len(cells) else ''

    # ---- 1. 找「时间行」：前 12 行里含时间区间最多的一行 ----
    time_row, best = None, 0
    for ln, cells in rows[:12]:
        hit = sum(1 for c in cells if _TIME_RANGE_RE.search(c or ''))
        # 同一格塞多个时间（如「14:30-15:45\n15:50-17:05」）算多次
        hit = sum(len(_TIME_RANGE_RE.findall(c or '')) for c in cells)
        if hit and hit > best:
            time_row, best = ln, hit
    if time_row is None:
        return {'sessions': [], 'invigs': [], 'rooms': [], 'header_row': 0,
                'errors': ['未找到「时间段」（形如 8:05-10:35）的行，'
                           '若这是两列式时间表（开始时间/结束时间），请用 Excel 模板导入']}

    # ---- 2. 找「日期行」：时间行上方最近含「月…日」的行 ----
    date_row = None
    for ln, cells in reversed([(ln, c) for ln, c in rows if ln < time_row]):
        if any(_DATE_CN_RE.search(c or '') for c in cells):
            date_row = ln
            break

    # ---- 3. 找「科目行」：时间行本身或其后 1~2 行里，命中已知科目最多的行 ----
    subject_row = None
    best_s = 0
    for ln in (time_row + 1, time_row + 2, time_row):
        if ln not in cells_by_line:
            continue
        cells = cells_by_line[ln]
        hit = sum(1 for c in cells if normalize_subject(c, strict=True))
        if hit and hit > best_s:
            subject_row, best_s = ln, hit

    # ---- 4. 场次列 = 时间行里含时间区间的列 ----
    session_cols = []
    for j in range(ncol):
        m = _TIME_RANGE_RE.search(cell(time_row, j))
        if m:
            session_cols.append(j)
    if not session_cols:
        return {'sessions': [], 'invigs': [], 'rooms': [], 'header_row': time_row,
                'errors': ['时间段行里没有可解析的时间区间']}

    sessions, col_to_sidx = [], {}
    for j in session_cols:
        t = cell(time_row, j)
        m = _TIME_RANGE_RE.search(t)
        d = normalize_date(_right_fill(cells_by_line.get(date_row, []), j), default_year) \
            if date_row else ''
        subj = ''
        if subject_row is not None:
            subj = normalize_subject(cell(subject_row, j))
        # 「上午 / 下午 / 四选二」是场次说明，不是科目
        if subj in ('上午', '下午', '四选二', '上下午'):
            subj = ''
        # 科目行没有时，试从「时间行上方的父级表头」里捞科目（部分表在上午/下午格写科目）
        if not subj:
            for ln in (time_row - 1, time_row - 2):
                cand = normalize_subject(cell(ln, j), strict=True)
                if cand:
                    subj = cand
                    break
        # 合并写法（物理/历史）统一为「、」连接，保证环节⑥ 同科回避能拆开
        subj = normalize_schedule_subject(subj)
        col_to_sidx[j] = len(sessions)
        sessions.append({'date': d,
                         'start': _valid_time(m.group(1), m.group(2)),
                         'end': _valid_time(m.group(3), m.group(4)),
                         'subject': subj, 'selection': ''})
    if not any(s['date'] for s in sessions):
        errs.append('未能确定场次日期，请导入后手工补全')

    # ---- 5. 找「考场号 / 位置 / 人数」列（表头区里含关键词的列） ----
    def find_col(*keys, rows_limit=None):
        rng = [c for _ln, c in rows if (rows_limit is None or _ln <= rows_limit)]
        for cells in rng:
            for j, c in enumerate(cells):
                t = _s(c)
                if any(k in t for k in keys):
                    return j
        return None

    c_room = find_col('考场号', '考场编号', '试室号', rows_limit=time_row)
    c_loc = find_col('考场位置', '教室', '地点', rows_limit=time_row)
    c_cap = find_col('人数', '容量', '参考人数', rows_limit=time_row)
    if c_room is None:
        # 兜底：数据行里连续两三位数字的列（考场号最常见）
        for j in range(ncol):
            vals = [cell(ln, j) for ln, _c in rows if ln > time_row]
            nums = [v for v in vals if re.match(r'^\d{1,3}$', v)]
            if len(nums) >= 2:
                c_room = j
                break

    header_row = max([x for x in (time_row, subject_row) if x] or [time_row])

    # ---- 6. 数据行 → 考场 + 监考老师 ----
    rooms, invigs = [], []
    if c_room is not None:
        for ln, cells in rows:
            if ln <= time_row:
                continue
            room_no = _s(cells[c_room]) if c_room < len(cells) else ''
            if not re.match(r'^\d{1,3}$', room_no):
                continue
            loc = cell(ln, c_loc) if c_loc is not None else ''
            cap = _num(cells[c_cap]) if (c_cap is not None and c_cap < len(cells)) else None
            rooms.append({'room': room_no.zfill(2) if len(room_no) < 2 else room_no,
                          'room_loc': loc, 'capacity': int(cap) if cap else None})
            for j in session_cols:
                raw = cell(ln, j)
                if not raw:
                    continue
                # 一格可能写多名监考老师（空格/顿号/斜杠分隔）→ 依次占 slot 1/2/3
                names = [x for x in re.split(r'[\s、,，/]+', raw) if len(x) >= 2]
                for si, nm in enumerate(names[:3], start=1):
                    invigs.append({'session_index': col_to_sidx[j],
                                   'room_no': rooms[-1]['room'], 'slot': si, 'name': nm})
    return {'sessions': sessions, 'invigs': invigs, 'rooms': rooms,
            'header_row': header_row, 'errors': errs}


# ---------- 图片路径：识图只输出场次 ----------

SCHEDULE_IMAGE_SYSTEM = (
    '你是考试时间表识别助手。用户上传一张「考试时间表 / 监考表 / 学情分析表」图片，\n'
    '请提取其中的**考试场次**（每场：日期、开始时间、结束时间、考试科目）。\n'
    '只输出一个 JSON，不要解释，格式：\n'
    '{"sessions":[{"date":"2026-10-08","start":"08:05","end":"10:35",'
    '"subject":"语文","selection":""}],"notes":"补充说明"}\n'
    '规则：\n'
    '1. 日期统一 YYYY-MM-DD；图片只有「月日」时，年份用 {year}。\n'
    '2. 时间统一 HH:MM（24 小时制）。\n'
    '3. 科目写系统全称（英语→外语）；同一时段考多科（如"物理/历史"同时进行）'
    '写成一条，科目用 / 连接。\n'
    '4.「上午 / 下午」「四选二」「间隔10分钟不休息」这类场次说明不要当科目；'
    '辨认不出的字段留空字符串。\n'
    '5. 只列**考试场次**，不要列监考老师、不要列教学进度/版本。'
)


def build_schedule_image_prompt(exam):
    return SCHEDULE_IMAGE_SYSTEM.format(year=(exam.exam_date.year if exam and exam.exam_date
                                              else date.today().year))


def parse_schedule_json(text):
    """容错解析识图返回的 JSON → (sessions, notes)

    兼容：```json 代码块包裹 / 前后有解释文字 / 直接给数组。
    """
    raw = _s(text)
    if not raw:
        return [], ''
    # 去掉 ```json ... ``` 包裹
    raw = re.sub(r'^```[a-zA-Z]*\s*|\s*```$', '', raw.strip())
    obj = None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # 前后有解释文字时：先尝试数组、再尝试对象
        # （顺序不能颠倒 —— `\{[\s\S]*\}` 是贪婪的，会把数组里的单个对象抠出来）
        for pat in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
            m = re.search(pat, raw)
            if not m:
                continue
            try:
                obj = json.loads(m.group(0))
                break
            except (json.JSONDecodeError, TypeError):
                obj = None
    if isinstance(obj, list):
        obj = {'sessions': obj}
    elif isinstance(obj, dict) and 'sessions' not in obj and \
            ('date' in obj or '日期' in obj or 'subject' in obj or '科目' in obj):
        # 模型把「单场」直接给了对象（没包在 sessions 里）
        obj = {'sessions': [obj]}
    if not isinstance(obj, dict):
        return [], ''
    sess_in = obj.get('sessions') or obj.get('场次') or []
    out = []
    for s in sess_in:
        if not isinstance(s, dict):
            continue
        d = normalize_date(s.get('date') or s.get('日期'))
        start = normalize_time(s.get('start') or s.get('开始时间') or s.get('开始'))
        end = normalize_time(s.get('end') or s.get('结束时间') or s.get('结束'))
        subj = normalize_subject(s.get('subject') or s.get('科目'))
        sel = _s(s.get('selection') or s.get('选科'))
        if not (d or start or subj):
            continue
        out.append({'date': d, 'start': start, 'end': end, 'subject': subj, 'selection': sel})
    return out, _s(obj.get('notes') or obj.get('notes_zh') or '')


def parse_schedule_columns(sheet, mapping=None):
    """两列式「考试时间表」（日期 / 开始时间 / 结束时间 / 科目）→ 场次

    2026-10-12：**自己定位表头行**，不依赖 probe 的 header_row ——
    这类表的数据行常有「日期/时间」等含数字的文本，probe 会把它们误并进表头
    （合成时间表实测 header_row 被判成第 3 行，导致前两场丢失）。
    定位规则：第一行同时出现「科目/学科」与「时间/日期」关键词的行即表头。
    """
    rows = sheet.get('rows') or []
    mapping = mapping or {}
    hdr_ln, hdr_cells = None, []
    for ln, cells in rows[:12]:
        joined = ' '.join(cells)
        if ('科目' in joined or '学科' in joined) and ('时间' in joined or '日期' in joined):
            hdr_ln, hdr_cells = ln, cells
            break
    if hdr_ln is None:
        return [], [{'line': 0, 'no': '',
                     'reason': '未找到表头行（需同时含「科目」与「时间/日期」列）'}]

    def idx(*names):
        for nm in names:                     # 优先用调用方给的映射（列名原文）
            if mapping.get(nm):
                for j, c in enumerate(hdr_cells):
                    if _s(c) == _s(mapping[nm]):
                        return j
        for j, c in enumerate(hdr_cells):
            if any(nm in _s(c) for nm in names):
                return j
        return None

    i_date = idx('日期')
    i_start = idx('开始')
    i_end = idx('结束')
    i_subj = idx('科目', '学科')
    i_sel = idx('选科', '组合', '分场')
    if i_subj is None or (i_start is None and i_date is None):
        return [], [{'line': hdr_ln, 'no': '',
                     'reason': '未识别到「考试科目」与「开始/结束时间」或「日期」列'}]
    sessions, errs = [], []
    for lineno, cells in rows:
        if lineno <= hdr_ln:
            continue
        get = lambda i: (_s(cells[i]) if (i is not None and i < len(cells)) else '')  # noqa: E731
        subj = normalize_subject(get(i_subj))
        start = normalize_time(get(i_start))
        end = normalize_time(get(i_end))
        d = normalize_date(get(i_date))
        if not (subj or start):
            continue
        if not subj:
            errs.append({'line': lineno, 'no': '', 'reason': '缺少考试科目，已跳过'})
            continue
        sessions.append({'date': d, 'start': start, 'end': end,
                         'subject': subj, 'selection': _s(get(i_sel))})
    if not sessions:
        errs.append({'line': hdr_ln, 'no': '', 'reason': '没有解析出任何场次'})
    return sessions, errs

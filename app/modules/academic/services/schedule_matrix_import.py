# StuLink v1.18.9.2 2026-10-10
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学校原样课表导入（矩阵式，2026-09-26）。

教务手上的课表几乎都不是"一行一节课"的长表，而是**贴墙那种矩阵**：

    ┌────────┬──────┬──────┬──────┬──────┬──────┐
    │  节次  │ 周一 │ 周二 │ 周三 │ 周四 │ 周五 │
    ├────────┼──────┼──────┼──────┼──────┼──────┤
    │  早读  │ 语文 │ 英语 │ 语文 │ 英语 │ 语文 │
    │  第1节 │ 数学 │ 语文 │ 数学 │ 语文 │ 数学 │
    │        │ 张伟 │ 李娜 │ 张伟 │ 李娜 │ 张伟 │   ← 格内两行：学科 + 教师
    └────────┴──────┴──────┴──────┴──────┴──────┘

本模块把它**直接读进来**，不再要求老师转成 `年级|班级|星期|节次|学科` 长表：

- 自动找表头行（含 ≥3 个星期名的行）、自动定位节次列；
- 自动从 **sheet 名 / 表格上方标题** 识别班级（如「高三1班」「2024级01班」「01班」），
  年级与学期里的年级自动对齐（高三 → 2024级 这类由 grade_utils 推算）；
- 格内支持 `学科`、`学科+换行+教师`、`学科 教师`、`学科(教师)`、`学科/教师`；
- 学科别名归一（语→语文、数→数学、外语→英语、思品→政治…）；
- 教师按姓名自动匹配教师名单（含"张老师"→"张伟"这种去掉后缀的写法）；
- 单双周标注 `语文(单周)`、空值 `— / 无` 都能处理；
- 解析只出计划，**不写库**；预览确认后才由 batch_add_entries 写入并报冲突。

一个文件可以放多张表/sheet（每班一张），一次性导入整校课表。

**支持两种版式（自动识别，不用手选）**：

- 版式 A「整班矩阵」：一个 sheet 一个班，行=节次、列=周一~周五；
- 版式 B「全校总课表」（2026-10-10 新增）：一张 sheet 放全校，
  行=星期 × 节次、列=班级（每列一个班），学校教务手上的总课表通常就是这个样子。
  识别出后按列拆成"一个班一条结果"，预览与写入流程和版式 A 完全一致。
"""
import io
import re

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.utils.export_helpers import xl_safe

# 星期识别（列头）
_WEEKDAY_TOKENS = [
    (('周一', '星期一', '礼拜一'), 1), (('周二', '星期二', '礼拜二'), 2),
    (('周三', '星期三', '礼拜三'), 3), (('周四', '星期四', '礼拜四'), 4),
    (('周五', '星期五', '礼拜五'), 5), (('周六', '星期六', '礼拜六'), 6),
    (('周日', '周天', '星期日', '星期天', '礼拜日', '礼拜天'), 7),
]

# 学科别名（教务常见简写/全称）
_SUBJECT_ALIAS = {
    '语': '语文', '语文': '语文', '语文学科': '语文', '阅读': '语文',
    '数': '数学', '数学': '数学', '数学学科': '数学',
    '英': '英语', '英语': '英语', '外语': '英语', '英语学科': '英语',
    '物': '物理', '物理': '物理', '物理学科': '物理',
    '化': '化学', '化学': '化学', '化学学科': '化学',
    '生': '生物', '生物': '生物', '生物学科': '生物',
    '政': '政治', '政治': '政治', '思想政治': '政治', '思品': '政治', '道法': '政治',
    '史': '历史', '历史': '历史', '历史学科': '历史',
    '地': '地理', '地理': '地理', '地理学科': '地理',
    '体': '体育', '体育': '体育', '体育与健康': '体育',
    '信': '信息技术', '信息': '信息技术', '信息技术': '信息技术',
    '通用技术': '通用技术', '劳技': '劳动', '劳动': '劳动',
    '音': '音乐', '音乐': '音乐', '美': '美术', '美术': '美术', '艺术': '艺术',
    '班': '班会', '班会': '班会', '班会课': '班会', '德育': '班会',
    '自': '自习', '自习': '自习', '晚自习': '晚自习', '早读': '早读', '晨读': '早读',
    '研究性学习': '研究性学习', '研学': '研究性学习', '心理': '心理健康',
    '心理健康': '心理健康', '社团': '社团活动', '社团活动': '社团活动',
    '升旗': '升旗仪式', '大课间': '大课间', '课间操': '大课间',
}
# 学科后面的考试/课程类型后缀：「生物学考」「语文选考」「数学必修」→ 取学科本体
_SUBJECT_SUFFIX = ('学考', '选考', '合格考', '高考', '会考', '必修', '选修', '模块',
                   '拓展', '基础')
_WEEKDAY_CN = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '日'}
_EMPTY_TOKENS = {'', '—', '-', '－', '/', '\\', '无', '空', '休息', '休', 'x', 'X',
                 '／', '.', '。', '待定', 'TBD'}
_CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8,
           '九': 9, '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14,
           '十五': 15, '十六': 16, '十七': 17, '十八': 18, '十九': 19, '二十': 20}


# ── 基础解析 ────────────────────────────────────────────────────────────────

def _clean(value):
    return re.sub(r'\s+', ' ', str(value if value is not None else '')).strip()


def weekday_of(token):
    """列头 → 星期（1-7）；认不出返回 None"""
    t = _clean(token).replace(' ', '')
    if not t:
        return None
    for names, num in _WEEKDAY_TOKENS:
        for n in names:
            if n in t:
                return num
    # 单字「一」~「日」或纯数字 1-7（仅在完全等同时接受，避免误伤"第1节"）
    if t in ('一', '二', '三', '四', '五', '六', '日', '天'):
        return {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
                '日': 7, '天': 7}[t]
    if t.isdigit() and 1 <= int(t) <= 7:
        return int(t)
    return None


def _cn_to_int(s):
    s = _clean(s)
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def period_of(token, periods_map):
    """节次单元格 → 节次号；periods_map 为 {节次名: 号}。

    关键：**先按系统的节次名匹配**，不要直接把"第1节"当成序号 1。
    高中默认作息里 1 号位是「早读」，学校表格里的「第1节」其实是 2 号位，
    直接取数字会让整张表整体错位一格。
    """
    t = _clean(token).replace(' ', '')
    if not t:
        return None
    if t in periods_map:                       # 表格与系统同名，直接用
        return periods_map[t]
    # 2026-10-10：放宽为「第」可省略 —— 学校的全校总课表里常直接写「1节」…「9节」
    m = re.search(r'第?\s*([0-9一二三四五六七八九十]{1,3})\s*节', t)
    if m:
        v = _cn_to_int(m.group(1))
        if v:
            if f'第{v}节' in periods_map:      # 「第一节」/「1节」→ 系统的「第1节」
                return periods_map[f'第{v}节']
            return v if 1 <= v <= 20 else None  # 系统没有该名称时按序号兜底
    if t.isdigit():
        v = int(t)
        return v if 1 <= v <= 20 else None
    # 「晚自习1」「早读」这类按名称包含匹配（取最长的名称，避免"自习"吃掉"晚自习"）
    for name in sorted((n for n in periods_map if n), key=len, reverse=True):
        if name and name in t:
            return periods_map[name]
    return None


def _pick_subject(token):
    """token → 规范学科名；认不出返回 None"""
    t = _clean(token).replace(' ', '')
    if not t or t in _EMPTY_TOKENS:
        return None
    if t in _SUBJECT_ALIAS:
        return _SUBJECT_ALIAS[t]
    # 2026-10-10：「生物学考」「语文选考」这类带考试类型的写法 → 取学科本体
    for suf in _SUBJECT_SUFFIX:
        if t.endswith(suf) and len(t) > len(suf):
            base = t[:-len(suf)]
            if base in _SUBJECT_ALIAS:
                return _SUBJECT_ALIAS[base]
    # 「语文（单周）」已被预处理，这里处理「语文*」这类带修饰的
    for key in sorted(_SUBJECT_ALIAS, key=len, reverse=True):
        if t.startswith(key) and len(t) <= len(key) + 1:
            return _SUBJECT_ALIAS[key]
    return None


def parse_cell_content(raw, default_week_range=None):
    """格内容 → [{subject, teacher_name, week_range, note, unknown}]。

    支持单段/多段（多段＝同一格多条，如单双周交替），返回列表。
    default_week_range（2026-10-10）：整批按周次导入时统一指定的周次
    （如「5」「1-9」「单周」），格内没有显式 (单周)/(3-5) 标注的课程用它；
    显式标注优先。
    """
    # 先留一份"保留换行"的原文：格内两行分别是学科/教师，_clean 会把换行压成空格，
    # 所以两行式解析必须用未压平的文本
    raw_text = str(raw if raw is not None else '').replace('\r\n', '\n').replace('\r', '\n')
    text = _clean(raw_text)
    if not text or text in _EMPTY_TOKENS:
        return []

    week_range = (default_week_range or '1-18')
    note = None
    m = re.search(r'[（(]\s*(单周|双周|\d{1,2}\s*-\s*\d{1,2}|第?\d{1,2}周)\s*[)）]', text)
    if m:
        wr = m.group(1).replace(' ', '')
        if wr in ('单周', '双周'):
            week_range = wr
        elif re.match(r'^第?\d{1,2}周$', wr):
            wk = int(re.sub(r'\D', '', wr))
            week_range = f'{wk}'
        else:
            week_range = wr
        text = text.replace(m.group(0), ' ')
        raw_text = raw_text.replace(m.group(0), ' ')

    # 「学科并列 + 教师并列」两行写法（并行课/走班常见）：
    #     地/政
    #     陈/邓      → 地理·陈 + 政治·邓 两条，别把第二个学科吞掉
    _lines = [p for p in re.split(r'[\n\r]+', raw_text) if _clean(p)]
    if len(_lines) == 2:
        _subs = [_pick_subject(x) for x in
                 re.split(r'[\s/、,，]+', _clean(_lines[0])) if x]
        _teach = [x for x in re.split(r'[\s/、,，]+', _clean(_lines[1])) if x]
        if (len(_subs) > 1 and len(_subs) == len(_teach) and all(_subs)
                and not any(_pick_subject(x) for x in _teach)):
            return [{'subject': s, 'teacher_name': t, 'week_range': week_range,
                     'note': note, 'unknown': False}
                    for s, t in zip(_subs, _teach)]

    # 一格内两门课（极简写法「语文/数学」通常指合班，这里按多段处理）
    parts = [p for p in re.split(r'[\n\r]+', text) if _clean(p)]
    # 块内再按空白切成 token，用于区分"学科"与"教师"
    out = []
    for part in parts:
        tokens = [x for x in re.split(r'[\s/、,，]+', _clean(part)) if x]
        # 括号里的名字（语文(张伟)）
        paren = re.findall(r'[（(]([^）)]{1,12})[)）]', part)
        if paren:
            tokens += [p for p in paren if _pick_subject(p) is None]
        subject = None
        teacher = None
        unknown = False
        for tk in tokens:
            if tk in _EMPTY_TOKENS:
                continue
            if subject is None:
                s = _pick_subject(tk)
                if s:
                    subject = s
                    continue
            if teacher is None and len(tk) <= 12 and _pick_subject(tk) is None:
                teacher = re.sub(r'(老师|教师)$', '', tk).strip() or None
        if subject is None:
            # 认不出的学科（心理健康、日语…）保留原文，标记 unknown 供人工确认
            subject = tokens[0] if tokens else None
            teacher = teacher or (tokens[1] if len(tokens) > 1 else None)
            unknown = bool(subject)
        if subject:
            out.append({'subject': subject, 'teacher_name': teacher,
                        'week_range': week_range, 'note': note,
                        'unknown': unknown})
    return out


def parse_class_from_text(text, grades):
    """从 sheet 名/标题行识别 (年级, 班级)。

    支持：「高三1班」「高三(1)班」「2024级01班」「高一（3）班 课表」「01班」。
    年级会与学期里的年级对齐（高三 → 2024级 由 grade_utils 推算）。
    """
    from app.modules.academic.services.grade_utils import grade_labels

    s = re.sub(r'\s+', '', _clean(text))
    if not s:
        return None, None

    labels = grade_labels(grades)                     # {'2024级': '高三(2024级)'}
    grade = None
    for g in grades:                                  # 直接命中「2024级」
        if g and g in s:
            grade = g
            s = s.replace(g, '')
            break
    if grade is None:
        short = {v.split('(')[0]: k for k, v in labels.items()}   # '高三' → '2024级'
        for kw in ('高三', '高二', '高一', '初中', '预科'):
            if kw in s and kw in short:
                grade = short[kw]
                s = s.replace(kw, '')
                break

    m = re.search(r'(\d{1,2})\s*班', s)
    if m:
        cls = f'{int(m.group(1)):02d}班'
    else:
        m2 = re.search(r'(\d{1,2})', s)
        cls = f'{int(m2.group(1)):02d}班' if m2 else (s or '01班')
    return grade, cls


def match_teacher(name, teachers_by_name):
    """姓名 → 教师记录；找不到返回 (None, None)。

    容错：去掉"老师"后缀、全半角空格、只写姓氏时不予匹配（宁缺勿错）。
    """
    if not name:
        return None, None
    key = re.sub(r'\s+', '', _clean(name))
    if key in teachers_by_name:
        t = teachers_by_name[key]
        return t.teacher_uid, t.name
    for full, t in teachers_by_name.items():
        if len(key) >= 2 and (full.startswith(key) or key == full):
            return t.teacher_uid, t.name
    return None, None


# ── 主解析：整本工作簿 ──────────────────────────────────────────────────────

def parse_school_workbook(stream, periods, grades, teachers, default_grade=None,
                          default_week_range=None):
    """解析学校原样课表 → {sheets: [...], errors: [...]}（只解析，不写库）。

    periods: PeriodDef 列表；grades: 学期年级；teachers: Teacher 列表。
    default_week_range（2026-10-10）：整批导入「某一周/某段周次」时统一指定
    （如按第 5 周批量导入：填 '5'）；格内显式标注（(单周)/(3-5)）优先。
    每个 sheet 的结果：
      {sheet, grade, class_name, entries, warnings, unknown_subjects, matched_teachers}
    """
    periods_map = {p.period_name: p.period_number for p in periods}
    teachers_by_name = {re.sub(r'\s+', '', t.name or ''): t for t in teachers if t.name}

    try:
        wb = load_workbook(stream, data_only=True)
    except Exception:  # noqa: BLE001
        return {'sheets': [], 'errors': [{'sheet': '', 'message': '无法解析 Excel 文件'}]}

    results = []
    errors = []
    for ws in wb.worksheets:
        if _sheet_is_empty(ws):
            continue         # 空 sheet（学校模板常带 Sheet2/Sheet3）直接忽略
        # 先按「整班矩阵」解析（行=节次、列=星期）；不是这种版式，再按
        # 「全校总课表」解析（行=星期×节次、列=班级）。两种版式都能直接上传。
        parsed = _parse_sheet(ws, periods_map, grades, teachers_by_name,
                              default_grade, default_week_range)
        if parsed is not None:
            results.append(parsed)
            continue
        multi = _parse_full_school_sheet(ws, periods_map, grades, teachers_by_name,
                                         default_grade, default_week_range)
        if multi:
            results.extend(multi)
            continue
        errors.append({'sheet': ws.title,
                       'message': '没找到可识别的表头（需含「周一…周五」星期行，'
                                  '或「星期 | 节次 | 班级…」的全校总课表表头），已跳过'})
    if not results and not errors:
        errors.append({'sheet': '', 'message': '工作簿里没有可解析的表'})
    return {'sheets': results, 'errors': errors}


def _sheet_is_empty(ws):
    """整张表没有任何内容（学校模板常带空白的 Sheet2/Sheet3）"""
    for row in ws.iter_rows(values_only=True):
        if any(_clean(c) for c in row):
            return False
    return True


def _find_header_row(rows, max_scan=15):
    """找星期表头行：能被识别为星期的单元格 ≥2 个（贴墙课表常见只到周五）。

    阈值取 2 而不是 3：有些表只排到周三/周四（如高三某阶段、教师个人表），
    阈值太高会把整张表当成"没有表头"跳过。
    """
    best = (-1, None)
    for ri, row in enumerate(rows[:max_scan]):
        hits = [i for i, cell in enumerate(row) if weekday_of(cell)]
        if len(hits) >= 2 and len(hits) > best[0]:
            best = (len(hits), ri)
    return best[1]


def _parse_sheet(ws, periods_map, grades, teachers_by_name, default_grade=None,
                 default_week_range=None):
    rows = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    if not rows:
        return None
    header_ri = _find_header_row(rows)
    if header_ri is None:
        return None

    header = rows[header_ri]
    wd_cols = {i: weekday_of(c) for i, c in enumerate(header)}
    wd_cols = {i: w for i, w in wd_cols.items() if w}
    first_wd_col = min(wd_cols)

    # 班级：sheet 名优先，其次表头上方的标题行
    title_text = ' '.join(_clean(c) for row in rows[:header_ri] for c in row if c)
    grade, class_name = parse_class_from_text(ws.title, grades)
    if not grade:
        g2, c2 = parse_class_from_text(title_text, grades)
        grade = grade or g2
        class_name = class_name if class_name != '01班' else c2
    grade = grade or default_grade
    class_name = class_name or '01班'

    warnings = []
    unknown_subjects = []
    matched = set()
    entries = []
    seen_slots = {}
    for ri in range(header_ri + 1, len(rows)):
        row = rows[ri]
        if not any(_clean(c) for c in row):
            continue
        # 节次列：星期列左侧第一个能解析出节次的单元格
        pn = None
        period_col = None
        for ci in range(0, first_wd_col if first_wd_col > 0 else 1):
            v = period_of(row[ci], periods_map)
            if v:
                pn = v
                period_col = ci
                break
        if not pn:
            continue                      # 不是节次行（可能是说明行），跳过
        for ci, wd in wd_cols.items():
            if ci >= len(row) or period_col is None:
                break
            raw = row[ci]
            for item in parse_cell_content(raw, default_week_range):
                key = (wd, pn, item['subject'])
                if key in seen_slots:
                    warnings.append(f'{ws.title}：第{pn}节周{wd} 出现重复「{item["subject"]}」')
                seen_slots[key] = True
                uid, tname = match_teacher(item['teacher_name'], teachers_by_name)
                if item['teacher_name'] and not uid:
                    warnings.append(
                        f'{ws.title}：第{pn}节周{wd} 教师「{item["teacher_name"]}」'
                        f'不在教师名单中（按姓名原样记录）')
                elif uid:
                    matched.add(tname)
                if item['unknown']:
                    unknown_subjects.append(item['subject'])
                entries.append({
                    'grade': grade, 'class_name': class_name,
                    'weekday': wd, 'period_number': pn,
                    'subject': item['subject'],
                    'teacher_uid': uid, 'teacher_name': tname or item['teacher_name'],
                    'room': None, 'week_range': item['week_range'],
                    'note': None,
                })

    if not entries:
        warnings.append('该表未解析到任何课程（请确认节次名称与学期作息一致）')
    return {
        'sheet': ws.title,
        'grade': grade or '',
        'class_name': class_name,
        'entries': entries,
        'warnings': warnings[:40],
        'unknown_subjects': sorted(set(unknown_subjects)),
        'matched_teachers': sorted(matched),
    }


# ── 模板：学校原样（矩阵式）示例 ─────────────────────────────────────────────

def build_school_template(periods, grade='高三', class_name='01班', sample=True,
                          week_range=None):
    """生成「整班导入」矩阵模板：行=节次、列=周一~周五、格内「学科 + 教师」。

    与老师手上的课表同形，可直接填写/粘贴；**一个 sheet = 一个班**，
    多班复制本 sheet、改标题与 sheet 名即可（导入页可整批指定周次）。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = f'{grade}{class_name}'
    thin = Side(style='thin', color='9AA5B1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill('solid', fgColor='1E293B')
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)

    title = f'{grade}{class_name} 课表'
    if week_range:
        title += f'（周次：{week_range}）'
    ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=13)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=7)
    ws.cell(row=1, column=1).alignment = center

    ws.cell(row=2, column=1, value='节次')
    for i, wd in enumerate(['周一', '周二', '周三', '周四', '周五', '周六', '周日']):
        ws.cell(row=2, column=2 + i, value=wd)
    for c in ws[2]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = head_fill
        c.alignment = center
        c.border = border

    demo = {1: ('语文', '张伟'), 2: ('数学', '李娜'), 3: ('英语', '王芳'),
            4: ('物理', '刘洋'), 5: ('化学', '陈静'), 6: ('生物', '赵磊'),
            7: ('政治', '孙倩'), 8: ('历史', '周敏'), 9: ('地理', '吴涛'),
            10: ('自习', ''),
            11: ('晚自习', ''), 12: ('晚自习', ''), 13: ('晚自习', '')}
    ri = 3
    for p in periods:
        name = p.period_name or f'第{p.period_number}节'
        ws.cell(row=ri, column=1, value=name)
        if sample:
            subj, teacher = demo.get(p.period_number, ('', ''))
            for i in range(5):
                val = f'{subj}\n{teacher}' if teacher else subj
                ws.cell(row=ri, column=2 + i, value=val)
        for col in range(1, 8):
            cell = ws.cell(row=ri, column=col)
            cell.border = border
            cell.alignment = center
        ri += 1
    ws.column_dimensions['A'].width = 12
    for i in range(2, 8):
        ws.column_dimensions[chr(64 + i)].width = 14

    tips = wb.create_sheet('填写说明')
    for i, line in enumerate([
        '把学校发的课表原样贴进来即可，不需要转成一行一节课的长表。',
        '· 【整班导入】一个 sheet = 一个班；多个班就复制本 sheet，'
        '改表头和 sheet 名即可（如「高三2班」）。',
        '· 表格上方标题或 sheet 名里写清班级（如「高三1班」「2024级01班」）。',
        '· 一格写「学科」即可；要带教师就换行写第二行，或用空格/斜杠分隔。',
        '· 支持简写：语/数/英/物/化/生/政/史/地/体/音/美/班/自习。',
        '· 空着或写「—」「/」「无」表示没课。',
        '· 单双周课写「语文(单周)」「数学(双周)」。',
        '· 晚自习、早读直接写名称即可（需与学期的节次名称一致）。',
        '· 【周次】默认 1-18；只导某一周/某段周次，在导入页「周次」框里填'
        '（如 5、1-9、单周），格内有标注的以标注为准。',
        '· 一个文件可放多个 sheet（每班一张），一次导入整校课表。',
    ], 1):
        ws2 = tips.cell(row=i, column=1, value=xl_safe(line))
        ws2.alignment = Alignment(vertical='center')
    tips.column_dimensions['A'].width = 70

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ── 「全校总课表」版式：行 = 星期 × 节次，列 = 班级 ──────────────────────────

def _looks_like_class_label(value):
    """像不像班级名：「高三1班」「高三(1)班」「2024级01班」「1班」。"""
    t = re.sub(r'\s+', '', _clean(value))
    if not t or len(t) > 12:
        return False
    return bool(re.search(r'\d{1,3}\s*[)）]?\s*班', t))


def _grade_of_label(label, grades):
    """班级名 → (年级, 班级)。年级优先与学期年级对齐（高三 → 2024级），
    推不出来就保留原样叫法（如「高三」），不阻断导入。"""
    g, cls = parse_class_from_text(label, grades)
    if g:
        return g, cls
    m = re.search(r'(高三|高二|高一|初中|预科)', re.sub(r'\s+', '', _clean(label)))
    return (m.group(1) if m else None), cls


def _parse_full_school_sheet(ws, periods_map, grades, teachers_by_name,
                             default_grade=None, default_week_range=None):
    """解析「全校总课表」版式 → **每个班级一条结果**（list）；不是这种版式返回 None。

        ┌──────┬──────┬────────┬────────┬────────┐
        │ 星期 │ 节次 │ 高三1班 │ 高三2班 │ 高三3班 │
        ├──────┼──────┼────────┼────────┼────────┤
        │星期一│ 1节  │ 历史   │ 英语   │ 英语   │   ← 格内两行：学科 + 教师
        │      │      │ 郭艳静 │ 王一楠 │ 任润萌 │
        │      │ 2节  │ 语文   │ 语文   │ 物理   │
        └──────┴──────┴────────┴────────┴────────┘

    A 列星期是**合并单元格**（只写在当天第一行）→ 向下继承；节次列必须有值，
    没有节次的行（如「午休」空行）整行跳过。
    """
    rows = [[c for c in row] for row in ws.iter_rows(values_only=True)]
    if not rows:
        return None

    # 表头行 = 前 10 行里含 ≥2 个班级名的行
    header_ri = None
    for ri, row in enumerate(rows[:10]):
        if sum(1 for c in row if _looks_like_class_label(c)) >= 2:
            header_ri = ri
            break
    if header_ri is None:
        return None

    class_cols = {}
    for ci, cell in enumerate(rows[header_ri]):
        if _looks_like_class_label(cell):
            g, cls = _grade_of_label(cell, grades)
            class_cols[ci] = (g or default_grade or '', cls or '01班')
    if not class_cols:
        return None
    first_class_col = min(class_cols)

    buckets = {ci: [] for ci in class_cols}
    warns = {ci: [] for ci in class_cols}
    unknowns = {ci: [] for ci in class_cols}
    seen = {ci: {} for ci in class_cols}
    matched = set()
    skipped_rows = []
    last_wd = None

    for ri in range(header_ri + 1, len(rows)):
        row = rows[ri]
        if not any(_clean(c) for c in row):
            continue
        wd = None
        pn = None
        for ci in range(0, first_class_col):
            v = row[ci] if ci < len(row) else None
            if wd is None:
                wd = weekday_of(v)
            if pn is None:
                pn = period_of(v, periods_map)
        if wd is None:
            wd = last_wd                 # 合并单元格：星期向下继承
        else:
            last_wd = wd
        if not wd or not pn:
            if any(_clean(row[ci]) for ci in class_cols if ci < len(row)):
                skipped_rows.append(ri + 1)
            continue

        for ci, (grade, cls) in class_cols.items():
            raw = row[ci] if ci < len(row) else None
            _items = parse_cell_content(raw, default_week_range)
            if len(_items) > 1:
                warns[ci].append(
                    f'第{pn}节周{wd} 一格内写了 {len(_items)} 门课'
                    f'（{"、".join(i["subject"] for i in _items)}）——'
                    f'若是并行/分层课请改用「走班教学班」，若是单双周请在格内标注'
                    f'「(单周)/(双周)」，否则同班同时段只保留第一条')
            for item in _items:
                key = (wd, pn, item['subject'])
                if key in seen[ci]:
                    warns[ci].append(
                        f'第{pn}节周{wd} 出现重复「{item["subject"]}」')
                seen[ci][key] = True
                uid, tname = match_teacher(item['teacher_name'], teachers_by_name)
                if item['teacher_name'] and not uid:
                    warns[ci].append(
                        f'第{pn}节周{wd} 教师「{item["teacher_name"]}」'
                        f'不在教师名单中（按姓名原样记录）')
                elif uid:
                    matched.add(tname)
                if item['unknown']:
                    unknowns[ci].append(item['subject'])
                buckets[ci].append({
                    'grade': grade, 'class_name': cls,
                    'weekday': wd, 'period_number': pn,
                    'subject': item['subject'],
                    'teacher_uid': uid, 'teacher_name': tname or item['teacher_name'],
                    'room': None, 'week_range': item['week_range'],
                    'note': None,
                })

    out = []
    for ci, (grade, cls) in class_cols.items():
        w = warns[ci]
        if skipped_rows:
            w.append(f'第 {skipped_rows[0]} 行等 {len(skipped_rows)} 行有内容但'
                     f'节次未识别（节次需与学期作息一致），已跳过')
        if not buckets[ci]:
            w.append('该班未解析到课程（请确认节次名称与学期作息一致）')
        out.append({
            'sheet': f'{ws.title}·{grade}{cls}',
            'grade': grade, 'class_name': cls,
            'entries': buckets[ci],
            'warnings': w[:40],
            'unknown_subjects': sorted(set(unknowns[ci])),
            'matched_teachers': sorted(matched),
            'layout': 'full_school',
        })
    return out


def build_full_school_template(periods, classes=None, sample=True,
                               weekdays=(1, 2, 3, 4, 5), title=None):
    """生成「全校总课表」版式模板：行 = 星期 × 节次，列 = 班级（每列一个班）。

    classes: [(年级, 班级), ...]；缺省给 3 个示例班（高三1/2/3班）。
    sample=False 时格子全部留空（空白模板），只保留表头与节次行。
    与学校"贴墙的总课表"同形，教务可直接粘过来；表头里的班级名决定落库的年级/班级。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = '全校总课表'
    thin = Side(style='thin', color='9AA5B1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill('solid', fgColor='1E293B')
    day_fill = PatternFill('solid', fgColor='E2E8F0')
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left = Alignment(horizontal='left', vertical='center')

    classes = list(classes or [('高三', '01班'), ('高三', '02班'), ('高三', '03班')])

    ws.cell(row=1, column=1, value='星期')
    ws.cell(row=1, column=2, value='节次')
    for i, (g, c) in enumerate(classes):
        ws.cell(row=1, column=3 + i, value=f'{g}{c}')
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = head_fill
        c.alignment = center
        c.border = border

    demo = ['语文', '数学', '英语', '物理', '化学', '生物', '政治', '历史', '地理']
    ri = 2
    for wd in weekdays:
        start_ri = ri
        for p in periods:
            name = p.period_name or f'第{p.period_number}节'
            ws.cell(row=ri, column=1, value=f'星期{_WEEKDAY_CN.get(wd, wd)}')
            ws.cell(row=ri, column=2, value=name)
            if sample and (p.period_type or '') != 'break':
                for i in range(len(classes)):
                    subj = demo[(p.period_number + i) % len(demo)]
                    ws.cell(row=ri, column=3 + i, value=f'{subj}\n示例教师')
            for col in range(1, 3 + len(classes)):
                cell = ws.cell(row=ri, column=col)
                cell.border = border
                cell.alignment = center
            ws.cell(row=ri, column=1).fill = day_fill
            ri += 1
        if ri - start_ri > 1:                       # 同一天的星期名合并成一格
            ws.merge_cells(start_row=start_ri, start_column=1,
                           end_row=ri - 1, end_column=1)

    ws.column_dimensions['A'].width = 10
    ws.column_dimensions['B'].width = 10
    for i in range(len(classes)):
        ws.column_dimensions[ws.cell(row=1, column=3 + i).column_letter].width = 14
    ws.freeze_panes = 'C2'

    # ── 填写说明（含字段对照表）───────────────────────────────────────────
    tips = wb.create_sheet('填写说明')
    lines = [
        '【全校总课表】一份表导入全校：行 = 星期 × 节次，列 = 班级，格内 = 学科 + 换行 + 教师。',
        '· 第 1 行是表头：A 列写「星期」，B 列写「节次」，C 列起每列写一个班（如「高三1班」）。',
        '· 第 2 行起：每天先写星期（合并单元格即可，当天只写一次），再按节次逐行排。',
        '· 一个班一列；班级增减就加减列，不用改结构。',
        '· 格子第一行写学科、第二行写教师（也可写在同一行用空格或斜杠分隔）。',
        '· 只排到周五就只写到周五；周末排课再补周六/周日。',
        '',
        '─' * 30,
        '【字段对照表】',
        '位置\t对应系统字段\t类型 / 格式\t必填\t说明',
        '表头 C 列起\t年级 + 班级\t文本：年级 + 数字班号\t必填\t如「高三1班」「2024级01班」；'
        '年级会与学期年级自动对齐（高三 → 2024级）',
        'A 列\t星期（day_of_week）\t文本/合并单元格：星期一~星期日，或 周一/1\t必填\t'
        '可只写在当天第一行，向下继承',
        'B 列\t节次（period_number）\t文本：1节 / 第1节 / 早读 / 晚自习1\t必填\t'
        '须与「节次配置」里的名称对得上；「N节」自动对应「第N节」',
        '格子第 1 行\t学科（subject）\t文本，≤20 字\t必填\t支持简写：语/数/英/物/化/生/政/史/地/体/音/美/班/自习',
        '格子第 2 行\t教师（teacher_name）\t文本，≤50 字\t选填\t按姓名自动匹配教师名单；'
        '匹配不上会按原样记录并在预览里提示',
        '格内 (单周)/(双周)\t周次（week_range）\t文本：单周 / 双周 / 1-9 / 5\t选填\t'
        '不写＝按导入页「整批周次」，再缺省为 1-18',
        '格子「语文@301」\t教室（room）\t文本，≤30 字\t选填\t用 @ 或「（教室）」标注',
        '—\t走班教学班\t文本，≤30 字\t选填\t行政班课不填；走班课才填教学班名',
        '',
        '─' * 30,
        '【掉内容的常见原因】',
        '· 节次写成了系统里没有的名称（如学校叫「第9节」而学期只配到「第8节」）：'
        '先在「节次配置」里补齐，或改名后再导。',
        '· 格子里的学科认不出来（如「日语」）：会照原样入库，并在预览里列为「未识别学科」。',
        '· 同一天同一节同一班写了两门课：第二条会被跳过并在预览里提示。',
    ]
    for i, line in enumerate(lines, 1):
        cell = tips.cell(row=i, column=1, value=xl_safe(line))
        cell.alignment = left
        cell.font = Font(bold=line.startswith('【'))
    tips.column_dimensions['A'].width = 96

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

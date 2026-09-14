# StuLink v1.9.0 2026-09-03
# 成绩导入服务：Excel 解析（模板 A/B 识别 / 年级列校验 / 学号匹配主库 / 分批行集规范化）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import openpyxl
from app.models import Student, ClassProfile
from app.models.grades import SUBJECTS, TOTAL_SUBJECT
from app.modules.grades.utils import parse_grade_any

# 列名兼容映射
_NO_KEYS = ('学号', '学生学号')
_NAME_KEYS = ('姓名',)
_CLASS_KEYS = ('班级',)
_GRADE_KEYS = ('年级',)
_TOTAL_KEYS = ('总分', '总成绩')
# 科目别名：文件里常见的"英语"对齐字典"外语"
_SUBJECT_ALIAS = {'英语': '外语'}
# 识别后忽略的列（粘贴参考 Excel 整表常见）
_IGNORED_COLS = {'考号', '方向', '选科', '备注', '座位号'}

SCAN_HEADER_MAX_ROW = 15
MAX_IMPORT_ROWS = 6000


class ParseError(Exception):
    """整表级解析错误"""


def _cell_text(v):
    if v is None:
        return ''
    return str(v).strip()


def parse_score_excel(stream, exam):
    """解析上传的成绩 Excel
    返回 dict：
      ok/template/error（整表错误）
      headers: 识别出的列信息 {no,name,grade,class_name,total,subjects:[...], ignored:[...]}
      rows: [{no,name,grade,class_name,direction,selection,enrollment_status,total,subjects:{科目:分数}}]
      errors: [{line,no,reason}]
      stats: {total_rows, ok_rows, error_rows, no_grade_rows, absent_rows}
    """
    fm = exam.full_marks()
    wb = openpyxl.load_workbook(stream, data_only=True, read_only=True)
    ws = wb.worksheets[0] if wb.worksheets else None
    if ws is None:
        raise ParseError('Excel 文件中没有工作表')

    # ---- 1. 扫描表头 ----
    header_idx = {}      # 逻辑名 -> 列索引
    header_line = 0
    header_raw = []
    for row_i, row in enumerate(ws.iter_rows(min_row=1, max_row=SCAN_HEADER_MAX_ROW,
                                             values_only=True), start=1):
        cells = [_cell_text(c) for c in row]
        found = {}
        for i, cell in enumerate(cells):
            if not cell:
                continue
            if cell in _NO_KEYS:
                found['no'] = i
            elif cell in _NAME_KEYS:
                found['name'] = i
            elif cell in _CLASS_KEYS:
                found['class_name'] = i
            elif cell in _GRADE_KEYS:
                found['grade'] = i
            elif cell in _TOTAL_KEYS:
                found['total'] = i
            elif cell in _SUBJECT_ALIAS:
                found.setdefault('subjects', {})[_SUBJECT_ALIAS[cell]] = i
            elif cell in SUBJECTS:
                found.setdefault('subjects', {})[cell] = i
        if 'no' in found or ('name' in found and 'subjects' in found):
            header_idx = found
            header_line = row_i
            header_raw = cells
            break
    if not header_idx or 'no' not in header_idx:
        # 兼容：仅有考号无学号
        raise ParseError('未识别到表头：需包含“学号”列（与姓名/科目列同行）；仅“考号”列时请补充学号列（可下载模板查看格式）')
    if 'subjects' not in header_idx or not header_idx['subjects']:
        raise ParseError('未识别到科目列（语文/数学/外语/物理/历史/化学/生物/政治/地理），请使用标准表头')

    subjects_in_file = list(header_idx['subjects'].keys())
    ignored = [c for c in header_raw if c and c in _IGNORED_COLS]
    template = 'A' if 'total' in header_idx else 'B'

    # ---- 2. 逐行解析 ----
    rows = []
    errors = []
    line_no = header_line
    absent_rows = 0
    for row in ws.iter_rows(min_row=header_line + 1, values_only=True):
        line_no += 1
        cells = [_cell_text(c) for c in row]
        no_cell = cells[header_idx['no']] if header_idx['no'] < len(cells) else ''
        no = _norm_no(no_cell)
        # 空行跳过（学号与全部科目列为空）
        subj_cols = header_idx['subjects'].values()
        has_any = any(i < len(cells) and cells[i] != '' for i in subj_cols)
        if not no and not has_any:
            continue
        # 修复：启用行数上限（原 MAX_IMPORT_ROWS 常量定义后从未校验），防止超大文件拖垮导入
        if len(rows) >= MAX_IMPORT_ROWS:
            raise ParseError(f'数据行超过上限（{MAX_IMPORT_ROWS} 行），请拆分文件后分批导入')
        if not no:
            errors.append({'line': line_no, 'no': '', 'reason': '缺少学号'})
            continue
        # 年级列校验（有列才校验，与考试年级必须一致）
        if 'grade' in header_idx:
            gi = header_idx['grade']
            raw_grade = cells[gi] if gi < len(cells) else ''
            g = parse_grade_any(raw_grade)
            if g is None:
                errors.append({'line': line_no, 'no': no,
                               'reason': f'年级列值“{raw_grade}”无法识别（需 {exam.grade}）'})
                continue
            if g != exam.grade:
                errors.append({'line': line_no, 'no': no,
                               'reason': f'年级 {g} 与考试年级 {exam.grade} 不一致'})
                continue
        # 分数读取与校验
        subjects = {}
        row_has_score = False
        bad = None
        for sub, ci in header_idx['subjects'].items():
            val = cells[ci] if ci < len(cells) else ''
            if val == '':
                subjects[sub] = None
                continue
            try:
                score = float(val)
            except (TypeError, ValueError):
                bad = f'{sub}列“{val}”不是数值'
                break
            limit = fm.get(sub, 100)
            if score < 0 or score > limit + 0.5:
                bad = f'{sub}分数 {score} 超出范围(0~{limit})'
                break
            subjects[sub] = score
            row_has_score = True
        if bad:
            errors.append({'line': line_no, 'no': no, 'reason': bad})
            continue
        total = None
        if 'total' in header_idx:
            ti = header_idx['total']
            tv = cells[ti] if ti < len(cells) else ''
            if tv != '':
                try:
                    total = float(tv)
                except (TypeError, ValueError):
                    errors.append({'line': line_no, 'no': no, 'reason': '总分列不是数值'})
                    continue
        if not row_has_score:
            absent_rows += 1
            continue  # 全行无分 = 未参加本场考试
        rows.append({'no': no, 'line': line_no, 'subjects': subjects, 'total': total})

    # 修复：文件内同一学号重复出现时仅保留最后一次出现并给出提示（原先静默后行覆盖前行）
    _seen = {}
    for _i, _r in enumerate(rows):
        _seen.setdefault(_r['no'], []).append(_i)
    _drop = set()
    for _no, _idxs in _seen.items():
        if len(_idxs) > 1:
            for _i in _idxs[:-1]:
                _drop.add(_i)
                errors.append({'line': rows[_i]['line'], 'no': _no,
                               'reason': '学号在文件中重复出现，已采用最后一次出现的数据'})
    if _drop:
        rows[:] = [r for i, r in enumerate(rows) if i not in _drop]

    # ---- 3. 主库匹配与快照 ----
    if rows:
        unmatched = _attach_student_info(exam, rows)
        errors.extend(unmatched)

    return {
        'ok': True,
        'template': template,
        'headers': {
            'no': True, 'name': 'name' in header_idx, 'grade': 'grade' in header_idx,
            'class_name': 'class_name' in header_idx, 'total': 'total' in header_idx,
            'subjects': subjects_in_file, 'ignored': ignored,
        },
        'rows': rows,
        'errors': errors,
        'stats': {
            'total_lines': line_no - header_line,
            'ok_rows': len(rows),
            'error_rows': len(errors),
            'absent_rows': absent_rows,
        },
    }


def _norm_no(v):
    """学号规范化：数字文本去掉前导 0 差异与空格"""
    if not v:
        return ''
    s = str(v).strip()
    if s.isdigit():
        return str(int(s))
    return s


def _attach_student_info(exam, rows):
    """按学号批量匹配主库学生，生成快照字段；无匹配的行移入 errors 语义（置 flag）"""
    from app.extensions import db
    nos = [r['no'] for r in rows]
    stu_map = {}
    # 分块 IN 查询（SQLite 变量数上限 999）
    for i in range(0, len(nos), 800):
        chunk = nos[i:i + 800]
        # 修复：双边归一化学号——主库侧同样经 _norm_no 归一后建映射，
        # 兼容主库学号含前导 0（如 020250006）而文件侧被归一为 20250006 的场景
        for s in Student.query.filter(Student.student_number.in_(chunk)).all():
            stu_map.setdefault(_norm_no(s.student_number), s)
        # 前导 0 学号的原文无法命中上面的原文 IN 匹配，追加按数值匹配一次
        ints = [int(n) for n in chunk if n.isdigit() and int(n) > 0]
        if ints:
            for s in Student.query.filter(
                    db.func.cast(Student.student_number, db.Integer).in_(ints)).all():
                stu_map.setdefault(_norm_no(s.student_number), s)
    # 该年级班型方向（兜底）
    cp_map = {}
    try:
        for cp in ClassProfile.query.filter_by(grade=exam.grade).all():
            cp_map[cp.class_name] = cp.subject_direction
    except Exception:
        cp_map = {}

    keep = []
    unmatched = []
    for r in rows:
        stu = stu_map.get(r['no'])
        if stu is None:
            unmatched.append({'line': r['line'], 'no': r['no'], 'reason': '学号未匹配到主库学生'})
            continue
        r['name'] = stu.name
        # 修复：学号以主库原值为准（归一化仅用于匹配），保证成绩行与主库键一致
        r['no'] = stu.student_number
        r['grade'] = stu.grade
        r['class_name'] = stu.class_name
        r['subject_selection'] = stu.subject_selection or ''
        r['enrollment_status'] = stu.enrollment_status or ''
        direction = ''
        sel = (r['subject_selection'] or '').strip()
        if sel.startswith('物'):
            direction = '物理'
        elif sel.startswith('史'):
            direction = '历史'
        elif sel in ('理科', '理'):
            direction = '物理'
        elif sel in ('文科', '文'):
            direction = '历史'
        if not direction:
            direction = cp_map.get(stu.class_name) or ''
        r['direction'] = direction
        # 不分科 / 统一考试本就无方向（direction 留空），不再剔除学生；
        # 有选科却仍无法识别方向的，按不分科兜底并提示，便于核对
        if not direction and sel:
            unmatched.append({'line': r['line'], 'no': r['no'],
                              'reason': '选科方向无法识别（已按不分科处理，请核对选科）'})
        keep.append(r)
    rows[:] = keep
    return unmatched

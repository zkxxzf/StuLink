# StuLink v1.18.1.0 2026-09-24
# 教师安排表解析：兼容《教师安排表.xlsx》总任课表横表（多年级区段 / 英语别名 / 忽略非高考科目列）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import re

import openpyxl

from app.models.grades import SUBJECTS
from app.modules.grades.utils import parse_grade_any

_SUBJECT_ALIAS = {'英语': '外语'}
_CLASS_RE = re.compile(r'^\s*(\d{1,2})班\s*$')
_SECTION_MARKERS = ('任课安排', '备课组长', '任课表')
_MAX_ROWS = 3000
# 班主任列（正班/副班，按职务优先级排序；与班型设置页每班≤3 班主任一致）
HEAD_COLS = ('正班', '副班1', '副班2')


def parse_teacher_excel(stream):
    """解析教师安排表（横表：一行一班、科目为列，多年级区段）
    返回: links=[{grade, class_name, subject, teacher_name}]
          homerooms=[{grade, class_name, teacher_name, pos}]
          sections / errors
    规则：
    - 含"任课安排/备课组长"的标题行与空行直接跳过；"数量"统计行忽略
    - 表头行：含 班级 且 含任一高考科目列（语文/数学/英语/外语…）
    - 数据行：含 'xx班'；年级取行内 '20xx级' 形式（不认 高三/高二 称谓）
    - 只提取 9 个高考科目列与班主任列（正班/副班1/副班2）；其余忽略
    """
    wb = openpyxl.load_workbook(stream, data_only=True, read_only=True)
    ws = wb.worksheets[0] if wb.worksheets else None
    if ws is None:
        raise ValueError('Excel 文件中没有工作表')

    subj_idx = None      # {subject: col}
    head_idx = None      # {pos: col}
    links = []
    homerooms = []
    errors = []
    sections = {}
    line_no = 0

    for row in ws.iter_rows(values_only=True):
        line_no += 1
        if line_no > _MAX_ROWS:
            break
        cells = [str(c).strip() if c is not None else '' for c in row]
        joined = ' '.join(cells)
        if any(m in joined for m in _SECTION_MARKERS):
            subj_idx = None
            head_idx = None
            continue
        if not any(cells):
            continue
        # 数量统计行忽略
        if cells[0].startswith('数量'):
            continue
        # 表头行识别
        if '班级' in cells and ('语文' in cells or '英语' in cells or '外语' in cells):
            idx = {}
            hi = {}
            for i, c in enumerate(cells):
                if c in SUBJECTS:
                    idx[c] = i
                elif c in _SUBJECT_ALIAS and _SUBJECT_ALIAS[c] not in idx:
                    idx[_SUBJECT_ALIAS[c]] = i
                elif c in HEAD_COLS:
                    hi[c] = i
            subj_idx = idx or None
            head_idx = hi or None
            continue
        if not subj_idx:
            continue
        # 数据行
        cls_cell = next((c for c in cells if _CLASS_RE.match(c)), '')
        if not cls_cell:
            continue  # 非班级数据行（数量行等）
        m = _CLASS_RE.match(cls_cell)
        class_name = f'{int(m.group(1)):02d}班'
        grade = ''
        for c in cells:
            g = parse_grade_any(c)
            if g:
                grade = g
                break
        if not grade:
            errors.append({'line': line_no, 'reason': f'行缺少可识别的年级列（{class_name}）'})
            continue
        sections.setdefault(grade, set()).add(class_name)
        for sub, col in subj_idx.items():
            if col >= len(cells):
                continue
            name = cells[col]
            if name:
                links.append({'grade': grade, 'class_name': class_name,
                              'subject': sub, 'teacher_name': name})
        # 班主任列（正班/副班1/副班2）
        if head_idx:
            for pos, col in head_idx.items():
                if col >= len(cells):
                    continue
                name = cells[col]
                if name:
                    homerooms.append({'grade': grade, 'class_name': class_name,
                                      'teacher_name': name, 'pos': pos})

    if not links and not homerooms:
        raise ValueError('未解析到任课数据：请确认文件为“总任课表”格式（行=班级、列=科目），'
                         '且含“班级”与“语文/数学/英语…”表头')
    # 班级同科同师去重（同区段表头重复出现）
    seen = set()
    unique = []
    for l in links:
        key = (l['grade'], l['class_name'], l['subject'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(l)
    links = unique
    # 班主任去重（同人同班多职务只保留最高职务）
    seen_hm = set()
    hm_keep = []
    for h in sorted(homerooms, key=lambda x: (x['grade'], x['class_name'],
                                              HEAD_COLS.index(x['pos']))):
        key = (h['grade'], h['class_name'], h['teacher_name'])
        if key in seen_hm:
            continue
        seen_hm.add(key)
        hm_keep.append(h)
    homerooms = hm_keep
    return {
        'links': links,
        'homerooms': homerooms,
        'sections': {g: sorted(c) for g, c in sections.items()},
        'errors': errors,
    }

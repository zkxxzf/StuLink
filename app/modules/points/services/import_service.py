# StuLink v1.9.2 2026-09-18
# 积分导入服务：Excel 解析 / 校验 / 批量写入 / 模板生成
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
from datetime import date, datetime

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

from app.extensions import db
from app.models import Student, PointRecord

# 表头识别
_NO_KEYS = ('学号',)
_NAME_KEYS = ('姓名',)
_GRADE_KEYS = ('年级',)
_CLASS_KEYS = ('班级',)
_POINTS_KEYS = ('积分值', '分值', '积分')
_CATEGORY_KEYS = ('类别',)
_REASON_KEYS = ('原因', '事由')

SCAN_HEADER_MAX_ROW = 10
MAX_IMPORT_ROWS = 3000
VALID_CATEGORIES = ['纪律', '学习', '卫生', '活动', '其他']


class ParseError(Exception):
    """整表级解析错误"""


def _cell_text(v):
    if v is None:
        return ''
    return str(v).strip()


def parse_points_excel(stream):
    """解析上传的积分 Excel
    返回 dict：
      rows: [{student_no, student_name, grade, class_name, points, category, reason, line}]
      errors: [{line, student_no, reason}]
      stats: {total_rows, ok_rows, error_rows}
    """
    wb = openpyxl.load_workbook(stream, data_only=True, read_only=True)
    ws = wb.worksheets[0] if wb.worksheets else None
    if ws is None:
        raise ParseError('Excel 文件中没有工作表')

    # ---- 1. 扫描表头 ----
    header_idx = {}
    header_line = 0
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
            elif cell in _GRADE_KEYS:
                found['grade'] = i
            elif cell in _CLASS_KEYS:
                found['class_name'] = i
            elif cell in _POINTS_KEYS:
                found['points'] = i
            elif cell in _CATEGORY_KEYS:
                found['category'] = i
            elif cell in _REASON_KEYS:
                found['reason'] = i
        if 'no' in found and 'points' in found:
            header_idx = found
            header_line = row_i
            break
    if not header_idx:
        raise ParseError('未识别到表头：需包含"学号"和"积分值"列')

    # ---- 2. 逐行解析 ----
    rows = []
    errors = []
    line_no = header_line
    for row in ws.iter_rows(min_row=header_line + 1, values_only=True):
        line_no += 1
        cells = [_cell_text(c) for c in row]
        no = cells[header_idx['no']] if header_idx['no'] < len(cells) else ''
        if not no:
            # 空行跳过
            if all(c == '' for c in cells):
                continue
            errors.append({'line': line_no, 'student_no': '', 'reason': '缺少学号'})
            continue
        if len(rows) >= MAX_IMPORT_ROWS:
            raise ParseError(f'数据行超过上限（{MAX_IMPORT_ROWS} 行），请拆分文件后分批导入')
        # 积分值
        pi = header_idx.get('points')
        pv = cells[pi] if pi is not None and pi < len(cells) else ''
        try:
            points = int(float(pv))
        except (TypeError, ValueError):
            errors.append({'line': line_no, 'student_no': no, 'reason': f'积分值"{pv}"不是整数'})
            continue
        if points == 0 or abs(points) > 100:
            errors.append({'line': line_no, 'student_no': no, 'reason': f'积分值{points}需在-100~100之间且不为0'})
            continue
        # 类别
        ci = header_idx.get('category')
        cat = cells[ci] if ci is not None and ci < len(cells) else ''
        if cat and cat not in VALID_CATEGORIES:
            errors.append({'line': line_no, 'student_no': no, 'reason': f'类别"{cat}"不在合法范围内'})
            continue
        # 原因
        ri = header_idx.get('reason')
        reason = cells[ri] if ri is not None and ri < len(cells) else ''
        if not reason:
            errors.append({'line': line_no, 'student_no': no, 'reason': '原因/事由不能为空'})
            continue
        # 姓名/年级/班级（可选，用于预校验）
        ni = header_idx.get('name')
        name = cells[ni] if ni is not None and ni < len(cells) else ''
        gi = header_idx.get('grade')
        grade = cells[gi] if gi is not None and gi < len(cells) else ''
        cli = header_idx.get('class_name')
        class_name = cells[cli] if cli is not None and cli < len(cells) else ''

        rows.append({
            'student_no': no,
            'student_name': name,
            'grade': grade,
            'class_name': class_name,
            'points': points,
            'category': cat or '其他',
            'reason': reason[:200],
            'line': line_no,
        })

    return {
        'rows': rows,
        'errors': errors,
        'stats': {
            'total_rows': line_no - header_line,
            'ok_rows': len(rows),
            'error_rows': len(errors),
        }
    }


def validate_records(rows):
    """校验记录：学号是否存在、姓名/年级/班级是否匹配
    返回 (valid_rows, invalid_rows)
    """
    if not rows:
        return [], []
    nos = list({r['student_no'] for r in rows})
    stu_map = {}
    for i in range(0, len(nos), 800):
        chunk = nos[i:i + 800]
        for s in Student.query.filter(Student.student_number.in_(chunk)).all():
            stu_map[str(s.student_number)] = s

    valid = []
    invalid = []
    for r in rows:
        stu = stu_map.get(r['student_no'])
        if stu is None:
            invalid.append({**r, 'error': '学号未匹配到学生'})
            continue
        # 用主库信息覆盖（以主库为准）
        r['student_name'] = stu.name
        r['grade'] = stu.grade
        r['class_name'] = stu.class_name
        valid.append(r)
    return valid, invalid


def import_records(valid_rows, operator_id, operator_name):
    """批量写入 point_records 表
    返回导入数量
    """
    if not valid_rows:
        return 0
    count = 0
    for r in valid_rows:
        rec = PointRecord(
            student_no=r['student_no'],
            student_name=r['student_name'],
            grade=r['grade'],
            class_name=r['class_name'],
            points=r['points'],
            category=r['category'],
            reason=r['reason'],
            recorded_at=date.today(),
            operator_id=operator_id,
            operator_name=operator_name,
        )
        db.session.add(rec)
        count += 1
    db.session.commit()
    return count


def generate_template():
    """生成积分导入模板 Excel
    返回 BytesIO 对象
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '积分导入模板'

    headers = ['学号', '姓名', '年级', '班级', '积分值', '类别', '原因']
    widths = [15, 12, 10, 10, 10, 10, 30]

    hf = Font(bold=True, color='FFFFFF', size=11)
    hfl = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
    tb = Border(left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'))

    for ci, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf
        c.fill = hfl
        c.alignment = Alignment(horizontal='center')
        c.border = tb
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = w

    # 示例数据
    examples = [
        ['2025001', '张三', '高一', '1班', 5, '学习', '月考进步显著'],
        ['2025002', '李四', '高一', '1班', -3, '纪律', '上课迟到'],
    ]
    for ri, row_data in enumerate(examples, 2):
        for ci, v in enumerate(row_data, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.border = tb

    # 类别说明行
    ws.cell(row=5, column=1, value='类别可选值：').font = Font(italic=True, color='666666')
    ws.cell(row=5, column=2, value='纪律、学习、卫生、活动、其他').font = Font(italic=True, color='666666')

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out

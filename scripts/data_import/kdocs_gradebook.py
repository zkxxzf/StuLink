"""读取「高中2025级历次考试成绩汇总.xlsx」并归一成 StuLink 标准导入列。

这份金山文档（kdocs）导出的汇总表，一个 sheet 就是一次考试：

    第 1 行：标题（如「年级学生成绩单」）
    第 2 行：表头 考号 | 姓名 | 总赋 | 语文 | 数学 | 外语 |
            物理 | 历史 | 化赋 | 生赋 | 政赋 | 地赋

其中「考号」列实际是学号；「总赋」是该场总分；「化赋/生赋/政赋/地赋」
是赋分后的化学/生物/政治/地理。这些列名都不在 StuLink 导入器的识别表里，
必须先转换。班级取「学生信息表」（= 考试当时班级，主库班级已因分班变更）。
"""

from openpyxl import Workbook, load_workbook

# 源列名 -> StuLink 标准科目名（顺序即标准导入表的列顺序）
SUBJECT_COLMAP = {
    '总赋': '总分',
    '语文': '语文',
    '数学': '数学',
    '外语': '外语',
    '物理': '物理',
    '历史': '历史',
    '化赋': '化学',
    '生赋': '生物',
    '政赋': '政治',
    '地赋': '地理',
}

# 标准导入表的列顺序（学号/姓名/年级/班级 + 总分 + 九科）
OUT_COLS = ['学号', '姓名', '年级', '班级', '总分'] + [
    '语文', '数学', '外语', '物理', '历史',
    '化学', '生物', '政治', '地理',
]

INFO_SHEET = '学生信息表'
HEADER_ROW = 2


def _text(value):
    """单元格值转成去掉首尾空白的字符串（None 变空串）。"""
    return '' if value is None else str(value).strip()


def read_student_info(path):
    """读「学生信息表」，返回 {学号: {'name', 'class_name', 'selection'}}。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    rows = list(wb[INFO_SHEET].iter_rows(values_only=True))
    wb.close()
    head = [_text(c) for c in rows[0]]
    out = {}
    for raw in rows[1:]:
        rec = dict(zip(head, raw))
        no = _text(rec.get('学号'))
        if not no:
            continue
        out[no] = {
            'name': _text(rec.get('姓名')),
            'class_name': _text(rec.get('班级')),
            'selection': _text(rec.get('选科组合')),
        }
    return out


def read_exam_sheet(path, sheet):
    """读某次考试的 sheet，返回 (表头列表, 字典行列表)。"""
    wb = load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        wb.close()
        raise KeyError('工作表不存在：%s（可选：%s）' %
                       (sheet, '、'.join(wb.sheetnames)))
    rows = list(wb[sheet].iter_rows(values_only=True))
    wb.close()
    head = [_text(c) for c in rows[HEADER_ROW - 1]]
    data = []
    for raw in rows[HEADER_ROW:]:
        if not any(c not in (None, '') for c in raw):
            continue
        data.append(dict(zip(head, raw)))
    return head, data


def normalize_rows(exam_rows, info):
    """把源行转成标准导入行。

    返回 (rows, problems)：
      rows     = [{'学号','姓名','班级','总分', 科目...}]
      problems = [{'no','reason'}]  学号缺失 / 学生信息表查不到班级
    """
    rows = []
    problems = []
    for rec in exam_rows:
        no = _text(rec.get('考号'))
        if not no:
            continue
        stu = info.get(no)
        if stu is None:
            problems.append({'no': no, 'reason': '学生信息表无此学号'})
            continue
        out = {
            '学号': no,
            '姓名': stu['name'] or _text(rec.get('姓名')),
            '班级': stu['class_name'],
        }
        for src, dst in SUBJECT_COLMAP.items():
            val = rec.get(src)
            out[dst] = None if val in (None, '') else val
        rows.append(out)
    return rows, problems


def write_standard_excel(rows, out_path, grade):
    """把标准行写成 StuLink 可识别的导入 Excel（首行即表头）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = '成绩导入'
    ws.append(OUT_COLS)
    for row in rows:
        line = [row.get('学号'), row.get('姓名'), grade,
                row.get('班级')]
        for col in OUT_COLS[4:]:
            line.append(row.get(col))
        ws.append(line)
    wb.save(out_path)
    return len(rows)

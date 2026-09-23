# StuLink v1.18.0 2026-09-23
# 课表 Excel 导入：模板生成 / 解析校验 / 冲突检测 / 确认写入
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import re

from openpyxl import Workbook, load_workbook

from app.extensions import db
from app.models.academic import Teacher, Timetable, TimetableEntry

HEADERS = ['班级', '学科', '教师编号', '星期', '节次', '周次范围', '教室']
SAMPLE_ROW = ['01班', '语文', 'T12345678', '1', '1', '1-16', 'A101']
_WEEKDAY_MAP = {'周一': 1, '周二': 2, '周三': 3, '周四': 4, '周五': 5, '周六': 6, '周日': 7}
_WEEK_RE = re.compile(r'^(\d{1,2})\s*[-–—~～]\s*(\d{1,2})$')

_TIPS = [
    '一、字段说明',
    '  1. 班级：必填（如 01班、02班）。',
    '  2. 学科：必填（如 语文、数学、英语）。',
    '  3. 教师编号：必填（教师名单中的唯一编号，以 T 开头）。',
    '  4. 星期：必填（1-5 的数字，或 周一-周五）。',
    '  5. 节次：必填（1-10 的数字）。',
    '  6. 周次范围：选填（如 1-16，表示第 1 到第 16 周）。',
    '  7. 教室：选填。',
    '二、注意事项',
    '  1. 第 2 行为示例行，导入前请删除。',
    '  2. 教师编号必须在教师名单中已存在，否则该行将标记错误。',
    '  3. 同一教师在同一星期同一节次不得安排多个班级（冲突检测）。',
    '三、导入流程',
    '  1. 下载模板 → 填写数据 → 上传预览 → 确认写入。',
    '  2. 预览阶段会显示所有错误和冲突，确认时只写入有效行。',
]


def build_template():
    """生成课表导入模板（xlsx BytesIO）"""
    wb = Workbook()
    ws = wb.active
    ws.title = '课表数据'
    ws.append(HEADERS)
    ws.append(SAMPLE_ROW)
    for i, w in enumerate([12, 10, 16, 8, 8, 12, 12], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = 'A2'

    tip = wb.create_sheet('填写说明')
    for line in _TIPS:
        tip.append([line])
    tip.column_dimensions['A'].width = 110

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def parse_timetable_excel(stream):
    """解析上传的 Excel 课表文件 → 行记录列表（含行级错误收集）"""
    wb = load_workbook(stream, data_only=True)
    ws = wb.active
    iter_rows = ws.iter_rows(values_only=True)
    try:
        header = next(iter_rows)
    except StopIteration:
        raise ValueError('文件内容为空')

    col = {}
    for idx, cell in enumerate(header):
        head = str(cell or '').strip()
        if head in HEADERS:
            col[head] = idx
    if '班级' not in col or '学科' not in col:
        raise ValueError('缺少必要列（班级、学科），请使用标准模板填写')

    out = []
    for rn, raw in enumerate(iter_rows, start=2):
        def cell(key):
            i = col.get(key)
            if i is None or i >= len(raw) or raw[i] is None:
                return ''
            return str(raw[i]).strip()

        class_name = cell('班级')
        subject = cell('学科')
        teacher_uid = cell('教师编号')
        weekday_raw = cell('星期')
        period_raw = cell('节次')
        week_range = cell('周次范围')
        room = cell('教室')

        if not any((class_name, subject, teacher_uid, weekday_raw, period_raw, week_range, room)):
            continue  # 跳过全空行
        if class_name == '01班' and teacher_uid == 'T12345678':
            continue  # 跳过模板示例行

        errors = []
        weekday = _parse_weekday(weekday_raw, errors)
        period = _parse_period(period_raw, errors)
        week_range = _normalize_week_range(week_range, errors)

        if not class_name:
            errors.append('班级为空')
        if not subject:
            errors.append('学科为空')
        if not teacher_uid:
            errors.append('教师编号为空')

        out.append({
            'row_no': rn, 'class_name': class_name, 'subject': subject,
            'teacher_uid': teacher_uid, 'weekday': weekday, 'period': period,
            'week_range': week_range, 'room': room,
            'weekday_raw': weekday_raw, 'period_raw': period_raw,
            'errors': errors,
        })

    if not out:
        raise ValueError('未解析到有效数据行（请确认使用标准模板且首行为表头）')
    return out


def _parse_weekday(raw, errors):
    """解析星期字段 → 1-7 整数，失败时追加 errors 并返回 None"""
    if not raw:
        errors.append('星期为空')
        return None
    # 中文
    if raw in _WEEKDAY_MAP:
        return _WEEKDAY_MAP[raw]
    try:
        v = int(raw)
        if 1 <= v <= 7:
            return v
        errors.append(f'星期超出范围：{raw}（应为 1-5）')
        return None
    except ValueError:
        errors.append(f'星期格式无效：{raw}（应为 1-5 或 周一-周五）')
        return None


def _parse_period(raw, errors):
    """解析节次字段 → 1-10 整数"""
    if not raw:
        errors.append('节次为空')
        return None
    try:
        v = int(raw)
        if 1 <= v <= 10:
            return v
        errors.append(f'节次超出范围：{raw}（应为 1-10）')
        return None
    except ValueError:
        errors.append(f'节次格式无效：{raw}')
        return None


def _normalize_week_range(raw, errors):
    """规范化周次范围，如 '1-16' → '1-16'；空值返回空字符串"""
    if not raw:
        return ''
    m = _WEEK_RE.match(raw)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            errors.append(f'周次范围无效：{raw}（起始 > 结束）')
            return raw
        return f'{lo}-{hi}'
    # 单周次
    try:
        v = int(raw)
        if 1 <= v <= 30:
            return str(v)
    except ValueError:
        pass
    errors.append(f'周次范围格式无效：{raw}（应为如 1-16）')
    return raw


def validate_and_enrich(entries):
    """校验教师是否存在，补充教师姓名；返回 enriched entries（原地修改 errors）"""
    teacher_cache = {}
    for e in entries:
        if e['errors']:
            continue
        uid = e['teacher_uid']
        if uid not in teacher_cache:
            t = Teacher.query.filter_by(teacher_uid=uid).first()
            teacher_cache[uid] = t
        t = teacher_cache[uid]
        if not t:
            e['errors'].append(f'教师编号不存在：{uid}')
        else:
            e['teacher_name'] = t.name
            e['teacher_subject'] = t.subject or ''
    return entries


def detect_conflicts(entries):
    """检测教师时间冲突：同一教师在同一星期同一节次只能安排一个班级。
    冲突行追加 errors 信息，返回 entries。
    """
    # 只检查无错误的行
    slot_map = {}  # (teacher_uid, weekday, period) -> first_row_no
    for e in entries:
        if e['errors']:
            continue
        key = (e['teacher_uid'], e['weekday'], e['period'])
        if key in slot_map:
            e['errors'].append(
                f'教师时间冲突：第 {slot_map[key]} 行已安排同一教师在同一时间'
            )
        else:
            slot_map[key] = e['row_no']
    return entries


def build_import_plan(name, grade, entries):
    """生成导入预览计划（不写库）"""
    valid = [e for e in entries if not e['errors']]
    invalid = [e for e in entries if e['errors']]
    return {
        'name': name,
        'grade': grade,
        'valid': valid,
        'invalid': invalid,
        'total': len(entries),
        'valid_count': len(valid),
        'invalid_count': len(invalid),
    }


def confirm_import(name, grade, entries, created_by):
    """确认写入数据库：创建 Timetable + TimetableEntry 记录"""
    valid = [e for e in entries if not e['errors']]
    if not valid:
        raise ValueError('无有效数据可写入')

    tt = Timetable(name=name, grade=grade or None, created_by=created_by)
    db.session.add(tt)
    db.session.flush()

    for e in valid:
        entry = TimetableEntry(
            timetable_id=tt.id,
            teacher_uid=e['teacher_uid'],
            teacher_name=e.get('teacher_name', ''),
            subject=e['subject'],
            class_name=e['class_name'],
            weekday=e['weekday'],
            period=e['period'],
            week_range=e.get('week_range', ''),
            room=e.get('room', ''),
        )
        db.session.add(entry)

    db.session.commit()
    return tt

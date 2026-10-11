# StuLink v1.19.40.2 2026-10-10
# 考务管理：完整考务流程（对应 Excel 宏工作簿 2025考场学生考号与考场信息编排v1.2）
#   步骤：① 学生名单（学生信息表） → ② 考场设置（考场信息表）
#        → ③ 编排与考号生成（三种模式，镜像宏 编排考场考号2）
#        → ④ 导出（按班级/按考场/竖版桌签） → ⑤ 成绩关联（绑定考试、按考场看成绩）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import json  # v1.12.1 选科前后缀配置解析
import random
from datetime import date, datetime

import openpyxl
# 2026-10-10：批量统计房间数需要 func.count（消除 N+1）
from sqlalchemy import func
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from flask import (render_template, request, jsonify, abort, redirect,
                   url_for, send_file, flash, current_app)
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Student
from app.models.grades import (ExamAffair, AffairRoom, AffairRoomLib, AffairStudent,
                               Exam)  # v1.12.1 加考场房间库；v1.12.2 移除成绩关联查询依赖
from app.modules.grades import bp
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation, get_dict_values
from app.modules.grades.utils import is_teaching_class
from app.utils.text_guard import (sanitize_label, sanitize_prefix,
                                  safe_download_name)
from app.utils.upload_guard import validate_upload   # L-8：导入文件类型校验
from app.utils.export_helpers import xl_row   # M-4：公式注入防护

MODE_LABEL = {0: '模式0：前缀+考场号+座号', 1: '模式1：学号即考号', 2: '模式2：自定义考号'}

_HF = Font(bold=True, color='FFFFFF')
_HFL = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
_TB = Border(left=Side(style='thin'), right=Side(style='thin'),
             top=Side(style='thin'), bottom=Side(style='thin'))
_CENTER = Alignment(horizontal='center', vertical='center')


def _get_affair_checked(aid):
    """H-4：取考务批次并校验年级范围（越界 403）。

    此前 20 余处直接 `ExamAffair.query.get_or_404(aid)`，只验存在不验归属，
    任何有 grades.edit 的人都能操作其它年级的考务批次。
    """
    from app.modules.grades.services.exam_guard import assert_grade_visible
    affair = ExamAffair.query.get_or_404(aid)
    assert_grade_visible(affair.grade)
    return affair


def _pad2(v):
    s = str(v)
    return s.zfill(2) if s.isdigit() else s


# v1.12.2 考场编号规范：纯数字编号统一补零为两位（01、02…），保证排序与考号拼接一致
def _norm_room_no(no):
    s = str(no).strip()
    return s.zfill(2) if s.isdigit() else s


def _inherit_rooms_from_prev(affair):
    """v1.19.0：新建考务批次时，默认沿用**该年级上一次**批次的考场安排与容量。

    容量不再存于基础数据（同一房间不同考试/年级容量可能不同），故新建考务时
    从该年级最近一场「有考场」的批次复制位置/容量/科目/备注/编号；找不到则不处理。
    返回复制的考场数。
    """
    cands = ExamAffair.query.filter(ExamAffair.grade == affair.grade,
                                    ExamAffair.id != affair.id).all()
    cands = [a for a in cands
             if AffairRoom.query.filter_by(affair_id=a.id).first() is not None]
    if not cands:
        return 0
    prev = max(cands, key=lambda a: (a.exam_date or date.min, a.id))
    src = (AffairRoom.query.filter_by(affair_id=prev.id)
           .order_by(AffairRoom.room_no).all())
    for r in src:
        db.session.add(AffairRoom(affair_id=affair.id, room_no=r.room_no,
                                  location=r.location, subject=r.subject or '默认',
                                  capacity=r.capacity or 30, prefix=r.prefix,
                                  note=r.note))
    return len(src)


def _grade_options():
    from app.modules.grades.services.scope import visible_grades
    return visible_grades(current_user)


# ==================== 考务批次列表 / 新建 / 删除 ====================


def delete_affair_cascade(aid):
    """v1.19.40.2：删除考务批次并级联清理其考场与学生名单。

    AffairRoom / AffairStudent 的 affair_id 无外键约束（多库快照式设计，不建跨表外键），
    直接 `db.session.delete(affair)` 会留下孤儿行；此处显式按 affair_id 批量删除。
    调用方负责 commit。返回 (考场数, 名单数)。
    """
    n_rooms = AffairRoom.query.filter_by(affair_id=aid).delete()
    n_stus = AffairStudent.query.filter_by(affair_id=aid).delete()
    n_aff = ExamAffair.query.filter_by(id=aid).delete()
    return n_rooms, n_stus + n_aff


@bp.route('/affairs')
@login_required
@perm_required('grades.edit')
def affairs_list():
    """v1.17.0 考试/考务合并收尾（v1.19.40.2）：考务批次列表已并入考试管理，
    本页保留为兼容入口（旧书签/外链），统一重定向到考试列表；
    未关联考试的孤儿批次由考试列表底部“待处理的考务批次”区兑底展示。"""
    return redirect(url_for('grades.exams_list'))


@bp.route('/affairs/overview')
@login_required
@perm_required('grades.edit')
def affairs_overview():
    """考务批次总览（跳考试）：从考试列表“待处理批次”区进入，不按年级过滤。"""
    grade = request.args.get('grade', '')
    q = ExamAffair.query
    if grade:
        q = q.filter_by(grade=grade)
    affairs = q.order_by(ExamAffair.created_at.desc(), ExamAffair.id.desc()).all()
    # 2026-10-10 架构审计修复：原实现每个批次都查一次 AffairStudent + 一次
    # AffairRoom.count()（N+1）。批次一多就是首屏瓶颈。
    # 改为一次性取出全部学生并按批次分组 + 一次性 group_by 统计房间数。
    # stats 的字段与计算口径完全不变。
    _affair_ids = [a.id for a in affairs]
    _stus_by_affair = {}
    if _affair_ids:
        for _s in AffairStudent.query.filter(
                AffairStudent.affair_id.in_(_affair_ids)).all():
            _stus_by_affair.setdefault(_s.affair_id, []).append(_s)
    _room_count_by_affair = {}
    if _affair_ids:
        _room_count_by_affair = {
            row[0]: row[1] for row in db.session.query(
                AffairRoom.affair_id, func.count(AffairRoom.id)
            ).filter(AffairRoom.affair_id.in_(_affair_ids)
                     ).group_by(AffairRoom.affair_id).all()
        }

    stats = {}
    for a in affairs:
        stus = _stus_by_affair.get(a.id, [])
        stats[a.id] = {
            'students': len(stus),
            'attend': sum(1 for s in stus if s.is_attend),
            'rooms': _room_count_by_affair.get(a.id, 0),
            'arranged': sum(1 for s in stus if s.room_no),
            'linked': bool(a.exam_id),
        }
    return render_template('grades/affairs/list.html', affairs=affairs, stats=stats,
                           grade=grade, grade_options=_grade_options())


@bp.route('/affairs/create', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_create():
    name = (request.form.get('name') or '').strip()
    grade = (request.form.get('grade') or '').strip()
    if not name or not grade:
        flash('批次名与年级必填', 'danger')
        return redirect(url_for('grades.exams_list'))
    # H-4：创建时同样校验年级范围（不能为其它年级建考务批次）
    from app.modules.grades.services.exam_guard import assert_grade_visible
    assert_grade_visible(grade)
    exam_date = None
    try:
        exam_date = date.fromisoformat(request.form.get('exam_date'))
    except (TypeError, ValueError):
        exam_date = None
    # v1.12.1 新建批次即定编排模式：selected 按选科分组 / plain 不选科全体一组
    selection_mode = request.form.get('selection_mode')
    if selection_mode not in ('selected', 'plain'):
        selection_mode = 'selected'
    affair = ExamAffair(name=name, grade=grade, exam_date=exam_date,
                        default_prefix=sanitize_prefix(request.form.get('default_prefix')) or '1701',
                        selection_mode=selection_mode,
                        operator_id=current_user.id)
    db.session.add(affair)
    db.session.flush()                          # 取 affair.id 以便继承上次考场
    n_rooms = _inherit_rooms_from_prev(affair)
    db.session.commit()
    log_operation(current_user, '新建', '考务批次', affair.id,
                  f'{grade} {name}' + (f'（沿用上次考场{n_rooms}个）' if n_rooms else ''),
                  module='grades')
    flash('考务批次已创建' + (f'，已沿用上次 {n_rooms} 个考场' if n_rooms else ''), 'success')
    return redirect(url_for('grades.affair_detail', aid=affair.id))


@bp.route('/exams/<int:exam_id>/affair')
@login_required
@perm_required('grades.edit')
def exam_affair_go(exam_id):
    """考试列表「考务安排」入口：已有批次→进详情；无→按原流程创建（预填考试信息）后进向导"""
    # H-4：统一走考试年级范围校验（原先只比对下拉选项，不校验数据范围）
    from app.modules.grades.services.exam_guard import assert_exam_visible
    exam = assert_exam_visible(exam_id)
    affair = (ExamAffair.query.filter_by(exam_id=exam_id)
              .order_by(ExamAffair.id.desc()).first())
    if affair:
        return redirect(url_for('grades.affair_detail', aid=affair.id))
    affair = ExamAffair(name=f'{exam.name} 考务',
                        grade=exam.grade,
                        exam_date=exam.exam_date,
                        exam_id=exam_id,
                        default_prefix='1701',
                        selection_mode='selected',
                        operator_id=current_user.id)
    db.session.add(affair)
    db.session.flush()                          # 取 affair.id 以便继承上次考场
    n_rooms = _inherit_rooms_from_prev(affair)
    db.session.commit()
    log_operation(current_user, '新建', '考务批次', affair.id,
                  f'{exam.grade} {affair.name}（自考试列表）'
                  + (f'（沿用上次考场{n_rooms}个）' if n_rooms else ''), module='grades')
    flash('已为该考试创建考务安排，请按原流程编排'
          + (f'（已沿用上次 {n_rooms} 个考场）' if n_rooms else ''), 'success')
    return redirect(url_for('grades.affair_detail', aid=affair.id))


@bp.route('/affairs/<int:aid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_delete(aid):
    affair = _get_affair_checked(aid)
    name = affair.name
    # v1.19.40.2：级联清理考场与名单（无外键约束，不显式删会留孤儿行）
    n_rooms, n_stus = delete_affair_cascade(aid)
    db.session.commit()
    log_operation(current_user, '删除', '考务批次', aid,
                  f'{name}（含考场{n_rooms}个/名单{n_stus}人）', module='grades')
    flash('考务批次已删除', 'success')
    return redirect(url_for('grades.exams_list'))


# ==================== 考务向导详情（含 5 个步骤 + 成绩关联） ====================

def _class_sort_key(name):
    """班级排序键：'02班'→(0,2,'02班')；无班号的（未分班等）排最后"""
    digits = ''.join(ch for ch in (name or '') if ch.isdigit())
    return (0, int(digits), name or '') if digits else (1, 0, name or '')


def _student_sort_key(s):
    """v1.19.40.2 名单排序键：班级 → 姓氏（姓名以姓氏开头，故用姓名拼音）→ 学号。

    拼音不可用时退化为汉字本身，仍保持稳定次序。
    """
    from app.modules.grades.services.user_account import pinyin_of
    return (s.class_name or '', (pinyin_of(s.name) or (s.name or '')), s.student_no or '')


@bp.route('/affairs/<int:aid>')
@login_required
@perm_required('grades.edit')
def affair_detail(aid):
    affair = _get_affair_checked(aid)
    students = (AffairStudent.query.filter_by(affair_id=aid)
                .order_by(AffairStudent.class_name, AffairStudent.student_no).all())
    # v1.19.40.2 名单次序（用户口径）：按「班级 → 姓氏」排序（不再把不参考的提到最上，
    # 而是左右分栏展示：左=参加考试，右=不参加考试）
    students.sort(key=_student_sort_key)
    rooms = (AffairRoom.query.filter_by(affair_id=aid)
             .order_by(AffairRoom.room_no).all())
    join_students = [s for s in students if s.is_attend]          # 左栏：参加考试
    skip_students = [s for s in students if not s.is_attend]      # 右栏：不参加考试
    scope_students = join_students
    arranged = [s for s in scope_students if s.room_no]
    # v1.19.40.2：步骤条状态用 —— 已生成考号的人数（AI 导入会带考号，此时步骤③应显示已完成）
    numbered = sum(1 for s in scope_students if s.exam_number)

    # v1.12.2 步骤⑤成绩关联 UI 已移除，不再查询考试与成绩（省去每次访问的无用查询）

    # v1.19.40.2 名单统计：年级汇总 + 各班（实际 / 参加 / 不参加）
    grade_stat = {'total': len(students), 'join': len(join_students),
                  'skip': len(skip_students)}
    per_class = {}
    for s in students:
        d = per_class.setdefault(s.class_name or '未分班',
                                 {'total': 0, 'join': 0, 'skip': 0})
        d['total'] += 1
        d['join' if s.is_attend else 'skip'] += 1
    class_stats = [dict(name=k, **v) for k, v in
                   sorted(per_class.items(), key=lambda kv: _class_sort_key(kv[0]))]

    # v1.12.1 编排容量统计：学生总数/参考数、考场总数/总容量、各选科人数与可用容量差额
    stats = {
        'students': len(students), 'attend': len(scope_students),
        'rooms': len(rooms), 'capacity': sum(r.capacity or 0 for r in rooms),
        'per_sel': {},
    }
    for s in scope_students:
        key = (s.subject_selection or '默认') or '默认'
        d = stats['per_sel'].setdefault(key, {'students': 0, 'capacity': 0})
        d['students'] += 1
    for r in rooms:
        if r.is_universal:  # 通用考场容量计入每个有参考学生的选科
            for d in stats['per_sel'].values():
                d['capacity'] += r.capacity or 0
            if not stats['per_sel']:
                stats['per_sel']['默认'] = {'students': 0, 'capacity': r.capacity or 0}
        elif r.subject in stats['per_sel']:
            stats['per_sel'][r.subject]['capacity'] += r.capacity or 0
        else:
            stats['per_sel'][r.subject] = {'students': 0, 'capacity': r.capacity or 0}
    # v1.12.1 选科选项（批次设置弹窗中按选科设前后缀）
    sel_options = sorted({s.subject_selection for s in students if s.subject_selection})
    # v1.19.40.2 步骤③下方学生名单：已编排者按「考场 → 座号」升序（考场号已补零，可直接字符串比较）
    arranged_sorted = sorted(arranged,
                             key=lambda s: (str(s.room_no or ''), s.seat_no or 0,
                                            s.student_no or ''))
    # v1.19.40.2 步骤②科目下拉：系统字典「选科」12 种 + 「全科」（= 通用/不限），
    # 再兜底补上本批次考场里出现过的历史值（如旧写法的「史政地」），避免下拉丢失当前值
    room_subject_options = ['全科'] + list(get_dict_values('subject') or [])
    for r in rooms:
        if r.subject and not r.is_universal and r.subject not in room_subject_options:
            room_subject_options.append(r.subject)
    return render_template('grades/affairs/detail.html',
                           affair=affair, students=students, rooms=rooms,
                           scope_students=scope_students, arranged=arranged,
                           numbered=numbered,
                           join_students=join_students, skip_students=skip_students,
                           grade_stat=grade_stat, class_stats=class_stats,
                           arranged_sorted=arranged_sorted,
                           room_subject_options=room_subject_options,
                           mode_label=MODE_LABEL,
                           stats=stats, sel_options=sel_options)


# ==================== 步骤① 学生名单：导入 / 模板 / 增删改 ====================

@bp.route('/affairs/<int:aid>/students/template')
@login_required
@perm_required('grades.edit')
def affair_students_template(aid):
    affair = _get_affair_checked(aid)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '学生信息表'
    headers = ['学号', '姓名', '班级', '选科组合', '是否参与考试', '固定考场', '固定座号',
               '考试科目', '自定义考号']
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=ci)
        c.font = _HF
        c.fill = _HFL
        c.alignment = _CENTER
        c.border = _TB
    # 预填系统该年级学生（与 Excel「学生信息表」口径一致，考务人员可直接编辑）
    # v1.19.40.2：只预填数字教学班且年级未毕业的学生（不分班/已转出/已毕业不进入成绩模板）
    from app.utils.helpers import get_graduated_grades
    _graduated = set(get_graduated_grades() or [])
    _grade_ok = affair.grade not in _graduated
    sys_stus = (Student.query.filter_by(grade=affair.grade)
                .order_by(Student.class_name, Student.student_number).all())
    sys_stus = [st for st in sys_stus if _grade_ok and is_teaching_class(st.class_name)]
    for st in sys_stus:
        ws.append([st.student_number, st.name, st.class_name,
                   st.subject_selection or '', '是', '', '', '', ''])
    for col, w in zip('ABCDEFGHI', [12, 10, 8, 10, 12, 10, 10, 16, 12]):
        ws.column_dimensions[col].width = w
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    fn = f'学生信息表模板_{affair.grade}.xlsx'
    return send_file(out, as_attachment=True,
                     download_name=fn, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def _header_index(ws, names):
    first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    idx = {}
    for i, cell in enumerate(first or []):
        name = str(cell).strip() if cell is not None else ''
        if name in names:
            idx[name] = i
    return idx


@bp.route('/affairs/<int:aid>/students/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_students_import(aid):
    affair = _get_affair_checked(aid)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    # L-8：统一上传校验（白名单 + 危险类型 + magic），不再只看扩展名
    ok, msg = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': msg})
    try:
        wb = openpyxl.load_workbook(f.stream, read_only=True, data_only=True)
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Excel 解析失败：{e}'})
    ws = wb.active
    idx = _header_index(ws, {'学号', '姓名', '班级', '选科组合', '是否参与考试',
                             '固定考场', '固定座号', '考试科目', '自定义考号'})
    if '学号' not in idx:
        return jsonify({'ok': False, 'msg': '缺少「学号」列，请使用模板'})
    added = 0
    skipped = 0
    skipped_no_class = 0
    sys_map = {s.student_number: s for s in Student.query.filter_by(grade=affair.grade).all()}
    for row in ws.iter_rows(min_row=2, values_only=True):
        no = str(row[idx['学号']]).strip() if idx['学号'] < len(row) and row[idx['学号']] is not None else ''
        if not no:
            continue
        exist = AffairStudent.query.filter_by(affair_id=aid, student_no=no).first()
        if exist:
            skipped += 1
            continue
        name = (str(row[idx['姓名']]).strip() if '姓名' in idx and idx['姓名'] < len(row) and row[idx['姓名']] else '')
        cls = (str(row[idx['班级']]).strip() if '班级' in idx and idx['班级'] < len(row) and row[idx['班级']] else '')
        sel = (str(row[idx['选科组合']]).strip() if '选科组合' in idx and idx['选科组合'] < len(row) and row[idx['选科组合']] else '')
        attend = str(row[idx['是否参与考试']]).strip() if '是否参与考试' in idx and idx['是否参与考试'] < len(row) and row[idx['是否参与考试']] else '是'
        fixed_room = (str(row[idx['固定考场']]).strip() if '固定考场' in idx and idx['固定考场'] < len(row) and row[idx['固定考场']] else '') or None
        fixed_seat = None
        if '固定座号' in idx and idx['固定座号'] < len(row) and row[idx['固定座号']]:
            try:
                fixed_seat = int(float(row[idx['固定座号']]))
            except (TypeError, ValueError):
                fixed_seat = None
        custom = (str(row[idx['自定义考号']]).strip() if '自定义考号' in idx and idx['自定义考号'] < len(row) and row[idx['自定义考号']] else '') or None
        # 用系统学生补全空白字段，保证与学籍一致
        sys_stu = sys_map.get(no)
        if sys_stu:
            name = name or sys_stu.name
            cls = cls or sys_stu.class_name
            sel = sel or (sys_stu.subject_selection or '')
        # v1.19.40.2：补全后仍无数字教学班的学生不导入成绩名单
        if not is_teaching_class(cls):
            skipped_no_class += 1
            continue
        st = AffairStudent(affair_id=aid, student_no=no, name=name, class_name=cls,
                           subject_selection=sel, subject=sel or '默认',
                           is_attend=(attend != '否'), fixed_room=fixed_room,
                           fixed_seat=fixed_seat, custom_number=custom)
        db.session.add(st)
        added += 1
    db.session.commit()
    wb.close()
    log_operation(current_user, '导入', '考务学生', aid,
                  f'新增{added}条/跳过{skipped}条'
                  + (f'/无班级{skipped_no_class}条' if skipped_no_class else ''),
                  module='grades')
    return jsonify({'ok': True, 'added': added, 'skipped': skipped})


@bp.route('/affairs/<int:aid>/students/sync', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_students_sync(aid):
    """v1.12.1 从学生学籍库同步本年级学生到考务名单（增量：新增+补全信息）

    v1.19.40.2 口径调整（用户明确）：
    - 无班级学生（不分班 / 已转出 / 离校 等非数字班级）与已毕业年级学生
      **不进入成绩治理模块**，同步时直接过滤掉；
    - 原「同时移除学籍已删的学生」复选框取消，改为**默认自动移除**：
      名单中任何不再属于“本年级 + 在读未毕业 + 数字班级”的学生（学籍已删、
      转入不分班、年级已毕业等）均同步剔除，无需手动勾选。
    """
    affair = _get_affair_checked(aid)
    from app.utils.helpers import get_graduated_grades
    graduated = set(get_graduated_grades() or [])
    all_stus = (Student.query.filter_by(grade=affair.grade)
                .order_by(Student.class_name, Student.student_number).all())
    # 有效学生：年级未毕业 + 数字教学班（不分班/已转出/离校等自然被排除）
    grade_ok = affair.grade not in graduated
    sys_stus = [st for st in all_stus if grade_ok and is_teaching_class(st.class_name)]
    exist = {s.student_no: s for s in AffairStudent.query.filter_by(affair_id=aid).all()}
    added = removed = skipped = 0
    for st in sys_stus:
        row = exist.get(st.student_number)
        if not row:
            row = AffairStudent(affair_id=aid, student_no=st.student_number)
            db.session.add(row)
            added += 1
        # 刷新基本信息，保留考务特有字段（固定考场/座号/自定义考号/是否参考）
        row.name = st.name
        row.class_name = st.class_name
        row.subject_selection = st.subject_selection or ''
        row.subject = row.subject_selection or '默认'
        if row.is_attend is None:
            row.is_attend = True
    # 默认移除：名单中已不属于有效集合的学生（学籍已删/无班级/年级已毕业）
    sys_nos = {st.student_number for st in sys_stus}
    for no, row in exist.items():
        if no not in sys_nos:
            db.session.delete(row)
            removed += 1
    skipped = len(all_stus) - len(sys_stus)   # 因无班级/已毕业而未导入的学籍数
    # v1.19.40.2 自动匹配「上一场不参加考试的学生」：取本年级**日期早于本批次**的最近一场考务批次，
    # 把其中标记为不参考的学号，在本批次一律同步为不参考（只 是→否；不反向把「否」改回「是」）。
    prev_affair = (ExamAffair.query
                   .filter(ExamAffair.grade == affair.grade,
                           ExamAffair.id != affair.id,
                           ExamAffair.exam_date < affair.exam_date)
                   .order_by(ExamAffair.exam_date.desc(), ExamAffair.id.desc()).first())
    non_attend_prev = set()
    if prev_affair is not None:
        non_attend_prev = {s.student_no for s in
                           AffairStudent.query.filter_by(affair_id=prev_affair.id,
                                                         is_attend=False).all()}
    matched = 0
    if non_attend_prev:
        for row in AffairStudent.query.filter_by(affair_id=aid).all():
            if row.student_no in non_attend_prev and row.is_attend:
                row.is_attend = False
                matched += 1
    db.session.commit()
    log_operation(current_user, '同步', '考务学生', aid,
                  f'新增{added}人/移除{removed}人/有效{len(sys_stus)}人/跳过无班{skipped}人'
                  + (f'/沿用上一场不参考{matched}人' if matched else ''),
                  module='grades')
    return jsonify({'ok': True, 'added': added, 'removed': removed,
                    'total': len(sys_stus), 'skipped_no_class': skipped,
                    'matched_non_attend': matched,
                    'prev_affair': (prev_affair.name if prev_affair else '')})


@bp.route('/affairs/<int:aid>/students/pick')
@login_required
@perm_required('grades.edit')
def affair_students_pick(aid):
    """v1.12.2 「加入学生」选择器数据源：返回本年级学籍学生，
    带 in_batch 标记（是否已在当前考务名单），供前端筛选/排序/搜索。

    v1.19.40.2：与 sync 同口径，只列数字教学班且年级未毕业的学生（无班级/已毕业不展示）。"""
    affair = _get_affair_checked(aid)
    from app.utils.helpers import get_graduated_grades
    graduated = set(get_graduated_grades() or [])
    grade_ok = affair.grade not in graduated
    have = {s.student_no for s in AffairStudent.query.filter_by(affair_id=aid).all()}
    rows = (Student.query.filter_by(grade=affair.grade)
            .order_by(Student.class_name, Student.student_number).all())
    return jsonify({'ok': True, 'students': [
        {'student_no': st.student_number, 'name': st.name, 'class_name': st.class_name,
         'subject_selection': st.subject_selection or '', 'gender': st.gender or '',
         'in_batch': st.student_number in have}
        for st in rows if grade_ok and is_teaching_class(st.class_name)]})


@bp.route('/affairs/<int:aid>/students/batch-add', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_students_batch_add(aid):
    """v1.12.2 从选择器批量加入：按学号从学籍库带出信息写入考务名单"""
    affair = _get_affair_checked(aid)
    nos = [n for n in (request.form.get('student_nos') or '').split(',') if n.strip()]
    if not nos:
        return jsonify({'ok': False, 'msg': '未选择学生'})
    exist = {s.student_no for s in AffairStudent.query.filter_by(affair_id=aid).all()}
    added = skipped = 0
    for st in Student.query.filter(Student.student_number.in_(nos)).all():
        if st.student_number in exist:
            continue
        # v1.19.40.2：无班级学生（不分班/已转出/离校等）不加入成绩治理名单
        if not is_teaching_class(st.class_name):
            skipped += 1
            continue
        db.session.add(AffairStudent(
            affair_id=aid, student_no=st.student_number, name=st.name,
            class_name=st.class_name, subject_selection=st.subject_selection or '',
            subject=st.subject_selection or '默认', is_attend=True))
        added += 1
    db.session.commit()
    log_operation(current_user, '加入', '考务学生', aid,
                  f'选择器加入{added}人' + (f'/跳过无班级{skipped}人' if skipped else ''),
                  module='grades')
    return jsonify({'ok': True, 'added': added, 'skipped_no_class': skipped})


@bp.route('/affairs/<int:aid>/students/<int:sid>/fixed', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_student_fixed(aid, sid):
    """v1.12.2 表格内直接编辑固定考场/固定座号（失焦即时保存）"""
    st = AffairStudent.query.get_or_404(sid)
    if st.affair_id != aid:
        abort(403)
    room = (request.form.get('fixed_room') or '').strip() or None
    seat_raw = (request.form.get('fixed_seat') or '').strip()
    try:
        seat = int(seat_raw) if seat_raw else None
    except ValueError:
        seat = None
    st.fixed_room, st.fixed_seat = room, seat
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/students/<int:sid>/attend', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_student_attend(aid, sid):
    """v1.19.40.2 步骤①「参考」就地点一下切换（本页不再提供「编辑学生信息」弹窗，
    但「是否参加本场考试」属考务信息，需要能手工修正，例如上一场缺考、本场恢复参考）。"""
    st = AffairStudent.query.get_or_404(sid)
    if st.affair_id != aid:
        abort(403)
    raw = (request.form.get('is_attend') or '').strip().lower()
    st.is_attend = raw in ('1', 'true', 'yes', 'on')
    db.session.commit()
    log_operation(current_user, '修改', '考务学生', sid,
                  f'[{st.name}] 参考={st.is_attend}', module='grades')
    return jsonify({'ok': True, 'is_attend': bool(st.is_attend)})


@bp.route('/affairs/<int:aid>/students/<int:sid>')
@login_required
@perm_required('grades.edit')
def affair_student_detail(aid, sid):
    st = AffairStudent.query.get_or_404(sid)
    if st.affair_id != aid:
        abort(403)
    return jsonify({
        'student_no': st.student_no, 'name': st.name, 'class_name': st.class_name,
        'subject_selection': st.subject_selection, 'is_attend': st.is_attend,
        'fixed_room': st.fixed_room, 'fixed_seat': st.fixed_seat, 'custom_number': st.custom_number,
    })


@bp.route('/affairs/<int:aid>/students/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_student_save(aid):
    affair = _get_affair_checked(aid)
    sid = request.form.get('sid')
    no = (request.form.get('student_no') or '').strip()
    if not no:
        return jsonify({'ok': False, 'msg': '学号必填'})
    if sid:
        st = AffairStudent.query.get_or_404(int(sid))
        if st.affair_id != aid:
            abort(403)
    else:
        st = AffairStudent.query.filter_by(affair_id=aid, student_no=no).first()
        if st:
            return jsonify({'ok': False, 'msg': f'学号 {no} 已存在'})
        st = AffairStudent(affair_id=aid, student_no=no)
        db.session.add(st)
    st.name = (request.form.get('name') or '').strip()
    st.class_name = (request.form.get('class_name') or '').strip()
    st.subject_selection = (request.form.get('subject_selection') or '').strip()
    st.subject = st.subject_selection or '默认'
    st.is_attend = (request.form.get('is_attend') == '1')
    st.fixed_room = (request.form.get('fixed_room') or '').strip() or None
    try:
        st.fixed_seat = int(request.form.get('fixed_seat')) if request.form.get('fixed_seat') else None
    except (TypeError, ValueError):
        st.fixed_seat = None
    st.custom_number = (request.form.get('custom_number') or '').strip() or None
    # v1.12.1 勾选后同步写入/更新学生学籍库（新增学生不再依赖 Excel 回导）
    if request.form.get('save_to_db') == '1':
        sys_stu = Student.query.filter_by(student_number=no).first()
        if not sys_stu:
            sys_stu = Student(student_number=no, name=st.name or '未命名',
                              gender=(request.form.get('gender') or '男'),
                              grade=affair.grade, class_name=st.class_name or '')
            db.session.add(sys_stu)
        else:
            sys_stu.name = st.name or sys_stu.name
            sys_stu.class_name = st.class_name or sys_stu.class_name
            sys_stu.subject_selection = st.subject_selection or sys_stu.subject_selection
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/students/<int:sid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_student_delete(aid, sid):
    st = AffairStudent.query.get_or_404(sid)
    if st.affair_id != aid:
        abort(403)
    db.session.delete(st)
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/students/clear', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_students_clear(aid):
    AffairStudent.query.filter_by(affair_id=aid).delete()
    db.session.commit()
    return jsonify({'ok': True})


# ==================== 步骤② 考场设置：导入 / 模板 / 增删改 ====================

@bp.route('/affairs/<int:aid>/rooms/template')
@login_required
@perm_required('grades.edit')
def affair_rooms_template(aid):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '考场信息表'
    headers = ['考场编号', '考场位置', '考场科目', '考场容量', '备注', '考号前缀']
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=ci)
        c.font = _HF
        c.fill = _HFL
        c.alignment = _CENTER
        c.border = _TB
    for col, w in zip('ABCDEF', [10, 16, 12, 10, 12, 10]):
        ws.column_dimensions[col].width = w
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name='考场信息表模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/affairs/<int:aid>/rooms/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_rooms_import(aid):
    affair = _get_affair_checked(aid)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    # L-8：统一上传校验（白名单 + 危险类型 + magic），不再只看扩展名
    ok, msg = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': msg})
    try:
        wb = openpyxl.load_workbook(f.stream, read_only=True, data_only=True)
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Excel 解析失败：{e}'})
    ws = wb.active
    idx = _header_index(ws, {'考场编号', '考场位置', '考场科目', '考场容量', '备注', '考号前缀'})
    if '考场编号' not in idx:
        return jsonify({'ok': False, 'msg': '缺少「考场编号」列'})
    added = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        no = _norm_room_no(str(row[idx['考场编号']]) if idx['考场编号'] < len(row) and row[idx['考场编号']] is not None else '')  # v1.12.2 编号补零
        if not no:
            continue
        room = AffairRoom.query.filter_by(affair_id=aid, room_no=no).first()
        if not room:
            room = AffairRoom(affair_id=aid, room_no=no)
            db.session.add(room)
            added += 1
        room.location = (str(row[idx['考场位置']]).strip() if '考场位置' in idx and idx['考场位置'] < len(row) and row[idx['考场位置']] else '') or ''
        room.subject = (str(row[idx['考场科目']]).strip() if '考场科目' in idx and idx['考场科目'] < len(row) and row[idx['考场科目']] else '') or '默认'
        cap = 0
        if '考场容量' in idx and idx['考场容量'] < len(row) and row[idx['考场容量']]:
            m = ''.join(ch for ch in str(row[idx['考场容量']]) if ch.isdigit())
            cap = int(m) if m else 0
        room.capacity = cap or 30
        # H-5：导入路径同样清洗（Excel 内容可被任意构造）
        room.prefix = (sanitize_prefix(str(row[idx['考号前缀']])) if '考号前缀' in idx and idx['考号前缀'] < len(row) and row[idx['考号前缀']] else '') or None
        room.note = (sanitize_label(str(row[idx['备注']]), 100) if '备注' in idx and idx['备注'] < len(row) and row[idx['备注']] else '') or ''
    db.session.commit()
    wb.close()
    return jsonify({'ok': True, 'added': added})


@bp.route('/affairs/<int:aid>/rooms/<int:rid>')
@login_required
@perm_required('grades.edit')
def affair_room_detail(aid, rid):
    room = AffairRoom.query.get_or_404(rid)
    if room.affair_id != aid:
        abort(403)
    return jsonify({
        'room_no': room.room_no, 'location': room.location, 'subject': room.subject,
        'capacity': room.capacity, 'prefix': room.prefix, 'note': room.note,
    })


@bp.route('/affairs/<int:aid>/rooms/save', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_room_save(aid):
    affair = _get_affair_checked(aid)
    rid = request.form.get('rid')
    no = _norm_room_no(request.form.get('room_no') or '')  # v1.12.2 编号补零
    if rid:
        room = AffairRoom.query.get_or_404(int(rid))
        if room.affair_id != aid:
            abort(403)
        # v1.12.2 编辑时编号与容量锁定不可改：编号由选取时自动生成、容量由考场库带出，
        # 改动会破坏已生成考号与编排一致性
    else:
        if not no:
            return jsonify({'ok': False, 'msg': '考场编号必填'})
        room = AffairRoom.query.filter_by(affair_id=aid, room_no=no).first()
        if room:
            return jsonify({'ok': False, 'msg': f'考场 {no} 已存在'})
        room = AffairRoom(affair_id=aid, room_no=no)
        db.session.add(room)
        try:
            room.capacity = int(request.form.get('capacity') or 30)
        except (TypeError, ValueError):
            room.capacity = 30
    # H-5：服务端字符白名单（前端转义之外的第二道防线）
    room.location = sanitize_label(request.form.get('location'), 50)
    room.subject = sanitize_label(request.form.get('subject') or '默认', 20) or '默认'
    room.prefix = sanitize_prefix(request.form.get('prefix')) or None
    room.note = sanitize_label(request.form.get('note'), 100)
    # v1.12.1 可选：把该房间位置/容量存入考场库，供后续批次直接选取
    if request.form.get('save_to_lib') == '1' and room.location:
        lib_row = AffairRoomLib.query.filter_by(location=room.location).first()
        if not lib_row:
            lib_row = AffairRoomLib(location=room.location)
            db.session.add(lib_row)
        lib_row.capacity = room.capacity
        lib_row.note = room.note
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/rooms/<int:rid>/patch', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_room_patch(aid, rid):
    """v1.19.40.2 步骤② 行内编辑：科目 / 容量 / 备注 就地修改（不再走「编辑」按钮弹窗）。

    只更新提交上来的字段（部分更新），避免前端行内编辑互相覆盖。
    科目「全科」等价于通用考场（存为「默认」，`is_universal` 为真）。
    """
    _get_affair_checked(aid)
    room = AffairRoom.query.get_or_404(rid)
    if room.affair_id != aid:
        abort(403)
    if 'subject' in request.form:
        sub = sanitize_label(request.form.get('subject'), 20) or '默认'
        room.subject = '默认' if sub in ('全科', '通用', '全部') else sub
    if 'capacity' in request.form:
        try:
            cap = int(str(request.form.get('capacity') or '').strip())
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'msg': '容量必须是整数'})
        if cap < 1 or cap > 500:
            return jsonify({'ok': False, 'msg': '容量需在 1~500 之间'})
        room.capacity = cap
    if 'note' in request.form:
        room.note = sanitize_label(request.form.get('note'), 100)
    db.session.commit()
    log_operation(current_user, '修改', '考务考场', rid,
                  f'考场{room.room_no} 科目={room.subject} 容量={room.capacity}',
                  module='grades')
    return jsonify({'ok': True, 'subject': room.subject, 'capacity': room.capacity,
                    'is_universal': bool(room.is_universal), 'note': room.note or ''})


@bp.route('/affairs/<int:aid>/rooms/capacity', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_rooms_capacity(aid):
    """v1.19.40.2 步骤② 考场容量批量操作。

    - `mode=all`：把本批次**全部考场**容量设为 `capacity`（1~500）；
    - `mode=prev`：按「同年级 + **同一房间**」沿用上一场（日期更早的最近一批次）的容量；
      房间身份优先用「位置」匹配，位置为空时用「考场编号」兜底。
    返回 `{ok, updated, total, src|capacity}`。
    """
    affair = _get_affair_checked(aid)
    rooms = AffairRoom.query.filter_by(affair_id=aid).all()
    if not rooms:
        return jsonify({'ok': False, 'msg': '本批次还没有考场，先在左侧「考场库」加入'})

    if (request.form.get('mode') or 'all').strip() == 'prev':
        prev = (ExamAffair.query
                .filter(ExamAffair.grade == affair.grade,
                        ExamAffair.id != affair.id,
                        ExamAffair.exam_date < affair.exam_date)
                .order_by(ExamAffair.exam_date.desc(), ExamAffair.id.desc()).first())
        if prev is None:
            return jsonify({'ok': False, 'msg': '本年级没有更早的考务批次，无可沿用'})
        prev_rooms = AffairRoom.query.filter_by(affair_id=prev.id).all()
        by_loc = {r.location: (r.capacity or 30) for r in prev_rooms if r.location}
        by_no = {r.room_no: (r.capacity or 30) for r in prev_rooms}
        updated = unmatched = 0
        for r in rooms:
            cap = by_loc.get(r.location) if r.location else None
            if cap is None:
                cap = by_no.get(r.room_no)
            if cap is None:
                unmatched += 1
                continue
            if cap != (r.capacity or 0):
                r.capacity = cap
                updated += 1
        db.session.commit()
        log_operation(current_user, '设置', '考务考场容量', aid,
                      f'沿用「{prev.name}」：更新{updated}个/未匹配{unmatched}个', module='grades')
        return jsonify({'ok': True, 'updated': updated, 'unmatched': unmatched,
                        'total': len(rooms), 'src': prev.name})

    try:
        cap = int(str(request.form.get('capacity') or '').strip())
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': '容量必须是整数'})
    if cap < 1 or cap > 500:
        return jsonify({'ok': False, 'msg': '容量需在 1~500 之间'})
    for r in rooms:
        r.capacity = cap
    db.session.commit()
    log_operation(current_user, '设置', '考务考场容量', aid,
                  f'全部 {len(rooms)} 个考场容量设为 {cap}', module='grades')
    return jsonify({'ok': True, 'updated': len(rooms), 'total': len(rooms), 'capacity': cap})


@bp.route('/affairs/<int:aid>/rooms/<int:rid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_room_delete(aid, rid):
    room = AffairRoom.query.get_or_404(rid)
    if room.affair_id != aid:
        abort(403)
    db.session.delete(room)
    db.session.commit()
    return jsonify({'ok': True})


# ==================== v1.12.1 考场房间库（跨批次共享，选取后自动带出位置/容量） ====================

@bp.route('/affairs/room-lib/list')
@login_required
@perm_required('grades.edit')
def affair_room_lib_list():
    rows = AffairRoomLib.query.order_by(AffairRoomLib.location).all()
    return jsonify({'ok': True, 'rooms': [
        {'id': r.id, 'location': r.location, 'capacity': r.capacity, 'note': r.note}
        for r in rows]})


@bp.route('/affairs/room-lib/<int:lid>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_room_lib_delete(lid):
    row = AffairRoomLib.query.get_or_404(lid)
    db.session.delete(row)
    db.session.commit()
    return jsonify({'ok': True})


@bp.route('/affairs/<int:aid>/rooms/from-lib', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_room_from_lib(aid):
    """v1.12.1 从考场房间库选取房间生成考场：
    自动生成考场编号（现有最大数字+1）、确定考场科目（默认，可再编辑）、带出默认容量"""
    AffairRoom.query.get_or_404 and ExamAffair.query.get_or_404(aid)  # 批次存在性校验
    lid = request.form.get('lib_id')
    lib_row = AffairRoomLib.query.get_or_404(int(lid))
    exist_rooms = AffairRoom.query.filter_by(affair_id=aid).all()
    if lib_row.location in {r.location for r in exist_rooms}:
        return jsonify({'ok': False, 'msg': f'考场位置 {lib_row.location} 已在本批次中'})
    nums = []
    for r in exist_rooms:
        digits = ''.join(ch for ch in r.room_no if ch.isdigit())
        if digits:
            nums.append(int(digits))
    # v1.12.2 考场编号统一补零为两位（01、02…）
    new_no = _norm_room_no((max(nums) + 1) if nums else 1)
    room = AffairRoom(affair_id=aid, room_no=new_no, location=lib_row.location,
                      subject='默认', capacity=lib_row.capacity or 30)
    db.session.add(room)
    db.session.commit()
    log_operation(current_user, '选取', '考务考场', aid,
                  f'从库选房间 {lib_row.location} → 考场{new_no}', module='grades')
    return jsonify({'ok': True, 'id': room.id, 'room_no': room.room_no,
                    'location': room.location, 'capacity': room.capacity, 'subject': room.subject})


# ==================== v1.12.1 批次设置：编排模式 / 非参考处理 / 选科前后缀 ====================

@bp.route('/affairs/<int:aid>/settings', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_settings(aid):
    affair = _get_affair_checked(aid)
    sm = request.form.get('selection_mode')
    if sm in ('selected', 'plain'):
        affair.selection_mode = sm
    nm = request.form.get('non_attend_mode')
    if nm in ('skip', 'tail'):
        affair.non_attend_mode = nm
    # v1.19.40.2 步骤② 去掉「考场前缀」列后，批次默认前缀成为唯一兜底 —— 允许在步骤③设置
    if 'default_prefix' in request.form:
        affair.default_prefix = sanitize_prefix(request.form.get('default_prefix'), 16) or None
    # 选科考号前后缀：前端以 prefix_<选科> / suffix_<选科> 成对提交，sels 为选科列表 JSON
    try:
        sel_list = json.loads(request.form.get('sels') or '[]')
    except (TypeError, ValueError, json.JSONDecodeError):
        sel_list = []
    if isinstance(sel_list, list):
        cfg = {}
        for sel in sel_list:
            sel = str(sel).strip()
            if not sel:
                continue
            p = sanitize_prefix(request.form.get(f'prefix_{sel}'), 16)    # H-5
            sfx = sanitize_prefix(request.form.get(f'suffix_{sel}'), 16)  # H-5
            if p or sfx:
                cfg[sel] = {'prefix': p, 'suffix': sfx}
        affair.subject_prefixes = json.dumps(cfg, ensure_ascii=False) if cfg else None
    db.session.commit()
    log_operation(current_user, '设置', '考务批次', aid,
                  f'模式={affair.selection_mode}/非参考={affair.non_attend_mode}', module='grades')
    return jsonify({'ok': True})


# ==================== 步骤③ 编排与考号生成（镜像宏 编排考场考号2） ====================

def _prev_stage_rank_map(affair):
    """上一场**同阶段**考试的校内排名 → {学号: 方向排名}，供考场分层排序

    口径（用户 2026-10-10）：同一场同阶段考试（同年级 + 同 exam_type）中
    日期早于本场、取最近一场；用「总分行」的 rank_dir（文理各自排，选科组合之间可比）。
    找不到（首次考试）→ 返回 ({}, '')，分配自然退化为随机，不报错。
    """
    from app.models.grades import Exam, ExamScore, TOTAL_SUBJECT
    if not affair.exam_id:
        return {}, ''
    cur = Exam.query.get(affair.exam_id)
    if cur is None:
        return {}, ''
    prev = (Exam.query.filter(Exam.grade == cur.grade,
                              Exam.exam_type == cur.exam_type,
                              Exam.exam_date < cur.exam_date)
            .order_by(Exam.exam_date.desc()).first())
    if prev is None:
        return {}, ''
    rows = (ExamScore.query.filter_by(exam_id=prev.id)
            .filter(ExamScore.subject == TOTAL_SUBJECT).all())
    return ({r.student_no: (r.rank_dir or 10 ** 9) for r in rows}, prev.name)


def _arrange(affair, students, rooms, mode, log, layered=False, rank_map=None):
    """镜像 Excel 宏的编排逻辑：固定座位优先 → 同班邻座限制 → 随机分配。
    v1.12.1：支持不选科模式、非参考学生尾场处理、选科考号前后缀。
    v1.19.0：**分层分配**（layered=True）—— 同一选科组内按上一场同阶段考试的
    校内排名升序填座，考场按房间号顺序、座位按号从前往后依次填满，
    即“前 N 个考场共 M 座 → 第 1~M 名填这几个考场”；无排名数据则退化随机。
    返回 (assigned_count, failed_count)"""
    rank_map = rank_map or {}
    room_by_no = {r.room_no: r for r in rooms}
    # 座位占用表
    seats = {r.room_no: [None] * r.capacity for r in rooms}
    used = {r.room_no: set() for r in rooms}
    # v1.12.1 不选科模式：全体一组，所有考场通用
    plain = (affair.selection_mode == 'plain')
    # v1.12.1 选科考号前后缀配置：{"物化生":{"prefix":"1701","suffix":""}}
    sel_cfg = affair.get_subject_prefixes()

    def neighbors(i, cap):
        n = []
        if i > 0:
            n.append(i - 1)
        if i < cap - 1:
            n.append(i + 1)
        return n

    def exam_number_for(student, room_no, seat):
        if mode == 0:
            # v1.12.1 前后缀优先级：选科配置 > 考场前缀 > 批次默认前缀（满足全校同前缀或文理分别前缀）
            cfg = sel_cfg.get((student.subject_selection or '') or '')
            prefix = (cfg or {}).get('prefix') or (room_by_no[room_no].prefix or affair.default_prefix or '1701')
            suffix = (cfg or {}).get('suffix') or ''
            return f'{prefix}{_pad2(room_no)}{_pad2(seat)}{suffix}'
        if mode == 1:
            return student.student_no
        return student.custom_number or f'未填考号_{student.name}'

    assigned = 0
    failed = 0

    # 1) 固定座位优先
    fixed = [s for s in students if s.is_attend and s.fixed_room and s.fixed_seat]
    for s in fixed:
        room = room_by_no.get(s.fixed_room)
        if not room:
            log.append(f'【固定考场】[{s.name}] 指定考场 {s.fixed_room} 不存在，跳过')
            continue
        if s.fixed_seat > room.capacity or s.fixed_seat in used[room.room_no]:
            log.append(f'【固定座位】[{s.name}] 座位超出容量或已占用，跳过')
            continue
        seats[room.room_no][s.fixed_seat - 1] = s
        used[room.room_no].add(s.fixed_seat)
        s.room_no = room.room_no
        s.seat_no = s.fixed_seat
        s.exam_number = exam_number_for(s, room.room_no, s.fixed_seat)
        assigned += 1

    # 2) 按选科分组，分配无固定座位学生
    groups = {}
    for s in students:
        if not s.is_attend or s.room_no:
            continue
        key = (s.subject_selection or '默认') or '默认'
        groups.setdefault(key, []).append(s)

    MAX_SAME_CLASS = 1
    for key, group in groups.items():
        # v1.19.0 分层分配：同一选科组内按上一场同阶段考试排名升序（无排名排最后）
        if layered:
            group.sort(key=lambda s: (rank_map.get(s.student_no, 10 ** 9),
                                      s.student_no or ''))
        # v1.12.1 不选科模式：全体一组，考场全部通用；选科模式按科目匹配
        # v1.19.0 固定按房间号排序，保证分层填充“从第一考场往后”可预期
        avail = sorted([(r.room_no, r) for r in rooms
                        if len(used[r.room_no]) < r.capacity
                        and (plain or r.is_universal or r.subject == key)],
                       key=lambda t: t[0])
        for s in group:
            candidates = []
            for rno, r in avail:
                if len(used[rno]) >= r.capacity:
                    continue
                for i in range(r.capacity):
                    if (i + 1) in used[rno]:
                        continue
                    same = sum(1 for nb in neighbors(i, r.capacity)
                               if seats[rno][nb] and seats[rno][nb].class_name == s.class_name)
                    if same <= MAX_SAME_CLASS:
                        candidates.append((rno, i + 1))
            if not candidates:
                # 放宽：任意空位
                for rno, r in avail:
                    if len(used[rno]) >= r.capacity:
                        continue
                    for i in range(r.capacity):
                        if (i + 1) not in used[rno]:
                            candidates.append((rno, i + 1))
            if not candidates:
                log.append(f'【分配失败】[{s.name}] 无可用座位（选科 {key}）')
                failed += 1
                continue
            if layered:
                # 分层：取“考场号→座号”最靠前的空位，实现容量累积分段
                candidates.sort(key=lambda x: (x[0], x[1]))
                rno, seat = candidates[0]
            else:
                rno, seat = random.choice(candidates)
            seats[rno][seat - 1] = s
            used[rno].add(seat)
            s.room_no = rno
            s.seat_no = seat
            s.exam_number = exam_number_for(s, rno, seat)
            assigned += 1
    # v1.12.1 非参考学生尾场处理：tail 模式下安排到同选科（或通用）考场靠后座号；
    # skip 模式（默认）不安排考场，保持 room_no 为空
    if affair.non_attend_mode == 'tail':
        for s in [x for x in students if not x.is_attend and not x.room_no and not x.fixed_room]:
            key = (s.subject_selection or '默认') or '默认'
            avail = [r for r in rooms if len(used[r.room_no]) < r.capacity
                     and (plain or r.is_universal or r.subject == key)]
            placed = False
            for r in sorted(avail, key=lambda x: x.room_no):
                seat = next((i + 1 for i in range(r.capacity - 1, -1, -1)
                             if (i + 1) not in used[r.room_no]), None)  # 从尾座往前找
                if seat:
                    seats[r.room_no][seat - 1] = s
                    used[r.room_no].add(seat)
                    s.room_no = r.room_no
                    s.seat_no = seat
                    s.exam_number = exam_number_for(s, r.room_no, seat)
                    assigned += 1
                    placed = True
                    break
            if not placed:
                log.append(f'【尾场】[{s.name}] 无可用空位，未安排')
    return assigned, failed


@bp.route('/affairs/<int:aid>/arrange', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_arrange(aid):
    affair = _get_affair_checked(aid)
    try:
        mode = int(request.form.get('mode', affair.mode))
    except (TypeError, ValueError):
        mode = 0
    if mode not in (0, 1, 2):
        mode = 0
    # 重新编排前清空旧结果
    AffairStudent.query.filter_by(affair_id=aid).update(
        {AffairStudent.room_no: None, AffairStudent.seat_no: None, AffairStudent.exam_number: None})
    affair.mode = mode
    students = AffairStudent.query.filter_by(affair_id=aid).all()
    rooms = AffairRoom.query.filter_by(affair_id=aid).all()
    if not rooms:
        return jsonify({'ok': False, 'msg': '请先在「考场设置」中添加考场'})
    scope = [s for s in students if s.is_attend]
    # v1.19.0 分层分配：同一选科组内按上一场同阶段考试的校内排名填座
    # （首次考试无历史成绩 → 自然退化为随机，不阻断）
    layered = request.form.get('layered') in ('1', 'true', 'on')
    rank_map, prev_name = ({}, '')
    if layered:
        rank_map, prev_name = _prev_stage_rank_map(affair)
    log = [f'当前模式：{MODE_LABEL[mode]}']
    if layered:
        log.append('分层依据：' + (
            f'上一场同阶段考试「{prev_name}」的校内排名（{len(rank_map)} 人有排名）'
            if rank_map else '无上一场同阶段成绩，本次退化为随机编排'))
    assigned, failed = _arrange(affair, students, rooms, mode, log,
                                layered=layered, rank_map=rank_map)
    affair.status = 'arranged' if assigned else 'draft'
    db.session.commit()
    log_operation(current_user, '编排', '考务批次', aid,
                  f'{MODE_LABEL[mode]}，分配{assigned}人/失败{failed}人', module='grades')
    return jsonify({'ok': True, 'assigned': assigned, 'failed': failed,
                    'scope': len(scope), 'log': log})


@bp.route('/affairs/<int:aid>/numbers/import', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_numbers_import(aid):
    """v1.19.40.2 步骤③「自定义模式 → 使用自定义考号」的批量导入考号。

    上传 Excel（表头需含「学号」与「考号」两列），把考号写入名单的 `custom_number`；
    之后以「自定义考号」模式编排时，即用该值作为最终考号（`_arrange` 模式2）。
    """
    _get_affair_checked(aid)
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'ok': False, 'msg': '请选择 Excel 文件'})
    ok, msg = validate_upload(f.filename, allowed_exts=['xlsx', 'xls'], stream=f.stream)
    if not ok:
        return jsonify({'ok': False, 'msg': msg})
    try:
        wb = openpyxl.load_workbook(f.stream, read_only=True, data_only=True)
    except Exception as e:
        return jsonify({'ok': False, 'msg': f'Excel 解析失败：{e}'})
    ws = wb.active
    idx = _header_index(ws, {'学号', '考号', '自定义考号'})
    if '学号' not in idx:
        return jsonify({'ok': False, 'msg': '缺少「学号」列'})
    num_key = '考号' if '考号' in idx else ('自定义考号' if '自定义考号' in idx else None)
    if num_key is None:
        return jsonify({'ok': False, 'msg': '缺少「考号」列（表头建议：学号 / 姓名 / 考号）'})
    by_no = {s.student_no: s for s in AffairStudent.query.filter_by(affair_id=aid).all()}
    updated = 0
    missing = 0
    empty = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        no = (str(row[idx['学号']]).strip()
              if idx['学号'] < len(row) and row[idx['学号']] is not None else '')
        if not no:
            continue
        num = (str(row[idx[num_key]]).strip()
               if idx[num_key] < len(row) and row[idx[num_key]] is not None else '')
        st = by_no.get(no)
        if st is None:
            missing += 1
            continue
        if not num:
            empty += 1
            continue
        st.custom_number = num
        updated += 1
    db.session.commit()
    wb.close()
    log_operation(current_user, '导入', '考务考号', aid,
                  f'写入考号 {updated} 条' + (f'/未匹配 {missing} 条' if missing else ''),
                  module='grades')
    return jsonify({'ok': True, 'updated': updated, 'missing': missing, 'empty': empty})


# ==================== 步骤④ 导出：按班级 / 按考场 / 竖版桌签 ====================

def _arranged_sorted(affair):
    return [s for s in AffairStudent.query.filter_by(affair_id=affair.id).all()
            if s.room_no]


def _write_sheet(ws, headers, rows):
    # M-4：学生姓名/班级/考场位置来自导入数据，统一做公式注入转义
    rows = [xl_row(r) for r in rows]
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        c = ws.cell(row=1, column=ci)
        c.font = _HF
        c.fill = _HFL
        c.alignment = _CENTER
        c.border = _TB
    for r in rows:
        ws.append(xl_row(r))   # M-4
        for ci in range(1, len(headers) + 1):
            ws.cell(row=ws.max_row, column=ci).border = _TB


@bp.route('/affairs/<int:aid>/export/class')
@login_required
@perm_required('grades.edit')
def affair_export_class(aid):
    affair = _get_affair_checked(aid)
    stus = sorted(_arranged_sorted(affair),
                  key=lambda s: (s.class_name or '', s.name or '', s.seat_no or 0))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '按班级学生信息'
    _write_sheet(ws, ['班级', '姓名', '考场位置', '考号', '考场编号', '座号'],
                 [[s.class_name, s.name, _room_loc(affair, s.room_no),
                   s.exam_number, s.room_no, _pad2(s.seat_no)] for s in stus])
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=safe_download_name(   # L-9
                         f'按班级学生信息_{affair.name}.xlsx', '按班级学生信息.xlsx'),
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@bp.route('/affairs/<int:aid>/export/room')
@login_required
@perm_required('grades.edit')
def affair_export_room(aid):
    affair = _get_affair_checked(aid)
    stus = sorted(_arranged_sorted(affair),
                  key=lambda s: (s.room_no or '', s.seat_no or 0))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '按考场学生信息'
    _write_sheet(ws, ['考场编号', '座号', '考号', '姓名', '班级', '考场位置'],
                 [[s.room_no, _pad2(s.seat_no), s.exam_number, s.name,
                   s.class_name, _room_loc(affair, s.room_no)] for s in stus])
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=safe_download_name(   # L-9
                         f'按考场学生信息_{affair.name}.xlsx', '按考场学生信息.xlsx'),
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


def _room_loc(affair, room_no):
    room = AffairRoom.query.filter_by(affair_id=affair.id, room_no=room_no).first()
    return room.location if room else ''


# ==================== v1.12.2 A4 打印版 PDF 导出（reportlab） ====================

def _pdf_resp(buf, filename):
    return send_file(buf, as_attachment=True, download_name=filename, mimetype='application/pdf')


def _room_loc_map(affair):
    """一次性取 {考场编号: 位置}，避免桌签/名单逐条查库（N+1）"""
    return {r.room_no: r.location for r in
            AffairRoom.query.filter_by(affair_id=affair.id).all()}


@bp.route('/affairs/<int:aid>/cards/pdf')
@login_required
@perm_required('grades.edit')
def affair_cards_pdf(aid):
    """考试桌签 PDF：一页 A4 排 3 栏 × 15 行 = 45 张小标签，裁开贴桌"""
    from app.utils.affair_pdf import desk_cards_pdf
    affair = _get_affair_checked(aid)
    stus = sorted(_arranged_sorted(affair),
                  key=lambda s: (s.room_no or '', s.seat_no or 0))
    if not stus:
        abort(400, description='尚未编排，无桌签可导出')
    loc_map = _room_loc_map(affair)
    buf = desk_cards_pdf(stus, lambda rn: loc_map.get(rn, ''))
    log_operation(current_user, '导出', '桌签PDF', aid, f'{len(stus)}人', module='grades')
    return _pdf_resp(buf, f'桌签_{affair.name}.pdf')


@bp.route('/affairs/<int:aid>/export/class/pdf')
@login_required
@perm_required('grades.edit')
def affair_export_class_pdf(aid):
    """按班级学生名单 A4 打印版 PDF：v1.12.2 一个班从新的一页开始"""
    from app.utils.affair_pdf import seating_list_sections
    affair = _get_affair_checked(aid)
    stus = sorted(_arranged_sorted(affair),
                  key=lambda s: (s.class_name or '', s.name or '', s.seat_no or 0))
    loc_map = _room_loc_map(affair)
    # 按班级分组（保持排序后的班级顺序）
    groups = {}
    for s in stus:
        groups.setdefault(s.class_name or '未分班', []).append(
            [s.name, s.exam_number, s.room_no, _pad2(s.seat_no),
             loc_map.get(s.room_no, '')])
    sections = [(f'{cls}（共 {len(rows)} 人）', rows) for cls, rows in groups.items()]
    buf = seating_list_sections(
        f'{affair.name}　按班级学生名单（共 {len(stus)} 人 {len(sections)} 个班）',
        ['姓名', '考号', '考场编号', '座号', '考场位置'],
        sections, widths_mm=[26, 36, 18, 14, 50])
    return _pdf_resp(buf, f'按班级学生名单_{affair.name}.pdf')


@bp.route('/affairs/<int:aid>/export/room/pdf')
@login_required
@perm_required('grades.edit')
def affair_export_room_pdf(aid):
    """按考场学生名单 A4 打印版 PDF（监考教师用）：v1.12.2 一个考场从新的一页开始"""
    from app.utils.affair_pdf import seating_list_sections
    affair = _get_affair_checked(aid)
    stus = sorted(_arranged_sorted(affair),
                  key=lambda s: (s.room_no or '', s.seat_no or 0))
    loc_map = _room_loc_map(affair)
    # 按考场分组（保持排序后的考场顺序）
    groups = {}
    for s in stus:
        groups.setdefault(s.room_no or '未分配', []).append(
            [_pad2(s.seat_no), s.exam_number, s.name, s.class_name])
    sections = [(f'考场 {rn}（{loc_map.get(rn, "")}，共 {len(rows)} 人）', rows)
                for rn, rows in groups.items()]
    buf = seating_list_sections(
        f'{affair.name}　按考场学生名单（共 {len(stus)} 人 {len(sections)} 个考场）',
        ['座号', '考号', '姓名', '班级'],
        sections, widths_mm=[14, 40, 26, 20])
    return _pdf_resp(buf, f'按考场学生名单_{affair.name}.pdf')


# v1.12.2 网页版桌签预览路由已删除：千人级渲染会卡死浏览器，统一改走 /cards/pdf


@bp.route('/affairs/<int:aid>/cards/export')
@login_required
@perm_required('grades.edit')
def affair_cards_export(aid):
    """竖版桌签导出（三栏布局，对应 Excel 竖版桌签（可打印））"""
    affair = _get_affair_checked(aid)
    stus = sorted(_arranged_sorted(affair),
                  key=lambda s: (s.room_no or '', s.seat_no or 0))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '竖版桌签'
    # 表头（每栏 4 列：考号/姓名/班级/考场-座号）
    per = 3
    headers = []
    for _ in range(per):
        headers += ['学号/考号', '姓名', '班级', '考场-座号']
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        ws.cell(row=1, column=ci).font = _HF
        ws.cell(row=1, column=ci).fill = _HFL
        ws.cell(row=1, column=ci).alignment = _CENTER
    # 按三栏分块写入
    rows = []
    for i in range(0, len(stus), per):
        chunk = stus[i:i + per]
        row = []
        for s in chunk:
            row += [s.exam_number, s.name, s.class_name, f'{s.room_no}-{_pad2(s.seat_no)}']
        while len(row) < per * 4:
            row += ['', '', '', '']
        rows.append(row)
    for r in rows:
        ws.append(xl_row(r))   # M-4
    # 列宽
    for ci in range(1, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = 14
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=safe_download_name(   # L-9
                         f'竖版桌签_{affair.name}.xlsx', '竖版桌签.xlsx'),
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ==================== 步骤⑤ 成绩关联：绑定考试 ====================

@bp.route('/affairs/<int:aid>/link-exam', methods=['POST'])
@login_required
@perm_required('grades.edit')
def affair_link_exam(aid):
    affair = _get_affair_checked(aid)
    exam_id = request.form.get('exam_id')
    if exam_id:
        exam = Exam.query.get(int(exam_id))
        if not exam or exam.grade != affair.grade:
            return jsonify({'ok': False, 'msg': '考试与批次年级不一致'})
        affair.exam_id = exam.id
        affair.status = 'linked' if affair.status != 'draft' else affair.status
    else:
        affair.exam_id = None
    db.session.commit()
    log_operation(current_user, '关联', '考务批次', aid, f'绑定考试 {affair.exam_id}', module='grades')
    return jsonify({'ok': True})

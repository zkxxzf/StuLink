# StuLink v1.9.0 2026-09-03
# 成绩管理：考试管理 + 成绩导入向导路由
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import io
import json
import uuid
from datetime import date, datetime

import openpyxl
from flask import (Blueprint, render_template, request, jsonify, flash,
                   redirect, url_for, send_file, current_app, abort)
from flask_login import login_required, current_user
from app.extensions import db
from app.models.grades import Exam, ExamScore, SUBJECTS, TOTAL_SUBJECT
from app.models import Student
from app.modules.grades import bp
from app.modules.grades.services import import_service, store_service, ranking
from app.modules.grades.services.import_service import ParseError
from app.modules.grades.utils import term_of_date, delete_cache_prefix
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

EXAM_STATUS_LABEL = {'draft': '未导入', 'imported': '已导入', 'dirty': '待重算'}


def _grade_options():
    from app.modules.grades.services.scope import visible_grades
    return visible_grades(current_user)


def _exam_date_from(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date.today()


# ==================== 考试列表 / 新建 ====================

@bp.route('/exams')
@login_required
@perm_required('grades.edit')
def exams_list():
    grade = request.args.get('grade', '')
    q = Exam.query
    if grade:
        q = q.filter_by(grade=grade)
    exams = q.order_by(Exam.exam_date.desc(), Exam.id.desc()).all()
    counts = {}
    rows = ExamScore.query.filter(ExamScore.exam_id.in_([e.id for e in exams] or [0]),
                                  ExamScore.subject == TOTAL_SUBJECT).all()
    for r in rows:
        counts[r.exam_id] = counts.get(r.exam_id, 0) + 1
    return render_template('grades/exam_list.html', exams=exams, counts=counts,
                           grade=grade, grade_options=_grade_options(),
                           status_label=EXAM_STATUS_LABEL)


@bp.route('/exams/new', methods=['GET', 'POST'])
@login_required
@perm_required('grades.edit')
def exam_new():
    if request.method == 'POST':
        grade = (request.form.get('grade') or '').strip()
        exam_date = _exam_date_from(request.form.get('exam_date'))
        name = (request.form.get('name') or '').strip()
        exam_type = (request.form.get('exam_type') or '').strip()
        if not grade or not name:
            flash('请选择年级并填写考试名称', 'danger')
            return render_template('grades/exam_form.html', grade_options=_grade_options(),
                                   grade=grade, exam_date=exam_date.isoformat(), name=name,
                                   exam_type=exam_type, default_date=date.today().isoformat())
        dup = Exam.query.filter_by(grade=grade, name=name).first()
        if dup:
            flash(f'同年级已存在同名考试「{name}」，建议修改名称加以区分', 'warning')
        exam = Exam(grade=grade, name=name, exam_date=exam_date,
                    exam_type=exam_type or '其他', term=term_of_date(exam_date),
                    operator_id=current_user.id)
        db.session.add(exam)
        db.session.commit()
        log_operation(current_user, '新建', '考试', exam.id,
                      f'{grade}{name}（{exam_date}）', module='grades')
        flash('考试已创建，接下来导入成绩', 'success')
        return redirect(url_for('grades.exam_detail', exam_id=exam.id))
    return render_template('grades/exam_form.html', grade_options=_grade_options(),
                           grade='', exam_date=date.today().isoformat(), name='',
                           exam_type='月考', default_date=date.today().isoformat())


# ==================== 考试详情 / 成绩浏览 / 修正 / 重算 / 删除 ====================

@bp.route('/exams/<int:exam_id>')
@login_required
@perm_required('grades.edit')
def exam_detail(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    rows = (ExamScore.query.filter_by(exam_id=exam_id)
            .order_by(ExamScore.subject == TOTAL_SUBJECT, ExamScore.subject,
                      ExamScore.rank_dir).all())
    # 学籍状态（徽标用，运行时以主库为准）
    st_map = {}
    nos = {r.student_no for r in rows}
    for i in range(0, len(nos), 800):
        chunk = list(nos)[i:i + 800]
        for s in Student.query.filter(Student.student_number.in_(chunk)).all():
            st_map[str(s.student_number)] = s.enrollment_status or ''
    data = {}
    for r in rows:
        data.setdefault(r.student_no, {
            'no': r.student_no, 'name': r.student_name, 'class_name': r.class_name,
            'direction': r.direction, 'selection': r.subject_selection,
            'status': st_map.get(r.student_no, ''),
            'total': None, 'total_sid': None, 'rank': None, 'move': None, 'subjects': {},
        })
        item = data[r.student_no]
        if r.subject == TOTAL_SUBJECT:
            item['total'] = r.score
            item['total_sid'] = r.id
            item['rank'] = r.rank_dir
            item['rank_class'] = r.rank_class
            item['move'] = r.move_rank
        else:
            item['subjects'][r.subject] = {
                'id': r.id, 'score': r.score, 'rank': r.rank_dir,
                'rank_class': r.rank_class,
            }
    students = sorted(data.values(), key=lambda x: (x['class_name'], x['rank'] or 99999))
    info = None
    if exam.import_info:
        try:
            info = json.loads(exam.import_info)
        except (json.JSONDecodeError, TypeError):
            info = None
    return render_template('grades/exam_detail.html', exam=exam, students=students,
                           subjects=SUBJECTS, status_label=EXAM_STATUS_LABEL,
                           import_info=info)


@bp.route('/exams/<int:exam_id>/score/<int:score_id>', methods=['POST'])
@login_required
@perm_required('grades.edit')
def score_update(exam_id, score_id):
    row = ExamScore.query.filter_by(id=score_id, exam_id=exam_id).first_or_404()
    try:
        val = (request.form.get('score') or '').strip()
        if val == '':
            flash('分数留空表示删除该科成绩（缺考），如需删除请使用“删除”操作', 'warning')
            return redirect(url_for('grades.exam_detail', exam_id=exam_id))
        score = float(val)
        fm = Exam.query.get(exam_id).full_marks()
        limit = fm.get(row.subject, 100)
        if score < 0 or score > limit + 0.5:
            flash(f'分数 {score} 超出范围(0~{limit})', 'danger')
            return redirect(url_for('grades.exam_detail', exam_id=exam_id))
    except ValueError:
        flash('分数格式不正确', 'danger')
        return redirect(url_for('grades.exam_detail', exam_id=exam_id))
    row.score = score
    _mark_dirty(exam_id)
    db.session.commit()
    flash('成绩已修改，请点击“重新计算排名”', 'warning')
    return redirect(url_for('grades.exam_detail', exam_id=exam_id))


@bp.route('/exams/<int:exam_id>/score/<int:score_id>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def score_delete(exam_id, score_id):
    row = ExamScore.query.filter_by(id=score_id, exam_id=exam_id).first_or_404()
    db.session.delete(row)
    _mark_dirty(exam_id)
    db.session.commit()
    flash('该科成绩已删除（视为缺考），请点击“重新计算排名”', 'warning')
    return redirect(url_for('grades.exam_detail', exam_id=exam_id))


@bp.route('/exams/<int:exam_id>/recalc', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_recalc(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    n = ranking.recalc_exam(exam_id)
    exam.status = 'imported'
    db.session.commit()
    delete_cache_prefix(f'grades_analysis_{exam_id}')
    log_operation(current_user, '重算', '考试', exam_id, f'{exam.name} 排名重算({n}行)', module='grades')
    flash(f'排名已重新计算（{n} 条成绩）', 'success')
    return redirect(url_for('grades.exam_detail', exam_id=exam_id))


@bp.route('/exams/<int:exam_id>/delete', methods=['POST'])
@login_required
@perm_required('grades.edit')
def exam_delete(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    confirm_name = (request.form.get('confirm_name') or '').strip()
    if confirm_name != exam.name:
        flash('确认输入的名称与考试名称不一致', 'danger')
        return redirect(url_for('grades.exam_detail', exam_id=exam_id))
    ExamScore.query.filter_by(exam_id=exam_id).delete()
    exam_name = exam.name
    db.session.delete(exam)
    db.session.commit()
    delete_cache_prefix('grades_analysis_')
    log_operation(current_user, '删除', '考试', exam_id, f'{exam_name}', module='grades')
    flash(f'考试「{exam_name}」及全部成绩已删除', 'success')
    return redirect(url_for('grades.exams_list'))


def _mark_dirty(exam_id):
    exam = Exam.query.get(exam_id)
    if exam and exam.status == 'imported':
        exam.status = 'dirty'


# ==================== 成绩导入向导 ====================

@bp.route('/exams/<int:exam_id>/import', methods=['GET'])
@login_required
@perm_required('grades.import')
def exam_import(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    return render_template('grades/import_wizard.html', exam=exam,
                           step='upload', mode='A', status_label=EXAM_STATUS_LABEL)


@bp.route('/exams/<int:exam_id>/import/upload', methods=['POST'])
@login_required
@perm_required('grades.import')
def exam_import_upload(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    mode = request.form.get('mode', 'A')
    remove_missing = request.form.get('remove_missing') == '1'
    file = request.files.get('file')
    if not file or not file.filename:
        flash('请选择要上传的 Excel 文件', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    fname = file.filename
    try:
        stream = io.BytesIO(file.read())
        parsed = import_service.parse_score_excel(stream, exam)
    except ParseError as e:
        flash(str(e), 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    except Exception as e:
        current_app.logger.error(f'成绩解析异常: {e}', exc_info=True)
        flash('文件解析失败，请确认是标准模板格式（可下载模板参考）', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))

    diff = _preview_diff(exam, parsed, mode)
    token = str(uuid.uuid4())
    exam.import_draft = json.dumps({
        'fname': fname, 'mode': mode, 'remove_missing': remove_missing,
        'headers': parsed['headers'], 'rows': parsed['rows'], 'errors': parsed['errors'],
        'stats': parsed['stats'], 'template': parsed['template'],
        'diff': diff, 'created_at': datetime.now().isoformat(),
    }, ensure_ascii=False, default=str)
    exam.import_token = token
    db.session.commit()
    return render_template('grades/import_wizard.html', exam=exam, step='report',
                           draft=json.loads(exam.import_draft), token=token, mode=mode,
                           status_label=EXAM_STATUS_LABEL)


def _preview_diff(exam, parsed, mode):
    """与库内现有成绩对比的差异预览"""
    existing_nos = {r.student_no for r in
                    ExamScore.query.filter_by(exam_id=exam.id).all()}
    new_nos = set()
    update_cnt = 0
    del_cnt = 0
    for r in parsed['rows']:
        key = (r['no'])
        if key not in existing_nos:
            new_nos.add(key)
        for sub, score in r['subjects'].items():
            row = ExamScore.query.filter_by(exam_id=exam.id, student_no=r['no'],
                                            subject=sub).first()
            if score is not None:
                if row is not None:
                    update_cnt += 1
            else:
                if row is not None:
                    del_cnt += 1
    file_nos = {r['no'] for r in parsed['rows']}
    unseen = len(existing_nos - file_nos) if mode == 'A' else 0
    return {
        'new_students': len(new_nos),
        'updated_rows': update_cnt,
        'deleted_rows': del_cnt,
        'unseen': unseen,
    }


@bp.route('/exams/<int:exam_id>/import/confirm', methods=['POST'])
@login_required
@perm_required('grades.import')
def exam_import_confirm(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    token = request.form.get('token', '')
    if not exam.import_draft or not exam.import_token or exam.import_token != token:
        flash('导入批次已失效，请重新上传', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    try:
        draft = json.loads(exam.import_draft)
    except (json.JSONDecodeError, TypeError):
        flash('导入批次数据异常，请重新上传', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    parsed = {'headers': draft['headers'], 'rows': draft['rows']}
    mode = draft.get('mode', 'A')
    remove_missing = draft.get('remove_missing', False)
    try:
        summary = store_service.apply_import(exam, parsed, mode=mode,
                                             remove_missing=remove_missing)
        ranking.recalc_exam(exam_id)
        exam.status = 'imported'
        exam.import_info = json.dumps({
            'fname': draft.get('fname', ''), 'mode': mode,
            'summary': summary, 'errors': len(draft.get('errors', [])),
            'operator': current_user.real_name,
            'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }, ensure_ascii=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'成绩导入失败: {e}', exc_info=True)
        flash(f'导入失败，数据已回滚：{e}', 'danger')
        return redirect(url_for('grades.exam_import', exam_id=exam_id))
    delete_cache_prefix('grades_analysis_')
    log_operation(current_user, '导入', '考试', exam_id,
                  f'{exam.name} {mode}模式 差异{json.dumps(summary, ensure_ascii=False)}',
                  module='grades')
    flash(f'导入完成：新增 {summary["new_students"]} 人，更新 {summary["updated_rows"]} 条，'
          f'删除 {summary["deleted_rows"]} 条，未出现保留 {summary["unseen"]} 人', 'success')
    return redirect(url_for('grades.exam_detail', exam_id=exam_id))


@bp.route('/exams/<int:exam_id>/import/errors')
@login_required
@perm_required('grades.import')
def exam_import_errors(exam_id):
    """导出最近一次预检的错误明细 xlsx"""
    exam = Exam.query.get_or_404(exam_id)
    if not exam.import_draft:
        abort(404)
    try:
        draft = json.loads(exam.import_draft)
        errors = draft.get('errors', [])
    except Exception:
        abort(404)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '错误明细'
    ws.append(['行号', '学号', '原因'])
    for e in errors:
        ws.append([e.get('line'), e.get('no'), e.get('reason')])
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=f'导入错误_{exam.name}_{datetime.now():%Y%m%d_%H%M%S}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ==================== 模板下载 ====================

@bp.route('/template.xlsx')
@login_required
@perm_required('grades.import')
def template_download():
    wb = openpyxl.Workbook()
    # Sheet1 模板A
    ws = wb.active
    ws.title = '模板A(全年级全科)'
    ws.append(['学号', '姓名', '年级', '班级', '总分',
               '语文', '数学', '外语', '物理', '历史', '化学', '生物', '政治', '地理'])
    ws.append(['20250001', '张三', '2025级', '01班', 560, 110, 108, 95, 85, None, 76, None, 82, 80])
    for col, width in zip('ABCDEFGHIJKLMN', [10, 10, 10, 8, 8, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7]):
        ws.column_dimensions[col].width = width
    # Sheet2 模板B
    ws2 = wb.create_sheet('模板B(分批次分科)')
    ws2.append(['学号', '姓名', '年级', '班级', '语文', '数学', '外语'])
    ws2.append(['20250001', '张三', '2025级', '01班', 110, 108, 95])
    for col, width in zip('ABCDEFG', [10, 10, 10, 8, 7, 7, 7]):
        ws2.column_dimensions[col].width = width
    # Sheet3 填表说明
    ws3 = wb.create_sheet('填表说明')
    tips = [
        ['成绩导入模板说明'],
        [''],
        ['1. 表头必须保留（第一行），列顺序不限；缺考科目留空单元格；0 分填 0。'],
        ['2. 学号必填，以学号匹配学生；姓名/班级辅助展示，班级仅用于分批范围说明。'],
        ['3. “年级”列可选：填写时须与考试年级一致（如 2025级），不一致的行会被拦截。'],
        ['4. “总分”列可选：外部总分优先采用（兼容含听力的总分）；不填则由系统按应考 6 科累加。'],
        ['5. 科目列可只保留本次要导入的科目（分批导入），未出现的科目列不会被改动。'],
        ['6. 历史方向班级不考“物理/化学/生物”，对应单元格留空即可；也可以直接粘贴参考成绩表整表，多余列自动忽略。'],
        ['7. 学号匹配不到、分数超范围的行会列入错误报告，可下载错误明细修改后重传。'],
    ]
    for row in tips:
        ws3.append(row)
    ws3.column_dimensions['A'].width = 110
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True,
                     download_name=f'成绩导入模板_{date.today():%Y%m%d}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

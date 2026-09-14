# StuLink v1.10.0 2026-09-13
# 个人成绩查询与分析：页面 + 数据API + 学生搜索 + 成绩证明生成/公开核验
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
from datetime import datetime, timedelta

from flask import render_template, request, jsonify, abort, url_for, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.models.grades import Exam, Certificate
from app.models import Student
from app.modules.grades import bp
from app.modules.grades.services import student_service, scope as scope_service
from app.utils.decorators import perm_required


# ============ 范围过滤（复用现有角色/权限组体系，不新增角色） ============

def _student_query_in_scope(user):
    """返回 Student 查询（已按当前用户成绩可见范围过滤）"""
    q = Student.query
    scope, grade = scope_service.get_scope(user)
    if user.role == 'admin' or scope == 'school':
        pass
    elif user.has_role('teacher'):
        grades = {l.grade for l in scope_service.teacher_links(user)}
        q = q.filter(Student.grade.in_(grades)) if grades else q.filter(False)
    elif scope == 'grade':
        q = q.filter(Student.grade == grade)
    elif scope == 'class':
        links = scope_service.visible_classes(user) or []
        if links:
            conds = [((Student.grade == g) & (Student.class_name == c)) for g, c in links]
            q = q.filter(or_(*conds))
        else:
            q = q.filter(False)
    else:
        q = q.filter(False)
    return q


# ============ 页面 ============

@bp.route('/student-query')
@login_required
@perm_required('grades.student_query')
def student_query_page():
    return render_template('grades/student_query.html')


@bp.route('/student-query/cert-print/<code>')
@login_required
@perm_required('grades.student_query')
def cert_print(code):
    """成绩证明打印页（教职工生成后打印/另存 PDF；含打印按钮与 @media print）"""
    cert = Certificate.query.filter_by(code=code).first()
    if not cert:
        abort(404)
    content = json.loads(cert.content_json or '{}')
    return render_template('grades/cert_print.html', cert=cert, content=content)


@bp.route('/cert/<code>')
def cert_verify(code):
    """成绩证明公开核验页（无需登录）：验真伪 + 可作废提示。
    v1.12.2 加密防伪码：先做 HMAC 验签（密钥+学号+随机码+内容哈希），签名不符即判为伪造/篡改。"""
    from app.utils.cert_sign import verify, is_legacy

    # 免登录端点，做简单的按 IP 限流，避免被批量探测刷库（60 秒内最多 30 次）
    from app.utils.cache import cache
    rate_key = f'cert_verify_rate_{request.remote_addr or "unknown"}'
    hits = (cache.get(rate_key) or 0) + 1
    try:
        cache.set(rate_key, hits, timeout=60)
    except Exception:
        pass
    if hits > 30:
        abort(429, description='核验请求过于频繁，请稍后再试')

    cert = Certificate.query.filter_by(code=code).first()
    content = json.loads(cert.content_json or '{}') if cert else None
    sig_ok = verify(cert) if cert else False
    return render_template('grades/cert_verify.html', cert=cert, content=content,
                           sig_ok=sig_ok, legacy=cert is not None and is_legacy(cert))


# ============ v1.12.2 成绩证明生成记录（台账） ============

@bp.route('/student-query/cert-records')
@login_required
@perm_required('grades.student_query')
def cert_records_page():
    """成绩证明记录列表：关键字段（防伪码/学生/范围/生成人/时间/状态）+ 筛选。
    可见范围：管理员看全部，其他用户仅看自己生成的。"""
    from app.models.user import User
    q = Certificate.query
    if current_user.role != 'admin':
        q = q.filter(Certificate.generated_by == current_user.id)
    kw = (request.args.get('kw') or '').strip()          # 姓名/学号/防伪码 关键字
    if kw:
        like = f'%{kw}%'
        q = q.filter(or_(Certificate.student_name.like(like),
                         Certificate.student_no.like(like),
                         Certificate.code.like(like)))
    status = (request.args.get('status') or '').strip()  # valid/invalid
    if status == 'invalid':
        q = q.filter(Certificate.invalid.is_(True))
    elif status == 'valid':
        q = q.filter(Certificate.invalid.isnot(True))
    d_from = (request.args.get('date_from') or '').strip()
    d_to = (request.args.get('date_to') or '').strip()
    if d_from:
        q = q.filter(Certificate.generated_at >= datetime.strptime(d_from, '%Y-%m-%d'))
    if d_to:
        q = q.filter(Certificate.generated_at <
                     datetime.strptime(d_to, '%Y-%m-%d') + timedelta(days=1))
    certs = q.order_by(Certificate.generated_at.desc()).limit(500).all()
    # 生成人姓名：跨库不 JOIN，单独取 map（Certificate 在 grades 库，User 在主库）
    uids = {c.generated_by for c in certs if c.generated_by}
    user_map = {u.id: u.real_name or u.username for u in
                User.query.filter(User.id.in_(uids)).all()} if uids else {}
    return render_template('grades/cert_records.html', certs=certs, user_map=user_map,
                           kw=kw, status=status, d_from=d_from, d_to=d_to)


# ============ API ============

@bp.route('/api/student-query/search')
@login_required
@perm_required('grades.student_query')
def api_search():
    q = (request.args.get('q') or '').strip()
    base = _student_query_in_scope(current_user)
    if q:
        like = f'%{q}%'
        base = base.filter(or_(Student.student_number.like(like), Student.name.like(like)))
    rows = base.order_by(Student.grade.desc(), Student.class_name).limit(20).all()
    return jsonify(success=True, data=[
        {'no': s.student_number, 'name': s.name, 'grade': s.grade, 'class_name': s.class_name}
        for s in rows
    ])


@bp.route('/api/student-query/options')
@login_required
@perm_required('grades.student_query')
def api_sq_options():
    base = _student_query_in_scope(current_user)
    grades = sorted({s.grade for s in base.with_entities(Student.grade).distinct().all()})
    exams = Exam.query.filter(Exam.grade.in_(grades)).all() if grades else []
    terms = sorted({e.term for e in exams if e.term})
    types = sorted({e.exam_type for e in exams if e.exam_type})
    from app.models.grades import SUBJECTS
    return jsonify(success=True, data={
        'grades': grades, 'terms': terms, 'exam_types': types, 'subjects': SUBJECTS,
    })


@bp.route('/api/student-query/data')
@login_required
@perm_required('grades.student_query')
def api_data():
    student_no = (request.args.get('student_no') or '').strip()
    if not student_no:
        return jsonify(success=False, message='请先选择学生')
    filters = {k: (request.args.get(k) or '') for k in
               ('term', 'exam_type', 'date_from', 'date_to', 'subject', 'exam_id')}
    # v1.12.2 自选证明考试（与生成口径一致，页面矩阵同步反映选择）
    ceids = (request.args.get('cert_exam_ids') or '').strip()
    if ceids:
        try:
            filters['cert_exam_ids'] = [int(x) for x in ceids.split(',') if x.strip()]
        except ValueError:
            pass
    try:
        result = student_service.build_data(current_user, student_no, filters)
    except PermissionError:
        abort(403)
    if 'error' in result:
        return jsonify(success=False, message=result['error'])
    return jsonify(success=True, data=result)


@bp.route('/api/student-query/cert', methods=['POST'])
@login_required
@perm_required('grades.student_query')
def api_create_cert():
    payload = request.get_json(force=True, silent=True) or {}
    student_no = str(payload.get('student_no') or '').strip()
    if not student_no:
        return jsonify(success=False, message='缺少学生')
    filters = {k: (payload.get(k) or '') for k in
               ('term', 'exam_type', 'date_from', 'date_to', 'subject', 'exam_id')}
    # v1.12.2 自选纳入证明的考试（矩阵列）；不传则按默认「每学期代表考试」口径
    ceids = payload.get('cert_exam_ids')
    if isinstance(ceids, list) and ceids:
        filters['cert_exam_ids'] = ceids
    try:
        result = student_service.build_data(current_user, student_no, filters)
    except PermissionError:
        abort(403)
    if 'error' in result or not result.get('exams'):
        return jsonify(success=False, message='无可生成证明的成绩数据')
    if not (result.get('cert_matrix') or {}).get('cols'):
        return jsonify(success=False, message='所选考试不足以生成证明，请至少勾选一场')
    basic = result['student']
    # 证明抬头（姓名/性别/身份证号/学籍号/入学时间）与校名随快照固化，保证事后可验真
    result['cert_header'] = student_service.cert_header(student_no)
    result['school_name'] = current_app.config.get('SCHOOL_NAME', '')
    content_json = json.dumps(result, ensure_ascii=False)
    # v1.12.2 加密防伪码：SL+日期+随机码+HMAC(密钥, 学号|随机码|内容哈希)；随机码/签名随记录落库
    from app.utils.cert_sign import make_code
    code, nonce, sig = make_code(basic['no'], content_json)
    # 范围摘要优先用矩阵实际纳入的考试名（自选口径），否则沿用单场说明
    names = (result.get('cert_matrix') or {}).get('exam_names') or []
    scope_desc = ('、'.join(names[:3]) + ('…' if len(names) > 3 else '')) if names \
        else result['meta'].get('selected_exam', '')
    cert = Certificate(
        code=code, student_no=basic['no'], student_name=basic['name'],
        grade=basic['grade'], class_name=basic['class_name'],
        term=(payload.get('term') or '全部学期'),
        scope_desc=scope_desc,
        # 关联考试 = 证明矩阵实际纳入的考试（自选口径）
        exam_ids=json.dumps((result.get('cert_matrix') or {}).get('exam_ids')
                            or [e['id'] for e in result['exams']]),
        generated_by=current_user.id, generated_at=datetime.now(),
        nonce=nonce, sig=sig, content_json=content_json,
    )
    from app.extensions import db
    db.session.add(cert)
    db.session.commit()
    return jsonify(success=True, data={
        'code': code,
        'print_url': url_for('grades.cert_print', code=code),
        'verify_url': url_for('grades.cert_verify', code=code),
    })


@bp.route('/api/student-query/cert/<code>/invalid', methods=['POST'])
@login_required
@perm_required('grades.student_query')
def api_invalid_cert(code):
    """作废成绩证明（仅生成人或管理员）"""
    cert = Certificate.query.filter_by(code=code).first()
    if not cert:
        abort(404)
    if current_user.role != 'admin' and cert.generated_by != current_user.id:
        abort(403)
    cert.invalid = True
    from app.extensions import db
    db.session.commit()
    return jsonify(success=True, message='成绩证明已作废')

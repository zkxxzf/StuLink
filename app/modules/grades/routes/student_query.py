# StuLink v1.19.47.19 2026-10-10
# 个人成绩查询与分析：页面 + 数据API + 学生搜索 + 成绩证明生成/公开核验
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import hashlib
import json
from datetime import datetime, timedelta

from flask import render_template, request, jsonify, abort, url_for
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.models.grades import Exam, Certificate
from app.models import Student
from app.modules.grades import bp
from app.modules.grades.services import student_service, scope as scope_service
from app.utils.decorators import perm_required

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
import logging

logger = logging.getLogger(__name__)


# ============ 范围过滤（复用现有角色/权限组体系，不新增角色） ============

def _student_query_in_scope(user):
    """返回 Student 查询（已按当前用户成绩可见范围过滤）"""
    q = Student.query
    # 2026-10-12 P1：用户级数据范围优先（与 student_scope/dormitory_scope 同口径）。
    # 原实现 `admin or scope=='school'` 先短路，staff+校级组再配用户级范围时
    # 会忽略用户级范围放行全校（口径漂移）。
    ug = scope_service.user_grade_scope(user)
    if ug is not None:
        return q.filter(Student.grade.in_(ug))
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
    """成绩证明打印页（教职工生成后打印/另存 PDF；含打印按钮与 @media print）

    2026-10-12 安全审计 H-4：原先只校验 perm_required('grades.student_query')，
    不校验生成人、也不校验学生是否在本用户数据范围内 → 同校任意教务员可打印
    任意学生的完整成绩证明（含完整身份证号与全部成绩明细，对外法律文书）。
    现与同文件 api_invalid_cert 对齐：仅生成人或管理员可打印。
    """
    cert = Certificate.query.filter_by(code=code).first()
    if not cert:
        abort(404)
    if current_user.role != 'admin' and cert.generated_by != current_user.id:
        abort(403, description='仅证明生成人或管理员可打印')
    from app.utils.cert_sign import load_content
    content = json.loads(load_content(cert.content_json) or '{}')
    return render_template('grades/cert_print.html', cert=cert, content=content)


@bp.route('/cert/<code>')
def cert_verify(code):
    """成绩证明公开核验页（无需登录）：验真伪 + 可作废提示。
    v1.12.2 加密防伪码：先做 HMAC 验签（密钥+学号+随机码+内容哈希），签名不符即判为伪造/篡改。"""
    from app.utils.cert_sign import verify, is_legacy, load_content

    # 免登录端点，做简单的按 IP 限流，避免被批量探测刷库（60 秒内最多 30 次）
    from app.utils.cache import cache
    rate_key = f'cert_verify_rate_{request.remote_addr or "unknown"}'
    hits = (cache.get(rate_key) or 0) + 1
    try:
        cache.set(rate_key, hits, timeout=60)
    except Exception as _e:  # noqa: BLE001
        logger.warning('student_query.py::cert_verify 异常（已降级处理）: %s', _e)
    if hits > 30:
        abort(429, description='核验请求过于频繁，请稍后再试')

    cert = Certificate.query.filter_by(code=code).first()
    sig_ok = verify(cert) if cert else False
    # M-14：公开端点不再解密/传递证明快照（此前未登录即可看到成绩明细），
    # 只提供真伪 + 作废状态 + 内容摘要；完整打印走登录后的 cert_print。
    content_hash = None
    if cert and sig_ok:
        try:
            content_hash = hashlib.sha256(
                (load_content(cert.content_json) or '').encode('utf-8')).hexdigest()[:32]
        except Exception:  # noqa: BLE001
            content_hash = None
    return render_template('grades/cert_verify.html', cert=cert, content=None,
                           content_hash=content_hash, sig_ok=sig_ok,
                           legacy=cert is not None and is_legacy(cert))


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
        try:
            q = q.filter(Certificate.generated_at >= datetime.strptime(d_from, '%Y-%m-%d'))
        except ValueError:
            pass  # 2026-10-11 审计：非法日期参数忽略该过滤条件，不再 500
    if d_to:
        try:
            q = q.filter(Certificate.generated_at <
                         datetime.strptime(d_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass  # 2026-10-11 审计：同上
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
        # 证明说明默认文案（生成弹窗的文本框预填它，经办人可改写）
        'cert_note_default': student_service.DEFAULT_CERT_NOTE,
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
        except ValueError as _e:
            # 参数含非数字 → 不带该筛选（行为不变）
            logger.debug('cert_exam_ids 含非数字(%r)，已忽略该筛选: %s', ceids, _e)
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
    # 学校名称：环境变量 > 数据库系统设置 > 化名占位
    from app.utils.helpers import get_school_name
    result['school_name'] = get_school_name()
    # v1.12.3 底部说明可由经办人改写；留空用默认文案，超长截断并去除首尾空白
    note = str(payload.get('cert_note') or '').strip()
    if len(note) > student_service.CERT_NOTE_MAX:
        note = note[:student_service.CERT_NOTE_MAX]
    result['cert_note'] = note or student_service.DEFAULT_CERT_NOTE
    content_json = json.dumps(result, ensure_ascii=False)
    # v1.12.2 加密防伪码：SL+日期+随机码+HMAC(密钥, 学号|随机码|内容哈希)；随机码/签名随记录落库
    # v1.12.5 快照整体 AES 加密落库（含身份证号等敏感字段），签名仍绑定明文快照，读取时解密
    from app.utils.cert_sign import make_code, store_content
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
        nonce=nonce, sig=sig, content_json=store_content(content_json),
    )
    from app.extensions import db
    db.session.add(cert)
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：成绩证明是对外法律效力文书，
    # 此前生成只有 generated_by/generated_at 两列、**无操作审计** ——
    # 事后无法回答"谁在哪台机器给谁开了证明"。这里补业务日志（含范围摘要）。
    from app.utils.helpers import log_operation
    log_operation(current_user, '生成', '成绩证明', cert.id,
                  f'{basic["name"]}（{basic["grade"]}{basic["class_name"]}）'
                  f'范围：{scope_desc}｜防伪码 {code}', module='grades')
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
    # 2026-10-12 数据可信度审计（§3.8）：作废同样补业务日志
    from app.utils.helpers import log_operation
    log_operation(current_user, '作废', '成绩证明', cert.id,
                  f'{cert.student_name}（{cert.grade}{cert.class_name}）防伪码 {code}',
                  module='grades')
    return jsonify(success=True, message='成绩证明已作废')

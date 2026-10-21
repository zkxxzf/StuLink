# StuLink v1.19.58.64 2026-10-10
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
from app.modules.grades.services import student_service
from app.utils.decorators import perm_required

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
import logging

logger = logging.getLogger(__name__)


# ============ 范围过滤（复用现有角色/权限组体系，不新增角色） ============

def _student_query_in_scope(user):
    """返回 Student 查询（已按当前用户成绩可见范围过滤）。

    2026-10-19 第十三轮审计：原实现只按「用户级范围的年级清单」过滤，
    把「仅授权某班」静默放大成整个年级（api/search 返回姓名+学号+班级，
    等于可枚举整年级花名册）。统一改走 apply_student_scope（与学生列表、
    宿舍、导出同口径，吸收 UserDataScope 的班级白名单）。
    """
    from app.utils.student_scope import apply_student_scope
    return apply_student_scope(Student.query, user)


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
    # L-10：改原子自增，get→set 在并发下可同时读到 29 而超限。
    from app.utils.cache import cache
    from app.utils.client_ip import get_client_ip
    rate_key = f'cert_verify_rate_{get_client_ip("unknown")}'
    hits = cache.incr(rate_key, 1, timeout=60)
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


# 2026-10-13 用户口径：删除「成绩核验」页签（原 /grades/student-query/cert-verify）。
# 理由：成绩证明台账（cert_records.html）每行已有「核验」按钮，直达公开核验页
# /grades/cert/<code>，同一套 HMAC 校验，功能不缺失 → 不需要再单独一页。
# 保留：公开核验页 grades.cert_verify（免登录、脱敏、IP 限流）与证书打印 cert_print。


# ============ API ============

def _class_sort_key(class_name):
    """班级自然序：让 '10班' 排在 '05班' 之后（纯字符串序会反过来）。"""
    digits = ''.join(ch for ch in (class_name or '') if ch.isdigit())
    return (int(digits) if digits else 9999, class_name or '')


@bp.route('/api/student-query/search')
@login_required
@perm_required('grades.student_query')
def api_search():
    """学生候选搜索（2026-10-14 用户口径：候选要够，不能只剩一个班）。

    原先固定 `limit(20)` 且无班级筛选，无关键词时只给出按年级/班级排序的前 20 人
    —— 实际就是**某一个班**，老师翻不到别的班的学生。现在：
      · 支持 `grade` / `class_name` 先缩小范围（下拉联动，级联条件与权限范围叠加）；
      · 上限默认 100、最大 300，并回传 `total` / `truncated` 让前端提示需继续缩小；
      · 排序补上学号，保证同班内顺序稳定（分页/展示不跳）。
    """
    q = (request.args.get('q') or '').strip()
    grade = (request.args.get('grade') or '').strip()
    class_name = (request.args.get('class_name') or '').strip()
    try:
        limit = int(request.args.get('limit') or 100)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 300))

    base = _student_query_in_scope(current_user)
    if grade:
        base = base.filter(Student.grade == grade)
    if class_name:
        base = base.filter(Student.class_name == class_name)
    if q:
        like = f'%{q}%'
        base = base.filter(or_(Student.student_number.like(like), Student.name.like(like)))
    total = base.count()
    rows = (base.order_by(Student.grade.desc(), Student.class_name, Student.student_number)
            .limit(limit).all())
    return jsonify(success=True, total=total, limit=limit, truncated=total > len(rows),
                   data=[
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
    # 2026-10-13：学年选项用 exam_term 口径（缺 term 的历史考试按日期推导），
    # 否则下拉里只剩有 term 的那一场，选了就查不到往年成绩。
    terms = sorted({t for t in (student_service.exam_term(e) for e in exams) if t})
    types = sorted({e.exam_type for e in exams if e.exam_type})
    # 2026-10-14：班级候选（按年级分组），供「年级 → 班级」联动下拉先缩小范围；
    # 班级按数字自然序排（01班 / 02班 / … / 10班，避免字符串排序把 10班排到 05班前）
    classes = {}
    for g, cn in base.with_entities(Student.grade, Student.class_name).distinct().all():
        if g and cn:
            classes.setdefault(g, set()).add(cn)
    classes = {g: sorted(v, key=_class_sort_key) for g, v in classes.items()}
    from app.models.grades import SUBJECTS
    return jsonify(success=True, data={
        'grades': grades, 'classes': classes,
        'terms': terms, 'exam_types': types, 'subjects': SUBJECTS,
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

# -*- coding: utf-8 -*-
"""成绩写工具（2026-10-11 新增；2026-10-12 对齐 v1.19.41.2 新口径）。

权限与范围口径严格对齐网页端 grades 模块写入路由：
  · 改分 / 删分  → **grades.edit + grades.score_edit 双权限点**（v1.19.41.2 起）
                   + exam_write_scope（考试年级范围）
                   + **当前账号密码二次确认**（防误改/防提示词注入）
  · 批量录入     → grades.import + exam_write_scope + 学号范围逐条校验
  · 重算排名     → grades.edit + exam_write_scope

v1.19.41.2 网页端改分的三条硬约束，MCP 侧必须同守（否则 AI 可绕过）：
  1. **总分行禁止直接改/删**——总分由各科自动汇总，只能改单科；
  2. 赋分(score) 与 原始分(raw_score) 可分别改；两者都留空 = 标记缺考（删该科）；
  3. 每次改分写一条 ScoreChangeLog（谁、改了哪科、改前改后），并**立即重算整场排名**。

与网页端一致的后置动作（缺一会导致分析页读到旧缓存或排名失真）：
  · 刷新该生总分行（refresh_student_total）；
  · 考试状态复位 'imported'（网页端 v1.19.41.2 起改分后自动重算，不再留"待重算"）；
  · 失效分析缓存（invalidate_exam_cache + tab_service.clear_exam_cache）。

刻意未开放：删除整场考试（网页端需账号+密码二次确认，且影响面过大）。
"""
from ..core import guards, respond, toolkit

_SCORE_FIELDS = ('id', 'exam_id', 'student_no', 'student_name', 'grade',
                 'class_name', 'subject', 'score', 'raw_score', 'rank_class',
                 'rank_dir')


def _exam_or_raise(exam_id, user):
    """取考试并校验写入范围（年级）。"""
    exam = guards.exam_or_raise(exam_id)
    guards.exam_write_scope(user, exam)
    return exam


def _score_row_or_raise(exam_id, score_id):
    from app.models.grades import ExamScore
    row = ExamScore.query.filter_by(id=int(score_id), exam_id=int(exam_id)).first()
    if row is None:
        raise ValueError(
            f'成绩记录不存在：score_id={score_id}（可用 get_student_scores 核对）')
    return row


def _check_score(exam, subject, value):
    """分数合法性与范围校验（与网页端 exams.py::score_update 同口径）。"""
    try:
        score = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'分数必须是数字，收到：{value!r}')
    limit = exam.full_marks().get(subject, 100)
    if score < 0 or score > limit + 0.5:
        raise ValueError(f'分数 {score} 超出范围（0~{limit}，科目 {subject}）')
    return score


def _log_score_change(exam, row, action, old_score, new_score, old_raw, new_raw,
                      user):
    """写一条分数修改记录（与网页端 _log_score_change 同字段）。"""
    from app.models.grades import ScoreChangeLog
    from app.extensions import db
    db.session.add(ScoreChangeLog(
        exam_id=exam.id, exam_name=exam.name, score_id=row.id,
        student_no=row.student_no, student_name=row.student_name,
        class_name=row.class_name, subject=row.subject, action=action,
        old_score=old_score, new_score=new_score,
        old_raw=old_raw, new_raw=new_raw,
        operator_id=user.id,
        operator_name=getattr(user, 'real_name', None) or getattr(
            user, 'username', ''),
        note='MCP'))


def _recalc_after_edit(exam_id, exam):
    """改分后立即重算整场排名并把状态复位（网页端 _recalc_after_edit 同名同义）。"""
    from app.modules.grades.services import ranking

    n = ranking.recalc_exam(exam_id)
    exam.status = 'imported'
    return n


def _verify_password(user, password):
    """改分二次确认：核对当前账号密码（与网页端 _verify_edit_password 同口径）。"""
    if not (password or '').strip():
        raise ValueError('改分需二次确认：请提供 confirm_password（当前登录账号的密码）')
    if not user.check_password(password):
        raise PermissionError('密码不正确，本次修改未生效')


def _update_score(user, exam_id, score_id, score, raw_score, password):
    """修改单条成绩（v1.19.41.2 口径）。

    score/raw_score 都留空 = 标记缺考（删该科）；只传其一则只改对应字段。
    必须提供 confirm_password 做二次确认。
    """
    from app.extensions import db
    from app.models.grades import TOTAL_SUBJECT
    from app.modules.grades.services import store_service
    from app.modules.grades.utils import invalidate_exam_cache
    from app.modules.grades.services import tab_service
    from app.utils.helpers import log_operation

    _verify_password(user, password)
    exam = _exam_or_raise(exam_id, user)
    row = _score_row_or_raise(exam_id, score_id)
    # 总分由各科自动汇总，禁止直接改（与网页端一致）
    if row.subject == TOTAL_SUBJECT:
        raise ValueError('总分由各科成绩自动汇总，不能直接修改，请修改对应的单科成绩')

    limit = exam.full_marks().get(row.subject, 100)
    old_score, old_raw = row.score, row.raw_score
    new_score, new_raw = old_score, old_raw
    if score is not None:
        new_score = _check_score(exam, row.subject, score) if str(score).strip() != '' else None
    if raw_score is not None:
        if str(raw_score).strip() == '':
            new_raw = None
        else:
            try:
                new_raw = float(raw_score)
            except (TypeError, ValueError):
                raise ValueError(f'raw_score 必须是数字，收到：{raw_score!r}')
            if new_raw < 0 or new_raw > limit + 0.5:
                raise ValueError(f'原始分 {new_raw} 超出范围(0~{limit})')

    # 赋分与原始分都清空 → 视为缺考，删除该科
    if new_score is None and new_raw is None:
        _log_score_change(exam, row, 'delete', old_score, None, old_raw, None, user)
        no, subject = row.student_no, row.subject
        db.session.delete(row)
        store_service.refresh_student_total(exam, no)
        n = _recalc_after_edit(exam_id, exam)
        db.session.commit()
        invalidate_exam_cache(exam.id)
        tab_service.clear_exam_cache(exam.id)
        log_operation(user, '删除', '成绩', score_id,
                      f'{no} {subject} 标记缺考（排名已重算，MCP）', module='grades')
        return {'deleted': True, 'student_no': no, 'subject': subject,
                'ranked_rows': n,
                'message': f'该科已标记缺考，排名已自动重算（{n} 条），'
                           f'并已记入分数修改记录'}

    row.score = new_score
    row.raw_score = new_raw
    store_service.refresh_student_total(exam, row.student_no)
    n = _recalc_after_edit(exam_id, exam)
    _log_score_change(exam, row, 'update', old_score, row.score,
                      old_raw, row.raw_score, user)
    db.session.commit()
    invalidate_exam_cache(exam.id)
    tab_service.clear_exam_cache(exam.id)
    log_operation(user, '修改', '成绩', row.id,
                  f'{row.student_name or row.student_no} {row.subject} 改分'
                  f'（排名已重算，MCP）', module='grades')
    return {'updated': True, 'score': respond.pick(row, _SCORE_FIELDS),
            'ranked_rows': n,
            'message': f'成绩已修改，排名已自动重算（{n} 条），并已记入分数修改记录'}


def _import_scores(user, exam_id, records, mode):
    """批量录入/更新成绩（整场事务，全成全败）。

    mode='A'（默认）增量覆盖：只动给出的科目，未列出的学生与科目保留；
    mode='B' 整场重置：先清空本场全部成绩再写入（慎用）。
    """
    from app.extensions import db
    from app.models.grades import ExamScore, SUBJECTS, TOTAL_SUBJECT
    from app.modules.grades.services import ranking, store_service
    from app.modules.grades.services import scope as grade_scope
    from app.modules.grades.utils import invalidate_exam_cache
    from app.utils.helpers import log_operation

    mode = (mode or 'A').strip().upper()
    if mode not in ('A', 'B'):
        raise ValueError("mode 只能是 'A'（增量覆盖）或 'B'（整场重置）")
    if not isinstance(records, list) or not records:
        raise ValueError('records 必须是非空数组')
    if len(records) > 2000:
        raise ValueError('单次最多录入 2000 条，请分批提交')

    exam = _exam_or_raise(exam_id, user)
    full_marks = exam.full_marks()
    valid_subjects = set(SUBJECTS) | {TOTAL_SUBJECT}

    # 1) 逐条校验（越权 / 学号不存在 / 科目非法 / 分数越界）——任一条不合格整批拒绝
    numbers = [str(r.get('student_no') or '').strip()
               for r in records if isinstance(r, dict)]
    students = {s.student_number: s for s in _load_students(numbers)}
    prepared = []
    for idx, raw in enumerate(records, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f'第 {idx} 条不是对象')
        number = str(raw.get('student_no') or '').strip()
        subject = str(raw.get('subject') or '').strip()
        if not number or not subject:
            raise ValueError(f'第 {idx} 条缺少 student_no 或 subject')
        # v1.19.41.2：总分由各科自动汇总，不接受直接录入（会自动重算生成）
        if subject == TOTAL_SUBJECT:
            raise ValueError(f'第 {idx} 条 subject 为「{TOTAL_SUBJECT}」，'
                             f'总分由各科自动汇总，请只录入单科成绩')
        if subject not in valid_subjects:
            raise ValueError(f'第 {idx} 条 subject="{subject}" 非法，可选：'
                             f'{"、".join(SUBJECTS)}')
        student = students.get(number)
        if student is None:
            raise ValueError(f'第 {idx} 条学号不存在：{number}')
        # 2026-10-12 审计修复：学生年级须与考试年级一致（网页端导入解析器
        # import_service.py:139 同规则），否则会把别年级学生成绩写进本场考试
        if student.grade != exam.grade:
            raise ValueError(
                f'第 {idx} 条学生 {number} 是 {student.grade}，'
                f'与本场考试年级 {exam.grade} 不一致，整批已拒绝')
        # 学号写入范围（年级/班级），与网页端录入同口径
        try:
            grade_scope.student_in_scope(user, number)
        except PermissionError:
            raise PermissionError(
                f'第 {idx} 条学号 {number}（{student.grade}{student.class_name}）'
                f'不在你的数据范围内，整批已拒绝')
        raw_value = raw.get('score')
        if raw_value is None or str(raw_value).strip() == '':
            raise ValueError(f'第 {idx} 条缺少 score（删除请用 delete_score）')
        score = _check_score(exam, subject, raw_value)
        prepared.append({'student_no': number, 'subject': subject, 'score': score,
                         'student': student})

    # 2) 整批写入（单事务；异常回滚，避免部分写入造成脏数据）
    try:
        summary = {'created': 0, 'updated': 0}
        if mode == 'B':
            deleted = ExamScore.query.filter_by(exam_id=exam.id).delete()
            db.session.flush()
            summary['cleared'] = deleted
        existing = {(r.student_no, r.subject): r for r in
                    ExamScore.query.filter_by(exam_id=exam.id).all()}
        for item in prepared:
            key = (item['student_no'], item['subject'])
            row = existing.get(key)
            if row is None:
                row = ExamScore(exam_id=exam.id, student_no=item['student_no'],
                                subject=item['subject'])
                db.session.add(row)
                existing[key] = row
                summary['created'] += 1
            else:
                summary['updated'] += 1
            row.score = item['score']
            st = item['student']
            row.student_name = st.name
            row.grade = st.grade
            row.class_name = st.class_name
            row.subject_selection = st.subject_selection
        db.session.flush()
        # 3) 总分行重算（缺考按 0，与导入口径一致）
        for number in {i['student_no'] for i in prepared}:
            if not any(i['subject'] == TOTAL_SUBJECT for i in prepared
                       if i['student_no'] == number):
                store_service.refresh_student_total(exam, number)
        db.session.flush()
        ranked = ranking.recalc_exam(exam.id)
        exam.status = 'imported'
        db.session.commit()
    except Exception as exc:  # noqa: BLE001  整批回滚，避免部分写入
        db.session.rollback()
        raise ValueError(f'录入失败，已整批回滚：{type(exc).__name__}: {exc}')

    invalidate_exam_cache(exam.id)
    log_operation(user, '导入', '成绩', exam.id,
                  f'批量录入 {len(prepared)} 条（模式{mode}，MCP）', module='grades')
    return {'imported': len(prepared), 'created': summary['created'],
            'updated': summary['updated'], 'mode': mode,
            'cleared': summary.get('cleared', 0), 'ranked_rows': ranked,
            'exam_id': exam.id,
            'message': f'已录入 {len(prepared)} 条并重算排名（{ranked} 行）；'
                       f'模式B会先清空本场原有成绩'}


def _load_students(numbers):
    """按学号批量取学生（一次查询，避免逐条 N+1）。"""
    from app.models.student import Student
    clean = [n for n in numbers if n]
    if not clean:
        return []
    return Student.query.filter(Student.student_number.in_(clean)).all()


def _recalc(user, exam_id):
    """重算整场排名（改分/划线后必须调用，否则分析页排名失真）。"""
    from app.extensions import db
    from app.modules.grades.services import ranking, tab_service
    from app.utils.helpers import log_operation

    exam = _exam_or_raise(exam_id, user)
    try:
        rows = ranking.recalc_exam(exam.id)
        exam.status = 'imported'
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        raise ValueError(f'重算失败：{type(exc).__name__}: {exc}')
    tab_service.clear_exam_cache(exam.id)
    log_operation(user, '重算', '考试', exam.id,
                  f'{exam.name} 排名重算（{rows} 行，MCP）', module='grades')
    return {'exam_id': exam.id, 'exam_name': exam.name, 'ranked_rows': rows,
            'message': f'排名已重新计算（{rows} 条成绩），分析缓存已失效'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='update_score',
                 description='【写操作】修改单条成绩（v1.19.41.2 口径，需二次确认）。'
                             'score_id 为成绩记录 id；score=赋分、raw_score=原始分，'
                             '**两者都留空 = 标记缺考（删该科）**；总分行不可直接修改。'
                             'confirm_password 必须填当前登录账号的密码。'
                             '改后自动重算整场排名并记入分数修改记录')
    async def update_score(exam_id: int, score_id: int, confirm_password: str,
                           score: str = None, raw_score: str = None) -> str:
        params = {'exam_id': exam_id, 'score_id': score_id}
        return await toolkit.db_write(
            lambda u: _update_score(u, exam_id, score_id, score, raw_score,
                                    confirm_password),
            tool='update_score', params=params,
            perm=('grades.edit', 'grades.score_edit'))

    @server.tool(name='import_scores',
                 description='【写操作】批量录入成绩（整场事务，任一条不合格则整批回滚）。'
                             'records 为数组，元素含 student_no / subject / score；'
                             'subject 取 语数外等 9 科（**不接受"总分"**，总分自动汇总）；'
                             "mode='A' 增量覆盖（默认），'B' 先清空本场再写入（慎用）。"
                             '录入后自动重算排名')
    async def import_scores(exam_id: int, records: list, mode: str = 'A') -> str:
        params = {'exam_id': exam_id, 'mode': mode, 'count': len(records or [])}
        return await toolkit.db_write(
            lambda u: _import_scores(u, exam_id, records, mode),
            tool='import_scores', params=params, perm='grades.import')

    @server.tool(name='recalc_exam_ranks',
                 description='【写操作】重算某场考试的排名（班排/方向排/进退步）并失效分析缓存；'
                             '改分或批量录入后必须调用')
    async def recalc_exam_ranks(exam_id: int) -> str:
        return await toolkit.db_write(lambda u: _recalc(u, exam_id),
                                      tool='recalc_exam_ranks',
                                      params={'exam_id': exam_id},
                                      perm='grades.edit')

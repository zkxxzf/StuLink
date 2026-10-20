# -*- coding: utf-8 -*-
"""教师离职 / 退休存档与「错误录入」彻底删除（2026-10-20 用户口径）。

规则：

- **错误录入** → 允许彻底删除（教师记录 + 登录账号 + 账号级数据）；
- **离职 / 退休** → 只做存档：教师 `status='left'` + 归档字段，账号停用并吊销
  小程序令牌，任课映射置为失效；数据保留，仅「系统管理 → 教师管理」可查阅；
- 归档后其他模块（教务名单、课表、监考、学科组长、成绩任课映射、小程序通讯录）
  查询都按 `status='active'` 或 `archive_kind IS NULL` 过滤，不能再调用。

彻底删除前会扫描各库中指向该账号的业务引用（操作日志、任课映射、成绩/表单/
证书等）；只要有任何业务痕迹就拒绝删除 —— 这种情况只能走「离职/退休」存档，
避免误删历史数据。
"""
from sqlalchemy import inspect, text

from app.extensions import db
from app.models import User
from app.models.academic import Teacher
from app.models.grades import TeacherSubjectLink

ARCHIVE_KINDS = {'leave': '离职', 'retire': '退休'}
DELETE_KIND = 'mistake'          # 错误录入（彻底删除）
KIND_LABELS = {DELETE_KIND: '错误录入（彻底删除）', **ARCHIVE_KINDS}

# 账号级数据：删号时一并清理，不计入「业务痕迹」
_ACCOUNT_TABLES = {'user_permission_groups', 'wechat_bindings', 'wechat_tickets',
                   'user_class_links', 'ai_usage', 'migration_log'}
# 可能指向 users.id 的列（整数 id）
_USER_COLS = ('user_id', 'operator_id', 'submitter_id', 'generated_by',
              'created_by', 'approver_id', 'applicant_id', 'reviewer_id',
              'updated_by', 'graduated_by')


def _engines():
    """各 bind 的 Engine（按库文件去重：默认库与 'system' bind 指向同一个文件）。"""
    out, seen = [], set()
    for key, eng in (db.engines or {}).items():
        url = str(eng.url)
        if url in seen:
            continue
        seen.add(url)
        out.append((key or 'system', eng))
    return out


def business_references(user_id):
    """扫描所有库：返回 [(db, table, column, count), ...] 业务引用。"""
    hits = []
    for bind, eng in _engines():
        try:
            insp = inspect(eng)
            tables = insp.get_table_names()
        except Exception:  # noqa: BLE001 库不可用时跳过
            continue
        meta = {}
        for t in tables:
            if t in _ACCOUNT_TABLES:
                continue
            cols = {c['name'] for c in insp.get_columns(t)}
            picked = [c for c in _USER_COLS if c in cols
                      # teachers.user_id 就是被删除的这条教师记录本身，不算业务痕迹
                      and not (t == 'teachers' and c == 'user_id')]
            if picked:
                meta[t] = picked
        if not meta:
            continue
        with eng.connect() as conn:
            for t, cols in meta.items():
                for col in cols:
                    try:
                        n = conn.execute(
                            text(f'SELECT COUNT(*) FROM "{t}" WHERE "{col}" = :uid'),
                            {'uid': user_id}).scalar() or 0
                    except Exception:  # noqa: BLE001 表结构异常时忽略
                        continue
                    if n:
                        hits.append((bind, t, col, n))
    return hits


def archive_teacher(teacher, kind, reason, actor):
    """离职 / 退休存档。返回变更摘要（供日志）。"""
    if kind not in ARCHIVE_KINDS:
        raise ValueError('归档类型只能是「离职」或「退休」')
    label = ARCHIVE_KINDS[kind]
    now = _now()
    teacher.status = 'left'
    teacher.archive_kind = label
    teacher.archive_reason = reason
    teacher.archived_at = now
    teacher.archived_by = getattr(actor, 'username', '') or ''
    # 任课映射失效：其他模块（课表/成绩/课时）不再把该教师算作当前任课人
    n_link = (TeacherSubjectLink.query
              .filter(TeacherSubjectLink.user_id == teacher.user_id,
                      TeacherSubjectLink.active.is_(True))
              .update({'active': False}, synchronize_session=False)) \
        if teacher.user_id else 0
    user = db.session.get(User, teacher.user_id) if teacher.user_id else None
    if user is not None:
        user.is_active = False
        # 吊销小程序旧令牌（网页 session 由 is_active 检查兜住）
        user.wx_token_ver = int(user.wx_token_ver or 0) + 1
    db.session.commit()
    return f'{label}存档：{teacher.name}（{teacher.teacher_uid}），原因：{reason}；' \
           f'任课映射失效 {n_link} 条' + ('，账号已停用' if user else '')


def hard_delete_teacher(teacher, reason, actor):
    """错误录入 → 彻底删除。有业务痕迹时抛 ValueError。"""
    uid = teacher.user_id
    if uid:
        refs = business_references(uid)
        if refs:
            detail = '、'.join(f'{b}.{t}.{c}×{n}' for b, t, c, n in refs[:6])
            raise ValueError('该账号已有业务数据（' + detail +
                             '），只能选择「离职」或「退休」存档，不能彻底删除')
    name, tuid = teacher.name, teacher.teacher_uid
    if uid:
        # 注意：system.db 同时是默认库与 'system' bind，通过 ORM 模型分别在两条
        # 连接上写同一文件会 SQLITE_BUSY（自锁）。这里统一走会话默认连接的原生 SQL。
        for table in ('user_permission_groups', 'wechat_bindings', 'wechat_tickets',
                      'user_class_links', 'ai_usage'):
            db.session.execute(
                text(f'DELETE FROM {table} WHERE user_id = :uid'), {'uid': uid})
    db.session.delete(teacher)
    db.session.flush()
    if uid:
        user = db.session.get(User, uid)
        if user is not None:
            db.session.delete(user)
    db.session.commit()
    return f'彻底删除（错误录入）：{name}（{tuid}），原因：{reason}；账号已删除'


def _now():
    from datetime import datetime
    return datetime.now()


def archived_teachers():
    """已归档（离职/退休）教师，按归档时间倒序。"""
    return (Teacher.query.filter(Teacher.archive_kind.isnot(None))
            .order_by(Teacher.archived_at.desc(), Teacher.id.desc()).all())


def archived_map(user_ids):
    """{user_id: Teacher}（仅归档教师）。"""
    ids = [i for i in user_ids if i]
    if not ids:
        return {}
    rows = (Teacher.query
            .filter(Teacher.user_id.in_(ids), Teacher.archive_kind.isnot(None))
            .all())
    return {t.user_id: t for t in rows}


def teacher_map(user_ids):
    """{user_id: Teacher}（全部状态，供 /users/ 行内操作按钮判断）。"""
    ids = [i for i in user_ids if i]
    if not ids:
        return {}
    return {t.user_id: t for t in
            Teacher.query.filter(Teacher.user_id.in_(ids)).all()}

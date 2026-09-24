# StuLink v1.18.2.1 2026-09-24
# 通知公告服务层
# v2.0 收件人表改造：发布时将可见范围展开为 notification_recipients 行，
# 列表/未读数/已读进度全部走单表索引查询；新增 target_type='users' 精确到人。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
from datetime import datetime

from sqlalchemy import func, or_, select, update

from app.extensions import db
from app.models.notification import Notification, NotificationRead, NotificationRecipient
from app.models.user import User
from app.models.student import Student
from app.utils.cache import cache

# v1.16.0 缓存参数：未读数按 user_id 短 TTL（供导航栏高频轮询）；
# target_scope 解析结果按 notification_id 缓存（创建后不再变更，可长 TTL）。
# v2.0 收件人表改造后保留同样的 TTL 与主动失效时机（查询本身已是索引 COUNT，
# 缓存进一步把 30s 轮询压到几乎零 SQL）。
_UNREAD_TTL = 45
_SCOPE_TTL = 600
_UNREAD_PREFIX = 'notif_unread_'

# 收件人 user_id 哨兵约定（与 NotificationRecipient 模型注释一致）：
# 正数=users.id；-student.id=无账号学生；-(1000000+teacher.id)=无账号教师
_TEACHER_SENTINEL_BASE = 1000000


def _invalidate_unread(user_id=None):
    """清除未读数缓存。user_id 为 None 时清除全部用户（通知新增/删除影响全体）。"""
    if user_id is not None:
        cache.delete(f'{_UNREAD_PREFIX}{user_id}')
    else:
        cache.clear(_UNREAD_PREFIX)


def _invalidate_unread_many(user_ids):
    """批量清除多个用户的未读数缓存（负数哨兵 id 无登录账号，跳过）。"""
    for uid in user_ids:
        if uid and uid > 0:
            cache.delete(f'{_UNREAD_PREFIX}{uid}')


def _parsed_scope(notif):
    """按 notification_id 缓存 target_scope 的 json 解析结果（避免重复 json.loads）。"""
    key = f'notif_scope_{notif.id}'
    scope = cache.get(key)
    if scope is None:
        scope = _scope_dict(notif.target_scope)
        cache.set(key, scope, timeout=_SCOPE_TTL)
    return scope


def _scope_dict(target_scope):
    """target_scope（JSON 字符串或 dict）→ dict，解析失败返回 {}"""
    if isinstance(target_scope, dict):
        return target_scope
    try:
        scope = json.loads(target_scope or '{}')
    except (json.JSONDecodeError, TypeError):
        scope = {}
    return scope if isinstance(scope, dict) else {}


def _uids_list(target_uids):
    """target_uids（JSON 字符串或 list）→ 去重去空的字符串列表"""
    if isinstance(target_uids, str):
        try:
            target_uids = json.loads(target_uids or '[]')
        except (json.JSONDecodeError, TypeError):
            target_uids = []
    if not isinstance(target_uids, (list, tuple, set)):
        return []
    out, seen = [], set()
    for u in target_uids:
        u = str(u).strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ── 收件人展开 ────────────────────────────────────────────────

def _resolve_uids(uids, exclude=None, taken_uids=None, taken_ids=None):
    """把业务 uid 列表解析为收件人（按 username → 学号 → teacher_uid 顺序）。

    返回 (recipients, missing_uids)：
    - recipients: [{user_id, user_uid, user_name}]
    - missing_uids: 三处都匹配不到的 uid（调用方决定如何处理，不静默丢弃）
    跨库遵循项目既有模式：User/Student 在 system.db（同一默认绑定），
    Teacher 在 academic.db 独立查询，不做跨库 JOIN。
    """
    exclude = {str(x) for x in (exclude or [])}
    taken_uids = set(taken_uids or [])
    taken_ids = set(taken_ids or [])
    result, seen = [], set()
    remaining = []
    for u in uids:
        u = str(u).strip()
        if u and u not in seen:
            seen.add(u)
            remaining.append(u)
    remaining = [u for u in remaining if u not in exclude and u not in taken_uids]

    # 1) 教职工账号：username
    if remaining:
        users = User.query.filter(User.username.in_(remaining)).all()
        hit = set()
        for u in users:
            hit.add(u.username)
            if u.id not in taken_ids:
                taken_ids.add(u.id)
                result.append({'user_id': u.id, 'user_uid': u.username,
                               'user_name': u.real_name})
        remaining = [u for u in remaining if u not in hit]

    # 2) 学生：student_number（当前无登录账号，user_id 用 -student.id 哨兵）
    if remaining:
        students = Student.query.filter(Student.student_number.in_(remaining)).all()
        hit = set()
        for s in students:
            hit.add(s.student_number)
            sid = -s.id
            if sid not in taken_ids:
                taken_ids.add(sid)
                result.append({'user_id': sid, 'user_uid': s.student_number,
                               'user_name': s.name})
        remaining = [u for u in remaining if u not in hit]

    # 3) 教师：teacher_uid（academic.db；有关联账号 user_id 时优先投递到账号）
    if remaining:
        try:
            from app.models.academic import Teacher
            teachers = Teacher.query.filter(Teacher.teacher_uid.in_(remaining)).all()
        except Exception:  # academic 库故障隔离：教师解析失败不阻塞通知发送
            teachers = []
        hit = set()
        linked = {}
        need_ids = [t.user_id for t in teachers if t.user_id]
        if need_ids:
            linked = {u.id: u for u in User.query.filter(User.id.in_(need_ids)).all()}
        for t in teachers:
            hit.add(t.teacher_uid)
            if t.user_id and t.user_id in linked:
                key = t.user_id
                entry = {'user_id': key, 'user_uid': t.teacher_uid,
                         'user_name': linked[key].real_name or t.name}
            else:
                key = -(_TEACHER_SENTINEL_BASE + t.id)
                entry = {'user_id': key, 'user_uid': t.teacher_uid,
                         'user_name': t.name}
            if key not in taken_ids:
                taken_ids.add(key)
                result.append(entry)
        remaining = [u for u in remaining if u not in hit]

    return result, remaining


def _expand_recipients(target_type, target_scope, target_uids, exclude_uids=None):
    """按定向规则展开收件人。返回 (recipients, missing_uids)。

    可见性语义与旧 _user_matches_target 保持一致：
    - 'all' 与未知类型 → 全体启用用户
    - 'grade'/'class'/'role' → scope 中对应键匹配 User.grade/class_name/role；
      scope 同时含多个键时取并集（混合定向），仅含无关键时结果为空
    - 'users' → 仅 target_uids 显式列出的人
    - 任何类型下 target_uids 都作为追加个人（混合定向），并集后按 user_id 去重
    """
    tt = (target_type or 'all').strip()
    scope = _scope_dict(target_scope)
    uids = _uids_list(target_uids)
    exclude = {str(x) for x in (exclude_uids or [])}
    found = {}

    def _add(user_id, uid, name):
        if uid is not None and str(uid) in exclude:
            return
        if user_id in found:
            return
        found[user_id] = {'user_id': user_id, 'user_uid': uid, 'user_name': name}

    if tt in ('grade', 'class', 'role'):
        conds = []
        if scope.get('grades'):
            conds.append(User.grade.in_(scope['grades']))
        if scope.get('classes'):
            conds.append(User.class_name.in_(scope['classes']))
        if scope.get('roles'):
            conds.append(User.role.in_(scope['roles']))
        if conds:
            for u in User.query.filter(User.is_active.is_(True), or_(*conds)).all():
                _add(u.id, u.username, u.real_name)
    elif tt != 'users':
        # all 及未知类型：全体启用用户（旧逻辑未知类型对所有人可见，保持一致）
        for u in User.query.filter(User.is_active.is_(True)).all():
            _add(u.id, u.username, u.real_name)

    missing = []
    if uids:
        extra, missing = _resolve_uids(
            uids, exclude=exclude,
            taken_uids={r['user_uid'] for r in found.values() if r['user_uid']},
            taken_ids=set(found.keys()))
        for r in extra:
            found.setdefault(r['user_id'], r)
    return list(found.values()), missing


def resolve_recipients(target_type, target_scope=None, target_uids=None,
                       exclude_uids=None):
    """解析可见范围 → 去重后的收件人列表 [{user_id, user_uid, user_name}]。

    target_type: all/grade/class/role/users（users=仅 target_uids 指定的人）
    target_scope: JSON 字符串或 dict，可含 grades/classes/roles（并集）
    target_uids: JSON 字符串或列表，显式追加/指定的个人 uid（username/学号/teacher_uid）
    exclude_uids: 需要排除的 uid 列表
    """
    recipients, _missing = _expand_recipients(
        target_type, target_scope, target_uids, exclude_uids)
    return recipients


def _insert_recipient_rows(notification_id, recipients, read_state=None, chunk=500):
    """批量写入收件人行（分批 bulk_save_objects，几千行无压力）。

    read_state: {user_id: (is_read, read_at)}，refresh 时保留既有已读状态。
    调用方负责 commit。
    """
    read_state = read_state or {}
    now = datetime.now()
    objs = []
    for r in recipients:
        is_read, read_at = read_state.get(r['user_id'], (False, None))
        objs.append(NotificationRecipient(
            notification_id=notification_id,
            user_id=r['user_id'],
            user_uid=r.get('user_uid'),
            user_name=r.get('user_name'),
            is_read=bool(is_read),
            read_at=read_at,
            is_deleted=False,
            created_at=now,
        ))
    for i in range(0, len(objs), chunk):
        db.session.bulk_save_objects(objs[i:i + chunk])
    db.session.flush()
    return len(objs)


# ── 发布 ─────────────────────────────────────────────────────

def create_notification(title, content, target_type='all', target_scope=None,
                        priority='normal', published_by=None,
                        target_uids=None, exclude_uids=None,
                        category='system', biz_type=None, biz_id=None,
                        link_url=None, creator_id=None, creator_name=None,
                        publish=True, **kwargs):
    """创建通知并在同一事务内展开写入收件人行。

    向后兼容：旧调用 create_notification(title, content, target_type,
    target_scope, priority, published_by) 的参数语义不变；返回值由 notif
    变为 (success, message, notif)（既有调用方均未消费返回值）。
    creator_name 仅作为接口占位（模型无发布人姓名列，published_by 存 id）。
    """
    if creator_id is not None:
        published_by = creator_id
    tt = (target_type or 'all').strip()
    scope_str = (target_scope if isinstance(target_scope, str)
                 else json.dumps(target_scope or {}, ensure_ascii=False))
    uids_str = (target_uids if isinstance(target_uids, str)
                else json.dumps(list(target_uids or []), ensure_ascii=False))
    try:
        notif = Notification(
            title=title,
            content=content,
            target_type=tt,
            target_scope=scope_str,
            target_uids=uids_str if tt == 'users' or _uids_list(uids_str) else None,
            priority=priority or 'normal',
            published_by=published_by,
            published_at=datetime.now(),
            is_active=bool(publish),
            category=category or 'system',
            biz_type=biz_type,
            biz_id=biz_id,
            link_url=link_url,
        )
        db.session.add(notif)
        db.session.flush()  # 取得 notif.id
        recipients, _missing = _expand_recipients(tt, scope_str, uids_str, exclude_uids)
        _insert_recipient_rows(notif.id, recipients)
        notif.recipient_count = len(recipients)
        notif.read_count = 0
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'创建通知失败：{e}', None
    # 失效未读数缓存：全员通知清全部；定向通知只清收件人
    if tt == 'all' or tt not in ('grade', 'class', 'role', 'users'):
        _invalidate_unread()
    else:
        _invalidate_unread_many([r['user_id'] for r in recipients])
    return True, ('通知已发布' if publish else '通知已保存（未发布）'), notif


def notify_users(uids, title, content, category='system', biz_type=None,
                 biz_id=None, link_url=None, creator_id=None, creator_name=None,
                 priority='normal', exclude_uids=None):
    """精确到人推送的便捷入口（催交等场景）。

    内部走 create_notification(target_type='users', target_uids=uids, ...)。
    返回 (success, message, {notified, missing_uids, notification_id})；
    uid 匹配不到任何用户/学生/教师时进入 missing_uids，不静默丢弃。
    """
    uid_list = _uids_list(uids)
    if not uid_list:
        return False, '收件人列表为空', {'notified': 0, 'missing_uids': [],
                                       'notification_id': None}
    _probe, missing = _resolve_uids(uid_list, exclude=exclude_uids)
    if not _probe:
        return False, '指定的 uid 均未匹配到收件人', {
            'notified': 0, 'missing_uids': missing, 'notification_id': None}
    success, msg, notif = create_notification(
        title=title, content=content, target_type='users',
        target_uids=uid_list, exclude_uids=exclude_uids, priority=priority,
        category=category or 'system', biz_type=biz_type, biz_id=biz_id,
        link_url=link_url, creator_id=creator_id, creator_name=creator_name,
        publish=True)
    if not success or notif is None:
        return False, msg, {'notified': 0, 'missing_uids': missing,
                            'notification_id': None}
    return True, f'已推送给 {notif.recipient_count or 0} 人', {
        'notified': notif.recipient_count or 0,
        'missing_uids': missing,
        'notification_id': notif.id,
    }


def refresh_recipients(notification_id):
    """重新展开某通知的收件人（未发布/范围被修改后调用）。

    先删旧行再写新行；按 user_id 记忆既有已读状态并回填。
    返回 (success, message, recipient_count)。
    """
    notif = db.session.get(Notification, notification_id)
    if not notif:
        return False, '通知不存在', 0
    try:
        old_rows = NotificationRecipient.query.filter_by(
            notification_id=notification_id).all()
        read_state = {r.user_id: (True, r.read_at) for r in old_rows if r.is_read}
        NotificationRecipient.query.filter_by(
            notification_id=notification_id).delete(synchronize_session=False)
        recipients, _missing = _expand_recipients(
            notif.target_type, notif.target_scope, notif.target_uids)
        _insert_recipient_rows(notif.id, recipients, read_state=read_state)
        notif.recipient_count = len(recipients)
        notif.read_count = sum(
            1 for r in recipients if read_state.get(r['user_id'], (False,))[0])
        db.session.commit()
    except Exception as e:  # noqa: BLE001
        db.session.rollback()
        return False, f'刷新收件人失败：{e}', 0
    # 范围变化可能影响任何用户的可见集，清全部未读缓存与该通知 scope 缓存
    _invalidate_unread()
    cache.delete(f'notif_scope_{notification_id}')
    return True, '收件人已刷新', len(recipients)


# ── 可见性兜底（旧逻辑，仅用于无收件人行的历史通知） ──────────

def _user_matches_target(user, notif):
    """判断通知是否对当前用户可见（v1.x 旧逻辑，保留作兜底）。

    v2.0 起正常路径以 notification_recipients 行为准；本函数仅用于
    recipient_count=0 且非 'users' 类型的历史/旁路通知（如脚本直插模型），
    保证这类通知不会从用户列表里"消失"。'users' 类型无兜底（防误广播）。
    """
    tt = notif.target_type or 'all'
    if tt == 'all':
        return True
    if tt == 'users':
        return False
    scope = _parsed_scope(notif)

    if tt == 'grade':
        grades = scope.get('grades', [])
        return (user.grade or '') in grades

    if tt == 'class':
        classes = scope.get('classes', [])
        return (user.class_name or '') in classes

    if tt == 'role':
        roles = scope.get('roles', [])
        return (user.role or '') in roles

    return True


def _legacy_orphan_notifs(user, category=None, only_unread=False):
    """无收件人行的历史通知（兜底路径），返回 [(notif, is_read)]。

    判定条件：is_active 且 recipient_count 为 0/NULL 且非 'users' 类型。
    已读状态取自 NotificationRead（旧表，仅兜底路径读写）。
    """
    orphans = (Notification.query
               .filter(Notification.is_active.is_(True),
                       func.coalesce(Notification.target_type, 'all') != 'users',
                       func.coalesce(Notification.recipient_count, 0) == 0)
               .all())
    visible = [n for n in orphans if _user_matches_target(user, n)]
    if not visible:
        return []
    ids = [n.id for n in visible]
    read_set = {r.notification_id for r in NotificationRead.query
                .filter(NotificationRead.notification_id.in_(ids),
                        NotificationRead.user_id == user.id).all()}
    out = []
    for n in visible:
        if category and (n.category or 'system') != category:
            continue
        is_read = n.id in read_set
        if only_unread and is_read:
            continue
        out.append((n, is_read))
    out.sort(key=lambda t: ((t[0].priority or ''), t[0].published_at or datetime.min),
             reverse=True)
    return out


# ── 查询（单表索引） ─────────────────────────────────────────

def get_notifications_for_user(user, page=1, per_page=20, category=None,
                               only_unread=False):
    """获取用户可见的通知列表（含已读状态），按优先级+时间倒序，数据库分页。

    v2.0：notification_recipients JOIN notifications 索引查询；不再全量载入。
    返回结构与旧版一致：{items, page, per_page, total, total_pages}，
    items 为 Notification ORM 对象并动态附加 is_read/read_at 属性。
    兼容：user 可传 User 对象或 user_id。
    """
    user_id = user.id if hasattr(user, 'id') else int(user)

    q = (db.session.query(NotificationRecipient, Notification)
         .join(Notification,
               NotificationRecipient.notification_id == Notification.id)
         .filter(NotificationRecipient.user_id == user_id,
                 NotificationRecipient.is_deleted.is_(False),
                 Notification.is_active.is_(True)))
    if category:
        q = q.filter(func.coalesce(Notification.category, 'system') == category)
    if only_unread:
        q = q.filter(NotificationRecipient.is_read.is_(False))

    total = q.count()
    rows = (q.order_by(Notification.priority.desc(),
                       Notification.published_at.desc(),
                       Notification.id.desc())
            .limit(per_page).offset((page - 1) * per_page).all())

    items = []
    for r, n in rows:
        n.is_read = bool(r.is_read)
        n.read_at = r.read_at
        items.append(n)

    # 兜底：无收件人行的历史通知（正常情况下为空集，1 条索引查询）
    orphans = _legacy_orphan_notifs(
        user if hasattr(user, 'id') else db.session.get(User, user_id),
        category=category, only_unread=only_unread)
    if orphans:
        total += len(orphans)
        if page == 1:
            seen = {n.id for n in items}
            for n, is_read in orphans:
                if n.id in seen:
                    continue
                n.is_read = is_read
                n.read_at = None
                items.append(n)

    total_pages = (total + per_page - 1) // per_page if total else 0
    return {
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': total,
        'total_pages': total_pages,
    }


def _unread_count_sql(user_id):
    """1 条 SQL 的未读数：走 idx_notif_recip_user_read 复合索引。"""
    return (db.session.query(func.count(NotificationRecipient.id))
            .join(Notification,
                  NotificationRecipient.notification_id == Notification.id)
            .filter(NotificationRecipient.user_id == user_id,
                    NotificationRecipient.is_read.is_(False),
                    NotificationRecipient.is_deleted.is_(False),
                    Notification.is_active.is_(True))
            .scalar()) or 0


def get_unread_count_accurate(user):
    """精确未读数（导航栏铃铛 30s 轮询依赖，签名与返回类型保持不变）。

    v2.0：单条索引 COUNT 取代全量载入+Python 过滤；
    保留 v1.16.0 的 45s TTL 缓存与主动失效（标记已读/全部已读/发布/删除时清）。
    兼容：user 可传 User 对象或 user_id。
    """
    user_id = user.id if hasattr(user, 'id') else int(user)
    key = f'{_UNREAD_PREFIX}{user_id}'
    cached_val = cache.get(key)
    if cached_val is not None:
        return cached_val
    count = _unread_count_sql(user_id)
    cache.set(key, count, timeout=_UNREAD_TTL)
    return count


def get_unread_count(user):
    """未读数（get_unread_count_accurate 的别名，接受 User 对象或 user_id）。"""
    return get_unread_count_accurate(user)


# ── 已读 / 删除 ──────────────────────────────────────────────

def mark_read(notification_id, user_id):
    """标记为已读（幂等）。

    v2.0：更新收件人行 is_read/read_at 并同步 read_count 快照；
    无收件人行的历史通知走旧 NotificationRead 路径（兜底，保证不丢已读）。
    """
    row = NotificationRecipient.query.filter_by(
        notification_id=notification_id, user_id=user_id).first()
    if row is not None:
        if not row.is_read:
            row.is_read = True
            row.read_at = datetime.now()
            if not row.is_deleted:
                db.session.execute(
                    update(Notification)
                    .where(Notification.id == notification_id)
                    .values(read_count=func.coalesce(Notification.read_count, 0) + 1))
            db.session.commit()
            _invalidate_unread(user_id)
        return True
    # 兜底：历史通知（无收件人行）沿用旧表
    notif = db.session.get(Notification, notification_id)
    if not notif or not notif.is_active:
        return False
    exists = NotificationRead.query.filter_by(
        notification_id=notification_id, user_id=user_id).first()
    if not exists:
        db.session.add(NotificationRead(
            notification_id=notification_id, user_id=user_id,
            read_at=datetime.now()))
        db.session.commit()
        _invalidate_unread(user_id)
    return True


def mark_all_read(user_id):
    """将当前用户全部未读通知标记为已读（单条 UPDATE，不循环），返回新标记条数。"""
    res = db.session.execute(
        update(NotificationRecipient)
        .where(NotificationRecipient.user_id == user_id,
               NotificationRecipient.is_read.is_(False),
               NotificationRecipient.is_deleted.is_(False))
        .values(is_read=True, read_at=datetime.now()))
    count = res.rowcount or 0
    if count:
        # 同步受影响通知的 read_count 快照（单条相关子查询 UPDATE）
        affected = (select(NotificationRecipient.notification_id)
                    .where(NotificationRecipient.user_id == user_id)
                    .scalar_subquery())
        db.session.execute(
            update(Notification)
            .where(Notification.id.in_(affected))
            .values(read_count=(
                select(func.count(NotificationRecipient.id))
                .where(NotificationRecipient.notification_id == Notification.id,
                       NotificationRecipient.is_read.is_(True),
                       NotificationRecipient.is_deleted.is_(False))
                .correlate(Notification)
                .scalar_subquery())))
        db.session.commit()
        _invalidate_unread(user_id)
    return count


def delete_for_user(notification_id, user_id):
    """收件人侧删除（软删自己的收件人行，不影响其他收件人）。"""
    row = NotificationRecipient.query.filter_by(
        notification_id=notification_id, user_id=user_id).first()
    if not row:
        return False
    if not row.is_deleted:
        row.is_deleted = True
        db.session.commit()
        _invalidate_unread(user_id)
    return True


def delete_notification(notification_id, operator_id=None):
    """管理员删除通知：is_active=False（软删通知本体）+ 级联删除收件人行。

    operator_id 仅作调用方标识（当前审计由路由层 log_operation 负责）。
    """
    notif = db.session.get(Notification, notification_id)
    if not notif:
        return False
    notif.is_active = False
    NotificationRecipient.query.filter_by(
        notification_id=notification_id).delete(synchronize_session=False)
    notif.recipient_count = 0
    notif.read_count = 0
    db.session.commit()
    # 删除通知影响全体用户可见集，清除所有未读数缓存与该通知 scope 缓存
    _invalidate_unread()
    cache.delete(f'notif_scope_{notification_id}')
    return True


# v2.0 命名别名（任务规格中的函数名；旧名保持为主实现，兼容既有调用方）
mark_as_read = mark_read
mark_all_as_read = mark_all_read


# ── 详情与已读进度 ───────────────────────────────────────────

def _enrich_recipients(rows, limit=500):
    """收件人行 → 展示信息（补年级/班级；负数哨兵 id 反查学生/教师表）。"""
    rows = rows[:limit]
    user_ids = [r.user_id for r in rows if r.user_id and r.user_id > 0]
    student_ids = [-r.user_id for r in rows
                   if r.user_id and -_TEACHER_SENTINEL_BASE < r.user_id < 0]
    teacher_ids = [-(r.user_id + _TEACHER_SENTINEL_BASE) for r in rows
                   if r.user_id and r.user_id <= -_TEACHER_SENTINEL_BASE]
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    students = {s.id: s for s in Student.query.filter(Student.id.in_(student_ids)).all()} if student_ids else {}
    teachers = {}
    if teacher_ids:
        try:
            from app.models.academic import Teacher
            teachers = {t.id: t for t in Teacher.query.filter(Teacher.id.in_(teacher_ids)).all()}
        except Exception:  # noqa: BLE001
            teachers = {}
    out = []
    for r in rows:
        grade, cls, has_account = '', '', False
        if r.user_id > 0:
            u = users.get(r.user_id)
            has_account = True
            grade = (u.grade if u else '') or ''
            cls = (u.class_name if u else '') or ''
        elif -_TEACHER_SENTINEL_BASE < r.user_id < 0:
            s = students.get(-r.user_id)
            grade = (s.grade if s else '') or ''
            cls = (s.class_name if s else '') or ''
        else:
            t = teachers.get(-(r.user_id + _TEACHER_SENTINEL_BASE))
            cls = (t.subject if t else '') or ''
        out.append({'user_id': r.user_id, 'uid': r.user_uid or '',
                    'name': r.user_name or '', 'grade': grade,
                    'class_name': cls, 'has_account': has_account})
    return out


def get_read_progress(notification_id):
    """已读进度：{recipient_count, read_count, unread_count, percent, unread_users}。

    unread_users 最多返回前 500 人（页面展示用），统计数字始终为全量精确值。
    """
    notif = db.session.get(Notification, notification_id)
    if not notif:
        return None
    base_q = NotificationRecipient.query.filter(
        NotificationRecipient.notification_id == notification_id,
        NotificationRecipient.is_deleted.is_(False))
    total = base_q.count()
    read = base_q.filter(NotificationRecipient.is_read.is_(True)).count()
    unread = total - read
    percent = round(read * 100.0 / total, 1) if total else 0.0
    unread_rows = base_q.filter(NotificationRecipient.is_read.is_(False)) \
        .order_by(NotificationRecipient.user_uid).all()
    return {
        'notification_id': notification_id,
        'recipient_count': total,
        'read_count': read,
        'unread_count': unread,
        'percent': percent,
        'unread_users': _enrich_recipients(unread_rows),
        'unread_users_truncated': len(unread_rows) > 500,
    }


def get_notification_detail(notification_id, user=None):
    """获取通知详情。

    user 为 None（旧签名）→ 仅返回 Notification 对象（向后兼容）。
    传 user → 返回 (notif, meta)：校验可见性（收件人行优先，旧逻辑兜底）、
    自动标记已读（保持现有行为）、发布者/管理员附已读进度。
    meta 为 None 表示当前用户不可见。
    """
    notif = db.session.get(Notification, notification_id)
    if user is None:
        return notif
    if not notif or not notif.is_active:
        return None, None
    row = NotificationRecipient.query.filter_by(
        notification_id=notification_id, user_id=user.id).first()
    if row is not None:
        visible = not row.is_deleted
    else:
        visible = _user_matches_target(user, notif)
    if not visible:
        return notif, None
    # 自动标记已读（保持 v1.x 行为）
    mark_read(notification_id, user.id)
    can_manage = bool(user.role == 'admin'
                      or user.has_perm('system.settings')
                      or notif.published_by == user.id)
    meta = {
        'is_read': True,
        'can_manage': can_manage,
        'progress': get_read_progress(notification_id) if can_manage else None,
    }
    return notif, meta

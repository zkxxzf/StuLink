# -*- coding: utf-8 -*-
"""德育（积分）写工具（2026-10-11 第 2 批）：录入 / 编辑 / 删除 / 批量导入。

权限（与网页端 routes.py 装饰器一致）：
  · 单条录入 / 编辑 / 删除 → points.edit
  · 批量导入               → points.import

范围：网页端用内联的 _scope()/_in_scope()，MCP 用 guards.points_student_scope
复刻同一优先级（用户级 UserDataScope → 组级 school/grade/class）。⚠️ 任课教师
组 scope_type='class'，若未配 UserClassLink 则范围为空、谁也打不了分
（fail-closed，与网页端一致）。

分值约束（与网页端 _parse_payload 同口径）：
  · points 必须是整数、非 0、|points| <= 100；
  · reason 必填；category 截断 20 字、remark 截断 200 字；
  · recorded_at 默认今天；**批量导入强制今天**（import_service 内部写死）。

批量导入：直接复用网页端 import_service.sanitize_rows（服务端重校验，越权行
计入 skipped）+ import_records；并补上网页端缺失的整批 rollback 保护。

刻意未开放：规则模板 CRUD（points.rules，属全局字典配置，建议网页端维护）。
"""
from datetime import date

from ..core import guards, respond, toolkit

_RECORD_FIELDS = ('id', 'student_no', 'student_name', 'grade', 'class_name',
                  'points', 'category', 'reason', 'remark', 'recorded_at',
                  'operator_id', 'operator_name')


def _parse_points(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError('分值必须是整数')


def _parse_date(value):
    text = (value or '').strip()
    if not text:
        return date.today()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise ValueError('日期格式不正确（应为 YYYY-MM-DD）')


def _build(user, student_no, points, reason, category, remark, recorded_at):
    """校验并构造一条积分记录（未 commit）。"""
    from app.models.points import PointRecord
    student_no = (student_no or '').strip()
    if not student_no:
        raise ValueError('请提供学号')
    points = _parse_points(points)
    if points == 0 or abs(points) > 100:
        raise ValueError('分值需在 -100 ~ 100 之间且不为 0')
    reason = (reason or '').strip()
    if not reason:
        raise ValueError('请填写事由')
    student = guards.points_student_scope(user, student_no)   # 范围校验
    return PointRecord(
        student_no=str(student.student_number), student_name=student.name,
        grade=student.grade, class_name=student.class_name, points=points,
        category=(category or '').strip()[:20], reason=reason,
        remark=(remark or '').strip()[:200],
        recorded_at=_parse_date(recorded_at),
        operator_id=user.id, operator_name=user.real_name)


def _create(user, student_no, points, reason, category, remark, recorded_at):
    from app.extensions import db
    from app.utils.helpers import log_operation

    rec = _build(user, student_no, points, reason, category, remark, recorded_at)
    db.session.add(rec)
    db.session.commit()
    log_operation(user, '录入', '积分', rec.id,
                  f'{rec.student_name} {points:+d}分 {rec.reason}（MCP）',
                  module='points')
    return {'created': True, 'record': respond.pick(rec, _RECORD_FIELDS),
            'message': f'已记录 {rec.student_name} {points:+d}分'}


def _update(user, rid, points, reason, category, remark, recorded_at):
    from app.extensions import db
    from app.models.points import PointRecord
    from app.utils.helpers import log_operation

    rec = PointRecord.query.get(int(rid)) if rid else None
    if rec is None:
        raise ValueError(f'积分记录不存在：{rid}')
    # 与网页端一致：按记录学号反查学生判范围（主库已删学生则跳过校验）
    try:
        guards.points_student_scope(user, rec.student_no)
    except ValueError:
        pass                      # 学生已从主库删除：沿用网页端行为，放行
    payload_points = _parse_points(points) if points is not None else rec.points
    if payload_points == 0 or abs(payload_points) > 100:
        raise ValueError('分值需在 -100 ~ 100 之间且不为 0')
    rec.points = payload_points
    if reason is not None:
        rec.reason = reason.strip()
        if not rec.reason:
            raise ValueError('请填写事由')
    if category is not None:
        rec.category = (category or '').strip()[:20]
    if remark is not None:
        rec.remark = (remark or '').strip()[:200]
    if recorded_at is not None:
        rec.recorded_at = _parse_date(recorded_at)
    db.session.commit()
    log_operation(user, '修改', '积分', rec.id,
                  f'{rec.student_name} {rec.points:+d}分（MCP）', module='points')
    return {'updated': True, 'record': respond.pick(rec, _RECORD_FIELDS),
            'message': f'已更新 {rec.student_name} 的积分记录'}


def _delete(user, rid):
    from app.extensions import db
    from app.models.points import PointRecord
    from app.utils.helpers import log_operation

    rec = PointRecord.query.get(int(rid)) if rid else None
    if rec is None:
        raise ValueError(f'积分记录不存在：{rid}')
    try:
        guards.points_student_scope(user, rec.student_no)
    except ValueError:
        pass
    db.session.delete(rec)
    db.session.commit()
    log_operation(user, '删除', '积分', rid,
                  f'{rec.student_name} {rec.points:+d}分（MCP）', module='points')
    return {'deleted': True, 'record': respond.pick(rec, _RECORD_FIELDS),
            'message': f'已删除 {rec.student_name} 的积分记录'}


def _import(user, records):
    """批量导入：复刻网页端 sanitize_rows + import_records，并补整批回滚。

    越权/不合法的行**跳过**（计入 errors / skipped），不是整批拒绝——与网页端
    import/confirm 同口径；但底层写库异常会整体回滚，避免留下部分写入。
    """
    from app.extensions import db
    from app.modules.points.services import import_service
    from app.utils.helpers import log_operation

    if not isinstance(records, list) or not records:
        raise ValueError('records 必须是非空数组')

    def _in_scope(student):
        from app.models import UserClassLink
        from app.modules.grades.services.scope import get_scope, user_grade_scope
        if getattr(user, 'role', None) == 'admin':
            return True
        ug = user_grade_scope(user)
        if ug is not None:
            return student.grade in ug
        scope, grade = get_scope(user)
        if scope == 'school':
            return True
        if scope == 'grade':
            return student.grade == grade
        if scope == 'class':
            pairs = {(l.grade, l.class_name) for l in
                     UserClassLink.query.filter_by(user_id=user.id).all()}
            return (student.grade, student.class_name) in pairs
        return False

    clean, errors, skipped = import_service.sanitize_rows(records,
                                                          in_scope=_in_scope)
    if not clean:
        raise ValueError(
            f'没有可导入的有效记录（错误 {len(errors)} 条，越权跳过 {skipped} 条）')
    try:
        count = import_service.import_records(clean, user.id, user.real_name)
    except Exception as exc:  # noqa: BLE001
        # import_records 内部是「先全部 add、最后一次 commit」，故此处 rollback
        # 覆盖的是提交前的写入阶段（提交成功则无异常，无需回滚）。
        db.session.rollback()
        raise ValueError(f'导入失败，已回滚：{type(exc).__name__}: {exc}')
    log_operation(user, '导入', '积分', 0,
                  f'批量导入 {count} 条（跳过 {skipped} 条，MCP）', module='points')
    return {'imported': count, 'skipped': skipped,
            'errors': respond.limit_rows(errors or []),
            'message': f'已导入 {count} 条积分记录'
                       + (f'，{skipped} 条因越权被跳过' if skipped else '')
                       + (f'，{len(errors)} 条有误' if errors else '')}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='add_point_record',
                 description='【写操作】给学生录入一条德育积分。'
                             'points 为整数（正=加分，负=扣分，非 0，|值|<=100），'
                             'reason 事由必填，category 取 纪律/学习/卫生/活动/其他')
    async def add_point_record(student_no: str, points: int, reason: str,
                               category: str = '', remark: str = '',
                               recorded_at: str = '') -> str:
        params = {'student_no': student_no, 'points': points,
                  'category': category}
        return await toolkit.db_write(
            lambda u: _create(u, student_no, points, reason, category, remark,
                              recorded_at),
            tool='add_point_record', params=params, perm='points.edit')

    @server.tool(name='update_point_record',
                 description='【写操作】修改积分记录（只改传入的字段）；'
                             '记录须在你的德育管理范围内')
    async def update_point_record(rid: int, points: int = None,
                                  reason: str = None, category: str = None,
                                  remark: str = None,
                                  recorded_at: str = None) -> str:
        params = {'rid': rid, 'points': points}
        return await toolkit.db_write(
            lambda u: _update(u, rid, points, reason, category, remark,
                              recorded_at),
            tool='update_point_record', params=params, perm='points.edit')

    @server.tool(name='delete_point_record',
                 description='【写操作】删除积分记录；记录须在你的德育管理范围内')
    async def delete_point_record(rid: int) -> str:
        return await toolkit.db_write(lambda u: _delete(u, rid),
                                      tool='delete_point_record',
                                      params={'rid': rid}, perm='points.edit')

    @server.tool(name='import_point_records',
                 description='【写操作】批量导入德育积分。records 元素含 '
                             'student_no / points / reason，可选 category；'
                             '不合法行计入 errors、越权行跳过计入 skipped。'
                             '注意：批量导入的日期统一为今天')
    async def import_point_records(records: list) -> str:
        params = {'count': len(records or [])}
        return await toolkit.db_write(
            lambda u: _import(u, records), tool='import_point_records',
            params=params, perm='points.import')

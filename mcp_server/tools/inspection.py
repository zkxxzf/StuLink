# -*- coding: utf-8 -*-
"""查课工具（2026-10-11 新增）：录入 / 批量录入 / 编辑 / 删除 / 矩阵标记 / 通知领导。

全部写操作走 `inspection_service`（2026-10-11 从 routes/inspection.py 抽出），
与网页端共用同一套校验，杜绝"网页不许写、AI 却能写"：
  · 权限点   academic.edit（与网页端 5 条写路由一致；注意不是
             academic.inspection_export——那是死键，导出路由实际也用 academic.edit）
  · 范围校验 年级 → 班级 → 教师 三重，复用 access_scope（service 内部完成）

与网页端的差异（刻意为之，便于模型理解）：
  · 网页端越权是 flash + redirect，这里是结构化结果：越权抛 PermissionError
    → toolkit 转成"权限不足：..."，参数问题抛 ValueError → "参数错误：..."
  · 批量录入与矩阵标记沿用网页端"跳过无效行"的语义（巡课常一次几十格），
    但会把 skipped 计数返回，让模型知道有几格没生效。
"""
from ..core import respond, toolkit

_RECORD_FIELDS = ('id', 'inspect_date', 'grade', 'class_name', 'period',
                  'teacher_uid', 'teacher_name', 'subject', 'result', 'note',
                  'inspector_id', 'created_at')

# 与 INSPECTION_RESULTS 一致（normal/late/absent/swap/other）
_RESULTS = ('normal', 'late', 'absent', 'swap', 'other')


def _ok_or_raise(result):
    """service 返回的 (ok, msg, data) → data；失败转 ValueError。"""
    ok, msg, data = result
    if not ok:
        raise ValueError(msg)
    return data


def _add(user, inspect_date, grade, teacher_uid, class_name, period, subject,
         result, note):
    from app.extensions import db
    from app.modules.academic.services import inspection_service
    rec = inspection_service.add_record(
        user, inspect_date=inspect_date, grade=grade, teacher_uid=teacher_uid,
        class_name=class_name, period=period, subject=subject,
        result=result or 'normal', note=note)
    db.session.commit()
    inspection_service.log_inspection(
        user, '新增', rec.id,
        f'{rec.inspect_date} 第{rec.period or "?"}节 {rec.teacher_name}（MCP）')
    return {'created': True, 'record': respond.pick(rec, _RECORD_FIELDS),
            'message': f'已记录 {rec.teacher_name} 的查课情况'}


def _add_batch(user, inspect_date, records):
    from app.extensions import db
    from app.modules.academic.services import inspection_service
    if not isinstance(records, list) or not records:
        raise ValueError('records 必须是非空数组')
    added = inspection_service.add_records_batch(user, inspect_date, records)
    db.session.commit()
    inspection_service.log_inspection(
        user, '批量新增', 0, f'{inspect_date} 共{added}条（MCP）')
    return {'created': added, 'requested': len(records),
            'skipped': len(records) - added,
            'message': f'已录入 {added} 条'
                       + (f'，{len(records) - added} 条因越权或无效被跳过'
                          if added != len(records) else '')}


def _edit(user, rid, result, class_name, subject, period, note):
    from app.extensions import db
    from app.modules.academic.services import inspection_service
    rec = inspection_service.edit_record(
        user, rid, result=result or None, class_name=class_name,
        subject=subject or None, period=period, note=note)
    db.session.commit()
    inspection_service.log_inspection(
        user, '更新', rid, f'{rec.teacher_name} 查课记录更新（MCP）')
    return {'updated': True, 'record': respond.pick(rec, _RECORD_FIELDS),
            'message': '查课记录已更新'}


def _delete(user, rid):
    from app.extensions import db
    from app.modules.academic.services import inspection_service
    rec = inspection_service.delete_record(user, rid)
    db.session.commit()
    inspection_service.log_inspection(
        user, '删除', rid, f'{rec.inspect_date} {rec.teacher_name}（MCP）')
    return {'deleted': True, 'record': respond.pick(rec, _RECORD_FIELDS),
            'message': '查课记录已删除'}


def _mark(user, inspect_date, cells):
    """矩阵标记（同格覆盖更新；result 空串 = 撤销该格）。"""
    from app.extensions import db
    from app.modules.academic.services import inspection_service
    updated, cleared, skipped, touched = inspection_service.mark_cells(
        user, inspect_date, cells)
    db.session.commit()
    if updated or cleared:
        inspection_service.log_inspection(
            user, '查课标记', 0,
            f'{inspect_date} 标记 {updated} 格 / 撤销 {cleared} 格（MCP）')
    return {'updated': updated, 'cleared': cleared, 'skipped': skipped,
            'cells': respond.limit_rows(touched or []),
            'message': f'已标记 {updated} 格'
                       + (f'，撤销 {cleared} 格' if cleared else '')
                       + (f'，忽略 {skipped} 格' if skipped else '')}


def _notify(user, inspect_date, period, grade, expected, checked, unchecked):
    from app.modules.academic.services import inspection_service
    data = _ok_or_raise(inspection_service.notify_leaders(
        user, inspect_date, period, grade=grade, expected=expected,
        checked=checked, unchecked=unchecked or []))
    inspection_service.log_inspection(
        user, '通知', 0, f'{inspect_date} 第{period}节 查课提醒（MCP）')
    return {'notified': True, 'data': data,
            'message': f'已推送给 {data.get("notified", 0)} 位课表管理员'}


def register(server):
    """把本模块的工具注册到 MCP 服务。"""

    @server.tool(name='add_inspection_record',
                 description='【写操作】录入一条查课记录。inspect_date 格式 YYYY-MM-DD，'
                             'grade 如 "2025级"，teacher_uid 为被查课教师编号，'
                             'result 可选 normal(正常)/late(迟到)/absent(缺课)/'
                             'swap(调课)/other(其他)，默认 normal')
    async def add_inspection_record(inspect_date: str, grade: str,
                                    teacher_uid: str, class_name: str = '',
                                    period: int = 0, subject: str = '',
                                    result: str = 'normal',
                                    note: str = '') -> str:
        params = {'inspect_date': inspect_date, 'grade': grade,
                  'teacher_uid': teacher_uid, 'class_name': class_name,
                  'period': period, 'result': result}
        return await toolkit.db_write(
            lambda u: _add(u, inspect_date, grade, teacher_uid, class_name,
                           period or None, subject, result, note),
            tool='add_inspection_record', params=params, perm='academic.edit')

    @server.tool(name='batch_add_inspection_records',
                 description='【写操作】批量录入查课记录。records 为数组，元素含 '
                             'teacher_uid / grade，可选 period / class_name / '
                             'subject / result / note；越权或无效行会被跳过'
                             '（返回 skipped 计数）')
    async def batch_add_inspection_records(inspect_date: str,
                                           records: list) -> str:
        params = {'inspect_date': inspect_date, 'count': len(records or [])}
        return await toolkit.db_write(
            lambda u: _add_batch(u, inspect_date, records),
            tool='batch_add_inspection_records', params=params,
            perm='academic.edit')

    @server.tool(name='update_inspection_record',
                 description='【写操作】修改查课记录（结果/班级/科目/节次/备注），'
                             '只改传入的字段；记录须在你的可见范围内')
    async def update_inspection_record(rid: int, result: str = '',
                                       class_name: str = '', subject: str = '',
                                       period: int = 0, note: str = '') -> str:
        params = {'rid': rid, 'result': result, 'class_name': class_name}
        return await toolkit.db_write(
            lambda u: _edit(u, rid, result, class_name or None, subject,
                            period or None, note or None),
            tool='update_inspection_record', params=params, perm='academic.edit')

    @server.tool(name='delete_inspection_record',
                 description='【写操作】删除查课记录；记录须在你的可见范围内')
    async def delete_inspection_record(rid: int) -> str:
        return await toolkit.db_write(lambda u: _delete(u, rid),
                                      tool='delete_inspection_record',
                                      params={'rid': rid}, perm='academic.edit')

    @server.tool(name='mark_inspection_cells',
                 description='【写操作】按「某天+某班+某节次」批量标记查课结果'
                             '（同格覆盖更新，result 传空串=撤销该格标记）。'
                             'cells 元素含 grade / period_number，可选 class_name / '
                             'entry_id（传 entry_id 时教师与学科以课表为准）/ '
                             'result / note。巡课"一键全部正常"即一次传多格')
    async def mark_inspection_cells(inspect_date: str, cells: list) -> str:
        params = {'inspect_date': inspect_date, 'count': len(cells or [])}
        return await toolkit.db_write(
            lambda u: _mark(u, inspect_date, cells),
            tool='mark_inspection_cells', params=params, perm='academic.edit')

    @server.tool(name='notify_inspection_summary',
                 description='【写操作】把某节次的查课情况以站内通知推送给课表管理员'
                             '（领导）。需 inspect_date + period，可选 grade / '
                             'expected / checked / unchecked（未查班级数组）')
    async def notify_inspection_summary(inspect_date: str, period: int,
                                        grade: str = '', expected: int = 0,
                                        checked: int = 0,
                                        unchecked: list = None) -> str:
        params = {'inspect_date': inspect_date, 'period': period,
                  'grade': grade, 'expected': expected, 'checked': checked}
        return await toolkit.db_write(
            lambda u: _notify(u, inspect_date, period, grade, expected,
                              checked, unchecked or []),
            tool='notify_inspection_summary', params=params,
            perm='academic.edit')

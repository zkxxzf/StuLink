# StuLink 2026-10-18
# 删除归档 / 恢复工具（回收站）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""删除前归档、恢复时重建 —— 与 `models/trash.py` 配套。

用法（在 `db.session.delete()` / `query.delete()` **之前**调用）：

    from app.utils.trash import archive
    archive('grades', 'affair_student', '考务名单', rows,
            summary=f'{affair.name}：{len(rows)} 人', operator=current_user)
    AffairStudent.query.filter_by(affair_id=aid).delete()

约定：
- `rows` 传单个对象或列表；`payload` 单行存 dict、整批存 list；
- 归档异常**不抛出**（只打日志），保证删除流程不被回收站拖垮；
- 恢复用**新 id**（避免与现存行撞主键），唯一约束冲突的行跳过并计入失败。
"""
import importlib
import json
import logging
import os
from datetime import date, datetime

from app.extensions import db
from app.models.trash import TrashRecord

logger = logging.getLogger(__name__)

# kind → (模块路径, 模型类名)；恢复时动态取，避免循环 import
_MODELS = {
    'affair_student': ('app.models.grades', 'AffairStudent'),
    'affair_room': ('app.models.grades', 'AffairRoom'),
    'affair_room_lib': ('app.models.grades', 'AffairRoomLib'),
    'affair': ('app.models.grades', 'ExamAffair'),
    'affair_invigilator': ('app.models.grades', 'AffairInvigilator'),
    'room': ('app.models.room', 'Room'),
    'bed_assignment': ('app.models.room', 'BedAssignment'),
    'student_accommodation': ('app.models.room', 'StudentAccommodation'),
    'schedule_entry': ('app.models.timetable', 'ScheduleEntry'),
    'exam_score': ('app.models.grades', 'ExamScore'),
    'student': ('app.models.student', 'Student'),
    # 2026-10-18（口径）：字典项删除接回收站，误删可恢复
    # 注：旧课表模块（routes/timetable.py 的 Timetable/TimetableEntry）已下线 ——
    # 模型与蓝图都不存在，当前课表统一走 schedule 模块，故不在此登记。
    'dictionary_item': ('app.models.dictionary', 'DictItem'),
}


def _attr_key(model_or_obj, col):
    """列 → ORM 属性名（类属性名）。

    加密字段列名是 `phone1`（property 会解密），而真实列属性名是
    `_phone1_encrypted`（密文）。归档/恢复都必须用后者。
    """
    try:
        return model_or_obj.__mapper__.get_property_by_column(col).key
    except Exception:                   # noqa: BLE001
        return col.name


def _dump(obj, keep_id=False):  # noqa: D401
    """整行 → dict（默认跳过主键；datetime 转 isoformat）。

    keep_id=True 只给 `archive_snapshot` 用：整批级联删除必须保留原 id，
    否则恢复后子行的 exam_id / affair_id 会指向不存在的父行（假恢复）。
    """
    data = {}
    for col in obj.__table__.columns:
        if col.name == 'id' and not keep_id:
            continue
        # 2026-10-18：取 **mapper 的属性 key**（类属性名），不能用 col.name ——
        # 加密字段的 property 会把密文解密成明文，用 col.name 会把明文身份证/
        # 手机号写进回收站 payload（等于换个地方裸奔）。
        # 注意 Column.key 默认就等于 col.name，必须用 __mapper__ 查真正的属性名。
        # 普通列两者相同，行为不变。
        val = getattr(obj, _attr_key(obj, col), None)
        # 2026-10-18：`date` 也必须序列化 —— Exam.exam_date 是 date 类型，
        # 只判 datetime 会让整场考试快照 json.dumps 抛 TypeError，
        # 再被上层 except 吞掉变成"静默失败"（归档没存，删除照做）。
        data[col.name] = (val.isoformat()
                          if isinstance(val, (datetime, date)) else val)
    return data


def archive(module, kind, label, rows, summary='', operator=None, count=None):
    """把 rows 序列化进回收站，返回 TrashRecord（失败返回 None，不抛异常）。"""
    try:
        items = list(rows) if isinstance(rows, (list, tuple)) else [rows]
        if not items:
            return None
        payload = [_dump(x) for x in items]
        rec = TrashRecord(
            module=module, kind=kind, kind_label=label,
            ref_id=(getattr(items[0], 'id', None) if len(items) == 1 else None),
            summary=(summary or '')[:250],
            payload=json.dumps(payload if len(payload) > 1 else payload[0],
                               ensure_ascii=False),
            item_count=(count if count is not None else len(items)),
        )
        if operator is not None:
            rec.operator_id = getattr(operator, 'id', None)
            rec.operator_name = (getattr(operator, 'real_name', None)
                                 or getattr(operator, 'username', '') or '')
        db.session.add(rec)
        return rec
    except Exception:                       # noqa: BLE001
        logger.warning('回收站归档失败 module=%s kind=%s', module, kind, exc_info=True)
        return None


def archive_snapshot(module, kind, label, tables, summary='', operator=None,
                     update_existing=False):
    """一次归档**一组关联表**，恢复时按**原 id** 重建。

    用于"整场考试 / 整批考务"这类级联删除：普通 `archive()` 恢复时会丢弃 id
    重新编号，父行换了新 id 后，子行的 exam_id / affair_id 全部变成孤儿 ——
    页面看着"恢复了"，其实数据全断链。快照模式把 id 一并存下、按原值插回，
    并且**父表在前、子表在后**，外键因此始终有效。

    tables: [(模型类, 行或行列表), ...]，顺序必须"父表 → 子表"。
    update_existing=True：用于"只清字段、不删行"的操作（重排/清空分配），
    恢复时按 id 更新现有行，而不是插入新行。
    """
    try:
        groups, total = [], 0
        for model, rows in tables:
            if rows is None:
                continue
            items = list(rows) if isinstance(rows, (list, tuple)) else [rows]
            items = [x for x in items if x is not None]
            if not items:
                continue
            groups.append({
                'model': f'{model.__module__}.{model.__name__}',
                'rows': [_dump(x, keep_id=True) for x in items],
            })
            total += len(items)
        if not total:
            return None
        payload = {'keep_ids': True, 'update_existing': bool(update_existing),
                   'tables': groups}
        rec = TrashRecord(
            module=module, kind=kind, kind_label=label,
            ref_id=None,
            summary=(summary or '')[:250],
            payload=json.dumps(payload, ensure_ascii=False),
            item_count=total,
        )
        if operator is not None:
            rec.operator_id = getattr(operator, 'id', None)
            rec.operator_name = (getattr(operator, 'real_name', None)
                                 or getattr(operator, 'username', '') or '')
        db.session.add(rec)
        return rec
    except Exception:                       # noqa: BLE001  归档失败不阻断删除流程
        logger.warning('回收站快照归档失败 module=%s kind=%s', module, kind, exc_info=True)
        return None


def purge_expired_records(dry_run=True, export_dir=None,
                          include_restored=True, module=None):
    """P2：清理已过期归档（expires_at < now）。

    返回 dict：count（命中条数）、deleted（实际删除）、export（导出文件）、
    sample（前 5 条元数据）、error（失败原因）。默认 dry-run；
    apply 前先导出 JSONL 元数据（不含 payload，避免 PII 再扩散）。
    expires_at 为空的历史行走“不自动删”策略，避免误删。
    """
    now = datetime.now()
    q = TrashRecord.query.filter(TrashRecord.expires_at.isnot(None),
                                 TrashRecord.expires_at < now)
    if not include_restored:
        q = q.filter(TrashRecord.restored_at.is_(None))
    if module:
        q = q.filter(TrashRecord.module == module)
    rows = q.order_by(TrashRecord.id).all()
    sample = [{
        'id': r.id, 'module': r.module, 'kind': r.kind,
        'summary': (r.summary or '')[:120],
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M:%S')
                      if r.created_at else '',
        'expires_at': r.expires_at.strftime('%Y-%m-%d %H:%M:%S')
                      if r.expires_at else '',
        'restored': bool(r.restored_at),
    } for r in rows[:5]]
    result = {'count': len(rows), 'deleted': 0, 'export': None,
              'sample': sample, 'error': None}
    if dry_run or not rows:
        return result

    if export_dir:
        try:
            os.makedirs(export_dir, exist_ok=True)
            ts = now.strftime('%Y%m%d_%H%M%S')
            path = os.path.join(export_dir, f'trash_{ts}.jsonl')
            with open(path, 'w', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps({
                        'id': r.id, 'module': r.module, 'kind': r.kind,
                        'kind_label': r.kind_label, 'ref_id': r.ref_id,
                        'summary': r.summary, 'item_count': r.item_count,
                        'operator_id': r.operator_id,
                        'operator_name': r.operator_name,
                        'created_at': r.created_at.isoformat() if r.created_at else None,
                        'expires_at': r.expires_at.isoformat() if r.expires_at else None,
                        'restored_at': r.restored_at.isoformat() if r.restored_at else None,
                    }, ensure_ascii=False) + '\n')
            result['export'] = path
        except OSError as exc:
            logger.warning('回收站过期导出失败，已中止删除: %s', exc)
            result['error'] = f'导出失败，已中止删除：{exc}'
            return result

    try:
        ids = [r.id for r in rows]
        deleted = (TrashRecord.query.filter(TrashRecord.id.in_(ids))
                   .delete(synchronize_session=False))
        db.session.commit()
        result['deleted'] = deleted
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        logger.warning('回收站过期清理失败: %s', exc, exc_info=True)
        result['error'] = f'删除失败：{exc}'
    return result


def model_of(kind):
    """kind → 模型类（未知类型抛 ValueError）。"""
    path, cls = _MODELS.get(kind) or (None, None)
    if not path:
        raise ValueError(f'未知归档类型：{kind}')
    return getattr(importlib.import_module(path), cls)


def restore(rec):
    """恢复一条归档。返回 (ok, message, 成功行数)。"""
    try:
        data = json.loads(rec.payload or 'null')
    except (TypeError, ValueError):
        return False, '归档内容损坏（JSON 解析失败）', 0
    if not data:
        return False, '归档内容为空', 0
    # 快照模式（整场考试 / 整批考务的级联删除）：按原 id 重建，
    # 父表在前、子表在后。必须在 model_of 之前分流 —— 快照的 kind
    # 不在 _MODELS 白名单里（它一次对应多张表）。
    if isinstance(data, dict) and data.get('keep_ids'):
        return _restore_snapshot(rec, data)
    try:
        model = model_of(rec.kind)
    except ValueError as e:
        return False, str(e), 0

    items = data if isinstance(data, list) else [data]
    cols = {c.name: c for c in model.__table__.columns}
    # 2026-10-18：恢复时按 col.key 赋值（绕过 property setter）——
    # 否则密文会被 setter **二次加密**（密文 → 密文的密文），恢复出来全是乱码。
    key_of = {c.name: _attr_key(model, c) for c in model.__table__.columns}
    okn, skipped = 0, []
    for row in items:
        clean = {}
        for name, val in (row or {}).items():
            if name not in cols:
                continue
            if isinstance(val, str) and \
                    cols[name].type.__class__.__name__ == 'DateTime':
                try:
                    val = datetime.fromisoformat(val)
                except ValueError:
                    val = None
            clean[key_of.get(name, name)] = val
        savepoint = db.session.begin_nested()
        try:
            db.session.add(model(**clean))
            savepoint.commit()
            okn += 1
        except Exception as e:              # noqa: BLE001  唯一约束冲突等 → 跳过该行
            savepoint.rollback()
            skipped.append(f'{type(e).__name__}')
            logger.info('回收站恢复跳过一行 kind=%s err=%s', rec.kind, e)
    rec.restored_at = datetime.now()
    db.session.commit()
    # 2026-10-18：字典项恢复后必须清缓存，否则下拉框在缓存 TTL 内仍看不到它
    if okn and rec.kind == 'dictionary_item':
        try:
            from app.utils.helpers import clear_dict_cache
            clear_dict_cache()
        except Exception:                  # noqa: BLE001  缓存清理失败不影响恢复结果
            logger.warning('回收站恢复后清除字典缓存失败', exc_info=True)
    msg = f'已恢复 {okn} / {len(items)} 行'
    if skipped:
        msg += f'；{len(skipped)} 行与现存数据冲突（已跳过）'
    return (okn > 0), msg, okn


def _restore_snapshot(rec, data):
    """按原 id 恢复整组关联表（与 `archive_snapshot` 配套）。

    逐表、逐行用保存点插入：任何一行与现存数据冲突只跳过它，不牵连其它行。
    父表先插入，子行因此能找到自己的 exam_id / affair_id，不会变成孤儿。
    """
    total, okn = 0, 0
    update_existing = bool(data.get('update_existing'))
    for tbl in data.get('tables') or []:
        path = (tbl or {}).get('model') or ''
        try:
            mod_name, cls_name = path.rsplit('.', 1)
            model = getattr(importlib.import_module(mod_name), cls_name)
        except Exception:                   # noqa: BLE001  模型已下线/改名 → 跳过该表
            logger.warning('快照恢复找不到模型 %s', path, exc_info=True)
            continue
        cols = {c.name: c for c in model.__table__.columns}
        key_of = {c.name: _attr_key(model, c) for c in model.__table__.columns}
        for row in tbl.get('rows') or []:
            total += 1
            clean = {}
            for name, val in (row or {}).items():
                if name not in cols:
                    continue
                type_name = cols[name].type.__class__.__name__
                if isinstance(val, str) and type_name in ('DateTime', 'Date'):
                    try:
                        # Date 列必须还原成 date（传 datetime 会被 SQLAlchemy 拒）
                        val = (datetime.fromisoformat(val)
                               if type_name == 'DateTime'
                               else date.fromisoformat(val[:10]))
                    except ValueError:
                        val = None
                clean[key_of.get(name, name)] = val
            if update_existing:
                pk = (row or {}).get('id')
                obj = db.session.get(model, pk) if pk is not None else None
                if obj is None:
                    logger.info('快照恢复找不到现有行 %s id=%s，跳过', path, pk)
                    continue
                for key, val in clean.items():
                    if key != 'id':
                        setattr(obj, key, val)
                okn += 1
                continue
            savepoint = db.session.begin_nested()
            try:
                db.session.add(model(**clean))
                savepoint.commit()
                okn += 1
            except Exception as e:          # noqa: BLE001  主键/唯一约束冲突 → 跳过
                savepoint.rollback()
                logger.info('快照恢复跳过一行 %s err=%s', path, e)
    # 先提交业务行，释放默认 bind 的读事务；否则 TrashRecord（system bind）
    # 与业务表同文件时，两个 SQLite 连接会互相锁住（database is locked）。
    if update_existing:
        db.session.commit()
    rec.restored_at = datetime.now()
    db.session.commit()
    msg = f'已恢复 {okn} / {total} 行（按原 id，外键保持有效）'
    return (okn > 0), msg, okn

# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import json
import os
import re
from app.models import DictCategory, Student, Room, ClassProfile, ClassSubject
from app.utils.cache import cache
from flask import request, g


def _insert_log_row(values):
    """用**独立连接**写审计日志。

    为什么不用 db.session：审计日志既要「立即落库」又「不能连带提交业务数据」，
    这两点在同一会话里无法兼得（session.commit 会把别人未提交的数据一起提交；
    不 commit 则请求结束时被 teardown rollback 掉）。独立连接两者都满足：
    业务 rollback 也不会把日志带走。

    SQLite 下若业务正持有写锁，PRAGMA busy_timeout=300 最多等 0.3s，冲突时抛错，
    由调用方回退到业务会话内的 SAVEPOINT 写法（尽最大努力，不阻断业务）。
    """
    from app.extensions import db
    from app.models.operation_log import OperationLog

    table = OperationLog.__table__
    with db.engine.connect() as conn:
        try:
            conn.exec_driver_sql('PRAGMA busy_timeout=300')
        except Exception:  # noqa: BLE001  非 SQLite（或不支持该 PRAGMA）忽略
            pass
        conn.execute(table.insert().values(**values))
        conn.commit()


def log_operation(user, action, target_type=None, target_id=None, detail=None,
                  module='system', severity='INFO', endpoint=None, method=None,
                  status_code=None):
    """记录操作审计日志（静默失败，不阻塞主流程）。

    2026-09-25 审计加固：
    - **不再无条件 db.session.commit()**：旧实现会把调用点尚未提交的业务数据
      一并落库，业务随后 rollback 也撤不回来。现在改为独立连接写入（见
      `_insert_log_row`），锁冲突时回退到业务会话内的 SAVEPOINT（只 add 不 commit，
      随业务事务一起落库）。
    - detail 支持 dict/list，自动序列化为 JSON（ensure_ascii=False）。
    - 写入后在 g 上打 `_audit_logged` 标记，after_request 兜底网据此去重。
    """
    try:
        from datetime import datetime
        from app.extensions import db
        from app.models.operation_log import OperationLog

        if isinstance(detail, (dict, list, tuple)):
            try:
                detail = json.dumps(detail, ensure_ascii=False, default=str)
            except Exception:  # noqa: BLE001  序列化失败退化为 str
                detail = str(detail)

        try:
            ip_address = request.remote_addr if request else None
        except Exception:  # noqa: BLE001  非请求上下文（脚本/任务）
            ip_address = None

        values = {
            'user_id': user.id if user else None,
            'action': str(action)[:20] if action else None,
            'target_type': str(target_type)[:30] if target_type else None,
            'target_id': target_id,
            'detail': str(detail)[:2000] if detail else None,
            'ip_address': ip_address,
            'module': str(module)[:30] if module else 'system',
            'severity': severity or 'INFO',
            'endpoint': str(endpoint)[:120] if endpoint else None,
            'method': str(method)[:10] if method else None,
            'status_code': status_code,
            'request_id': getattr(g, 'request_id', None),
            'created_at': datetime.now(),
        }

        try:
            _insert_log_row(values)
        except Exception:  # noqa: BLE001  锁冲突等 → 退回业务会话内 SAVEPOINT
            with db.session.begin_nested():
                db.session.add(OperationLog(**values))

        g._audit_logged = True  # 供 after_request 兜底网去重
    except Exception:
        pass  # 日志记录失败不阻塞业务


def is_dict_value_in_use(category_code, value):
    """检查字典值是否被学生或宿舍引用"""
    try:
        if category_code == 'grade':
            if Student.query.filter_by(grade=value).first():
                return True
            if Room.query.filter_by(grade=value).first():
                return True
            try:
                if ClassProfile.query.filter_by(grade=value).first():
                    return True
            except Exception:
                pass
        elif category_code == 'class':
            if Student.query.filter_by(class_name=value).first():
                return True
            if Room.query.filter_by(class_name=value).first():
                return True
            try:
                if ClassProfile.query.filter_by(class_name=value).first():
                    return True
            except Exception:
                pass
        elif category_code == 'building':
            if Room.query.filter_by(building=value).first():
                return True
        elif category_code == 'floor':
            try:
                floor_num = int(value.replace(' 层', '').replace('层', '').strip())
                if Room.query.filter_by(floor=floor_num).first():
                    return True
            except (ValueError, AttributeError):
                pass
        elif category_code == 'boarding_type':
            from app.models import StudentAccommodation
            if StudentAccommodation.query.filter_by(boarding_type=value).first():
                return True
        elif category_code == 'enrollment_status':
            if Student.query.filter_by(enrollment_status=value).first():
                return True
        elif category_code == 'day_student_type':
            from app.models import StudentAccommodation
            if StudentAccommodation.query.filter_by(day_student_type=value).first():
                return True
        elif category_code == 'class_type':
            try:
                if ClassProfile.query.filter_by(class_type=value).first():
                    return True
            except Exception:
                pass
        elif category_code == 'subject_direction':
            try:
                if ClassProfile.query.filter_by(subject_direction=value).first():
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def get_dict_items(code):
    """根据字典分类code获取所有选项，返回 [(值，值), ...] 用于WTForms SelectField"""
    cat = DictCategory.query.filter_by(code=code).first()
    if not cat:
        return []
    items = cat.items.filter_by(is_active=True).order_by('sort_order').all()
    return [(item.value, item.value) for item in items]


def get_dict_values(code):
    """根据字典分类code获取所有选项值列表（带缓存优化）"""
    cache_key = f'dict_values_{code}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    cat = DictCategory.query.filter_by(code=code).first()
    if not cat:
        return []
    
    values = [item.value for item in cat.items.filter_by(is_active=True).order_by('sort_order').all()]
    
    cache.set(cache_key, values, timeout=600)
    
    return values


def clear_dict_cache():
    """清除字典缓存（在字典数据更新后调用）"""
    keys_to_delete = [key for key in cache._cache.keys() if key.startswith('dict_values_')]
    for key in keys_to_delete:
        cache.delete(key)


def get_graduated_grades():
    """返回已毕业年级列表（TTL 600s 缓存，写法同 get_dict_values）

    被 statistics/scope/students 等多处每请求多次调用（曾在循环体内），
    而毕业标记几乎不变；年级毕业操作后需调 clear_graduated_grades_cache() 主动失效。
    """
    cache_key = 'graduated_grades'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        from app.models.grade_setting import GradeSetting
        grades = [gs.grade for gs in GradeSetting.query.filter_by(is_graduated=True).all()]
    except Exception:
        return []  # 异常不写缓存，下次重试
    cache.set(cache_key, grades, timeout=600)
    return grades


def clear_graduated_grades_cache():
    """清除已毕业年级缓存（年级毕业标记变更后调用）"""
    cache.delete('graduated_grades')


def get_active_grades():
    """在校生年级（排除已毕业年级）

    主应用（StuLink）统一使用：年级下拉框、年级筛选、自动/可视化宿舍分配等
    一律只看到在校年级；已毕业年级只出现在往届生查询站（Alumni）。
    """
    graduated = get_graduated_grades()
    if not graduated:
        return get_dict_values('grade')
    return [g for g in get_dict_values('grade') if g not in graduated]


# 标准班级名：包含数字+"班"（如 01班、2024级01班）
# 非标准班级：未分班、不分班、已转出、借读等
_STANDARD_CLASS_PATTERN = re.compile(r'\d+班')


def is_standard_class(class_name):
    """是否为正式班级（排除 不分班/已转出/借读 等）"""
    if not class_name:
        return False
    return bool(_STANDARD_CLASS_PATTERN.search(class_name))


def get_class_options():
    """班级下拉框选项：只返回正式班级，排除 不分班/已转出 等"""
    return [c for c in get_dict_values('class') if is_standard_class(c)]


# ---- 系统设置（存库，运行期可维护）----

SCHOOL_NAME_KEY = 'school_name'
DEFAULT_SCHOOL_NAME = '某某学校'   # 占位化名：真实校名不写进代码/仓库


def get_school_name():
    """学校名称：环境变量 SCHOOL_NAME > 数据库 system_settings > 占位化名

    说明：默认值必须是化名，真实校名通过环境变量或「系统设置」页注入，
    避免学校名称出现在公开仓库中。
    """
    env_name = (os.environ.get('SCHOOL_NAME') or '').strip()
    if env_name:
        return env_name
    try:
        from app.models.system_setting import SystemSetting
        db_name = (SystemSetting.get(SCHOOL_NAME_KEY) or '').strip()
        if db_name:
            return db_name
    except Exception:
        pass
    return DEFAULT_SCHOOL_NAME


# ---- 选科组合 ----

# 3+1+2 共 12 种组合，物理方向在前、历史方向在后（与字典表 subject 保持一致）
SUBJECT_COMBINATIONS = [
    '物化生', '物化地', '物化政', '物生地', '物生政', '物地政',
    '史化生', '史化地', '史化政', '史生地', '史生政', '史地政',
]
# 旧写法 → 新写法（统一为「X地政」顺序，避免同义异写）
SUBJECT_RENAMES = {
    '物政地': '物地政',
    '史政地': '史地政',
}


def subject_direction(subject):
    """选科组合所属方向：physics（物理）/ history（历史）/ 空"""
    if not subject:
        return ''
    if subject.startswith('物'):
        return 'physics'
    if subject.startswith('史'):
        return 'history'
    return ''


def subject_badge_class(subject):
    """选科标签的配色 class（物理方向 / 历史方向分色）"""
    d = subject_direction(subject)
    return f'subj-{d}' if d else ''


def normalize_subject(subject):
    """把旧写法（如 史政地）规范为新写法（史地政）"""
    if not subject:
        return subject
    return SUBJECT_RENAMES.get(subject.strip(), subject.strip())


def set_school_name(value, user_id=None):
    """保存学校名称到数据库"""
    from app.extensions import db
    from app.models.system_setting import SystemSetting

    SystemSetting.set(SCHOOL_NAME_KEY, (value or '').strip(), user_id=user_id,
                      description='学校名称：成绩证明等对外文书抬头')
    db.session.commit()


def _change_log_ddl():
    """变迁日志表建表 SQL（与旧版 schema 完全一致）"""
    return '''
        CREATE TABLE IF NOT EXISTS student_change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            student_number TEXT,
            student_name TEXT NOT NULL,
            change_type TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            detail TEXT,
            operator TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    '''


def init_history_tables():
    """启动时建好 history.db 的变迁日志表（create_app 调用一次，
    避免 write_change_log 每次写入都新建 sqlite3 连接并执行 CREATE TABLE）"""
    from sqlalchemy import text
    from app.extensions import db
    try:
        engine = db.engines.get('history')
        if engine is None:
            return
        with engine.begin() as conn:
            conn.execute(text(_change_log_ddl()))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f'初始化变迁日志表失败: {e}')


def write_change_log(change_type, students_data, old_value='', new_value='', detail='',
                     operator_name='', changed_at=None):
    """写入学生变迁日志到 history.db（复用 history bind 的连接池，不再每次新建连接）
    students_data: list of dicts with keys id, student_number, name
    函数签名与调用方式与旧版完全一致；表已在 create_app 启动时建好，
    若表缺失（如绕过启动初始化直接调脚本）则自动补建一次后重试。
    changed_at: v1.18.9.2 新增，指定历史时间（'YYYY-MM-DD HH:MM:SS'）；
                None = 用当前时间（表默认值）
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from app.extensions import db
    rows = [
        {'sid': s['id'], 'sno': s.get('student_number', ''), 'sname': s.get('name', ''),
         'ctype': change_type, 'old': old_value, 'new': new_value,
         'detail': detail, 'operator': operator_name}
        for s in students_data
    ]
    if not rows:
        return
    if changed_at:
        insert_sql = text(
            'INSERT INTO student_change_log (student_id,student_number,student_name,change_type,old_value,new_value,detail,operator,changed_at) '
            'VALUES (:sid,:sno,:sname,:ctype,:old,:new,:detail,:operator,:cat)'
        )
        for r in rows:
            r['cat'] = changed_at
    else:
        insert_sql = text(
            'INSERT INTO student_change_log (student_id,student_number,student_name,change_type,old_value,new_value,detail,operator) '
            'VALUES (:sid,:sno,:sname,:ctype,:old,:new,:detail,:operator)'
        )
    try:
        engine = db.engines.get('history')
        if engine is None:
            return
        try:
            with engine.begin() as conn:
                conn.execute(insert_sql, rows)
        except OperationalError:
            # 表不存在（未经启动初始化的调用方）：补建后重试一次
            with engine.begin() as conn:
                conn.execute(text(_change_log_ddl()))
                conn.execute(insert_sql, rows)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f'写入变迁日志异常: {e}', exc_info=True)


# ==================== v1.18.9.2 调班记录（学生班级变更） ====================

#: 班级类变更类型 → (中文标签, Bootstrap 颜色)
#: enroll  入校分班：新增学生 / 导入新生时带班级
#: reassign 重新分班：Excel 批量调班（多人同时重排）
#: transfer 个别调班：转班弹窗 / 因学籍状态归置班级
#: correct  修正班级：此前录入有误，改对而已（非真实换班）
#: direction 转科：物理 ↔ 历史（方向变了）
#: subject_selection 选科组合变更：方向不变，只换组合（物化生 → 物化地）
#: withdraw 转出：学籍已转出 → 班级归置为「已转出」
#: enrollment 学籍变更：学籍情况字段本身发生变化
#: number  学号变更：修正学生学号
#: class    旧版批量调班（历史数据兼容，仅读不写）
CLASS_CHANGE_TYPES = ('enroll', 'reassign', 'transfer', 'correct',
                      'direction', 'subject_selection', 'withdraw',
                      'enrollment', 'number', 'class')

CHANGE_TYPE_LABELS = {
    'enroll': ('入校分班', 'primary'),
    'reassign': ('重新分班', 'warning'),
    'transfer': ('个别调班', 'info'),
    'correct': ('修正班级', 'secondary'),
    'direction': ('转科', 'danger'),
    'subject_selection': ('选科变更', 'info'),
    'withdraw': ('转出', 'dark'),
    'enrollment': ('学籍变更', 'warning'),
    'number': ('学号变更', 'secondary'),
    'class': ('调班', 'info'),          # 旧数据
    'graduate': ('毕业', 'success'),
    'dormitory': ('宿舍', 'warning'),
}


def change_type_label(change_type):
    """变更类型 → (中文标签, 颜色)；未知名原样返回"""
    return CHANGE_TYPE_LABELS.get(change_type, (change_type or '', 'secondary'))


# ==================== v1.18.9.2 方向推导与就读位置描述 ====================

_DIR_RULES = (
    ('物', '物理'), ('史', '历史'),
    ('理科', '物理'), ('理', '物理'),
    ('文科', '历史'), ('文', '历史'),
)


def resolve_direction(subject_selection, class_name=None, grade=None):
    """由选科组合推导方向（物理/历史）。

    与 import_service 同口径：优先本人选科，无则回落班级档（ClassProfile.subject_direction）。
    subject_selection 形如 '物化生' / '史政地' / '理科'。无法识别返回 ''。
    """
    sel = (subject_selection or '').strip()
    if sel:
        for prefix, d in _DIR_RULES:
            if sel.startswith(prefix):
                return d
    if class_name:
        try:
            from app.models.class_profile import ClassProfile
            q = ClassProfile.query.filter_by(class_name=class_name)
            if grade:
                q = q.filter_by(grade=grade)
            cp = q.first()
            if cp and cp.subject_direction:
                return cp.subject_direction
        except Exception:
            pass
    return ''


def get_class_type(grade, class_name):
    """取该班当前班型（强基班/卓越班…）；无则返回 ''"""
    if not (grade and class_name):
        return ''
    try:
        from app.models.class_profile import ClassProfile
        cp = ClassProfile.query.filter_by(grade=grade, class_name=class_name).first()
        return (cp.class_type or '') if cp else ''
    except Exception:
        return ''


def get_class_selection(grade, class_name):
    """取该班在班型设置中登记的选科组合（可能多个，顿号连接）；无则 ''

    仅作学生本人选科缺失时的回落展示（如 03班 登记为“物化政、物化地”）。
    """
    if not (grade and class_name):
        return ''
    try:
        from app.models.class_profile import ClassProfile
        cp = ClassProfile.query.filter_by(grade=grade, class_name=class_name).first()
        if not cp:
            return ''
        lst = cp.subject_list
        return '、'.join(lst) if lst else ''
    except Exception:
        return ''


def location_desc(grade, class_name, class_type=None, direction=None, selection=None):
    """构造“就读位置”描述：`2024级01班·强基班·物理·物化生`

    班型 / 方向 / 选科为空时自动省略对应段，保证旧数据与无班型场景可兼。
    方向与选科同时保留：同一方向下组合可能不同（物化生 / 物化地），需能区分。
    **非教学班**（已转出 / 不分班等）只返回“年级+班级”，不挂属性（它们不参与教学与考试）。
    """
    base = f'{grade or ""}{class_name or ""}'
    if not _is_teaching_class(class_name):
        return base
    parts = [p for p in (class_type, direction, selection) if p]
    return base + (''.join('·' + p for p in parts) if parts else '')


def _is_teaching_class(class_name):
    """本模块内轻量判断：数字教学班（01班~99班）。与 grades.utils.is_teaching_class 同口径，
    此处就地实现以避免 utils → modules 的早期导入依赖。"""
    import re as _re
    return bool(class_name and _re.match(r'^\d{1,2}班$', str(class_name).strip()))


def student_location_desc(student, extra_class_type=None, extra_direction=None,
                          extra_selection=None):
    """由 Student 对象构造位置描述：`2024级01班·强基班·物理·物化生`

    选科优先取学生本人 subject_selection；为空时回落读该班登记的选科组合。
    """
    grade = getattr(student, 'grade', '') or ''
    cls = getattr(student, 'class_name', '') or ''
    ct = extra_class_type if extra_class_type is not None else get_class_type(grade, cls)
    own_sel = (getattr(student, 'subject_selection', '') or '').strip()
    sel = extra_selection if extra_selection is not None else own_sel
    if not sel:
        sel = get_class_selection(grade, cls)
    dr = extra_direction
    if dr is None:
        dr = resolve_direction(own_sel, cls, grade)
    return location_desc(grade, cls, ct, dr, sel)


def student_change_logs(student_id, limit=200):
    """v1.18.9.2 取单个学生的全部变迁记录（时间倒序）

    学生详情页与编辑页共用，异常时返回 []。
    """
    from sqlalchemy import text
    from app.extensions import db
    try:
        engine = db.engines.get('history')
        if engine is None:
            return []
        sql = text('SELECT id, student_id, student_number, student_name, change_type, '
                   'old_value, new_value, detail, operator, changed_at '
                   'FROM student_change_log WHERE student_id=:sid '
                   'ORDER BY changed_at DESC, id DESC LIMIT :lim')
        with engine.connect() as conn:
            return [dict(r._mapping) for r in
                    conn.execute(sql, {'sid': int(student_id),
                                       'lim': int(limit)}).fetchall()]
    except Exception:
        return []


def class_change_counts(student_ids):
    """v1.18.9.2 学生列表用：批量取学生 → 调班次数

    只统计班级类变更（enroll/reassign/transfer/correct/direction/
    subject_selection/withdraw/class）。
    返回 {student_id: 次数}；异常时返回空 dict（静默失败，不阻塞列表页）。
    """
    ids = [int(i) for i in (student_ids or []) if i]
    if not ids:
        return {}
    from sqlalchemy import text
    from app.extensions import db
    try:
        engine = db.engines.get('history')
        if engine is None:
            return {}
        ph = ','.join(f':p{i}' for i in range(len(ids)))
        params = {f'p{i}': v for i, v in enumerate(ids)}
        ctypes = ','.join(f"'{t}'" for t in CLASS_CHANGE_TYPES)
        sql = text(f'SELECT student_id, COUNT(*) AS n FROM student_change_log '
                   f'WHERE student_id IN ({ph}) AND change_type IN ({ctypes}) '
                   f'GROUP BY student_id')
        with engine.connect() as conn:
            return {row[0]: row[1] for row in conn.execute(sql, params).fetchall()}
    except Exception:
        return {}


def class_change_logs(student_ids=None, change_types=None, grade='', class_name='',
                      date_from='', date_to='', limit=500):
    """v1.18.9.2 调班记录查询（总览页用）

    student_ids: 限定学生范围（权限过滤用）；None=不限
    change_types: 类型列表；None=全部班级类
    返回 list[dict]，按时间倒序。异常时返回 []。

    注：student_ids 可能上千（全校范围），因此分块查询后合并，
    避开 SQLite 的 SQLITE_MAX_VARIABLE_NUMBER 上限。
    """
    from sqlalchemy import text
    from app.extensions import db
    types = list(change_types or CLASS_CHANGE_TYPES)
    type_sql = ','.join("'" + t + "'" for t in types)

    extra_conds = []
    extra_params = {}
    if grade:
        extra_conds.append('new_value LIKE :g')
        extra_params['g'] = f'{grade}%'
    if class_name:
        extra_conds.append('(new_value LIKE :c OR old_value LIKE :c)')
        extra_params['c'] = f'%{class_name}%'
    if date_from:
        extra_conds.append('changed_at >= :df')
        extra_params['df'] = date_from
    if date_to:
        extra_conds.append('changed_at <= :dt')
        extra_params['dt'] = date_to + ' 23:59:59'
    tail = (' AND ' + ' AND '.join(extra_conds)) if extra_conds else ''
    order = f' ORDER BY changed_at DESC, id DESC LIMIT {int(limit)}'
    base_where = f'change_type IN ({type_sql})' + tail
    select_cols = ('SELECT id, student_id, student_number, student_name, change_type, '
                   'old_value, new_value, detail, operator, changed_at '
                   'FROM student_change_log WHERE ')

    try:
        engine = db.engines.get('history')
        if engine is None:
            return []
        if student_ids is None:
            sql = text(select_cols + base_where + order)
            with engine.connect() as conn:
                return [dict(r._mapping) for r in conn.execute(sql, extra_params).fetchall()]

        ids = [int(i) for i in student_ids if i]
        if not ids:
            return []
        out = []
        CHUNK = 800
        with engine.connect() as conn:
            for i in range(0, len(ids), CHUNK):
                chunk = ids[i:i + CHUNK]
                ph = ','.join(f':s{k}' for k in range(len(chunk)))
                params = dict(extra_params)
                params.update({f's{k}': v for k, v in enumerate(chunk)})
                sql = text(select_cols + f'student_id IN ({ph}) AND ' + base_where + order)
                out.extend(dict(r._mapping) for r in conn.execute(sql, params).fetchall())
        out.sort(key=lambda r: (str(r.get('changed_at') or ''), r.get('id') or 0), reverse=True)
        return out[:int(limit)]
    except Exception:
        return []



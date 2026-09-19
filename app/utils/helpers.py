# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
import re
from app.models import DictCategory, Student, Room, ClassProfile, ClassSubject
from app.utils.cache import cache
from flask import request


def log_operation(user, action, target_type, target_id=None, detail=None, module='system', severity='INFO'):
    """记录操作审计日志（静默失败，不阻塞主流程）"""
    try:
        from app.models.operation_log import OperationLog
        from app.extensions import db
        log = OperationLog(
            user_id=user.id if user else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=str(detail)[:2000] if detail else None,
            ip_address=request.remote_addr if request else None,
            module=module,
            severity=severity,
        )
        db.session.add(log)
        db.session.commit()
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


def write_change_log(change_type, students_data, old_value='', new_value='', detail='', operator_name=''):
    """写入学生变迁日志到 history.db（复用 history bind 的连接池，不再每次新建连接）
    students_data: list of dicts with keys id, student_number, name
    函数签名与调用方式与旧版完全一致；表已在 create_app 启动时建好，
    若表缺失（如绕过启动初始化直接调脚本）则自动补建一次后重试。
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



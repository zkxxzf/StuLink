# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
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
                floor_num = int(value.replace('楼', ''))
                if Room.query.filter_by(floor=floor_num).first():
                    return True
            except (ValueError, AttributeError):
                pass
        elif category_code == 'boarding_type':
            if Student.query.filter_by(boarding_type=value).first():
                return True
        elif category_code == 'enrollment_status':
            if Student.query.filter_by(enrollment_status=value).first():
                return True
        elif category_code == 'day_student_type':
            if Student.query.filter_by(day_student_type=value).first():
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
    """返回已毕业年级列表"""
    try:
        from app.models.grade_setting import GradeSetting
        return [gs.grade for gs in GradeSetting.query.filter_by(is_graduated=True).all()]
    except Exception:
        return []


def write_change_log(change_type, students_data, old_value='', new_value='', detail='', operator_name=''):
    """写入学生变迁日志到 history.db
    students_data: list of dicts with keys id, student_number, name
    """
    import sqlite3
    import os
    from config import BASE_DIR
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    try:
        conn = sqlite3.connect(history_db_path)
        conn.execute('''
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
        ''')
        conn.commit()
        for s in students_data:
            conn.execute(
                'INSERT INTO student_change_log (student_id,student_number,student_name,change_type,old_value,new_value,detail,operator) VALUES (?,?,?,?,?,?,?,?)',
                (s['id'], s.get('student_number', ''), s.get('name', ''),
                 change_type, old_value, new_value, detail, operator_name)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'写入变迁日志异常: {e}')



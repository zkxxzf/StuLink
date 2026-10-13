"""年级管理：毕业/存档/历史查询"""
import os
import shutil
import sqlite3
import logging
from datetime import datetime
from flask import (Blueprint, render_template, request, jsonify, flash, redirect,
                   url_for, abort)
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Room, BedAssignment, Student, GradeSetting, DictCategory, DictItem
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation, write_change_log, get_dict_values, clear_graduated_grades_cache
from app.utils.alumni_scope import filter_alumni_grades, assert_alumni_grade_visible
from app.utils.crypto import decrypt

# 2026-10-11 修复：本文件 4 处静默异常降级日志误用裸 logger（未定义，触发即 NameError），在此补定义
logger = logging.getLogger(__name__)


def _prune_backups(backups_dir, keep=20, max_age_days=180):
    """M-6：毕业备份保留策略。

    此前 `data/backups/` 只增不清理，明文整库副本（含口令哈希、身份证密文）
    会长期累积并随同步盘扩散。

    v1.19.47.29 S-5：仅清理自动生成的、名字严格匹配
    `(graduate|system|history)_<年级>_YYYYMMDD_HHMMSS.db` 的文件，
    不误删手工改名的历史备份（如 `_local_before_repro/` 、`academic.db.bak-*` 等）。
    """
    import re
    import time
    pat = re.compile(r'^(?:graduate|system|history)_[^/\\]+_\d{8}_\d{6}\.db$')
    try:
        files = [os.path.join(backups_dir, f) for f in os.listdir(backups_dir)
                 if pat.match(f)]
    except Exception:  # noqa: BLE001
        return 0
    files = [(p, os.path.getmtime(p)) for p in files if os.path.isfile(p)]
    files.sort(key=lambda x: x[1], reverse=True)
    removed = 0
    cutoff = time.time() - max_age_days * 86400
    for p, mtime in files[keep:]:
        try:
            os.remove(p)
            removed += 1
        except Exception as _e:  # noqa: BLE001
            logger.warning('grade_mgmt.py::_prune_backups 异常（已降级处理）: %s', _e)
    for p, mtime in files[:keep]:
        if mtime < cutoff:
            try:
                os.remove(p)
                removed += 1
            except Exception as _e:  # noqa: BLE001
                logger.warning('grade_mgmt.py::_prune_backups 异常（已降级处理）: %s', _e)
    return removed


def _history_watermark(conn, table):
    """记录历史库某表的 rowid 水位（用于失败补偿时只删本次新增的行）"""
    try:
        return conn.execute(f'SELECT IFNULL(MAX(rowid), 0) FROM {table}').fetchone()[0]
    except Exception:  # noqa: BLE001  表还不存在时视为 0
        return 0


def _undo_history_archive(grade, wm_students=None, wm_rooms=None, wm_beds=None):
    """H-2 补偿：删掉本次毕业**刚写入**的历史归档行（按 rowid 水位）。

    2026-10-13 全面审计 H-2：历史库用裸 sqlite3 独立提交，主库那笔 commit 若失败，
    会出现"历史库有归档、主库没毕业"的半成品，用户重试就会重复归档。这里按水位
    精确回退本次新增的行（该年级历史里既有的行不受影响）。
    """
    from config import BASE_DIR
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    pairs = (('graduated_students', wm_students),
             ('graduated_rooms', wm_rooms),
             ('graduated_beds', wm_beds))
    try:
        conn = sqlite3.connect(history_db_path)
        removed = 0
        for table, wm in pairs:
            if not wm:
                continue
            cur = conn.execute(
                f'DELETE FROM {table} WHERE rowid > ? AND graduated_grade = ?',
                (wm, grade))
            removed += cur.rowcount or 0
        conn.commit()
        conn.close()
        logger.warning('%s 毕业失败，已回退本次历史归档 %d 行', grade, removed)
    except Exception as _e:  # noqa: BLE001
        logger.error('%s 历史归档补偿失败（需人工核对 history.db）: %s',
                     grade, _e, exc_info=True)


def _mask_id_card(id_card):
    """身份证号脱敏：显示前6位和后4位，中间用*替换"""
    if not id_card:
        return ''
    id_card = str(id_card).strip()
    if len(id_card) != 18:
        return id_card
    return id_card[:6] + '**********' + id_card[-4:]

bp = Blueprint('grade_mgmt', __name__, url_prefix='/grade-mgmt')


def _get_all_grades():
    """从字典表获取所有年级（与字典表联动）"""
    return get_dict_values('grade')


@bp.route('/')
@login_required
@perm_required('system.grade_mgmt')
def index():
    """年级管理页面"""
    settings = {}
    all_grades = _get_all_grades()
    # 按年份降序排列（新年级在前）
    all_grades.sort(key=lambda g: int(''.join(filter(str.isdigit, g)) or '0'), reverse=True)
    for grade in all_grades:
        gs = GradeSetting.query.filter_by(grade=grade).first()
        settings[grade] = {
            'is_graduated': gs.is_graduated if gs else False,
        }
    
    return render_template('system/grade_mgmt/index.html', settings=settings)


@bp.route('/graduate', methods=['POST'])
@login_required
@perm_required('system.grade_mgmt')
def graduate():
    """设置年级毕业"""
    data = request.get_json() or {}
    grade = data.get('grade', '')
    
    if grade not in _get_all_grades():
        return jsonify({'success': False, 'message': '无效的年级'}), 400
    
    gs = GradeSetting.query.filter_by(grade=grade).first()
    if not gs:
        gs = GradeSetting(grade=grade)
        db.session.add(gs)
    
    if gs.is_graduated:
        return jsonify({'success': False, 'message': f'{grade} 已经毕业'}), 400
    
    # 1. 备份数据库
    from config import BASE_DIR
    db_path = os.path.join(BASE_DIR, 'data', 'dormitory.db')
    system_db_path = os.path.join(BASE_DIR, 'data', 'system.db')
    backups_dir = os.path.join(BASE_DIR, 'data', 'backups')
    os.makedirs(backups_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_name = f'graduate_{grade}_{timestamp}.db'
    backup_path = os.path.join(backups_dir, backup_name)
    shutil.copy2(db_path, backup_path)
    # 同时备份系统库和历史库
    shutil.copy2(system_db_path, os.path.join(backups_dir, f'system_{grade}_{timestamp}.db'))
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')
    if os.path.exists(history_db_path):
        history_backup_name = f'history_{grade}_{timestamp}.db'
        history_backup_path = os.path.join(backups_dir, history_backup_name)
        shutil.copy2(history_db_path, history_backup_path)
    # M-6：备份保留策略（此前只增不清理，明文整库副本会长期累积并随同步盘扩散）。
    # 只清理本函数产生的 *.db 备份，保留最近 N 份；不动其它文件。
    _prune_backups(backups_dir, keep=20)
    
    # 1.5 归档学生数据到历史库
    # 2026-10-13 H-2：先记归档前水位，主库落库失败时据此精确回退本次写入（见 _undo_history_archive）
    _wm_students = _wm_rooms = _wm_beds = None
    try:
        hist_conn = sqlite3.connect(history_db_path)
        hist_conn.execute('''
            CREATE TABLE IF NOT EXISTS graduated_students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER,
                student_number TEXT,
                name TEXT NOT NULL,
                gender TEXT,
                ethnicity TEXT,
                grade TEXT,
                class_name TEXT,
                subject_selection TEXT,
                boarding_type TEXT,
                day_student_type TEXT,
                enrollment_status TEXT,
                textbook TEXT,
                teacher_notes TEXT,
                enrollment_notes TEXT,
                graduation_school_code TEXT,
                graduation_school TEXT,
                phone1 TEXT,
                phone2 TEXT,
                id_card_number TEXT,
                graduated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                graduated_grade TEXT
            )
        ''')
        hist_conn.commit()
        _wm_students = _history_watermark(hist_conn, 'graduated_students')
        sys_conn = sqlite3.connect(system_db_path)
        # 2026-10-12 审计 P0-1：boarding_type/day_student_type/textbook/teacher_notes
        # 已从 students 表迁至 dormitory 库 student_accommodation（cleanup_student_columns
        # 删列），原 SELECT 仍引用这 4 列 → 必抛 no such column → 被裸 except 吞掉 →
        # student_count=0 而流程继续清空在校数据并标记毕业（数据丢失链）。
        # 裸 sqlite3 无法跨库 JOIN，ATTACH 后 LEFT JOIN；无住宿记录时四列为 NULL。
        if not os.path.exists(db_path):
            raise RuntimeError('dormitory.db 不存在，无法关联住宿信息，拒绝继续归档')
        sys_conn.execute('ATTACH DATABASE ? AS dorm', (db_path,))
        students = sys_conn.execute(
            '''SELECT s.id,s.student_number,s.name,s.gender,s.ethnicity,s.grade,
                      s.class_name,s.subject_selection,a.boarding_type,
                      a.day_student_type,s.enrollment_status,a.textbook,a.teacher_notes,
                      s.enrollment_notes,s.graduation_school_code,s.graduation_school,
                      s.phone1,s.phone2,s.id_card_number
                 FROM students s
                 LEFT JOIN dorm.student_accommodation a ON a.student_id = s.id
                WHERE s.grade=?''',
            (grade,)
        ).fetchall()
        if not students:
            # 空归档同样是事故信号（年级无学生/年级名不匹配），一律硬中止
            raise RuntimeError(f'students 表中没有 {grade} 的记录，拒绝空归档')
        for s in students:
            hist_conn.execute(
                'INSERT INTO graduated_students (original_id,student_number,name,gender,ethnicity,grade,class_name,subject_selection,boarding_type,day_student_type,enrollment_status,textbook,teacher_notes,enrollment_notes,graduation_school_code,graduation_school,phone1,phone2,id_card_number,graduated_grade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (*s, grade)
            )
        hist_conn.commit()
        sys_conn.close()
        hist_conn.close()
        student_count = len(students)
    except Exception as e:
        # 2026-10-12 审计 P0-1：归档失败必须硬中止。原实现裸 except 记日志后继续，
        # 后续仍会清空房间/床位并把 is_graduated 置真 → "归档 0 人 + 在校数据已清"。
        logger.error(f'归档学生数据失败，毕业已中止（{grade}）: {e}', exc_info=True)
        for _c in ('hist_conn', 'sys_conn'):
            try:
                locals()[_c].close()
            except Exception:
                pass
        db.session.rollback()
        return jsonify({'success': False,
                        'message': f'归档学生数据失败，{grade} 毕业已中止。'
                                   f'请检查服务端日志后重试；在校数据未做任何修改。'}), 500
    
    # 1.6 归档宿舍数据到历史库
    room_archived = 0
    bed_archived = 0
    try:
        hist_conn = sqlite3.connect(history_db_path)
        # 建表
        hist_conn.execute('''
            CREATE TABLE IF NOT EXISTS graduated_rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_room_id INTEGER,
                building TEXT,
                room_number TEXT,
                gender TEXT,
                floor INTEGER,
                capacity INTEGER,
                grade TEXT,
                class_name TEXT,
                combined_class TEXT,
                combined_details TEXT,
                notes TEXT,
                graduated_grade TEXT,
                graduated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # 兼容旧表：确保 combined_details 列存在
        room_cols = [row[1] for row in hist_conn.execute('PRAGMA table_info(graduated_rooms)').fetchall()]
        if 'combined_details' not in room_cols:
            hist_conn.execute('ALTER TABLE graduated_rooms ADD COLUMN combined_details TEXT')
        hist_conn.execute('''
            CREATE TABLE IF NOT EXISTS graduated_beds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_bed_id INTEGER,
                original_room_id INTEGER,
                bed_number INTEGER,
                student_id INTEGER,
                student_number TEXT,
                student_name TEXT,
                student_gender TEXT,
                student_grade TEXT,
                student_class TEXT,
                graduated_grade TEXT,
                graduated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        hist_conn.commit()
        _wm_rooms = _history_watermark(hist_conn, 'graduated_rooms')
        _wm_beds = _history_watermark(hist_conn, 'graduated_beds')

        # 归档房间
        dorm_conn = sqlite3.connect(db_path)
        rooms = dorm_conn.execute(
            'SELECT id,building,room_number,gender,floor,capacity,grade,class_name,combined_class,combined_details,notes FROM rooms WHERE grade=?',
            (grade,)
        ).fetchall()
        for r in rooms:
            hist_conn.execute(
                'INSERT INTO graduated_rooms (original_room_id,building,room_number,gender,floor,capacity,grade,class_name,combined_class,combined_details,notes,graduated_grade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (*r, grade)
            )
        room_archived = len(rooms)

        # 归档床位（该年级所有有学生的床位）
        if rooms:
            room_ids = [r[0] for r in rooms]
            placeholders = ','.join('?' * len(room_ids))
            beds = dorm_conn.execute(
                f'SELECT id,room_id,bed_number,student_id FROM bed_assignments WHERE room_id IN ({placeholders}) AND student_id IS NOT NULL',
                room_ids
            ).fetchall()
            if beds:
                # 从 system.db 查学生信息
                sys_conn = sqlite3.connect(system_db_path)
                student_ids = [b[3] for b in beds]
                s_placeholders = ','.join('?' * len(student_ids))
                stu_rows = sys_conn.execute(
                    f'SELECT id,student_number,name,gender,grade,class_name FROM students WHERE id IN ({s_placeholders})',
                    student_ids
                ).fetchall()
                stu_map = {s[0]: s for s in stu_rows}
                for b in beds:
                    stu = stu_map.get(b[3])
                    hist_conn.execute(
                        'INSERT INTO graduated_beds (original_bed_id,original_room_id,bed_number,student_id,student_number,student_name,student_gender,student_grade,student_class,graduated_grade) VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (b[0], b[1], b[2], b[3],
                         stu[1] if stu else '', stu[2] if stu else '',
                         stu[3] if stu else '', stu[4] if stu else '', stu[5] if stu else '',
                         grade)
                    )
                bed_archived = len(beds)
                sys_conn.close()

        dorm_conn.close()
        hist_conn.commit()
        hist_conn.close()
    except Exception as e:
        # 2026-10-12 审计 P0-1：宿舍归档失败同样硬中止——原实现记日志后继续，
        # 后续仍会清空床位分配并标记毕业 → 往届住宿查询永久缺该年级数据。
        logger.error(f'归档宿舍数据失败，毕业已中止（{grade}）: {e}', exc_info=True)
        for _c in ('hist_conn', 'dorm_conn', 'sys_conn'):
            _obj = locals().get(_c)
            if _obj is not None:
                try:
                    _obj.close()
                except Exception:
                    pass
        db.session.rollback()
        return jsonify({'success': False,
                        'message': f'归档宿舍数据失败，{grade} 毕业已中止。'
                                   f'请检查服务端日志后重试；在校数据未做任何修改。'}), 500

    # 1.7 写入毕业变迁日志
    try:
        grad_students = Student.query.filter(Student.grade == grade).all()
        students_data = [{'id': s.id, 'student_number': s.student_number or '', 'name': s.name} for s in grad_students]
        if students_data:
            write_change_log('graduate', students_data, old_value='在校', new_value='毕业',
                           detail=f'{grade} 毕业归档', operator_name=current_user.real_name)
    except Exception as e:
        logger.error(f'毕业变迁日志异常: {e}', exc_info=True)

    # 2. 获取该年级所有房间ID（清空前查）
    rooms = Room.query.filter(Room.grade == grade).all()
    room_ids = [r.id for r in rooms]
    
    # 3. 清空房间分配
    rooms_updated = len(rooms)
    for r in rooms:
        r.grade = None
        r.class_name = None
        r.combined_class = None
    
    # 4. 清空床位（分两步：先从system.db查学生ID，再更新dormitory.db）
    student_ids = [s.id for s in Student.query.filter(Student.grade == grade).all()]
    beds_cleared = 0
    if student_ids:
        beds_cleared = BedAssignment.query.filter(
            BedAssignment.student_id.in_(student_ids)
        ).update({'student_id': None, 'assigned_by': None, 'assigned_at': None}, synchronize_session=False)
    
    # 4. 标记毕业
    gs.is_graduated = True
    gs.graduated_at = datetime.now()
    gs.backup_path = backup_path
    gs.graduated_by = current_user.id
    try:
        db.session.commit()
    except Exception as _e:  # noqa: BLE001
        # 2026-10-13 全面审计 H-2：此处 commit 跨 system + dormitory 两个库，
        # 失败时主库回滚，但历史库是先独立提交的 —— 会留下"历史库已归档、主库没毕业"
        # 的半成品，用户重试即重复归档。这里按水位把本次历史写入撤掉，让重试是干净的。
        db.session.rollback()
        logger.error('%s 毕业：主库落库失败，已回滚并撤销本次历史归档: %s',
                     grade, _e, exc_info=True)
        _undo_history_archive(grade, _wm_students, _wm_rooms, _wm_beds)
        return jsonify({'success': False,
                        'message': f'{grade} 毕业未完成：数据写入失败，已回滚'
                                   f'（本次历史归档已撤销，可直接重试）。'
                                   f'详情见服务端日志。'}), 500

    # v1.16.0：年级毕业状态变更，主动失效已毕业年级缓存（避免统计/搜索页 600s 内用到陈旧名单）
    clear_graduated_grades_cache()

    log_operation(current_user, '毕业', '年级', None, f'{grade} 毕业，归档{student_count}名学生、{room_archived}间房、{bed_archived}个床位，清空{rooms_updated}间房、{beds_cleared}个床位')

    return jsonify({
        'success': True,
        'message': f'{grade} 已毕业！归档{student_count}名学生、{room_archived}间房、{bed_archived}个床位',
        'backup': backup_name
    })


# ==================== 历史查询 ====================


@bp.route('/history')
@login_required
@perm_required('system.grade_mgmt')
def history():
    """基本信息历史查询"""
    from config import BASE_DIR
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')

    graduated_grades = []
    if os.path.exists(history_db_path):
        try:
            conn = sqlite3.connect(history_db_path)
            grades = conn.execute(
                'SELECT DISTINCT graduated_grade FROM graduated_students ORDER BY graduated_grade'
            ).fetchall()
            graduated_grades = [g[0] for g in grades if g[0]]
            conn.close()
        except Exception as _e:  # noqa: BLE001
            logger.warning('grade_mgmt.py::history 异常（已降级处理）: %s', _e)
    # 2026-10-12 审计 P1：历史查询与 /alumni 同口径收敛（往届=已毕业年级）
    graduated_grades = filter_alumni_grades(current_user, graduated_grades)

    search_grade = request.args.get('grade', '')
    search_name = request.args.get('name', '').strip()
    search_student_number = request.args.get('student_number', '').strip()
    search_school_code = request.args.get('school_code', '').strip()

    results = []
    if search_grade and os.path.exists(history_db_path):
        try:
            assert_alumni_grade_visible(current_user, search_grade)
        except PermissionError:
            abort(403)
        try:
            conn = sqlite3.connect(history_db_path)
            conn.row_factory = sqlite3.Row
            where_clauses = ['graduated_grade = ?']
            params = [search_grade]
            if search_name:
                where_clauses.append('name LIKE ?')
                params.append(f'%{search_name}%')
            if search_student_number:
                where_clauses.append('student_number LIKE ?')
                params.append(f'%{search_student_number}%')
            if search_school_code:
                where_clauses.append('graduation_school_code LIKE ?')
                params.append(f'%{search_school_code}%')
            sql = f'SELECT * FROM graduated_students WHERE {" AND ".join(where_clauses)} ORDER BY class_name, name LIMIT 200'
            rows = conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if r.get('id_card_number'):
                    # M-5：解密失败不再把密文当明文展示，降级为空串
                    try:
                        r['id_card_decrypted'] = decrypt(r['id_card_number'])
                    except Exception:
                        r['id_card_decrypted'] = ''
                    r['id_card_masked'] = _mask_id_card(r['id_card_decrypted'])
                else:
                    r['id_card_decrypted'] = ''
                    r['id_card_masked'] = ''
                results.append(r)
            conn.close()
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

    return render_template('system/grade_mgmt/history.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_name=search_name,
                           search_student_number=search_student_number,
                           search_school_code=search_school_code,
                           results=results)


@bp.route('/alumni')
@login_required
@perm_required('system.grade_mgmt')
def alumni():
    """往届生基本信息查询"""
    from config import BASE_DIR
    history_db_path = os.path.join(BASE_DIR, 'data', 'history.db')

    graduated_grades = []
    if os.path.exists(history_db_path):
        try:
            conn = sqlite3.connect(history_db_path)
            grades = conn.execute(
                'SELECT DISTINCT graduated_grade FROM graduated_students ORDER BY graduated_grade'
            ).fetchall()
            graduated_grades = [g[0] for g in grades if g[0]]
            conn.close()
        except Exception as _e:  # noqa: BLE001
            logger.warning('grade_mgmt.py::alumni 异常（已降级处理）: %s', _e)
    # 2026-10-12 审计 P1：往届生档案查询收敛数据范围（原先零校验，H-2 复活）
    graduated_grades = filter_alumni_grades(current_user, graduated_grades)

    search_grade = request.args.get('grade', '')
    search_name = request.args.get('name', '').strip()
    search_student_number = request.args.get('student_number', '').strip()
    search_school_code = request.args.get('school_code', '').strip()

    results = []
    change_logs = {}
    if search_grade and os.path.exists(history_db_path):
        try:
            assert_alumni_grade_visible(current_user, search_grade)
        except PermissionError:
            abort(403)
        try:
            conn = sqlite3.connect(history_db_path)
            conn.row_factory = sqlite3.Row
            # 确保表存在
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
            # 查询学生
            where_clauses = ['graduated_grade = ?']
            params = [search_grade]
            if search_name:
                where_clauses.append('name LIKE ?')
                params.append(f'%{search_name}%')
            if search_student_number:
                where_clauses.append('student_number LIKE ?')
                params.append(f'%{search_student_number}%')
            if search_school_code:
                where_clauses.append('graduation_school_code LIKE ?')
                params.append(f'%{search_school_code}%')
            sql = f'SELECT * FROM graduated_students WHERE {" AND ".join(where_clauses)} ORDER BY class_name, name LIMIT 200'
            rows = conn.execute(sql, params).fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if r.get('id_card_number'):
                    # M-5：解密失败不再把密文当明文展示，降级为空串
                    try:
                        r['id_card_decrypted'] = decrypt(r['id_card_number'])
                    except Exception:
                        r['id_card_decrypted'] = ''
                    r['id_card_masked'] = _mask_id_card(r['id_card_decrypted'])
                else:
                    r['id_card_decrypted'] = ''
                    r['id_card_masked'] = ''
                results.append(r)

            # 查变迁日志
            if results:
                student_ids = [r['original_id'] for r in results if r.get('original_id')]
                if student_ids:
                    ph = ','.join('?' * len(student_ids))
                    logs = conn.execute(
                        f'SELECT * FROM student_change_log WHERE student_id IN ({ph}) ORDER BY student_id, changed_at',
                        student_ids
                    ).fetchall()
                    for log in logs:
                        sid = log['student_id']
                        if sid not in change_logs:
                            change_logs[sid] = []
                        change_logs[sid].append(dict(log))

            conn.close()
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

    return render_template('system/grade_mgmt/alumni.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_name=search_name,
                           search_student_number=search_student_number,
                           search_school_code=search_school_code,
                           results=results,
                           change_logs=change_logs)

"""往届生基本信息查询"""
import logging
import sqlite3
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import check_password_hash
from config import Config
from app.auth import AlumniUser, is_alumni_role_allowed
from app.utils.crypto import decrypt, mask_id_card

bp = Blueprint('basic', __name__)
_log = logging.getLogger('alumni.basic')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        try:
            conn = sqlite3.connect(f'file:{Config.SYSTEM_DB}?mode=ro', uri=True)
            row = conn.execute(
                'SELECT id, username, password_hash, real_name, role FROM users WHERE username=? AND is_active=1',
                (username,)
            ).fetchone()
            conn.close()
            if row and check_password_hash(row[2], password):
                role = row[4]
                # H-7：只验口令的时代结束——必须校验角色白名单，
                # 否则任一在职主站账号（宿管/任课教师）都能登录本站读往届生数据。
                if not is_alumni_role_allowed(role):
                    _log.warning('alumni 登录被拒：角色不在白名单（username=%s role=%s）',
                                 username, role)
                    return '无权访问往届生查询系统（当前账号角色不在允许范围内）', 403
                user = AlumniUser(row[0], row[1], row[3], role)
                login_user(user)
                return redirect(url_for('basic.index'))
        except Exception:
            # L-5：认证异常不得静默吞没
            _log.exception('alumni 登录查询异常 username=%s', username)
        flash('用户名或密码错误', 'danger')
    return render_template('login.html')


@bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('basic.login'))


@bp.route('/')
@login_required
def index():
    graduated_grades = []
    try:
        conn = sqlite3.connect(f'file:{Config.HISTORY_DB}?mode=ro', uri=True)
        grades = conn.execute(
            'SELECT DISTINCT graduated_grade FROM graduated_students ORDER BY graduated_grade'
        ).fetchall()
        graduated_grades = [g[0] for g in grades if g[0]]
        conn.close()
    except Exception:
        pass

    search_grade = request.args.get('grade', '')
    search_name = request.args.get('name', '').strip()
    search_student_number = request.args.get('student_number', '').strip()
    search_school_code = request.args.get('school_code', '').strip()
    search_class = request.args.get('class_name', '').strip()

    # 获取班级列表
    class_list = []
    if search_grade:
        try:
            conn = sqlite3.connect(f'file:{Config.HISTORY_DB}?mode=ro', uri=True)
            classes = conn.execute(
                'SELECT DISTINCT class_name FROM graduated_students WHERE graduated_grade=? ORDER BY class_name',
                (search_grade,)
            ).fetchall()
            class_list = [c[0] for c in classes if c[0]]
            conn.close()
        except Exception:
            pass

    results = []
    change_logs = {}
    if search_grade:
        try:
            conn = sqlite3.connect(f'file:{Config.HISTORY_DB}?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row

            where_clauses = ['graduated_grade = ?']
            params = [search_grade]
            if search_class:
                where_clauses.append('class_name = ?')
                params.append(search_class)
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
                # H-7：解密出的完整身份证号不再放入模板上下文（模板只渲染掩码），
                # 避免任何一次模板改动/调试输出把明文身份证带进响应。
                if r.get('id_card_number'):
                    try:
                        r['id_card_masked'] = mask_id_card(decrypt(r['id_card_number']))
                    except Exception:
                        _log.exception('alumni 身份证解密失败 student_id=%s',
                                       r.get('original_id'))
                        r['id_card_masked'] = ''
                else:
                    r['id_card_masked'] = ''
                r.pop('id_card_number', None)
                results.append(r)

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
        except Exception:
            # M-12 同类要求：内部异常文本（含路径/SQL）不回传前端，仅服务端记录
            _log.exception('alumni 往届生查询异常')
            flash('查询失败，请联系管理员', 'danger')

    return render_template('basic_search.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_class=search_class,
                           class_list=class_list,
                           search_name=search_name,
                           search_student_number=search_student_number,
                           search_school_code=search_school_code,
                           results=results,
                           change_logs=change_logs)

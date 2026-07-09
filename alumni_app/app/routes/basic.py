"""往届生基本信息查询"""
import sqlite3
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import check_password_hash
from config import Config
from app.auth import AlumniUser
from app.utils.crypto import decrypt, mask_id_card

bp = Blueprint('basic', __name__)


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
                user = AlumniUser(row[0], row[1], row[3], row[4])
                login_user(user)
                return redirect(url_for('basic.index'))
        except Exception:
            pass
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
                if r.get('id_card_number'):
                    r['id_card_decrypted'] = decrypt(r['id_card_number'])
                    r['id_card_masked'] = mask_id_card(r['id_card_decrypted'])
                else:
                    r['id_card_decrypted'] = ''
                    r['id_card_masked'] = ''
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
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

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

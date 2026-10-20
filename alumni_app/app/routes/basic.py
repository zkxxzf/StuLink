"""往届生基本信息查询"""
import logging
import sqlite3
from flask import (Blueprint, abort, render_template, request, flash, redirect,
                   url_for)
from flask_login import login_required, login_user, logout_user, current_user
from werkzeug.security import check_password_hash
from config import Config
from app.auth import AlumniUser, is_alumni_role_allowed
from app.scope import filter_grades, grade_visible
from app import throttle
from app.utils.crypto import decrypt, mask_id_card, mask_phone

bp = Blueprint('basic', __name__)
_log = logging.getLogger('alumni.basic')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        # 2026-10-19 第十三轮审计：往届站原先零限流，可在线爆破主站账号口令
        if throttle.is_locked(request.remote_addr, username):
            flash('尝试次数过多，请 5 分钟后再试', 'danger')
            return render_template('login.html'), 429
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
                    # 2026-10-19：口令正确但角色不允许时，不再返回可区分文案
                    # （原 403 明文可判定凭据有效 + 枚举角色）
                    _log.warning('alumni 登录被拒：角色不在白名单（username=%s role=%s）',
                                 username, role)
                    throttle.register_failure(request.remote_addr, username)
                    flash('用户名或密码错误', 'danger')
                    return render_template('login.html')
                throttle.clear(request.remote_addr, username)
                user = AlumniUser(row[0], row[1], row[3], role)
                login_user(user)
                return redirect(url_for('basic.index'))
        except Exception:
            # L-5：认证异常不得静默吞没
            _log.exception('alumni 登录查询异常 username=%s', username)
        throttle.register_failure(request.remote_addr, username)
        flash('用户名或密码错误', 'danger')
    return render_template('login.html')


@bp.route('/logout')
@login_required   # 2026-10-14 审计 API-11：与主站 auth.logout 对齐，未登录不得触发登出流程
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
    # 2026-10-19 第十三轮审计：按用户可见年级收敛（与主站 alumni_scope 同口径，
    # 年级长只到本人当前年级、班主任/任课教师为空集）
    graduated_grades = filter_grades(current_user, graduated_grades)

    search_grade = request.args.get('grade', '')
    if search_grade and not grade_visible(current_user, search_grade):
        abort(403)
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
                # 2026-10-19：手机号与身份证同口径 —— 只把**掩码**放进模板上下文，
                # 明文一律 pop，避免任何一次模板改动把完整号码带进响应。
                # （此前 phone1/phone2 是明文直出，是校友站唯一的明文泄露点）
                for _k in ('phone1', 'phone2'):
                    if r.get(_k):
                        try:
                            r[_k + '_masked'] = mask_phone(decrypt(r[_k]))
                        except Exception:
                            _log.exception('alumni 手机号解密失败 student_id=%s',
                                           r.get('original_id'))
                            r[_k + '_masked'] = ''
                    else:
                        r[_k + '_masked'] = ''
                    r.pop(_k, None)
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

"""往届生宿舍查询"""
import logging
import sqlite3
from flask import Blueprint, abort, render_template, request, flash
from flask_login import login_required, current_user
from config import Config
from app.scope import filter_grades, grade_visible

_log = logging.getLogger('alumni.dormitory')

bp = Blueprint('dormitory', __name__)


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
    # 2026-10-19 第十三轮审计：与 basic.index 同口径（此前宿舍往届页同样零范围）
    graduated_grades = filter_grades(current_user, graduated_grades)

    search_grade = request.args.get('grade', '')
    if search_grade and not grade_visible(current_user, search_grade):
        abort(403)
    search_room = request.args.get('room_number', '').strip()
    search_building = request.args.get('building', '').strip()
    search_class = request.args.get('class_name', '').strip()

    class_list = []
    # 从 graduated_rooms 获取班级列表
    if search_grade:
        try:
            conn = sqlite3.connect(f'file:{Config.HISTORY_DB}?mode=ro', uri=True)
            classes = conn.execute(
                'SELECT DISTINCT class_name FROM graduated_rooms WHERE graduated_grade=? AND class_name IS NOT NULL AND class_name!="" ORDER BY class_name',
                (search_grade,)
            ).fetchall()
            class_list = [c[0] for c in classes if c[0]]
            conn.close()
        except Exception:
            pass

    rooms = []
    beds = []
    if search_grade:
        try:
            conn = sqlite3.connect(f'file:{Config.HISTORY_DB}?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row

            room_where = ['graduated_grade = ?']
            room_params = [search_grade]
            if search_room:
                room_where.append('room_number LIKE ?')
                room_params.append(f'%{search_room}%')
            if search_building:
                room_where.append('building LIKE ?')
                room_params.append(f'%{search_building}%')
            if search_class:
                room_where.append('class_name = ?')
                room_params.append(search_class)

            room_sql = f'SELECT * FROM graduated_rooms WHERE {" AND ".join(room_where)} ORDER BY building, room_number LIMIT 200'
            room_rows = conn.execute(room_sql, room_params).fetchall()
            rooms = [dict(r) for r in room_rows]

            room_ids = [r['original_room_id'] for r in rooms if r['original_room_id']]
            if room_ids:
                ph = ','.join('?' * len(room_ids))
                bed_rows = conn.execute(
                    f'SELECT * FROM graduated_beds WHERE original_room_id IN ({ph}) ORDER BY original_room_id, bed_number',
                    room_ids
                ).fetchall()
                beds = [dict(b) for b in bed_rows]

            conn.close()
        except Exception:
            # L-02 同类：内部异常文本（含路径/SQL）不回传前端
            _log.exception('往届宿舍查询异常')
            flash('查询失败，请稍后重试', 'danger')

    return render_template('dormitory_search.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_room=search_room,
                           search_building=search_building,
                           search_class=search_class,
                           class_list=class_list,
                           rooms=rooms,
                           beds=beds)

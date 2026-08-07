"""往届生宿舍查询"""
import sqlite3
from flask import Blueprint, render_template, request, flash
from flask_login import login_required
from config import Config

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

    search_grade = request.args.get('grade', '')
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
        except Exception as e:
            flash(f'查询失败：{str(e)}', 'danger')

    return render_template('dormitory_search.html',
                           graduated_grades=graduated_grades,
                           search_grade=search_grade,
                           search_room=search_room,
                           search_building=search_building,
                           search_class=search_class,
                           class_list=class_list,
                           rooms=rooms,
                           beds=beds)

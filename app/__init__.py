# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import logging
import os
import re as _re
import secrets
import sqlite3 as _sqlite3

from flask import (Flask, render_template, request, url_for, abort, redirect,
                   session, flash)
from sqlalchemy import event as _sa_event
from sqlalchemy.engine import Engine as _SAEngine

from config import Config
from app.extensions import db, login_manager, csrf
from app.utils.permission_map import default_keys as _module_keys
from app.utils.upload_guard import is_static_uploads_path
from markupsafe import escape, Markup
import gzip


# ---- SQLite 连接级 PRAGMA（对主库与全部 bind 库的每个新连接生效） ----
# synchronous=NORMAL：写入吞吐明显提升，配合非 WAL 仍是安全档位（断电最多丢末尾事务）
# busy_timeout=5000：多线程写入冲突时等待 5s 而非立即报 database is locked
# foreign_keys=ON：开启前已对全部 8 库执行 PRAGMA foreign_key_check（2026-09-18，
#   0 孤儿引用）；跨库引用本就不设 FK，风险仅在库内，新增脏数据会被及时拦截
# WAL：默认关闭。取舍与开启方法见 config.py 的 SQLITE_ENABLE_WAL 注释
#   （data 目录在同步盘，WAL 伴生文件有被同步工具损坏的风险）
@_sa_event.listens_for(_SAEngine, 'connect')
def _set_sqlite_pragma(dbapi_conn, _connection_record):
    if not isinstance(dbapi_conn, _sqlite3.Connection):
        return  # 非 SQLite 连接直接跳过
    cur = dbapi_conn.cursor()
    try:
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.execute('PRAGMA foreign_keys=ON')
        if getattr(Config, 'SQLITE_ENABLE_WAL', False):
            cur.execute('PRAGMA journal_mode=WAL')
    finally:
        cur.close()

DICT_DATA = {
    'grade': ('年级', ['2025级', '2024级', '2023级']),
    'class': ('班级', ['01班', '02班', '03班', '04班', '05班', '06班', '07班',
                       '08班', '09班', '10班', '不分班', '已转出', '离校']),
    'gender': ('性别', ['男', '女']),
    # 选科组合（3+1+2 共 12 种）：物理方向 6 种 + 历史方向 6 种，按此顺序展示
    'subject': ('选科', ['物化生', '物化地', '物化政', '物生地', '物生政', '物地政',
                         '史化生', '史化地', '史化政', '史生地', '史生政', '史地政']),
    'boarding_type': ('走读/住校', ['住校', '走读', '离校']),
    'subject_direction': ('选科方向', ['物理', '历史']),
    'class_type': ('班型', ['强基班', '卓越班']),
    'exam_type': ('考试类型', ['月考', '期中', '期末', '联考', '开学考', '模拟', '其他']),
    'exam_subject': ('考试科目', ['语文', '数学', '外语', '物理', '历史',
                                 '化学', '生物', '政治', '地理']),
    'day_student_type': ('走读类型', ['晚走读', '午晚走读']),
    'bed_number': ('床号', ['1床', '2床', '3床', '4床', '5床', '6床', '7床', '8床']),
    'textbook': ('课本', ['领', '未领']),
    'enrollment_status': ('学籍情况', ['在籍不在校', '借读', '借读又走了', '转入',
                                       '借读后学籍转入', '复学', '学籍已转出']),
    'building': ('宿舍楼', ['西宿舍楼', '东宿舍楼']),
    'floor': ('楼层', ['1 层', '2 层', '3 层', '4 层', '5 层', '6 层']),
    'ethnicity': ('民族', [
        '汉族', '蒙古族', '回族', '藏族', '维吾尔族', '苗族', '彝族', '壮族',
        '布依族', '朝鲜族', '满族', '侗族', '瑶族', '白族', '土家族', '哈尼族',
        '哈萨克族', '傣族', '黎族', '傈僳族', '佤族', '畲族', '高山族', '拉祜族',
        '水族', '东乡族', '纳西族', '景颇族', '柯尔克孜族', '土族', '达斡尔族',
        '仫佬族', '羌族', '布朗族', '撒拉族', '毛南族', '仡佬族', '锡伯族',
        '阿昌族', '普米族', '塔吉克族', '怒族', '乌孜别克族', '俄罗斯族',
        '鄂温克族', '德昂族', '保安族', '裕固族', '京族', '塔塔尔族', '独龙族',
        '鄂伦春族', '赫哲族', '门巴族', '珞巴族', '基诺族',
    ]),
}

ROOM_NUMBERS = [
    '男201', '男202', '男203', '男204', '男205', '男206', '男207', '男208',
    '男209', '男210', '男211', '男212', '男213', '男214', '男215', '男216',
    '男217', '男218', '男219', '男221', '男222', '男223',
    '男301', '男302', '男303', '男304', '男305', '男306', '男307', '男308',
    '男309', '男310', '男311', '男312', '男313', '男314', '男315', '男316',
    '男317', '男318', '男319', '男321', '男322', '男323',
    '男401', '男402', '男403', '男404', '男405', '男406', '男407', '男408',
    '男409', '男410', '男411', '男412', '男413', '男414', '男415', '男416',
    '男417', '男418', '男419', '男421', '男422', '男423',
    '男501', '男502', '男503', '男504', '男505', '男506', '男508', '男509',
    '男510', '男511', '男512', '男513', '男514', '男515', '男516', '男517',
    '男518', '男519', '男521', '男522', '男523',
    '男602', '男603', '男604', '男605', '男606', '男607', '男608', '男609',
    '男610', '男611', '男612', '男613', '男614', '男615', '男616', '男617',
    '男618', '男619', '男621', '男622', '男623',
    '女201', '女202', '女203', '女204', '女205', '女206', '女207', '女208',
    '女209', '女210', '女211', '女212', '女213', '女214', '女215', '女216',
    '女217', '女218', '女219', '女221', '女222', '女223',
    '女302', '女303', '女304', '女305', '女306', '女307', '女308', '女309',
    '女310', '女311', '女312', '女313', '女314', '女315', '女319', '女321',
    '女322', '女323',
    '女401', '女402', '女403', '女404', '女405', '女406', '女407', '女408',
    '女409', '女410', '女411', '女412', '女413', '女414', '女415', '女416',
    '女417', '女418', '女419', '女421', '女422', '女423',
    '女503', '女504', '女505', '女506', '女507', '女508', '女509', '女510',
    '女511', '女512', '女513', '女514', '女515', '女516', '女517', '女518',
    '女519', '女521', '女522', '女523',
    '女601', '女602', '女603', '女604', '女605', '女606', '女607', '女608',
    '女609', '女610', '女611', '女612', '女613', '女614', '女615', '女616',
    '女617', '女618', '女619', '女621', '女622', '女623',
]

PERMISSION_GROUPS = [
    {
        'name': '管理员组',
        'role': 'admin',
        'scope_type': 'school',
        'description': '系统管理员，拥有全部权限',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export', 'students.export_id_card', 'students.export_phone',
            'students.export_graduation_school', 'students.export_enrollment',
            'students.transfer',
            'dormitory.view', 'dormitory.manage', 'dormitory.assign',
            'dormitory.beds', 'dormitory.import',
            'statistics.view',
            'system.users', 'system.dictionary', 'system.class_profile',
            'system.perm_groups', 'system.grade_mgmt', 'system.settings',
            'points.view', 'points.edit',
            'points.import', 'points.export', 'points.rules',
            'grades.view', 'grades.edit',
            'grades.import', 'grades.settings', 'grades.teachers', 'grades.student_query',
            # v1.9.2 合并：master 新增选科维护页权限 + first 学术/画像/工作台权限（取并集）
            'grades.subject_mgmt',
            'academic.view', 'academic.timetable', 'academic.swap',
            'academic.inspection_export', 'academic.forms', 'academic.forms_view',
            'portrait.view', 'portrait.edit',
            'workbench.records', 'workbench.class_view',
            'workbench.attendance_view', 'workbench.attendance',
            'workbench.notifications_view', 'workbench.notifications',
        ],
    },
    {
        'name': '年级长组',
        'role': 'grade_leader',
        'scope_type': 'grade',
        'description': '年级长，管理本年级学生数据和统计',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export', 'students.export_id_card', 'students.export_phone',
            'students.export_graduation_school', 'students.export_enrollment',
            'students.transfer',
            'dormitory.view', 'dormitory.beds',
            'statistics.view',
            'points.view', 'grades.view',
            'grades.edit', 'grades.import', 'grades.settings', 'grades.student_query',
            # v1.9.2 合并：master 新增选科维护页权限 + first 画像/学术/工作台权限（取并集）
            'grades.subject_mgmt',
            'portrait.view', 'portrait.edit',
            'points.import', 'points.rules',
            'academic.view', 'academic.forms_view',
            # v1.17.0：年级长按年级只读工作台（班级概览/考勤查看）
            'workbench.class_view', 'workbench.attendance_view',
            'workbench.notifications_view',
        ],
    },
    {
        'name': '班主任组',
        'role': 'homeroom_teacher',
        'scope_type': 'class',
        'description': '班主任，管理本班学生和床位',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export', 'students.export_id_card', 'students.export_phone',
            'students.export_graduation_school', 'students.export_enrollment',
            'students.transfer',
            'dormitory.view', 'dormitory.beds',
            'statistics.view',
            'points.view', 'grades.view', 'grades.student_query',
            'portrait.view', 'portrait.edit',
            'points.import',
            'academic.view', 'academic.swap', 'academic.forms_view',
            # v1.17.0：写权限必须配套只读权限，否则「有考勤录入权却打不开考勤页」
            'workbench.records', 'workbench.class_view',
            'workbench.attendance_view', 'workbench.attendance',
            'workbench.notifications',
        ],
    },
    {
        'name': '宿管组',
        'role': 'dorm_manager',
        'scope_type': 'school',
        'description': '宿管教师，管理全校宿舍分配和床位',
        'menu_keys': [
            'students.view',
            'dormitory.view', 'dormitory.manage', 'dormitory.assign',
            'dormitory.beds', 'dormitory.import',
            'statistics.view',
        ],
    },
    {
        'name': '任课教师组',
        'role': 'teacher',
        'scope_type': 'class',
        'description': '任课教师，查看所教班级学生',
        'menu_keys': [
            'students.view',
            'points.view', 'points.edit',
            'points.import',
            'grades.view', 'grades.student_query',
            'academic.view', 'academic.swap', 'academic.forms_view',
            'workbench.class_view',
        ],
    },
    # ===== v1.9.2 新增身份（身份不写死，可在权限页随时新增/调整） =====
    {
        'name': '校长',
        'role': 'staff',
        'scope_type': 'school',
        'description': '校长：全校数据只读（如需限定年级，可在数据范围表勾选）',
        'menu_keys': _module_keys(
            ('students', 'read'), ('dormitory', 'read'), ('grades', 'read'),
            ('points', 'read'), ('academic', 'read')),
    },
    {
        'name': '副校长',
        'role': 'staff',
        'scope_type': 'school',
        'description': '副校长：全校数据只读（如需限定年级，可在数据范围表勾选）',
        'menu_keys': _module_keys(
            ('students', 'read'), ('dormitory', 'read'), ('grades', 'read'),
            ('points', 'read'), ('academic', 'read')),
    },
    {
        'name': '教务主任',
        'role': 'staff',
        'scope_type': 'school',
        'description': '教务主任：学生/成绩/教务写入，其余只读（全校）',
        'menu_keys': _module_keys(
            ('students', 'write'), ('dormitory', 'read'), ('grades', 'write'),
            ('points', 'read'), ('academic', 'write')),
    },
    {
        'name': '教务员',
        'role': 'staff',
        'scope_type': 'none',
        'description': '教务员：成绩/教务写入，学生/积分只读（数据范围按用户勾选，如仅 2025 级）',
        'menu_keys': _module_keys(
            ('students', 'read'), ('dormitory', 'read'), ('grades', 'write'),
            ('points', 'read'), ('academic', 'write')),
    },
    {
        'name': '备课组长',
        'role': 'staff',
        'scope_type': 'none',
        'description': '备课组长：成绩写入，其余只读（数据范围按用户勾选，如本年级）',
        'menu_keys': _module_keys(
            ('students', 'read'), ('dormitory', 'read'), ('grades', 'write'),
            ('points', 'read'), ('academic', 'read')),
    },
    {
        'name': '教研组长',
        'role': 'staff',
        'scope_type': 'none',
        'description': '教研组长：成绩写入，其余只读（数据范围按用户勾选，如本年级）',
        'menu_keys': _module_keys(
            ('students', 'read'), ('dormitory', 'read'), ('grades', 'write'),
            ('points', 'read'), ('academic', 'read')),
    },
    {
        'name': '学生发展中心',
        'role': 'staff',
        'scope_type': 'none',
        'description': '学生发展中心：积分/画像写入 + 学生只读（数据范围按用户勾选）',
        'menu_keys': _module_keys(
            ('students', 'read'), ('points', 'write'), ('portrait', 'write')),
    },
]

# v1.17.0（PR#5 审查 M1）：通知收件箱是「看自己的通知」，属所有登录身份的基础能力，
# 与数据范围无关，故所有身份默认具备 workbench.notifications_view（只读），
# 避免出现「铃铛点进去 403」。发布/删除另需写权限 workbench.notifications 或 system.settings。
for _g in PERMISSION_GROUPS:
    if 'workbench.notifications_view' not in _g['menu_keys']:
        _g['menu_keys'].append('workbench.notifications_view')


def _init_system_data():
    """初始化系统基础数据（字典、权限组、宿舍房间）"""
    from app.models import User, DictCategory, DictItem, Room, BedAssignment, PermissionGroup
    
    seeded = False
    
    if not DictCategory.query.first():
        for code, (name, values) in DICT_DATA.items():
            cat = DictCategory(code=code, name=name)
            db.session.add(cat)
            db.session.flush()
            for i, val in enumerate(values):
                db.session.add(DictItem(category_id=cat.id, value=val, sort_order=i))
        print(f'[INIT] 字典数据已初始化 ({len(DICT_DATA)} 个分类)')
        seeded = True
    
    if not PermissionGroup.query.first():
        for p in PERMISSION_GROUPS:
            g = PermissionGroup(
                name=p['name'],
                role=p['role'],
                scope_type=p['scope_type'],
                description=p['description'],
            )
            g.set_menu_keys(p['menu_keys'])
            db.session.add(g)
        print(f'[INIT] 权限组已初始化 ({len(PERMISSION_GROUPS)} 个)')
        seeded = True
        
        admin = User.query.filter_by(username='admin').first()
        if admin and not admin.permission_group_id:
            admin_group = PermissionGroup.query.filter_by(name='管理员组').first()
            if admin_group:
                admin.permission_group_id = admin_group.id
    
    if not Room.query.first():
        created_rooms = 0
        for raw_number in ROOM_NUMBERS:
            gender = '男' if raw_number.startswith('男') else '女'
            building = '西宿舍楼' if raw_number.startswith('男') else '东宿舍楼'
            room_number = raw_number[1:]
            floor_num = int(room_number[0])
            room = Room(building=building, room_number=room_number, gender=gender,
                        floor=floor_num, capacity=8, is_active=True)
            db.session.add(room)
            db.session.flush()
            for bed_num in range(1, 9):
                db.session.add(BedAssignment(room_id=room.id, bed_number=bed_num))
            created_rooms += 1
        print(f'[INIT] 宿舍房间已初始化 ({created_rooms} 间)')
        seeded = True
    
    if seeded:
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    app.jinja_env.auto_reload = app.config.get('TEMPLATES_AUTO_RELOAD', False)
    # 模板全局函数：选科方向判断与配色
    from app.utils.helpers import subject_direction, subject_badge_class, normalize_subject
    app.jinja_env.globals.update(
        subject_direction=subject_direction,
        subject_badge_class=subject_badge_class,
        normalize_subject=normalize_subject,
    )

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import User, Student, Room, BedAssignment, DictCategory, DictItem
    from app.models import OperationLog, PermissionGroup, ClassProfile, GradeSetting, UserClassLink, AssignmentHistory
    from app.models.grades import (Exam, ExamScore, ExamBand, BandTemplate,
                                   TeacherSubjectLink, AiKey,
                                   AiGlobalKey, AiReport, AiChatMessage, Certificate)
    from app.models.academic import (Teacher, Timetable, TimetableEntry,
                                     InspectionRecord, TeacherAchievement,
                                     FormCategory, FormTemplate, FormQuestion,
                                     FormSubmission, FormAnswer)
    from app.models.portrait import StudentPortrait, PortraitComment, PortraitEvent  # noqa: F401
    from app.models.timetable import (TermSchedule, PeriodDef, ScheduleEntry,  # noqa: F401
                                      ScheduleSwap, ScheduleVersion)
    with app.app_context():
        # v1.15.0 模块故障隔离：逐库建表，单个模块库异常不阻塞系统启动。
        # system 为根基库最先建；其他模块库失败仅告警（对应模块暂不可用），
        # 系统管理与基础数据不受影响。
        for _bind in (None, 'dormitory', 'history', 'grades', 'points',
                      'academic', 'portrait', 'system', 'timetable'):
            try:
                db.create_all(bind_key=_bind)
            except Exception as _e:  # noqa: BLE001
                print(f'[WARN] 数据库 {_bind or "system"} 初始化失败（该模块暂不可用）：{_e}')
        
        # H-1：内置 admin 不再使用硬编码默认口令。
        # 首启：生成随机口令，仅在控制台打印一次，并置 must_change_pwd=True 强制首登改密。
        # 存量：若 admin 仍是 admin123（旧部署升级而来），同样置为强制改密并打印显著告警。
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            _initial_pwd = secrets.token_urlsafe(12)
            admin = User(username='admin', real_name='系统管理员', role='admin',
                         must_change_pwd=True)
            admin.set_password(_initial_pwd)
            db.session.add(admin)
            db.session.commit()
            print('=' * 70)
            print('[安全] 已创建内置管理员账号：admin')
            print(f'[安全] 初始随机口令（仅此一次显示，请妥善保存）：{_initial_pwd}')
            print('[安全] 首次登录后必须修改密码')
            print('=' * 70)
        elif admin.check_password('admin123'):
            admin.must_change_pwd = True
            db.session.commit()
            print('=' * 70)
            print('[安全警告] 内置 admin 仍在使用默认口令 admin123！')
            print('[安全警告] 已置为「首次登录强制改密」，请立即登录并修改密码。')
            print('=' * 70)
        
        _init_system_data()

        # 启动时建好 history.db 变迁日志表：write_change_log 不再每次新建连接建表
        from app.utils.helpers import init_history_tables
        init_history_tables()

    # 安全过滤器：先转义 HTML 再将 \n 转为 <br>（替代危险的 |safe）
    @app.template_filter('nl2br')
    def nl2br_filter(text):
        return Markup(escape(str(text)).replace('\n', '<br>'))

    # JSON 字符串安全解析（表单题目选项渲染用）
    import json as _json
    @app.template_filter('load_json_safe')
    def load_json_safe_filter(text):
        if not text:
            return []
        try:
            return _json.loads(text)
        except Exception:
            return []

    # 静态资源版本号：按「单个文件」的修改时间生成 ?v=，模板里用 {{ su('js/x.js') }}。
    # 改动某文件后浏览器自动拉新，根治「改了代码但浏览器用旧缓存」导致的
    # 汇报区持续 loading、下拉失效等问题（无需手动 Ctrl+F5）。
    # v1.13.2：由「全局最大 mtime」改为「按文件 mtime」——避免任意小改动把
    # echarts(1MB)/bootstrap 等大文件一起挤掉缓存；配合下方 immutable 强缓存，
    # 未变更的资源二次访问零请求。
    import os as _os

    # v1.16.1 su() 缓存优化：启动时扫描 static 目录一次，把 mtime 存进 dict，
    # 避免每次请求每个资源都调 os.path.getmtime（高并发下 syscall 开销明显）。
    # dev 模式下可通过 ?_su_refresh=1 触发重新扫描（仅对当前请求生效一次）。
    _mtime_cache = {}

    def _build_mtime_cache():
        """遍历 static 目录，把每个文件的 mtime 存入 _mtime_cache。"""
        base = app.static_folder
        if not base or not _os.path.isdir(base):
            return
        for dirpath, _dirs, files in _os.walk(base):
            for fn in files:
                full = _os.path.join(dirpath, fn)
                rel = _os.path.relpath(full, base).replace(_os.sep, '/')
                try:
                    _mtime_cache[rel] = int(_os.path.getmtime(full))
                except OSError:
                    _mtime_cache[rel] = 0

    with app.app_context():
        _build_mtime_cache()

    _app_logger = logging.getLogger('stulink.app')

    # R-10（纵深防御）：静态资源名白名单。`_asset_mtime` 会用 filename 拼磁盘路径，
    # 一旦将来改为接收请求参数，就可能变成「任意文件信息探测」。这里显式校验：
    # 必须是相对路径、不含 ..、不含反斜杠与盘符，且只含安全字符。
    _ASSET_NAME_RE = _re.compile(r'^[A-Za-z0-9_\-./]+$')

    def _is_safe_asset_name(filename):
        if not filename or '..' in filename or '\\' in filename:
            return False
        if filename.startswith('/') or ':' in filename or '\x00' in filename:
            return False
        return bool(_ASSET_NAME_RE.match(filename))

    def _asset_mtime(filename):
        if not _is_safe_asset_name(filename):
            _app_logger.warning('[R-10] 拒绝非法的静态资源名：%r', filename)
            return 0
        if request.args.get('_su_refresh'):
            p = _os.path.join(app.static_folder, filename.replace('/', _os.sep))
            try:
                v = int(_os.path.getmtime(p))
                _mtime_cache[filename] = v
                return v
            except OSError:
                return 0
        return _mtime_cache.get(filename, 0)

    @app.context_processor
    def inject_asset_version():
        def su(filename):
            return url_for('static', filename=filename, v=_asset_mtime(filename))
        return {'su': su}

    # 静态资源强缓存：URL 带 ?v=（内容变更即换 URL）→ 一年 immutable，浏览器零回源；
    # 不带 ?v= 的（如 CSS 内相对路径引用的字体）保持默认协商缓存，避免改文件读旧版。
    @app.after_request
    def cache_static_immutable(response):
        if (response.status_code == 200
                and request.path.startswith('/static/')
                and request.args.get('v')):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response

    # Gzip 压缩响应（提升传输速度）
    @app.after_request
    def compress_response(response):
        accept_encoding = request.headers.get('Accept-Encoding', '')
        if 'gzip' not in accept_encoding.lower():
            return response
        
        if (response.status_code < 200 or response.status_code >= 300 or 
            'Content-Encoding' in response.headers or
            response.content_length is None or 
            response.content_length < 500):
            return response
        
        # 只压缩文本类型
        content_type = response.headers.get('Content-Type', '').lower()
        if not any(t in content_type for t in ['text/', 'application/json', 'application/javascript']):
            return response
        
        try:
            compressed_data = gzip.compress(response.data)
            response.data = compressed_data
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = len(compressed_data)
        except Exception:
            pass  # 压缩失败则返回原始数据
        
        return response

    # 上传附件目录禁止通过 /static/... 直链访问（PR#5 安全审查 M4）。
    # 历史实现把用户上传的表单材料落在 app/static/uploads 下，任何拿到
    # /static/uploads/forms/<id>/<sid>/<file> 的人（含未登录）都能抓取学生材料。
    # 现在应用内部生成的下载链接一律走带鉴权的 academic.form_file 路由，
    # 这里再把 /static/uploads/ 直链整体封掉，杜绝「猜路径」式越权读取。
    # TODO(后续)：uploads 目录仍在 Flask 静态目录下，最彻底的做法是迁移到
    # instance/uploads 等静态目录之外，并同步迁移 DB 里的 FormAnswer.file_path。
    @app.before_request
    def _block_static_uploads():
        # H-8：原实现 `request.path.startswith('/static/uploads')` 大小写敏感，
        # 而 Windows 文件系统大小写不敏感 + Werkzeug 静态前缀大小写敏感 +
        # <path:filename> 原样透传 → `/static/Uploads/...` 可绕过守卫直取学生材料。
        # 改用统一判定（normcase + 反斜杠/多斜杠归一）。
        if is_static_uploads_path(request.path):
            abort(404)

    # M-2：会话有效性。口令摘要不匹配（改密/重置）或账号被禁用 → 立即登出并清会话。
    # 在此之前「改密不停旧会话、禁用不踢会话」，权限撤销形同虚设。
    @app.before_request
    def _enforce_session_validity():
        from flask_login import current_user, logout_user
        from app.utils.session_guard import is_valid
        if not current_user.is_authenticated:
            return
        if is_valid(current_user):
            return
        logout_user()
        session.clear()
        flash('登录状态已失效，请重新登录', 'warning')
        return redirect(url_for('auth.login'))

    # H-1：强制改密。must_change_pwd 字段此前「只写不读」（全库 15 处命中全为写入方），
    # 导致新导入教师「手机号即密码」可长期直接登录。这里补上唯一的读取方：
    # 除改密、登出、静态资源外一律 302 到改密页。
    @app.before_request
    def _force_password_change():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        try:
            must_change = bool(current_user.must_change_pwd)
        except Exception:  # noqa: BLE001  # 会话用户对象异常时不阻断请求
            return
        if not must_change:
            return
        if request.endpoint in {'auth.change_password', 'auth.logout', 'static'}:
            return
        if request.path.startswith('/static/'):
            return
        return redirect(url_for('auth.change_password'))

    # CSRF 错误友好提示（Edge 等浏览器 cookie 策略较严时可能触发）
    @app.errorhandler(400)
    def bad_request(e):
        return render_template('error.html',
                               code=400,
                               message='请求无效，请刷新页面后重试。如果问题持续，请清除浏览器缓存/Cookie后再试。'), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('error.html', code=403, message='您没有权限访问此页面'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('error.html', code=404, message='页面不存在'), 404

    @app.errorhandler(500)
    def internal_error(e):
        return render_template('error.html', code=500, message='服务器内部错误，请联系管理员'), 500

    # M-9：为后续切换到 enforcing CSP 预留 nonce（每请求一个，模板可用 {{ csp_nonce }}）
    @app.before_request
    def _assign_csp_nonce():
        request.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _inject_csp_nonce():
        return {'csp_nonce': getattr(request, 'csp_nonce', '')}

    # 安全响应头
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        # M-9：X-XSS-Protection 已被现代浏览器废弃（且旧实现本身可被利用），移除
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

        # M-9：CSP。项目内联 <script>/onclick 数量大，直接 enforcing 会白屏，
        # 因此默认 Report-Only（只上报不拦截），收集一轮违规后再切换：
        #   STULINK_CSP_MODE=enforce → 直接下发 CSP（script-src 带 nonce、允许内联样式）
        #   STULINK_CSP_MODE=off     → 不下发
        mode = os.environ.get('STULINK_CSP_MODE', 'report').strip().lower()
        if mode != 'off':
            nonce = getattr(request, 'csp_nonce', '') or ''
            script_src = "'self'"
            if nonce:
                script_src += f" 'nonce-{nonce}'"
            policy = ("default-src 'self'; "
                      f"script-src {script_src} 'unsafe-inline'; "
                      "style-src 'self' 'unsafe-inline'; "
                      "img-src 'self' data:; "
                      "font-src 'self' data:; "
                      "connect-src 'self'; "
                      "frame-ancestors 'none'; "
                      "object-src 'none'; "
                      "base-uri 'self'")
            header = ('Content-Security-Policy' if mode == 'enforce'
                      else 'Content-Security-Policy-Report-Only')
            response.headers[header] = policy
        return response

    # 注册蓝图（模块化架构）
    from app.modules import register_blueprints
    register_blueprints(app)

    return app



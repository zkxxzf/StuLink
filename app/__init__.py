# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Flask, render_template, request
from config import Config
from app.extensions import db, login_manager, csrf
from markupsafe import escape, Markup
import gzip

DICT_DATA = {
    'grade': ('年级', ['2025级', '2024级', '2023级']),
    'class': ('班级', ['01班', '02班', '03班', '04班', '05班', '06班', '07班',
                       '08班', '09班', '10班', '未分班', '已转出', '离校']),
    'gender': ('性别', ['男', '女']),
    'subject': ('选科', ['物化生', '物化政', '物化地', '物生政', '物生地', '物政地',
                         '史政地', '史生地', '史生政', '史化生', '史化政', '史化地']),
    'boarding_type': ('走读/住校', ['住校', '走读', '离校']),
    'subject_direction': ('选科方向', ['物理', '历史']),
    'class_type': ('班型', ['强基班', '卓越班']),
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
            'students.export', 'students.transfer',
            'dormitory.view', 'dormitory.manage', 'dormitory.assign',
            'dormitory.beds', 'dormitory.import',
            'statistics.view',
            'system.users', 'system.dictionary', 'system.class_profile',
            'system.perm_groups', 'system.grade_mgmt',
            'points.view', 'points.edit',
            'grades.view', 'grades.edit',
        ],
    },
    {
        'name': '年级长组',
        'role': 'grade_leader',
        'scope_type': 'grade',
        'description': '年级长，管理本年级学生数据和统计',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export', 'students.transfer',
            'dormitory.view', 'dormitory.beds',
            'statistics.view',
            'points.view', 'grades.view',
        ],
    },
    {
        'name': '班主任组',
        'role': 'homeroom_teacher',
        'scope_type': 'class',
        'description': '班主任，管理本班学生和床位',
        'menu_keys': [
            'students.view', 'students.edit', 'students.import',
            'students.export', 'students.transfer',
            'dormitory.view', 'dormitory.beds',
            'statistics.view',
            'points.view', 'grades.view',
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
            'grades.view', 'grades.edit',
        ],
    },
]


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
    
    app.jinja_env.auto_reload = True

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import User, Student, Room, BedAssignment, DictCategory, DictItem
    from app.models import OperationLog, PermissionGroup, ClassProfile, GradeSetting, UserClassLink, AssignmentHistory
    with app.app_context():
        db.create_all()
        
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', real_name='系统管理员', role='admin', must_change_pwd=False)
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
        
        _init_system_data()

    # 安全过滤器：先转义 HTML 再将 \n 转为 <br>（替代危险的 |safe）
    @app.template_filter('nl2br')
    def nl2br_filter(text):
        return Markup(escape(str(text)).replace('\n', '<br>'))

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

    # 安全响应头
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response

    # 注册蓝图（模块化架构）
    from app.modules import register_blueprints
    register_blueprints(app)

    return app



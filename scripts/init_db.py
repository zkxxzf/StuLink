"""初始化数据库：建表 + 创建默认管理员 + 导入字典数据"""
import os
import sys

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import User, DictCategory, DictItem, Room, BedAssignment

app = create_app()

# 字典数据定义
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

# 宿舍房间列表（从Excel字典表提取）
ROOM_NUMBERS = [
    # 西宿舍楼 2楼
    '男201', '男202', '男203', '男204', '男205', '男206', '男207', '男208',
    '男209', '男210', '男211', '男212', '男213', '男214', '男215', '男216',
    '男217', '男218', '男219', '男221', '男222', '男223',
    # 西宿舍楼 3楼
    '男301', '男302', '男303', '男304', '男305', '男306', '男307', '男308',
    '男309', '男310', '男311', '男312', '男313', '男314', '男315', '男316',
    '男317', '男318', '男319', '男321', '男322', '男323',
    # 西宿舍楼 4楼
    '男401', '男402', '男403', '男404', '男405', '男406', '男407', '男408',
    '男409', '男410', '男411', '男412', '男413', '男414', '男415', '男416',
    '男417', '男418', '男419', '男421', '男422', '男423',
    # 西宿舍楼 5楼
    '男501', '男502', '男503', '男504', '男505', '男506', '男508', '男509',
    '男510', '男511', '男512', '男513', '男514', '男515', '男516', '男517',
    '男518', '男519', '男521', '男522', '男523',
    # 西宿舍楼 6楼
    '男602', '男603', '男604', '男605', '男606', '男607', '男608', '男609',
    '男610', '男611', '男612', '男613', '男614', '男615', '男616', '男617',
    '男618', '男619', '男621', '男622', '男623',
    # 东宿舍楼 2楼
    '女201', '女202', '女203', '女204', '女205', '女206', '女207', '女208',
    '女209', '女210', '女211', '女212', '女213', '女214', '女215', '女216',
    '女217', '女218', '女219', '女221', '女222', '女223',
    # 东宿舍楼 3楼
    '女302', '女303', '女304', '女305', '女306', '女307', '女308', '女309',
    '女310', '女311', '女312', '女313', '女314', '女315', '女319', '女321',
    '女322', '女323',
    # 东宿舍楼 4楼
    '女401', '女402', '女403', '女404', '女405', '女406', '女407', '女408',
    '女409', '女410', '女411', '女412', '女413', '女414', '女415', '女416',
    '女417', '女418', '女419', '女421', '女422', '女423',
    # 东宿舍楼 5楼
    '女503', '女504', '女505', '女506', '女507', '女508', '女509', '女510',
    '女511', '女512', '女513', '女514', '女515', '女516', '女517', '女518',
    '女519', '女521', '女522', '女523',
    # 东宿舍楼 6楼
    '女601', '女602', '女603', '女604', '女605', '女606', '女607', '女608',
    '女609', '女610', '女611', '女612', '女613', '女614', '女615', '女616',
    '女617', '女618', '女619', '女621', '女622', '女623',
]


def init_database():
    with app.app_context():
        # 建表
        db.create_all()
        print('数据库表已创建')

        # 创建默认管理员
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                real_name='系统管理员',
                role='admin',
                must_change_pwd=False,
            )
# ⚠️ 生产环境请修改默认密码！或者部署后立即通过系统界面修改
            admin.set_password('admin123')
            db.session.add(admin)
            print('默认管理员已创建 (用户名: admin, 密码: admin123) ⚠️ 请立即修改！')

        # 导入字典数据
        for code, (name, values) in DICT_DATA.items():
            cat = DictCategory.query.filter_by(code=code).first()
            if not cat:
                cat = DictCategory(code=code, name=name)
                db.session.add(cat)
                db.session.flush()
                for i, val in enumerate(values):
                    db.session.add(DictItem(category_id=cat.id, value=val, sort_order=i))
                print(f'字典 [{name}] 已导入 {len(values)} 项')

        # 创建宿舍房间 + 预创建床位
        created_rooms = 0
        for raw_number in ROOM_NUMBERS:
            gender = '男' if raw_number.startswith('男') else '女'
            building = '西宿舍楼' if raw_number.startswith('男') else '东宿舍楼'
            room_number = raw_number[1:]  # 去掉性别前缀：男201 -> 201
            floor_num = int(room_number[0])  # 从房间号提取楼层：201 -> 2
            if Room.query.filter_by(building=building, room_number=room_number).first():
                continue
            room = Room(building=building, room_number=room_number, gender=gender,
                        floor=floor_num, capacity=8, is_active=True)
            db.session.add(room)
            db.session.flush()
            # 预创建8个床位
            for bed_num in range(1, 9):
                db.session.add(BedAssignment(room_id=room.id, bed_number=bed_num))
            created_rooms += 1

        if created_rooms:
            print(f'已创建 {created_rooms} 间宿舍（每间8个床位）')

        db.session.commit()
        print('\n数据库初始化完成！')


if __name__ == '__main__':
    init_database()

# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0



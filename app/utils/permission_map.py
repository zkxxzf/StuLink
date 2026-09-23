# StuLink v1.18.1.0 2026-09-23
# 模块权限映射：功能权限表格（身份 × 子功能 × 三档）与细粒度 menu_keys 的转换层
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""模块权限映射（转换层）

设计说明：
- 身份（权限组）不写死：任意权限组都可在权限表格中配置各子功能等级；
- 每个大模块划分为若干「子功能」（如宿舍管理 → 查看/房间管理/宿舍分配/床位分配），
  每个子功能定义 read（只读 key 集）与 write（写入增量 key 集）：
    不可见 → 空集；只读 → read；写入 → read + write
- 子功能的 allow_read 为 False 时没有实际只读功能（如"敏感导出""床位分配"），
  UI 上仅提供 不可见/写入 两档；
- 分隔符用 ':' 生成子功能标识（如 students:view），与 menu_keys 命名区分；
- 本文件仅含常量与纯函数，不依赖 app 包。
"""

MODULES = [
    {
        'key': 'students',
        'name': '学生管理',
        'icon': 'bi-person',
        'items': [
            {'key': 'view', 'name': '查看', 'read': ['students.view'], 'write': []},
            {'key': 'edit', 'name': '编辑/调班',
             'read': [], 'write': ['students.edit', 'students.transfer']},
            {'key': 'io', 'name': '导入/导出',
             'read': [],
             'write': ['students.import', 'students.export',
                       'students.export_enrollment',
                       'students.export_graduation_school']},
            {'key': 'sensitive', 'name': '敏感导出',
             'read': [], 'write': ['students.export_id_card',
                                   'students.export_phone']},
        ],
    },
    {
        'key': 'dormitory',
        'name': '宿舍管理',
        'icon': 'bi-building',
        'items': [
            {'key': 'view', 'name': '查看/统计',
             'read': ['dormitory.view', 'statistics.view'], 'write': []},
            {'key': 'manage', 'name': '房间管理',
             'read': [], 'write': ['dormitory.manage', 'dormitory.import']},
            {'key': 'assign', 'name': '宿舍分配',
             'read': [], 'write': ['dormitory.assign']},
            {'key': 'beds', 'name': '床位分配',
             'read': [], 'write': ['dormitory.beds']},
        ],
    },
    {
        'key': 'grades',
        'name': '成绩管理',
        'icon': 'bi-book',
        'items': [
            {'key': 'view', 'name': '查看分析', 'read': ['grades.view'], 'write': []},
            {'key': 'manage', 'name': '教学管理',
             'read': [],
             'write': ['grades.edit', 'grades.import', 'grades.settings']},
            {'key': 'teachers', 'name': '任课映射',
             'read': [], 'write': ['grades.teachers']},
            {'key': 'query', 'name': '个人成绩查询',
             'read': [], 'write': ['grades.student_query']},
            {'key': 'subject', 'name': '选科维护',
             'read': [], 'write': ['grades.subject_mgmt']},
        ],
    },
    {
        'key': 'points',
        'name': '德育管理',
        'icon': 'bi-star',
        'items': [
            {'key': 'view', 'name': '查看', 'read': ['points.view'], 'write': []},
            {'key': 'edit', 'name': '录入编辑',
             'read': [], 'write': ['points.edit']},
            {'key': 'import', 'name': '批量导入',
             'read': [], 'write': ['points.import']},
            {'key': 'export', 'name': '导出',
             'read': [], 'write': ['points.export']},
            {'key': 'rules', 'name': '规则模板',
             'read': [], 'write': ['points.rules']},
        ],
    },
    {
        'key': 'academic',
        'name': '教务管理',
        'icon': 'bi-mortarboard',
        'items': [
            {'key': 'view', 'name': '查看', 'read': ['academic.view'], 'write': []},
            {'key': 'edit', 'name': '录入管理',
             'read': [], 'write': ['academic.edit']},
            {'key': 'timetable', 'name': '课表管理',
             'read': [], 'write': ['academic.timetable']},
            {'key': 'swap', 'name': '调课管理',
             'read': [], 'write': ['academic.swap']},
            {'key': 'inspection_export', 'name': '查课导出',
             'read': [], 'write': ['academic.inspection_export']},
            {'key': 'forms', 'name': '问卷收集',
             'read': ['academic.forms_view'], 'write': ['academic.forms']},
        ],
    },
    {
        'key': 'portrait',
        'name': '学生画像',
        'icon': 'bi-person-vcard',
        'items': [
            {'key': 'view', 'name': '查看', 'read': ['portrait.view'], 'write': []},
            {'key': 'edit', 'name': '评语/事件管理',
             'read': [], 'write': ['portrait.edit']},
        ],
    },
    {
        'key': 'workbench',
        'name': '班主任工作台',
        'icon': 'bi-person-workspace',
        'items': [
            {'key': 'records', 'name': '工作记录', 'read': [], 'write': ['workbench.records']},
            {'key': 'class_view', 'name': '班级概览', 'read': ['workbench.class_view'], 'write': []},
            {'key': 'attendance', 'name': '考勤管理', 'read': ['workbench.attendance_view'], 'write': ['workbench.attendance']},
            {'key': 'notifications', 'name': '通知管理', 'read': ['workbench.notifications_view'], 'write': ['workbench.notifications']},
        ],
    },
    {
        'key': 'system',
        'name': '系统管理',
        'icon': 'bi-gear',
        'items': [
            {'key': 'users', 'name': '教师管理',
             'read': [], 'write': ['system.users']},
            {'key': 'config', 'name': '基础配置',
             'read': [],
             'write': ['system.dictionary', 'system.class_profile',
                       'system.grade_mgmt']},
            {'key': 'perm', 'name': '权限管理',
             'read': [], 'write': ['system.perm_groups']},
            {'key': 'settings', 'name': '系统设置',
             'read': [], 'write': ['system.settings']},
        ],
    },
]

# 受保护身份：不允许删除/修改（防误操作）
PROTECTED_GROUP_NAMES = {'管理员组'}

LEVEL_NONE = 'none'
LEVEL_READ = 'read'
LEVEL_WRITE = 'write'
LEVEL_LABELS = {LEVEL_NONE: '不可见', LEVEL_READ: '只读', LEVEL_WRITE: '写入'}


def item_id(module_key, item_key):
    """子功能标识（表单字段名），如 students:view"""
    return f'{module_key}:{item_key}'


def item_allow_read(module_key, item_key):
    """子功能是否有实际只读档（read 非空即可展示"只读"选项）"""
    for m in MODULES:
        if m['key'] != module_key:
            continue
        for it in m['items']:
            if it['key'] == item_key:
                return bool(it.get('read'))
    return False


def item_levels_to_keys(levels):
    """{子功能标识: 等级} → menu_keys 列表"""
    keys = []
    for m in MODULES:
        for it in m['items']:
            lv = (levels or {}).get(item_id(m['key'], it['key']), LEVEL_NONE)
            if lv == LEVEL_READ and it.get('read'):
                keys.extend(it['read'])
            elif lv == LEVEL_WRITE:
                keys.extend(it['read'])
                keys.extend(it['write'])
    return keys


def keys_to_item_levels(menu_keys):
    """menu_keys 列表 → {子功能标识: 等级}（反推显示用）"""
    keys = set(menu_keys or [])
    out = {}
    for m in MODULES:
        for it in m['items']:
            iid = item_id(m['key'], it['key'])
            if keys & set(it['write']):
                out[iid] = LEVEL_WRITE
            elif keys & set(it['read']):
                out[iid] = LEVEL_READ
            else:
                out[iid] = LEVEL_NONE
    return out


def module_levels_to_item_levels(module_levels):
    """模块级档位 → 子功能档位（种子初始化便捷写法用）。

    规则：模块"只读"时仅有查看能力的子功能为只读，其余子功能不可见；
    模块"写入"时全部子功能写入；模块"不可见"时全部不可见。
    """
    out = {}
    for m in MODULES:
        ml = (module_levels or {}).get(m['key'], LEVEL_NONE)
        for it in m['items']:
            iid = item_id(m['key'], it['key'])
            if ml == LEVEL_WRITE:
                out[iid] = LEVEL_WRITE
            elif ml == LEVEL_READ:
                out[iid] = LEVEL_READ if it.get('read') else LEVEL_NONE
            else:
                out[iid] = LEVEL_NONE
    return out


def default_keys(*module_pairs):
    """按 (模块key, 等级) 序列生成 menu_keys（身份种子初始化用）"""
    return item_levels_to_keys(
        module_levels_to_item_levels(dict(module_pairs)))

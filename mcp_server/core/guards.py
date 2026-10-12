# -*- coding: utf-8 -*-
"""数据范围与功能权限守卫：复用系统既有判定，越界统一抛 PermissionError。

MCP 侧不重新实现一套权限逻辑——直接调用成绩模块 / 工作台模块 / 教务模块
已有的范围函数与 User.has_perm，保证"AI 能看到的范围"与"网页上该账号
能看到的范围"一致：
  · 数据范围：student_in_scope / class_allowed / check_exam_visible；
  · 功能权限：网页端 @perm_required(...) 对应的权限点（has_perm）；
  · 分析维度：网页端 _guard_tab 的"角色 → 分析 tab"可见矩阵；
  · 写操作额外复用网页端写入路径的范围判定（调课条目授权 / 考试年级 / 学号）。
"""
from . import bootstrap  # noqa: F401  确保项目根已进 sys.path

_ALL_TABS = {'grade', 'class', 'subject', 'teacher', 'compare'}


def perm(user, key):
    """功能权限校验：对应网页端 @perm_required(key)。

    key 支持传列表/元组：命中其中任意一个即通过。用于"多级审批"这类
    不同级对应不同权限点、但工具入口相同的场景（如调课审批：一级
    academic.swap_approve、终审 academic.timetable 共用 review_swap）。
    """
    keys = (key,) if isinstance(key, str) else tuple(key or ())
    if not keys:
        return True
    if user is None or not any(user.has_perm(k) for k in keys):
        raise PermissionError(
            f'账号缺少权限：{"、".join(keys)}'
            f'（请在权限管理中为绑定账号所在权限组勾选该权限）')
    return True


def student_scope(user, student_no):
    """校验学号在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.grades.services.scope import student_in_scope
    return student_in_scope(user, student_no)


def class_scope(user, grade, class_name):
    """校验班级在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.workbench.services.scope import assert_class_allowed
    return assert_class_allowed(user, grade, class_name)


def tab_scope(user, tab):
    """成绩分析维度校验：移植网页端 analysis.py 的 _guard_tab 可见矩阵。

    网页端口径：管理员 / 校级范围 / 用户级数据范围 / 年级长（或年级范围）可用
    全部五个分析维度；班主任仅 class；任课教师仅 teacher；其余一律拒绝。
    """
    from app.modules.grades.services import scope as scope_service
    if user.role == 'admin':
        allowed = set(_ALL_TABS)
    else:
        scope_type, _grade = scope_service.get_scope(user)
        if scope_type == 'school' or scope_service.has_user_scope(user):
            allowed = set(_ALL_TABS)
        elif user.role == 'grade_leader' or scope_type == 'grade':
            allowed = set(_ALL_TABS)
        elif user.has_role('homeroom_teacher'):
            allowed = {'class'}
        elif user.has_role('teacher'):
            allowed = {'teacher'}
        else:
            allowed = set()
    if tab not in allowed:
        raise PermissionError(f'当前账号无权访问该分析维度（{tab}）')
    return True


def visible_class_names(user, grade):
    """受限用户在该年级可见的班级名集合；None = 不限班（与 visible_classes 同源）。

    网页端可见性过滤写的是 `if limited:`（空列表会被当作"不过滤"），这里用
    `is None` 判定：空列表代表"没有任何可见班级"，放行会越权，故按空集合处理。
    """
    from app.modules.grades.services import scope as scope_service
    limited = scope_service.visible_classes(user)
    if limited is None:
        return None
    return {c for g, c in limited if g == grade}


def exam_scope(user, exam):
    """校验考试在账号可见范围内（越界抛 PermissionError）。"""
    from app.modules.grades.services.scope import check_exam_visible
    return check_exam_visible(user, exam)


def find_student(student_no):
    """按学号取学生（不存在抛 ValueError，便于工具返回明确提示）。"""
    from app.models.student import Student
    student = Student.query.filter_by(student_number=student_no).first()
    if student is None:
        raise ValueError(f'学号不存在：{student_no}')
    return student


def exam_or_raise(exam_id):
    """按 id 取考试（不存在抛 ValueError）。"""
    from app.extensions import db
    from app.models.grades import Exam
    exam = db.session.get(Exam, int(exam_id)) if exam_id else None
    if exam is None:
        raise ValueError(f'考试不存在：exam_id={exam_id}（可先用 list_exams 查询可用考试）')
    return exam


# ══════════════════════════════════════════════════════════════════════
# 写入侧守卫（2026-10-11 新增：随 MCP 写工具引入）
# 与网页端写入路由同口径，避免"网页不许写、AI 却能写"的越权缝隙。
# ══════════════════════════════════════════════════════════════════════

def swap_entry_scope(user, entry):
    """调课条目写入授权：复用网页端的 swap_entry_authorizer。

    网页端 /swap/apply 与 /swap/bulk 提交前都会用它逐条过滤"能不能对这节课
    提交调课"（本人任教的课一律允许；其余按班级数据范围）。MCP 写工具必须
    走同一判定，否则 AI 可以替教师申请任意班级的调课。
    """
    from app.modules.academic.services.access_scope import swap_entry_authorizer
    if entry is None:
        raise ValueError('课表条目不存在')
    if not swap_entry_authorizer(user)(entry):
        raise PermissionError('只能为本人任教的课程或本人负责班级的课程提交调课申请')
    return True


def swap_reviewable(user):
    """是否具备"参与任意一级调课审批"的资格（管理员 / 一级审批 / 课表终审）。

    列表与审批工具的准入闸：审批级由审批链决定，不同级对应不同权限点
    （一级 academic.swap_approve、终审 academic.timetable），故这里不能
    只判某一个 key，否则会挡住终审人。真正的"这一级轮不轮到我"由
    swap_review_scope（can_review）判定。
    """
    if user is None:
        raise PermissionError('未获取到执行身份')
    if getattr(user, 'role', None) == 'admin':
        return True
    for key in ('academic.swap_approve', 'academic.timetable'):
        if user.has_perm(key):
            return True
    raise PermissionError('账号没有调课审批权限（需 academic.swap_approve 或 '
                          'academic.timetable）')


def swap_review_scope(user, swap):
    """调课审批授权：复用网页端 can_review（分级 + 年级范围）。

    网页端 /swap/<id>/approve|reject 用 `_reject_if_not_my_step` 调 can_review，
    判定"当前这一级是否轮到我审"且"该调课年级是否在我可见范围内"。
    """
    from app.modules.academic.services import swap_service
    if swap is None:
        raise ValueError('调课记录不存在')
    if not swap_service.can_review(swap, user):
        step = swap_service.review_step_of(swap)
        name = step['name'] if step else '（已结束）'
        raise PermissionError(f'当前环节是「{name}」，不轮到你审批（或该调课不在你的年级范围内）')
    return True


def exam_write_scope(user, exam):
    """考试写入授权：与网页端 assert_exam_visible 同口径（年级范围）。

    成绩类写工具（改分 / 批量录入 / 重算）在 perm 之外必须再过这一关，
    否则 2025 级年级长可改 2024 级考试的成绩。
    """
    from app.modules.grades.services.scope import check_exam_visible
    if exam is None:
        raise ValueError('考试不存在')
    try:
        check_exam_visible(user, exam)
    except PermissionError:
        raise PermissionError(f'无权操作考试 {exam.id}「{exam.name}」（年级 {exam.grade} 不在你的数据范围内）')
    return True


def student_write_scope(user, student_no):
    """学号写入授权：复用 student_in_scope（成绩/考勤写路径共用）。"""
    return student_scope(user, student_no)


# ══════════════════════════════════════════════════════════════════════
# 宿舍 / 德育 / 学生 写入守卫（2026-10-11 第 2 批）
#
# 与第 1 批同样的原则：写工具在 perm 之外必须再过"网页端写入路径"的范围判定。
# 注意宿舍口径（app/utils/dormitory_scope.py）**不等价于**成绩口径
# （grades/services/scope.py），写宿舍工具必须用前者。
# ══════════════════════════════════════════════════════════════════════

def dorm_class_scope(user, grade, class_name):
    """班级是否在账号宿舍可见范围内（越界抛 PermissionError）。"""
    from app.utils.dormitory_scope import class_in_scope
    if not class_in_scope(user, grade, class_name):
        raise PermissionError(f'班级 {grade}{class_name} 不在你的宿舍管理范围内')
    return True


def dorm_student_scope(user, student_id):
    """学生是否在账号宿舍可见范围内（按主库 Student 判定）。"""
    from app.models.student import Student
    from app.utils.dormitory_scope import student_in_dorm_scope
    student = db_student(student_id)
    if not student_in_dorm_scope(user, student):
        raise PermissionError(
            f'学生 {student.student_number}（{student.grade}{student.class_name}）'
            f'不在你的宿舍管理范围内')
    return student


def db_student(student_id):
    """按主键取学生（不存在抛 ValueError）。"""
    from app.models.student import Student
    student = Student.query.get(int(student_id)) if student_id else None
    if student is None:
        raise ValueError(f'学生不存在：id={student_id}')
    return student


def points_student_scope(user, student_no):
    """德育打分授权：复用网页端 _in_scope 口径（年级/班级/用户级范围）。

    网页端 points/routes.py 的 _scope()/_in_scope() 是内联的，这里按同样优先级
    重写一份，避免 MCP 侧重新发明规则。
    """
    from app.models import UserClassLink
    from app.models.student import Student
    from app.modules.grades.services.scope import get_scope, user_grade_scope

    student = Student.query.filter_by(student_number=student_no).first()
    if student is None:
        raise ValueError(f'学号不存在：{student_no}')
    if getattr(user, 'role', None) == 'admin':
        return student
    ug = user_grade_scope(user)
    if ug is not None:                      # 用户级数据范围优先
        if student.grade not in ug:
            raise PermissionError(f'学生 {student_no} 不在你的德育管理范围内')
        return student
    scope, grade = get_scope(user)
    if scope == 'school':
        return student
    if scope == 'grade':
        if student.grade != grade:
            raise PermissionError(f'学生 {student_no} 不在你的德育管理范围内')
        return student
    if scope == 'class':
        pairs = {(l.grade, l.class_name)
                 for l in UserClassLink.query.filter_by(user_id=user.id).all()}
        if (student.grade, student.class_name) not in pairs:
            raise PermissionError(f'学生 {student_no} 不在你的德育管理范围内')
        return student
    raise PermissionError('账号没有有效的德育数据范围')


def schedule_class_scope(user, grade, class_name):
    """课表条目写入授权：与网页端条目 CRUD 同口径（academic_class_is_visible）。

    注意与 swap_entry_scope 的差异：调课用 swap_entry_authorizer（"本人任教的课"
    无条件放行，比班级范围更宽）；而**直接改课表**用纯班级范围。MCP 侧必须
    各用各的，不能混用，否则会出现"AI 能改、网页不能改"的越权缝隙。
    """
    from app.modules.academic.services.access_scope import academic_class_is_visible
    if not academic_class_is_visible(user, grade, class_name):
        raise PermissionError(f'班级 {grade}{class_name} 不在你的课表管理范围内')
    return True


def student_list_scope(user, student_no):
    """学号是否在账号的**学生列表口径**范围内（H-2 唯一口径）。

    2026-10-12 审计新增：网页端住宿检索页（dormitory/dashboard.py:210）用的是
    `app/utils/student_scope.apply_student_scope`，与成绩口径 `student_in_scope`
    不是同一个函数。用于住宿信息这类"学生列表类"读接口，保证与网页端同源。
    """
    from app.models.student import Student
    from app.utils.student_scope import apply_student_scope
    if getattr(user, 'role', None) == 'admin':
        return True
    visible = apply_student_scope(
        Student.query.filter_by(student_number=student_no), user).first()
    if visible is None:
        raise PermissionError(f'学生 {student_no} 不在你的可见范围内')
    return True


def schedule_writable(ts):
    """归档学期只读（网页端 _readonly_guard 的等价物）。

    服务层 add_entry/edit_entry/delete_entry/save_periods **不检查学期状态**，
    该限制只在网页端路由层；MCP 直接调服务层，故必须自己补这一关。
    """
    if ts is not None and getattr(ts, 'status', None) == 'archived':
        raise PermissionError('历史（已归档）学期课表为只读，不能修改')
    return True
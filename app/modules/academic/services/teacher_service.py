# StuLink v1.18.2.1 2026-09-24
# 教师名单服务：唯一编号生成 / 账号匹配与建号 / 任课映射统计
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import secrets

from app.extensions import db
from app.models import User
from app.models.academic import Teacher
from app.modules.grades.services import user_account
from app.utils import id_card as id_card_util

# 去易混字符（无 I/O/0/1），与成绩证明随机码风格一致
_UID_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def gen_teacher_uid():
    """生成教师唯一编号：T + 8 位字符，冲突自动重试（创建后不可更改）"""
    while True:
        uid = 'T' + ''.join(secrets.choice(_UID_ALPHABET) for _ in range(8))
        if not Teacher.query.filter_by(teacher_uid=uid).first():
            return uid


def find_by_id_card(value):
    """按身份证号明文等值查找教师（确定性密文，支持索引查询）"""
    cipher = id_card_util.encrypt_id_card(value)
    if not cipher:
        return None
    return Teacher.query.filter_by(id_card_enc=cipher).first()


def match_user(teacher):
    """为教师匹配登录账号。

    优先级：手机号 == username > 姓名 == real_name（唯一在职账号）。
    返回 (user, candidates, reason)：
      - user 非空：命中唯一账号
      - candidates 非空：存在多个同名账号，需人工处理
      - reason 非空：其他需人工处理的原因（如同名账号已停用）
    """
    if teacher.phone:
        u = User.query.filter_by(username=teacher.phone).first()
        if u and u.is_active:
            return u, [], ''
        if u and not u.is_active:
            return None, [], '同名手机号账号已停用，请先在教师管理中启用'
    users = User.query.filter_by(real_name=teacher.name).all()
    actives = [u for u in users if u.is_active]
    if len(actives) == 1:
        return actives[0], [], ''
    if len(actives) > 1:
        return None, actives[:10], '存在多个同名账号，请人工选择'
    if users:
        return None, [], '同名账号已停用，请先在教师管理中启用'
    return None, [], ''


def ensure_account(teacher):
    """确保教师有登录账号：已有则返回，未匹配则自动开户。

    返回 (user, password, created)：password 仅在本次新建账号时非空。
    """
    if teacher.user_id:
        u = db.session.get(User, teacher.user_id)
        if u:
            return u, None, False
    user, candidates, reason = match_user(teacher)
    if user:
        teacher.user_id = user.id
        return user, None, False
    if candidates or reason:
        return None, None, False   # 需人工处理，交给调用方提示
    username = teacher.phone or user_account.gen_username(teacher.name)
    user, pwd = user_account.create_teacher_account(teacher.name, username,
                                                    role='teacher')
    teacher.user_id = user.id
    return user, pwd, True


def lesson_map_count(teacher_uid):
    """该教师的任课映射数量（成绩管理 TeacherSubjectLink，按 user_id 关联）"""
    from app.models.grades import TeacherSubjectLink
    t = Teacher.query.filter_by(teacher_uid=teacher_uid).first()
    if not t or not t.user_id:
        return 0
    return TeacherSubjectLink.query.filter_by(user_id=t.user_id, active=True).count()


def teacher_of_user(user):
    """根据登录账号找教师名单记录：user_id 精确关联，兜底唯一同名自动补关联"""
    t = Teacher.query.filter_by(user_id=user.id).first()
    if t:
        return t
    rows = Teacher.query.filter_by(name=user.real_name).all()
    for cand in rows:
        if cand.user_id is None:
            cand.user_id = user.id
            db.session.commit()
            return cand
    return None

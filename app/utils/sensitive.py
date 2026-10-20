# StuLink v1.19.49.46 2026-10-13
# 敏感字段（身份证号 / 联系电话）字段级权限与脱敏展示
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""敏感字段的字段级权限与脱敏展示（唯一口径）。

背景（2026-10-13 权限审计）：
此前学生身份证号 / 联系电话只有**导出**维度有权限点（students.export_id_card /
export_phone），**页面展示**维度完全没有控制 —— 只要拿到 students.view 就能看到
明文（学生详情页身份证号、学生列表 / 宿舍床位卡联系电话）。教师手机号同样明文。

本模块提供：
- 字段级权限点常量：students.view_id_card / students.view_phone /
  academic.view_teacher_phone（在 permission_map 中 standalone=True，
  不随模块「写入」批发，只能单独勾选）
- 统一脱敏函数（身份证前 3 后 4；手机号前 3 后 4）
- Jinja 全局 `field_value(value, field)`：有权限返回明文，否则返回脱敏值。
  模板只改取值处即可，避免"前端隐藏但后端仍回传明文"的伪脱敏。

口径：**后端决定明文/脱敏**（不是前端隐藏）。无权限时即使调接口也只拿得到脱敏值。
"""
from app.utils.id_card import mask as _mask_id_card

PERM_ID_CARD = 'students.view_id_card'
PERM_PHONE = 'students.view_phone'
PERM_TEACHER_PHONE = 'academic.view_teacher_phone'

_FIELD_PERMS = {
    'id_card': PERM_ID_CARD,
    'phone': PERM_PHONE,
    'teacher_phone': PERM_TEACHER_PHONE,
}


def mask_phone(value):
    """联系电话脱敏：保留前 3 后 4（如 138****5678）。

    过短的号码（座机短号等）降级：>=3 位保留首尾各 1 位，更短整体打星。
    """
    s = str(value or '').strip()
    if not s:
        return ''
    if len(s) >= 8:
        return s[:3] + '*' * (len(s) - 7) + s[-4:]
    if len(s) >= 3:
        return s[0] + '*' * (len(s) - 2) + s[-1]
    return '*' * len(s)


def can_view(user, field):
    """当前用户是否有该敏感字段的明文查看权。字段未登记 → 视为无限制（True）。"""
    perm = _FIELD_PERMS.get(field)
    if not perm:
        return True
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    try:
        return bool(user.has_perm(perm))
    except Exception:                      # noqa: BLE001
        return False


def display(value, field, user=None):
    """按字段级权限返回明文或脱敏值（空值返回空串）。"""
    if not value:
        return ''
    if user is None:
        from flask_login import current_user
        user = current_user
    if can_view(user, field):
        return value
    if field == 'id_card':
        return _mask_id_card(value)
    if field in ('phone', 'teacher_phone'):
        return mask_phone(value)
    return value


def field_value(value, field):
    """Jinja 全局：模板中 `{{ field_value(s.phone1, 'phone') }}`。"""
    return display(value, field)

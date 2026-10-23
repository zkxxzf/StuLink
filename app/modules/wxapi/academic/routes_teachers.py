# StuLink v1.19.71.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序教师名单（只读通讯录）。

字段级脱敏与网页端一致：无 `academic.view_teacher_phone` 时手机号返回掩码。
权限也与网页端 `/academic/teachers` 同口径（`academic.view`）——
2026-10-20 审计：原来只要求登录，任意小程序账号都能枚举全校教师
姓名 / 工号 / 学科 / 备注。
"""
from flask import g, request
from sqlalchemy import or_

from app.models.academic import Teacher
from app.modules.wxapi import bp
from app.modules.wxapi.core.guards import wx_perm_required
from app.modules.wxapi.core.response import wx_ok
from app.utils import sensitive

PERM = 'academic.view'
LIMIT = 300


@bp.route('/teachers')
@wx_perm_required(PERM)   # 2026-10-19 第十三轮审计：网页端同功能要 academic.view，
                          # 这里原只登录即可拉全量通讯录；wx_perm_required 内含
                          # 登录校验，勿再叠 wx_login_required（会双倍限流）
def wx_teachers():
    """教师名单（query: q / subject / status，默认在职）。"""
    q = (request.args.get('q') or '').strip()
    subject = (request.args.get('subject') or '').strip()
    status = (request.args.get('status') or 'active').strip()
    # 2026-10-20：离职/退休已归档教师只在「系统管理 → 教师管理」可查，
    # 小程序通讯录一律不返回（即使显式传 status=left）。
    query = Teacher.query.filter(Teacher.archive_kind.is_(None))
    if status in ('active', 'left'):
        query = query.filter(Teacher.status == status)
    if subject:
        query = query.filter(Teacher.subject == subject)
    if q:
        like = '%' + q + '%'
        query = query.filter(or_(Teacher.name.like(like),
                                 Teacher.teacher_uid.like(like)))
    rows = query.order_by(Teacher.subject, Teacher.name).limit(LIMIT).all()
    # 学科筛选项只取在职教师，避免离职教师的旧学科混进筛选
    subjects = sorted({t.subject for t in
                       Teacher.query.filter_by(status='active').all()
                       if t.subject})
    return wx_ok({
        'items': [_row(t) for t in rows],
        'total': len(rows),
        'subjects': subjects,
    })


def _row(t):
    """教师行：工号 / 姓名 / 学科 / 状态 / 手机号（按权限脱敏）。"""
    return {
        'uid': t.teacher_uid or '',
        'name': t.name or '',
        'subject': t.subject or '',
        'status': t.status or '',
        'status_text': '在职' if t.status == 'active' else '离职',
        'phone': sensitive.display(t.phone, 'teacher_phone', g.wx_user),
        'note': t.note or '',
    }

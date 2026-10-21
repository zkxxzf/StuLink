# StuLink v1.19.58.64 2026-10-14
# 任课映射方案：新建 / 使用（切为当前）/ 重命名 / 删除 + 与当前映射同步
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""任课映射方案服务（2026-10-14，用户要求"每学期会变，要有记录、可新增方案"）。

模型
----
- 方案（``TeacherLinkPlan``）＝某学期的整套任课映射留档，可任意多个，其中一个
  ``is_current=True`` 是**当前生效方案**；
- 快照（``TeacherLinkPlanRow``）＝方案 × 年级 × 班级 × 科目 → 教师账号，
  每个方案（含当前方案）各存一份完整明细。

与主表的关系（关键口径，勿改）
----------------------------
``teacher_subject_links`` 仍是**当前生效映射的唯一运行真值**（成绩 / 教务 / 考务 /
工作台 / MCP 等 20+ 处读取点只认 ``active=True``，零改动）。因此：

- 保存矩阵 / 导入确认后 → :func:`sync_current`：主表(active) 覆盖写当前方案快照；
- 使用某方案 → :func:`use_plan`：先把主表现状回写当前方案快照（防丢），再把目标
  方案快照**物化**回主表（新增 / 恢复 / 失效），最后翻转 ``is_current``；
- 主表语义与列都不动，下游读取点感知不到「方案」这层。
"""
import logging
from datetime import datetime

from sqlalchemy import func

from app.extensions import db
from app.models.grades import TeacherLinkPlan, TeacherLinkPlanRow, TeacherSubjectLink

# 2026-10-10 架构审计：本模块统一 logger 留痕，不吞异常。
logger = logging.getLogger(__name__)

# 无任何方案时自动创建的初始方案名（迁移脚本会为存量库先建「初始方案」）
DEFAULT_PLAN_NAME = '初始方案'

ACTIONS = ('current', 'blank')  # 新建方案的起点（'plan:<id>' 也支持）


# ── 公共小工具 ─────────────────────────────────────────────────────────────

def user_names(ids):
    """users.id 集合 → {id: 姓名}（用于「创建人」列 / 矩阵里已停用账号的姓名）。"""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    from app.models import User
    return {u.id: (u.real_name or u.username or '')
            for u in User.query.filter(User.id.in_(ids)).all()}


def default_plan_name(school_year, term=''):
    """默认方案名：「2026-2027学年 第一学期」/ 未填学年时「任课映射方案」。"""
    school_year = (school_year or '').strip()
    term = (term or '').strip() or '整学年'
    if not school_year:
        return '任课映射方案'
    return f'{school_year}学年 {term}'


# ── 读取 ───────────────────────────────────────────────────────────────────

def current_plan():
    """当前生效方案（无则 None）。"""
    return (TeacherLinkPlan.query.filter_by(is_current=True)
            .order_by(TeacherLinkPlan.id).first())


def row_count(plan_id):
    """某方案的映射条数（页面「已登记 N 条」）。"""
    return TeacherLinkPlanRow.query.filter_by(plan_id=plan_id).count()


def list_items():
    """方案清单（当前在前，其余按创建时间倒序）：含条数 / 年级范围 / 创建人。"""
    plans = (TeacherLinkPlan.query
             .order_by(TeacherLinkPlan.is_current.desc(),
                       TeacherLinkPlan.created_at.desc(),
                       TeacherLinkPlan.id.desc()).all())
    if not plans:
        return []
    counts = dict(db.session.query(TeacherLinkPlanRow.plan_id,
                                   func.count(TeacherLinkPlanRow.id))
                  .group_by(TeacherLinkPlanRow.plan_id).all())
    scopes = {}
    for pid, grade in (db.session.query(TeacherLinkPlanRow.plan_id,
                                        TeacherLinkPlanRow.grade).distinct().all()):
        scopes.setdefault(pid, set()).add(grade or '')
    names = user_names({p.created_by for p in plans})
    items = []
    for p in plans:
        d = p.to_dict()
        d['count'] = counts.get(p.id, 0)
        d['scope'] = ' / '.join(sorted(g for g in scopes.get(p.id, set()) if g))
        d['creator'] = names.get(p.created_by, '')
        items.append(d)
    return items


# ── 主表 ↔ 方案快照 ────────────────────────────────────────────────────────

def _snapshot_from_main(plan, operator_id=None):
    """主表(active) → 方案快照（覆盖写）。不 commit，由调用方统一提交。"""
    rows = TeacherSubjectLink.query.filter_by(active=True).all()
    TeacherLinkPlanRow.query.filter_by(plan_id=plan.id).delete(synchronize_session=False)
    db.session.flush()      # 先落 DELETE 再 INSERT，避免唯一键瞬态冲突
    for l in rows:
        db.session.add(TeacherLinkPlanRow(plan_id=plan.id, grade=l.grade,
                                          class_name=l.class_name,
                                          subject=l.subject, user_id=l.user_id))
    plan.updated_at = datetime.now()


def _materialize(plan, operator_id=None):
    """方案快照 → 主表（新增 / 恢复 / 改派 / 失效），返回统计。不 commit。"""
    target = {(r.grade, r.class_name, r.subject): r.user_id
              for r in TeacherLinkPlanRow.query.filter_by(plan_id=plan.id).all()}
    existing = {(l.grade, l.class_name, l.subject): l
                for l in TeacherSubjectLink.query.all()}
    stats = {'added': 0, 'restored': 0, 'changed': 0, 'released': 0}
    for (grade, class_name, subject), user_id in target.items():
        link = existing.get((grade, class_name, subject))
        if link is None:
            db.session.add(TeacherSubjectLink(grade=grade, class_name=class_name,
                                              subject=subject, user_id=user_id,
                                              active=True, created_by=operator_id))
            stats['added'] += 1
        elif not link.active:
            link.user_id = user_id
            link.active = True
            stats['restored'] += 1
        elif link.user_id != user_id:
            link.user_id = user_id
            stats['changed'] += 1
    for (grade, class_name, subject), link in existing.items():
        if (grade, class_name, subject) not in target and link.active:
            # 目标方案里没有这一格 → 失效（保留行，与矩阵「清空」同一语义）
            link.active = False
            stats['released'] += 1
    return stats


def _snapshot_differs(plan):
    """当前方案快照与主表(active) 是否有实质差异（含"换了老师但条数不变"）。"""
    main = {(l.grade, l.class_name, l.subject, l.user_id)
            for l in TeacherSubjectLink.query.filter_by(active=True).all()}
    snap = {(r.grade, r.class_name, r.subject, r.user_id)
            for r in TeacherLinkPlanRow.query.filter_by(plan_id=plan.id).all()}
    return main != snap


def ensure_current_plan(operator_id=None):
    """保证存在当前生效方案，且它的快照与主表一致（幂等）。

    页面进入 / 保存矩阵 / 导入确认 / 切换方案都会走这里：

    - 没有方案 → 把现有映射收拢成「初始方案」；
    - 有方案但快照与主表不同 → 以主表为准刷新快照（运维脚本绕过页面直接改
      teacher_subject_links 时，打开页面即自愈，避免"清单条数/内容"滞后）。
    """
    plan = current_plan()
    if plan is None:
        # 异常态（有方案但都没标 current / 或一个都没有）
        plan = (TeacherLinkPlan.query
                .order_by(TeacherLinkPlan.created_at.desc(),
                          TeacherLinkPlan.id.desc()).first())
        if plan is None:
            plan = TeacherLinkPlan(name=DEFAULT_PLAN_NAME, note='首次使用时自动创建',
                                   is_current=True, created_by=operator_id)
            db.session.add(plan)
            db.session.flush()
        _snapshot_from_main(plan, operator_id=operator_id)
        plan.is_current = True
        db.session.commit()
        return plan
    if _snapshot_differs(plan):
        try:
            _snapshot_from_main(plan, operator_id=operator_id)
            db.session.commit()
        except Exception as e:  # noqa: BLE001  并发下重复刷新可能撞唯一键，下次进入自愈
            db.session.rollback()
            logger.warning('当前方案快照刷新失败（下次进入页面会重试）：%s', e)
    return plan


def sync_current(operator_id=None):
    """主表 → 当前方案快照（保存矩阵 / 导入确认后调用，保证快照不落后）。"""
    return ensure_current_plan(operator_id=operator_id)


# ── 方案管理 ───────────────────────────────────────────────────────────────

def create_plan(name='', school_year='', term='', note='', copy_from='current',
                use_now=True, operator_id=None):
    """新建方案。

    :param copy_from: 'current'=复制当前映射（默认，新学期沿用最常用）/ 'blank'=空白 /
                      'plan:<id>'=复制某历史方案
    :param use_now: 创建后立即「使用」（切换为当前生效；默认 True）
    """
    school_year = (school_year or '').strip()
    term = (term or '').strip()
    name = (name or '').strip() or default_plan_name(school_year, term)
    if TeacherLinkPlan.query.filter_by(name=name).first():
        raise ValueError(f'方案「{name}」已存在，请换个名称')
    plan = TeacherLinkPlan(name=name, school_year=school_year, term=term,
                           note=(note or '').strip(), is_current=False,
                           created_by=operator_id)
    db.session.add(plan)
    db.session.flush()

    source = (copy_from or 'current').strip()
    rows = []
    if source.startswith('plan:'):
        try:
            src_id = int(source.split(':', 1)[1])
        except (ValueError, TypeError):
            raise ValueError('复制来源不正确')
        if TeacherLinkPlan.query.get(src_id) is None:
            raise ValueError('复制的来源方案不存在')
        rows = TeacherLinkPlanRow.query.filter_by(plan_id=src_id).all()
    elif source == 'blank':
        rows = []
    else:                                   # current（默认）
        rows = TeacherSubjectLink.query.filter_by(active=True).all()
    seen = set()
    for r in rows:
        key = (r.grade, r.class_name, r.subject)
        if key in seen:                     # 源数据理论上不会重复，防御
            continue
        seen.add(key)
        db.session.add(TeacherLinkPlanRow(plan_id=plan.id, grade=r.grade,
                                          class_name=r.class_name,
                                          subject=r.subject, user_id=r.user_id))
    db.session.commit()
    if use_now:
        use_plan(plan.id, operator_id=operator_id)
    return plan


def use_plan(plan_id, operator_id=None):
    """使用某方案：切换为当前生效（物化到主表）。返回 (plan, stats)。"""
    plan = TeacherLinkPlan.query.get(int(plan_id))
    if plan is None:
        raise ValueError('方案不存在（可能已被删除）')
    if plan.is_current:
        return plan, None
    for old in TeacherLinkPlan.query.filter_by(is_current=True).all():
        # 先把当前方案的快照按主表刷新（防止切走后最近的编辑丢失）
        _snapshot_from_main(old, operator_id=operator_id)
        old.is_current = False
    stats = _materialize(plan, operator_id=operator_id)
    plan.is_current = True
    db.session.commit()
    return plan, stats


def rename_plan(plan_id, name='', note=None, operator_id=None):
    """重命名 / 改说明（学年学期不可改：它是导出与筛选的坐标）。"""
    plan = TeacherLinkPlan.query.get(int(plan_id))
    if plan is None:
        raise ValueError('方案不存在（可能已被删除）')
    name = (name or '').strip()
    if not name:
        raise ValueError('方案名称不能为空')
    dup = (TeacherLinkPlan.query
           .filter(TeacherLinkPlan.name == name, TeacherLinkPlan.id != plan.id)
           .first())
    if dup:
        raise ValueError(f'方案「{name}」已存在，请换个名称')
    plan.name = name
    if note is not None:
        plan.note = (note or '').strip()
    db.session.commit()
    return plan


def delete_plan(plan_id, operator_id=None):
    """删除方案（当前生效方案不可删 —— 否则"当前映射"就没了来源）。"""
    plan = TeacherLinkPlan.query.get(int(plan_id))
    if plan is None:
        raise ValueError('方案不存在（可能已被删除）')
    if plan.is_current:
        raise ValueError('当前生效方案不能删除，请先「使用」其他方案')
    # 2026-10-18 审计加固：方案明细是"当时的班×科映射"，删掉后只能靠现在的映射
    # 近似重建（等于失真）。删前整组快照，回收站可按原 id 整体恢复。
    rows = TeacherLinkPlanRow.query.filter_by(plan_id=plan.id).all()
    from app.utils.trash import archive_snapshot
    from app.models import User
    _op = db.session.get(User, operator_id) if operator_id else None
    _snap = archive_snapshot(
        'grades', 'link_plan_bundle', '任课映射方案',
        tables=[(TeacherLinkPlan, plan), (TeacherLinkPlanRow, rows)],
        summary=f'{plan.name}：{len(rows)} 条班级×科目明细',
        operator=_op)
    if _snap is None:
        db.session.rollback()
        raise ValueError('删除前归档失败，已中止（避免方案不可恢复）')
    db.session.commit()          # 归档先落库，再删
    TeacherLinkPlanRow.query.filter_by(plan_id=plan.id).delete(synchronize_session=False)
    db.session.delete(plan)
    db.session.commit()
    return plan

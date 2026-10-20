# StuLink v1.19.49.39 2026-10-13
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""节次显示名（period_number → 学校口径的节次名）—— 全站唯一口径。

**为什么需要它**：`schedule_entries.period_number` 只是**行号**，不是学校叫法。
按学校作息，`period_defs` 里 1 号位是「早读」，于是：

    period_number=2 → 第1节   period_number=8 → 第7节   period_number=9 → 第8节

凡是把 period_number 直接当节次印出来的地方（如教师工作台「今日课程」显示
「第 8 节」而实际是「第7节」），都会整体错一位。展示一律走本模块：

    {{ period_label(e.period_number) }}           # 模板全局，单值
    period_names()                                 # 模板全局，{number: name}（前端 JS 用）

取不到节次定义（学期没配节次 / 库异常）时回落「第N节」，不会渲染成空。
"""
from app.utils.cache import cached


@cached(ttl=600)
def period_names(schedule_id=None):
    """{period_number: period_name}；无节次定义或库异常 → {}（调用方自行回落）。

    600s TTL：作息表改完最多 10 分钟生效（与其它参考数据缓存同档）。
    """
    if not schedule_id:
        return {}
    try:
        from app.models.timetable import PeriodDef
        rows = (PeriodDef.query
                .filter_by(term_schedule_id=int(schedule_id)).all())
    except Exception:   # noqa: BLE001  库缺失/未迁移：降级为空，不影响页面渲染
        return {}
    return {p.period_number: (p.period_name or '').strip() for p in rows}


def _active_id():
    """当前学期 id（取不到返回 None）"""
    try:
        from app.modules.academic.services import schedule_service
        ts = schedule_service.get_active_schedule()
        return ts.id if ts else None
    except Exception:   # noqa: BLE001
        return None


def period_label(period_number, schedule_id=None):
    """节次显示名：优先 PeriodDef.period_name（可带「第N节 / 早读 / 晚自习1」），
    查不到回落「第N节」；period_number 非数字/为 0 时返回空串。
    """
    try:
        n = int(period_number or 0)
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    sid = schedule_id if schedule_id is not None else _active_id()
    return (period_names(sid) or {}).get(n) or ('第%d节' % n)


def period_name_map(schedule_id=None):
    """{number: name}（当前学期）；供模板注入前端 JS 使用"""
    sid = schedule_id if schedule_id is not None else _active_id()
    return period_names(sid) or {}

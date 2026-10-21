# StuLink v1.19.59.64 2026-10-20
# 限时练单科分析：班均分 + 名次区间人数（不做多科汇总）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练单科分析（2026-10-20 用户口径）。

只做两件事，不加载任何其它分析子页面：
1. **各班该科去差均分**（缺考不落库，天然不计入分母；去差 = 按班型名额
   剔除该科成绩末尾 N 人后重算）；
2. **各班落在指定名次区间内的人数**（区间来自限时练专属分层模板，
   按本场该科的**全年级名次** ``rank_grade`` 落区间）。

去差口径与「全局对比 / 汇报区」共用一份规则（``report_service.TRIM_RULES``，
卓越班 2 人、其它班型不去差），实际剔除人数也复用 ``global_compare_service``
的「缺考先顶替名额」算法，跨页同数。
2026-10-20 用户口径：分析页删掉最高分/最低分，均分改为去差均分。
"""
import logging
import time

logger = logging.getLogger(__name__)

from app.extensions import db
from app.models.timed import TimedScore
from app.modules.grades.services.timed_import import _norm_class


# ==================== 进程内缓存（2026-10-20 性能优化） ====================
# 为什么：一场 500 人 × 9 科 = 4500 行，翻页 / 换搜索词 / 看分析页都会重复
# 算一遍名次与 T 分（纯 Python 遍历 + 每科 500 次均值方差）。
# 这些结果只随"成绩变动"变化，所以按场次缓存 2 分钟；导入 / 删除时主动失效。
# 单进程 dict 足够：数据量小（几 MB），多进程部署最多多算几次，不影响正确性。
_CACHE = {}
_CACHE_TTL = 120


def _cached(key, builder):
    """命中返回缓存值，否则用 builder() 生成并缓存"""
    now = time.time()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = builder()
    if len(_CACHE) > 128:                      # 顺手清过期，防止无限增长
        for k in [k for k, v in _CACHE.items() if v[0] <= now]:
            _CACHE.pop(k, None)
    _CACHE[key] = (now + _CACHE_TTL, val)
    return val


def invalidate(exam_id=None):
    """成绩变动后清缓存（导入 / 删除 / 覆盖时调用）。

    直接整表清：趋势 / 学生详情是**跨场次**的派生数据，按单个场次清不干净，
    而重建成本只有一次几百毫秒，宁可多算也别给旧数据。
    """
    _CACHE.clear()


def save_subject(exam, subject, rows):
    """整科覆盖写入 + 重算名次（不 commit，事务交给路由层）

    2026-10-21 审计：**不在这里清缓存** —— 事务还没提交，清完可能被并发请求
    用旧数据重建，提交后仍继续存活到 TTL。由路由在 commit 成功后调
    ``invalidate()``（导入确认 / AI 导入 / 删场次三处）。
    """
    for row in rows:
        db.session.add(TimedScore(
            exam_id=exam.id, student_no=row['student_no'],
            student_name=row.get('student_name', ''),
            zhixue_no=row.get('zhixue_no', ''),
            grade=exam.grade, class_name=row.get('class_name', ''),
            subject=subject, score=row['score']))
    db.session.flush()
    from app.modules.grades.services import timed_service
    timed_service.recalc_subject_ranks(exam.id, subject)


def classes_of(exam_id, subject=None, user=None):
    """本场出现过的班级（按班级名排序，供成绩单 / 分析页筛选）

    ``user`` 给出时只留可见班级（筛选按钮本身也不该暴露不可见班级名）。
    """
    q = db.session.query(TimedScore.class_name).filter(
        TimedScore.exam_id == int(exam_id))
    if subject:
        q = q.filter(TimedScore.subject == subject)
    names = {r[0] for r in q.distinct().all() if r[0]}
    scope = _scope_of(user)
    if scope is not None:
        grade = (db.session.query(TimedScore.grade)
                 .filter(TimedScore.exam_id == int(exam_id)).limit(1).scalar())
        names = {n for n in names if scope.can_view_class(grade, n)}
    # 2026-10-21：同一个班可能存成「01班」与「高三01班」两种写法，
    # 归一化后再去重，筛选按钮不会出现两个同名班
    return sorted({_norm_class(n) for n in names if _norm_class(n)})


def _summary_base(exam):
    """汇总表全量（按场次缓存）：科目 + 每生各科分 + 名次 + T 分"""
    from app.models.grades import SUBJECTS

    rows = TimedScore.query.filter_by(exam_id=exam.id).all()
    subs = {r.subject for r in rows}
    # 2026-10-21 用户口径：列固定展示**全部 9 科**（+总分），本场没导入的科
    # 留「—」。导入后要能一眼看出哪科还没导，不能按"有的科"收窄列。
    subjects = list(SUBJECTS)
    by = {}
    by_subject = {}
    for r in rows:
        raw_cls = _norm_class(r.class_name)
        it = by.setdefault(r.student_no, {
            'student_no': r.student_no, 'name': r.student_name or '',
            # 无班级归属的（不分班/未分班）显示为「—」，页面与导出里不再出现「不分班」
            'class_name': '—' if _no_class(raw_cls) else raw_cls,
            'scores': {}})
        it['scores'][r.subject] = r.score
        by_subject.setdefault(r.subject, {})[r.student_no] = r.score

    # 每科 T 分（同科内比），汇总表在分数下方用小字显示
    tmaps = {s: _t_map_of(m) for s, m in by_subject.items()}

    out = []
    for it in by.values():
        it['total'] = round(sum(it['scores'].values()), 1)
        it['subject_count'] = len(it['scores'])
        it['ts'] = {s: tmaps.get(s, {}).get(it['student_no'])
                    for s in subjects}
        out.append(it)

    # 年级名次 → 班级名次（都在**过滤班级之前**算，保证名次口径是全年级）
    out.sort(key=lambda x: (-x['total'], x['student_no']))
    _assign_ranks(out, 'rank_grade')
    classes = [c for c in sorted({x['class_name'] for x in out}) if c != '—']
    for cls in classes:
        _assign_ranks([x for x in out if x['class_name'] == cls], 'rank_class')

    # 总分 T 分（跨场次比总水平用）
    tmap = _t_map_of({x['student_no']: x['total'] for x in out})
    for x in out:
        x['t'] = tmap.get(x['student_no'])

    return {'subjects': subjects, 'out': out, 'classes': classes,
            'have_count': len(subs), 't_ready': bool(tmap)}


def summary_matrix(exam, class_name='', q='', page=None, per_page=50, user=None):
    """汇总表：一行一个学生 × 各科分数 + 总分 + 年级名次 + 班级名次 + 总分 T 分。

    2026-10-20 用户口径：限时练没有「总分」行，总分由各科现算；
    名次按总分降序（同分并列 1,1,3），班级名次在**同班**内部再排一次。
    全量结果按场次缓存，翻页 / 搜索 / 换班级都只做内存过滤。

    2026-10-21 权限收口：按 ``user`` 的成绩可见范围裁剪（见 ``_mask_summary_row``）。
    返回里带 ``total_visible`` / ``rank_g_visible`` / ``rank_c_visible`` 三个开关，
    模板与导出据此决定是否渲染总分、年级名次、班级名次这几列。
    """
    base = _cached(('summary', exam.id), lambda: _summary_base(exam))
    scope = _scope_of(user)
    grade = exam.grade
    all_classes = list(base['classes'])
    classes = (all_classes if scope is None
               else [c for c in all_classes if scope.can_view_class(grade, c)])
    subjects = _column_subjects(scope, grade, class_name, classes, base['subjects'])

    shown = [x for x in base['out']
             if not class_name or x['class_name'] == class_name]
    if scope is not None:
        # 非可见班级整行都不给。「—」无班级行按年级口径判定：年级长 / 任课教师
        # 可见（R1 本来就是「年级 × 学科」），班主任不可见。
        shown = [x for x in shown if scope.can_view_class(grade, x['class_name'])]
        shown = [_mask_summary_row(x, scope, grade) for x in shown]

    # 列开关：以当前筛选下真实存在的字段为准；没有行时按范围口径给默认值
    if shown:
        total_visible = any('total' in x for x in shown)
        rank_g_visible = any('rank_grade' in x for x in shown)
        rank_c_visible = any('rank_class' in x for x in shown)
    else:
        probe = class_name or (classes[0] if classes else '')
        if scope is None:
            total_visible = rank_g_visible = rank_c_visible = True
        else:
            total_visible = bool(probe and scope.class_total_visible(grade, probe))
            rank_c_visible = total_visible
            rank_g_visible = bool(probe and scope.total_rank_visible(grade, probe))

    # 搜索：学号 / 姓名包含即可（不区分大小写）
    kw = (q or '').strip().lower()
    if kw:
        shown = [x for x in shown
                 if kw in (x['student_no'] or '').lower()
                 or kw in (x['name'] or '').lower()]
    total = len(shown)
    # page=None → 全量（导出用）；否则分页（页面默认 50 人/页）
    if page is None:
        rows, pages, page_no = shown, 1, 1
    else:
        pages = max(1, (total + per_page - 1) // per_page)
        try:
            page_no = int(page or 1)
        except (TypeError, ValueError):
            page_no = 1
        page_no = min(max(1, page_no), pages)
        rows = shown[(page_no - 1) * per_page: page_no * per_page]
    return {'subjects': subjects, 'rows': rows, 'classes': classes,
            't_ready': base['t_ready'], 't_min': T_MIN_SAMPLE,
            'have_count': base['have_count'],
            'total_visible': total_visible, 'rank_g_visible': rank_g_visible,
            'rank_c_visible': rank_c_visible,
            'count': total, 'page': page_no, 'pages': pages,
            'per_page': per_page, 'q': q}


def _column_subjects(scope, grade, class_name, classes, subjects):
    """表头可见科目。

    指定了班级 → 按该班口径；否则取可见班级的并集（任课教师＝所教科，
    班主任＝本班全科，所以浏览「全部」时表头仍是全科）。
    """
    if scope is None:
        return list(subjects)
    if class_name:
        allowed = scope.class_subjects(grade, class_name)
        return list(subjects) if allowed is None else [
            s for s in subjects if s in allowed]
    if not classes:
        allowed = scope.grade_subjects(grade)
        return list(subjects) if allowed is None else [
            s for s in subjects if s in allowed]
    return [s for s in subjects
            if any(scope.can_view_subject(grade, c, s) for c in classes)]


def _assign_ranks(items, key):
    """按当前顺序写入名次（同分并列，如 1,1,3）"""
    prev = None
    rank = 0
    for i, it in enumerate(items, 1):
        if it['total'] != prev:
            rank = i
            prev = it['total']
        it[key] = rank


def subject_blocks(exam, tpl, user=None):
    """分析页数据：一个学科一块，每块两组「图 + 表」——

    上：**人数** 左堆叠图（各班各名次段人数） + 右人数表；
    下：**去差均分** 左柱状图（各班去差均分） + 右表（去差均分 + 各段均分）。

    2026-10-21 权限收口：只保留当前账号可见的「班级 × 学科」组合；
    任课教师只出本学科（本年级全部班），班主任出本班全科，无可见数据的科目
    整块不出（避免出现空图）。
    """
    from app.models.grades import SUBJECTS
    scope = _scope_of(user)
    skey = _scope_key(scope)
    have = {r[0] for r in db.session.query(TimedScore.subject)
            .filter_by(exam_id=exam.id).distinct().all()}
    out = []
    for s in SUBJECTS:
        if s not in have:
            continue
        a = _cached(('analysis', exam.id, s, getattr(tpl, 'id', 0), skey),
                    lambda s=s: subject_analysis(exam, s, tpl, scope=scope))
        if not a['rows']:
            continue                    # 该科没有任何可见班级
        rows = a['rows']
        classes = [r['class_name'] for r in rows]
        # 堆积图：互斥段，各班段内人数
        seg_names = [seg['name'] for seg in a['total']['stack']]
        count_chart = {
            'classes': classes,
            'series': [
                {'name': name,
                 'data': [next((seg['count'] for seg in r['stack']
                                if seg['name'] == name), 0)
                          for r in rows]}
                for name in seg_names],
        }
        out.append({
            'subject': s,
            'analysis': a,
            'count_chart': count_chart,
            # 柱状图画的也是去差均分（与表格同口径，2026-10-20 用户要求）
            'avg_chart': {'classes': classes,
                          'avgs': [r['trim_avg'] for r in rows]},
        })
    return out


def _layer_label(layer):
    """层说明（悬停用）：前10 → 「全年级名次 ≤ 10」"""
    high = layer.get('to_rank')
    if high in (None, ''):
        return '全年级'
    return f'全年级名次 ≤ {int(high)}'


def effective_layers(layers, count):
    """按实考人数裁剪 / 自适应分层（保底机制，2026-10-20 用户口径）。

    - **人数 < 100**：按 10 人一组分层（前10、前20…刚好覆盖全体），
      不够 100 人时 12 层模板的前50/前100 没有区分度；
    - **人数 ≥ 100**：沿用模板层，但**丢掉门槛 > 人数的层**
      （497 人时「前500 / 前550」就等于全体，不再显示）。
    """
    layers = list(layers or [])
    if count <= 0:
        return []
    # 收口层：无上界 = 全年级都算。
    # 为什么不用「前{count}」：同分同名次后延跳号，497 人里最大名次可能到 499，
    # 用名次门槛会漏掉尾巴上的人；「全员」段保证一个人都不少。
    closing = {'name': '全员', 'from_rank': 1, 'to_rank': None}
    if count < 100:
        out = []
        rank = 10
        while rank < count:
            out.append({'name': f'前{rank}', 'from_rank': 1, 'to_rank': rank})
            rank += 10
        out.append(closing)
        return out
    keep = [x for x in layers
            if x.get('to_rank') is None or int(x['to_rank']) <= count]
    keep = [x for x in keep if x.get('to_rank') is not None] or [
        dict(x) for x in layers[:3] if x.get('to_rank') is not None]
    keep.append(closing)
    return keep


def _empty_row(name):
    """一行统计骨架（班级 / 合计）"""
    return {'class_name': name, 'teacher': '', 'count': 0, 'sum': 0.0,
            'scores': [], 'bands': [], 'band_scores': {}}


def _teacher_map(grade, subject):
    """{班级: 任课教师姓名}（取当前生效的任课映射，未设置返回空）"""
    from app.models import User
    from app.models.grades import TeacherSubjectLink

    links = (TeacherSubjectLink.query
             .filter_by(grade=grade, subject=subject, active=True).all())
    if not links:
        return {}
    users = {u.id: u for u in User.query.filter(
        User.id.in_({x.user_id for x in links})).all()}
    out = {}
    for link in links:
        u = users.get(link.user_id)
        if u:
            # 2026-10-21：映射里存「01班」，成绩里可能是「高三01班」，
            # 不归一化就会整列「—」
            out[_norm_class(link.class_name)] = u.real_name or u.username
    return out


def _hit(layer, rank):
    """累积门槛：名次 ≤ 层门槛即命中（to_rank 空 = 全年级都算）"""
    if not rank:
        return False
    high = layer.get('to_rank')
    return True if high in (None, '') else rank <= int(high)


def _accumulate(row, score, rank):
    """把一名学生累加进统计行（rank 只用于区间判定，见 subject_analysis）"""
    row['count'] += 1
    row['sum'] += float(score or 0)
    row['scores'].append(float(score or 0))


def _trim_quotas(grade):
    """{班级: 去差名额}：按班型取名额（与全局对比共用 TRIM_RULES）

    限时练成绩行没有「班型快照」，取当前班型设置（ClassProfile）；
    班级未登记班型 / 班型不在规则里 → 0（不去差）。
    """
    from app.models import ClassProfile
    from app.modules.grades.services.report_service import (
        DEFAULT_TRIM, TRIM_RULES)
    try:
        profs = ClassProfile.query.filter_by(grade=grade).all()
    except Exception as _e:                  # noqa: BLE001 配置异常不拖垮分析
        logger.warning('timed_analysis.py::_trim_quotas 异常（按不去差处理）: %s', _e)
        return {}
    return {_norm_class(p.class_name):
            TRIM_RULES.get(p.class_type or '', DEFAULT_TRIM) for p in profs}


def _finish(row, need=0):
    """结算平均分 + 去差均分（1 位小数）

    need = 该班去差名额（班型规则，卓越班 2 人）；实际剔除收敛到 count-1 以内。
    去差均分 = 剔除本班该科末尾 need 人后重算（2026-10-20 用户口径：
    分析页只看去差均分，不再给最高/最低分）。
    保留 ``kept`` 供「合计」行按去差后合并（取完需 pop）。
    """
    cnt = row['count']
    scores = sorted(row.pop('scores', []))
    row['avg'] = round(row['sum'] / cnt, 1) if cnt else None
    # 至少留 1 人：班级人数少到没有可剔除的，去差人数自动收敛为 0
    row['trim_n'] = min(max(need, 0), max(cnt - 1, 0))
    # 升序后从**低分端**剔除（去差剔的是末尾/尾巴，不是最高分）
    keep = scores[row['trim_n']:] if row['trim_n'] else scores
    row['trim_avg'] = round(sum(keep) / len(keep), 1) if keep else None
    row['kept'] = keep
    row.pop('sum', None)
    return row


# 无班级归属的占位班级名（这类成绩不进班级统计）
NO_CLASS_NAMES = ('不分班', '未分班')


def _no_class(name):
    """是否为「无班级」占位值（空 / 不分班 / 未分班）"""
    return not (name or '').strip() or (name or '').strip() in NO_CLASS_NAMES


# ==================== 成绩可见范围（2026-10-21 权限收口） ====================
# 审计发现：限时练此前只校验「年级可见」，逐生层（汇总表 / 学生详情 / T 分走势）
# 对任何持 `grades.view` 的账号全开 —— 任课教师能看到整个年级全部科目的分数和
# 总分，比普通考试的收口还宽，属越权。
# 现在统一走 `score_guard.ScoreScope`（与普通考试同一份口径）：
#   · 任课教师：本年级所教科目的分数 + 该生总分年级名次（不给总分分数、不给班名次）；
#   · 班主任：本班全科 + 总分 + 名次；
#   · 年级长及以上 / 校级 / 管理员：本年级 / 全校。
# **只在出结果时裁剪，不动缓存里的全量对象** —— 否则一次低权限请求会把收窄后的
# 对象写回缓存，后续正常请求反而看不到数据。

def _scope_of(user=None):
    """解析账号的成绩可见范围。

    ``user`` 缺省时取当前登录账号（Web 端各页都在登录态）；**没有请求上下文**
    （脚本 / 后台任务）返回 ``None`` = 不做裁剪，这类调用方自己已有边界。
    请求内取不到有效身份时按空范围处理（``resolve`` 失败同样 fail-closed）。
    """
    from app.modules.grades.services import score_guard
    if user is None:
        from flask import has_request_context
        if not has_request_context():
            return None
        from flask_login import current_user
        user = current_user
    try:
        return score_guard.resolve(user)
    except PermissionError:
        return score_guard.ScoreScope()


def _scope_key(scope):
    """范围指纹（缓存键用）：同一份分析结果对不同范围不能混用。"""
    if scope is None:
        return 'all'
    return (bool(scope.full_school), tuple(sorted(scope.grades)),
            tuple(sorted(scope.classes)), tuple(sorted(scope.teach)))


def _mask_summary_row(row, scope, grade):
    """按可见范围裁一行汇总数据（返回新 dict，绝不改缓存对象）。

    - 科目：只留该班可见学科（任课教师＝所教科；班主任本班＝全部）；
    - 总分：`class_total_visible` 为假时删掉总分 / 班级名次 / 总分 T；
    - 年级名次：`total_rank_visible` 为真时保留（任课教师只给名次，不给分数）。
    """
    item = dict(row)
    item['scores'] = dict(row.get('scores') or {})
    item['ts'] = dict(row.get('ts') or {})
    cls = row.get('class_name')
    allowed = scope.class_subjects(grade, cls)
    if allowed is not None:
        item['scores'] = {k: v for k, v in item['scores'].items() if k in allowed}
        item['ts'] = {k: v for k, v in item['ts'].items() if k in allowed}
    if not scope.class_total_visible(grade, cls):
        for key in ('total', 'rank_class', 't'):
            item.pop(key, None)
        if not scope.total_rank_visible(grade, cls):
            item.pop('rank_grade', None)
    return item


def subject_analysis(exam, subject, template, user=None, scope=None):
    """单科分析表：各班去差均分 + 各名次区间人数 + 合计行（纯内存，一次查询）

    2026-10-20 用户口径：
    - 「不分班」这类无班级归属的成绩不参与统计（表里不再出现该行）；
    - 均分统一给**去差均分**（按班型名额剔除该科末尾 N 人），最高/最低分不再展示。
    去差名额与全局对比同一份规则（``report_service.TRIM_RULES``）；但这里
    **不套「缺考先顶替名额」**——限时练常有成批学生不参加（一个班只来 40/49 人），
    套上去就永远剔不到人，用户要的是「每班去掉末尾 N 人」。

    2026-10-21 权限收口：``user`` / ``scope`` 给出时只统计可见班级，
    且只统计该班可见的学科（任课教师＝所教科；班主任本班＝全科）。
    """
    if scope is None:
        scope = _scope_of(user)
    teachers = _teacher_map(exam.grade, subject)
    quotas = _trim_quotas(exam.grade)
    rows = (TimedScore.query
            .filter_by(exam_id=exam.id, subject=subject)
            .order_by(TimedScore.class_name.asc()).all())
    # 无班级归属的不参与统计（先剔除，人数口径才准）
    rows = [r for r in rows if not _no_class((r.class_name or '').strip())]
    if scope is not None:
        rows = [r for r in rows
                if scope.can_view_subject(exam.grade,
                                          (r.class_name or '').strip(), subject)]
    # 分层按实考人数自适应：<100 人按 10 人一组；≥100 人丢掉超过人数的层
    layers = effective_layers(
        template.get_layers() if template else [], len(rows))
    by_class = {}
    total = _empty_row('合计')
    layer_scores = {}
    for r in rows:
        # 「高三01班」与「01班」按同一班统计（老数据兼容，新导入已归一化）
        cls = _norm_class(r.class_name)
        row = by_class.setdefault(cls, _empty_row(cls))
        _accumulate(row, r.score, r.rank_grade)
        _accumulate(total, r.score, r.rank_grade)
        for layer in layers:
            # 累积门槛：一名学生可以同时计入「前10 / 前20 / 前50 …」
            if _hit(layer, r.rank_grade):
                row['bands'].append(layer['name'])
                total['bands'].append(layer['name'])
                layer_scores.setdefault(layer['name'], []).append(r.score)
                row['band_scores'].setdefault(layer['name'], []).append(r.score)

    def _band_counts(item):
        """把命中的层名列表转成各层人数（按模板顺序）"""
        return [{'name': lay['name'], 'label': _layer_label(lay),
                 'count': item['bands'].count(lay['name'])} for lay in layers]

    def _stack_of(item):
        """堆积条用的**互斥区间**人数：本层累计 − 上一层累计。

        表里的层是累积门槛（前10 ⊂ 前20），直接堆叠会重复计数；
        堆积条要的是"本段多少人"，所以取相邻两层的差，末层之后单列一段。
        """
        counts = [b['count'] for b in item['bands']]
        segs = []
        prev = 0
        for i, band in enumerate(item['bands']):
            segs.append({'name': band['name'], 'count': counts[i] - prev})
            prev = counts[i]
        rest = item['count'] - prev
        if rest > 0:
            segs.append({'name': '未入层', 'count': rest})
        return segs

    def _row_layer_avgs(item):
        """本班各累积层的学生均分（去差均分表右侧列用）"""
        out = []
        for lay in layers:
            scores = item['band_scores'].get(lay['name'], [])
            out.append({'name': lay['name'], 'label': _layer_label(lay),
                        'avg': round(sum(scores) / len(scores), 1)
                        if scores else None})
        item.pop('band_scores', None)
        return out

    out_rows = []
    kept_all = []
    for cls in sorted(by_class):
        item = by_class[cls]
        item['bands'] = _band_counts(item)
        item['teacher'] = teachers.get(cls, '')
        item['stack'] = _stack_of(item)
        item['layer_avgs'] = _row_layer_avgs(item)
        out_rows.append(_finish(item, quotas.get(cls, 0)))
        kept_all.extend(item.pop('kept', []))       # 合计要按「各班去差后」合并
    total['bands'] = _band_counts(total)
    total['stack'] = _stack_of(total)
    total['layer_avgs'] = _row_layer_avgs(total)
    total = _finish(total, 0)
    if kept_all:
        # 去差均分的合计 = 各班去差后合并（与全局对比的「总计」同口径）
        total['trim_avg'] = round(sum(kept_all) / len(kept_all), 1)
        total['trim_n'] = total['count'] - len(kept_all)
    total.pop('kept', None)

    # 每个名次层的学生平均分（累积口径：前10 的均分、前20 的均分…）
    layer_avgs = []
    for lay in layers:
        scores = layer_scores.get(lay['name'], [])
        layer_avgs.append({
            'name': lay['name'], 'label': _layer_label(lay),
            'count': len(scores),
            'avg': round(sum(scores) / len(scores), 1) if scores else None,
        })

    return {
        'subject': subject,
        'template_name': template.name if template else '',
        'layers': [{'name': lay['name'], 'label': _layer_label(lay)}
                   for lay in layers],
        'rows': out_rows,
        'total': total,
        'layer_avgs': layer_avgs,
        'classes': [r['class_name'] for r in out_rows],
        'student_count': int(len(rows)),
        'class_count': len(out_rows),
    }





# ==================== T 分数（标准分） ====================
# T = 10 × Z + 50，Z = (个人分 - 群体均分) / 群体标准差。
# 作用：把"原始分"换成"在这批考生里的相对位置"，跨试卷 / 跨科目可比。
# 边界：样本少于 30 人不算（小样本的均分/标准差抖动大，T 分会误导）。
T_MIN_SAMPLE = 30


def _mean_sd(values):
    """(均值, 标准差)；样本不足 2 个返回 (None, None)"""
    n = len(values)
    if n < 2:
        return None, None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, var ** 0.5


def _t_map_of(score_map):
    """{键: T 分}：T = 10×(x−均分)÷标准差 + 50；样本不足 / 标准差≈0 返回空"""
    if len(score_map) < T_MIN_SAMPLE:
        return {}
    mean, sd = _mean_sd(list(score_map.values()))
    if not sd or sd < 1e-9:
        return {}
    return {k: round(10 * (v - mean) / sd + 50, 1)
            for k, v in score_map.items()}


def subject_t_map(exam_id, subject):
    """{学号: T 分}（单科口径，分析页给单科 T 用）"""
    rows = (TimedScore.query
            .filter_by(exam_id=int(exam_id), subject=subject)
            .with_entities(TimedScore.student_no, TimedScore.score).all())
    return _t_map_of({x[0]: x[1] for x in rows})


def _selection_map(student_numbers):
    """{学号: 该生**应学**科目集合}；未选科 / 全科 / 查不到的不进表（＝不过滤）。

    2026-10-20 用户口径（学生详情）：选完科就不该再显示没选的科目。
    2026-10-21 同一口径补到「学生分析」：往日全科场留下的物化生折线
    不该挂在文科班格里，否则看着像"文科生也在考理化生"。
    `subjects_of_selection` 已覆盖 3+1+2 各组合（物化生 / 史政地 / 物化地…）
    与「全科/不分科」（高一未选科）——后者返回 9 科，这里按不过滤处理。
    """
    nums = [n for n in student_numbers if n]
    if not nums:
        return {}
    from app.models import Student
    from app.models.grades import SUBJECTS, subjects_of_selection
    try:
        rows = (db.session.query(Student.student_number,
                                 Student.subject_selection)
                .filter(Student.student_number.in_(nums))
                .all())
    except Exception:                                  # noqa: BLE001
        return {}
    out = {}
    for no, sel in rows:
        sel = (sel or '').strip()
        if not sel:
            continue
        subs = subjects_of_selection(sel)
        if subs and len(subs) < len(SUBJECTS):
            out[no] = set(subs)
    return out


def _allowed_subjects(student_no):
    """该生应学科目集合（单个学生版）；不确定返回 None ＝ 不过滤"""
    return _selection_map([student_no]).get(student_no)


def _student_detail_base(exam, student_no):
    """单个学生的**全部场次**明细（学生分析详情页）。

    行 = 本年级每一场限时练，列 = 该生参加过的科目；
    单元格 = 分数 + 该科 T 分 + 年级名次；另给各科 T 分折线数据。
    """
    from app.models.grades import SUBJECTS
    from app.models.timed import TimedExam

    exams = (TimedExam.query.filter_by(grade=exam.grade)
             .order_by(TimedExam.exam_date.asc(), TimedExam.id.asc()).all())
    eids = [e.id for e in exams]
    rows = (TimedScore.query
            .filter(TimedScore.exam_id.in_(eids),
                    TimedScore.student_no == student_no).all())
    if not rows:
        return {'student_no': student_no, 'name': '', 'cls': '',
                'subjects': [], 'lines': [], 'chart': {}, 't_min': T_MIN_SAMPLE}

    # T 分要按「每场每科全体」算，所以仍取一次全量成绩（内存分组）
    full = (TimedScore.query.filter(TimedScore.exam_id.in_(eids))
            .with_entities(TimedScore.exam_id, TimedScore.subject,
                           TimedScore.student_no, TimedScore.score).all())
    groups = {}
    for eid, subj, no, score in full:
        groups.setdefault((eid, subj), {})[no] = score
    tmaps = {k: _t_map_of(m) for k, m in groups.items()}

    name = next((r.student_name for r in rows if r.student_name), '')
    cls = _norm_class(next((r.class_name for r in rows if r.class_name), ''))
    subjects = [s for s in SUBJECTS if any(r.subject == s for r in rows)]
    allowed = _allowed_subjects(student_no)
    if allowed:
        subjects = [s for s in subjects if s in allowed]

    by_exam = {}
    for r in rows:
        item = by_exam.setdefault(r.exam_id, {'cells': {}})
        item['cells'][r.subject] = {
            'score': r.score, 'rank': r.rank_grade,
            't': tmaps.get((r.exam_id, r.subject), {}).get(student_no),
        }
    emap = {e.id: e for e in exams}
    lines = [{'exam': emap[eid], **data} for eid, data in by_exam.items()]
    lines.sort(key=lambda x: (x['exam'].exam_date, x['exam'].id))

    # 走势数据：{科目: [[完整日期, T], …]}
    # 日期必须带年份 —— 一届限时练跨三个学年，只用 %m-%d 会让
    # 「2025-09-10」和「2024-09-10」落到同一个类目上，折线错位（2026-10-20 实测：
    # 43 场被折叠成十几个 x 点，看起来像几根乱线）。
    chart = {}
    for subj in subjects:
        pts = []
        for it in lines:
            cell = it['cells'].get(subj)
            if cell and cell['t'] is not None:
                pts.append([it['exam'].exam_date.strftime('%Y-%m-%d'),
                            cell['t']])
        if pts:
            chart[subj] = pts

    # 每科最近一次 T（卡片上直接看）
    latest = {}
    for s in subjects:
        pts = chart.get(s) or []
        latest[s] = pts[-1][1] if pts else None

    # 学科档案（2026-10-20 用户要求「雷达图看薄弱学科」）：
    # latest=最近一场 T；avg=该科历史平均 T；sd=波动（越大越不稳）；
    # trend=最近 3 场均值 − 之前 3 场均值（正=在进步）。
    stats = []
    for s in subjects:
        vals = [p[1] for p in (chart.get(s) or [])]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        sd = (sum((v - avg) ** 2 for v in vals) / len(vals)) ** 0.5
        recent, prev = vals[-3:], vals[-6:-3]
        trend = ((sum(recent) / len(recent)) - (sum(prev) / len(prev))
                 if prev else 0.0)
        stats.append({
            'subject': s, 'latest': round(vals[-1], 1),
            'avg': round(avg, 1), 'sd': round(sd, 1), 'n': len(vals),
            'trend': round(trend, 1),
            # 47 以下算薄弱（T 50 是年级平均，47 已经低于平均 3 分）；
            # 60 以上算优势（高一个档次）
            'weak': vals[-1] < 47, 'strong': vals[-1] >= 60,
        })
    stats.sort(key=lambda x: x['latest'])          # 弱的排前面，一眼看到短板
    by_subj = {x['subject']: x for x in stats}
    radar_subj = [s for s in subjects if s in by_subj]
    radar = {'subjects': radar_subj,
             'latest': [by_subj[s]['latest'] for s in radar_subj],
             'avg': [by_subj[s]['avg'] for s in radar_subj]}

    return {'student_no': student_no, 'name': name, 'cls': cls,
            'subjects': subjects, 'lines': lines, 'chart': chart,
            'latest': latest, 'stats': stats, 'radar': radar,
            'weak': [x for x in stats if x['weak']],
            't_min': T_MIN_SAMPLE}


def _mask_student_detail(data, allowed):
    """按可见科目裁剪学生详情（不改缓存里的原始数据）。"""
    subjects = [s for s in data['subjects'] if s in allowed]
    keep = set(subjects)
    lines = [{'exam': it['exam'],
              'cells': {k: v for k, v in it['cells'].items() if k in keep}}
             for it in data['lines']]
    chart = {k: v for k, v in data['chart'].items() if k in keep}
    latest = {k: v for k, v in data['latest'].items() if k in keep}
    stats = [x for x in data['stats'] if x['subject'] in keep]
    by_subj = {x['subject']: x for x in stats}
    radar_subj = [s for s in subjects if s in by_subj]
    return {**data, 'subjects': subjects, 'lines': lines, 'chart': chart,
            'latest': latest, 'stats': stats,
            'radar': {'subjects': radar_subj,
                      'latest': [by_subj[s]['latest'] for s in radar_subj],
                      'avg': [by_subj[s]['avg'] for s in radar_subj]},
            'weak': [x for x in stats if x['weak']],
            'subject_limited': True}


def student_detail(exam, student_no, page=1, user=None):
    """学生详情（按年级 + 学号缓存，重复点同一人不再扫全表）。

    2026-10-21 用户口径：逐场成绩表按日期**倒序**（新的在上），10 条一页。
    缓存里仍保留按时间升序的 lines（走势图/最新 T/统计都依赖这个顺序），
    只在返回时切出当页，避免每翻一页重算整份数据。

    2026-10-21 权限收口：先校验该生所在班级可见（不可见直接 ``PermissionError``，
    防止靠 URL 枚举学号），再按班级口径裁掉不可见科目的分数 / T 分 / 走势 / 学科档案；
    分页在裁剪之后做。
    """
    data = _cached(('detail', exam.grade, student_no),
                   lambda: _student_detail_base(exam, student_no))
    scope = _scope_of(user)
    if scope is not None:
        cls = data.get('cls') or ''
        if not scope.can_view_class(exam.grade, cls):
            raise PermissionError(
                f'无权查看 {exam.grade} {cls or "（无班级）"} 的学生成绩：'
                '任课教师仅能查看所教年级所教科目的成绩，班主任仅能查看本班成绩')
        allowed = scope.class_subjects(exam.grade, cls)
        if allowed is not None:
            data = _mask_student_detail(data, allowed)
    return _paged_lines(data, page)


DETAIL_PAGE_SIZE = 10


def _paged_lines(base, page):
    """逐场成绩：日期倒序 + 10 条一页（page 越界自动收敛到首/末页）"""
    if not base.get('lines'):
        return dict(base, page=1, pages=1, count=0,
                    page_size=DETAIL_PAGE_SIZE)
    newest = sorted(base['lines'],
                    key=lambda x: (x['exam'].exam_date, x['exam'].id),
                    reverse=True)
    total = len(newest)
    pages = max(1, (total + DETAIL_PAGE_SIZE - 1) // DETAIL_PAGE_SIZE)
    try:
        page_no = int(page or 1)
    except (TypeError, ValueError):
        page_no = 1
    page_no = min(max(page_no, 1), pages)
    start = (page_no - 1) * DETAIL_PAGE_SIZE
    out = dict(base)
    out.update({'lines': newest[start:start + DETAIL_PAGE_SIZE],
                'page': page_no, 'pages': pages, 'count': total,
                'page_size': DETAIL_PAGE_SIZE})
    return out


TREND_MAX_POINTS = 8      # 迷你折线最多回溯几场


def _spark_path(points, w=72, h=22):
    """把 [(日期, T)] 折成 SVG polyline 坐标串（宽 w 高 h，纵向归一化）"""
    if len(points) < 2:
        return ''
    ts = [p[1] for p in points]
    lo, hi = min(ts), max(ts)
    span = (hi - lo) or 1.0
    step = w / (len(ts) - 1)
    return ' '.join(
        f'{round(i * step, 1)},{round(h - 2 - (t - lo) / span * (h - 4), 1)}'
        for i, t in enumerate(ts))


def _trends_base(exam):
    """全年级学生 × 各科 **T 分轨迹**（按年级缓存，班级筛选与分页在调用方做）。

    性能取舍：
    - 一次把本年级**全部**限时练成绩取出来（6 场 ≈ 1.4 万行），在内存里按
      (场次, 科目) 分组算 T 分，避免 N 场 × M 科 的循环查询；
    - 结果缓存（见 ``_cached``），翻页 / 换班级不再重算。
    """
    from app.models.grades import SUBJECTS
    from app.models.timed import TimedExam

    exams = (TimedExam.query.filter_by(grade=exam.grade)
             .order_by(TimedExam.exam_date.asc(), TimedExam.id.asc()).all())
    eids = [e.id for e in exams]
    dates = {e.id: e.exam_date for e in exams}
    rows = (TimedScore.query.filter(TimedScore.exam_id.in_(eids))
            .with_entities(TimedScore.exam_id, TimedScore.subject,
                           TimedScore.student_no, TimedScore.student_name,
                           TimedScore.class_name, TimedScore.score).all())

    groups = {}
    for eid, subj, no, _name, _cls, score in rows:
        groups.setdefault((eid, subj), {})[no] = score
    tmaps = {k: _t_map_of(m) for k, m in groups.items()}

    cur = [r for r in rows if r[0] == exam.id]
    # 2026-10-21 用户口径：一列不缺 —— 限时练场次可能是全 9 科，也可能是
    # 物化生这类选科组合，列固定按 9 科展开，本场没考的科留「—」，
    # 导入后一眼能看出哪科还没导。
    subjects = list(SUBJECTS)

    info = {}
    for eid, subj, no, name, cls, score in cur:
        it = info.setdefault(no, {'no': no, 'name': name or '',
                                  'cls': _norm_class(cls), 'now': {},
                                  'scores': {}})
        it['now'][subj] = tmaps.get((eid, subj), {}).get(no)
        it['scores'][subj] = score

    series = {}
    for (eid, subj), m in tmaps.items():
        if subj not in subjects:
            continue
        for no, t in m.items():
            if no in info:
                series.setdefault((no, subj), []).append((dates[eid], t))

    # 列固定 9 科，但**折线只画该生选科内的科目**：文科班的物化生格不该挂
    # 高一全科场的历史折线（看着像"文科生也在考理化生"），本场没分就显示「—」。
    allowed = _selection_map(list(info.keys()))
    out = []
    for no, it in info.items():
        allow = allowed.get(no)
        pts = {}
        for s in subjects:
            if allow is not None and s not in allow:
                pts[s] = []
            else:
                pts[s] = sorted(series.get((no, s), []))[-TREND_MAX_POINTS:]
        it['total'] = (round(sum(it['scores'].values()), 1)
                       if it['scores'] else None)
        out.append({
            **it, 'pts': pts,
            # 行内迷你折线：后端直接给 SVG 坐标串，前端不画图
            'paths': {s: _spark_path(p) for s, p in pts.items()},
        })

    # 本场总分 T 高的在前，方便老师先看头部学生
    def _sort_key(x):
        vals = [v for v in x['now'].values() if v is not None]
        return (-(sum(vals) / len(vals)) if vals else 0, x['cls'], x['no'])

    out.sort(key=_sort_key)
    return {'subjects': subjects, 'rows': out}


def student_trends(exam, class_name='', page=1, per_page=20, user=None):
    """学生分析页：班级筛选 + 分页（全量结果按年级缓存，翻页不再重算）

    2026-10-21 权限收口：只出行级可见班级；每行的科目键按该班口径裁剪
    （表头取可见班级的并集，行里没有的科目显示「—」）。
    """
    base = _cached(('trends', exam.grade), lambda: _trends_base(exam))
    scope = _scope_of(user)
    grade = exam.grade
    subjects = list(base['subjects'])
    out = base['rows']
    if scope is not None:
        out = [x for x in out if scope.can_view_class(grade, x['cls'])]
        vis_classes = {x['cls'] for x in out}
        subjects = ([s for s in subjects
                     if any(scope.can_view_subject(grade, c, s) for c in vis_classes)]
                    if vis_classes else [])
        rows = []
        for x in out:
            allowed = scope.class_subjects(grade, x['cls'])
            if allowed is None:
                rows.append(x)
                continue
            rows.append({**x,
                         'now': {k: v for k, v in x['now'].items() if k in allowed},
                         'pts': {k: v for k, v in x['pts'].items() if k in allowed},
                         'paths': {k: v for k, v in x['paths'].items()
                                   if k in allowed}})
        out = rows
    if class_name:
        out = [x for x in out if x['cls'] == class_name]

    total = len(out)
    pages = max(1, (total + per_page - 1) // per_page)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return {'subjects': subjects, 'rows': out[start:start + per_page],
            'total': total, 'page': page, 'pages': pages,
            'per_page': per_page, 'class_name': class_name,
            't_min': T_MIN_SAMPLE}

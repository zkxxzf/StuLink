# StuLink v1.19.49.52 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科组长（2026-09-25 建，2026-10-14 由「备课组长」升级）。

`/academic/leaders`：学科组长矩阵 —— 行＝学科（含综合组），
列＝高一 / 高二 / 高三 / 教研组（不分年级），格子里选本学科教师。

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校；
- **综合组组长**（用户口径，同日二次修订）：整个综合组只设 1 名 —— 体育 / 音乐 / 美术 /
  信息技术 / 通用技术 / 心理 六科**合用一位**，不分年级、不分学科，
  矩阵里综合组只渲染一行一格（落库 `subject='综合组'` / `grade=''`）；
- 三类组长都必须是本学科教师（任课教师映射为唯一依据，课表兜底；综合组＝六科并集）。

落库在 academic.db.subject_leaders。

2026-10-14：原「职责 / 组范围」明细页签、新增/编辑弹窗、单条保存与删除接口
**整功能删除**（用户确认从未使用），页面只保留矩阵维护 + 导出 + 打印。

已下线（2026-10-10）：
- 任课安排（原 `/academic/duty`）：教师-学科关系以「任课教师映射」
  （TeacherSubjectLink）为唯一依据；
- 晚自习值班（原 `/academic/night-duty`、`/academic/schedule/<sid>/night-duty`）：
  晚自习在「全校总课表」里本就按节次列出并带值班教师，独立页面重复，整功能删除。
"""
import json
import logging
from datetime import datetime

from flask import jsonify, render_template, request, send_file, url_for
from flask_login import login_required, current_user

from app.modules.academic import bp
from app.modules.academic.services import subject_leader_service as svc
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation
from app.utils.text_guard import safe_download_name

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
logger = logging.getLogger(__name__)

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


# ── 学科组长矩阵 ───────────────────────────────────────────────────────────

def _json_body():
    """请求体 → dict（兼容标准 JSON 与旧版 jQuery 以表单形式发送的 JSON 文本）。"""
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    raw = (request.get_data(as_text=True) or '').strip()
    if not raw.startswith('{'):
        # CSRF 校验会先把 body 当表单解析，此时 JSON 文本会成为表单字段名
        raw = next((k.strip() for k in request.form if k.strip().startswith('{')), '')
    if raw.startswith('{'):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _context():
    """当前查看的方案 → (plans, school_year, term)。

    优先级：URL 的 school_year/term（导出链接、收藏、测试都在用）→ 当前学期方案
    → 最近的一条方案 → 最近有登记的学年。

    2026-10-14 第四轮审计修复（SEC3-01）：本函数**不再写库** —— 原实现在这里自动
    登记方案（"没有方案就自动登记"），使一次 GET 页面访问 / 导出就产生 INSERT；
    GET 只读是硬原则，方案登记只在 POST 路径（矩阵保存 subject_leader_matrix_save）
    进行。school_year/term 同时加长度上限。
    """
    school_year = (request.args.get('school_year') or '').strip()[:20]
    term = (request.args.get('term') or '').strip()[:20]
    if not school_year:
        cur = svc.current_plan()
        if cur is not None:
            school_year, term = cur.school_year, cur.term or ''
    if not school_year:
        first = svc.list_plans()
        if first:
            school_year, term = first[0]['school_year'], first[0]['term']
    if not school_year:
        years = svc.leader_years()
        school_year = years[0] if years else ''
    return svc.list_plans(), school_year, term


@bp.route('/leaders')
@login_required
@perm_required('academic.view')
def subject_leaders():
    """学科组长矩阵（行＝学科、列＝高一/高二/高三/教研组，格内选本学科教师）。"""
    plans, school_year, term = _context()
    return render_template('academic/subject_leaders.html',
                           matrix=svc.matrix_data(school_year, term),
                           plans=plans, school_year=school_year, term=term,
                           can_edit=current_user.has_perm('academic.edit'))


@bp.route('/leaders/matrix/save', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_matrix_save():
    """矩阵批量保存：{'school_year','term','cells': {'高一|语文': 教师编号 或 ''}}

    空值＝清空该格（删除这条登记）；未提交的格子不动。保存前逐格校验「组长是本学科教师」。
    """
    data = _json_body()
    try:
        changes, skipped = svc.save_matrix(data, operator_id=current_user.id)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    # 2026-10-14 方案：保存即登记该学期的方案（幂等；页面下拉 / 历史记录里可见）
    svc.ensure_plan(data.get('school_year') or '', data.get('term') or '',
                    operator_id=current_user.id)
    msg = (f'已保存：新增 {changes["set"]} 条 / 变更 {changes["changed"]} 条'
           f' / 清空 {changes["cleared"]} 条')
    if skipped:
        msg += f'；{len(skipped)} 格未保存（{skipped[0]}）'
    log_operation(current_user, '维护', '学科组长', None,
                  f'{(data.get("school_year") or "")} {data.get("term") or "整学年"} '
                  f'矩阵保存：{json.dumps(changes, ensure_ascii=False)}'
                  + (f'；忽略 {skipped}' if skipped else ''),
                  module='academic')
    return jsonify(success=True, message=msg, changes=changes, skipped=skipped)


@bp.route('/leaders/export')
@login_required
@perm_required('academic.view')
def subject_leader_export():
    """导出学科组长名单 Excel（备课组长 + 教研组长）"""
    _plans, school_year, term = _context()
    leaders = svc.leaders_of_term(school_year, term) if school_year else []
    buf = svc.export_leaders_workbook(leaders, school_year=school_year, term=term)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    # 2026-10-14 第四轮审计修复（SEC-05）：school_year / term 来自 request.args，
    # 直接拼进 Content-Disposition 属未消毒的响应头输入 —— 统一走 safe_download_name。
    name = safe_download_name(
        f'学科组长名单_{school_year or ""}_{term or "整学年"}_{stamp}.xlsx',
        '学科组长名单.xlsx')
    return send_file(buf, as_attachment=True, download_name=name, mimetype=_XLSX_MIME)


# ── 方案（历史记录，2026-10-14）────────────────────────────────────────────
# 用户口径：每学期组长会变，要有记录 —— 历史记录里可①使用历史方案 ②新建方案。

@bp.route('/leaders/plans')
@login_required
@perm_required('academic.view')
def subject_leader_plans():
    """方案清单（「历史记录」弹窗的数据源）。"""
    return jsonify(success=True, data={'items': svc.list_plans()})


@bp.route('/leaders/plans/create', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_plans_create():
    """新建方案（可复制当前学期的登记 / 空白 / 复制某历史方案，可选立即使用）。"""
    data = _json_body()
    try:
        plan = svc.create_plan(
            name=data.get('name') or '', school_year=data.get('school_year') or '',
            term=data.get('term') or '', note=data.get('note') or '',
            copy_from=data.get('copy_from') or 'current',
            source_year=data.get('source_year') or '',
            source_term=data.get('source_term') or '',
            use_now=bool(data.get('use_now', True)), operator_id=current_user.id)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
    log_operation(current_user, '新建', '学科组长方案', None,
                  f'方案「{plan.name}」（{plan.school_year} {plan.term or "整学年"}，'
                  f'{"已设为当前" if plan.is_current else "未启用"}）', module='academic')
    return jsonify(success=True, data={
        'id': plan.id, 'is_current': plan.is_current,
        'redirect': url_for('academic.subject_leaders', school_year=plan.school_year,
                            term=plan.term)},
        message=f'已新建方案「{plan.name}」')


@bp.route('/leaders/plans/use', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_plans_use():
    """使用某方案：设为当前学期方案并跳转到它。"""
    data = _json_body()
    try:
        plan = svc.use_plan(data.get('plan_id'), operator_id=current_user.id)
    except (ValueError, TypeError) as e:
        return jsonify(success=False, message=str(e) or '参数不正确'), 400
    log_operation(current_user, '使用', '学科组长方案', None,
                  f'切换为「{plan.name}」', module='academic')
    return jsonify(success=True, message=f'已切换到「{plan.name}」',
                   data={'id': plan.id,
                         'redirect': url_for('academic.subject_leaders',
                                             school_year=plan.school_year,
                                             term=plan.term)})


@bp.route('/leaders/plans/rename', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_plans_rename():
    """重命名方案 / 改说明。"""
    data = _json_body()
    try:
        plan = svc.rename_plan(data.get('plan_id'), name=data.get('name') or '',
                               note=data.get('note'), operator_id=current_user.id)
    except (ValueError, TypeError) as e:
        return jsonify(success=False, message=str(e) or '参数不正确'), 400
    log_operation(current_user, '重命名', '学科组长方案', None, f'方案改为「{plan.name}」',
                  module='academic')
    return jsonify(success=True, message='已保存', data=plan.to_dict())


@bp.route('/leaders/plans/delete', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_plans_delete():
    """删除方案（连同该学期的组长登记一起删除）。"""
    data = _json_body()
    try:
        plan, removed = svc.delete_plan(data.get('plan_id'), operator_id=current_user.id)
    except (ValueError, TypeError) as e:
        return jsonify(success=False, message=str(e) or '参数不正确'), 400
    log_operation(current_user, '删除', '学科组长方案', None,
                  f'方案「{plan.name}」（{plan.school_year} {plan.term or "整学年"}，'
                  f'连带删除登记 {removed} 条）', module='academic')
    return jsonify(success=True,
                   message=f'已删除方案「{plan.name}」及其 {removed} 条组长登记')

# StuLink v1.19.48.1 2026-10-14
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科组长（2026-09-25 建，2026-10-14 由「备课组长」升级）。

`/academic/leaders`：学科组长矩阵 —— 行＝学科（含综合组），
列＝高一 / 高二 / 高三 / 教研组（不分年级），格子里选本学科教师。

- **备课组长**：年级 × 学科（高一 / 高二 / 高三 各学科各一名），年级必填；
- **教研组长**：只按学科（不分年级），年级留空＝全校；
- 两类组长都必须是本学科教师（任课教师映射为唯一依据，课表兜底）；
- 体育 / 音乐 / 美术 / 信息技术 / 通用技术 / 心理 合并展示为「综合组」。

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

from flask import jsonify, render_template, request, send_file
from flask_login import login_required, current_user

from app.modules.academic import bp
from app.modules.academic.services import subject_leader_service as svc
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 2026-10-10 架构审计修复：本文件原有静默 `except Exception: pass`，失败不可观测。
# 统一加 logger 记录 warning（仅留痕，不改变任何降级行为与返回值）。
logger = logging.getLogger(__name__)

_XLSX_MIME = ('application/vnd.openxmlformats-officedocument'
              '.spreadsheetml.sheet')


# ── 学科组长矩阵 ───────────────────────────────────────────────────────────

def _context():
    """学年 / 学期口径：默认取最近有登记的学年，其次当前学年。"""
    years = svc.leader_years()
    school_year = (request.args.get('school_year') or '').strip()
    if not school_year:
        school_year = years[0] if years else ''
    term = (request.args.get('term') or '').strip()
    return years, school_year, term


@bp.route('/leaders')
@login_required
@perm_required('academic.view')
def subject_leaders():
    """学科组长矩阵（行＝学科、列＝高一/高二/高三/教研组，格内选本学科教师）。"""
    years, school_year, term = _context()
    return render_template('academic/subject_leaders.html',
                           matrix=svc.matrix_data(school_year, term),
                           years=years, school_year=school_year, term=term,
                           can_edit=current_user.has_perm('academic.edit'))


@bp.route('/leaders/matrix/save', methods=['POST'])
@login_required
@perm_required('academic.edit')
def subject_leader_matrix_save():
    """矩阵批量保存：{'school_year','term','cells': {'高一|语文': 教师编号 或 ''}}

    空值＝清空该格（删除这条登记）；未提交的格子不动。保存前逐格校验「组长是本学科教师」。
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        # 兼容「JSON 文本 + 表单 content-type」的老写法（jQuery $.post 不声明 JSON）：
        # 此时 CSRF 校验已把 body 当表单解析，get_data() 取不到原文，
        # 但 JSON 文本会原样成为表单的**字段名**，从 request.form 的键里捞回来。
        raw = (request.get_data(as_text=True) or '').strip()
        if not raw.startswith('{'):
            raw = next((k.strip() for k in request.form if k.strip().startswith('{')), '')
        try:
            data = json.loads(raw) if raw.startswith('{') else {}
        except (ValueError, TypeError):
            data = {}
    try:
        changes, skipped = svc.save_matrix(data, operator_id=current_user.id)
    except ValueError as e:
        return jsonify(success=False, message=str(e)), 400
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
    years, school_year, term = _context()
    leaders = svc.leaders_of_term(school_year, term) if school_year else []
    buf = svc.export_leaders_workbook(leaders, school_year=school_year, term=term)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    name = f'学科组长名单_{school_year or ""}_{term or "整学年"}_{stamp}.xlsx'
    return send_file(buf, as_attachment=True, download_name=name, mimetype=_XLSX_MIME)

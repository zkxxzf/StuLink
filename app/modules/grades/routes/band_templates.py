# StuLink v1.19.62.65 2026-10-10
# 分层模板管理：自定义模板 + 考试与模板绑定
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user
import json

from app.extensions import db
from app.models import DictCategory
from app.models.grades import Exam, ExamBand, BandTemplate
from app.modules.grades import bp
from app.modules.grades.services.exam_guard import assert_exam_visible
from app.utils.decorators import perm_required
from app.utils.helpers import log_operation

# 内置模板：首次访问时自动写入（幂等），保证「四层 / 默认模板」一直在
# v1.19.62.65 用户口径：默认模板改为 985线 / 211线 / 特控线 / 本科线 四层，
# 低于本科线即「未上线」（不单列一层，由“不落任何层”自然得出）。
BUILTIN_LAYERS_NAME = '默认模板'      # 默认模板名（内置不可删）
BUILTIN_TEMPLATES = [
    {'name': '四层', 'lower_mode': 'ratio', 'sort_order': 1, 'remark': '常用：按比例分层',
     'layers': [{'name': '优秀', 'ratio': 20}, {'name': '良好', 'ratio': 60},
                {'name': '及格', 'ratio': 95}, {'name': '待提升', 'ratio': 0}]},
    # seq=1 最高：985线 → 211线 → 特控线 → 本科线；本科线以下 = 未上线
    {'name': BUILTIN_LAYERS_NAME, 'lower_mode': 'score', 'sort_order': 2,
     'remark': '默认：985/211/特控/本科 四层，本科线以下即未上线',
     'layers': [{'name': '985线', 'ratio': None}, {'name': '211线', 'ratio': None},
                {'name': '特控线', 'ratio': None}, {'name': '本科线', 'ratio': None}]},
]
# 旧版内置名 → 新名（历史上叫「高考线」，含清北/未上线六层）：就地迁移，不丢绑定
LEGACY_RENAME = {'高考线': BUILTIN_LAYERS_NAME}


def ensure_builtin_templates():
    """写入/补齐内置模板（幂等）

    - 同名不存在 → 新建
    - 旧名「高考线」→ 重命名为「默认模板」并刷新为四层（保留 id，考试绑定不断）
    - 已是内置同名模板 → 层级与用户自定义可能不同，不强制覆盖；
      但默认模板必须是四层，所以只对它做结构校正（层名按规范重写）。
    """
    for old, new in LEGACY_RENAME.items():
        obj = BandTemplate.query.filter_by(name=old).first()
        if obj and not BandTemplate.query.filter_by(name=new).first():
            obj.name = new
            obj.is_builtin = True
            obj.layers_json = json.dumps(
                BUILTIN_TEMPLATES[1]['layers'], ensure_ascii=False)
            obj.lower_mode = BUILTIN_TEMPLATES[1]['lower_mode']
            obj.remark = BUILTIN_TEMPLATES[1]['remark']
    for t in BUILTIN_TEMPLATES:
        obj = BandTemplate.query.filter_by(name=t['name']).first()
        if obj:
            # 默认模板被改坏（层数/层名不符）时校正回规范四层
            if (t['name'] == BUILTIN_LAYERS_NAME
                    and [x.get('name') for x in obj.get_layers()] !=
                        [x['name'] for x in t['layers']]):
                obj.set_layers(t['layers'])
                obj.lower_mode = t['lower_mode']
                obj.is_builtin = True
            continue
        obj = BandTemplate(name=t['name'], lower_mode=t['lower_mode'],
                           remark=t['remark'], is_builtin=True,
                           sort_order=t['sort_order'])
        obj.set_layers(t['layers'])
        db.session.add(obj)
    # v1.19.62.65 修正 bug：历史上给 is_builtin 加字段时，迁移把已有行全标成了内置，
    # 导致用户手工建的模板也没有删除按钮。内置与否以**名字**为唯一依据：
    # 不在规范定义（含旧内置名）里的-list 全部归为可删。
    builtin_names = {t['name'] for t in BUILTIN_TEMPLATES} | set(LEGACY_RENAME)
    for obj in BandTemplate.query.all():
        if obj.name not in builtin_names and obj.is_builtin:
            obj.is_builtin = False
    db.session.commit()


@bp.route('/band-templates')
@login_required
@perm_required('grades.settings')
def band_templates_page():
    ensure_builtin_templates()
    return render_template('grades/band_templates.html')


def _exam_type_options():
    """可选考试类型：字典 exam_type + 库里已出现过的类型（兼容没录字典的情况）"""
    opts = []
    cat = DictCategory.query.filter_by(code='exam_type').first()
    if cat:
        opts = [i.value for i in cat.items.filter_by(is_active=True)
                .order_by('sort_order').all()]
    for (t,) in db.session.query(Exam.exam_type).distinct().all():
        if t and t not in opts:
            opts.append(t)
    return opts


def _norm_line(name):
    """线名归一化：去首尾空白 + 去尾部「线」（「特控线」≈「特控」）

    与划线分层页 bands.html 回填的 norm() 同口径 —— 导入的线名与模板层名
    常差一个「线」字，精确比较会对不上。
    """
    t = str(name or '').strip()
    return t[:-1] if t.endswith('线') else t


@bp.route('/api/band-templates')
@login_required
@perm_required('grades.view')      # 只读列表：划线页（含教师只读视图）要显示「本场绑定模板」
def api_band_templates():
    """模板列表；带 exam_id 时返回该考试已绑定的模板，未绑定则推荐一个

    推荐优先级（2026-10-13 口径："先知道是哪个模板，再填比例/数值"）：
      ① 已绑定 → bound_id
      ② 本场已保存划线的层名（归一化）与某模板层名重叠最多 → 按内容推荐
      ③ 按考试适用类型推荐（原口径）
    suggest_reason: 'lines' | 'exam_type' | ''（推荐依据，前端展示用）
    suggest_hit / suggest_extra：命中几条已保存的线 / 模板里还有几层待填（②用）

    2026-10-15：本接口为只读（模块定义，无学生成绩），门槛与划线页一致放开到
    grades.view —— 否则教师只读视图加载不到「本场绑定模板」这一行。
    写操作（save/delete/bind）仍为 grades.settings。ensure_builtin_templates()
    是幂等初始化（只在缺失时补齐内置模板），不构成越权写。
    """
    ensure_builtin_templates()
    exam_id = request.args.get('exam_id', type=int)
    rows = (BandTemplate.query.order_by(BandTemplate.sort_order, BandTemplate.id).all())
    data = [{
        'id': t.id, 'name': t.name, 'lower_mode': t.lower_mode,
        'layers': t.get_layers(), 'remark': t.remark or '',
        'is_builtin': bool(t.is_builtin), 'exam_types': t.get_exam_types(),
    } for t in rows]

    bound = suggested = None
    suggest_reason = ''
    suggest_hit = suggest_extra = 0
    exam_type = ''
    if exam_id:
        exam = Exam.query.get(exam_id)
        if exam:
            bound = exam.band_template_id()
            exam_type = exam.exam_type or ''
            if not bound:
                # ② 按本场已保存划线的层名推荐
                saved = {_norm_line(n) for (n,) in
                         db.session.query(ExamBand.name)
                         .filter(ExamBand.exam_id == exam_id).all()}
                saved.discard('')
                if len(saved) >= 2:
                    # 排序键（越小越合适）：
                    #   ① missing = 已保存的线里模板没有的 → 越少越好（别丢线）
                    #   ② extra   = 模板里没有对应划线值的层 → 越少越好（别多出一堆空行）
                    #   ③ 内置模板优先（标准口径），最后按 sort_order/id 稳定兜底
                    # 2026-07 那种「AI 导入遗留模板」与「默认模板」命中数相同时，
                    # 旧实现按 sort_order 先到先得（sort_order 为 NULL 的排最前）→
                    # 推荐了垃圾模板；现在按上面三条比，默认模板胜出。
                    best, best_key = None, None
                    for t in rows:
                        lay = {_norm_line(x.get('name')) for x in t.get_layers()}
                        if len(saved & lay) < 2:      # 至少命中 2 层才算对得上
                            continue
                        key = (len(saved - lay), len(lay - saved),
                               0 if t.is_builtin else 1,
                               t.sort_order if t.sort_order is not None else 999, t.id)
                        if best_key is None or key < best_key:
                            best, best_key = t.id, key
                    if best:
                        suggested, suggest_reason = best, 'lines'
                        suggest_extra = best_key[1]
                        suggest_hit = len(saved) - best_key[0]
            if not suggested and not bound and exam_type:
                # ③ 未绑定时按「适用考试类型」推荐：取第一个声明适用该类型的模板
                for t in rows:
                    if exam_type in t.get_exam_types():
                        suggested, suggest_reason = t.id, 'exam_type'
                        break
            if not suggested and not bound:
                # ④ 兜底：连类型都没匹配上 → 用内置「默认模板」（985线/211线/特控线/本科线）占位，
                #    而不是落到「不套用 + 默认四层（优秀/良好/及格/待提升）」——
                #    那正是"表格里层名乱七八糟、跟模板对不上"的来源（用户口径 2026-10-13）
                fb = next((t for t in rows if t.name == BUILTIN_LAYERS_NAME), None)
                if fb:
                    suggested, suggest_reason = fb.id, 'default'
    return jsonify(success=True, data={'templates': data, 'bound_id': bound,
                                       'suggested_id': suggested,
                                       'suggest_reason': suggest_reason,
                                       'suggest_hit': suggest_hit,
                                       'suggest_extra': suggest_extra,
                                       'exam_type': exam_type,
                                       'exam_type_options': _exam_type_options()})


@bp.route('/api/band-templates/save', methods=['POST'])
@login_required
@perm_required('grades.settings')
def api_band_template_save():
    payload = request.get_json(force=True, silent=True) or {}
    name = str(payload.get('name') or '').strip()
    if not name:
        return jsonify(success=False, message='请填写模板名称')
    layers = payload.get('layers') or []
    layers = [{'name': str(x.get('name') or '').strip(),
               'ratio': (float(x['ratio']) if x.get('ratio') not in (None, '') else None)}
              for x in layers if str(x.get('name') or '').strip()]
    if not layers:
        return jsonify(success=False, message='请至少填写一个层名')
    if len({x['name'] for x in layers}) != len(layers):
        return jsonify(success=False, message='层名不能重复')

    tid = payload.get('id')
    obj = BandTemplate.query.get(tid) if tid else None
    if not obj:
        dup = BandTemplate.query.filter_by(name=name).first()
        if dup and str(dup.id) != str(tid or ''):
            return jsonify(success=False, message=f'模板名「{name}」已存在')
        mx = db.session.query(db.func.max(BandTemplate.sort_order)).scalar() or 0
        obj = BandTemplate(name=name, created_by=current_user.id, sort_order=mx + 1)
    else:
        obj.name = name
    lm = payload.get('lower_mode')
    obj.lower_mode = lm if lm in ('score', 'ratio', 'rank') else 'score'
    obj.remark = str(payload.get('remark') or '')[:100]
    obj.set_layers(layers)
    # 适用考试类型（空=通用）；用于按考试类型自动默认匹配模板
    obj.set_exam_types([str(x).strip() for x in (payload.get('exam_types') or [])
                        if str(x).strip()])
    db.session.add(obj)
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：划线模板改动会影响后续所有考试划线，
    # 原先零业务日志。记录层定义，便于回溯"当时这套线是怎么配的"。
    log_operation(current_user, '保存', '划线模板', obj.id,
                  f'{name}（{obj.lower_mode}）：'
                  + '、'.join(f'{x["name"]}={x["ratio"]}' for x in layers),
                  module='grades')
    return jsonify(success=True, data={'id': obj.id}, message='模板已保存')


@bp.route('/api/band-templates/<int:tid>/delete', methods=['POST'])
@login_required
@perm_required('grades.settings')
def api_band_template_delete(tid):
    obj = BandTemplate.query.get_or_404(tid)
    if obj.is_builtin:
        return jsonify(success=False, message='内置模板不能删除')
    # 已绑定的考试解除绑定，避免残留悬空 id
    # 2026-10-13 全面审计 M-1 复核说明：这里**有意不按年级范围收敛** —— 模板被删后，
    # 任何仍引用它的考试都必须解绑，否则留下悬空 id（前端显示"套用了不存在的模板"）。
    # 模板本身是全校共用的基础配置（`grades.settings` 才可改），故整批解绑是维护数据完整性。
    # v1.11.1 避免全表遍历：用 config_json.contains 粗筛，Python 端 band_template_id() 精确验证兜底
    # （contains 生成 LIKE '%...%' 仍扫描 config_json 列，但只实例化匹配行，核心 OOM 风险消除）
    # band_template_id() 精确验证还能防御 LIKE 子串误匹配（如模板 id=1 误匹配含 id=11 的 JSON）
    for exam in Exam.query.filter(
            Exam.config_json.contains(f'"band_template_id": {obj.id}')).all():
        if exam.band_template_id() == obj.id:
            exam.set_band_template(None)
            db.session.add(exam)
    db.session.delete(obj)
    db.session.commit()
    # 2026-10-12 数据可信度审计（§3.8）：删模板会解除已绑定考试的关联，补业务日志
    log_operation(current_user, '删除', '划线模板', obj.id, f'{obj.name}', module='grades')
    return jsonify(success=True, message='模板已删除')


@bp.route('/api/exam-band-template', methods=['POST'])
@login_required
@perm_required('grades.settings')
def api_bind_exam_template():
    """考试 ↔ 模板绑定（解绑传 template_id=0）"""
    payload = request.get_json(force=True, silent=True) or {}
    exam_id = payload.get('exam_id')
    # 2026-10-13 全面审计 M-1：绑定/解绑都要过考试范围守卫（原 get_or_404 只验存在，
    # 任何 grades.settings 持有者都能改其它年级考试的划线模板绑定）——不存在 404、越界 403
    exam = assert_exam_visible(exam_id) if exam_id else None
    if not exam:
        return jsonify(success=False, message='考试不存在')
    tid = payload.get('template_id')
    if not tid:
        exam.set_band_template(None)
    else:
        tpl = BandTemplate.query.get(tid)
        if not tpl:
            return jsonify(success=False, message='模板不存在')
        exam.set_band_template(tpl.id, tpl.name)
    db.session.add(exam)
    db.session.commit()
    return jsonify(success=True, message='已绑定' if tid else '已解除绑定',
                   data={'template_id': exam.band_template_id(),
                         'template_name': exam.band_template_name()})

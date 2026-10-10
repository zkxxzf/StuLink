# StuLink v1.18.9.2 2026-10-10
# 分层模板管理：自定义模板 + 考试与模板绑定
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import render_template, request, jsonify, abort
from flask_login import login_required, current_user
import json

from app.extensions import db
from app.models import DictCategory
from app.models.grades import Exam, BandTemplate
from app.modules.grades import bp
from app.utils.decorators import perm_required

# 内置模板：首次访问时自动写入（幂等），保证「四层 / 默认模板」一直在
# v1.18.9.2 用户口径：默认模板改为 985线 / 211线 / 特控线 / 本科线 四层，
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
    # v1.18.9.2 修正 bug：历史上给 is_builtin 加字段时，迁移把已有行全标成了内置，
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


@bp.route('/api/band-templates')
@login_required
@perm_required('grades.settings')
def api_band_templates():
    """模板列表；带 exam_id 时返回该考试已绑定的模板，未绑定则按考试类型推荐一个"""
    ensure_builtin_templates()
    exam_id = request.args.get('exam_id', type=int)
    rows = (BandTemplate.query.order_by(BandTemplate.sort_order, BandTemplate.id).all())
    data = [{
        'id': t.id, 'name': t.name, 'lower_mode': t.lower_mode,
        'layers': t.get_layers(), 'remark': t.remark or '',
        'is_builtin': bool(t.is_builtin), 'exam_types': t.get_exam_types(),
    } for t in rows]

    bound = suggested = None
    exam_type = ''
    if exam_id:
        exam = Exam.query.get(exam_id)
        if exam:
            bound = exam.band_template_id()
            exam_type = exam.exam_type or ''
            if not bound and exam_type:
                # 未绑定时按「适用考试类型」推荐：取第一个声明适用该类型的模板
                for t in rows:
                    if exam_type in t.get_exam_types():
                        suggested = t.id
                        break
    return jsonify(success=True, data={'templates': data, 'bound_id': bound,
                                       'suggested_id': suggested,
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
    return jsonify(success=True, data={'id': obj.id}, message='模板已保存')


@bp.route('/api/band-templates/<int:tid>/delete', methods=['POST'])
@login_required
@perm_required('grades.settings')
def api_band_template_delete(tid):
    obj = BandTemplate.query.get_or_404(tid)
    if obj.is_builtin:
        return jsonify(success=False, message='内置模板不能删除')
    # 已绑定的考试解除绑定，避免残留悬空 id
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
    return jsonify(success=True, message='模板已删除')


@bp.route('/api/exam-band-template', methods=['POST'])
@login_required
@perm_required('grades.settings')
def api_bind_exam_template():
    """考试 ↔ 模板绑定（解绑传 template_id=0）"""
    payload = request.get_json(force=True, silent=True) or {}
    exam_id = payload.get('exam_id')
    exam = Exam.query.get_or_404(exam_id) if exam_id else None
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

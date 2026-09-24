# StuLink v1.18.1.0 2026-09-24
# 教师名单 Excel 导入：模板生成 / 解析校验 / 导入计划与应用 / 密码清单导出
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import csv
import io
import re

from openpyxl import Workbook, load_workbook

from app.extensions import db
from app.models.academic import Teacher
from app.modules.academic.services import teacher_service
from app.modules.grades.services import user_account
from app.utils import id_card as id_card_util

HEADERS = ['姓名', '身份证号', '手机号', '学科', '备注']
SAMPLE_ROW = ['张三', '', '13800138000', '语文', '示例行，导入前请删除本行']
_PHONE_RE = re.compile(r'^1\d{10}$')

_TIPS = [
    '一、字段说明',
    '  1. 姓名：必填（用于账号核对与自动建号）。',
    '  2. 身份证号：选填；首次导入可不填，之后可补录。',
    '     填写时必须为 18 位有效号码（含校验码）；一经录入教师本人不可修改，仅管理员可更新；系统内全局唯一。',
    '  3. 手机号：选填；如有将作为登录账号（教师后续可在工作台自行更换）。',
    '  4. 学科、备注：选填。',
    '二、导入匹配规则',
    '  1. 同一教师的判定顺序：身份证号 > 手机号 > 姓名。',
    '  2. 命中已有教师：补充空缺字段；身份证号与已有记录不一致时该行标记冲突。',
    '  3. 未命中：新建教师记录，系统自动生成唯一教师编号（T 开头，创建后不可更改）。',
    '三、账号处理',
    '  1. 手机号与现有账号用户名一致，或姓名唯一匹配现有账号：自动关联。',
    '  2. 无法匹配：自动创建登录账号（用户名默认取手机号或姓名拼音），初始密码在导入完成后统一展示。',
    '  3. 存在多个同名账号等特殊情况：该行标记为冲突并跳过账号处理，请到教师管理页人工核对。',
    '四、第 2 行为化名示例行，导入前请删除。',
]


def build_template():
    """生成教师名单导入模板（xlsx BytesIO）"""
    wb = Workbook()
    ws = wb.active
    ws.title = '教师名单'
    ws.append(HEADERS)
    ws.append(SAMPLE_ROW)
    for i, w in enumerate([14, 24, 16, 12, 40], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = 'A2'

    tip = wb.create_sheet('填写说明')
    for line in _TIPS:
        tip.append([line])
    tip.column_dimensions['A'].width = 110

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def parse_excel(stream):
    """解析上传的 Excel → 行记录列表（含行级错误收集与文件内查重）"""
    wb = load_workbook(stream, data_only=True)
    ws = wb.active
    iter_rows = ws.iter_rows(values_only=True)
    try:
        header = next(iter_rows)
    except StopIteration:
        raise ValueError('文件内容为空')

    col = {}
    for idx, cell in enumerate(header):
        head = str(cell or '').strip()
        if head in HEADERS:
            col[head] = idx
    if '姓名' not in col:
        raise ValueError('缺少「姓名」列，请使用标准模板填写')

    out = []
    for rn, raw in enumerate(iter_rows, start=2):
        def cell(key):
            i = col.get(key)
            if i is None or i >= len(raw) or raw[i] is None:
                return ''
            return str(raw[i]).strip()

        name = cell('姓名')
        id_card_raw = cell('身份证号')
        phone = cell('手机号')
        subject = cell('学科')
        note = cell('备注')
        if not any((name, id_card_raw, phone, subject, note)):
            continue  # 跳过全空行
        if note.startswith('示例行'):
            continue  # 跳过模板示例行

        errors = []
        if not name:
            errors.append('姓名为空')
        if id_card_raw and not id_card_util.validate(id_card_raw):
            errors.append(f'身份证号无效：{id_card_raw}')
        if phone and not _PHONE_RE.match(phone):
            errors.append(f'手机号格式不正确：{phone}')
        out.append({'row_no': rn, 'name': name,
                    'id_card': id_card_util.normalize(id_card_raw),
                    'phone': phone, 'subject': subject, 'note': note,
                    'errors': errors})

    if not out:
        raise ValueError('未解析到有效数据行（请确认使用标准模板且首行为表头）')

    # 文件内查重：身份证号 / 手机号不得跨行重复
    seen_id, seen_phone = {}, {}
    for r in out:
        if r['errors']:
            continue
        if r['id_card']:
            if r['id_card'] in seen_id:
                r['errors'].append(f'身份证号与第 {seen_id[r["id_card"]]} 行重复')
            else:
                seen_id[r['id_card']] = r['row_no']
        if r['phone']:
            if r['phone'] in seen_phone:
                r['errors'].append(f'手机号与第 {seen_phone[r["phone"]]} 行重复')
            else:
                seen_phone[r['phone']] = r['row_no']
    return out


def _account_plan(name, phone):
    """预览账号处理方式（只读）：返回 (status, user_id, username, reason)"""
    probe = Teacher(name=name, phone=phone or None)
    user, candidates, reason = teacher_service.match_user(probe)
    if user:
        return 'bind', user.id, '', ''
    if candidates:
        return 'conflict', None, '', reason
    if reason:
        return 'conflict', None, '', reason
    username = phone or user_account.gen_username(name) or ''
    return 'to_create', None, username, ''


def build_plan(rows):
    """生成导入计划（不写库）：
    items 每行包含 action=create/update/conflict、字段值与账号处理状态，
    summary 为各状态计数，errors 为无效行列表。
    """
    items, errors = [], []
    summary = {'create': 0, 'update': 0, 'conflict': 0, 'invalid': 0,
               'account_bind': 0, 'account_create': 0, 'account_conflict': 0}

    for r in rows:
        if r['errors']:
            summary['invalid'] += 1
            errors.append((r['row_no'], '；'.join(r['errors'])))
            continue

        target, conflict_reason = None, ''
        if r['id_card']:
            target = teacher_service.find_by_id_card(r['id_card'])
        if target is None and r['phone']:
            target = Teacher.query.filter_by(phone=r['phone']).first()
        if target is None:
            same = Teacher.query.filter_by(name=r['name']).all()
            if len(same) == 1:
                target = same[0]
            elif len(same) > 1:
                if not r['id_card'] and not r['phone']:
                    conflict_reason = '存在多个同名教师，请填写身份证号或手机号以便定位'

        item = {'idx': len(items), 'row_no': r['row_no'], 'name': r['name'],
                'id_card': r['id_card'], 'id_masked': id_card_util.mask(r['id_card']),
                'phone': r['phone'],
                'subject': r['subject'], 'note': r['note'],
                'teacher_id': target.id if target else None,
                'teacher_uid': target.teacher_uid if target else teacher_service.gen_teacher_uid(),
                'changes': {}, 'conflict_reason': conflict_reason,
                'account': {'status': '', 'user_id': None, 'username': '', 'reason': ''}}

        if conflict_reason:
            item['action'] = 'conflict'
            item['account']['status'] = 'skip'
            summary['conflict'] += 1
        elif target is None:
            item['action'] = 'create'
            summary['create'] += 1
        else:
            item['action'] = 'update'
            summary['update'] += 1
            if r['id_card'] and not target.id_card_enc:
                item['changes']['id_card'] = '补录身份证'
            elif r['id_card'] and target.id_card_enc:
                exist = id_card_util.decrypt_id_card(target.id_card_enc)
                if exist and exist != r['id_card']:
                    item['action'] = 'conflict'
                    item['conflict_reason'] = '身份证号与已录入记录不一致（仅管理员可在教师管理页修改）'
                    summary['update'] -= 1
                    summary['conflict'] += 1
            if r['phone'] and r['phone'] != (target.phone or ''):
                item['changes']['phone'] = f'{(target.phone or "空")} → {r["phone"]}'
            if r['subject'] and r['subject'] != (target.subject or ''):
                item['changes']['subject'] = f'{(target.subject or "空")} → {r["subject"]}'
            if r['name'] != target.name:
                item['changes']['name'] = f'{target.name} → {r["name"]}'

        if item['action'] == 'conflict':
            pass
        elif target is not None and target.user_id:
            from app.models import User
            u = db.session.get(User, target.user_id)
            if u:
                item['account'] = {'status': 'bind', 'user_id': u.id,
                                   'username': u.username, 'reason': ''}
                summary['account_bind'] += 1
            else:
                st, uid, un, rsn = _account_plan(r['name'], r['phone'])
                item['account'] = {'status': st, 'user_id': uid,
                                   'username': un, 'reason': rsn}
                summary['account_' + ('bind' if st == 'bind' else
                                      'create' if st == 'to_create' else 'conflict')] += 1
        else:
            st, uid, un, rsn = _account_plan(r['name'], r['phone'])
            item['account'] = {'status': st, 'user_id': uid,
                               'username': un, 'reason': rsn}
            summary['account_' + ('bind' if st == 'bind' else
                                  'create' if st == 'to_create' else 'conflict')] += 1
        items.append(item)

    return {'items': items, 'summary': summary, 'errors': errors}


def apply_plan(items, username_overrides=None):
    """执行导入计划：建/更教师记录、绑定或创建账号。返回结果汇总。"""
    username_overrides = username_overrides or {}
    created_accounts = []
    n_create = n_update = n_skip = 0

    for it in items:
        if it['action'] == 'conflict':
            n_skip += 1
            continue

        if it['action'] == 'create':
            uid = it['teacher_uid']
            if Teacher.query.filter_by(teacher_uid=uid).first():
                uid = teacher_service.gen_teacher_uid()
            t = Teacher(teacher_uid=uid, name=it['name'],
                        phone=it['phone'] or None,
                        subject=it['subject'] or None,
                        note=it['note'] or None)
            if it['id_card']:
                t.id_card_enc = id_card_util.encrypt_id_card(it['id_card'])
            db.session.add(t)
            db.session.flush()
            n_create += 1
        else:
            t = db.session.get(Teacher, it['teacher_id'])
            if not t:
                n_skip += 1
                continue
            if it['id_card'] and not t.id_card_enc:
                t.id_card_enc = id_card_util.encrypt_id_card(it['id_card'])
            if it['phone']:
                t.phone = it['phone']
            if it['subject']:
                t.subject = it['subject']
            if it['note']:
                t.note = it['note']
            if it['name'] and it['name'] != t.name:
                t.name = it['name']
            n_update += 1

        acc = it.get('account') or {}
        status = acc.get('status')
        if status == 'bind' and acc.get('user_id'):
            t.user_id = acc['user_id']
        elif status == 'to_create':
            username = (username_overrides.get(str(it['idx']))
                        or username_overrides.get(it['idx'])
                        or acc.get('username') or '').strip()
            user, pwd = user_account.create_teacher_account(
                it['name'], username or None, role='teacher')
            t.user_id = user.id
            created_accounts.append({'name': it['name'],
                                     'username': user.username,
                                     'password': pwd,
                                     'teacher_uid': t.teacher_uid})

    db.session.commit()
    return {'created_teachers': n_create, 'updated_teachers': n_update,
            'skipped': n_skip, 'accounts': created_accounts}


def accounts_csv(accounts):
    """初始账号密码清单 CSV（带 BOM，Excel 直接打开不乱码）"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['姓名', '登录用户名', '初始密码', '教师编号'])
    for a in accounts:
        writer.writerow([a['name'], a['username'], a['password'], a['teacher_uid']])
    return ('\ufeff' + buf.getvalue()).encode('utf-8')

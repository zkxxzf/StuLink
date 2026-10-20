# -*- coding: utf-8 -*-
"""把 Excel 里的学生对上主库学号。

为什么不能只看考号：源文件的「考号」有两类错误 ——
① 高一下期末（No.7）整列是座位号（20251170 在文件里是柯心宇、主库是王晶）；
② 同名学生的两行常常写了同一个考号（No.3/No.4/No.5 的刘奕辰、毛依依、
   崔鑫媛），按考号导入必然张冠李戴。
所以规则是「姓名优先，考号/班级只用于消歧」，并且要求同组学号不重复使用：

1. 姓名在主库唯一 → 直接采用；
2. 同名多人：
   a. 文件班级 == 主库当前班级 → 采用（分班后班级最稳）；
   b. 跨场次「(姓名, 班级) → 学号」唯一 → 采用；
   c. 组内只剩 1 人 1 号 → 排除法采用；
   d. 考号命中其一 → 采用；
   e. 仍不行 → 报错，绝不猜。
"""
import os

from app.models import Student

from . import score_reader
from .specs import SOURCE_DIR

# 源文件已知错别字 → 主库姓名（只用于匹配，不改主库）
NAME_ALIASES = {
    '陈康高畅': '陈康高唱',
}


def build(grade='2025级'):
    """构建主库索引：姓名 → [(学号, 班级)]、学号 → 班级。

    NAME_ALIASES 用于源文件里的已知错别字（如「陈康高畅」实为主库
    「陈康高唱」20251309，同班同学号），只影响匹配，不改主库姓名。
    """
    cls_of, by_name = {}, {}
    for stu in Student.query.filter(Student.grade == grade).all():
        no = stu.student_number
        cls_of[no] = stu.class_name or ''
        by_name.setdefault(stu.name, []).append(no)
    for alias, real in NAME_ALIASES.items():
        if real in by_name:
            by_name[alias] = list(by_name[real])
    return {'cls_of': cls_of, 'by_name': by_name}


def collect_hints(index, group):
    """扫全部源文件，收集 (姓名, 班级) → {学号}（只认考号与姓名都对上的行）。"""
    hints = {}
    for spec in group:
        path = os.path.join(SOURCE_DIR, spec['file'])
        for rec in score_reader.read_scores(path, spec['sheets']):
            no = rec['exam_no']
            if index['by_name'].get(rec['name']) is None:
                continue
            if no not in index['cls_of']:
                continue
            hints.setdefault((rec['name'], rec['class_name']), set()).add(no)
    return hints


def _bind(rec, no):
    """把学号写到记录上。"""
    rec['student_no'] = no


def _assign_group(name, recs, index, hints, used):
    """同名多人：按班级 / 跨场提示 / 排除法 / 考号逐个消歧。

    返回 (已绑定记录, 未绑定记录)。used 为全场已占用学号集合。
    """
    cands = [no for no in index['by_name'][name] if no not in used]
    pending, failed = [], []
    for rec in recs:
        hit = [no for no in cands if index['cls_of'].get(no) == rec['class_name']]
        if len(hit) == 1 and hit[0] not in used:
            _bind(rec, hit[0])
            used.add(hit[0])
        else:
            pending.append(rec)
    rest = []
    for rec in pending:
        hit = [no for no in (hints.get((name, rec['class_name'])) or ())
               if no in cands and no not in used]
        if len(hit) == 1:
            _bind(rec, hit[0])
            used.add(hit[0])
        else:
            rest.append(rec)
    left = [no for no in cands if no not in used]
    if len(rest) == 1 and len(left) == 1:
        _bind(rest[0], left[0])
        used.add(left[0])
        rest = []
    for rec in rest:
        if rec['exam_no'] in left:
            _bind(rec, rec['exam_no'])
            used.add(rec['exam_no'])
        else:
            failed.append(rec)
    return [r for r in recs if r.get('student_no')], failed


def attach(records, index, hints=None):
    """给记录写 student_no；返回 (成功列表, 未匹配列表)。"""
    hints = hints or {}
    groups = {}
    for rec in records:
        groups.setdefault(rec['name'], []).append(rec)
    ok, bad, used = [], [], set()
    for name, recs in groups.items():
        if name not in index['by_name']:
            bad.extend({'name': name, 'class_name': r['class_name'],
                        'reason': '未匹配到主库'} for r in recs)
            continue
        if len(recs) == 1 and len(index['by_name'][name]) == 1:
            _bind(recs[0], index['by_name'][name][0])
            used.add(recs[0]['student_no'])
            ok.append(recs[0])
            continue
        good, fail = _assign_group(name, recs, index, hints, used)
        ok.extend(good)
        bad.extend({'name': name, 'class_name': r['class_name'],
                    'reason': '同名无法消歧'} for r in fail)
    return ok, bad

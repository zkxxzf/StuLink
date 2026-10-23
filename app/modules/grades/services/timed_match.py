# StuLink v1.19.70.64 2026-10-21
# 限时练学生匹配：考号 / 学号 / 智学网账号 / 姓名+班级 四级
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""限时练导入的学生匹配与差异预览（不写库）。

匹配优先级（2026-10-21 用户口径：要"智能匹配"，别再整表未匹配）：

1. 文件【考号】或【学号】→ ``students.student_number``（学号，最稳）；
2. 同一串号 → ``students.zhixue_no``（智学网准考证号）；
3. 同一串号 → ``students.zhixue_account``（智学网账号，10 位，最稳定）；
4. 都不中 → **姓名 + 班级**（本年级内唯一才采用；同名同班报歧义，不猜）。

为什么要第 4 级：智学网成绩表只给"准考证号"，而准考证号**每场重排**
（同一届 47637703 → 47642671），只靠号必然大面积未匹配；名单里的
账号/准考证号靠 ``scripts/sync_zhixue_roster.py`` 同步进来。
"""
from app.models import Student
from app.modules.grades.services.timed_import import _code, _norm_class


def _norm_name(value):
    return (value or '').replace('\u3000', ' ').strip()


def _norm_student_no(value):
    """学号/考号归一化：去空白、去前导 0（数字才去）"""
    s = _code(value)
    return str(int(s)) if s.isdigit() else s


def _load_index(grade):
    """一次把本年级学生读进内存，建四个索引（全校两千人，毫秒级）"""
    q = Student.query
    if grade:
        q = q.filter(Student.grade == grade)
    by_no, by_zhixue, by_acct, by_name = {}, {}, {}, {}
    for s in q.all():
        by_no.setdefault(_norm_student_no(s.student_number), s)
        if s.zhixue_no:
            by_zhixue.setdefault(_norm_student_no(s.zhixue_no), s)
        acct = getattr(s, 'zhixue_account', None)
        if acct:
            by_acct.setdefault(_norm_student_no(acct), s)
        if s.name:
            key = (_norm_name(s.name), _norm_class(s.class_name))
            by_name.setdefault(key, []).append(s)
    return by_no, by_zhixue, by_acct, by_name


# 兼容旧调用：以前按"考号集合 + 学号集合"分块查库
def _load_students(grade, zhixue=None, nos=None):
    idx = _load_index(grade)
    if zhixue is None and nos is None:
        return idx[1], idx[0]

    def _keep(d, keys):
        want = {_norm_student_no(k) for k in keys if k}
        return {k: v for k, v in d.items() if k in want}

    return _keep(idx[1], zhixue or []), _keep(idx[0], nos or [])


def _pick(r, by_no, by_zhixue, by_acct, by_name):
    """返回 (学生, 匹配方式) 或 (None, '')"""
    codes = []
    for key in (r.get('zhixue_no'), r.get('student_no')):
        k = _norm_student_no(key)
        if k and k not in codes:
            codes.append(k)
    name = _norm_name(r.get('name'))
    for k in codes:
        for idx, how in ((by_no, '学号'), (by_zhixue, '考号'),
                         (by_acct, '智学网账号')):
            stu = idx.get(k)
            if stu is None:
                continue
            # 准考证号每场重排、会被复用：号对上了但名字不是他 → 不认这个号，
            # 交给姓名+班级兜底（宁可不匹配，也不挂错人）。
            if name and stu.name and _norm_name(stu.name) != name:
                continue
            return stu, how
    # 姓名 + 班级：唯一才认（同名同班宁可不匹配，也不挂错人）
    name = _norm_name(r.get('name'))
    cls = _norm_class(r.get('class_name'))
    if name and cls:
        hits = by_name.get((name, cls)) or []
        if len(hits) == 1:
            return hits[0], '姓名+班级'
    return None, ''


def match_students(rows, grade=''):
    """把解析行挂到主库学生上（考号/学号/账号/姓名+班级）。

    返回 (matched_rows, errors, stats)；matched_rows 的 ``student_no``
    一律以主库原值为准，并补 ``student_name`` / ``class_name`` / ``zhixue_no``
    与 ``match_by``（怎么匹配上的，预览页可核对）。
    """
    by_no, by_zhixue, by_acct, by_name = _load_index(grade)

    out, errors, seen = [], [], set()
    hows = {}
    for r in rows:
        stu, how = _pick(r, by_no, by_zhixue, by_acct, by_name)
        if stu is None:
            code = (r.get('zhixue_no') or r.get('student_no') or '').strip()
            name = (r.get('name') or '').strip()
            errors.append({
                'line': r['line'], 'code': code,
                'reason': ('考号 %s 未匹配；姓名+班级（%s %s）也没对上'
                           % (code, name or '?', r.get('class_name') or '?'))
                if code else '缺少考号/学号'})
            continue
        if stu.student_number in seen:
            errors.append({'line': r['line'], 'code': stu.student_number,
                           'reason': '匹配到同一学生，已跳过重复行'})
            continue
        seen.add(stu.student_number)
        hows[how] = hows.get(how, 0) + 1
        out.append({'line': r['line'], 'student_no': stu.student_number,
                    'student_name': stu.name or r.get('name', ''),
                    'zhixue_no': _code(stu.zhixue_no) or r.get('zhixue_no', ''),
                    # 2026-10-21：班级**以主库学籍为准**。文件里的班级是导入方
                    # 自己填的，若当真，导入人可把成绩挂到任意班级 —— 而班级正是
                    # 成绩可见范围的判定依据（班主任按班授权）。
                    'class_name': stu.class_name or r.get('class_name') or '',
                    'score': r['score'], 'match_by': how})
    stats = {'matched': len(out), 'unmatched': len(errors), 'by_kind': hows}
    return out, errors, stats


def preview_diff(exam_id, subject, rows):
    """预览差异：新增 / 改分 / 缺考删除（同科重导 = 整科覆盖）"""
    from app.models.timed import TimedScore
    existing = {r.student_no: r.score for r in TimedScore.query.filter_by(
        exam_id=int(exam_id), subject=subject).all()}
    new_ids = {r['student_no'] for r in rows}
    updated = sum(1 for r in rows if r['student_no'] in existing
                  and abs((existing[r['student_no']] or 0) - r['score']) > 1e-9)
    return {
        'new_students': len(new_ids - set(existing)),
        'updated_rows': updated,
        'deleted_rows': len(set(existing) - new_ids),
        'kept_rows': len(new_ids & set(existing)),
    }

# StuLink v1.19.50.54 2026-10-15
# 成绩单（考试概览）的数据范围——Web 侧唯一实现，供页面、翻页片段与侧栏共用
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""成绩单（考试概览 / 成绩单页）的账号班级范围。

用户口径（2026-10-15）：**成绩单里是学生的成绩，任课教师 / 班主任也能看**，
但"就展示他们班的就行了"——所以按账号范围收敛，规则与「临界生对比」完全一致：

  · 管理员 / 校级 / 年级长及以上 / 用户级数据范围授权 → 整年级（不限班）；
  · 班主任 → 本班（含总分口径）；
  · 仅任课映射（单科授权）的班**不含总分口径**（成绩单含总分与排名）→ 不计入；
  · 一个可用班都没有 = 无权（调用方 403，fail-closed，不回落成"全部班级"）。

之所以单独成模块：路由（`routes/exams.py::_scoresheet_scope`）与侧栏
（`app/utils/exam_nav.py` 的「考试概览·成绩单」入口显隐）必须同一份判断，
否则会出现"看得见点不进"的 403 死链，或更糟——入口在、数据没收窄。
"""
from app.extensions import db
from app.models.grades import TOTAL_SUBJECT, ExamScore


def exam_classes(exam_id):
    """本场考试出现过的班级（总分行口径＝考试当时班级，与班排/方向排一致）。"""
    rows = (db.session.query(ExamScore.class_name)
            .filter(ExamScore.exam_id == exam_id,
                    ExamScore.subject == TOTAL_SUBJECT,
                    ExamScore.class_name.isnot(None),
                    ExamScore.class_name != '')
            .distinct().all())
    return sorted({r[0] for r in rows})


def visible(user, exam, classes=None):
    """返回 (allowed, cls_list)。

    allowed=None 表示不限班级（整年级/全校）；否则为可用班级集合（可能为空集）。
    cls_list 始终是"本场考试里、该账号可用的班级"列表，供页面下拉使用。
    账号完全没有成绩范围时会抛 PermissionError，由调用方决定 403。

    2026-10-15 口径变更：任课授权放宽成"年级 × 学科"后，`visible_classes()` 对
    有任课的年级也返回 None，**不能再用 `visible_classes() is None → 不限班`**
    —— 那会把成绩单（含总分列、方向排 / 班排 / 进退步）整档放开给任课教师。
    收口改用 `total_visible_classes()`：任课教师（无总分口径）→ 空集 → fail-closed。
    """
    from app.modules.grades.services import score_guard
    classes = exam_classes(exam.id) if classes is None else list(classes)
    scope = score_guard.resolve(user)
    allowed = scope.total_visible_classes(exam.grade, classes)
    if allowed is None:                       # 整年级 / 全校授权
        return None, classes
    allowed = {c for c in classes if c in allowed}
    return allowed, [c for c in classes if c in allowed]

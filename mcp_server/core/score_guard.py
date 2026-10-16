# -*- coding: utf-8 -*-
"""（薄转发）成绩访问权限引擎 —— 2026-10-13 已迁入主应用。

实现改为 `app.modules.grades.services.score_guard`，**Web 端（成绩汇报区 AI 分析）
与 MCP 服务共用同一份规则**；本文件只保留同名模块与同名符号，历史调用方
（`tools/scores.py`、`tools/score_analysis.py`、`tools/_score_common.py`、
`smoke_scores.py`）无需改动。

为什么迁走：Web 端要在进程内按同一套细粒度规则（R1 任课教师学科级 / R2 班主任 /
R3 年级长及以上 / R4 显式授权）收窄 AI 工具的结果，若两边各写一份，权限口径
迟早漂移——权限漂移是安全红线，故收敛为单一真值。

注意：必须先 `from . import bootstrap`（把项目根注入 sys.path）再导入 app.*，
否则 MCP 独立进程下 import 会失败。
"""
from . import bootstrap  # noqa: F401  确保项目根已进 sys.path（必须早于 app.* 导入）

from app.modules.grades.services.score_guard import (  # noqa: E402
    ScoreScope, require_class, require_exam, require_student, resolve,
)

__all__ = ['ScoreScope', 'resolve', 'require_exam', 'require_class', 'require_student']

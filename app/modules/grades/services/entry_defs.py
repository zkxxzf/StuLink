# -*- coding: utf-8 -*-
"""成绩模块「功能入口」定义表（2026-10-19 首页仪表盘入口板）。

口径：
1. 只收录**页面路由**（GET、渲染模板）。导出 / PDF / 模板下载 / JSON 接口 /
   分页片段（如 ``/exams/<id>/scores`` 只返回 ``<tr>``）一律不进目录，
   避免出现"点进去是一段 JSON"的假入口。
2. ``exam``：None=跨考试；'path'=路由里带考试 id；'query'=``?exam=<id>``。
   ``imported=True`` 表示考试下拉只列「已导入成绩」的场次（分析类）。
3. ``gate``：菜单权限之外的业务门槛。目前只有 ``'scoresheet'``
   （考试概览·成绩单要先过 ``services/scoresheet.visible``，防 403 死链）。

维护方式：新增/下线成绩页面时只改本文件；链接与权限校验在
``entry_catalog`` 里完成（那里不重复登记路由）。
"""

# 分组顺序即页面上的展示顺序
G_MAIN = '考试管理'
G_ONE = '单场考试'
G_ANA = '成绩分析'
G_ME = '个人与证明'
G_CFG = '基础配置'
GROUPS = (G_MAIN, G_ONE, G_ANA, G_ME, G_CFG)


def _e(key, title, icon, group, endpoint, perm='grades.view',
       exam=None, query=None, imported=False, gate=None, desc=''):
    """构造一条入口定义（字段含义见模块文档）"""
    return {'key': key, 'title': title, 'icon': icon, 'group': group,
            'endpoint': endpoint, 'perm': perm, 'exam': exam,
            'tab': query or {}, 'imported': imported, 'gate': gate,
            'desc': desc}


ENTRIES = (
    # ---------- 考试管理（跨考试） ----------
    _e('exams', '历次考试', 'bi-journals', G_MAIN, 'grades.exams_list',
       desc='全部考试列表：新建 / 导入状态 / 考务批次'),
    _e('exam_new', '新建考试', 'bi-plus-circle', G_MAIN, 'grades.exam_new',
       perm='grades.edit', desc='登记一场新考试（年级 / 阶段 / 日期）'),
    _e('affairs', '考务批次总览', 'bi-kanban', G_MAIN,
       'grades.affairs_overview', perm='grades.edit',
       desc='未关联考试的考务批次与编排进度'),
    _e('rooms', '考场维护', 'bi-door-open', G_MAIN, 'grades.exam_rooms',
       perm='grades.edit', desc='考场房间库、分组模板与默认容量'),

    # ---------- 单场考试（先选考试） ----------
    _e('detail', '考试概览·成绩单', 'bi-file-earmark-text', G_ONE,
       'grades.exam_detail', exam='path', gate='scoresheet',
       desc='本场成绩单、班级名次与四大流程入口'),
    _e('import', '数据导入', 'bi-cloud-arrow-up', G_ONE, 'grades.exam_import',
       perm='grades.import', exam='path',
       desc='标准模板 / AI 智能导入，含分数线与考生名单'),
    _e('ai_import', 'AI 智能导入', 'bi-stars', G_ONE, 'grades.exam_ai_import',
       perm='grades.import', exam='path',
       desc='用大模型从原始表格里抽成绩并写入本场考试'),
    _e('bands', '划线分层', 'bi-layers', G_ONE, 'grades.bands_page',
       exam='path', desc='总分线 / 单科三线的划线分层与模板应用'),
    _e('affair', '考务安排', 'bi-clipboard2-check', G_ONE,
       'grades.exam_affair_go', perm='grades.edit', exam='path',
       desc='考场编排、考生名单、监考表与打印交付'),
    _e('score_changes', '分数修改记录', 'bi-pencil-square', G_ONE,
       'grades.score_changes', perm='grades.edit', exam='query',
       desc='本场考试的全部改分流水（谁改的、改前改后）'),

    # ---------- 成绩分析（先选考试，且只列已导入） ----------
    _e('analysis_grade', '年级分析', 'bi-bar-chart-line', G_ANA,
       'grades.index', exam='query', query={'tab': 'grade'}, imported=True,
       desc='年级汇总、分层上线率与班级对比'),
    _e('analysis_class', '班级分析', 'bi-diagram-3', G_ANA, 'grades.index',
       exam='query', query={'tab': 'class'}, imported=True,
       desc='本班各科均分、名次与学科短板'),
    _e('analysis_subject', '学科分析', 'bi-book', G_ANA, 'grades.index',
       exam='query', query={'tab': 'subject'}, imported=True,
       desc='单科班级对比与分数段分布'),
    _e('analysis_teacher', '任课教师', 'bi-person-badge', G_ANA,
       'grades.index', exam='query', query={'tab': 'teacher'}, imported=True,
       desc='按任课教师聚合的班级成绩'),
    _e('analysis_compare', '班级对比', 'bi-columns-gap', G_ANA,
       'grades.index', exam='query', query={'tab': 'compare'}, imported=True,
       desc='班级 × 学科的横向对比矩阵'),
    _e('global_compare', '全局对比', 'bi-globe2', G_ANA,
       'grades.global_compare_page', exam='query', imported=True,
       desc='各年级各组合的分层人数、均分与上线率'),
    _e('near_line', '临界生对比', 'bi-bullseye', G_ANA,
       'grades.exam_near_line', exam='path', imported=True,
       desc='划线上下临界生名单与前后场变化'),
    _e('pivot', '自由分析', 'bi-table', G_ANA, 'grades.pivot_page',
       exam='query', imported=True, desc='行 / 列 / 指标任意组合的透视分析'),
    _e('honor', '表彰名单', 'bi-award', G_ANA, 'grades.honor',
       exam='query', imported=True, desc='年级前 30 / 卓越班前 10 / 单科前 10'),
    _e('report', '成绩汇报区', 'bi-easel', G_ANA, 'grades.report_page',
       exam='query', imported=True, desc='面向校级汇报的图表与 PPT 交付'),
    _e('print_summary', '汇总打印', 'bi-printer', G_ANA,
       'grades.print_summary_page', exam='query', imported=True,
       desc='A4 网格汇总表，可直接打印 / 存 PDF'),
    _e('ai', 'AI 分析', 'bi-robot', G_ANA, 'grades.ai_studio',
       exam='query', imported=True, desc='一句话提问 → 后台取数 → HTML 报告'),

    # ---------- 个人与证明 ----------
    _e('student_query', '学生成绩', 'bi-person-vcard', G_ME,
       'grades.student_query_page', perm='grades.student_query',
       desc='按学生查历次成绩，可开具成绩证明'),
    _e('cert_records', '成绩证明台账', 'bi-patch-check', G_ME,
       'grades.cert_records_page', perm='grades.student_query',
       desc='已开具证明的台账、作废与核验'),
    _e('reviews', '成绩复核', 'bi-clipboard-check', G_ME,
       'grades.reviews_page', desc='提交 / 查看调分申请'),
    _e('reviews_manage', '复核审批', 'bi-check2-square', G_ME,
       'grades.reviews_manage_page', perm='grades.score_edit',
       desc='审批调分申请并留痕'),

    # ---------- 基础配置 ----------
    _e('subjects', '选科维护', 'bi-bookmark-check', G_CFG,
       'grades.subject_mgmt', perm='grades.subject_mgmt',
       desc='班级选科组合与行政班配置'),
    _e('band_templates', '分层模板', 'bi-collection', G_CFG,
       'grades.band_templates_page', perm='grades.settings',
       desc='划线层模板（人数 / 比例 / 名次分段）'),
    _e('teachers', '任课教师映射', 'bi-person-workspace', G_CFG,
       'grades.teachers_page', perm='grades.teachers',
       desc='谁教哪个班哪一科（成绩归属唯一口径）'),
    _e('teachers_import', '教师映射导入', 'bi-file-earmark-arrow-up', G_CFG,
       'grades.teachers_import_page', perm='grades.teachers',
       desc='用教师安排表批量绑定任课与班主任'),
)

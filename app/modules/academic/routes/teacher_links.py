# StuLink v1.18.8.0 2026-10-10
# 教务管理 · 任课教师映射：与成绩管理同款页面（复用视图函数与源数据 TeacherSubjectLink）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""教务管理侧的「任课教师映射」。

设计（2026-10-10，用户要求：教务管理里要有同款页面、不是跳转、数据源不变）：
- 源数据只有一份 —— 成绩管理的 ``TeacherSubjectLink``，服务与校验逻辑都在成绩管理侧；
- 所以这里用 ``add_url_rule`` 把同一批视图函数挂到 ``/academic`` 前缀下（零逻辑复制），
  整条链路（矩阵读取/保存、批量导入上传/预览/确认、模板下载）都留在教务管理；
- 页面内链接、表单 action、重定向与前端请求由 ``grades.routes.teachers._ep()``
  按 ``request.blueprint`` 自适应：成绩管理里仍是 /grades/*，教务管理里是 /academic/*；
- 权限沿用视图函数自带的 ``grades.teachers``（菜单可见性判断同此 key）。

注册的端点：
    academic.teacher_links_page            GET  映射矩阵页（/academic/teacher-links）
    academic.teacher_links_api             GET  矩阵数据 JSON
    academic.teacher_links_save_api        POST 保存矩阵单元格
    academic.teacher_links_import_page     GET  批量导入页
    academic.teacher_links_import_upload   POST 上传解析并核对账号
    academic.teacher_links_import_confirm  POST 确认导入
    academic.teacher_links_template        GET  下载导入模板
"""
from app.modules.academic import bp
from app.modules.grades.routes import teachers as gt  # noqa: F401  （复用其视图函数）

# 矩阵页 + 数据接口
bp.add_url_rule('/teacher-links', 'teacher_links_page', gt.teachers_page)
bp.add_url_rule('/teacher-links/api', 'teacher_links_api', gt.teachers_get)
bp.add_url_rule('/teacher-links/api/save', 'teacher_links_save_api',
                gt.teachers_save, methods=['POST'])

# 批量导入链路（上传 → 预览核对 → 确认写入）
bp.add_url_rule('/teacher-links/import', 'teacher_links_import_page',
                gt.teachers_import_page)
bp.add_url_rule('/teacher-links/import/upload', 'teacher_links_import_upload',
                gt.teachers_import_upload, methods=['POST'])
bp.add_url_rule('/teacher-links/import/confirm', 'teacher_links_import_confirm',
                gt.teachers_import_confirm, methods=['POST'])

# 导入模板下载
bp.add_url_rule('/teacher-links/template.xlsx', 'teacher_links_template',
                gt.teacher_template_download)

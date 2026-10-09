"""教师名单路由占位（实际实现见本目录各文件）"""
# v1.16.0 学期课表（timetable.db）：academic/__init__.py 已 import 本包，此处导入即完成路由注册
from app.modules.academic.routes import schedule  # noqa: F401,E402
# v1.9.3 表单收集汇总侧（看板/汇总表格/材料包/催交）：导入即完成路由注册
from app.modules.academic.routes import form_summary  # noqa: F401,E402
# 2026-10-10 任课教师映射（教务侧同款页面）：复用成绩管理视图函数与源数据，导入即注册
from app.modules.academic.routes import teacher_links  # noqa: F401,E402

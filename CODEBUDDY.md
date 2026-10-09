# StuLink 项目约定（供 AI 协作参考）

## 教师-学科关系：以「任课教师映射」为唯一依据（2026-10-10 起）

- **唯一数据源**：`TeacherSubjectLink`（成绩管理模块维护），决定"谁教哪个班哪一科"、
  谁能看哪些成绩、教师维度的教学成绩归属。
- **页面（同一批视图函数、同一份数据）**：
  - 教务管理：`/academic/teacher-links`（端点 `academic.teacher_links_*`）
  - 成绩管理：`/grades/teachers`（端点 `grades.teachers_*`，批量导入入口 `grades.teachers_import_page`）
  - 权限 key：`grades.teachers`
- **已下线（不要再引用或恢复）**：任课安排 `/academic/duty`、`/academic/duty/export`、
  `/academic/api/duty` 与模板 `academic/duty_table.html`，以及 `duty_service.py` 里
  按课表实时聚合的 `build_class_duty` / `build_teacher_duty` / `build_subject_duty` /
  `export_duty_workbook`。旧地址一律 404。
- **核查纪律**：修改或核查任何涉及"班级 × 学科 × 教师"的逻辑、报表、导出、界面时，
  只读任课教师映射（`TeacherSubjectLink`），**不要再参考或依赖任课安排**（包括它的
  课表聚合口径、导出格式与旧文档描述）。
- **不受影响**：备课组长 `/academic/leaders`、晚自习值班 `/academic/schedule/<sid>/night-duty`、
  学期课表 `/academic/schedule/*`。
- 背景与版本记录见 `docs/教务管理设计手册.md`（第 3、4、7 节）。

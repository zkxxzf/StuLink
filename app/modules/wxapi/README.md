# 小程序 API 目录（/api/wx）

按领域分包，避免所有 `routes_*` 平铺在一个目录里：

```
wxapi/
├── core/        鉴权 guards、响应 response、令牌 tokens、序列化 serializers
├── auth/        登录 / 启动 / 当前用户
├── workbench/   首页工作台（课程、监考、待办）
├── grades/      成绩、考试分析、学生档案、画像/德育列表
├── academic/    课表、查课、教师、业绩
├── dorm/        宿舍概览、房间、查询、统计
├── notice/      通知公告
└── exam/        考务批次、考场、监考
```

## 约定

- `__init__.py` 只定义 Blueprint，并按领域导入各 `routes_*`，
  保证 `@bp.route` 注册到同一个 `/api/wx`。
- 各路由文件从 `app.modules.wxapi` 导入 `bp`，不要自行再建 Blueprint。
- 公共依赖只允许 `core/` 被其他领域依赖；领域之间不要互相堆逻辑。
- 业务计算一律复用 `app/modules/<domain>/services`，不复制公式。
- 新增接口：在对应领域目录建 `routes_*.py`，再在 `__init__.py` 加一行导入。
- 权限：`wx_login_required` 做登录校验，`wx_perm_required` 做模块权限；
  数据范围在服务层按当前用户收敛。

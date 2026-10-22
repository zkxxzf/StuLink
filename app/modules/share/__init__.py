"""对外分享（只读）模块入口（2026-10-22）。

目前只有 AI 报告：`/s/<token>`。分享页面**只要求登录**，不要求 `grades.view` ——
这是生成者主动外发的报告；若加成绩权限，"别人打开链接登录即可查看"就不成立了。

安全边界不靠权限点，而靠这四条（缺一不可）：
  1. token 由 `secrets.token_urlsafe` 生成，不可枚举；
  2. 只有报告所有者能生成/撤销（见 routes/ai_share.py）；
  3. 可设有效期，随时可撤销，报告删除即失效；
  4. 响应带 `X-Robots-Tag: noindex, nofollow` 与 `no-store`，不被搜索引擎收录/缓存。
"""
from flask import Blueprint

bp = Blueprint('share', __name__)

from app.modules.share import routes  # noqa: E402,F401

# StuLink v1.19.62.65 2026-10-22
# Copyright (c) 2026 zkxxzf. Apache License 2.0

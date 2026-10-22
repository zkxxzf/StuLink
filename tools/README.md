# tools 目录（开发期辅助）

只放**开发期使用、不参与上线流程、不做断言测试**的工具。

## 允许

- 生成代码 / 配置 / 模板资源
- 解析 schema、导出结构快照
- 调试 MCP 客户端、浏览器油猴脚本
- Git 合并冲突分析等开发辅助

## 禁止

- ❌ 冒烟、check、回归脚本 → 放 `tests/`
- ❌ 数据库迁移、备份、同步业务脚本 → 放 `scripts/`
- ❌ `_tmp`、一次性截图/调试脚本 → 放 `tests/_local/`

## 现有工具

| 工具 | 用途 |
|---|---|
| `make_pptx_clean_template.py` | 从母版生成干净 PPT 模板 |
| `mcp_doubao_connector.ps1` | 豆包客户端接 MCP 的本地连通器 |
| `git/analyze_conflicts.py` | 分析 git 合并冲突块（版本头 vs 实质） |
| `userscript/` | 本机油猴脚本（已在 .gitignore，不入库） |

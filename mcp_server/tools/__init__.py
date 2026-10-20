# -*- coding: utf-8 -*-
"""工具注册总入口。

本服务已收敛为**成绩分析专用**（只读）：只注册 `scores` 一个工具模块。
权限由 `core/score_guard.py` 按三条规则裁决——
R1 任课教师（**(年级, 学科) 二元组**命中，仅所教学科；2026-10-15 起从三元组放宽）
/ R2 班主任（本班）/ R3 年级长及以上（本年级）。真值在
`app/modules/grades/services/score_guard.py`，这里只是薄转发。

2026-10-14 审计 API-04：`allow_write` 默认值是 true，而本服务的定位是只读。
此前"只读"只靠"当前没写工具"这一事实保证，一旦有人往模块里加一个写工具就会静默生效。
这里在注册完成后做一次**失败即拒绝启动**的断言：只读模式（allow_write=false）下
出现写工具（显式声明 readOnlyHint=False，或工具名带写操作动词）直接记 ERROR 并抛错。
"""
import importlib
import logging

from .. import config

logger = logging.getLogger('stulink.mcp.tools')

MODULES = (
    ('scores', '成绩查询（只读）'),
    ('score_analysis', '成绩分析（只读）'),
)

# 写工具判据之一：工具名前缀（模块里所有工具统一 snake_case 命名）
_WRITE_NAME_PREFIXES = (
    'create_', 'update_', 'delete_', 'remove_', 'add_', 'set_',
    'import_', 'write_', 'save_', 'edit_', 'batch_', 'export_',
    # 2026-10-20 第十六轮：补上其余写动词 —— 原名单漏 sync_/reset_/submit_/
    # upload_/apply_/toggle_ 等，新增一个用这些动词命名、内部走 db_call 的写
    # 工具就会同时绕过「名字判据」与「readOnlyHint 判据」两道软尺子。
    'sync_', 'reset_', 'submit_', 'upload_', 'apply_', 'toggle_',
    'approve_', 'reject_', 'publish_', 'assign_', 'grant_', 'revoke_',
)


def _tool_is_write(tool):
    """判断一个已注册工具是否属于写工具。

    两条判据任一命中即算写工具：
      1. 声明里显式写了 readOnlyHint=False（MCP ToolAnnotations）；
      2. 工具名带写操作动词前缀。
    """
    ann = getattr(tool, 'annotations', None)
    read_only = ann.get('readOnlyHint') if isinstance(ann, dict) else getattr(ann, 'readOnlyHint', None)
    if read_only is False:
        return True
    name = (getattr(tool, 'name', '') or '').lower()
    return name.startswith(_WRITE_NAME_PREFIXES)


def _assert_read_only(server):
    """只读模式（allow_write=false）下拒绝任何写工具：记 ERROR 并终止启动。"""
    if config.load_config().get('allow_write'):
        return
    manager = getattr(server, '_tool_manager', None)
    tools = list(manager.list_tools()) if manager is not None else []
    writes = [t.name for t in tools if _tool_is_write(t)]
    if writes:
        logger.error('MCP 只读模式（allow_write=false）发现写工具，拒绝注册：%s',
                     '、'.join(writes))
        raise RuntimeError(
            'MCP 只读模式不允许注册写工具：' + '、'.join(writes)
            + '；请移除这些工具，或在 mcp_server/config.json 中把 allow_write 改为 true'
              '（本服务定位为只读成绩分析服务，不建议）')
    logger.info('只读模式校验通过：已注册 %d 个工具，无写工具', len(tools))


def register_all(server):
    """逐模块注册工具，返回 [(模块名, 说明), ...] 供启动日志展示。"""
    loaded = []
    for name, label in MODULES:
        module = importlib.import_module(f'.{name}', package=__package__)
        module.register(server)
        loaded.append((name, label))
    _assert_read_only(server)
    return loaded

# -*- coding: utf-8 -*-
"""MCP 服务配置。

首次运行自动生成 mcp_server/config.json（含随机访问令牌），之后直接改该文件即可。
所有键也可用环境变量覆盖（STULINK_MCP_<大写键名>），便于部署时注入而不落盘。
"""
import json
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

# 键名 → (默认值, 说明)：说明会写进自动生成的 config.json，作为使用文档
DEFAULTS = {
    'host': ('127.0.0.1', '监听地址；改为 0.0.0.0 可被局域网内其他设备访问。'
                           '注意：account 模式的账号密码经明文 HTTP 传输，'
                           '仅在受信网段使用，或同时开启 require_token'),
    'port': (8765, '监听端口'),
    'path': ('/mcp', 'MCP 端点路径（客户端填写的 URL 为 http://地址:端口 + 本路径）'),
    'auth_mode': ('account', '接入认证模式：account=每请求校验 StuLink 账号密码（推荐，'
                             '客户端权限与网页端完全一致）；token=仅校验静态访问令牌、'
                             '执行身份固定为 bind_user（兼容旧客户端；⚠ 持有令牌者即以'
                             'bind_user 身份执行全部工具且不受数据范围限制，请勿在不可信网络暴露）'),
    'require_token': (False, 'account 模式下是否额外要求 X-StuLink-Token（双重准入，'
                             '防止端口被随意扫描）；false 时只认账号密码。'
                             '非本机监听（host=0.0.0.0）时建议置 true'),
    'token': ('', '静态访问令牌（token 模式的唯一凭据；account 模式在 require_token=true 时'
                  '作为第二道准入）；留空则自动生成'),
    'bind_user': ('admin', 'token 模式的执行账号（例如 admin）；account 模式不生效——'
                           '每个客户端用自己的账号密码登录'),
    'allow_write': (True, '是否允许写入类工具（录入/修改数据）；false 时写工具直接拒绝'),
    'audit_reads': (False, '是否把只读查询也写入操作日志（写操作始终记录）'),
    'max_rows': (200, '单次返回的最大行数，超出会截断并在结果中提示'),
    'exam_cache_ttl': (300, '成绩分析数据在 MCP 侧的缓存秒数：超过则主动重建（0=沿用主程序默认 900 秒）'),
    'credential_cache_ttl': (300, '账号密码校验结果缓存秒数：0=每次请求都做完整密码校验（较慢）；'
                                  '缓存命中仍会校验账号是否停用/是否改密（立即生效）'),
    'login_fail_limit': (5, '同一账号连续认证失败达到该次数后临时锁定；0=不锁定'),
    'login_lock_seconds': (300, '认证失败锁定时长（秒）'),
    'allowed_hosts': ([], '允许的 Host 头（DNS 重绑定防护）；留空则自动放行本机与监听地址'),
}

_cache = None


def _env_override(key, value):
    """环境变量覆盖：STULINK_MCP_PORT=9000 之类。仅做基础类型转换。"""
    raw = os.environ.get('STULINK_MCP_' + key.upper())
    if raw is None:
        return value
    if isinstance(value, bool):
        return raw.strip().lower() in ('1', 'true', 'yes', 'on')
    if isinstance(value, int):
        try:
            return int(raw)
        except ValueError:
            return value
    if isinstance(value, list):
        return [x.strip() for x in raw.split(',') if x.strip()]
    return raw


def _write_default(path, cfg):
    """写出带说明的初始配置（键顺序固定，方便手改）。"""
    doc = {k: cfg[k] for k in DEFAULTS}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write('\n')
    # 说明以注释形式集中附在文件末尾（JSON 本身不支持注释）
    with open(path, 'a', encoding='utf-8') as f:
        f.write('\n// 配置说明（修改后需重启 MCP 服务生效）\n')
        for k, (_v, desc) in DEFAULTS.items():
            f.write(f'//   {k}: {desc}\n')


def load_config(reload=False):
    """读取配置（带缓存）。缺失的键用默认值补齐，令牌为空时自动生成并落盘。"""
    global _cache
    if _cache is not None and not reload:
        return _cache

    cfg = {k: v[0] for k, v in DEFAULTS.items()}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                # 容忍用户手工添加的 // 注释行
                text = '\n'.join(l for l in f if not l.lstrip().startswith('//'))
            cfg.update(json.loads(text))
        except Exception as exc:  # noqa: BLE001  配置损坏时回落默认值并提示
            print(f'[MCP] 警告：config.json 解析失败（{exc}），已使用默认配置')

    for key in DEFAULTS:
        cfg[key] = _env_override(key, cfg.get(key, DEFAULTS[key][0]))

    if not cfg.get('token'):
        cfg['token'] = secrets.token_urlsafe(32)
        _write_default(CONFIG_PATH, cfg)
        print(f'[MCP] 已生成配置文件：{CONFIG_PATH}')

    _cache = cfg
    return cfg
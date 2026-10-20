# -*- coding: utf-8 -*-
"""StuLink 成绩分析 MCP 服务入口（**只读**）：HTTP(Streamable HTTP) 传输 + 接入认证。

服务范围：只暴露 12 个成绩分析只读工具，权限按三条规则细粒度裁决——
R1 任课教师（**(年级, 学科) 二元组**命中，仅所教学科；2026-10-15 起从三元组放宽）
/ R2 班主任（本班）/ R3 年级长及以上（本年级，校级/管理员=全校）。无任何写入能力。
详见 docs/mcp/MCP服务-成绩分析权限设计.md。

启动方式（二选一）：
  · 双击 mcp_server\\start_mcp.bat（首次会自动创建虚拟环境并装依赖）
  · 命令行：mcp_server\\.venv\\Scripts\\python.exe mcp_server\\run.py

客户端接入（默认 account 模式）：URL = http://<监听地址>:<端口><path>，
请求头携带 StuLink 账号密码（任选其一）：
  · X-StuLink-User: <账号> + X-StuLink-Password: <密码>
  · Authorization: Basic base64(账号:密码)
（require_token=true 时另加 X-StuLink-Token；token 兼容模式见 config.json。）
认证设计见 docs/mcp/MCP服务-认证与授权设计.md。
"""
import functools
import ipaddress
import logging
import os
import socket
import sys

# 把项目根目录加入 sys.path，保证以 "mcp_server.xxx" 形式导入包
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcp.server.mcpserver import MCPServer                    # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from mcp_server import config                                 # noqa: E402
from mcp_server.core import auth, bootstrap                    # noqa: E402
from mcp_server.tools import register_all                      # noqa: E402

logger = logging.getLogger('stulink.mcp')

INSTRUCTIONS = (
    '本服务只提供 StuLink 成绩的只读分析，无任何写入能力。\n'
    '可见范围＝接入账号在网页端的可见范围，**分两层**：\n'
    '· 聚合层（年级/班级/学科统计·分布·上线率·班级对比）**公开**——能看该考试的账号'
    '都能看全年级。分析某个班请用 get_class_score_summary / compare_classes 拿年级对照，'
    '别只报一个孤立的本班数字。\n'
    '· 逐生层（学生个人成绩、班级逐生明细）只给自己的班：任课教师＝所教年级×所教学科，'
    '班主任＝本班全科，年级长及以上＝本年级；班级汇总行的 detail_visible 指出能否下钻。\n'
    '两类考试：kind=exam 普通考试（默认）；kind=timed 限时练（独立库，无总分行、'
    '单科追加、名次＝该科年级名次；分析用 get_subject_analysis 指定 subject）。\n'
    '省额度约定：view 默认 brief（精简、明细走列式 cols+rows），要全字段才传 view=full；'
    '先用 class_name / subject / top 缩小范围，再取数。\n'
    '越权一律返回 {"ok": false, "error": "权限不足：…"}，不要绕过提示继续尝试。'
)


_FAKE_IP_NETS = (ipaddress.ip_network('198.18.0.0/15'),)
"""代理 TUN（Clash 等）的 fake-ip 段，兼 RFC 2544 保留段：不是真实网卡地址，必须排除。"""


def _route_probe_ip():
    """路由探测：UDP connect 只查路由表、不真正发包；但经代理 TUN 时会拿到 fake-ip。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('10.255.255.255', 1))
        return sock.getsockname()[0]
    except Exception:  # noqa: BLE001  离线环境取不到就返回空
        return ''
    finally:
        sock.close()


@functools.lru_cache(maxsize=1)
def _local_ipv4s():
    """本机可用的 IPv4 列表（路由探测在前，其后是各网卡地址），供白名单与启动横幅共用。

    剔除环回 / 链路本地 / 多播 / 未指定地址，以及代理 TUN 的 fake-ip 段：否则
    「本机局域网地址」会被探测成 198.18.0.1，白名单与横幅一起指错地方（Host 头 421）。
    """
    raw = [_route_probe_ip()]
    try:
        raw.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:  # noqa: BLE001  主机名解析失败不影响启动
        pass
    out = []
    for ip in raw:
        if not ip or ip in out:
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_unspecified or any(addr in net for net in _FAKE_IP_NETS)):
            continue
        out.append(ip)
    return out


def _lan_ip():
    """挑一个最可能被别的设备访问到的本机地址。

    首选路由探测结果（默认路由所在网卡）；它被剔除（如代理 fake-ip）或不是内网地址时，
    再按 192.168 → 10 → 172.16-31 的优先级挑，避免选中虚拟网卡。
    """
    ips = _local_ipv4s()
    if not ips:
        return ''
    first = ips[0]
    if first.startswith(('192.168.', '10.', '172.')):
        return first

    def rank(ip):
        return (0 if ip.startswith('192.168.') else
                1 if ip.startswith('10.') else
                2 if ip.startswith('172.') else 3)

    return min(ips, key=rank)


def _allowed_hosts(cfg):
    """DNS 重绑定防护的 Host 白名单：本机名 + 监听地址 + 各网卡 IP（含端口形式）。"""
    if cfg.get('allowed_hosts'):
        return list(cfg['allowed_hosts'])
    port = int(cfg['port'])
    names = {'localhost', '127.0.0.1', socket.gethostname()}
    host = (cfg.get('host') or '').strip()
    if host and host not in ('0.0.0.0', '::', '::0'):
        names.add(host)
    names.update(_local_ipv4s())
    out = []
    for name in sorted(names):
        out.extend([name, f'{name}:{port}'])
    return out


def _auth_mode(cfg):
    """当前接入认证模式：account（账号密码，默认）| token（静态令牌，兼容）。"""
    return (cfg.get('auth_mode') or 'account').strip().lower()


def build_asgi_app(cfg):
    """组装 ASGI 应用：MCP(Streamable HTTP) → 接入认证中间件。"""
    if _auth_mode(cfg) == 'token':
        bootstrap.warm_up(cfg['bind_user'])      # 兼容模式：固定执行身份（启动即校验）
    else:
        bootstrap.prepare_app()                  # 账号密码模式：多用户，每请求认证
    server = MCPServer(
        name='stulink-scores',
        title='StuLink 成绩分析',
        instructions=INSTRUCTIONS,
        version='1.0.0',
    )
    loaded = register_all(server)
    logger.info('已注册工具模块：%s', '、'.join(label for _n, label in loaded))

    starlette_app = server.streamable_http_app(
        streamable_http_path=cfg['path'] or '/mcp',
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=_allowed_hosts(cfg),
        ),
        host=cfg['host'] or '127.0.0.1',
    )
    return auth.AccessGuard(starlette_app, cfg), server


def _mask_token(token):
    """启动横幅只显示令牌首尾片段，避免截图/日志泄露完整令牌。"""
    text = token or ''
    if len(text) <= 12:
        return '****'
    return f'{text[:6]}…{text[-4:]}'


def _client_example(cfg, url, mode):
    """客户端配置示例（按认证模式给出对应的 headers）。"""
    lines = ['  客户端配置示例（Streamable HTTP）：', '  {', f'    "url": "{url}",']
    if mode == 'account':
        lines.append('    "headers": {')
        if cfg.get('require_token'):
            lines.append('      "X-StuLink-User": "<你的 StuLink 账号>",')
            lines.append('      "X-StuLink-Password": "<你的账号密码>",')
            lines.append('      "X-StuLink-Token": "<服务令牌，见 config.json>"')
        else:
            lines.append('      "X-StuLink-User": "<你的 StuLink 账号>",')
            lines.append('      "X-StuLink-Password": "<你的账号密码>"')
        lines.append('    }')
    else:
        lines.append('    "headers": { "Authorization": "Bearer <服务令牌>" }')
    lines.append('  }')
    return lines


def _print_banner(cfg, me):
    """启动横幅：把客户端需要的信息一次印全，省得翻文档。"""
    mode = _auth_mode(cfg)
    host = cfg['host'] or '127.0.0.1'
    shown = _lan_ip() if host == '0.0.0.0' else host
    url = f"http://{shown}:{cfg['port']}{cfg['path'] or '/mcp'}"
    if mode == 'token':
        identity_line = (f"  执行身份 : {me['real_name']}（{me['username']}，{me['role']}）"
                         ' · 静态令牌模式')
    else:
        identity_line = '  执行身份 : 每个客户端用自己的 StuLink 账号密码（每请求认证）'
    lines = [
        '=' * 68,
        '  StuLink 成绩分析 MCP 服务已启动（只读）',
        '',
        f"  接入地址 : {url}",
        f"  认证模式 : {'账号密码（每请求校验）' if mode == 'account' else '静态访问令牌（兼容模式）'}",
        f"  服务令牌 : {_mask_token(cfg['token'])}（完整值见 mcp_server/config.json；"
        + ('必填' if mode == 'token' or cfg.get('require_token') else '可选') + '）',
        identity_line,
        '  工具集   : 成绩分析（12 个只读工具，无写入能力）；'
        'kind=timed 查限时练（独立库）',
        '  权限口径 : 聚合层（年级/班级/学科统计·分布·上线率·班级对比）公开，',
        '             逐生明细=任课教师 所教年级×所教学科 / 班主任 本班全科',
        '  省额度   : view 默认 brief（紧凑输出 + 明细列式），view=full 才给全字段',
        '',
    ]
    lines.extend(_client_example(cfg, url, mode))
    # 2026-10-14 审计 API-03：token 模式 = 一个静态令牌 + 固定 bind_user（默认 admin）
    # 身份，令牌泄露即该账号可见数据全部泄露，且无法区分客户端。启动就说清楚。
    if mode == 'token':
        lines.extend([
            '',
            '  【风险提示】当前为 token 兼容模式：所有客户端共用固定账号'
            f" {cfg.get('bind_user') or 'admin'} 的身份执行，",
            '         令牌一旦泄露 = 该账号可见的成绩数据全部可读，且无法追溯到具体人。',
            '         生产请在 config.json 中把 auth_mode 改回 account（每请求校验账号密码），',
            '         或至少把 bind_user 改成权限最小化的专用账号。',
        ])
    # 2026-10-13（全面审计 M-5）：局域网暴露时的准入自检 —— 只认账号密码，等于让
    # 同网段任何设备都能拿真实教师账号反复试探本端口，这里显式提醒一次。
    if (host not in ('127.0.0.1', 'localhost', '::1')
            and not (cfg.get('require_token') or mode == 'token')):
        lines.extend([
            '',
            '  【安全提示】服务暴露到局域网（host=0.0.0.0），当前只有账号密码一道准入：',
            '         同网段任何设备都能拿账号口令试探本端口。已有防护：同来源+同账号连错 5 次',
            '         → 锁该来源（不锁账号）；同来源失败 20 次 → 挡该来源；跨来源累计 → 只加延迟。',
            '         要更严可选：config.json 的 require_token 改 true（客户端加',
            '         X-StuLink-Token 头），或把 host 收回 127.0.0.1（仅本机可访问）。',
        ])
    lines.extend([
        '',
        f"  配置文件 : {config.CONFIG_PATH}",
        '  停止服务 : 在本窗口按 Ctrl+C',
        '=' * 68,
    ])
    try:
        print('\n'.join(lines), flush=True)
    except UnicodeEncodeError:  # 控制台编码异常时不阻塞启动
        print(f'StuLink MCP started: {url}', flush=True)


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
    cfg = config.load_config()
    try:
        app, _server = build_asgi_app(cfg)
        me = (bootstrap.warm_up(cfg['bind_user'])
              if _auth_mode(cfg) == 'token' else None)
    except Exception as exc:  # noqa: BLE001  启动期错误要打印清楚原因
        print(f'[MCP] 启动失败：{exc}', flush=True)
        return 1
    _print_banner(cfg, me)
    import uvicorn
    uvicorn.run(app, host=cfg['host'] or '127.0.0.1', port=int(cfg['port']),
                log_level='info')
    return 0


if __name__ == '__main__':
    sys.exit(main())
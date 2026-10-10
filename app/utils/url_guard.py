# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""出站 URL 守卫（H-9 BYOK SSRF 的唯一实现）。

背景（清单 H-9）：AI 分析允许「自定义（OpenAI 兼容）」服务商，用户可填任意
`base_url`，服务端随即把 `Authorization: Bearer <key>` 与成绩数据发往该地址——
任何持 `grades.view` 的人都能探测内网/环回/云元数据，并把**全局 Key**外发。

口径（用户 2026-09-23 确认）：保留自定义，但
  1) 域名必须在白名单：内置服务商域名，或管理员显式审批的域名
     （环境变量 `AI_ALLOWED_BASE_URLS`，或 `data/ai_approved_base_urls.txt`，一行一个）；
  2) 出站前做 DNS 解析，解析出的**任一** IP 属于私网/环回/链路本地/组播/保留
     /未指定/非全局地址即拒绝（含 169.254.169.254 云元数据）；
  3) 非 https 的地址必须由管理员显式审批（避免 Key 明文外发）。

注：解析校验与真正建连之间存在极小的 DNS rebinding 时间窗，属已知残余风险；
对 `127.0.0.1`、`10.x`、`192.168.x`、`169.254.169.254` 这类静态地址可直接拦截。
"""
import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

_log = logging.getLogger('stulink.outbound')

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APPROVED_FILE = os.path.join(_ROOT, 'data', 'ai_approved_base_urls.txt')


def _builtin_hosts():
    """内置服务商域名（来自 ai_providers 注册表）"""
    try:
        from app.modules.grades.services import ai_providers
        hosts = set()
        for cfg in ai_providers.PROVIDERS.values():
            host = urlparse(cfg.get('base_url') or '').hostname
            if host:
                hosts.add(host.lower())
        return hosts
    except Exception:  # noqa: BLE001  导入失败时退化为「仅显式审批」
        return set()


def _explicitly_approved_hosts():
    """管理员显式审批的域名：环境变量 + data/ai_approved_base_urls.txt"""
    hosts = set()
    raw = os.environ.get('AI_ALLOWED_BASE_URLS', '') or ''
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        host = urlparse(part if '//' in part else 'https://' + part).hostname
        if host:
            hosts.add(host.lower())
    try:
        if os.path.isfile(APPROVED_FILE):
            with open(APPROVED_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    host = urlparse(line if '//' in line else 'https://' + line).hostname
                    if host:
                        hosts.add(host.lower())
    except OSError:
        pass
    return hosts


def host_approved(host):
    """域名是否允许出站（内置服务商或管理员审批）"""
    host = (host or '').lower()
    if not host:
        return False
    return host in _builtin_hosts() or host in _explicitly_approved_hosts()


def _norm_host(value):
    """把用户输入的“地址/域名”统一成 host"""
    v = (value or '').strip()
    if not v:
        return ''
    host = urlparse(v if '//' in v else 'https://' + v).hostname or ''
    return host.lower()


def list_approved_hosts():
    """管理员显式审批的域名清单（不含内置服务商），供设置页展示"""
    return sorted(_explicitly_approved_hosts())


def builtin_hosts():
    """内置服务商域名（只读展示用）"""
    return sorted(_builtin_hosts())


def add_approved_host(value):
    """管理员审批一个域名（写入 data/ai_approved_base_urls.txt，幂等）

    返回 (ok, message)。校验规则与出站守卫一致：必须是合法主机名。
    """
    host = _norm_host(value)
    if not host or '.' not in host or len(host) > 200:
        return False, '域名格式不正确（示例：api.openai.com 或 https://api.moonshot.cn/v1）'
    if host in _builtin_hosts():
        return True, '%s 属内置服务商，本来就允许出站' % host
    if host in _explicitly_approved_hosts():
        return True, '%s 已在白名单中' % host
    try:
        os.makedirs(os.path.dirname(APPROVED_FILE), exist_ok=True)
        with open(APPROVED_FILE, 'a', encoding='utf-8') as f:
            f.write(host + '\n')
    except OSError as e:
        return False, '写入白名单文件失败：%s' % e
    _log.info('AI 出站域名已审批 host=%s', host)
    return True, '已批准 %s，可直接使用该地址' % host


def remove_approved_host(value):
    """撤销审批（从文件中移除该行）"""
    host = _norm_host(value)
    if not host:
        return False, '域名格式不正确'
    if not os.path.isfile(APPROVED_FILE):
        return False, '白名单文件不存在'
    try:
        with open(APPROVED_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        kept = [ln for ln in lines if _norm_host(ln) != host]
        if len(kept) == len(lines):
            return False, '%s 不在白名单中' % host
        with open(APPROVED_FILE, 'w', encoding='utf-8') as f:
            f.writelines(kept)
    except OSError as e:
        return False, '更新白名单文件失败：%s' % e
    _log.info('AI 出站域名已撤销 host=%s', host)
    return True, '已移除 %s' % host


def _is_blocked_ip(ip_str, allow_private=False):
    """IP 是否禁止出站

    v1.19.0 口径调整（自建 AI 网关场景）：
      · **链路本地 / 组播 / 保留 / 未指定** —— 任何情况都拦（如 169.254.169.254 云元数据）；
      · **私网 / 环回**（10.x、192.168.x、127.x…）—— 默认拦；
        但若该地址已由**管理员显式批准**（allow_private=True，例如局域网内的自建
        OpenAI 兼容网关），则放行：这是管理员的有意决定，不是越权探测。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # 永远拦截：链路本地（云元数据）、组播、保留、未指定
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if ip.is_loopback:
        # 环回只允许在“管理员显式批准”时走（自建网关常见 http://127.0.0.1:xxxx）
        return not allow_private
    if ip.is_private:
        return not allow_private
    return not ip.is_global


def assert_outbound_url_allowed(url):
    """校验出站地址，不合法则抛 ValueError（调用方转成用户可见提示）。

    :return: 规范化后的主机名（便于调用方记日志/审计）
    """
    parsed = urlparse(url or '')
    if parsed.scheme not in ('http', 'https'):
        raise ValueError('接口地址只允许 http/https')
    host = parsed.hostname
    if not host:
        raise ValueError('接口地址缺少主机名')

    explicit = host.lower() in _explicitly_approved_hosts()
    if not (host_approved(host)):
        raise ValueError(
            '接口地址域名未获批准：%s\n'
            '自定义（OpenAI 兼容）地址需管理员审批。管理员可在「用户设置 → AI 出站域名白名单」'
            '一键批准，或写入 data/ai_approved_base_urls.txt / 环境变量 AI_ALLOWED_BASE_URLS。'
            % host)
    if parsed.scheme != 'https' and not explicit:
        raise ValueError('接口地址需使用 https（明文 http 只能用于管理员显式审批的地址）')

    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        infos = socket.getaddrinfo(host, port)
    except Exception:
        raise ValueError('接口地址域名解析失败，请检查地址是否正确')
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_ip(ip_str, allow_private=explicit):
            raise ValueError(
                '接口地址解析到内网/保留地址（%s），已拒绝（防 SSRF）。\n'
                '如确是局域网内的自建 AI 网关（如 http://10.x.x.x:xxxx/v1），'
                '可由管理员在「用户设置 → AI 出站域名白名单」中把该地址加入后重试；'
                '云元数据等链路本地地址永远不允许。' % ip_str)

    _log.info('AI 出站请求已放行 host=%s scheme=%s', host, parsed.scheme)
    return host.lower()

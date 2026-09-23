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


def _is_blocked_ip(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (not ip.is_global) or ip.is_private or ip.is_loopback \
        or ip.is_link_local or ip.is_multicast or ip.is_reserved \
        or ip.is_unspecified


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
            '接口地址域名未获批准：自定义（OpenAI 兼容）地址需由管理员审批后写入 '
            'data/ai_approved_base_urls.txt 或环境变量 AI_ALLOWED_BASE_URLS')
    if parsed.scheme != 'https' and not explicit:
        raise ValueError('接口地址需使用 https（明文 http 只能用于管理员显式审批的地址）')

    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        infos = socket.getaddrinfo(host, port)
    except Exception:
        raise ValueError('接口地址域名解析失败，请检查地址是否正确')
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_ip(ip_str):
            raise ValueError(
                f'接口地址解析到内网/保留地址（{ip_str}），已拒绝（防 SSRF）')

    _log.info('AI 出站请求已放行 host=%s scheme=%s', host, parsed.scheme)
    return host.lower()

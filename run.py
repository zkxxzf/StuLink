# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import ipaddress
import os
import socket
import sys

# 确保 data 目录存在
os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)

from app import create_app

app = create_app()


def _set_console_title(title):
    """把控制台标题改成提醒文案（Windows 下窗口最小化也能看到）"""
    try:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        else:
            sys.stdout.write(f'\033]0;{title}\007')
            sys.stdout.flush()
    except Exception:
        pass


# 不能当作「别的设备能访问到的本机地址」的网段：代理 TUN 的 fake-ip 段（兼 RFC 2544
# 保留段）、VirtualBox / Docker Toolbox 虚拟网卡段
_EXCLUDED_NETS = tuple(ipaddress.ip_network(n) for n in (
    '198.18.0.0/15', '192.168.56.0/24', '192.168.99.0/24'))


def _lan_ip():
    """探测本机局域网 IP：UDP connect 只查本机路由表、不真正发包；取不到时枚举网卡。
    失败返回空串。

    必须剔除代理 TUN 的 fake-ip 段：本机开着 Clash 之类代理时，默认路由会落在它的虚拟
    网卡上，探测结果会变成保留段 198.18.0.1（同时它也不是别人能访问的地址）。
    """
    cands = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))   # 走默认路由的网卡即首选内网 IP
        cands.append(s.getsockname()[0])
    except Exception:
        pass
    finally:
        s.close()
    # 兜底：枚举主机名解析结果（离线/无网关环境）
    try:
        cands.extend(info[4][0] for info in
                     socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except Exception:
        pass
    out = []
    for ip in cands:
        if not ip or ip in out:
            continue
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_unspecified or any(addr in net for net in _EXCLUDED_NETS)):
            continue
        out.append(ip)
    if not out:
        return ''
    first = out[0]                     # 路由探测结果：默认路由所在网卡，最可能是它
    if first.startswith(('192.168.', '10.', '172.')):
        return first
    return min(out, key=lambda ip: (0 if ip.startswith('192.168.') else
                                    1 if ip.startswith('10.') else
                                    2 if ip.startswith('172.') else 3))


def _print_keep_running_notice(url):
    """启动后提示：本窗口为后端服务进程，不能关闭"""
    bar = '=' * 64
    lines = [
        bar,
        '  【重要提醒】本窗口是 StuLink 后端服务进程，请保持运行，不要关闭！',
        '',
        '   · 关闭窗口 / 按 Ctrl+C / 结束进程 => 系统将无法访问',
        '   · 日常使用请把窗口最小化保留在后台，不影响浏览器操作',
        '   · 浏览器访问地址：' + url,
        bar,
        '',
    ]
    try:
        print('\n'.join(lines))
    except UnicodeEncodeError:  # 控制台编码异常时不阻塞启动
        pass


if __name__ == '__main__':
    # 生产模式为默认；--dev 显式开启开发模式（仅限本地）
    debug_mode = '--dev' in sys.argv
    if debug_mode:
        import warnings
        warnings.warn('⚠ 开发模式已启用，切勿在生产环境使用！', stacklevel=2)
        _set_console_title('StuLink 后端服务运行中 —— 请勿关闭此窗口！')
        # config.py 为生产性能关闭了模板自动重载，开发模式必须打开，
        # 否则改了 html 服务器仍返回旧模板（会一直看到旧的 CDN 引用与旧脚本）
        app.config['TEMPLATES_AUTO_RELOAD'] = True
        app.jinja_env.auto_reload = True
        print('宿舍管理系统已启动 (开发模式 - 仅限本地)')
        print('请在浏览器访问: http://localhost:5000')
        _print_keep_running_notice('http://localhost:5000')
        app.run(debug=True, host='127.0.0.1', port=5000)
    else:
        from waitress import serve
        _set_console_title('StuLink 后端服务运行中 —— 请勿关闭此窗口！')
        print('宿舍管理系统已启动 (生产模式)')
        print('请在浏览器访问: http://localhost:5000')
        # 局域网地址：启动时探测实际本机 IP 并显示（供其他电脑/手机访问）
        lan = _lan_ip()
        lan_url = f'http://{lan}:5000' if lan else 'http://本机IP:5000（未能自动获取，请在网络设置中查看）'
        _print_keep_running_notice(f'http://localhost:5000  或  {lan_url}')
        # 并发调优：waitress 默认只开 4 个线程，成绩分析各 tab / 全局对比（班级×学科宽表）
        # / Excel 导入导出 / AI 分析这类慢请求会长时间占住线程，第 5 个并发请求就开始排队
        # （表现为后台反复刷 “WARNING:waitress.queue:Task queue depth is N”、页面转圈久）。
        # Web 请求多为 I/O 等待，16 足够；不建议再调高——底层是 SQLite，写操作整库串行，
        # 线程过多只会把等待从“网络层”挪到“数据库锁”上，反而容易触发 database is locked。
        # 可用环境变量临时覆盖（本地压测/特殊场景）。
        serve(app, host='0.0.0.0', port=5000,
              threads=int(os.environ.get('STULINK_THREADS', '16')),
              channel_timeout=int(os.environ.get('STULINK_CHANNEL_TIMEOUT', '120')))



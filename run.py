# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
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
        print('请在浏览器访问: http://0.0.0.0:5000')
        _print_keep_running_notice('http://0.0.0.0:5000  或  http://本机IP:5000')
        serve(app, host='0.0.0.0', port=5000)



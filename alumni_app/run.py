"""往届生查询系统 启动入口"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['ALUMNI_CONFIG'] = os.path.join(os.path.dirname(__file__), 'config.py')
from app import create_app

app = create_app()

def _print_keep_running_notice(url):
    """启动后提示：本窗口为后端服务进程，不能关闭"""
    bar = '=' * 64
    lines = [
        bar,
        '  【重要提醒】本窗口是往届生查询后端服务进程，请保持运行，不要关闭！',
        '',
        '   · 关闭窗口 / 按 Ctrl+C / 结束进程 => 查询系统将无法访问',
        '   · 日常使用请把窗口最小化保留在后台',
        '   · 浏览器访问地址：' + url,
        bar,
        '',
    ]
    try:
        print('\n'.join(lines))
    except UnicodeEncodeError:  # 控制台编码异常时不阻塞启动
        pass


if __name__ == '__main__':
    # v1.19.56.64 H-7 P0 修复：debug=True 会开启 Werkzeug 交互式调试器，PIN 可爆破 => RCE
    # 默认生产模式；开发时显式传 --dev 才开 debug（且仅限本地）
    debug_mode = '--dev' in sys.argv
    # 2026-10-12 H-7 收敛：--dev 只绑回环，调试器/源码回溯不再对局域网可达；
    # 生产模式才对外（与主应用 run.py 的 dev/prod 分流口径一致）
    host = '127.0.0.1' if debug_mode else '0.0.0.0'
    print('往届生查询系统已启动' + (' (开发模式 - 仅限本地)' if debug_mode else ' (生产模式)'))
    print(f'请在浏览器访问: http://{host}:5001')
    _print_keep_running_notice(f'http://{host}:5001')
    app.run(debug=debug_mode, host=host, port=5001, use_reloader=False)

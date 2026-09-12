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
    print('往届生查询系统已启动')
    print('请在浏览器访问: http://localhost:5001')
    _print_keep_running_notice('http://localhost:5001')
    app.run(debug=True, host='0.0.0.0', port=5001)

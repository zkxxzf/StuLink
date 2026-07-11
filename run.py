# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
import sys

# 确保 data 目录存在
os.makedirs(os.path.join(os.path.dirname(__file__), 'data'), exist_ok=True)

from app import create_app

app = create_app()

if __name__ == '__main__':
    # 生产模式为默认；--dev 显式开启开发模式（仅限本地）
    debug_mode = '--dev' in sys.argv
    if debug_mode:
        import warnings
        warnings.warn('⚠ 开发模式已启用，切勿在生产环境使用！', stacklevel=2)
        print('宿舍管理系统已启动 (开发模式 - 仅限本地)')
        print('请在浏览器访问: http://localhost:5000')
        app.run(debug=True, host='127.0.0.1', port=5000)
    else:
        from waitress import serve
        print('宿舍管理系统已启动 (生产模式)')
        print('请在浏览器访问: http://0.0.0.0:5000')
        serve(app, host='0.0.0.0', port=5000)



"""往届生查询系统 启动入口"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['ALUMNI_CONFIG'] = os.path.join(os.path.dirname(__file__), 'config.py')
from app import create_app

app = create_app()

if __name__ == '__main__':
    print('往届生查询系统已启动')
    print('请在浏览器访问: http://localhost:5001')
    app.run(debug=True, host='0.0.0.0', port=5001)

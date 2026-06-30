# StuLink v1.4.5 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Flask, render_template, request
from config import Config
from app.extensions import db, login_manager, csrf
from markupsafe import escape
import gzip
import io


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # 模板配置：生产环境从 Config 读取，开发环境可覆盖
    app.jinja_env.auto_reload = app.config.get('TEMPLATES_AUTO_RELOAD', False)

    # 初始化扩展
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    # 安全过滤器：先转义 HTML 再将 \n 转为 <br>（替代危险的 |safe）
    @app.template_filter('nl2br')
    def nl2br_filter(text):
        return escape(str(text)).replace('\n', '<br>')

    # Gzip 压缩响应（提升传输速度）
    @app.after_request
    def compress_response(response):
        accept_encoding = request.headers.get('Accept-Encoding', '')
        if 'gzip' not in accept_encoding.lower():
            return response
        
        if (response.status_code < 200 or response.status_code >= 300 or 
            'Content-Encoding' in response.headers or
            response.content_length is None or 
            response.content_length < 500):
            return response
        
        # 只压缩文本类型
        content_type = response.headers.get('Content-Type', '').lower()
        if not any(t in content_type for t in ['text/', 'application/json', 'application/javascript']):
            return response
        
        try:
            compressed_data = gzip.compress(response.data)
            response.data = compressed_data
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = len(compressed_data)
        except Exception:
            pass  # 压缩失败则返回原始数据
        
        return response

    # CSRF 错误友好提示（Edge 等浏览器 cookie 策略较严时可能触发）
    @app.errorhandler(400)
    def bad_request(e):
        return render_template('error.html',
                               code=400,
                               message='请求无效，请刷新页面后重试。如果问题持续，请清除浏览器缓存/Cookie后再试。'), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('error.html', code=403, message='您没有权限访问此页面'), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template('error.html', code=404, message='页面不存在'), 404

    # 注册蓝图（模块化架构）
    from app.modules import register_blueprints
    register_blueprints(app)

    return app

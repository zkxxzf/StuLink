"""往届生查询系统"""
from flask import Flask
from flask_wtf.csrf import CSRFProtect
from config import Config
from app.auth import init_auth

csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.jinja_env.auto_reload = True
    # M-7：启用 CSRF 防护（此前本站唯一 POST /login 完全裸奔）
    csrf.init_app(app)
    print(f'[ALUMNI] Template folder: {app.template_folder} | auto_reload: {app.jinja_env.auto_reload}')
    print(f'[ALUMNI] SECRET_KEY in app config: len={len(app.config["SECRET_KEY"])}')
    print(f'[ALUMNI] SECRET_KEY is None: {app.config["SECRET_KEY"] is None}')
    print(f'[ALUMNI] SECRET_KEY is empty: {app.config["SECRET_KEY"] == ""}')

    # 用户认证（复用主项目 system.db）
    init_auth(app)

    # 注册路由
    from app.routes.basic import bp as basic_bp
    from app.routes.dormitory import bp as dormitory_bp
    app.register_blueprint(basic_bp)
    app.register_blueprint(dormitory_bp, url_prefix='/dormitory')

    return app

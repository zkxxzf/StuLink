"""模块蓝图注册入口"""
def register_blueprints(app):
    # 认证模块（共享）
    from app.modules.auth.routes import bp as auth_bp
    app.register_blueprint(auth_bp)

    # 欢迎页（/）
    from app.modules.welcome.routes import bp as welcome_bp
    app.register_blueprint(welcome_bp)

    # 宿舍管理模块
    from app.modules.dormitory.routes.dashboard import bp as dashboard_bp
    from app.modules.dormitory.routes.rooms import bp as rooms_bp
    from app.modules.dormitory.routes.assignments import bp as assignments_bp
    from app.modules.dormitory.routes.statistics import bp as statistics_bp
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(rooms_bp)
    app.register_blueprint(assignments_bp)
    app.register_blueprint(statistics_bp)

    # 系统管理模块
    from app.modules.system.routes.students import bp as students_bp
    from app.modules.system.routes.users import bp as users_bp
    from app.modules.system.routes.dictionary import bp as dictionary_bp
    from app.modules.system.routes.class_profile import bp as class_profile_bp
    from app.modules.system.routes.perm_groups import bp as perm_groups_bp
    from app.modules.system.routes.grade_mgmt import bp as grade_mgmt_bp
    app.register_blueprint(students_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(dictionary_bp)
    app.register_blueprint(class_profile_bp)
    app.register_blueprint(perm_groups_bp)
    app.register_blueprint(grade_mgmt_bp)

    # 占位模块
    from app.modules.points.routes import bp as points_bp
    from app.modules.grades.routes import bp as grades_bp
    app.register_blueprint(points_bp)
    app.register_blueprint(grades_bp)

# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0

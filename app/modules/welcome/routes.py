# StuLink v1.4.6 2026-06-30
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
from flask import Blueprint, render_template
from flask_login import login_required, current_user

bp = Blueprint('welcome', __name__)


@bp.route('/')
@login_required
def index():
    return render_template('welcome/index.html')

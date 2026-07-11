# StuLink v1.6.1 2026-07-09
# Copyright (c) 2026 zkxxzf. Apache License 2.0
from flask import Blueprint, render_template
from flask_login import login_required, current_user

bp = Blueprint('welcome', __name__)


@bp.route('/')
@login_required
def index():
    return render_template('welcome/index.html')



# StuLink Alumni v1.0.0 2026-07-03
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def get_data_path(filename):
    """获取数据文件路径：优先环境变量，其次 Docker 路径，最后开发环境路径"""
    data_dir = os.environ.get('DATA_DIR', '')
    if data_dir:
        return os.path.join(data_dir, filename)
    docker_path = os.path.join('/app', 'data', filename)
    if os.path.exists(os.path.dirname(docker_path)):
        return docker_path
    return os.path.join(BASE_DIR, '..', 'data', filename)


def _get_secret_key():
    """获取 SECRET_KEY：环境变量（非空）> 文件 > 自动生成并持久化"""
    env_key = os.environ.get('SECRET_KEY', '').strip()
    if env_key:
        print(f'[ALUMNI] SECRET_KEY from environment (len={len(env_key)})')
        return env_key
    
    key_file = get_data_path('.secret_key')
    
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r') as f:
                key = f.read().strip()
                print(f'[ALUMNI] SECRET_KEY from file: {key_file} (len={len(key)})')
                return key
    except Exception as e:
        print(f'[ALUMNI] Error reading {key_file}: {e}')
    
    new_key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    try:
        with open(key_file, 'w') as f:
            f.write(new_key)
        print(f'[ALUMNI] SECRET_KEY auto-generated and saved to: {key_file} (len={len(new_key)})')
    except Exception as e:
        print(f'[ALUMNI] Error saving SECRET_KEY to {key_file}: {e}')
    return new_key


class Config:
    SECRET_KEY = _get_secret_key()
    HISTORY_DB = get_data_path('history.db')
    SYSTEM_DB = get_data_path('system.db')
    SESSION_COOKIE_NAME = 'alumni_session'
    SESSION_COOKIE_HTTPONLY = True
    
    print(f'[ALUMNI] Config.SECRET_KEY set (len={len(SECRET_KEY)})')

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
    """获取往届站 SECRET_KEY（P1 M-11：与主站隔离）。

    优先级：ALUMNI_SECRET_KEY 环境变量 > data/.alumni_secret_key > 首次生成。
    **不再读取主站 data/.secret_key** —— 两站共用一把钥时，任一站泄露即可伪造
    另一站会话；往届站只读历史数据，没有理由拿主站密钥。
    旧部署升级后第一次启动会生成新钥，往届站既有会话失效一次（符合预期）。
    """
    env_key = os.environ.get('ALUMNI_SECRET_KEY', '').strip()
    if env_key:
        print(f'[ALUMNI] SECRET_KEY from ALUMNI_SECRET_KEY (len={len(env_key)})')
        return env_key

    key_file = get_data_path('.alumni_secret_key')
    if os.path.exists(key_file):
        try:
            with open(key_file, 'r') as f:
                key = f.read().strip()
        except OSError as e:
            raise RuntimeError(f'[ALUMNI] 无法读取独立密钥文件：{key_file}') from e
        if not key:
            raise RuntimeError(f'[ALUMNI] 独立密钥文件为空：{key_file}')
        print(f'[ALUMNI] SECRET_KEY from file: {key_file} (len={len(key)})')
        return key

    new_key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    try:
        with open(key_file, 'w') as f:
            f.write(new_key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        print(f'[ALUMNI] SECRET_KEY auto-generated and saved to: {key_file} '
              f'(len={len(new_key)})')
    except Exception as e:  # noqa: BLE001 写入失败仍可用本次进程密钥
        print(f'[ALUMNI] Error saving SECRET_KEY to {key_file}: {e}')
    return new_key


class Config:
    SECRET_KEY = _get_secret_key()
    HISTORY_DB = get_data_path('history.db')
    SYSTEM_DB = get_data_path('system.db')
    SESSION_COOKIE_NAME = 'alumni_session'
    SESSION_COOKIE_HTTPONLY = True
    # M-7：CSRF token 有效期（与主应用一致，8 小时；None=永不过期）
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = int(os.environ.get('ALUMNI_CSRF_TIME_LIMIT', '28800'))
    
    print(f'[ALUMNI] Config.SECRET_KEY set (len={len(SECRET_KEY)})')

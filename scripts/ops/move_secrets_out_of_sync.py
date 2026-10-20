# -*- coding: utf-8 -*-
"""H-07：把 SECRET_KEY / 加密密钥 / .env 从同步盘复制到受控目录。

只复制、不移动、不删除原文件；目标已存在且内容不同则拒绝覆盖（除非 --force）。
默认 dry-run，--apply 需同时 --confirm。

用法：
    py -3.12 scripts/ops/move_secrets_out_of_sync.py --target-dir D:/stulink-secrets
    py -3.12 scripts/ops/move_secrets_out_of_sync.py --target-dir D:/stulink-secrets --apply --confirm

复制完成后，按输出的环境变量片段重启服务；确认新路径生效且登录/解密正常后，
再由人工删除 data/ 下的旧密钥（本脚本不代删）。
"""
import argparse
import hashlib
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

FILES = [
    ('data/.secret_key', 'SECRET_KEY', 'text', 64),
    ('data/.encryption_key', 'ENCRYPTION_KEY', 'bin', 32),
    ('.env', 'ENV_FILE', 'text', 0),
]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _describe(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    return {'rel': rel, 'path': path, 'size': size,
            'sha256': _sha256(path)}


def _copy_one(item, target_dir, force):
    dst = os.path.join(target_dir, os.path.basename(item['rel']))
    if os.path.exists(dst):
        if _sha256(dst) == item['sha256']:
            print(f'  [SKIP] 已存在且内容一致：{dst}')
            return dst
        if not force:
            print(f'  [拒绝对覆盖] 目标已存在且内容不同：{dst}（需要 --force）')
            return None
    shutil.copy2(item['path'], dst)
    try:
        os.chmod(dst, 0o600)
    except OSError:
        pass
    if _sha256(dst) != item['sha256']:
        print(f'  [失败] 复制后校验不一致：{dst}')
        return None
    print(f'  [OK] {item["rel"]} -> {dst}')
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target-dir', required=True,
                    help='受控目录（必须在同步盘之外）')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--confirm', action='store_true')
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    target_dir = os.path.abspath(args.target_dir)
    # 2026-10-20 审计修复：同步盘判定与 app/utils/crypto.py 的 _is_unsafe_path 同口径
    # （原先只看「同步 / onedrive」，漏了 dropbox / nutstore / 百度网盘 / 坚果云）。
    _sync_marks = ('同步', 'onedrive', 'dropbox', 'nutstore', '百度网盘', '坚果云')
    if any(m in target_dir.lower() for m in _sync_marks):
        print('拒绝：目标目录仍在同步盘内，请换到本地受控目录。')
        return 2
    if args.apply and not args.confirm:
        print('拒绝执行：--apply 必须同时带 --confirm。')
        return 2

    print('== 密钥迁出同步盘（只复制，不移动/不删除）==')
    items = []
    for rel, label, _kind, _min_len in FILES:
        item = _describe(rel)
        if item is None:
            print(f'  [缺失] {rel}（跳过）')
            continue
        print(f'  {label}: {rel}  {item["size"]} B  sha256={item["sha256"][:12]}...')
        items.append(item)

    if not items:
        print('没有可复制的文件。')
        return 1
    if not args.apply:
        print(f'\n预览完成。目标目录：{target_dir}')
        print(f'执行：--target-dir {target_dir} --apply --confirm')
        return 0

    os.makedirs(target_dir, exist_ok=True)
    copied = []
    for item in items:
        dst = _copy_one(item, target_dir, args.force)
        if dst:
            copied.append((item['rel'], dst))

    print('\n复制完成。请在启动环境（或 .env）中加入：')
    for rel, dst in copied:
        if rel == 'data/.secret_key':
            print(f'  STULINK_SECRET_KEY_FILE={dst}')
        elif rel == 'data/.encryption_key':
            print(f'  STULINK_ENCRYPTION_KEY_FILE={dst}')
        else:
            print(f'  # .env 已复制到 {dst}，请按需合并')
    print('\n注意：')
    print('  1) 先重启并验证登录、身份证/手机号解密正常；')
    print('  2) 确认无误后再人工删除 data/ 下的旧密钥；')
    print('  3) 同步盘的历史版本仍可能保留旧密钥，需在同步工具中清理。')
    return 0


if __name__ == '__main__':
    sys.exit(main())

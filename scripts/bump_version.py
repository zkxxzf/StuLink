# -*- coding: utf-8 -*-
# StuLink v1.18.0.0 2026-09-23
# 版本号自动 bump 工具（四段式：主.次.三.四）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""StuLink 四段版本号 bump 工具

版本号规范：
    主.次.三.四
    - 第三段（zk）：主线维护者 zkxxzf 每次改动 +1
    - 第四段（sakay）：协助者 sakay 每次改动 +1

用法（在项目根或任意位置执行）：
    python -m scripts.bump_version show              # 打印当前版本
    python -m scripts.bump_version zk                # 第三段 +1，第四段归零（v1.18.0.0 → v1.18.1.0）
    python -m scripts.bump_version sakay             # 第四段 +1（v1.18.1.0 → v1.18.1.1）
    python -m scripts.bump_version minor             # 次版本 +1，三四归零（v1.18.x.x → v1.19.0.0）
    python -m scripts.bump_version major             # 主版本 +1，其他归零
    python -m scripts.bump_version zk --dry          # 只列出将改的文件不写盘
    python -m scripts.bump_version zk --no-commit    # 写盘但不 git commit
    python -m scripts.bump_version set 1.19.0.0     # 直接指定完整版本

默认行为：扫描项目 → 替换所有形如 vX.Y.Z.W / vX.Y.Z 的"当前版本"引用 → 若 git 干净则自动 commit。
不修改：scripts/migrate_*.py、代码内联变更注释、data/、聊天总结/ 等目录。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURRENT_HEAD = ROOT / 'config.py'   # 从 config.py 首行提取当前版本
SKIP_DIRS = {'.git', 'data', 'deploy', 'uploads', '__pycache__',
             '参考表格', '聊天总结', 'node_modules', 'backup',
             '.qoder', '.trae', '.vscode', '.venv', 'venv', 'env'}
ALLOWED_EXT = {'.py', '.html', '.js', '.css', '.md', '.txt', '.yml', '.yaml'}
# 路径过滤：迁移脚本与备份文件不改
EXCLUDE_PATH_RX = re.compile(
    r'(^scripts/migrate_|^scripts/_db_backup|^scripts/bump_version\.py|\.bak-\d{8})'
)


def current_version() -> tuple[int, int, int, int]:
    """从 config.py 首行 `# StuLink vX.Y.Z.W YYYY-MM-DD` 中解析当前版本"""
    if not CURRENT_HEAD.exists():
        raise SystemExit(f'找不到 {CURRENT_HEAD}，无法识别当前版本')
    with CURRENT_HEAD.open(encoding='utf-8') as fh:
        for _ in range(5):
            line = fh.readline()
            m = re.search(r'v(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?', line)
            if m:
                return tuple(int(x or 0) for x in m.groups())
    raise SystemExit('config.py 前 5 行未找到版本号')


def fmt(v: tuple[int, int, int, int]) -> str:
    a, b, c, d = v
    # 第四段为 0 且非全零时仍然写出（本项目规范要求四段）
    return f'{a}.{b}.{c}.{d}'


def next_version(mode: str, cur: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    a, b, c, d = cur
    if mode == 'zk':       return (a, b, c + 1, 0)
    if mode == 'sakay':    return (a, b, c, d + 1)
    if mode == 'minor':    return (a, b + 1, 0, 0)
    if mode == 'major':    return (a + 1, 0, 0, 0)
    raise SystemExit(f'未知模式：{mode}')


def parse_version(s: str) -> tuple[int, int, int, int]:
    m = re.match(r'^v?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?$', s.strip())
    if not m:
        raise SystemExit(f'版本格式非法：{s}（期望 X.Y.Z.W 或 X.Y.Z）')
    return tuple(int(x or 0) for x in m.groups())


def bump_files(new_ver: str, dry: bool = False) -> list[str]:
    """扫描项目并替换当前版本的引用（宽松匹配 vX.Y.Z[.W]）"""
    old = current_version()
    # 精确匹配：只替换与当前版本数字完全一致的字符串
    old_pat = re.compile(
        r'\bv' + r'\.'.join(str(x) for x in old) + r'(?!\.\d)'
    )
    changed: list[str] = []
    for p in ROOT.rglob('*'):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(ROOT)
        except ValueError:
            continue
        if any(d in rel.parts for d in SKIP_DIRS):
            continue
        rel_str = str(rel).replace('\\', '/')
        if EXCLUDE_PATH_RX.search(rel_str):
            continue
        if p.suffix.lower() not in ALLOWED_EXT:
            continue
        try:
            content = p.read_text(encoding='utf-8')
        except Exception:
            continue
        if not old_pat.search(content):
            continue
        new_content = old_pat.sub(f'v{new_ver}', content)
        if new_content != content:
            changed.append(rel_str)
            if not dry:
                try:
                    raw = p.read_bytes()
                    crlf = b'\r\n' in raw
                    enc = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
                    b = new_content.encode(enc)
                    if crlf:
                        b = b.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
                    p.write_bytes(b)
                except Exception as e:
                    print(f'  [WARN] 写入失败 {rel_str}: {e}', file=sys.stderr)
    return changed


def git_commit(new_ver: str, mode: str) -> None:
    r = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True)
    if not r.stdout.strip():
        print('工作区无变更，跳过 commit')
        return
    subprocess.run(['git', 'add', '-A'], cwd=ROOT, check=True)
    msg = f'chore: 版本号升级 v{new_ver}（{mode} 段 +1，脚本 bump_version.py 自动）'
    subprocess.run(['git', 'commit', '-m', msg], cwd=ROOT, check=True)
    print(f'已 commit：{msg}')


def main() -> int:
    ap = argparse.ArgumentParser(description='StuLink 四段版本号 bump 工具')
    ap.add_argument('mode', choices=['show', 'zk', 'sakay', 'minor', 'major', 'set'],
                    help='zk=第三段+1 / sakay=第四段+1 / minor / major / set 指定 / show 打印')
    ap.add_argument('target', nargs='?', help='mode=set 时提供完整版本号，如 1.19.0.0')
    ap.add_argument('--dry', action='store_true', help='只列出将改的文件不写盘')
    ap.add_argument('--no-commit', action='store_true', help='写盘但不 git commit')
    args = ap.parse_args()

    cur = current_version()
    if args.mode == 'show':
        print(f'当前版本：v{fmt(cur)}')
        return 0
    if args.mode == 'set':
        if not args.target:
            raise SystemExit('set 模式需要提供版本号，例如：python -m scripts.bump_version set 1.19.0.0')
        new = parse_version(args.target)
    else:
        new = next_version(args.mode, cur)

    new_str = fmt(new)
    print(f'版本变更：v{fmt(cur)} → v{new_str}（模式：{args.mode}）')
    changed = bump_files(new_str, dry=args.dry)
    tag = 'DRY-RUN' if args.dry else 'APPLIED'
    print(f'{tag} 影响 {len(changed)} 文件')
    for f in changed[:5]:
        print(f'  {f}')
    if len(changed) > 5:
        print(f'  ... 共 {len(changed)} 个')
    if not args.dry and not args.no_commit and changed:
        git_commit(new_str, args.mode)
    print('\n提示：如需推送 `git push origin master`；如需 tag `git tag -a v' + new_str + ' -m "release" && git push origin v' + new_str + '`')
    return 0


if __name__ == '__main__':
    sys.exit(main())

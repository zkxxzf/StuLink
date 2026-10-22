# StuLink v1.19.62.65 2026-10-21
# 合并冲突分析器：判定每个冲突块是否只是「文件头版本号行」冲突。
# 用法：py -3.12 scripts/merge/analyze_conflicts.py [--fix]
#   --fix 时只处理「全部冲突块均为版本头行」的文件，采用 theirs（master）一侧。
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""分析 git 合并冲突块，区分版本头冲突与实质代码冲突。

入参：--fix 可选，是否自动解决纯版本头冲突（取 theirs）。
输出：按文件列出冲突块数量、类型，末尾给出汇总。
"""
import re
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

VER_LINE = re.compile(r'^\s*(#.*|//.*|<!--.*)?\s*StuLink\s+v\d+\.\d+\.\d+\.\d+')
VER_NUM = re.compile(r'\d+\.\d+\.\d+\.\d+')
VER_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
ROOT = Path(__file__).resolve().parents[2]


def conflict_files():
    """返回当前处于冲突(U)状态的文件相对路径列表。"""
    out = subprocess.run(['git', 'diff', '--name-only', '--diff-filter=U'],
                         cwd=ROOT, capture_output=True, text=True,
                         encoding='utf-8', errors='replace')
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def parse_blocks(text):
    """解析冲突块，返回 [(ours_lines, theirs_lines), ...]。"""
    res, ours, theirs, side = [], [], [], None
    for line in text.splitlines(keepends=True):
        if line.startswith('<<<<<<< '):
            ours, theirs, side = [], [], 'ours'
            continue
        if line.startswith('=======') and side:
            side = 'theirs'
            continue
        if line.startswith('>>>>>>> ') and side:
            res.append((ours, theirs))
            side = None
            continue
        if side == 'ours':
            ours.append(line)
        elif side == 'theirs':
            theirs.append(line)
    return res


def normalized(line):
    """把版本号与日期抹平成占位符，用于比较两行结构是否一致。"""
    return VER_DATE.sub('<D>', VER_NUM.sub('<V>', line))


def is_ver_block(ours, theirs):
    """判断冲突块是否为「仅版本号/日期不同」的元信息冲突。

    规则：两侧行数相同；逐行抹平版本号与日期后完全一致；且至少一处原文不同，
    又至少一行含版本号/日期。多行注释头里的版本号差异同样按此处理。
    """
    if not ours or len(ours) != len(theirs):
        return False
    pairs = list(zip(ours, theirs))
    if any(normalized(o) != normalized(t) for o, t in pairs):
        return False
    if all(o == t for o, t in pairs):
        return False
    return any(VER_NUM.search(o) or VER_DATE.search(o) for o, _ in pairs)


def resolve_ver_only(rel, text):
    """把纯版本头冲突按 theirs 解决，返回新文本。"""
    out, side = [], None
    for line in text.splitlines(keepends=True):
        if line.startswith('<<<<<<< '):
            side = 'ours'
            continue
        if line.startswith('=======') and side:
            side = 'theirs'
            continue
        if line.startswith('>>>>>>> ') and side:
            side = None
            continue
        if side == 'ours':
            continue
        out.append(line)
    return ''.join(out)


def show_nonver(rel, text):
    """打印文件中所有非版本头的冲突块全文，带行号便于人工定位。"""
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines, 1):
        if line.startswith('<<<<<<< '):
            start = idx
        elif line.startswith('>>>>>>> ') and start is not None:
            block = lines[start - 1:idx]
            parsed = parse_blocks('\n'.join(block) + '\n')
            if parsed and not is_ver_block(*parsed[0]):
                print(f'--- {rel}:{start}-{idx} ---')
                print('\n'.join(block))
            start = None


def take_theirs(paths):
    """对指定文件按块取 theirs，保留文件内其它已合并内容。"""
    for rel in paths:
        path = ROOT / rel
        text = path.read_text(encoding='utf-8', errors='replace')
        if '<<<<<<< ' not in text:
            print(f'跳过（无冲突标记）: {rel}')
            continue
        path.write_text(resolve_ver_only(rel, text), encoding='utf-8',
                        newline='')
        subprocess.run(['git', 'add', '--', rel], cwd=ROOT)
        print(f'已按 theirs 解决: {rel}')


def main():
    if '--take-theirs' in sys.argv:
        idx = sys.argv.index('--take-theirs')
        take_theirs(sys.argv[idx + 1:])
        return
    do_fix = '--fix' in sys.argv
    do_show = '--show' in sys.argv
    files = conflict_files()
    ver_only, mixed = [], []
    for rel in files:
        path = ROOT / rel
        text = path.read_text(encoding='utf-8', errors='replace')
        blocks = parse_blocks(text)
        if not blocks:
            continue
        if all(is_ver_block(o, t) for o, t in blocks):
            ver_only.append(rel)
            if do_fix:
                path.write_text(resolve_ver_only(rel, text),
                                encoding='utf-8', newline='')
                subprocess.run(['git', 'add', '--', rel], cwd=ROOT)
        else:
            bad = [(o, t) for o, t in blocks if not is_ver_block(o, t)]
            mixed.append((rel, len(blocks), len(bad)))
            if do_show:
                show_nonver(rel, text)
    print(f'冲突文件总数: {len(files)}')
    print(f'纯版本头冲突: {len(ver_only)}' + ('（已自动解决）' if do_fix else ''))
    print(f'含实质冲突: {len(mixed)}')
    for rel, total, bad in mixed:
        print(f'  - {rel}: 块 {total} 个，其中非版本头 {bad} 个')


if __name__ == '__main__':
    main()

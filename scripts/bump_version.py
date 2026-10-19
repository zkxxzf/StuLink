# -*- coding: utf-8 -*-
# StuLink v1.19.47.8 2026-10-12
# 版本号自动 bump 工具（四段式：主.次.zk.sakay）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""StuLink 四段版本号 bump 工具

版本号规范（用户 2026-10-12 定稿，脚本强制校验）：

    主.次.zk.sakay        例：v1.19.47.8
     │  │  │   └── ④ sakay：本分支作者（sakaay）的改动计数 —— **每次改动及时 +1**
     │  │  └────── ③ zk  ：主线维护者 zkxxzf 的改动计数 —— **与我们无关，我们不动**
     │  └───────── ② 次  ：系统版本 · 里程碑 —— 发布里程碑时 +1
     └──────────── ① 主  ：系统版本 · 里程碑 —— 大版本时 +1

四条硬规则（本脚本逐条强制，违反即报错退出）：
    R1 bump 时机由用户决定（2026-10-13 更新）：日常改动**不自动 bump**；
        用户主动发起提交时，协助者才执行 `sakay` 模式 +1。
    R2 不动第三段：第三位由 zk 侧控制，我们 bump 时**必须原样保留**（旧独立性）。
    R3 里程碑不重置：升级前两位（主/次）时，后两位**完全不动** —— 既不归零，也不 +1。
    R4 后两位只增不减：第三、四位永不归零、永不回退，任何情形不得小于历史值。
        （`set` 若确实要回退，须显式 `--force` 并在提交说明里写明原因。）

用法（在项目根或任意位置执行）：
    python -m scripts.bump_version status              # 要不要 bump？（自上次版本号提交以来的改动）
    python -m scripts.bump_version sakay               # ④ +1 ← 本作者日常只用这条
    python -m scripts.bump_version milestone           # ② +1（打里程碑 tag，后两位原样）
    python -m scripts.bump_version major               # ① +1（大版本，后两位原样）
    python -m scripts.bump_version zk                  # ③ +1（zk 侧指令；④ 保留）
    python -m scripts.bump_version set 1.19.47.9       # 指定版本（受 R3/R4 校验）
    python -m scripts.bump_version check               # 校验：引用一致性 + 单调性
    python -m scripts.bump_version sakay --dry         # 只列出将改的文件不写盘
    python -m scripts.bump_version sakay --no-commit   # 写盘但不 git commit

默认行为：扫描项目 → 替换所有形如 vX.Y.Z.W / vX.Y.Z 的"当前版本"引用 → 若 git 干净则自动 commit。
不修改：scripts/migrate/migrate_*.py、代码内联变更注释、data/、聊天总结/（2026-10-13 起位于
docs/聊天总结/；SKIP_DIRS 按**目录名**匹配路径各部分，与层级无关）、
版本历史/（2026-10-16 起位于 docs/版本历史/ —— **历史记录里的版本号必须保持原样**，
改了历史等于伪造历史）等目录。
详细规范见 docs/规范与说明/版本号管理规范.md；版本内容记录见 docs/版本历史/README.md。
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
             '参考表格', '聊天总结', '版本历史', 'node_modules', 'backup',
             '.qoder', '.trae', '.vscode', '.venv', 'venv', 'env'}
ALLOWED_EXT = {'.py', '.html', '.js', '.css', '.md', '.txt', '.yml', '.yaml'}
# 路径过滤：迁移脚本与备份文件不改
EXCLUDE_PATH_RX = re.compile(
    # 2026-10-19：scripts/ 重组后迁移脚本移入 scripts/migrate|fix/ 子目录，
    # 原 `^scripts/migrate_` 不再匹配 → 会被 bump 误改脚本内记录的当时版本号
    r'(^scripts/(?:migrate|fix)/|^scripts/_db_backup|^scripts/bump_version\.py|\.bak-\d{8}'
    r'|\.min\.(js|css)$)'          # 第三方库（jquery/bootstrap 等自带版本号，与本项目无关）
)
# 四条硬规则的生效起点：此之前的版本号迁跃属于「规则定稿前的历史」，只提示不判错
POLICY_BASE: tuple[int, int, int, int] = (1, 19, 47, 8)


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
    """四段版本号推演（2026-10-12 定稿规则）

    语义：`主.次.zk.sakay`
    - 第三段（zk）由主线维护者 zkxxzf 控制，**本作者对任何模式都不得改动**（R2）；
      只有显式 `zk` 模式才 +1（zk 侧自行执行）。
    - 第四段（sakay）= 本作者的改动累计计数，每次改动 +1（R1）。
    - 前两位是系统版本（里程碑）：升级时**后两位完全不动**（R3，不归零也不 +1）。
    - 后两位永不归零、永不回退（R4）。
    """
    a, b, c, d = cur
    if mode == 'zk':        return (a, b, c + 1, d)     # ③ +1（zk 侧），④ 保留
    if mode in ('sakay', 'rev'):
        return (a, b, c, d + 1)                          # ④ +1 ← 本作者日常
    if mode in ('minor', 'milestone'):
        return (a, b + 1, c, d)                          # 里程碑：仅 ② +1，③④ 原样保留
    if mode == 'major':     return (a + 1, b, c, d)      # 主版本：仅 ① +1，②③④ 原样保留
    raise SystemExit(f'未知模式：{mode}')


def verify_monotonic(cur: tuple[int, int, int, int],
                     new: tuple[int, int, int, int],
                     allow_zk_change: bool = False) -> list[str]:
    """版本推进的四条硬规则校验；返回错误清单（空=合规）。

    R2 第三段不动（除非 zk 模式）· R3 里程碑不动后两位 · R4 后两位只增不减
    """
    (a1, b1, c1, d1), (a2, b2, c2, d2) = cur, new
    head_old, head_new = (a1, b1), (a2, b2)      # 前两位：系统版本（里程碑）
    tail_old, tail_new = (c1, d1), (c2, d2)      # 后两位：zk.sakay（永不归零/回退）
    errs: list[str] = []
    if not allow_zk_change and c2 != c1:
        errs.append(f'R2 违规：第三位（zk）不得由本作者改动 '
                    f'（{c1} → {c2}）；该段由 zk 侧控制，请保持原样')
    if head_new < head_old:
        errs.append(f'R4 违规：前两位（系统版本）不可减小（{head_old} → {head_new}）')
    if tail_new < tail_old:
        errs.append(f'R4 违规：后两位不可减小（{fmt(cur)} → {fmt(new)}）—— '
                    f'规范要求永不归零、只增不减')
    if head_new > head_old and tail_new != tail_old:
        errs.append(f'R3 违规：里程碑升级（{head_old[0]}.{head_old[1]} → '
                    f'{head_new[0]}.{head_new[1]}）时后两位必须原样保留，'
                    f'不得归零也不可 +1（{fmt_tail(cur)} → {fmt_tail(new)}）')
    if new == cur:
        errs.append('R1 违规：版本号没有推进（后两位需至少 +1）')
    return errs


def fmt_tail(v: tuple[int, int, int, int]) -> str:
    return f'{v[2]}.{v[3]}'


VERSION_COMMIT_RX = re.compile(r'版本号升级\s+v?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?')


def _git(*args: str) -> str:
    try:
        r = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ''
    except Exception:
        return ''


def last_bump_commit() -> tuple[str, str, tuple[int, int, int, int] | None]:
    """返回最近一次「版本号升级」提交的 (短 sha, 标题版本, 版本元组)；没有则 ('', '', None)"""
    sha = _git('log', '-1', '--format=%h', '--grep=版本号升级')
    if not sha:
        return '', '', None
    subject = _git('log', '-1', '--format=%s', sha)
    date = _git('log', '-1', '--format=%ad', '--date=short', sha)
    m = VERSION_COMMIT_RX.search(subject or '')
    ver = tuple(int(x or 0) for x in m.groups()) if m else None
    return f'{sha} ({date})', (m.group(0) if m else subject), ver


def history_versions(limit: int = 40) -> list[tuple[int, int, int, int]]:
    """从提交历史里提取近 N 次版本号升级记录（由新到旧）"""
    subjects = _git('log', f'-{limit}', '--format=%s', '--grep=版本号升级')
    out: list[tuple[int, int, int, int]] = []
    for line in subjects.splitlines():
        m = VERSION_COMMIT_RX.search(line)
        if m:
            out.append(tuple(int(x or 0) for x in m.groups()))
    return out


def cmd_status() -> None:
    """是否该 bump 了：基于「上次版本号提交之后有没有实质改动」判断"""
    cur = current_version()
    print(f'当前版本：v{fmt(cur)}   （主={cur[0]} 次={cur[1]} zk={cur[2]} sakay={cur[3]}）')
    ref, title, hist_ver = last_bump_commit()
    if not ref:
        print('未找到「版本号升级」提交 —— 若这是首次，请直接 bump')
        return
    print(f'最近一次版本号提交：{ref}  {title}')
    if hist_ver and hist_ver != cur:
        print(f'  [注意] 该提交记录的版本 v{fmt(hist_ver)} 与当前 v{fmt(cur)} 不一致')
    changed = [x for x in _git('diff', '--name-only', ref.split()[0], 'HEAD').splitlines() if x]
    if changed:
        print(f'  之后有 {len(changed)} 个文件变更（前 5 个）：')
        for f in changed[:5]:
            print(f'    {f}')
    dirty = [x for x in _git('status', '--porcelain').splitlines() if x]
    if dirty:
        print(f'  工作区另有 {len(dirty)} 个未提交改动')
    if changed or dirty:
        print('  → 结论：已有实质改动，**第四位应及时 +1**：'
              'python -m scripts.bump_version sakay')
    else:
        print('  → 结论：自上次版本号提交以来无代码改动，无需 bump')


def cmd_check() -> None:
    """一致性 + 单调性校验：引用是否统一、历史是否只增不减、里程碑是否误动后两位"""
    cur = current_version()
    print(f'当前版本：v{fmt(cur)}')
    # 1) 历史单调性（由旧到新）
    hist = history_versions()
    if len(hist) >= 2:
        seq = list(reversed(hist))
        bad = legacy = 0
        for old, new in zip(seq, seq[1:]):
            if new == old:
                legacy += 1
                continue                      # 同一版本重复提交版本号：历史遗留，仅略过
            errs = verify_monotonic(old, new, allow_zk_change=True)
            if not errs:
                continue
            strict = old >= POLICY_BASE       # 规则生效起点之后的跃迁才算违规
            tag = '[错误]' if strict else '[历史]'
            if strict:
                bad += 1
            else:
                legacy += 1
            print(f'  {tag} v{fmt(old)} → v{fmt(new)}：')
            for e in errs:
                print(f'         {e}')
        print(f'1. 历史单调性：检查 {len(seq) - 1} 次升级，'
              f'规则期（≥ v{fmt(POLICY_BASE)}）内 ' + ('全部合规' if not bad else f'{bad} 处违规') +
              (f'；另有 {legacy} 处规则定稿前的历史记录（不算违规）' if legacy else ''))
    else:
        print(f'1. 历史单调性：样本不足（{len(hist)} 条），跳过')
    # 2) 全库引用一致性
    pat = re.compile(r'\bv(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?!\.\d)')
    higher: dict[str, list[str]] = {}
    prev_tail = (cur[0], cur[1], cur[2], cur[3] - 1) if cur[3] > 0 else None
    prev_hits: list[str] = []
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
        if EXCLUDE_PATH_RX.search(rel_str) or p.suffix.lower() not in ALLOWED_EXT:
            continue
        try:
            content = p.read_text(encoding='utf-8')
        except Exception:
            continue
        for m in pat.finditer(content):
            v = tuple(int(x or 0) for x in m.groups())
            if (v[0], v[1]) != (cur[0], cur[1]):
                continue          # 不同产品线（第三方库等）的版本号不参与判断
            if v > cur:
                higher.setdefault(f'v{fmt(v)}', []).append(rel_str)
            elif prev_tail and v == prev_tail:
                prev_hits.append(rel_str)
    ok = True
    if higher:
        ok = False
        for v, files in list(higher.items())[:5]:
            print(f'2. [错误] 存在高于当前版本的引用 {v}（{len(files)} 文件）'
                  f'，例如 {files[0]} —— 通常是有人手改过头')
    else:
        print('2. 引用超前检查：无')
    if prev_hits:
        uniq = sorted(set(prev_hits))
        print(f'3. [提示] 仍有 {len(uniq)} 个文件写着上一版本 v{fmt(prev_tail)}'
              f'（多为历史里程碑记录，需人工确认是否应保留）：')
        for f in uniq[:5]:
            print(f'     {f}')
    else:
        print(f'3. 上一版本残留：无')
    # 3) README 徽章（不带 v 前缀，bump 不处理）
    badge = ROOT / 'README.md'
    if badge.exists():
        txt = badge.read_text(encoding='utf-8')
        m = re.search(r'badge/version-([\d.]+)-', txt)
        if m:
            nums = [int(x) for x in m.group(1).split('.')]
            got = tuple((nums + [0, 0, 0, 0])[:4])
            if got[:4] != cur:
                ok = False
                print(f'4. [错误] README 版本徽章为 {m.group(1)}，与当前 v{fmt(cur)} 不一致'
                      f'（bump 不处理无 v 前缀的写法，请手工同步）')
            else:
                print(f'4. README 版本徽章：{m.group(1)} 一致')
    print('结论：' + ('✅ 版本管理合规' if ok else '❌ 存在问题，请按上面提示处理'))


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
            # v1.18.3.0 防误改：同一文件内当前版本出现 ≥2 次，很可能是“历史里程碑/
            # 致谢表”里的历史记录，全局替换会把它们一并改掉。提醒人工核对。
            hits = len(old_pat.findall(content))
            if hits >= 2:
                print(f'  [警告] {rel_str} 命中 {hits} 处当前版本，可能含历史里程碑记录，'
                      f'请核对是否应保留旧版本号（建议历史表写法不带 v 前缀）')
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
    ap = argparse.ArgumentParser(description='StuLink 四段版本号 bump 工具（主.次.zk.sakay）')
    ap.add_argument('mode',
                    choices=['show', 'status', 'check', 'sakay', 'milestone',
                             'major', 'zk', 'minor', 'set'],
                    help='sakay=第四位+1（本作者日常） / milestone=第二段+1（里程碑，后两位不动） / '
                         'major=第一段+1 / zk=第三段+1（zk 侧） / set 指定 / '
                         'status 看是否该 bump / check 校验一致性')
    ap.add_argument('target', nargs='?', help='mode=set 时提供完整版本号，如 1.19.47.9')
    ap.add_argument('--dry', action='store_true', help='只列出将改的文件不写盘')
    ap.add_argument('--no-commit', action='store_true', help='写盘但不 git commit')
    ap.add_argument('--force', action='store_true',
                    help='仅 set 模式：确认要回退/跨越审批，绕过 R2/R3/R4 校验')
    args = ap.parse_args()

    cur = current_version()
    if args.mode == 'show':
        print(f'当前版本：v{fmt(cur)}')
        print(f'  第①位 主={cur[0]}  第②位 次（里程碑）={cur[1]}  '
              f'第③位 zk={cur[2]}（不由本作者改动）  第④位 sakay={cur[3]}（改动即 +1）')
        return 0
    if args.mode == 'status':
        cmd_status()
        return 0
    if args.mode == 'check':
        cmd_check()
        return 0
    if args.mode == 'set':
        if not args.target:
            raise SystemExit('set 模式需要提供版本号，例如：python -m scripts.bump_version set 1.19.47.9')
        new = parse_version(args.target)
        # R2：第三位归 zk 侧，本作者**任何模式（含 set）都不得改动**，除非显式 --force
        allow_zk = False
    else:
        new = next_version(args.mode, cur)
        allow_zk = (args.mode == 'zk')

    new_str = fmt(new)
    errs = verify_monotonic(cur, new, allow_zk_change=allow_zk)
    if errs:
        print('版本推进校验未通过，已中止：')
        for e in errs:
            print(f'  ✗ {e}')
        if not args.force:
            print('\n规范回顾：前两位=系统版本（里程碑，升级时后两位不动）；'
                  '第三位 zk 由 zk 侧控制我们不改；第四位 sakay 每次改动 +1；'
                  '后两位永不归零、永不回退。确实要破例请加 --force。')
            return 2
        print('  （--force 已指定，忽略上述校验并继续）')

    print(f'版本变更：v{fmt(cur)} → v{new_str}（模式：{args.mode}）')
    if args.mode in ('minor', 'milestone', 'major'):
        seg = {'minor': '二', 'milestone': '二', 'major': '一'}[args.mode]
        print(f'  [里程碑] 第 {seg} 段已升级，后两位 {fmt_tail(new)} 原样保留 ✅；'
              f'建议打里程碑 tag：git tag -a v{new_str} -m "milestone: v{new_str}"')
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

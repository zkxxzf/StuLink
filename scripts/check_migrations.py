# -*- coding: utf-8 -*-
"""P1 M-23：迁移清单 / 执行器 / 登记表自动对账。

检查三类差异：
1) 清单有、执行器无 → 上线必漏跑（阻断，退出 1）；
2) 执行器有、清单无 → 文档缺失（阻断，退出 1）；
3) 已登记但脚本已不存在 / 未登记 → 提示（--strict 时阻断）。

用法：
    py -3.12 scripts/check_migrations.py
    py -3.12 scripts/check_migrations.py --strict
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, 'docs', '规范与说明', '数据库迁移清单.md')
RUNNER = os.path.join(ROOT, 'deploy', 'nas_migrate_pending.sh')

# 迁移脚本命名：migrate_/fix_/backfill_/apply_/import_bands_ 等，统一在 scripts/ 下
_SCRIPT_RE = re.compile(
    r'scripts/(?:migrate|fix|backfill|apply|seed)/[A-Za-z0-9_./-]+\.py')
_RUN_RE = re.compile(r'run_mig\s+(scripts/[A-Za-z0-9_./-]+\.py)')


def _norm(path):
    return os.path.normpath(path).replace('\\', '/').lstrip('./')


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except OSError as exc:
        print(f'[ERROR] 读取失败：{path}（{exc}）')
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strict', action='store_true',
                    help='把“未登记/待执行”也视为失败')
    args = ap.parse_args()

    doc_text = _read(DOC)
    run_text = _read(RUNNER)
    if not doc_text or not run_text:
        return 1

    doc = {_norm(m) for m in _SCRIPT_RE.findall(doc_text)}
    runner = {_norm(m) for m in _RUN_RE.findall(run_text)}

    # 只保留真实存在的脚本，避免文档里的示例路径干扰
    doc = {p for p in doc if os.path.exists(os.path.join(ROOT, p))}
    runner = {p for p in runner if os.path.exists(os.path.join(ROOT, p))}

    only_doc = sorted(doc - runner)
    only_run = sorted(runner - doc)

    print('== 迁移对账 ==')
    print(f'  清单脚本：{len(doc)} 个；执行器脚本：{len(runner)} 个')
    if only_doc:
        print('  [缺失执行器] 清单有、执行器无：')
        for p in only_doc:
            print(f'    - {p}')
    if only_run:
        print('  [缺失清单] 执行器有、清单无：')
        for p in only_run:
            print(f'    - {p}')

    # 登记表状态（只提示，不默认阻断）
    pending, missing_script = [], []
    try:
        sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        import _mig_log
        applied = {r[0] for r in _mig_log.all_applied()}
        for p in sorted(doc | runner):
            if os.path.basename(p) not in applied:
                pending.append(p)
        for name in sorted(applied):
            if not any(os.path.basename(p) == name for p in (doc | runner)):
                missing_script.append(name)
    except Exception as exc:  # noqa: BLE001
        print(f'  [WARN] 读取迁移登记表失败：{exc}')
        applied = set()

    if pending:
        print(f'  [待执行] {len(pending)} 个（上线前按清单执行，不阻断本次对账）')
        for p in pending[:10]:
            print(f'    - {p}')
        if len(pending) > 10:
            print(f'    ... 另有 {len(pending) - 10} 个')
    if missing_script:
        print(f'  [登记表有、清单/执行器无] {len(missing_script)} 个：')
        for n in missing_script[:10]:
            print(f'    - {n}')

    failed = bool(only_doc or only_run)
    if args.strict and (pending or missing_script):
        failed = True
    if failed:
        print('\n结论：存在清单/执行器差异，请先补齐。')
        return 1
    print('\n结论：清单与执行器一致。')
    return 0


if __name__ == '__main__':
    sys.exit(main())

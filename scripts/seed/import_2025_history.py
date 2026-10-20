# -*- coding: utf-8 -*-
"""把 2025 级历次考试（桌面 7 个 Excel）导入 StuLink。

用法：
    py -3.12 scripts/seed/import_2025_history.py --list
    py -3.12 scripts/seed/import_2025_history.py --only 2025级202510月考（高一上）
    py -3.12 scripts/seed/import_2025_history.py --all --apply
    py -3.12 scripts/seed/import_2025_history.py --all --bands-only --apply

默认 dry-run（只解析、打印将写入什么）；`--apply` 才写库。
成绩按「有赋分进主分、原始分进 raw_score」口径；划线只写
特控线（seq=1）与本科线（seq=2），不收清北/985/211/专科。
"""
import argparse
import os
import sys

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:      # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import create_app                                       # noqa: E402
from g2025 import runner, specs                                   # noqa: E402


def _print_scores(report):
    """打印一场成绩导入报告。"""
    head = '[新建]' if report['created'] else '[已有]'
    print('\n%s %s (id=%s)' % (head, report['exam'], report['exam_id']))
    print('   解析行数=%s 含原始分=%s' % (report['rows'], report['with_raw']))
    if report['unmatched']:
        print('   ⚠ 未匹配主库 %s 人：' % len(report['unmatched']))
        for item in report['unmatched'][:10]:
            print('      -', item)
    errs = report['errors']
    if errs:
        print('   ⚠ 解析告警 %s 条，前 5 条：' % len(errs))
        for item in errs[:5]:
            print('      -', item)
    if report['applied']:
        print('   已写入：', report['summary'])
    else:
        print('   [dry-run] 未写库')


def _print_bands(report):
    """打印一场划线导入报告。"""
    head = '[新建]' if report['created'] else '[已有]'
    print('\n%s %s (id=%s)' % (head, report['exam'], report['exam_id']))
    for direction, subjects in report['lines'].items():
        label = direction or '全科'
        total = subjects.get('总分') or {}
        subs = len(subjects)
        print('   %s：特控 %s / 本科 %s（%s 个科目线）' % (
            label, total.get('特控线', '-'), total.get('本科线', '-'), subs))
    if not report['lines']:
        print('   ⚠ 未读到任何特控/本科线')
    print('   [已写入 %s 条]' % report['count'] if report['applied']
          else '   [dry-run] 未写库')


def _pick(args):
    """按参数挑出要处理的场次。"""
    if args.all:
        return list(specs.SPECS)
    picked = []
    for name in args.only or []:
        spec = specs.spec_by_name(name)
        if spec is None:
            print('未知考试名：', name)
            continue
        picked.append(spec)
    return picked


def main():
    """入口。"""
    ap = argparse.ArgumentParser(description='2025 级历次考试导入')
    ap.add_argument('--list', action='store_true', help='列出七场考试')
    ap.add_argument('--only', action='append', help='只处理指定考试名（可重复）')
    ap.add_argument('--all', action='store_true', help='处理全部七场')
    ap.add_argument('--scores-only', action='store_true', help='只导成绩')
    ap.add_argument('--bands-only', action='store_true', help='只导划线')
    ap.add_argument('--mode', default='B', choices=['A', 'B'],
                    help='A=增量覆盖（保留库内多出的学生）/ B=清空本场重导')
    ap.add_argument('--apply', action='store_true', help='真正写库')
    args = ap.parse_args()

    if args.list:
        for spec in specs.SPECS:
            print('%-34s %s %s %s' % (spec['name'], spec['date'],
                                      spec['type'], spec['file']))
        return 0

    picked = _pick(args)
    if not picked:
        print('未指定场次：用 --all 或 --only <考试名>（--list 可查看）')
        return 1

    app = create_app()
    with app.app_context():
        index, hints = runner.build_index() if not args.bands_only else ({}, {})
        for spec in picked:
            if not args.bands_only:
                _print_scores(runner.run_scores(spec, args.apply, index,
                                                hints, args.mode))
            if not args.scores_only:
                _print_bands(runner.run_bands(spec, args.apply))
    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""课表批量导入工具（命令行版，2026-10-10）。

与网页「课表导入（整班 · 原样）」走**同一套解析与写入链路**
（`schedule_matrix_import.parse_school_workbook` + `schedule_service.batch_add_entries`），
适合把学校教务发来的总课表一次性灌进来，或做批量回归验证。

支持两种版式（自动识别，无需指定）：
  ① 全校总课表：行 = 星期 × 节次，列 = 班级（每列一个班）
  ② 整班矩阵：一个 sheet 一个班，行 = 节次，列 = 周一~周五

用法：
    # 先干跑（默认），只报告不写库
    python scripts/import_school_timetable.py --file "D:/课表.xlsx"

    # 确认无误后写库（导入到新建的学期）
    python scripts/import_school_timetable.py --file "D:/课表.xlsx" --commit

    # 指定已有学期
    python scripts/import_school_timetable.py --file "D:/课表.xlsx" --term-id 5 --commit

    # 整批指定周次（只导某一周：第 5 周）
    python scripts/import_school_timetable.py --file "D:/课表.xlsx" --week 5 --commit

    # 导入前先清空该学期的课表条目（慎用：会连带写版本快照）
    python scripts/import_school_timetable.py --file "D:/课表.xlsx" --term-id 5 --replace --commit

约定：
  - 冲突（同班同时段、同教师同时段）由写入层自动跳过并报告，不会中断整批；
  - 教师姓名匹配不上教师名单时，按姓名原样入库（只提示，不报错）；
  - 学期节次名称与表格里的节次对不上时该行会被跳过，报告里会点名。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models import ClassProfile, User                     # noqa: E402
from app.models.academic import Teacher                        # noqa: E402
from app.models.timetable import (PeriodDef, ScheduleEntry,    # noqa: E402
                                  TermSchedule)
from app.modules.academic.services import schedule_matrix_import as mi  # noqa: E402
from app.modules.academic.services import schedule_service as svc       # noqa: E402


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='课表批量导入（全校总课表 / 整班矩阵）')
    p.add_argument('--file', help='课表 Excel 路径（.xlsx / .xlsm）')
    p.add_argument('--term-id', type=int, help='导入到已有学期 id（缺省新建学期）')
    p.add_argument('--term-name', default='2026-2027学年第一学期（课表导入）',
                   help='新建学期的名称')
    p.add_argument('--school-year', default='2026-2027', help='学年，如 2026-2027')
    p.add_argument('--term', default='第一学期', help='学期，如 第一学期')
    p.add_argument('--week', default='', help='整批周次，如 5 / 1-9 / 单周（留空按 1-18）')
    p.add_argument('--operator', default='', help='操作人用户名（写版本快照用）')
    p.add_argument('--replace', action='store_true', help='导入前清空该学期已有课表条目')
    p.add_argument('--activate', action='store_true',
                   help='导入后把学期设为启用中 + 当前学期（缺省只建草稿）')
    p.add_argument('--commit', action='store_true', help='真正写库（缺省只干跑报告）')
    p.add_argument('--dump-template', metavar='路径.xlsx',
                   help='只导出空白导入模板（全校总课表版式）后退出，不读 --file')
    return p.parse_args(argv)


def _periods_like(schedule_id):
    """该学期的节次（dict 列表，与页面解析用的结构一致）"""
    return [p.to_dict() for p in PeriodDef.query.filter_by(
        term_schedule_id=schedule_id).order_by(PeriodDef.period_number).all()]


def _period_obj_of(dicts):
    class _P:
        def __init__(self, d):
            self.period_name = d['period_name']
            self.period_number = d['period_number']
            self.period_type = d.get('period_type')
            self.start_time = d.get('start_time')
            self.end_time = d.get('end_time')
    return [_P(d) for d in dicts]


def main(argv=None):
    args = parse_args(argv)

    # ── 只导出空白模板 ──────────────────────────────────────────────────
    if args.dump_template:
        app = create_app()
        with app.app_context():
            class _P:
                def __init__(self, d):
                    self.period_name = d['period_name']
                    self.period_number = d['period_number']
                    self.period_type = d.get('period_type')
            from app.models.timetable import get_default_periods
            periods = ([] if args.term_id else
                       [_P(d) for d in get_default_periods()])
            if args.term_id:
                periods = [_P(d) for d in _periods_like(args.term_id)]
            buf = mi.build_full_school_template(periods, sample=False)
            with open(args.dump_template, 'wb') as f:
                f.write(buf.getvalue())
        print(f'[模板] 已导出空白模板：{args.dump_template}')
        return 0

    if not args.file:
        print('[FAIL] 缺少 --file（或使用 --dump-template 只导出模板）')
        return 2
    if not os.path.isfile(args.file):
        print(f'[FAIL] 文件不存在：{args.file}')
        return 2

    app = create_app()
    with app.app_context():
        user = None
        if args.operator:
            user = User.query.filter_by(username=args.operator).first()
        if user is None:
            user = (User.query.filter_by(username='admin').first()
                    or User.query.filter(User.role == 'admin').first())

        # ── 目标学期 ────────────────────────────────────────────────────
        created = False
        ts = db.session.get(TermSchedule, args.term_id) if args.term_id else None
        if args.term_id and not ts:
            print(f'[FAIL] 学期 id={args.term_id} 不存在')
            return 2
        if ts is None:
            ts = svc.create_schedule(args.term_name, args.school_year, args.term,
                                     description='由 scripts/import_school_timetable.py 导入',
                                     created_by=getattr(user, 'id', None))
            created = True
        print(f'[学期] id={ts.id} {ts.name} status={ts.status}'
              f'{"（新建）" if created else ""}')

        periods = _periods_like(ts.id)
        print(f'[节次] {len(periods)} 节：' +
              '、'.join(p['period_name'] for p in periods))

        grades = sorted({g for (g,) in db.session.query(ClassProfile.grade).distinct() if g})
        teachers = Teacher.query.filter_by(status='active').all()
        print(f'[上下文] 学期年级 {grades or "（班级档案为空）"}；'
              f'在职教师 {len(teachers)} 人')

        # ── 解析 ────────────────────────────────────────────────────────
        with open(args.file, 'rb') as f:
            data = mi.parse_school_workbook(
                f, _period_obj_of(periods), grades, teachers,
                default_week_range=(args.week or '').strip() or None)

        sheets = data['sheets']
        entries = [e for sh in sheets for e in sh['entries']]
        print(f'\n[解析] 班级 {len(sheets)} 个 · 课表条目 {len(entries)} 条')
        for e in data['errors']:
            print(f'  ! 跳过 {e["sheet"] or "（工作簿）"}：{e["message"]}')

        warn_kind = Counter()
        teachers_missing = Counter()
        multi_cell = 0
        for sh in sheets:
            for w in sh['warnings']:
                if '不在教师名单中' in w:
                    warn_kind['教师未匹配'] += 1
                    teachers_missing[w.split('教师「')[1].split('」')[0]] += 1
                elif '一格内写了' in w:
                    warn_kind['一格多科'] += 1
                    multi_cell += 1
                elif '节次未识别' in w:
                    warn_kind['节次未识别'] += 1
                else:
                    warn_kind['其它'] += 1
        print('[校验] 警告统计：' + ('、'.join(f'{k} {v} 处' for k, v in warn_kind.items())
                                   or '无'))
        if multi_cell:
            print(f'       ↑ 有 {multi_cell} 处「一格写了多门课」，'
                  f'若不是单双周，导入时同班同时段只保留第一条')
        if teachers_missing:
            top = '、'.join(f'{n}({c})' for n, c in teachers_missing.most_common(8))
            print(f'       未匹配教师 {len(teachers_missing)} 人（前 8：{top}）'
                  f'—— 已按姓名原样记入课表')
        unknown = sorted({s for sh in sheets for s in sh['unknown_subjects']})
        if unknown:
            print(f'       未识别学科：{"、".join(unknown)}（已按原文入库）')

        print('\n[明细] 班级 → 条目数')
        for sh in sheets:
            print(f'  {sh["grade"]}{sh["class_name"]:<6} {len(sh["entries"]):>4} 条'
                  f'{"  (layout=%s)" % sh.get("layout") if sh.get("layout") else ""}')

        if not args.commit:
            print('\n[干跑] 未写库。确认无误后加 --commit 执行导入。')
            if created:                       # 干跑也别留下空学期
                svc.delete_schedule(ts.id)
                print(f'[干跑] 已回收本次试建的学期 id={ts.id}')
            return 0

        # ── 写库 ────────────────────────────────────────────────────────
        if args.replace:
            olds = ScheduleEntry.query.filter_by(term_schedule_id=ts.id,
                                                 is_deleted=False).all()
            for o in olds:
                o.is_deleted = True
            db.session.commit()
            print(f'[清空] 该学期已有 {len(olds)} 条课表已软删除')

        result = svc.batch_add_entries(ts.id, entries, operator=user)
        print(f'\n[写入] 成功 {result["success"]} 条，跳过 {result["failed"]} 条')
        for err in result['errors'][:20]:
            print(f'  ! 第 {err["row"]} 条：{err["message"]}')
        if len(result['errors']) > 20:
            print(f'  … 其余 {len(result["errors"]) - 20} 条省略')

        if args.activate:
            if ts.status != 'active':
                svc.activate_schedule(ts.id)
            try:
                svc.set_current_term(ts.id)
            except ValueError as e:
                print(f'  ! 设为当前学期失败：{e}')
            print(f'[学期] 已设为启用中 + 当前学期（id={ts.id}）')

        total = ScheduleEntry.query.filter_by(term_schedule_id=ts.id,
                                              is_deleted=False).count()
        print(f'[结果] 学期 id={ts.id} 现有课表条目：{total} 条')
        return 0


if __name__ == '__main__':
    sys.exit(main())

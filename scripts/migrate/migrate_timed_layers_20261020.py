# -*- coding: utf-8 -*-
"""把限时练内置分层模板改成与现有模板一致的 12 层（累积门槛口径）。

## 背景
`band_templates` 里已有的「限时练名次分层」是 12 层
（前10/前20/前50/前80/前100/前150/前200/前250/前300/前400/前500/前550），
而限时练自己的 `timed_band_templates` 初始只有 5 层。用户口径：
**限时练的分层层数要和那套模板一样多。**

## 做什么
- 只更新 **内置模板**（`is_builtin=1`）的 `layers_json` 为
  `models.timed.DEFAULT_TIMED_LAYERS`（12 层，from_rank=1、to_rank=N）；
- 自定义模板（`is_builtin=0`）一律不动；
- 幂等：已一致则跳过。

## 用法
    py -3.12 scripts/migrate/migrate_timed_layers_20261020.py           # 预览
    py -3.12 scripts/migrate/migrate_timed_layers_20261020.py --apply   # 执行
"""
import json
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

SCRIPT_NAME = _os.path.basename(__file__)
APPLY = '--apply' in _sys.argv
GRD_DB = _os.path.join(_ROOT, 'data', 'grades.db')


def main():
    if not _os.path.exists(GRD_DB):
        print(f'[ERROR] 找不到 {GRD_DB}')
        return 1

    from app import create_app
    from app.extensions import db
    from app.models.timed import (BUILTIN_TIMED_TEMPLATE, DEFAULT_TIMED_LAYERS,
                                  TimedBandTemplate)

    want = json.dumps([dict(x) for x in DEFAULT_TIMED_LAYERS],
                      ensure_ascii=False)
    app = create_app()
    with app.app_context():
        tpl = TimedBandTemplate.query.filter_by(
            name=BUILTIN_TIMED_TEMPLATE).first()
        if tpl is None:
            print('[SKIP] 内置模板不存在（先跑 migrate_timed_exam_20261020）')
            return 0
        old = tpl.layers_json or '[]'
        print(f'内置模板 #{tpl.id} {tpl.name}')
        print(f'  当前层数：{len(json.loads(old))} → 目标：{len(DEFAULT_TIMED_LAYERS)}')
        if old == want:
            print('  [SKIP] 已是 12 层，无需变更')
            return 0
        if not APPLY:
            print('[DRY-RUN] 未改库；确认后加 --apply')
            return 0

        from _db_backup import backup_db
        backup_db(GRD_DB, tag='before_timed_layers_12')
        tpl.layers_json = want
        db.session.commit()
        print('  [OK] 已更新为 12 层')
        try:
            import _mig_log
            _mig_log.record(SCRIPT_NAME, note='限时练内置分层模板改为 12 层')
        except Exception as exc:                        # noqa: BLE001
            print(f'[WARN] 迁移登记失败：{exc}')
    return 0


if __name__ == '__main__':
    _sys.exit(main())

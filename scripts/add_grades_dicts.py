# StuLink v1.9.0 2026-09-03
# 成绩模块上线补种脚本（幂等，可重复执行）：
#   1. 补种字典 exam_type / exam_subject
#   2. 将 5 个默认权限组的 menu_keys 同步为新定义（含 grades.* 扩展）
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from app import create_app, DICT_DATA, PERMISSION_GROUPS  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import DictCategory, DictItem, PermissionGroup  # noqa: E402

EXAM_CATEGORIES = ('exam_type', 'exam_subject')


def seed_dicts():
    added = []
    for code, (name, values) in DICT_DATA.items():
        if code not in EXAM_CATEGORIES:
            continue
        cat = DictCategory.query.filter_by(code=code).first()
        if not cat:
            cat = DictCategory(code=code, name=name)
            db.session.add(cat)
            db.session.flush()
        existing = {i.value for i in cat.items.all()}
        order = max([i.sort_order for i in cat.items.all()] or [-1]) + 1
        cnt = 0
        for val in values:
            if val not in existing:
                db.session.add(DictItem(category_id=cat.id, value=val, sort_order=order))
                order += 1
                cnt += 1
        added.append(f'{code}: +{cnt}（当前 {len(existing) + cnt} 项）')
    db.session.commit()
    return added


def sync_default_groups():
    changed = []
    for p in PERMISSION_GROUPS:
        g = PermissionGroup.query.filter_by(name=p['name']).first()
        if not g:
            print(f'[跳过] 组不存在（默认组已删除或改名？）：{p["name"]}')
            continue
        old = set(g.get_menu_keys())
        new = set(p['menu_keys'])
        if old != new:
            g.set_menu_keys(p['menu_keys'])
            db.session.commit()
            changed.append(f'{p["name"]}: -{sorted(old - new)} +{sorted(new - old)}')
        else:
            print(f'[一致] {p["name"]}')
    return changed


def main():
    app = create_app()
    with app.app_context():
        print('== 1. 字典补种 ==')
        for line in seed_dicts():
            print(' ', line)
        print('== 2. 默认权限组 menu_keys 同步 ==')
        changed = sync_default_groups()
        for line in changed:
            print(' ', line)
        if not changed:
            print('  无需变更')
        print('完成。用户自建权限组未自动修改，请管理员在「权限组管理」中自查成绩模块权限。')


if __name__ == '__main__':
    main()

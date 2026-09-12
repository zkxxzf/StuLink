# -*- coding: utf-8 -*-
"""给考试灌入一套完整的分层（划线）数据：走批量划线接口，一次写入全部「方向 × 学科」组合

默认方案：全科统一 + 人数比例 + 含总分，四层 优秀20% / 良好60% / 及格95% / 待提升0。
比例模式由系统按「该方向该科」自己的排名换算成分数，各科各方向互不干扰。

用法（在 StuLink/ 目录下执行）：
    python scripts/apply_demo_bands.py                # 默认灌入最新一场考试
    python scripts/apply_demo_bands.py 1              # 指定 exam_id
    python scripts/apply_demo_bands.py 1 --mode score # 固定分数模式（用 PRESET 里的分数值）

说明：
    - 幂等：按 (考试, 方向, 学科) 覆盖写入，重复执行不会产生重复行。
    - 需要考试已导入成绩（有参考学生），否则组合会因无参考人群被跳过。
"""
import argparse
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import create_app                                   # noqa: E402
from app.extensions import db                                # noqa: E402
from app.models.grades import Exam, ExamBand, TOTAL_SUBJECT   # noqa: E402

app = create_app()

# 比例模板：[(层名, 百分比)]  最低层一般设 0
RATIO_PRESET = [('优秀', 20), ('良好', 60), ('及格', 95), ('待提升', 0)]
# 固定分数模板：[(层名, 分数)]  语数外 150 满分、其余 100 满分时用同一套值并不合适，
# 故仅在 --mode score 时使用（按 150 分制给主科、100 分制给选考需手工逐科填，页面操作更直观）
SCORE_PRESET = [('优秀', 120), ('良好', 100), ('及格', 80), ('待提升', 0)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exam_id', nargs='?', type=int, default=None)
    ap.add_argument('--mode', choices=['ratio', 'score'], default='ratio')
    args = ap.parse_args()

    with app.app_context():
        exam = (Exam.query.get(args.exam_id) if args.exam_id
                else Exam.query.order_by(Exam.id.desc()).first())
        if not exam:
            print('未找到考试')
            return 1
        eid, ename = exam.id, exam.name

    preset = RATIO_PRESET if args.mode == 'ratio' else SCORE_PRESET
    layers = [{'seq': i + 1, 'name': n, 'value': v}
              for i, (n, v) in enumerate(preset)]

    with app.test_client() as c:
        r0 = c.get('/login')
        csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"',
                         r0.get_data(as_text=True)).group(1)
        c.post('/login', data={'csrf_token': csrf, 'username': 'admin',
                               'password': 'admin123'}, follow_redirects=True)
        r = c.post('/grades/api/bands/batch',
                   json={'exam_id': eid, 'mode': args.mode, 'scope': 'all',
                         'include_total': True, 'layers': layers},
                   headers={'X-CSRFToken': csrf})
        res = r.get_json()
        if not res.get('success'):
            print('失败：', res.get('message'))
            return 1
        print(f'考试：id={eid} 「{ename}」')
        print(f'方案：全科统一 + {"人数比例" if args.mode == "ratio" else "固定分数"} + 含总分')
        print(res['message'])
        if res.get('skipped'):
            print('  跳过：', '、'.join(res['skipped']))

    with app.app_context():
        rows = (ExamBand.query.filter_by(exam_id=eid)
                .order_by(ExamBand.direction.desc(), ExamBand.subject, ExamBand.seq).all())
        print('\n分层结果：')
        cur = None
        for b in rows:
            key = (b.direction, b.subject)
            if key != cur:
                cur = key
                print(f'  {b.direction or "全体"}·{b.subject}'.ljust(14), end='')
            print(f'{b.name}={b.lower_value:g}'.ljust(12), end='')
            if b is not rows[-1] and (rows[rows.index(b) + 1].direction,
                                      rows[rows.index(b) + 1].subject) != key:
                print()
        print()
        combo = db.session.query(ExamBand.direction, ExamBand.subject) \
            .filter_by(exam_id=eid).distinct().count()
        print(f'合计：{combo} 个组合 / {len(rows)} 行')
    print(f'浏览器查看：http://localhost:5000/grades/exams/{eid}/bands')
    return 0


if __name__ == '__main__':
    sys.exit(main())

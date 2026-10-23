# StuLink v1.19.70.64 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""小程序 /api/wx 接口冒烟（只读）。

用法：
    py -3.12 scripts/smoke_wx_api.py [--user-id 1]

做的事：用真实库里的账号签发一个令牌，把小程序会调用的接口逐条打一遍，
断言 HTTP 200 + success=true，并对关键结构做形状校验（成绩图表 / 课表 / 宿舍分页）。
不写库（通知详情会标记已读，故只测列表与未读数）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                       # noqa: E402
from app.models import ExamScore                 # noqa: E402
from app.modules.wxapi.core.tokens import issue_token  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=''):
    """记录一条断言结果。"""
    if cond:
        PASSED.append(name)
        print('  PASS  %s' % name)
    else:
        FAILED.append('%s %s' % (name, detail))
        print('  FAIL  %s  %s' % (name, detail))


def call(client, headers, path, expect=200):
    """GET 一个接口，返回 (body, status)。"""
    res = client.get('/api/wx' + path, headers=headers)
    try:
        body = res.get_json() or {}
    except Exception:  # noqa: BLE001
        body = {}
    return body, res.status_code, path


def expect_ok(client, headers, path, name=''):
    """断言接口 200 且 success=true，返回 data。"""
    body, status, _p = call(client, headers, path)
    label = name or path
    check(label + ' 状态 200', status == 200, 'got %s' % status)
    check(label + ' success', body.get('success') is True,
          str(body.get('message'))[:60])
    return body.get('data')


def maybe_ok(client, headers, path):
    """数据依赖型接口：成功返回 data，未划线等业务提示打印 SKIP（不算失败）。"""
    body, status, _p = call(client, headers, path)
    if status == 200 and body.get('success') is True:
        return body.get('data')
    print('  SKIP  %s（%s）' % (path, str(body.get('message'))[:60]))
    return None


def main():
    """入口：签发令牌 → 逐条打接口 → 输出汇总。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--user-id', type=int, default=1,
                        help='用哪个账号的令牌（默认 1=管理员）')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        token, _ttl = issue_token(args.user_id)
        # 取一个有总分行（即有真实成绩）的学生，避免抽到无数据的空档案
        sample = (ExamScore.query
                  .filter(ExamScore.subject == '总分',
                          ExamScore.score.isnot(None))
                  .order_by(ExamScore.student_no).first())
        sample_no = sample.student_no if sample else ''
    headers = {'X-Wx-Token': token}
    client = app.test_client()

    print('[认证]')
    boot = expect_ok(client, headers, '/bootstrap')
    check('bootstrap 含 modules', isinstance((boot or {}).get('modules'), list))
    check('bootstrap 含 caps', isinstance((boot or {}).get('caps'), dict))
    caps = (boot or {}).get('caps') or {}
    check('bootstrap caps 含应用开关',
          all(k in caps for k in
              ('points', 'morality', 'portrait', 'attendance',
               'dorm', 'admin')))
    expect_ok(client, headers, '/profile')

    print('[首页工作台]')
    wb = expect_ok(client, headers, '/workbench')
    check('workbench 含 courses/invigilations',
          isinstance((wb or {}).get('courses'), list)
          and isinstance((wb or {}).get('invigilations'), list))
    check('workbench 含 todos',
          isinstance((wb or {}).get('todos'), list)
          and 'todo_count' in (wb or {}))

    print('[成绩]')
    exams = expect_ok(client, headers, '/grades/exams') or []
    check('考试列表非空', len(exams) > 0, 'len=%d' % len(exams))
    board = maybe_ok(client, headers, '/grades/manage/entries')
    if board:
        check('成绩管理入口板含 groups',
              isinstance(board.get('groups'), list))
        check('入口板带可见考试',
              isinstance(board.get('exams'), list))
    exam_rows = maybe_ok(client, headers, '/grades/manage/exams')
    if exam_rows:
        check('历次考试含 items',
              isinstance(exam_rows.get('items'), list))
        items = exam_rows.get('items') or []
        if items:
            check('历次考试含四步进度',
                  isinstance(items[0].get('steps'), list)
                  and len(items[0]['steps']) == 4)
            check('历次考试含参考人数', 'count' in items[0])
    classes = expect_ok(client, headers, '/grades/classes') or []
    check('班级列表含 grade/classes',
          bool(classes) and 'classes' in classes[0])
    exam_id = exams[0]['id'] if exams else 0
    if sample_no:
        stu = expect_ok(client, headers,
                        '/grades/student/%s' % sample_no)
        check('个人成绩含 history', isinstance((stu or {}).get('history'), list))
        check('个人成绩含 charts', isinstance((stu or {}).get('charts'), dict))
        check('个人成绩含 subject_rank',
              isinstance(((stu or {}).get('tables') or {}).get('subject_rank'),
                         list))
        latest = (stu or {}).get('latest') or {}
        check('个人成绩 latest 含 total', isinstance(latest.get('total'), dict))
        check('历次成绩含 exam_id',
              any(h.get('exam_id') for h in (stu or {}).get('history', [])))
        if latest.get('subjects'):
            row = latest['subjects'][0]
            check('科目行含 rank_class 字段', 'rank_class' in row)
        stu_sub = expect_ok(client, headers,
                            '/grades/student/%s?subject=语文' % sample_no)
        check('单科趋势图已返回',
              'trend_subject' in ((stu_sub or {}).get('charts') or {}))
    if exam_id:
        cs = expect_ok(client, headers,
                       '/grades/class-summary?exam_id=%d' % exam_id) or {}
        check('班级汇总含 rows', isinstance(cs.get('rows'), list))
        cls = [r.get('class_name') for r in (cs.get('rows') or [])
               if r.get('class_name')]
        a_tab = expect_ok(client, headers,
                          '/grades/analysis/grade?exam_id=%d' % exam_id) or {}
        check('分析年级 tab 含表格',
              bool((a_tab.get('tables') or {})))
        # 2026-10-19：年级分析页的图表区已按用户口径下线（tab_service 恒返回
        # charts={}），旧断言"含图表"必然失败；与 smoke_grades 的断言对齐。
        check('分析年级 tab 图表区已下线（charts 为空）',
              (a_tab.get('charts') or {}) == {})
        if cls:
            c_tab = expect_ok(
                client, headers,
                '/grades/analysis/class?exam_id=%d&class_name=%s'
                % (exam_id, cls[0])) or {}
            check('分析班级 tab 含表格',
                  bool((c_tab.get('tables') or {})))
        s_tab = expect_ok(client, headers,
                          '/grades/analysis/subject?exam_id=%d'
                          % exam_id) or {}
        check('分析学科 tab 含表格',
              bool((s_tab.get('tables') or {})))
        t_tab = expect_ok(client, headers,
                          '/grades/analysis/teacher?exam_id=%d'
                          % exam_id) or {}
        check('分析教师 tab 含表格',
              bool((t_tab.get('tables') or {})))
        if len(cls) >= 2:
            cp_tab = expect_ok(
                client, headers,
                '/grades/analysis/compare?exam_id=%d&classes=%s'
                % (exam_id, ','.join(cls[:2]))) or {}
            check('分析对比 tab 含表格',
                  bool((cp_tab.get('tables') or {})))
        expect_ok(client, headers, '/grades/subjects?exam_id=%d' % exam_id)
        seg = expect_ok(client, headers,
                        '/grades/segments?exam_id=%d' % exam_id)
        check('分数段非空', bool((seg or {}).get('segments')))
        rank = expect_ok(client, headers,
                         '/grades/segments?exam_id=%d&by_rank=1' % exam_id)
        check('名次分层标记 by_rank',
              (rank or {}).get('by_rank') is True)
        sc = expect_ok(client, headers,
                       '/grades/scores?exam_id=%d&size=30' % exam_id) or {}
        check('成绩单含 rows/subjects',
              isinstance(sc.get('rows'), list)
              and isinstance(sc.get('subjects'), list))
        check('成绩单分页字段完整',
              sc.get('page') == 1 and (sc.get('total_pages') or 0) >= 1
              and sc.get('size') == 30)
        check('成绩单行含总分与科目',
              bool(sc.get('rows')) and 'subjects' in sc['rows'][0]
              and 'rank_class' in sc['rows'][0])
        if cls and sc.get('rows'):
            from urllib.parse import quote
            one = expect_ok(
                client, headers,
                '/grades/scores?exam_id=%d&class_name=%s&size=5'
                % (exam_id, quote(cls[0]))) or {}
            check('成绩单按班级过滤',
                  all(r.get('class_name') == cls[0]
                      for r in (one.get('rows') or [])))
        honor = maybe_ok(client, headers,
                         '/grades/honor?exam_id=%d' % exam_id)
        if honor:
            check('表彰名单含 groups/elite',
                  isinstance(honor.get('groups'), list)
                  and isinstance(honor.get('elite'), dict))
        near = maybe_ok(client, headers,
                        '/grades/near-line?exam_id=%d' % exam_id)
        if near:
            check('临界生含 stats/rows',
                  isinstance(near.get('rows'), list)
                  and 'below_n' in near)
            check('临界生含 class_stats',
                  isinstance(near.get('class_stats'), list))
        cls_names = (classes[0].get('classes') or [])[:2] if classes else []
        if len(cls_names) >= 2:
            cmp = maybe_ok(client, headers,
                           '/grades/compare?exam_id=%d&classes=%s'
                           % (exam_id, ','.join(cls_names)))
            if cmp:
                check('班级对比含 tables',
                      isinstance(cmp.get('tables'), dict))
        gcmp = maybe_ok(client, headers,
                        '/grades/global-compare?exam_id=%d' % exam_id)
        if gcmp:
            check('全局对比含 groups/layers',
                  isinstance(gcmp.get('groups'), list)
                  and isinstance(gcmp.get('layers'), list))

    print('[课表]')
    terms = expect_ok(client, headers, '/schedule/terms') or []
    check('学期列表非空', len(terms) > 0, 'len=%d' % len(terms))
    expect_ok(client, headers, '/schedule/current')
    periods = expect_ok(client, headers, '/schedule/periods') or []
    check('节次非空', len(periods) > 0, 'len=%d' % len(periods))
    pairs = expect_ok(client, headers, '/schedule/classes') or []
    if pairs:
        grade = pairs[0]['grade']
        cls = (pairs[0].get('classes') or [''])[0]
        view = expect_ok(client, headers,
                         '/schedule/class?grade=%s&class_name=%s'
                         % (grade, cls))
        check('班级课表含 grid/periods',
              isinstance((view or {}).get('grid'), dict)
              and isinstance((view or {}).get('periods'), list))
        check('班级课表含 stats', isinstance((view or {}).get('stats'), dict))
    teachers = expect_ok(client, headers, '/schedule/teachers') or []
    check('教师名单非空', len(teachers) > 0, 'len=%d' % len(teachers))
    if teachers:
        uid = teachers[0]['uid']
        tv = expect_ok(client, headers, '/schedule/teacher?uid=%s' % uid)
        check('教师课表含 teacher', isinstance((tv or {}).get('teacher'), dict))
    my = expect_ok(client, headers, '/schedule/my')
    check('本人课表含 mode', bool((my or {}).get('mode')))
    ovv = expect_ok(client, headers, '/schedule/overview?day=1')
    check('总课表含 columns/rows',
          isinstance((ovv or {}).get('columns'), list)
          and isinstance((ovv or {}).get('rows'), list))

    print('[学生档案]')
    if sample_no:
        prof = expect_ok(client, headers,
                         '/student/profile?student_no=%s' % sample_no) or {}
        check('学生档案含 student/sections',
              isinstance(prof.get('student'), dict)
              and isinstance(prof.get('sections'), dict))
        check('学生档案按权限返回积分/奖惩/考勤',
              all(prof['sections'].get(k) for k in
                  ('points', 'rewards', 'attendance', 'portrait')))
        check('学生档案回传成绩入口', prof.get('can_grades') is True)
    _body, _status, _p = call(client, headers,
                              '/student/profile?student_no=__nope__')
    check('学生档案不存在返回 404', _status == 404)

    print('[画像列表]')
    plist = expect_ok(client, headers, '/portrait/students') or {}
    check('画像列表含 rows/options',
          isinstance(plist.get('rows'), list)
          and isinstance(plist.get('options'), dict))
    check('画像列表含 caps',
          isinstance(plist.get('caps'), dict)
          and plist['caps'].get('portrait') is True)
    _body, _status, _p = call(client, headers,
                              '/portrait/students?level=Z')
    check('画像列表非法评级返回 400', _status == 400)

    print('[教务个人]')
    insp = expect_ok(client, headers, '/inspection/mine')
    check('查课含 stats/results',
          isinstance((insp or {}).get('stats'), dict)
          and isinstance((insp or {}).get('results'), list))
    ach = expect_ok(client, headers, '/achievements/mine')
    check('业绩含 stats/statuses',
          isinstance((ach or {}).get('stats'), dict)
          and isinstance((ach or {}).get('statuses'), list))
    _body, _status, _p = call(client, headers,
                              '/achievements/attachment/999999')
    check('业绩附件不存在返回 404', _status == 404)
    directory = expect_ok(client, headers, '/teachers?q=张')
    check('教师名单含 items/subjects',
          isinstance((directory or {}).get('items'), list)
          and isinstance((directory or {}).get('subjects'), list))

    print('[宿舍]')
    summary = expect_ok(client, headers, '/dorm/summary')
    check('宿舍概览含 buildings',
          isinstance((summary or {}).get('buildings'), list))
    filters = expect_ok(client, headers, '/dorm/filters') or {}
    check('宿舍筛选项含 buildings', isinstance(filters.get('buildings'), list))
    rooms = expect_ok(client, headers, '/dorm/rooms?page=1') or {}
    check('房间列表为分页对象',
          isinstance(rooms.get('items'), list) and 'total' in rooms)
    if rooms.get('items'):
        rid = rooms['items'][0]['id']
        detail = expect_ok(client, headers, '/dorm/room/%d' % rid) or {}
        check('房间详情含 beds', isinstance(detail.get('beds'), list))
    dstats = expect_ok(client, headers, '/dorm/stats') or {}
    check('宿舍统计含 grades/classes/totals',
          isinstance(dstats.get('grades'), list)
          and isinstance(dstats.get('classes'), list)
          and isinstance(dstats.get('totals'), dict))

    print('[考务]')
    invig = expect_ok(client, headers, '/exam/my-invigilation') or {}
    check('监考列表含 items', isinstance(invig.get('items'), list))
    expect_ok(client, headers, '/exam/my-invigilation?scope=upcoming')
    affairs = expect_ok(client, headers, '/exam/affairs') or []
    if affairs:
        aid = affairs[0]['id']
        detail = expect_ok(client, headers, '/exam/affair/%d' % aid) or {}
        check('批次详情含 schedules/mine',
              isinstance(detail.get('schedules'), list)
              and isinstance(detail.get('mine'), list))
        rooms_data = maybe_ok(client, headers,
                              '/exam/affair/%d/rooms' % aid)
        if rooms_data:
            check('批次考场含 rooms/my_rooms',
                  isinstance(rooms_data.get('rooms'), list)
                  and isinstance(rooms_data.get('my_rooms'), list))
        work = maybe_ok(client, headers,
                        '/exam/affair/%d/workload' % aid)
        if work is not None:
            check('批次工作量含 cells/detail',
                  'cells' in work and isinstance(work.get('detail'), list))

    print('[调课 / 成绩复核]')
    swaps = expect_ok(client, headers, '/swap/list') or {}
    check('调课列表含 items/statuses',
          isinstance(swaps.get('items'), list)
          and isinstance(swaps.get('statuses'), list))
    check('调课列表含待确认数', 'confirm_count' in swaps)
    entries = expect_ok(client, headers, '/swap/entries') or {}
    check('调课可选课程已返回', isinstance(entries.get('entries'), list))
    confirms = expect_ok(client, headers, '/swap/confirm') or {}
    check('换课待确认已返回', isinstance(confirms.get('items'), list))
    reviews = expect_ok(client, headers, '/grades/reviews') or {}
    check('复核列表含 items/counts',
          isinstance(reviews.get('items'), list)
          and isinstance(reviews.get('counts'), dict))
    rexams = expect_ok(client, headers, '/grades/reviews/exams') or []
    check('复核考试列表已返回', isinstance(rexams, list))
    if rexams and sample_no:
        found = maybe_ok(client, headers,
                         '/grades/reviews/search?exam_id=%d&q=%s'
                         % (rexams[0]['id'], sample_no))
        if found:
            check('复核搜学生含全科成绩',
                  'subjects' in found[0]
                  and 'hidden_subjects' in found[0])

    print('[通知]')
    notices = expect_ok(client, headers, '/notices?page=1') or {}
    check('通知列表含 items/categories',
          isinstance(notices.get('items'), list)
          and isinstance(notices.get('categories'), list))
    expect_ok(client, headers, '/notices/unread')

    print('\n结果：PASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
    for item in FAILED:
        print('  - ' + item)
    return 1 if FAILED else 0


if __name__ == '__main__':
    raise SystemExit(main())

"""已部署小程序接口冒烟：用本机 SECRET_KEY 签令牌，探测真实 HTTP 服务。

用途：区分「代码没合并」和「服务没重启/没部署」。
本机开发机与后端共用同一份 config.py 时可直接跑：

    py -3.12 scripts/smoke_deployed_wx.py
    py -3.12 scripts/smoke_deployed_wx.py --base-url http://10.193.191.52:5000

若目标服务器 SECRET_KEY 不同，令牌会 401，此时只用于检查 404/HTML 错误页。
"""
import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from app.models import Exam, Student, User  # noqa: E402
from app.modules.wxapi.core.tokens import issue_token  # noqa: E402


def _get(url, token):
    req = Request(url, headers={'X-Wx-Token': token})
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except HTTPError as exc:
        return exc.code, exc.read().decode('utf-8', 'replace')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5000')
    parser.add_argument('--user-id', type=int, default=0)
    args = parser.parse_args()
    base = args.base_url.rstrip('/')

    app = create_app()
    with app.app_context():
        user = (User.query.get(args.user_id) if args.user_id
                else User.query.filter_by(role='admin').first())
        if user is None:
            print('找不到测试用户')
            return 1
        token, _ttl = issue_token(user.id)
        exam = Exam.query.order_by(Exam.id.desc()).first()
        student = Student.query.first()
        if exam is None or student is None:
            print('缺少考试或学生数据，无法探测')
            return 1

    paths = [
        '/api/wx/bootstrap',
        '/api/wx/workbench',
        '/api/wx/grades/exams',
        '/api/wx/grades/class-summary?exam_id=%d' % exam.id,
        '/api/wx/grades/subjects?exam_id=%d' % exam.id,
        '/api/wx/grades/segments?exam_id=%d' % exam.id,
        '/api/wx/grades/student/%s' % student.student_number,
        '/api/wx/inspection/mine',
        '/api/wx/achievements/mine',
        '/api/wx/portrait/students',
        '/api/wx/student/profile?student_no=%s' % student.student_number,
        '/api/wx/exam/affairs',
        '/api/wx/teachers',
        '/api/wx/dorm/summary',
        '/api/wx/notices?page=1',
    ]
    failed = []
    for path in paths:
        status, text = _get(base + path, token)
        try:
            body = json.loads(text)
            ok = status == 200 and body.get('success') is True
        except ValueError:
            body = None
            ok = False
        if ok:
            print('PASS', path)
        else:
            failed.append(path)
            tip = '非 JSON/HTML 错误页' if body is None else (
                body.get('message') or '业务失败')
            print('FAIL', path, 'status=%s' % status, tip)

    print('结果：PASS %d / FAIL %d（base=%s）'
          % (len(paths) - len(failed), len(failed), base))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

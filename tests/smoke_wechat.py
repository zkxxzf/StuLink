"""微信绑定 / 扫码登录冒烟（需 WX_MP_MOCK=1）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['WX_MP_MOCK'] = '1'
os.environ.setdefault('STULINK_WX_ENV', 'dev')
TEST_OIDS = ('openid-a', 'openid-b', 'openid-c')

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User, WeChatBinding, WeChatTicket  # noqa: E402

PASS = 0
FAIL = 0


def ok(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('PASS', label)
    else:
        FAIL += 1
        print('FAIL', label)


def main():
    app = create_app()
    if app.config.get('WX_ENV') == 'prod':
        print('拒绝在 prod 环境运行绑定冒烟')
        return 1
    app.config['WX_MP_MOCK'] = True
    app.config['WX_ENV'] = 'dev'
    app.config['WTF_CSRF_ENABLED'] = False
    client = app.test_client()

    with app.app_context():
        admin = User.query.filter_by(role='admin').first()
        teacher = User.query.filter(User.role != 'admin').first()
        if admin is None or teacher is None:
            print('缺少测试账号')
            return 1
        old_admin = admin.password_hash
        old_teacher = teacher.password_hash
        admin.set_password('WxTest123!')
        teacher.set_password('WxTest123!')
        WeChatBinding.query.filter(
            WeChatBinding.openid.in_(TEST_OIDS)).delete(synchronize_session=False)
        WeChatTicket.query.filter(
            WeChatTicket.openid.in_(TEST_OIDS)).delete(synchronize_session=False)
        db.session.commit()
        admin_id, teacher_id = admin.id, teacher.id
        admin_name, teacher_name = admin.username, teacher.username

    try:
        r = client.post('/api/wx/wechat/bind', json={
            'code': 'mock:openid-a',
            'username': admin_name, 'password': 'WxTest123!',
        })
        first = (r.get_json() or {}).get('data', {})
        ok('首次绑定返回令牌', r.status_code == 200 and bool(first.get('token')))

        # 先账号密码登录，进入后再绑定当前微信
        r = client.post('/api/wx/wechat/bind-current', headers={
            'X-Wx-Token': first.get('token', ''),
        }, json={'code': 'mock:openid-c'})
        ok('登录后可绑定当前微信', r.status_code == 200)
        r = client.post('/api/wx/wechat/login',
                        json={'code': 'mock:openid-c'})
        ok('新绑定的微信可直接登录', r.status_code == 200
           and (r.get_json() or {}).get('data', {}).get('bound') is True)

        r = client.post('/api/wx/wechat/login',
                        json={'code': 'mock:openid-a'})
        body = r.get_json() or {}
        ok('已绑定微信直接登录', r.status_code == 200
           and body.get('data', {}).get('bound') is True)

        r = client.post('/api/wx/wechat/bind', json={
            'code': 'mock:openid-a',
            'username': teacher_name, 'password': 'WxTest123!',
        })
        ok('同一微信绑定第二个账号', r.status_code == 200)

        with app.app_context():
            third = User.query.filter(
                User.id.notin_([admin_id, teacher_id])).first()
            third_id = third.id if third else 0
            third_name = third.username if third else ''
            old_third = third.password_hash if third else ''
            if third:
                third.set_password('WxTest123!')
                db.session.commit()
        if third_id:
            r = client.post('/api/wx/wechat/bind', json={
                'code': 'mock:openid-a',
                'username': third_name, 'password': 'WxTest123!',
            })
            ok('第三个账号被拒绝', r.status_code == 409)
            with app.app_context():
                third = db.session.get(User, third_id)
                third.password_hash = old_third
                db.session.commit()

        # 网页扫码登录
        r = client.get('/qr/new')
        qr = r.get_json() or {}
        ticket = qr.get('ticket', '')
        ok('二维码生成', bool(ticket) and '<svg' in qr.get('svg', ''))
        r = client.post('/api/wx/wechat/scan', json={
            'code': 'mock:openid-a', 'ticket': ticket,
        })
        body = r.get_json() or {}
        ok('多账号扫码要求选择', body.get('data', {}).get('need_select'))
        token = body.get('data', {}).get('select_token', '')
        r = client.post('/api/wx/wechat/scan/confirm', json={
            'select_token': token, 'user_id': admin_id, 'ticket': ticket,
        })
        ok('扫码确认账号', r.status_code == 200)
        r = client.get('/qr/status/' + ticket)
        ok('二维码状态已批准',
           (r.get_json() or {}).get('status') == 'approved')
        r = client.post('/qr/consume', json={'ticket': ticket})
        ok('网页换取登录态', r.status_code == 200
           and bool((r.get_json() or {}).get('redirect')))
        r = client.get('/my/settings')
        ok('登录后能访问用户设置', r.status_code == 200)
        r = client.get('/wechat-bind')
        ok('微信绑定页可访问', r.status_code == 200)
        r = client.get('/users/')
        page = r.get_data(as_text=True)
        ok('教师管理显示微信列与分组选项卡',
           r.status_code == 200 and '微信绑定' in page
           and '系统 UID' in page and 'user-group-tabs' in page)

        # 网页扫码绑定
        r = client.get('/wechat-bind/new')
        qr = r.get_json() or {}
        bind_ticket = qr.get('ticket', '')
        r = client.post('/api/wx/wechat/scan', json={
            'code': 'mock:openid-b', 'ticket': bind_ticket,
        })
        ok('网页绑定二维码被确认', r.status_code == 200)
        r = client.get('/wechat-bind/status/' + bind_ticket)
        ok('绑定状态已批准',
           (r.get_json() or {}).get('status') == 'approved')
        with app.app_context():
            ok('openid-b 已绑定',
               WeChatBinding.query.filter_by(openid='openid-b').count() == 1)
    finally:
        with app.app_context():
            WeChatBinding.query.filter(
                WeChatBinding.openid.in_(TEST_OIDS)).delete(
                    synchronize_session=False)
            WeChatTicket.query.filter(
                WeChatTicket.openid.in_(TEST_OIDS)).delete(
                    synchronize_session=False)
            admin = db.session.get(User, admin_id)
            teacher = db.session.get(User, teacher_id)
            if admin:
                admin.password_hash = old_admin
            if teacher:
                teacher.password_hash = old_teacher
            db.session.commit()

    print('结果：PASS %d / FAIL %d' % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())

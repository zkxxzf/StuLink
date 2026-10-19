# -*- coding: utf-8 -*-
"""一次性脚本：把网页复核审批改为调用 review_service（跑完删除）。"""
import io

PATH = r'd:\00同步文件\00程序\stulink\app\modules\grades\routes\score_review.py'
NEW_FUNC = '''@bp.route('/reviews/<int:rid>/review', methods=['POST'])
@login_required
@perm_required('grades.score_edit')
def review_audit(rid):
    """批准 / 驳回（审批与「批准并应用」逻辑见 review_service.audit_review）。"""
    r = _review_or_404(rid)
    back = redirect(url_for('grades.reviews_manage_page'))
    # 审批（「批准并应用」会直接改分并重算排名）必须先过**数据范围**校验：
    # 只有 grades.score_edit 功能权限、但不在本年级范围内的账号不得越权写分。
    _r_exam = db.session.get(Exam, r.exam_id)
    if _r_exam is not None:
        try:
            scope_service.check_exam_visible(current_user, _r_exam)
        except PermissionError:
            abort(403)
    _sc = score_guard.resolve(current_user)
    if not _sc.can_view_class(
            r.grade or (_r_exam.grade if _r_exam else ''), r.class_name):
        abort(403)
    action = (request.form.get('action') or '').strip()
    note = (request.form.get('review_note') or '').strip()[:200]
    apply_now = (request.form.get('apply') or '') in ('1', 'on', 'true')
    ok, msg = review_service.audit_review(
        r, current_user, action, note, apply_now)
    flash(msg, 'success' if ok else 'danger')
    return back'''.splitlines()


def main():
    with io.open(PATH, encoding='utf-8') as fp:
        lines = fp.read().splitlines()
    assert lines[163].strip().startswith("@bp.route('/reviews/<int:rid>/review'")
    assert lines[265].strip() == 'return back'
    lines[163:266] = NEW_FUNC
    assert lines[31].startswith('from app.models.grades import (Exam, ExamScore')
    lines[31:33] = ['from app.models.grades import (Exam, ScoreReviewRequest,',
                    '                               TOTAL_SUBJECT, SUBJECTS)']
    assert lines[34].startswith('from app.modules.grades.services import (ranking')
    lines[34:36] = [
        'from app.modules.grades.services import (review_service,',
        '                                        scope as scope_service,',
        '                                        score_guard)',
    ]
    assert lines[37].startswith(
        'from app.modules.grades.utils import invalidate_exam_cache')
    del lines[37]
    with io.open(PATH, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(lines) + '\n')
    print('patched, lines=%d' % len(lines))


if __name__ == '__main__':
    main()

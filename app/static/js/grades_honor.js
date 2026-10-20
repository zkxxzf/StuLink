/* StuLink 表彰名单页（2026-10-18）
 * 职责：考试/分组维度切换（整页刷新）、三个榜单页签、卓越班班级与单科切换、
 * 班级+关键词过滤（纯前端，不请求后端）、打印。
 */
(function () {
    var $app = $('#honorApp');
    if (!$app.length) { return; }
    var examId = String($app.data('exam') || '');

    function go(group) {
        var url = '/grades/honor';
        var qs = [];
        if (examId) { qs.push('exam=' + encodeURIComponent(examId)); }
        if (group) { qs.push('group=' + encodeURIComponent(group)); }
        if (qs.length) { url += '?' + qs.join('&'); }
        window.location.href = url;
    }

    $('#hnExam').on('change', function () {
        if (this.value) { examId = this.value; go(''); }
    });
    $('#hnGroup').on('change', function () { go(this.value); });
    $('#hnPrint').on('click', function () { window.print(); });

    /* 导出：带上当前考试 / 分组维度 / 班级 / 关键词，服务端按同口径过滤 */
    $('#hnExport').on('click', function () {
        var qs = [];
        if (examId) { qs.push('exam=' + encodeURIComponent(examId)); }
        var group = $('#hnGroup').val();
        if (group) { qs.push('group=' + encodeURIComponent(group)); }
        var cls = $('#hnClass').val();
        if (cls) { qs.push('cls=' + encodeURIComponent(cls)); }
        var kw = ($('#hnKw').val() || '').trim();
        if (kw) { qs.push('kw=' + encodeURIComponent(kw)); }
        window.location.href = '/grades/honor/export'
            + (qs.length ? '?' + qs.join('&') : '');
    });

    /* ---------- 榜单页签 ---------- */
    $('.hn-tab').on('click', function () {
        $('.hn-tab').removeClass('active');
        $(this).addClass('active');
        $('.honor-pane').addClass('d-none');
        $('#pane-' + $(this).data('pane')).removeClass('d-none');
        applyFilter();
    });

    /* ---------- 卓越班：班级切换 ---------- */
    $('[data-elite-class]').on('click', function () {
        var name = $(this).data('elite-class');
        $('[data-elite-class]').removeClass('active');
        $(this).addClass('active');
        $('.honor-elite-class').addClass('d-none');
        $('.honor-elite-class[data-class="' + name + '"]').removeClass('d-none');
        applyFilter();
    });

    /* ---------- 卓越班：单科切换 ---------- */
    $('.honor-subj-chips button').on('click', function () {
        var card = $(this).closest('.honor-card');
        card.find('.honor-subj-chips button').removeClass('active');
        $(this).addClass('active');
        card.find('.honor-subj-box').addClass('d-none');
        card.find('.honor-subj-box[data-subject="' + $(this).data('subject') + '"]')
            .removeClass('d-none');
        applyFilter();
    });

    /* ---------- 班级 + 关键词过滤 ---------- */
    function applyFilter() {
        var cls = $('#hnClass').val() || '';
        var kw = ($('#hnKw').val() || '').trim().toLowerCase();
        $('.honor-table').each(function () {
            var table = $(this);
            var visible = 0;
            table.find('tbody tr[data-key]').each(function () {
                var tr = $(this);
                var ok = !cls || tr.attr('data-class') === cls;
                if (ok && kw) {
                    ok = (tr.attr('data-key') || '').toLowerCase().indexOf(kw) >= 0;
                }
                tr.toggleClass('hn-hidden', !ok);
                if (ok) { visible += 1; }
            });
            var empty = table.find('tr.hn-empty-row');
            if (!visible) {
                if (!empty.length) {
                    empty = $('<tr class="hn-empty-row"><td colspan="99" '
                        + 'class="text-center text-muted py-3">当前筛选无匹配</td></tr>');
                    table.find('tbody').append(empty);
                }
                empty.removeClass('hn-hidden');
            } else if (empty.length) {
                empty.addClass('hn-hidden');
            }
        });
        var pane = $('.honor-pane:not(.d-none)');
        var shown = pane.find('.honor-table:visible tbody tr[data-key]:not(.hn-hidden)').length;
        var total = pane.find('.honor-table:visible tbody tr[data-key]').length;
        $('#hnShown').text(shown);
        $('#hnTotal').text(total);
    }

    $('#hnClass').on('change', applyFilter);
    $('#hnKw').on('input', applyFilter);
    $('#hnClear').on('click', function () {
        $('#hnClass').val('');
        $('#hnKw').val('');
        applyFilter();
    });
    applyFilter();
})();

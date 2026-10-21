/* StuLink 分层模板管理前端 v1.9.2
 * 自定义模板（层名 + 可选默认比例），考试再绑定到某个模板
 */
(function () {
var csrf = function () { return window.csrfToken || ''; };

function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}
function post(url, body) {
    return $.ajax({
        url: url, type: 'POST', data: JSON.stringify(body || {}),
        contentType: 'application/json', headers: {'X-CSRFToken': csrf()}
    });
}

/* ---------------- 列表 ---------------- */
function load() {
    $.getJSON('/grades/api/band-templates', function (res) {
        var $tb = $('#tplTable tbody').empty();
        var list = (res.data || {}).templates || [];
        window.__tpls = list;                                        // 缓存，编辑弹窗回填用
        window.__examTypes = (res.data || {}).exam_type_options || []; // 可选考试类型
        if (!list.length) {
            $tb.append('<tr><td colspan="5" class="text-muted">暂无模板</td></tr>');
            return;
        }
        list.forEach(function (t) {
            /* 后缀随下界方式变化：比例→%、名次→第N名、分数→分。
               原先一律拼 %，名次模板会显示成「前10 10%」造成误读 */
            var mode = t.lower_mode;
            var names = (t.layers || []).map(function (x) {
                var v = (x.ratio === null || x.ratio === undefined) ? null : x.ratio;
                var suffix = '';
                if (v !== null && v !== '') {
                    suffix = mode === 'rank' ? ('第' + v + '名')
                           : (mode === 'ratio' ? (v + '%') : (v + '分'));
                }
                return esc(x.name)
                    + (suffix ? '<span class="text-muted small"> ' + suffix + '</span>' : '');
            }).join(' ＞ ');
            var tr = $('<tr>');
            tr.append($('<td>').append(esc(t.name))
                .append(t.is_builtin ? ' <span class="badge bg-secondary">内置</span>' : '')
                .append((t.exam_types && t.exam_types.length)
                    ? '<div class="text-muted small">适用：' + esc(t.exam_types.join('、')) + '</div>'
                    : '<div class="text-muted small">通用</div>'));
            tr.append($('<td>').text(t.lower_mode === 'ratio' ? '人数比例%'
                                     : (t.lower_mode === 'rank' ? '人数（名次）' : '固定分数')));
            tr.append($('<td>').html(names));
            tr.append($('<td class="text-muted small">').text(t.remark || ''));
            var op = $('<td>');
            op.append('<button class="btn btn-sm btn-outline-primary tpl-edit" data-id="' + t.id + '">编辑</button> ');
            if (!t.is_builtin) {
                op.append('<button class="btn btn-sm btn-outline-danger tpl-del" data-id="' + t.id + '">删除</button>');
            }
            tr.append(op);
            $tb.append(tr);
        });
    }).fail(function () { $('#tplTable tbody').html('<tr><td colspan="5" class="text-danger">加载失败</td></tr>'); });
}

/* ---------------- 层编辑 ---------------- */
function renderLayers(layers) {
    var $tb = $('#tplLayers tbody').empty();
    (layers || []).forEach(function (x, i) {
        var tr = $('<tr>');
        tr.append($('<td class="text-muted">').text(i + 1));
        tr.append($('<td>').append($('<input class="form-control form-control-sm tl-name">').val(x.name || '')));
        var $num = $('<input type="number" class="form-control form-control-sm tl-ratio">')
            .val((x.ratio === null || x.ratio === undefined) ? '' : x.ratio);
        applyNumConstraints($num);      // 名次可能 > 100（如 550），不可被 max=100 卡住
        tr.append($('<td>').append($num));
        var op = $('<td>');
        op.append('<button class="btn btn-sm btn-outline-secondary tl-up" title="上移">↑</button> ');
        op.append('<button class="btn btn-sm btn-outline-secondary tl-down" title="下移">↓</button> ');
        op.append('<button class="btn btn-sm btn-outline-danger tl-del">×</button>');
        tr.append(op);
        $tb.append(tr);
    });
    if (!(layers || []).length) {
        $tb.append('<tr><td colspan="4" class="text-muted">请点「新增层」添加</td></tr>');
    }
}
function readLayers(keepBlank) {
    // 默认**保留未填写的空白行**：否则连续点「新增层」时空白行会被吞掉，
    // 变成"必须先输入才能再加一行"，无法一次加 9 层再逐个填。
    var out = [];
    $('#tplLayers tbody tr').each(function () {
        var $n = $(this).find('.tl-name');
        if (!$n.length) return;           // 无输入框的占位行
        var name = String($n.val() || '').trim();
        // 仅当显式传 false（保存时）才过滤空行；默认保留，保证可连续新增
        if (!name && keepBlank === false) return;
        var r = $(this).find('.tl-ratio').val();
        out.push({name: name, ratio: (r === '' || r === null) ? null : parseFloat(r)});
    });
    return out;
}
function swap(a, b) {
    var rows = readLayers();
    if (b < 0 || b >= rows.length) return;
    var t = rows[a]; rows[a] = rows[b]; rows[b] = t;
    renderLayers(rows);
}

/* 第三列输入约束随模式变化：比例 0-100，名次 ≥1（可到几百），分数 ≥0 */
function applyNumConstraints($inp) {
    var m = $('#tplMode').val();
    return $inp.attr('step', m === 'ratio' ? 0.1 : 1)
               .attr('min', m === 'rank' ? 1 : 0)
               .attr('max', m === 'ratio' ? 100 : '');
}

/* 第三列含义随下界方式变化：比例→百分比、名次→第几名、分数→分数线 */
function syncUnitLabel() {
    var m = $('#tplMode').val();
    $('#tplRatioTh').text(m === 'rank' ? '默认名次（选填）'
                          : (m === 'ratio' ? '默认比例%（选填）' : '固定分数（选填）'));
    // 切换模式时就地更新已渲染行的输入约束，不然 rank 模式会被旧的 max=100 拦住
    $('#tplLayers .tl-ratio').each(function () { applyNumConstraints($(this)); });
}

/* 适用考试类型：不勾=通用；勾选后该类型的新考试会自动默认选中本模板 */
function renderExamTypes(selected) {
    var box = $('#tplExamTypes').empty();
    var opts = window.__examTypes || [];
    if (!opts.length) {
        box.append('<span class="text-muted small">（暂无可选考试类型，可先在字典 exam_type 维护）</span>');
        return;
    }
    opts.forEach(function (t, i) {
        box.append('<div class="form-check form-check-inline mb-0">'
            + '<input class="form-check-input tpl-et" type="checkbox" value="' + esc(t) + '" id="tet' + i + '">'
            + '<label class="form-check-label small" for="tet' + i + '">' + esc(t) + '</label></div>');
    });
    (selected || []).forEach(function (v) {
        $('#tplExamTypes .tpl-et').each(function () {
            if ($(this).val() === v) { $(this).prop('checked', true); }
        });
    });
}
function readExamTypes() {
    var out = [];
    $('#tplExamTypes .tpl-et:checked').each(function () { out.push($(this).val()); });
    return out;
}

function openModal(t) {
    $('#tplId').val(t ? t.id : '');
    $('#tplName').val(t ? t.name : '');
    $('#tplMode').val(t ? t.lower_mode : 'score');
    syncUnitLabel();
    $('#tplRemark').val(t ? (t.remark || '') : '');
    renderExamTypes(t ? (t.exam_types || []) : []);
    $('#tplModalTitle').text(t ? '编辑模板' : '新建模板');
    renderLayers(t ? t.layers : [{name: '', ratio: null}, {name: '', ratio: null}]);
    new bootstrap.Modal(document.getElementById('tplModal')).show();
}

function findTpl(id) {
    var hit = null;
    (window.__tpls || []).forEach(function (t) { if (String(t.id) === String(id)) hit = t; });
    return hit;
}

/* ---------------- 事件 ---------------- */
$(function () {
    load();

    $('#tplAdd').on('click', function () { openModal(null); });
    $('#tplMode').on('change', syncUnitLabel);
    // 可连续点击：每次追加一个空行并聚焦，方便一次加多行后再逐个填
    $('#tplLayerAdd').on('click', function () {
        renderLayers(readLayers().concat([{name: '', ratio: null}]));
        $('#tplLayers tbody tr').last().find('.tl-name').focus();
    });
    /* v1.19.56.64 支持从 Markdown / Excel / 纯文本直接粘贴多行层名
       （表格形式：一行一层；自动去掉 | - 等表格符号与表头行）*/
    $('#tplLayers').on('paste', '.tl-name', function (e) {
        var txt = (e.originalEvent && e.originalEvent.clipboardData
                   ? e.originalEvent.clipboardData.getData('text') : '');
        if (!txt) return;
        var lines = String(txt).replace(/\r/g, '').split('\n');
        if (lines.length < 2 && txt.indexOf('\t') < 0 && txt.indexOf('|') < 0) return;
        e.preventDefault();
        var rows = readLayers(), added = 0;
        lines.forEach(function (ln) {
            var s = ln.trim();
            if (!s) return;
            var cells = (s.indexOf('|') >= 0 ? s.split('|') : s.split('\t'))
                .map(function (x) { return x.trim(); })
                .filter(function (x) { return x !== ''; });
            if (!cells.length) return;
            var nm = cells[0].replace(/^[\-*#>]\s*/, '').trim();
            if (!nm || /^[-: ]+$/.test(nm)) return;                 // 分隔行 |---|---|
            if (/^(层名|名称|序号|层级|line|name)$/i.test(nm)) return;  // 表头行
            var num = null;
            if (cells.length > 1) {
                var v = parseFloat(String(cells[cells.length - 1]).replace(/[=%]/g, ''));
                if (!isNaN(v)) num = v;
            }
            rows.push({name: nm, ratio: num});
            added++;
        });
        if (!added) { $(this).val(txt.split('\n')[0]); return; }
        renderLayers(rows);
        alert('已粘贴 ' + added + ' 个层（请核对层名与顺序，第 1 层最高）');
    });
    $('#tplLayers').on('keydown', '.tl-name', function (e) {
        // 末行按回车 = 再新增一行，连续录入不用反复点按钮
        if (e.key === 'Enter'){
            e.preventDefault();
            $('#tplLayerAdd').click();
        }
    });
    $('#tplLayers').on('click', '.tl-up', function () {
        swap($(this).closest('tr').index(), $(this).closest('tr').index() - 1);
    });
    $('#tplLayers').on('click', '.tl-down', function () {
        swap($(this).closest('tr').index(), $(this).closest('tr').index() + 1);
    });
    $('#tplLayers').on('click', '.tl-del', function () {
        var rows = readLayers();
        rows.splice($(this).closest('tr').index(), 1);
        renderLayers(rows);
    });
    $('#tplTable').on('click', '.tpl-edit', function () {
        openModal(findTpl($(this).data('id')));
    });
    $('#tplTable').on('click', '.tpl-del', function () {
        var id = $(this).data('id');
        if (!confirm('删除该模板？已绑定它的考试会自动解除绑定。')) return;
        post('/grades/api/band-templates/' + id + '/delete').done(function (res) {
            if (!res.success) { alert(res.message || '删除失败'); return; }
            load();
        }).fail(function (x) {
            alert((x.responseJSON && x.responseJSON.message) || '删除失败');
        });
    });
    $('#tplSave').on('click', function () {
        var layers = readLayers(false);      // 保存时才过滤未填名的行
        if (!$.trim($('#tplName').val())){ alert('请填写模板名称'); return; }
        if (!layers.length){ alert('请至少填写一个层名'); return; }
        var body = {
            id: $('#tplId').val() || null,
            name: $('#tplName').val(),
            lower_mode: $('#tplMode').val(),
            remark: $('#tplRemark').val(),
            exam_types: readExamTypes(),
            layers: layers
        };
        post('/grades/api/band-templates/save', body).done(function (res) {
            if (!res.success) { alert(res.message || '保存失败'); return; }
            bootstrap.Modal.getInstance(document.getElementById('tplModal')).hide();
            load();
        }).fail(function (x) {
            alert((x.responseJSON && x.responseJSON.message) || '保存失败');
        });
    });
});
})();

/* StuLink 表格列显隐通用组件（2026-10-15）
 *
 * 用户口径：「所有的表格的列加一个显示与不显示的功能，勾选即显示，不勾选就不显示」。
 *
 * 交互：表格卡片头一个小「列」按钮 → 下拉里逐列勾选；勾选 = 显示，取消 = 隐藏；
 *       状态按「表 key」持久化到 localStorage（下次打开同一张表仍是你的选择；
 *       存储的是**隐藏集合**，所以以后新增的列默认可见，不会被旧记录吞掉）；
 *       保护：不允许把列全部取消（表格空了没意义）——最后一列取消时自动忽略。
 *
 * 两种接入方式：
 *   1) 动态重建的表（推荐，列从数据里来）：渲染时用 TableCols.visible(key, cols)
 *      过滤列；卡片头挂 TableCols.menu(key, cols, onChange)。
 *   2) 静态表（服务端渲染）：TableCols.attach($table, key) —— 表头外右上角浮一个小按钮，
 *      勾选后用 CSS 隐藏对应列（单行表头适用；含 colspan/rowspan 的复杂表头自动跳过，
 *      避免破相）。
 *
 * 本文件只依赖 jQuery（base.html 全局加载）。ES5 语法（旧微信 X5 兼容，与
 * check_frontend_compat 自检口径一致）。
 */
(function (global, $) {
    var PREFIX = 'stulink.cols.hidden::';

    function loadHidden(key) {
        try {
            var raw = global.localStorage.getItem(PREFIX + key);
            var arr = raw ? JSON.parse(raw) : [];
            return (arr && arr instanceof Array) ? arr : [];
        } catch (e) { return []; }
    }
    function saveHidden(key, arr) {
        try { global.localStorage.setItem(PREFIX + key, JSON.stringify(arr)); } catch (e) {}
    }

    /* 该表当前可见的列 key 数组（无记录 = 全部可见） */
    function visible(key, cols) {
        var hidden = loadHidden(key), out = [];
        (cols || []).forEach(function (c) {
            if (hidden.indexOf(c.key) < 0) out.push(c.key);
        });
        return out;
    }

    /* 生成「列」下拉（Bootstrap dropdown）。
     * key   — 表的稳定标识（如 'a1::class_summary'）
     * cols  — [{key, label}, ...]
     * onChange(visibleKeys) — 勾选变化回调（调用方据此重渲染）
     */
    function menu(key, cols, onChange) {
        cols = cols || [];
        var $dd = $('<div class="dropdown tc-col-menu d-inline-block">');
        $dd.append('<button type="button" class="btn btn-sm btn-outline-secondary dropdown-toggle"'
            + ' data-bs-toggle="dropdown" data-bs-auto-close="outside"'
            + ' title="勾选要显示的列（不勾选即隐藏）">'
            + '<i class="bi bi-layout-three-columns"></i> 列</button>');
        var $ul = $('<ul class="dropdown-menu dropdown-menu-end p-2"'
            + ' style="max-height:320px;overflow:auto;min-width:190px">');
        var hidden = loadHidden(key);
        $ul.append('<div class="d-flex justify-content-between align-items-center px-1 pb-1 mb-1 border-bottom">'
            + '<span class="small text-muted">勾选即显示</span>'
            + '<button type="button" class="btn btn-sm btn-link p-0 tc-col-all">全选</button></div>');
        cols.forEach(function (c, i) {
            var cid = 'tc' + Math.random().toString(36).slice(2, 8) + '_' + i;
            var $item = $('<div class="form-check py-0">');
            $item.append($('<input class="form-check-input tc-col" type="checkbox">')
                .attr('id', cid).attr('data-col-key', c.key)
                .prop('checked', hidden.indexOf(c.key) < 0));
            $item.append($('<label class="form-check-label small">')
                .attr('for', cid).text(c.label || c.key));
            $ul.append($item);
        });

        function currentKeys() {
            var keys = [];
            $ul.find('input.tc-col:checked').each(function () {
                keys.push($(this).attr('data-col-key'));
            });
            return keys;
        }
        function apply() {
            var keys = currentKeys();
            if (!keys.length) {                       // 保护：不允许全部隐藏
                var $first = $ul.find('input.tc-col').first();
                $first.prop('checked', true);
                keys = currentKeys();
            }
            var hiddenKeys = [];
            cols.forEach(function (c) {
                if (keys.indexOf(c.key) < 0) hiddenKeys.push(c.key);
            });
            saveHidden(key, hiddenKeys);
            if (onChange) onChange(keys);
        }
        $ul.on('change', 'input.tc-col', apply);
        $ul.on('click', '.tc-col-all', function () {
            var $boxes = $ul.find('input.tc-col');
            var allOn = $boxes.filter(':checked').length === $boxes.length;
            $boxes.prop('checked', !allOn);
            $(this).text(allOn ? '全选' : '全不选');
            apply();
        });
        $dd.append($ul);
        return $dd;
    }

    /* 静态表挂载：表容器右上角浮一个小按钮，勾选后 CSS 隐藏列。
     * 只支持单行表头的表（有 colspan/rowspan 的复杂表头自动跳过并返回 false）。 */
    function attach($table, key) {
        $table = $($table);
        if (!$table.length) return false;
        var $headRow = $table.find('thead tr').first();
        if ($headRow.length !== 1) return false;      // 多行表头（分组表头）跳过
        var $ths = $headRow.find('th');
        var complex = false;
        $ths.each(function () {
            if (this.colSpan > 1 || this.rowSpan > 1) complex = true;
        });
        if (complex || $ths.length < 3) return false; // 列太少或复杂表头不做
        var cols = [];
        $ths.each(function (i) {
            var label = $.trim($(this).text()) || ('第' + (i + 1) + '列');
            cols.push({ key: 'i' + i, label: label });
        });
        var $m = menu(key, cols, function (keys) {
            $table.find('tr').each(function () {
                $(this).children('th,td').each(function (i) {
                    if (this.colSpan > 1) return;
                    $(this).toggle(keys.indexOf('i' + i) >= 0);
                });
            });
        });
        // 初始应用一次（恢复上次选择）
        var visKeys = visible(key, cols);
        $table.find('tr').each(function () {
            $(this).children('th,td').each(function (i) {
                if (this.colSpan > 1) return;
                $(this).toggle(visKeys.indexOf('i' + i) >= 0);
            });
        });
        var $host = $table.closest('.table-responsive');
        if (!$host.length) $host = $table.parent();
        if (!$host.length) return false;
        $host.css('position', 'relative');
        $m.addClass('tc-col-float').css({
            position: 'absolute', top: '2px', right: '6px', zIndex: 5
        });
        $host.append($m);
        return true;
    }

    global.TableCols = { visible: visible, menu: menu, attach: attach };
})(window, jQuery);

/* StuLink v1.18.0.0 2026-09-23
 * 公共前端工具（R-2 前端渲染规约）：转义函数在 base.html 全局引入，
 * 所有页面脚本统一复用，禁止再各写一份实现（清单第 0 节铁律②）。
 */
(function (window) {
    'use strict';

    var HTML_MAP = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    };

    /** HTML 文本转义：用于「把服务端字符串拼进 innerHTML」的一切场景 */
    function escHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return HTML_MAP[c];
        });
    }

    /** 属性上下文转义：比 escHtml 更严格，额外转义反引号与等号，避免拼进 on* 属性 */
    function escAttr(s) {
        return String(s == null ? '' : s).replace(/[&<>"'`=]/g, function (c) {
            return HTML_MAP[c] || ('&#' + c.charCodeAt(0) + ';');
        });
    }

    /** 把用户数据交给 JS 字符串变量时的安全封装（优先用 data-* + JSON.parse） */
    function jsStr(s) {
        return JSON.stringify(s == null ? '' : String(s));
    }

    window.escHtml = escHtml;
    window.escAttr = escAttr;
    window.jsStr = jsStr;

    /* 2026-10-12 UI 审计 M5：全局操作反馈（stuToast）。
       成绩模块原先混用 alert() / #scFlash / #qMsg / #tcMsg / .rp-copied 五种反馈，
       同一模块内用户要适应 4 种心智模型。这里提供一个非阻塞、自动消失的 toast，
       样式见 static/css/grades.css 的 .stu-toast-*。
       用法：stuToast('已保存') / stuToast('失败原因', 'err')；type ∈ ok|warn|err。
       不替代「删除/覆盖类」的 confirm（那类仍需用户明确决策）。 */
    var _toastWrap = null;
    function toast(msg, type, ms) {
        type = type || 'ok';
        ms = ms || (type === 'err' ? 4200 : 2200);
        if (!_toastWrap) {
            _toastWrap = document.createElement('div');
            _toastWrap.className = 'stu-toast-wrap';
            document.body.appendChild(_toastWrap);
        }
        var el = document.createElement('div');
        el.className = 'stu-toast ' + type;
        el.setAttribute('role', type === 'err' ? 'alert' : 'status');
        el.textContent = msg == null ? '' : String(msg);
        _toastWrap.appendChild(el);
        setTimeout(function () {
            el.classList.add('leaving');
            setTimeout(function () {
                if (el.parentNode) el.parentNode.removeChild(el);
            }, 300);
        }, ms);
        return el;
    }

    window.stuToast = toast;

    /* 2026-10-13：滚动容器（.table-responsive 等 overflow:auto）内的下拉菜单定位。
       默认 .dropdown-menu 是 position:absolute，超出容器边界就被 overflow 裁掉
       （2026-10-13 用户实测：考务⑤考试时间表的科目多选下拉「弹在页面下方、被表格遮住」）。
       用法：给 [data-bs-toggle="dropdown"] 按钮加 data-popper-fixed，
       再对所在容器（或新插入的行）调用 initPopperFixedDropdowns()。
       原理：改用 Popper 的 fixed 定位策略（菜单以视口为参照渲染），不再受容器 overflow 裁剪；
             boundary 用 viewport、flip 保留，贴近视口边缘时自动向上/换边展开。 */
    var POPPER_FIXED_SEL = '[data-bs-toggle="dropdown"][data-popper-fixed]';
    var POPPER_FIXED_CFG = {
        popperConfig: {
            strategy: 'fixed',
            modifiers: [
                { name: 'preventOverflow', options: { boundary: 'viewport' } },
                { name: 'offset', options: { offset: [0, 4] } },
                { name: 'flip', options: { fallbackPlacements: ['top-start', 'top-end', 'bottom-start', 'bottom-end'] } }
            ]
        }
    };

    /** 为 scope 内标记了 data-popper-fixed 的下拉按钮创建/复用固定定位实例（已存在的不重建） */
    function initPopperFixedDropdowns(scope) {
        if (!window.bootstrap || !window.bootstrap.Dropdown) return;
        var list = (scope || document).querySelectorAll(POPPER_FIXED_SEL);
        for (var i = 0; i < list.length; i++) {
            window.bootstrap.Dropdown.getOrCreateInstance(list[i], POPPER_FIXED_CFG);
        }
    }

    window.initPopperFixedDropdowns = initPopperFixedDropdowns;
})(window);

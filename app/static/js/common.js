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
})(window);

/* StuLink v1.17.1 2026-09-23
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
})(window);

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

    /* 2026-10-13：编号补零到两位（考场号 / 座号）——与服务端 app.utils.text_guard.pad2
       同一口径（纯数字 zfill(2)，'A1' 原样）。页面里前端渲染编号时统一走它，
       不要再各写一份 padStart。 */
    function pad2(value) {
        var s = (value === null || value === undefined) ? '' : String(value).trim();
        return /^\d+$/.test(s) ? (s.length < 2 ? '0' + s : s) : s;
    }

    window.stuPad2 = pad2;

    /* =========================================================================
       2026-10-14 主题取色（审计修复）：图表色不再写死字面量
       -------------------------------------------------------------------------
       背景：ECharts 的颜色是 JS 字面量（grades.js / student_query.js /
       inspection.js / points.js …），CSS 的 `[data-bs-theme="dark"] ...` 兜底
       完全够不到 → 深色下出现刺眼白块（热力图）与看不见的线。
       做法：theme.css 提供两套 --sl-chart-1..10 / --sl-heat-1..4 / 语义色变量，
       这里只负责「读变量 + 兜底」。页面脚本一律调用本组函数，不要在各自文件里
       再写一份字面量。读不到（老缓存 / 变量未定义）时回落到原浅色字面量，
       保证图表不空、不报错。
       ========================================================================= */
    /** 读 CSS 变量；读不到用兜底值（与 grades_dashboard.js 的 v() 同一口径） */
    function stuCssVar(name, fallback) {
        var v = '';
        try {
            v = (window.getComputedStyle(document.documentElement)
                .getPropertyValue(name) || '').trim();
        } catch (e) { v = ''; }
        return v || fallback;
    }

    window.stuCssVar = stuCssVar;

    /** 按前缀批量读 --prefix-1..count，缺哪个用 fallbacks 补哪个 */
    function stuCssVarList(prefix, count, fallbacks) {
        var out = [], i, v;
        for (i = 1; i <= count; i++) {
            v = stuCssVar(prefix + i, '');
            out.push(v || (fallbacks && fallbacks[i - 1]) || '#3b82f6');
        }
        return out;
    }

    /** 图表主色板（10 槽，语义固定：蓝紫绿橙红青粉黄绿橙红靛，两套主题只换明度） */
    window.stulinkPalette = function () {
        return stuCssVarList('--sl-chart-', 10, [
            '#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444',
            '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1'
        ]);
    };

    /** 热力图渐变色（低值 → 高值）；深色下为深→亮逆序，不再是刺眼白块 */
    window.stulinkHeatColors = function () {
        return stuCssVarList('--sl-heat-', 4, ['#eff6ff', '#93c5fd', '#3b82f6', '#1d4ed8']);
    };

    /** 热力图格内数字色（保证在 4 段色上都读得清） */
    window.stulinkHeatLabel = function () {
        return stuCssVar('--sl-heat-label', '#0f172a');
    };

    /** 语义单色：状态柱 / 参考线 / 面积图。红黄绿仍要能区分，只提亮不换色相。 */
    window.stulinkSemantic = function () {
        return {
            ok: stuCssVar('--sl-chart-ok', '#10b981'),
            warn: stuCssVar('--sl-chart-warn', '#f59e0b'),
            danger: stuCssVar('--sl-chart-danger', '#ef4444'),
            info: stuCssVar('--sl-chart-info', '#3b82f6'),
            purple: stuCssVar('--sl-chart-purple', '#6366f1'),
            cyan: stuCssVar('--sl-chart-cyan', '#0ea5e9'),
            muted: stuCssVar('--sl-chart-muted', '#94a3b8'),
            line: stuCssVar('--sl-border', '#e2e8f0'),
            text: stuCssVar('--sl-text', '#1e293b')
        };
    };

    /** 当前是否深色（少数必须二选一的场景用，能走变量就别用这个） */
    window.stulinkIsDark = function () {
        return document.documentElement.getAttribute('data-bs-theme') === 'dark';
    };

    /* 主题切换广播：base.html 的 StuLinkTheme.apply() 无法在本次改动范围内修改，
       这里用 MutationObserver 监听 <html data-bs-theme> 变化并广播同一事件，
       使已渲染的 ECharts 实例可以就地重设颜色（无需刷新页面）。
       只在真正发生变化时触发一次，且做了 try/catch 兜底（老浏览器无 MO 就降级为
       「刷新页面后生效」）。 */
    var THEME_EVT = 'stulink:themechange';
    var _themeHandlers = [];
    var _lastTheme = document.documentElement.getAttribute('data-bs-theme');

    window.stuOnThemeChange = function (fn) {
        if (typeof fn !== 'function') { return; }
        _themeHandlers.push(fn);
    };

    function broadcastTheme() {
        try {
            document.dispatchEvent(new Event(THEME_EVT));
        } catch (e) {
            /* IE / 老 X5 不支持 Event 构造 → 走下面的直接回调 */
        }
        for (var i = 0; i < _themeHandlers.length; i++) {
            try { _themeHandlers[i](); } catch (e) { /* 单个页面出错不影响其余 */ }
        }
    }

    try {
        if (window.MutationObserver) {
            new window.MutationObserver(function (muts) {
                for (var i = 0; i < muts.length; i++) {
                    var now = document.documentElement.getAttribute('data-bs-theme');
                    if (now !== _lastTheme) {
                        _lastTheme = now;
                        broadcastTheme();
                        return;
                    }
                }
            }).observe(document.documentElement,
                { attributes: true, attributeFilter: ['data-bs-theme'] });
        }
    } catch (e) { /* 不支持就只保证「刷新页面后生效」 */ }
})(window);

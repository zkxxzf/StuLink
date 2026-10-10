/* StuLink v1.19.0 2026-10-10
   浏览器本地 AI 配置（**私人 Key 只存本机，不上服务器**）
   全站共享：任何页面都能用 window.StuLinkAI.has()/ensure()/payload()
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';
    var AI_KEY = 'stulink.ai';

    function read() {
        try {
            var s = localStorage.getItem(AI_KEY);
            return s ? JSON.parse(s) : null;
        } catch (e) { return null; }
    }

    window.StuLinkAI = {
        /** 原始配置对象（含 api_key）；无则 null */
        get: read,
        /** 是否已可用（有 key 且至少能定位到接口） */
        has: function () {
            var c = read();
            return !!(c && c.api_key && (c.base_url || c.provider));
        },
        /** 未配置则提示并引导到用户设置；返回 true = 可继续 */
        ensure: function () {
            if (window.StuLinkAI.has()) return true;
            if (window.confirm('还没有配置 AI Key，无法使用 AI 功能。\n\n现在去「用户设置 → 我的 AI 配置」配置吗？')) {
                location.href = '/my/settings#ai';
            }
            return false;
        },
        /** 组装为后端约定的 ai 参数（随请求体一起发，服务器不落库） */
        payload: function () {
            var c = read() || {};
            var out = {
                provider: c.provider || '', base_url: c.base_url || '',
                model: c.model || '', api_key: c.api_key || ''
            };
            // v1.19.0 可选高级参数：不填就不传，由服务商自行决定
            //   max_input=输入上限  max_tokens=输出上限  reasoning_effort=思考强度
            ['max_input', 'max_tokens', 'reasoning_effort'].forEach(function (k) {
                var v = c[k];
                if (v !== undefined && v !== null && v !== '') { out[k] = v; }
            });
            return out;
        },
        /** 给带 AI 功能的按钮加提示：未配置时按钮变灰并提示去配置 */
        guard: function (selector) {
            var ok = window.StuLinkAI.has();
            var $els = window.jQuery ? window.jQuery(selector) : null;
            if ($els && $els.length) {
                $els.toggleClass('disabled', !ok)
                    .attr('title', ok ? '' : '未配置 AI Key：请到「用户设置 → 我的 AI 配置」填写');
            }
            return ok;
        }
    };

    // 右上角菜单里显示“已配置/未配置”
    document.addEventListener('DOMContentLoaded', function () {
        var el = document.getElementById('navAiState');
        if (!el) return;
        if (window.StuLinkAI.has()) {
            el.textContent = '已配置';
            el.className = 'badge bg-success-subtle text-success-emphasis ms-1';
        } else {
            el.textContent = '未配置';
            el.className = 'badge bg-warning-subtle text-warning-emphasis ms-1';
        }
    });
})();

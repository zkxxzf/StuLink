/* StuLink v1.19.0 2026-10-10
   用户设置页：AI 配置（仅存本机浏览器，不上服务器）+ 界面偏好
   同时把读取/检查能力挂到 window.StuLinkAI，供其它模块（AI 分析、智能导入）复用。
   Copyright (c) 2026 zkxxzf. Apache License 2.0 */
(function () {
    'use strict';

    var AI_KEY = 'stulink.ai';        // {provider, base_url, model, api_key}
    var PREF_KEY = 'stulink.pref';    // {size}

    function lsGet(k, dft) {
        try {
            var s = localStorage.getItem(k);
            return s ? JSON.parse(s) : dft;
        } catch (e) { return dft; }
    }
    function lsSet(k, v) {
        try { localStorage.setItem(k, JSON.stringify(v)); return true; }
        catch (e) { return false; }
    }
    function lsDel(k) { try { localStorage.removeItem(k); } catch (e) {} }

    /* ---------- 对外能力由全站共享脚本 ai_local.js 提供 ----------
       （window.StuLinkAI：get / has / ensure / payload / guard）
       本文件只负责「用户设置」页的表单交互，避免两份实现不一致。 */

    /* ---------- 页面初始化 ---------- */
    $(function () {
        var PROVIDERS = window.STULINK_PROVIDERS || [];
        var DFLT = window.STULINK_DEFAULT_PROVIDER || '';

        function provOf(key) {
            for (var i = 0; i < PROVIDERS.length; i++) {
                if (PROVIDERS[i].key === key) return PROVIDERS[i];
            }
            return null;
        }

        // 服务商下拉
        var $p = $('#aiProvider').empty();
        PROVIDERS.forEach(function (v) {
            $p.append($('<option>').val(v.key).text(v.name + (v.key === DFLT ? '（默认）' : '')));
        });
        if (!provOf('custom')) {
            $p.append($('<option>').val('custom').text('自定义（OpenAI 兼容）'));
        }

        function applyProvider(key, keepBase) {
            var v = provOf(key);
            if (!v) return;
            if (!keepBase) $('#aiBase').val(v.base_url || '');
            $('#aiModel').val(v.default_model || '');
            var $dl = $('#aiModelList').empty();
            (v.models || []).forEach(function (m) { $dl.append($('<option>').val(m)); });
            $('#aiKeyHint').text(v.key_hint ? ('格式示例：' + v.key_hint) : '');
        }

        // 载入本机已存配置
        var cfg = lsGet(AI_KEY, null) || {};
        $p.val(cfg.provider || DFLT);
        applyProvider(cfg.provider || DFLT, !!cfg.base_url);
        if (cfg.base_url) $('#aiBase').val(cfg.base_url);
        if (cfg.model) $('#aiModel').val(cfg.model);
        if (cfg.api_key) {
            // 回显时打码，避免肩窥；用户不改就不用重填
            $('#aiKey').val(cfg.api_key);
            $('#aiKeyHint').text('已保存到本机（' + cfg.api_key.slice(0, 3) + '****' +
                                 cfg.api_key.slice(-4) + '），修改后请重新保存');
        }
        var pref = lsGet(PREF_KEY, {});
        if (pref.size) $('#pfSize').val(String(pref.size));

        $p.on('change', function () { applyProvider($(this).val(), false); });

        $('#aiKeyToggle').on('click', function () {
            var $k = $('#aiKey');
            var show = $k.attr('type') === 'password';
            $k.attr('type', show ? 'text' : 'password');
            $(this).text(show ? '隐藏' : '显示');
        });

        function msg(ok, text) {
            $('#aiMsg').html($('<div>')
                .addClass('alert py-1 px-2 mb-0 ' + (ok ? 'alert-success' : 'alert-danger'))
                .text(text));
        }

        function formCfg() {
            return {
                provider: $('#aiProvider').val() || '',
                base_url: $.trim($('#aiBase').val() || ''),
                model: $.trim($('#aiModel').val() || ''),
                api_key: $.trim($('#aiKey').val() || '')
            };
        }

        $('#aiSave').on('click', function () {
            var c = formCfg();
            if (!c.api_key) { msg(false, '请填写 API Key'); return; }
            if (!c.base_url) { msg(false, '请填写接口地址（base_url）'); return; }
            if (!c.model) { msg(false, '请填写模型名'); return; }
            if (!lsSet(AI_KEY, c)) { msg(false, '本机存储不可用（可能浏览器禁用了 localStorage）'); return; }
            msg(true, '已保存到本机浏览器（服务器不留存）。可点「测试连接」验证是否可用。');
        });

        $('#aiTest').on('click', function () {
            var c = formCfg();
            if (!c.api_key) { msg(false, '请填写 API Key'); return; }
            var $b = $(this).prop('disabled', true).text('测试中…');
            $.ajax({
                url: '/my/ai/test', type: 'POST',
                contentType: 'application/json',
                headers: { 'X-CSRFToken': window.csrfToken || '' },
                data: JSON.stringify({ ai: c })
            }).done(function (res) {
                msg(!!res.success, res.message || (res.success ? '连接成功' : '连接失败'));
            }).fail(function (x) {
                msg(false, (x.responseJSON && x.responseJSON.message) || '请求失败');
            }).always(function () {
                $b.prop('disabled', false).html('<i class="bi bi-plug"></i> 测试连接');
            });
        });

        $('#aiClear').on('click', function () {
            if (!confirm('清除本机保存的 AI 配置？（服务器上没有存，纯本地数据）')) return;
            lsDel(AI_KEY);
            $('#aiKey').val('');
            applyProvider($p.val(), false);
            msg(true, '已清除本机的 AI 配置');
        });

        $('#pfSave').on('click', function () {
            var p = lsGet(PREF_KEY, {});
            p.size = parseInt($('#pfSize').val(), 10) || 50;
            lsSet(PREF_KEY, p);
            msg(true, '界面偏好已保存到本机');
        });

        $('#localClear').on('click', function () {
            if (!confirm('清除本机全部本地配置（AI Key、界面偏好）？\n不影响服务器上的任何数据。')) return;
            lsDel(AI_KEY); lsDel(PREF_KEY);
            location.reload();
        });

        if (location.hash === '#ai') { $('#aiProvider').focus(); }
    });
})();

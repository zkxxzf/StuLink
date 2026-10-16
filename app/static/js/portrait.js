/**
 * StuLink v1.15.0 2026-09-18
 * 学生画像模块公共JS
 * Copyright (c) 2026 zkxxzf. Apache License 2.0
 *
 * 注：详情页的图表和交互逻辑已内嵌在 detail.html 模板中，
 * 因为需要使用 Jinja2 模板变量。此文件保留用于未来扩展公共功能。
 */

/* 2026-10-14 审计修复：评级 / 维度色原为写死字面量（#198754…），深色下压在深面板上
   发闷。改为读 theme.css 的图表语义变量（--sl-chart-ok/info/warn/danger/cyan），
   并在主题切换时刷新。语义不变：A 绿 / B 蓝 / C 黄 / D 红。 */
function portraitCssVar(name, fallback) {
    try {
        return (window.stuCssVar && window.stuCssVar(name, '')) || fallback;
    } catch (e) { return fallback; }
}

// 画像评级配色方案（随主题刷新）
let PORTRAIT_LEVEL_COLORS = {};
// 维度配色（随主题刷新）
let DIMENSION_COLORS = {};

function refreshPortraitColors() {
    PORTRAIT_LEVEL_COLORS = {
        'A': portraitCssVar('--sl-chart-ok', '#198754'),      // success
        'B': portraitCssVar('--sl-chart-info', '#0d6efd'),    // primary
        'C': portraitCssVar('--sl-chart-warn', '#ffc107'),    // warning
        'D': portraitCssVar('--sl-chart-danger', '#dc3545')   // danger
    };
    DIMENSION_COLORS = {
        academic: portraitCssVar('--sl-chart-info', '#0d6efd'),
        behavior: portraitCssVar('--sl-chart-ok', '#198754'),
        dormitory: portraitCssVar('--sl-chart-cyan', '#0dcaf0'),
        attendance: portraitCssVar('--sl-chart-warn', '#ffc107')
    };
}
refreshPortraitColors();
if (window.stuOnThemeChange) { window.stuOnThemeChange(refreshPortraitColors); }

/**
 * 获取评级对应的Bootstrap颜色类
 */
function getLevelColorClass(level) {
    const map = {
        'A': 'success',
        'B': 'primary',
        'C': 'warning',
        'D': 'danger',
    };
    return map[level] || 'secondary';
}

/**
 * 格式化分数显示
 */
function formatScore(score, decimals = 0) {
    if (score === null || score === undefined) return '-';
    return Number(score).toFixed(decimals);
}

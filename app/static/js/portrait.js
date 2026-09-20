/**
 * StuLink v1.15.0 2026-09-18
 * 学生画像模块公共JS
 * Copyright (c) 2026 zkxxzf. Apache License 2.0
 *
 * 注：详情页的图表和交互逻辑已内嵌在 detail.html 模板中，
 * 因为需要使用 Jinja2 模板变量。此文件保留用于未来扩展公共功能。
 */

// 画像评级配色方案
const PORTRAIT_LEVEL_COLORS = {
    'A': '#198754',  // success
    'B': '#0d6efd',  // primary
    'C': '#ffc107',  // warning
    'D': '#dc3545',  // danger
};

// 维度配色
const DIMENSION_COLORS = {
    academic: '#0d6efd',
    behavior: '#198754',
    dormitory: '#0dcaf0',
    attendance: '#ffc107',
};

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

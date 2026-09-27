# StuLink v1.18.2.1 2026-09-24
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""学科配色（课表网格用）。

一个学科一种固定颜色，规则：
1. 常用学科走 PRESET（符合直觉：语文红、数学蓝、英语绿…）；
2. 其它学科（校本课程、社团等）用学科名做稳定散列取色 —— 同一学科在任何
   页面/学期/机器上颜色一致，不同学科尽量分散。

返回 {c: 主色, bg: 浅底, fg: 深字}：网格条目用浅底 + 左侧色条 + 深色标题，
既能在彩色屏幕上区分，也能在打印（黑白）时靠明度差辨认。

⚠ 前端 `app/static/js/schedule.js` 里有一份等价实现（AJAX 重渲染网格时用），
   修改调色板或散列算法时两处必须同步，否则服务端渲染与 AJAX 重渲染颜色会漂移。
"""

# 12 色（红/蓝/绿/橙/紫/青/玫红/橄榄/石板灰/金/蓝绿/亮紫）
TONES = [
    {'c': '#dc2626', 'bg': '#fee2e2', 'fg': '#991b1b'},
    {'c': '#2563eb', 'bg': '#dbeafe', 'fg': '#1e40af'},
    {'c': '#059669', 'bg': '#d1fae5', 'fg': '#065f46'},
    {'c': '#d97706', 'bg': '#fef3c7', 'fg': '#92400e'},
    {'c': '#7c3aed', 'bg': '#ede9fe', 'fg': '#5b21b6'},
    {'c': '#0891b2', 'bg': '#cffafe', 'fg': '#155e75'},
    {'c': '#db2777', 'bg': '#fce7f3', 'fg': '#9d174d'},
    {'c': '#65a30d', 'bg': '#ecfccb', 'fg': '#3f6212'},
    {'c': '#475569', 'bg': '#e2e8f0', 'fg': '#1e293b'},
    {'c': '#ca8a04', 'bg': '#fef9c3', 'fg': '#854d0e'},
    {'c': '#0d9488', 'bg': '#ccfbf1', 'fg': '#115e59'},
    {'c': '#9333ea', 'bg': '#f3e8ff', 'fg': '#6b21a8'},
]

# 常用学科固定色号（避免"数学变紫、语文变青"这类反直觉）
PRESET = {
    '语文': 0, '数学': 1, '英语': 2,
    '物理': 4, '化学': 5, '生物': 3,
    '政治': 6, '历史': 7, '地理': 10,
    '体育': 8, '音乐': 11, '美术': 11,
    '信息技术': 5, '通用技术': 5, '班会': 8, '自习': 8, '晚自习': 8,
}


def subject_tone(name):
    """取学科配色 dict（c/bg/fg）；空名/未知名回退中性灰。"""
    s = (name or '').strip()
    if not s:
        return TONES[8]
    if s in PRESET:
        return TONES[PRESET[s]]
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) % len(TONES)
    return TONES[h]


def subject_tone_map(names):
    """批量取色：{学科名: tone}，供页面一次性下发（可选）。"""
    return {n: subject_tone(n) for n in (names or []) if n}

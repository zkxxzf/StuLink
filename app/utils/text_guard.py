# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""短文本（标签类）输入清洗（H-5 服务端白名单 / L-12 长度上限的统一实现）。

背景（清单 H-5）：考务的「考场位置 / 备注 / 考号前缀」等字段服务端只做 `.strip()`
就入库，前端又用 innerHTML 拼接渲染 → 有 grades.edit 的人（或能导入名单者）
写入 `<img src=x onerror=...>` 即构成存储型 XSS。

口径：这一类「标签型短文本」根本不需要 HTML/脚本字符，因此服务端直接按字符
白名单剔除，配合前端 escHtml 形成两道防线。
"""
import re

# 允许：中英文、数字、空格与常用标点（不含 < > & " ' ` / \ = ; { } 等可用于构造标签/属性的字符）
_ALLOWED = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff\s\-_.·（）()、,，:：#+*]")


def sanitize_label(value, max_len=50):
    """清洗标签型短文本：截断长度 + 剔除危险字符"""
    text = (value or '').strip()[:max_len]
    return _ALLOWED.sub('', text).strip()


def safe_download_name(name, default='download', max_len=80):
    """L-9：清洗 Content-Disposition 文件名（DB 取值反射进响应头，存在 CRLF 头注入风险）。

    规则：截断长度；删除 CR/LF 与控制字符；去掉路径分隔符与引号；去空后用默认名。
    """
    import re as _re
    text = (name or '').strip()
    text = _re.sub(r'[\r\n\x00-\x1f"\'\\/]', '', text)
    text = text.replace('..', '')
    text = text[:max_len].strip()
    return text or default


def pad2(value):
    """编号补零到两位：'1' → '01'；非纯数字原样返回（'A1' → 'A1'）。

    2026-10-13 用户口径（考务编排结果的输出）：考场号 / 座号一律两位
    （一考场 = 01 考场、1 号座 = 01 座），与 scripts/migrate_room_no_pad.py
    的入库口径一致。页面与各类导出统一走它，不要再各写一份 zfill / '%02d'。
    """
    s = str(value if value is not None else '').strip()
    return s.zfill(2) if s.isdigit() else s


def clamp_text(value, max_len):
    """L-12：用户输入长度上限（功能性/存储风险）"""
    if value is None:
        return ''
    return str(value)[:max_len]


def sanitize_prefix(value, max_len=16):
    """考号前缀/后缀：只允许数字与字母（会参与考号拼接，字符集更严）"""
    text = (value or '').strip()[:max_len]
    return re.sub(r'[^0-9A-Za-z]', '', text)

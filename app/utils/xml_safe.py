# StuLink 2026-10-19
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""P2：安全 XML 解析入口。

禁止在业务代码里直接 `import xml.etree.ElementTree` / `lxml`：
它们默认允许 DTD/外部实体，解析不可信 XML 时存在 XXE 与实体膨胀风险。
需要 XML 时统一用本模块（基于 defusedxml）。
"""
from defusedxml import ElementTree as SafeET


def parse_string(data):
    """解析 XML 字符串/bytes，返回 Element；异常与标准库 ElementTree 一致。"""
    return SafeET.fromstring(data)


def parse_file(path):
    """解析 XML 文件，返回 ElementTree。"""
    return SafeET.parse(path)

# StuLink v1.18.0.0 2026-09-23
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""上传文件校验（H-8 / R-3 / L-8 的**唯一实现**）。

背景（清单 H-8）：历史实现里「上传类型」只在题型配置了 `file_types` 时才比对扩展名，
未配置即任意后缀可传；而 `/static/uploads` 直链又可被大小写绕过（Windows），
于是 `.html`/`.svg` 一类文件能构成**未登录即可触发**的存储型 XSS。

本模块提供统一口径：
  - 扩展名白名单（不在白名单一律拒绝）
  - 危险类型黑名单（即使管理员在题型里手填也拒绝）
  - magic 字节嗅探（扩展名与实际内容不符即拒绝）
所有上传入口（表单材料、积分导入、学生/教师导入、考务导入）统一调用，禁止各自内联
`endswith(('.xlsx','.xls'))`。
"""
import os

# 允许落盘的扩展名（学生材料场景：文档 / 表格 / 图片 / 压缩包 / 音视频 / 纯文本）
ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'txt', 'csv', 'md', 'log',
    'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tif', 'tiff', 'ico',
    'zip', 'rar', '7z',
    'mp3', 'wav', 'mp4', 'mov', 'm4a', 'avi',
}

# 危险类型：可被浏览器或服务器解释执行，优先级高于题型配置
DANGEROUS_EXTENSIONS = {
    'html', 'htm', 'xhtml', 'shtml', 'svg', 'xml', 'mhtml',
    'js', 'mjs', 'jsx', 'vbs', 'php', 'php3', 'php5', 'phtml',
    'jsp', 'jspx', 'asp', 'aspx', 'ashx',
    'exe', 'bat', 'cmd', 'com', 'scr', 'msi', 'dll', 'sys',
    'ps1', 'psm1', 'sh', 'bash', 'py', 'rb', 'pl',
    'jar', 'war', 'class', 'lnk', 'url', 'hta', 'iso', 'reg',
}

# magic 字节：扩展名 → 文件头签名（用于「内容与扩展名不符」检测）
MAGIC_SIGNATURES = {
    'pdf': (b'%PDF',),
    'png': (b'\x89PNG\r\n\x1a\n',),
    'jpg': (b'\xff\xd8\xff',),
    'jpeg': (b'\xff\xd8\xff',),
    'gif': (b'GIF87a', b'GIF89a'),
    'bmp': (b'BM',),
    # Office 2007+（docx/xlsx/pptx）与 zip 同为 PK 容器
    'docx': (b'PK\x03\x04',),
    'xlsx': (b'PK\x03\x04',),
    'pptx': (b'PK\x03\x04',),
    'zip': (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'),
    # Office 97-2003（doc/xls/ppt）为 OLE2 复合文档
    'doc': (b'\xd0\xcf\x11\xe0',),
    'xls': (b'\xd0\xcf\x11\xe0',),
    'ppt': (b'\xd0\xcf\x11\xe0',),
    'rar': (b'Rar!\x1a\x07', b'Rar!'),
    '7z': (b'7z\xbc\xaf',),
}


def ext_of(filename):
    """取小写扩展名（不含点）；无扩展名返回空串"""
    name = (filename or '').rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    if '.' not in name:
        return ''
    return name.rsplit('.', 1)[-1].lower()


def read_head(stream, size=16):
    """读取文件头若干字节用于 magic 校验，读完后复位流位置（不影响后续保存）"""
    if stream is None:
        return b''
    try:
        pos = stream.tell()
        head = stream.read(size) or b''
        stream.seek(pos)
        return head
    except Exception:  # noqa: BLE001  某些流不可 seek，退化为不校验 magic
        return b''


def validate_upload(filename, allowed_exts=None, stream=None, head=None):
    """校验一次上传。

    :param filename: 原始文件名（会再经 secure_filename 处理，这里只看扩展名）
    :param allowed_exts: 可选的题型级白名单（题目配置的 file_types），为 None 时用全局白名单
    :param stream: 文件流（用于 magic 嗅探，可为空）
    :param head: 已读取的文件头字节，为空时从 stream 读取
    :return: (True, '') 或 (False, 用户可见的中文错误提示)
    """
    ext = ext_of(filename)
    if not ext:
        return False, '文件缺少扩展名，无法校验类型'
    if ext in DANGEROUS_EXTENSIONS:
        return False, f'不允许上传 .{ext} 文件（存在脚本/可执行文件风险）'

    allowed = {e.strip().lower().lstrip('.')
               for e in (allowed_exts or [])} or ALLOWED_EXTENSIONS
    # 题型配置的白名单同样不得包含危险类型
    allowed -= DANGEROUS_EXTENSIONS
    if ext not in allowed:
        return False, f'不允许的文件类型：.{ext}'

    _head = head if head is not None else read_head(stream)
    if _head:
        sigs = MAGIC_SIGNATURES.get(ext)
        if sigs and not _head.startswith(sigs):
            return False, f'文件内容与 .{ext} 扩展名不符，已拒绝'
    return True, ''


def is_static_uploads_path(path):
    """H-8：/static/uploads 直链判定（Windows 大小写不敏感 + 反斜杠/多斜杠归一）"""
    if not path:
        return False
    normalized = os.path.normcase(str(path)).replace('\\', '/')
    while '//' in normalized:
        normalized = normalized.replace('//', '/')
    return normalized.startswith('/static/uploads')

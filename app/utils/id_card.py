# StuLink v1.9.3 2026-09-19
# 身份证号工具：格式与校验码验证、脱敏展示、确定性加密存取
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""身份证号工具

- 校验：18 位格式 + GB 11643-1999 校验码
- 存储：crypto.encrypt 确定性加密（同一号码密文相同，支持唯一性等值查询）
- 展示：脱敏（前 3 后 4）
"""
from app.utils.crypto import encrypt, decrypt

# 校验码权重与映射（GB 11643-1999）
_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHECK_CODES = '10X98765432'


def normalize(value):
    """去除空格并大写 X；空值返回空串"""
    return str(value or '').strip().replace(' ', '').upper()


def validate(value):
    """18 位身份证号格式 + 校验码验证"""
    s = normalize(value)
    if len(s) != 18:
        return False
    if not s[:17].isdigit() or not (s[17].isdigit() or s[17] == 'X'):
        return False
    if s[:2] not in ('11', '12', '13', '14', '15', '21', '22', '23', '31',
                     '32', '33', '34', '35', '36', '37', '41', '42', '43',
                     '44', '45', '46', '50', '51', '52', '53', '54', '61',
                     '62', '63', '64', '65', '71', '81', '82', '91'):
        return False
    total = sum(int(s[i]) * _WEIGHTS[i] for i in range(17))
    return _CHECK_CODES[total % 11] == s[17]


def mask(value):
    """脱敏展示：前 3 后 4（如 110***********1234）；空值返回空串"""
    s = normalize(value)
    if len(s) != 18:
        return s
    return s[:3] + '*' * 11 + s[-4:]


def encrypt_id_card(value):
    """身份证号 → 密文（空值返回 None）"""
    s = normalize(value)
    return encrypt(s) if s else None


def decrypt_id_card(cipher):
    """密文 → 身份证号；解密失败返回空串（旧明文数据兼容）"""
    if not cipher:
        return ''
    return normalize(decrypt(cipher))


def masked_from_cipher(cipher):
    """由密文直接得到脱敏展示串"""
    return mask(decrypt_id_card(cipher))

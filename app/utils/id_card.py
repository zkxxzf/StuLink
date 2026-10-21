# StuLink v1.19.54.64 2026-10-10
# 身份证号工具：格式与校验码验证、脱敏展示、确定性加密存取
# Copyright (c) 2026 zkxxzf. Apache License 2.0
"""身份证号工具

- 校验：18 位格式 + GB 11643-1999 校验码
- 存储（2026-10-19 H-06 起）：crypto.encrypt_rand 随机 IV；
  唯一性/等值查询走 crypto.id_card_index 盲索引（HMAC，不可逆）。
  legacy_encrypt_id_card 仅用于与存量确定性密文双读兼容。
- 展示：脱敏（前 3 后 4）
"""
from app.utils.crypto import (decrypt, encrypt, encrypt_rand,
                              id_card_index as _id_card_index,
                              normalize_id_card)

# 校验码权重与映射（GB 11643-1999）
_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHECK_CODES = '10X98765432'


def normalize(value):
    """去除空白/分隔符并大写 X；空值返回空串"""
    return normalize_id_card(value) or ''


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
    """身份证号 → 随机 IV 密文（空值返回 None）。新数据一律走这里。"""
    s = normalize(value)
    return encrypt_rand(s) if s else None


def legacy_encrypt_id_card(value):
    """存量兼容：确定性 IV 密文（只用于与旧数据等值比对，禁止写新数据）。"""
    s = normalize(value)
    return encrypt(s) if s else None


def id_card_index(value):
    """身份证盲索引（随机 IV 加密后的精确查询/去重）。"""
    return _id_card_index(value)


def decrypt_id_card(cipher):
    """密文 → 身份证号；解密失败返回空串（旧明文数据兼容）。

    M-5：crypto.decrypt 现在对"无法解密"抛 DecryptError（不再把密文当明文返回），
    展示侧降级为空串；真正的失败原因由 crypto 侧日志留痕。
    """
    if not cipher:
        return ''
    try:
        return normalize(decrypt(cipher))
    except Exception:
        return ''


def masked_from_cipher(cipher):
    """由密文直接得到脱敏展示串"""
    return mask(decrypt_id_card(cipher))

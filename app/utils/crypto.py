"""AES-256-CBC 对称加密工具 — 用于加密身份证号等敏感字段

使用确定性 IV：同一明文 → 同一密文 → 支持数据库等值查询
密钥来源：环境变量 ENCRYPTION_KEY，否则从 data/.encryption_key 自动生成
"""
# StuLink v1.7.0 2026-08-02
# Copyright (c) 2026 zkxxzf. Apache License 2.0
import logging
import os
import hashlib
import base64
import secrets
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from config import BASE_DIR


# 2026-10-10：logger 必须先于 _get_encryption_key() 定义 ——
# 该函数在模块加载时即被调用（下方 _ENCRYPTION_KEY 初始化），若 logger 在其后定义，
# 密钥读写失败时记日志会触发 NameError，反而把「历史密文无法解密」的高危分支掩盖掉。
_log = logging.getLogger('stulink.crypto')


def _get_encryption_key():
    """获取加密密钥：环境变量 > 持久化文件 > 自动生成"""
    env_key = os.environ.get('ENCRYPTION_KEY', '').strip()
    if env_key:
        return hashlib.sha256(env_key.encode()).digest()
    key_file = os.path.join(BASE_DIR, 'data', '.encryption_key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                return f.read()
    except Exception as _e:  # noqa: BLE001
        # 2026-10-10：绝不能在日志里打印密钥内容。此处失败将落到下方"生成新密钥"，
        # 而新密钥会让**全部历史密文永久无法解密**，属高危分支，必须留痕告警。
        _log.error('读取加密密钥文件失败(%s)：将生成新密钥，历史加密数据可能永久无法解密: %s',
                   key_file, _e)
    new_key = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    try:
        with open(key_file, 'wb') as f:
            f.write(new_key)
    except Exception as _e:  # noqa: BLE001
        _log.error('写入加密密钥文件失败(%s)：进程重启后密钥将变化，历史密文可能无法解密: %s',
                   key_file, _e)
    return new_key


# 全局密钥（模块加载时初始化）
_ENCRYPTION_KEY = _get_encryption_key()


class DecryptError(ValueError):
    """M-5：密文无法解密（密钥不匹配 / 数据损坏）。

    旧实现「解密失败静默返回密文原文」会把密文当明文流向页面与导出，
    语义上等同于泄露；现在改为抛错，由调用方决定降级展示。
    """


def encrypt(plaintext):
    """加密明文，返回 base64 字符串；空值返回 None（确定性 IV，支持等值查询）"""
    if not plaintext:
        return None
    plaintext = str(plaintext).strip()
    if not plaintext:
        return None
    # 确定性 IV = SHA256(plaintext)[:16]
    iv = hashlib.sha256(plaintext.encode()).digest()[:16]
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(_ENCRYPTION_KEY), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode()


def encrypt_rand(plaintext):
    """随机 IV 加密（API Key 等无需等值查询的场景；返回 base64：iv+密文）"""
    if not plaintext:
        return None
    plaintext = str(plaintext).strip()
    if not plaintext:
        return None
    iv = secrets.token_bytes(16)
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(_ENCRYPTION_KEY), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode()


def _get_index_key():
    """盲索引密钥：由主密钥派生（主密钥 || 用途串 再哈希）。

    与主加密密钥**隔离**：即便盲索引被离线撞库，也推不出加密密钥。
    """
    return hashlib.sha256(_ENCRYPTION_KEY + b'|blind-index-v1').digest()


def normalize_phone(value):
    """手机号规范化：只保留数字，去掉 +86 / 横线 / 空格。

    避免"138-0000-0000"与"13800000000"算出不同的索引而互相查不到。
    """
    if not value:
        return None
    digits = ''.join(ch for ch in str(value) if ch.isdigit())
    if digits.startswith('86') and len(digits) == 13:
        digits = digits[2:]
    return digits or None


def blind_index(value):
    """敏感字段盲索引：HMAC-SHA256(规范化值)，用于**加密后**的精确等值查询。

    只支持"完全等于"，不支持模糊/前缀（模糊请用 decrypt 后内存过滤）。
    相同明文 → 相同索引；索引本身不可逆，无法还原号码。
    """
    import hmac as _hmac
    norm = normalize_phone(value)
    if not norm:
        return None
    return _hmac.new(_get_index_key(), norm.encode(), hashlib.sha256).hexdigest()


def decrypt(ciphertext_b64):
    """解密密文（base64 字符串，兼容 随机IV/确定性IV 两种密文），返回明文；空值/明文直接返回"""
    if not ciphertext_b64:
        return None
    ciphertext_b64 = str(ciphertext_b64).strip()
    if not ciphertext_b64:
        return None
    # 兼容旧数据：看起来像身份证号的明文直接返回
    if len(ciphertext_b64) == 18 and (ciphertext_b64[:-1].isdigit() or ciphertext_b64[-1].upper() == 'X'):
        return ciphertext_b64
    # 2026-10-18：兼容**迁移前**的明文手机号（11 位纯数字），
    # 否则存量数据在跑迁移脚本之前会全部解密失败变成空值。
    if len(ciphertext_b64) == 11 and ciphertext_b64.isdigit():
        return ciphertext_b64
    try:
        raw = base64.b64decode(ciphertext_b64)
        if len(raw) < 16:
            return ciphertext_b64  # 不是有效密文，当作明文返回
        iv = raw[:16]
        ciphertext = raw[16:]
        cipher = Cipher(algorithms.AES(_ENCRYPTION_KEY), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        return plaintext.decode()
    except Exception as e:
        # M-5：解密失败不再"静默当明文返回"（此前会把密文直接流进页面/导出）。
        # 记录脱敏日志后抛错，由调用方降级为掩码展示。
        _log.error('[M-5] 解密失败（密钥不匹配或数据损坏），已拒绝返回密文原文：%s',
                   type(e).__name__)
        raise DecryptError('数据解密失败（密文损坏或密钥不匹配），已拒绝返回原文')



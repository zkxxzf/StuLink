"""AES-256-CBC 对称加密工具 — 用于加密身份证号等敏感字段

使用确定性 IV：同一明文 → 同一密文 → 支持数据库等值查询
密钥来源：环境变量 ENCRYPTION_KEY，否则从 data/.encryption_key 自动生成
"""
# StuLink v1.5.0 2026-07-01
# Copyright (c) 2026 zkxxzf. CC BY-NC 4.0
import os
import hashlib
import base64
import secrets
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from config import BASE_DIR


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
    except Exception:
        pass
    new_key = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(key_file), exist_ok=True)
    try:
        with open(key_file, 'wb') as f:
            f.write(new_key)
    except Exception:
        pass
    return new_key


# 全局密钥（模块加载时初始化）
_ENCRYPTION_KEY = _get_encryption_key()


def encrypt(plaintext):
    """加密明文，返回 base64 字符串；空值返回 None"""
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


def decrypt(ciphertext_b64):
    """解密密文（base64 字符串），返回明文；空值/明文直接返回"""
    if not ciphertext_b64:
        return None
    ciphertext_b64 = str(ciphertext_b64).strip()
    if not ciphertext_b64:
        return None
    # 兼容旧数据：看起来像身份证号的明文直接返回
    if len(ciphertext_b64) == 18 and (ciphertext_b64[:-1].isdigit() or ciphertext_b64[-1].upper() == 'X'):
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
    except Exception:
        # 解密失败，可能是旧数据明文，直接返回
        return ciphertext_b64

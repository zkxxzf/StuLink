"""AES-256-CBC 对称加密工具 — 用于解密身份证号等敏感字段

使用确定性 IV：同一明文 → 同一密文 → 支持数据库等值查询
密钥来源：环境变量 ENCRYPTION_KEY，否则从 data/.encryption_key 读取
"""
import os
import hashlib
import base64
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _get_encryption_key():
    """获取加密密钥：环境变量 > 持久化文件"""
    env_key = os.environ.get('ENCRYPTION_KEY', '').strip()
    if env_key:
        return hashlib.sha256(env_key.encode()).digest()
    # 尝试多个可能的密钥文件位置（兼容开发环境和 Docker 环境）
    possible_paths = [
        os.path.join(BASE_DIR, 'data', '.encryption_key'),  # 开发环境
        '/app/data/.encryption_key',  # Docker 环境
    ]
    for key_file in possible_paths:
        try:
            if os.path.exists(key_file):
                with open(key_file, 'rb') as f:
                    return f.read()
        except Exception:
            pass
    raise RuntimeError('加密密钥不存在，请设置 ENCRYPTION_KEY 环境变量或确保 data/.encryption_key 文件存在')


_ENCRYPTION_KEY = _get_encryption_key()


def decrypt(ciphertext_b64):
    """解密密文（base64 字符串），返回明文；空值/明文直接返回"""
    if not ciphertext_b64:
        return None
    ciphertext_b64 = str(ciphertext_b64).strip()
    if not ciphertext_b64:
        return None
    if len(ciphertext_b64) == 18 and (ciphertext_b64[:-1].isdigit() or ciphertext_b64[-1].upper() == 'X'):
        return ciphertext_b64
    try:
        raw = base64.b64decode(ciphertext_b64)
        if len(raw) < 16:
            return ciphertext_b64
        iv = raw[:16]
        ciphertext = raw[16:]
        cipher = Cipher(algorithms.AES(_ENCRYPTION_KEY), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        return plaintext.decode()
    except Exception:
        return ciphertext_b64


def mask_id_card(id_card):
    """身份证号脱敏：显示前6位和后4位，中间用*替换"""
    if not id_card:
        return ''
    id_card = str(id_card).strip()
    if len(id_card) != 18:
        return id_card
    return id_card[:6] + '**********' + id_card[-4:]

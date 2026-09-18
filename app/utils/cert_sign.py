# StuLink v1.9.2 2026-09-16
# 成绩证明防伪码加密工具：随机码 + 密钥签名（HMAC-SHA256）
#   防伪码结构：SL + YYYYMMDD(8) + 随机码(8) + 签名段(16) = 38 位
#   签名段   = HMAC-SHA256(密钥, "学号|随机码|SHA256(成绩快照)") 的前 16 位 hex
#   密钥来源：grades 库 cert_signing_keys 表（随机生成、AES 加密存储，不落配置文件）
# 效果：仅知道码格式无法伪造；改快照或换学生都会导致签名校验失败。
import hashlib
import hmac
import secrets

from app.utils.crypto import encrypt_rand, decrypt

_KEY_LEN = 32          # 密钥 32 字节 → 64 位 hex
NONCE_LEN = 8          # 随机码长度（大写 base36 字符）
SIG_LEN = 16           # 防伪码中保留的签名段长度

# v1.12.5 快照加密：content_json 落库前整体 AES 加密，加此前缀区分密文/明文（旧数据）
_ENC_PREFIX = 'enc::'


def store_content(plaintext_json):
    """把明文快照 JSON 加密为落库字符串（带 enc:: 前缀，空值原样返回）。"""
    if not plaintext_json:
        return plaintext_json
    return _ENC_PREFIX + encrypt_rand(plaintext_json)


def load_content(stored):
    """把落库字符串还原为明文快照 JSON。
    - 带 enc:: 前缀 → 解密（新数据）
    - 无前缀 → 原样返回（v1.12.5 之前的明文旧数据，向后兼容）
    """
    if not stored:
        return stored
    if stored.startswith(_ENC_PREFIX):
        return decrypt(stored[len(_ENC_PREFIX):])
    return stored


def get_signing_key():
    """取签名密钥（bytes）：库中无则自动生成并落库（并发下唯一约束兜底）。"""
    from app.extensions import db
    from app.models.grades import CertSigningKey
    row = CertSigningKey.query.get(1)
    if row and row.key_enc:
        return bytes.fromhex(decrypt(row.key_enc))
    key_hex = secrets.token_hex(_KEY_LEN)
    try:
        db.session.add(CertSigningKey(id=1, key_enc=encrypt_rand(key_hex)))
        db.session.commit()
        return bytes.fromhex(key_hex)
    except Exception:
        db.session.rollback()   # 并发插入冲突：改用已存在的行
        row = CertSigningKey.query.get(1)
        if not (row and row.key_enc):
            # 回滚后仍取不到密钥行（非唯一约束类失败，如连接异常/插入未提交）：
            # 必须显式报错——若静默返回上面未落库的 key_hex，签出的防伪码在后续
            # 核验时会读到库中另一份密钥，被误判为「伪造/篡改」。
            raise RuntimeError('成绩证明签名密钥初始化失败，请检查数据库后重试')
        return bytes.fromhex(decrypt(row.key_enc))


def _content_hash(content_json):
    """成绩快照内容的 SHA256（签名绑定内容，防篡改快照）"""
    return hashlib.sha256((content_json or '').encode('utf-8')).hexdigest()


def sign(student_no, nonce, content_json, key=None):
    """计算签名段（hex 大写，前 SIG_LEN 位）"""
    if key is None:
        key = get_signing_key()
    msg = f'{student_no}|{nonce}|{_content_hash(content_json)}'.encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:SIG_LEN].upper()


def _rand_nonce():
    """随机码：secrets 取自 CSPRNG，字符集为大写字母+数字（与防伪码风格一致）"""
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # 去掉易混的 I/O/0/1
    return ''.join(secrets.choice(alphabet) for _ in range(NONCE_LEN))


def make_code(student_no, content_json):
    """生成加密防伪码，返回 (code, nonce, sig) 三元组，nonce/sig 需随记录落库。"""
    key = get_signing_key()
    from datetime import datetime
    date = datetime.now().strftime('%Y%m%d')
    nonce = _rand_nonce()
    sig = sign(student_no, nonce, content_json, key)
    return f'SL{date}{nonce}{sig}', nonce, sig


def verify(cert, content_json=None):
    """校验记录签名是否可信：
    - 旧数据（无 nonce/sig）视为通过（向后兼容，核验页提示为早期版本码）
    - 快照被改、学号/随机码/签名任一不符 → False
    签名绑定的是明文快照，故先解密落库内容再验签（v1.12.5 快照加密）。
    """
    if not getattr(cert, 'nonce', None) or not getattr(cert, 'sig', None):
        return True
    content = cert.content_json if content_json is None else content_json
    content = load_content(content)
    return hmac.compare_digest(sign(cert.student_no, cert.nonce, content), cert.sig)


def is_legacy(cert):
    """是否为加密方案上线前生成的旧防伪码（无签名段）"""
    return not getattr(cert, 'nonce', None) or not getattr(cert, 'sig', None)

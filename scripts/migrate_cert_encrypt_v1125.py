"""成绩证明快照加密迁移（v1.12.5）

背景：
- 此前 certificates.content_json 以明文 JSON 落库，含 cert_header.id_card（完整身份证号）
  等敏感字段，存在数据保护风险。
- 新逻辑：落库前整体 AES 加密（store_content，带 enc:: 前缀），读取时解密（load_content）。
- 防伪码签名绑定的是「明文快照」，加密只改存储形态、不改明文内容，
  因此存量记录加密后 code / nonce / sig 均不变，旧防伪码继续可验真。

本脚本：
- 把所有「未加密」（非空且不以 enc:: 开头）的 content_json 加密回写。
- 幂等：已带 enc:: 前缀的行跳过，可重复执行。
- 执行前自动备份 grades.db（同目录 .bak-<时间戳>）。

用法：
    python scripts/migrate_cert_encrypt_v1125.py
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)   # 脚本直跑时引导导入 app 包（复用运行期加密实现）
DB = os.path.join(BASE_DIR, 'data', 'grades.db')
ENC_PREFIX = 'enc::'   # 与 app.utils.cert_sign._ENC_PREFIX 保持一致


def backup(path):
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = f'{path}.bak-{stamp}'
    shutil.copy2(path, dst)
    return dst


def main():
    if not os.path.exists(DB):
        print('grades.db 不存在，跳过')
        return
    print('备份 ->', os.path.basename(backup(DB)))

    # 复用应用内加密工具，保证与运行期 store_content 完全一致（同一密钥、同一前缀）
    from app.utils.cert_sign import store_content

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id, content_json FROM certificates "
        "WHERE content_json IS NOT NULL AND content_json != '' "
        "AND substr(content_json, 1, 5) != ?", (ENC_PREFIX,)
    ).fetchall()

    done = 0
    for cid, plain in rows:
        conn.execute(
            "UPDATE certificates SET content_json = ? WHERE id = ?",
            (store_content(plain), cid))
        done += 1
    conn.commit()
    conn.close()
    print(f'已加密 {done} 条成绩证明快照（签名不变，旧防伪码仍可验真）')
    print('完成。请重启应用。')


if __name__ == '__main__':
    main()

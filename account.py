"""账号体系：注册 / 登录 / 密码哈希 / token。纯标准库，无第三方依赖。

- 密码用 PBKDF2-HMAC-SHA256 加盐哈希，绝不明文存储、绝不用弱哈希。
- 登录态靠随机 token（secrets.token_hex），服务端只存哈希与 token。
- 校验用 hmac.compare_digest 防时序侧信道。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

PBKDF2_ROUNDS = 100_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (pwhash, salt)。salt 缺省随机生成；可外部传入以便测试。"""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS
    )
    return dk.hex(), salt


def verify_password(password: str, salt: str, pwhash: str) -> bool:
    test, _ = hash_password(password, salt)
    return hmac.compare_digest(test, pwhash)


def new_token() -> str:
    return secrets.token_hex(24)

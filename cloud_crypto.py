"""Password-protected Actions state and browser-compatible QR envelopes."""
import base64
import hashlib
import json
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


ITERATIONS = 600000
MAX_PAYLOAD = 4 * 1024 * 1024


def password():
    value = os.getenv("WXREAD_QR_PASSWORD", "")
    if len(value) < 20 or value != value.strip() or "\n" in value or "\r" in value:
        raise RuntimeError("请先在 AutoRead 环境中设置至少 20 位随机口令 Secret：WXREAD_QR_PASSWORD。")
    return value


def repository_scope():
    value = os.getenv("GITHUB_REPOSITORY") or os.getenv("WXREAD_REPOSITORY") or "local/wxread"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise RuntimeError("仓库标识格式无效。")
    return value.lower()


def cache_prefix():
    """Cache identity never contains password material.

    The v2 prefix hashed the password into the key itself, which turned every
    cache entry into a one-round offline oracle for the AutoRead password. The
    key is now derived from the repository scope only; the password still
    protects the payload through the AESGCM KDF.
    """
    identity = hashlib.sha256(b"wxread-cloud-cache-v3\0" + repository_scope().encode()).hexdigest()
    return "wxread-login-v3-" + identity


def _aad(purpose, scope):
    return json.dumps({"v": 1, "purpose": purpose, "scope": scope}, separators=(",", ":")).encode()


def _key(value, salt):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(value.encode())


def encrypt(data, purpose, value=None):
    value = password() if value is None else value
    scope = repository_scope()
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_key(value, salt)).encrypt(nonce, data, _aad(purpose, scope))
    envelope = {"v": 1, "purpose": purpose, "scope": scope, "iterations": ITERATIONS,
                "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
                "data": base64.b64encode(ciphertext).decode()}
    return json.dumps(envelope, separators=(",", ":")).encode()


def decrypt(data, purpose, value=None):
    try:
        if len(data) > MAX_PAYLOAD:
            raise ValueError("oversized envelope")
        envelope = json.loads(data)
        scope = repository_scope()
        if (envelope["v"] != 1 or envelope["purpose"] != purpose or envelope["scope"] != scope
                or envelope["iterations"] != ITERATIONS):
            raise ValueError("envelope identity mismatch")
        salt, nonce, ciphertext = (base64.b64decode(envelope[name], validate=True) for name in ("salt", "nonce", "data"))
        if len(salt) != 16 or len(nonce) != 12 or len(ciphertext) < 16:
            raise ValueError("invalid envelope lengths")
        value = password() if value is None else value
        return AESGCM(_key(value, salt)).decrypt(nonce, ciphertext, _aad(purpose, scope))
    except (ValueError, KeyError, TypeError, InvalidTag) as exc:
        raise RuntimeError("无法解密云端状态：请检查口令、仓库与文件是否匹配，勿删除仍可用的登录配置。") from exc

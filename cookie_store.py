"""Encrypted, atomic CookieJar persistence for local runs and Actions caches."""

import base64
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from requests.cookies import RequestsCookieJar, create_cookie


class CookieStore:
    def __init__(self, initial_cookies, path=None):
        # The captured cURL is a secret containing the original login credentials.
        # Separate labels keep the public cache identifier independent of the key.
        seed = json.dumps(initial_cookies, sort_keys=True, separators=(",", ":")).encode()
        self.cache_id = hashlib.sha256(b"wxread-cache-v1\0" + seed).hexdigest()
        key = hashlib.sha256(b"wxread-encryption-v1\0" + seed).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))
        self.path = Path(path or os.getenv("WXREAD_COOKIE_FILE", ".state/cookies.enc"))

    def load(self):
        try:
            payload = json.loads(self.cipher.decrypt(self.path.read_bytes()))
            if payload["version"] != 1 or payload["seed"] != self.cache_id:
                raise ValueError("Cookie state version or source changed")
            jar = RequestsCookieJar()
            for record in payload["cookies"]:
                if (
                    not isinstance(record["name"], str)
                    or not isinstance(record["value"], str)
                    or record["domain"].lstrip(".") != "weread.qq.com"
                    or not record["path"].startswith("/")
                ):
                    raise ValueError("Invalid Cookie state")
                jar.set_cookie(create_cookie(**record))
            jar.clear_expired_cookies()
            logging.info("已恢复上次运行保存的 Cookie。")
            return jar
        except FileNotFoundError:
            return None
        except (OSError, InvalidToken, ValueError, KeyError, TypeError, AttributeError):
            # Never include cached contents or decryption exceptions in logs.
            logging.warning("Cookie 缓存不可用或初始配置已变更，使用当前配置重新初始化。")
            return None

    def save(self, jar):
        records = []
        for cookie in jar:
            if cookie.domain.lstrip(".") != "weread.qq.com" or cookie.is_expired():
                continue
            records.append({
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
                "discard": cookie.discard,
                "rest": cookie._rest,
            })
        payload = json.dumps({"version": 1, "seed": self.cache_id, "cookies": records}).encode()
        encrypted = self.cipher.encrypt(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    # Used before cache restore; prints only a one-way cache namespace.
    from config import cookies

    if not cookies.get("wr_vid") or not cookies.get("wr_skey"):
        raise SystemExit("缺少 wr_vid / wr_skey，请扫码并同步 WXREAD_LOGIN_JSON，或更新 WXREAD_CURL_BASH。")
    cache_id = CookieStore(cookies).cache_id
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"cache_id={cache_id}\n")
    else:
        print(cache_id)

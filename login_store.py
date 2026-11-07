"""Local encrypted QR credentials and the equivalent GitHub Secret bootstrap."""
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


ROOT = Path(__file__).resolve().parent
SECRET_NAME = "WXREAD_LOGIN_JSON"


def login_path():
    return Path(os.getenv("WXREAD_LOGIN_FILE") or ROOT / ".state" / "login.enc")


def atomic_private_write(path, data):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("登录状态路径不能是符号链接。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_login(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise RuntimeError("登录数据格式或版本无效，请重新扫码登录。")
    cookies = value.get("cookies")
    if not isinstance(cookies, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in cookies.items()):
        raise RuntimeError("登录 Cookie 格式无效。")
    if not cookies.get("wr_vid") or not cookies.get("wr_skey"):
        raise RuntimeError("登录数据缺少必要凭据，请重新扫码登录。")
    headers = value.get("headers", {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
        raise RuntimeError("登录请求头格式无效。")
    headers = {k.lower(): v for k, v in headers.items()
               if k.lower() in {"user-agent", "accept", "accept-language", "referer", "origin"}}
    # Read legacy login files/secrets unchanged; no feature retrieves or uses this metadata now.
    api_key = value.get("api_key", "")
    if not isinstance(api_key, str):
        raise RuntimeError("官方 API Key 格式无效。")
    return {"version": 1, "cookies": dict(cookies), "headers": headers, "api_key": api_key}


def load_login():
    bootstrap = os.getenv(SECRET_NAME)
    if bootstrap:
        try:
            return validate_login(json.loads(bootstrap))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("WXREAD_LOGIN_JSON 无效，请重新扫码并同步登录 Secret。") from exc
    path = login_path()
    if not path.exists():
        return None
    key_path = path.with_suffix(".key")
    try:
        if path.is_symlink() or key_path.is_symlink():
            raise RuntimeError("登录状态文件不能是符号链接。")
        data = Fernet(key_path.read_bytes()).decrypt(path.read_bytes())
        return validate_login(json.loads(data))
    except (OSError, ValueError, TypeError, InvalidToken) as exc:
        raise RuntimeError("无法解密本地登录状态，请重新运行 python qr_login.py。") from exc


def save_login(value):
    value = validate_login(value)
    path = login_path()
    key_path = path.with_suffix(".key")
    if key_path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("登录状态路径不能是符号链接。")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        try:
            descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
    try:
        cipher = Fernet(key_path.read_bytes())
        atomic_private_write(path, cipher.encrypt(json.dumps(value, ensure_ascii=False).encode()))
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法保存本地登录状态，请检查目录权限或登录密钥文件。") from exc
    return path


def current_login():
    """Use already-rotated cookies when exporting a previously used login."""
    value = load_login()
    if value is None:
        return None
    from cookie_store import CookieStore
    cached = CookieStore(value["cookies"]).load()
    if cached is not None:
        value["cookies"] = {cookie.name: cookie.value for cookie in cached}
    return validate_login(value)

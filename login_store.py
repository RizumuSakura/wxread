"""Local encrypted QR credentials and the equivalent GitHub Secret bootstrap."""
import json
import math
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
    # Keep existing login files compatible; the shelf client may reuse an existing key.
    api_key = value.get("api_key", "")
    if not isinstance(api_key, str):
        raise RuntimeError("官方 API Key 格式无效。")
    cursor = value.get("reading_cursor", "")
    if not isinstance(cursor, str) or len(cursor) > 128:
        raise RuntimeError("书架阅读游标格式无效。")
    pending = value.get("pending_progress")
    if pending is not None:
        from book_context import reader_url, saved_position
        from reader_protocol import encode_id
        if (not isinstance(pending, dict) or pending.get("version") != 1
                or pending.get("owner") != cookies["wr_vid"]
                or not isinstance(pending.get("book_id"), str)
                or reader_url(pending.get("reference", "")).rsplit("/", 1)[-1].split("k", 1)[0] != encode_id(pending["book_id"])
                or not isinstance(pending.get("app_id"), str)
                or isinstance(pending.get("last_write"), bool)
                or not isinstance(pending.get("last_write"), (int, float))
                or (isinstance(pending["last_write"], float) and not math.isfinite(pending["last_write"]))
                or pending["last_write"] < 0):
            raise RuntimeError("进度恢复记录无效，停止以免覆盖阅读进度。")
        saved_position(pending.get("position"))
        if pending.get("restore_from") is not None:
            saved_position(pending["restore_from"])
    return {"version": 1, "cookies": dict(cookies), "headers": headers, "api_key": api_key,
            "reading_cursor": cursor, "pending_progress": pending}


def load_login():
    path = login_path()
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("登录状态路径不能是符号链接。")
    # A confirmed cloud login is newer than the optional legacy bootstrap Secret.
    if os.getenv("WXREAD_QR_PASSWORD") and path.exists() and path.read_bytes().lstrip().startswith(b"{"):
        from cloud_crypto import decrypt
        try:
            return validate_login(json.loads(decrypt(path.read_bytes(), "login-state")))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("云端登录状态无效，请重新运行“扫码登录”工作流。") from exc
    bootstrap = os.getenv(SECRET_NAME)
    if bootstrap:
        try:
            return validate_login(json.loads(bootstrap))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("WXREAD_LOGIN_JSON 无效，请重新扫码并同步登录 Secret。") from exc
    if not path.exists():
        return None
    key_path = path.with_suffix(".key")
    try:
        if path.is_symlink() or key_path.is_symlink():
            raise RuntimeError("登录状态文件不能是符号链接。")
        data = Fernet(key_path.read_bytes()).decrypt(path.read_bytes())
        return validate_login(json.loads(data))
    except (OSError, ValueError, TypeError, InvalidToken) as exc:
        raise RuntimeError("无法解密既有登录状态，请在 GitHub Actions 重新运行“扫码登录”。") from exc


def save_login(value):
    value = validate_login(value)
    path = login_path()
    if os.getenv("WXREAD_QR_PASSWORD"):
        from cloud_crypto import encrypt
        atomic_private_write(path, encrypt(json.dumps(value, ensure_ascii=False).encode(), "login-state"))
        return path
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


def reading_cursor():
    login = load_login()
    return login.get("reading_cursor", "") if login else ""


def save_reading_cursor(book_id):
    login = load_login()
    if login is None:
        return False
    # Preserve the initial cookie seed: cookies.enc is encrypted against it.
    login["reading_cursor"] = str(book_id)
    save_login(login)
    return True


def pending_progress():
    login = load_login()
    return login.get("pending_progress") if login else None


def save_pending_progress(pending):
    login = load_login()
    if login is None:
        raise RuntimeError("无法保存进度恢复记录，请先扫码登录。")
    login["pending_progress"] = pending
    save_login(login)


def preserve_metadata(new_login, previous):
    if previous:
        if new_login["cookies"]["wr_vid"] != previous["cookies"]["wr_vid"]:
            raise RuntimeError("扫码账号不一致，未覆盖原始进度恢复记录。")
        new_login = dict(new_login)
        for key in ("reading_cursor", "pending_progress", "api_key"):
            new_login[key] = previous.get(key, "" if key != "pending_progress" else None)
    return new_login

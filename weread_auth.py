"""WeRead QR protocol used by the GitHub Actions login workflow (no local CLI)."""
import io
import json
import secrets
import time
from urllib.parse import quote

import qrcode
import requests

from login_store import atomic_private_write, login_path


BASE = "https://weread.qq.com"
LOGIN_PAGE = BASE + "/r/weread-skills"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def device_fingerprint():
    path = login_path().parent / "device.json"
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))["fingerprint"]
            if isinstance(value, str) and value.isdigit() and 0 <= int(value) <= 0xffffffff:
                return value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        raise RuntimeError("云端设备标识无效，请检查缓存。")
    value = str(secrets.randbits(32))
    atomic_private_write(path, json.dumps({"fingerprint": value}).encode())
    return value


class QRLogin:
    def __init__(self, fingerprint, session=None):
        self.http = session or requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Referer": LOGIN_PAGE, "Accept": "application/json, text/plain, */*"})
        self.http.cookies.set("wr_fp", fingerprint, domain=".weread.qq.com", path="/", secure=True)

    def _get_json(self, path, params=None, headers=None, timeout=(10, 20)):
        try:
            for attempt in range(3 if headers else 1):
                response = self.http.get(BASE + path, params=params, headers=headers, timeout=timeout, allow_redirects=False)
                if response.status_code == 401 and headers and attempt < 2:
                    time.sleep(0.5)
                    continue
                break
        except requests.RequestException as exc:
            raise RuntimeError("微信读书登录服务连接失败，请稍后重试。") from exc
        if response.status_code != 200:
            raise RuntimeError(f"微信读书登录服务返回 HTTP {response.status_code}。")
        for cookie in response.cookies:
            if cookie.domain.lstrip(".") == "weread.qq.com":
                for old in list(self.http.cookies):
                    if old.name == cookie.name and old.path == cookie.path and old.domain.lstrip(".") == "weread.qq.com":
                        self.http.cookies.clear(old.domain, old.path, old.name)
                if not cookie.is_expired():
                    self.http.cookies.set_cookie(cookie)
        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError("微信读书登录服务返回了非 JSON 响应。") from exc
        if not isinstance(result, dict):
            raise RuntimeError("微信读书登录服务返回的数据格式无效。")
        if path != "/api/auth/getLoginInfo" and result.get("errCode", result.get("errcode")) not in (None, 0, "0"):
            raise RuntimeError("微信读书未接受本次登录请求，请重新扫码。")
        return result

    def begin(self):
        try:
            page = self.http.get(LOGIN_PAGE, timeout=(10, 20), allow_redirects=False)
        except requests.RequestException as exc:
            raise RuntimeError("无法打开微信读书登录页。") from exc
        if page.status_code != 200:
            raise RuntimeError(f"微信读书登录页返回 HTTP {page.status_code}。")
        result = self._get_json("/api/auth/getLoginUid")
        uid = result.get("uid")
        if not isinstance(uid, str) or not uid or len(uid) > 512:
            raise RuntimeError("微信读书没有返回有效二维码，请重新尝试。")
        return uid

    def poll(self, uid):
        try:
            return self._get_json("/api/auth/getLoginInfo", {"uid": uid, "otp": ""}, timeout=(5, 8))
        except RuntimeError as exc:
            if isinstance(exc.__cause__, requests.RequestException):
                return {"pending": True}
            raise

    def complete(self, result):
        if result.get("succeed") is not True:
            raise RuntimeError("手机端尚未确认登录。")
        vid = str(result.get("webLoginVid") or "")
        skey = result.get("accessToken")
        if not vid.isascii() or not vid.isdigit() or not isinstance(skey, str) or not skey:
            raise RuntimeError("登录响应缺少必要凭据，未保存登录状态。")
        for name, value in (("wr_vid", vid), ("wr_skey", skey), ("wr_ql", "0")):
            self.http.cookies.set(name, value, domain=".weread.qq.com", path="/", secure=True)
        refresh = result.get("refreshToken")
        if isinstance(refresh, str) and refresh:
            self.http.cookies.set("wr_rt", quote(refresh, safe=""), domain=".weread.qq.com", path="/", secure=True)
        user = self._get_json("/api/userInfo", {"userVid": vid}, headers={"X-Vid": vid, "X-Skey": skey})
        if not isinstance(user.get("name"), str) or str(user.get("vid", user.get("userVid", vid))) != vid:
            raise RuntimeError("无法验证扫码账号，未保存新的登录状态。")
        cookies = {cookie.name: cookie.value for cookie in self.http.cookies
                   if cookie.domain.lstrip(".") == "weread.qq.com" and not cookie.is_expired()}
        return {"version": 1, "cookies": cookies, "headers": {"user-agent": USER_AGENT, "referer": LOGIN_PAGE}, "api_key": ""}

    def close(self):
        self.http.close()


def qr_png(uid):
    image = qrcode.make(BASE + "/web/confirm?uid=" + quote(uid, safe=""))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()

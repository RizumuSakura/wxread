"""One persistent HTTP session for all WeRead requests and Cookie rotation."""

import logging
import os
import time
from copy import deepcopy
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit

import requests

from cookie_store import CookieStore


REQUEST_TIMEOUT = (10, 30)
RENEW_URL = "https://weread.qq.com/web/login/renewal"
COOKIE_DATA_VARIANTS = (
    {"rq": "%2Fweb%2Fbook%2Fread", "ql": False},
    {"rq": "%2Fweb%2Fbook%2Fread", "ql": True},
    {"rq": "%2Fweb%2Fbook%2Fread"},
)


class WeReadSession:
    def __init__(self, headers, cookies, cookie_file=None):
        if not cookies.get("wr_vid") or not cookies.get("wr_skey"):
            raise RuntimeError("缺少登录 Cookie，请在 GitHub Actions 运行“扫码登录”，或检查旧 WXREAD_CURL_BASH 配置。")
        self.http = requests.Session()
        # A copied Content-Length or Cookie header would override the current data/jar.
        self.http.headers.update({
            key: value for key, value in headers.items()
            if key.lower() not in {"cookie", "content-length", "host"}
        })
        self.http.headers["Content-Type"] = "application/json;charset=UTF-8"
        self.store = CookieStore(cookies, cookie_file)
        restored = self.store.load()
        if restored is not None:
            self.http.cookies = restored
        else:
            for name, value in cookies.items():
                self.http.cookies.set(name, value, domain=".weread.qq.com", path="/", secure=True)
        self._mask_cookies()
        if not any(cookie.name == "wr_rt" for cookie in self.http.cookies):
            logging.warning("Cookie 缺少 wr_rt，长期续期可能失败；请在 GitHub Actions 运行“扫码登录”。")

    def _mask_cookies(self):
        if os.getenv("GITHUB_ACTIONS") == "true":
            for cookie in self.http.cookies:
                if cookie.value and len(cookie.value) >= 8:
                    value = cookie.value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                    print(f"::add-mask::{value}", flush=True)

    def _remove_cookie(self, name, path):
        for cookie in list(self.http.cookies):
            if (cookie.name == name and cookie.path == path
                    and cookie.domain.lstrip(".") == "weread.qq.com"):
                self.http.cookies.clear(cookie.domain, cookie.path, cookie.name)

    def _merge_response_cookies(self, response):
        # A host-only/domain duplicate can otherwise send both old and new wr_skey.
        for reply in [*response.history, response]:
            for cookie in reply.cookies:
                if cookie.domain.lstrip(".") == "weread.qq.com":
                    self._remove_cookie(cookie.name, cookie.path)
                    if not cookie.is_expired():
                        self.http.cookies.set_cookie(cookie)
            # CookieJar omits expired Set-Cookie entries, so also honor deletions.
            raw_headers = getattr(reply.raw, "headers", None)
            if raw_headers is not None and hasattr(raw_headers, "getlist"):
                for header in raw_headers.getlist("Set-Cookie"):
                    parsed = SimpleCookie()
                    try:
                        parsed.load(header)
                    except CookieError:
                        continue
                    for name, morsel in parsed.items():
                        domain = morsel["domain"].lstrip(".") or urlsplit(reply.url).hostname
                        expired = False
                        if morsel["max-age"]:
                            try:
                                expired = int(morsel["max-age"]) <= 0
                            except ValueError:
                                pass
                        elif morsel["expires"]:
                            try:
                                expired = parsedate_to_datetime(morsel["expires"]).timestamp() <= time.time()
                            except (TypeError, ValueError, OverflowError):
                                pass
                        if domain == "weread.qq.com" and expired:
                            default_path = urlsplit(reply.url).path.rsplit("/", 1)[0] or "/"
                            self._remove_cookie(name, morsel["path"] or default_path)
        self.http.cookies.clear_expired_cookies()

    def _persist(self):
        self._mask_cookies()
        # Save after every response, before inspecting its body: a later request
        # or read failure must not discard a successfully rotated refresh token.
        try:
            self.store.save(self.http.cookies)
        except OSError as exc:
            raise RuntimeError("无法保存续期 Cookie，请检查 WXREAD_COOKIE_FILE 的目录权限。") from exc

    def post(self, url, payload, persist_cookies=True):
        try:
            response = self.http.post(url, json=payload, timeout=REQUEST_TIMEOUT, allow_redirects=False)
        except requests.RequestException:
            # A redirect can rotate a Cookie before a subsequent connection fails.
            if persist_cookies:
                self._persist()
            raise
        self._merge_response_cookies(response)
        if persist_cookies:
            self._persist()
        return response

    def get(self, url, headers=None):
        try:
            response = self.http.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False, **({"headers": headers} if headers else {}))
        except requests.RequestException:
            self._persist()
            raise
        self._merge_response_cookies(response)
        self._persist()
        return response

    def renew(self):
        logging.info("尝试续期登录会话。")
        for payload in COOKIE_DATA_VARIANTS:
            # Requests applies Set-Cookie before the response is validated.
            # A rejected renewal must not delete/replace usable credentials.
            previous_cookies = deepcopy(self.http.cookies)
            try:
                response = self.post(RENEW_URL, payload, persist_cookies=False)
            except requests.RequestException as exc:
                self.http.cookies = previous_cookies
                logging.warning("续期请求失败（%s）。", type(exc).__name__)
                continue
            try:
                result = response.json()
            except ValueError:
                self.http.cookies = previous_cookies
                logging.warning("续期接口返回非 JSON 响应，HTTP %s。", response.status_code)
                continue
            if response.ok and isinstance(result, dict) and result.get("succ") == 1:
                self._persist()
                logging.info("登录会话续期成功，完整 Cookie 已加密保存。")
                return True
            self.http.cookies = previous_cookies
            logging.warning(
                "续期未成功：HTTP %s，errCode=%s。",
                response.status_code,
                result.get("errCode") if isinstance(result, dict) else None,
            )
        return False

    def close(self):
        self.http.close()

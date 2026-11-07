"""User-confirmed WeRead QR login; no browser cookie capture is required."""
import argparse
import getpass
import io
import json
import os
import re
import secrets
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

import qrcode
import requests

from login_store import atomic_private_write, current_login, login_path, save_login


BASE = "https://weread.qq.com"
SKILLS_PAGE = BASE + "/r/weread-skills"
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
        raise RuntimeError("本地设备标识无效，请检查 .state/device.json。")
    value = str(secrets.randbits(32))
    atomic_private_write(path, json.dumps({"fingerprint": value}).encode())
    return value


class QRLogin:
    def __init__(self, fingerprint, session=None):
        self.http = session or requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Referer": SKILLS_PAGE, "Accept": "application/json, text/plain, */*"})
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
            page = self.http.get(SKILLS_PAGE, timeout=(10, 20), allow_redirects=False)
        except requests.RequestException as exc:
            raise RuntimeError("无法打开微信读书登录页。") from exc
        if page.status_code != 200:
            raise RuntimeError(f"微信读书登录页返回 HTTP {page.status_code}。")
        result = self._get_json("/api/auth/getLoginUid")
        uid = result.get("uid")
        if not isinstance(uid, str) or not uid or len(uid) > 512:
            raise RuntimeError("微信读书没有返回有效二维码，请重新尝试。")
        return uid

    def poll(self, uid, otp=""):
        try:
            return self._get_json("/api/auth/getLoginInfo", {"uid": uid, "otp": otp}, timeout=(5, 8))
        except RuntimeError as exc:
            if isinstance(exc.__cause__, requests.RequestException):
                return {"pending": True}
            raise

    def complete(self, result):
        if result.get("succeed") is not True:
            raise RuntimeError("手机端尚未确认登录。")
        vid = str(result.get("webLoginVid") or "")
        skey = result.get("accessToken")
        if not vid or not isinstance(skey, str) or not skey:
            raise RuntimeError("登录响应缺少必要凭据，未保存登录状态。")
        self.http.cookies.set("wr_vid", vid, domain=".weread.qq.com", path="/", secure=True)
        self.http.cookies.set("wr_skey", skey, domain=".weread.qq.com", path="/", secure=True)
        self.http.cookies.set("wr_ql", "0", domain=".weread.qq.com", path="/", secure=True)
        refresh = result.get("refreshToken")
        if isinstance(refresh, str) and refresh:
            self.http.cookies.set("wr_rt", quote(refresh, safe=""), domain=".weread.qq.com", path="/", secure=True)
        auth_headers = {"X-Vid": vid, "X-Skey": skey}
        user = self._get_json("/api/userInfo", {"userVid": vid}, headers=auth_headers)
        if (not isinstance(user.get("name"), str)
                or str(user.get("vid", user.get("userVid", vid))) != vid):
            raise RuntimeError("无法验证扫码账号，未保存新的登录状态。")
        try:
            key = self._get_json("/api/skills/apikeyGet", {"only_show": 1}, headers=auth_headers).get("apikey", "")
        except RuntimeError:
            key = ""
        cookies = {cookie.name: cookie.value for cookie in self.http.cookies
                   if cookie.domain.lstrip(".") == "weread.qq.com" and not cookie.is_expired()}
        return {"version": 1, "cookies": cookies, "headers": {"user-agent": USER_AGENT, "referer": SKILLS_PAGE},
                "api_key": key if isinstance(key, str) else ""}

    def close(self):
        self.http.close()


def create_qr(uid):
    output = login_path().parent / "login-qr.png"
    image = qrcode.make(BASE + "/web/confirm?uid=" + quote(uid, safe=""))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    atomic_private_write(output, stream.getvalue())
    return output.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description="微信扫码登录并加密保存凭据，免手动抓包")
    parser.add_argument("--no-open", action="store_true", help="不自动打开二维码图片")
    parser.add_argument("--timeout", type=int, default=300, help="等待扫码的最长秒数（最多 600）")
    parser.add_argument("--github-repo", help="可选：同步到自己的 owner/repository")
    parser.add_argument("--github-environment", help="可选：同步到指定 GitHub 环境，例如 AutoRead")
    parser.add_argument("--sync-only", action="store_true", help="只同步已有的本地登录状态到 GitHub")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 600:
        parser.error("timeout 必须在 1 到 600 秒之间")
    client = None
    try:
        if args.sync_only:
            if not args.github_repo:
                raise RuntimeError("--sync-only 需要同时指定 --github-repo。")
            login = current_login()
            if login is None:
                raise RuntimeError("尚未扫码登录，请先运行 python qr_login.py。")
        else:
            client = QRLogin(device_fingerprint())
            uid = client.begin()
            qr_path = create_qr(uid)
            print("QR_READY " + json.dumps({"path": str(qr_path), "expires_in_seconds": args.timeout}), flush=True)
            print("请用微信扫码，并在手机上确认登录。", flush=True)
            if not args.no_open:
                webbrowser.open(qr_path.as_uri())
            deadline = time.monotonic() + args.timeout
            otp = ""
            while time.monotonic() < deadline:
                result = client.poll(uid, otp)
                otp = ""
                if result.get("succeed") is True:
                    login = client.complete(result)
                    save_login(login)
                    print("登录成功；凭据已加密保存。", flush=True)
                    if not login["api_key"]:
                        print("本次未取得官方 API Key；网页登录已保存，官方查询可另设 WEREAD_API_KEY。", flush=True)
                    break
                code = result.get("logicCode", "")
                if code in ("NEED_OTP", "OTP_NOT_MATCH"):
                    print("OTP_REQUIRED：请输入手机上显示的四位验证码。", flush=True)
                    otp = getpass.getpass("验证码（不回显）: ").strip()
                    if not re.fullmatch(r"[0-9]{4}", otp):
                        raise RuntimeError("验证码必须为四位数字，请重新扫码。")
                elif code in ("LOGIN_TIMEOUT", "OTP_EXPIRED"):
                    raise RuntimeError("二维码或验证码已过期，请重新运行扫码登录。")
                elif code not in ("", "WAIT", "WAITING", "SCANNED", "WAIT_SCAN", "WAIT_CONFIRM"):
                    raise RuntimeError("扫码登录未完成，请在手机确认或重新尝试。")
                time.sleep(0.5)
            else:
                raise RuntimeError("等待扫码超时，未替换原登录状态。")
        if args.github_repo:
            from github_login_secret import upload_login
            upload_login(args.github_repo, login, environment=args.github_environment)
            print("登录状态已加密同步到 GitHub Secret WXREAD_LOGIN_JSON。", flush=True)
        return 0
    except (RuntimeError, OSError, EOFError, KeyboardInterrupt) as exc:
        print("登录已取消。" if isinstance(exc, (EOFError, KeyboardInterrupt)) else str(exc), flush=True)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())

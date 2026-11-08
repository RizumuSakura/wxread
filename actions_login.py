"""GitHub-only QR login orchestration; public artifacts contain ciphertext only."""
import argparse
import json
import os
from pathlib import Path
import re
import time

import requests
from requests.cookies import create_cookie

from cloud_crypto import cache_prefix, decrypt, encrypt, password, repository_scope
from cookie_store import CookieStore
from login_store import atomic_private_write, load_login, login_path, save_login, preserve_metadata
from weread_auth import QRLogin, device_fingerprint, qr_png


PUBLIC_DIR = Path(".state/qr-public")
PENDING_FILE = Path(".state/qr-session.enc")


def output(name, value):
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def summary(text):
    target = os.getenv("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(text + "\n")


def cache_metadata(require_cloud=False):
    legacy_prefix = ""
    if os.getenv("WXREAD_QR_PASSWORD"):
        prefix = cache_prefix()
        mode = "cloud"
    else:
        if require_cloud:
            password()
        from config import cookies
        if not cookies.get("wr_vid") or not cookies.get("wr_skey"):
            raise RuntimeError("尚无登录状态，请先配置 WXREAD_QR_PASSWORD 并运行“扫码登录”工作流。")
        prefix = "wxread-cookie-v1-" + CookieStore(cookies).cache_id
        mode = "legacy"
    prefix += "-" + os.getenv("GITHUB_REF_NAME", "main") + "-"
    if mode == "cloud" and (os.getenv("WXREAD_LOGIN_JSON") or os.getenv("WXREAD_CURL_BASH")):
        # Migrate the last valid old cache rather than retrying a stale bootstrap.
        from config import cookies
        if cookies.get("wr_vid") and cookies.get("wr_skey"):
            legacy_prefix = "wxread-cookie-v1-" + CookieStore(cookies).cache_id + "-" + os.getenv("GITHUB_REF_NAME", "main") + "-"
    key = prefix + os.getenv("GITHUB_RUN_ID", "local") + "-" + os.getenv("GITHUB_RUN_ATTEMPT", "1")
    for name, value in (("cache_prefix", prefix), ("cache_key", key), ("mode", mode), ("legacy_cache_prefix", legacy_prefix)):
        output(name, value)
    print("已准备加密会话缓存（" + mode + "）。", flush=True)
    return prefix, key


def cookie_records(jar):
    return [{"name": item.name, "value": item.value, "domain": item.domain, "path": item.path,
             "secure": item.secure, "expires": item.expires, "discard": item.discard, "rest": item._rest}
            for item in jar if item.domain.lstrip(".") == "weread.qq.com" and not item.is_expired()]


def restore_cookies(client, records):
    if not isinstance(records, list) or len(records) > 100:
        raise RuntimeError("扫码会话 Cookie 数据无效。")
    client.http.cookies.clear()
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get("name"), str)
                or not isinstance(record.get("value"), str) or record.get("domain", "").lstrip(".") != "weread.qq.com"
                or not record.get("path", "").startswith("/")):
            raise RuntimeError("扫码会话 Cookie 数据无效。")
        cookie = create_cookie(**record)
        if not cookie.is_expired():
            client.http.cookies.set_cookie(cookie)


def save_pending(state, client):
    state["cookies"] = cookie_records(client.http.cookies)
    atomic_private_write(PENDING_FILE, encrypt(json.dumps(state, separators=(",", ":")).encode(), "qr-session"))


def read_pending():
    try:
        state = json.loads(decrypt(PENDING_FILE.read_bytes(), "qr-session"))
        now = time.time()
        if (state["version"] != 1 or state["repository"] != repository_scope()
                or not re.fullmatch(r"[0-9]+", str(state["run_id"]))
                or not isinstance(state["uid"], str) or not 1 <= len(state["uid"]) <= 512
                or not 0 < state["expires_at"] - state["created_at"] <= 600
                or state["created_at"] > now + 30 or state["expires_at"] <= now):
            raise ValueError("session mismatch or expired")
        return state
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("扫码会话缺失、过期或不匹配，请重新生成二维码；原登录状态未替换。") from exc


def write_viewer(uid, state):
    template = Path(__file__).with_name("qr_viewer.html").read_text(encoding="utf-8")
    envelope = encrypt(qr_png(uid), "qr-png").decode()
    metadata = json.dumps({"repository": state["repository"], "run_id": state["run_id"],
                           "expires_at": state["expires_at"]}, separators=(",", ":"))
    html = template.replace("__QR_ENVELOPE__", envelope).replace("__QR_METADATA__", metadata)
    atomic_private_write(PUBLIC_DIR / "login.html", html.encode())


def prepare(timeout=600):
    password()
    if not 1 <= timeout <= 600:
        raise RuntimeError("二维码等待上限必须在 1–600 秒之间。")
    run_id = os.getenv("GITHUB_RUN_ID", "0")
    client = None
    try:
        login = load_login()
        expected_vid = os.getenv("WXREAD_ACCOUNT_VID", "") or (login["cookies"]["wr_vid"] if login else "")
        if expected_vid and not re.fullmatch(r"[0-9]+", expected_vid):
            raise RuntimeError("预期账号标识无效。")
        fingerprint = device_fingerprint()
        client = QRLogin(fingerprint)
        uid = client.begin()
        created = int(time.time())
        state = {"version": 1, "repository": repository_scope(), "run_id": run_id, "uid": uid,
                 "fingerprint": fingerprint, "expected_vid": expected_vid,
                 "created_at": created, "expires_at": created + timeout}
        save_pending(state, client)
        write_viewer(state["uid"], state)
        summary("### 扫码登录\n请下载本次运行的 `wxread-login-qr` 附件，解压后用浏览器打开 `login.html`，输入二维码查看口令并用微信扫码。\n"
                "扫码后返回此页，确认 **Save cloud login state** 成功。")
        print("加密二维码查看页已生成；上传附件后将等待手机确认。", flush=True)
        return state
    finally:
        if client is not None:
            client.close()


def wait_for_login():
    output("authenticated", "false")
    state = read_pending()
    if str(state["run_id"]) != os.getenv("GITHUB_RUN_ID", "0"):
        raise RuntimeError("扫码会话不属于本次运行。")
    client = QRLogin(state["fingerprint"])
    try:
        restore_cookies(client, state["cookies"])
        deadline = time.monotonic() + min(600, state["expires_at"] - time.time())
        print("等待微信扫码及手机确认；不会执行阅读。", flush=True)
        while time.monotonic() < deadline:
            result = client.poll(state["uid"])
            if result.get("succeed") is True:
                if state["expected_vid"] and str(result.get("webLoginVid")) != state["expected_vid"]:
                    raise RuntimeError("扫码账号与既有账号不一致，拒绝替换；请使用原微信读书账号。")
                login = preserve_metadata(client.complete(result), load_login())
                save_login(login)
                CookieStore(login["cookies"]).save(client.http.cookies)
                output("authenticated", "true")
                PENDING_FILE.unlink(missing_ok=True)
                summary("### 登录已确认\n账号校验与加密状态写入成功。请确认最后的 **Save cloud login state** 步骤也成功后，再运行 `wxread`。\n"
                        "后续 Cookie 轮换保存在加密缓存，不需要本地 Python，也不需要额外的 GitHub PAT。")
                print("手机已确认，账号校验通过；云端登录状态已加密保存，等待缓存上传。", flush=True)
                return 0
            code = result.get("logicCode", "")
            if code == "LOGIN_TIMEOUT":
                raise RuntimeError("二维码已过期，请重新触发生成新的二维码。")
            if code not in ("", "WAIT", "WAITING", "SCANNED", "WAIT_SCAN", "WAIT_CONFIRM"):
                raise RuntimeError("微信读书未接受本次扫码，请重新触发。")
            time.sleep(0.5)
        raise RuntimeError("等待扫码超时，原登录状态未替换；需要时重新触发。")
    finally:
        client.close()
        PENDING_FILE.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="GitHub Actions 云端扫码登录入口，不提供本地扫码 CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    cache = sub.add_parser("cache-key")
    cache.add_argument("--require-cloud", action="store_true")
    create = sub.add_parser("prepare")
    create.add_argument("--timeout", type=int, default=600)
    sub.add_parser("wait")
    args = parser.parse_args(argv)
    try:
        if os.getenv("GITHUB_ACTIONS") != "true":
            raise RuntimeError("请从 GitHub Actions 触发“扫码登录”工作流；本地扫码入口已移除。")
        if args.command == "cache-key":
            cache_metadata(args.require_cloud)
        elif args.command == "prepare":
            prepare(args.timeout)
        else:
            return wait_for_login()
        return 0
    except (RuntimeError, OSError, requests.RequestException):
        # Request exceptions may contain URLs or token-bearing headers. Never echo them.
        import sys
        exc = sys.exc_info()[1]
        message = str(exc) if isinstance(exc, RuntimeError) else "云端登录发生网络或文件错误，请重试；未输出敏感响应。"
        print(message, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

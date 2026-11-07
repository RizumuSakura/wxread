"""Fixed-book reading session with measured, nonaccelerated reporting intervals."""
import argparse
from dataclasses import dataclass
import hashlib
import logging
import random
import time

import requests

from book_context import CATALOG_URL, load_context
from config import headers, cookies, READ_NUM, BOOK_URL, PUSH_METHOD
from log_utils import setup_logging
from push import push
from reader_protocol import encode_data, cal_hash
from weread_session import WeReadSession


READ_URL = "https://weread.qq.com/web/book/read"
FIX_SYNCKEY_URL = CATALOG_URL
INTERVAL_SECONDS = 30
SYNCKEY_REPAIR_LIMIT = 3
AUTH_ERROR_CODES = {"-2010", "-2012", "-2013"}
AUTH_RETRY_LIMIT = 2
LOGIN_HELP = "请在 GitHub Actions 运行“扫码登录”工作流，手机确认后重新检查登录。"


@dataclass
class ReadingResult:
    book_id: str
    title: str
    completed: int
    acknowledged_seconds: int


def refresh_cookie(client, strict=True):
    if client.renew():
        return True
    if strict:
        raise RuntimeError("登录会话无法续期；可能已失效或续期接口暂时不可用。" + LOGIN_HELP)
    logging.warning("启动续期未成功，使用当前 Cookie 尝试读取目标书籍。")
    return False


def response_data(response):
    try:
        result = response.json()
    except ValueError as exc:
        if response.status_code == 401:
            return {}
        raise RuntimeError(f"read 接口返回非 JSON 响应，HTTP {response.status_code}。") from exc
    if not isinstance(result, dict):
        raise RuntimeError("read 接口返回了非预期的 JSON 结构。")
    return result


def auth_error(response, result):
    return response.status_code == 401 or str(result.get("errCode")) in AUTH_ERROR_CODES


def sign(payload):
    payload.pop("s", None)
    payload["s"] = cal_hash(encode_data(payload))
    return payload


def enter_book(client, context):
    client.http.headers.update({"Referer": context.url, "Origin": "https://weread.qq.com"})
    payload = sign(context.position(int(time.time())))
    response = client.post(READ_URL, payload)
    result = response_data(response)
    if not response.ok or result.get("succ") != 1:
        raise RuntimeError(f"无法进入目标书籍：HTTP {response.status_code}，errCode={result.get('errCode')}。" + LOGIN_HELP)


def fix_no_synckey(client, book_id):
    response = client.post(FIX_SYNCKEY_URL, {"bookIds": [str(book_id)]})
    response.raise_for_status()


def wait_interval(since):
    # Re-check monotonic time after sleep: an early wakeup cannot create 30 seconds.
    while True:
        remaining = INTERVAL_SECONDS - (time.monotonic() - since)
        if remaining <= 0:
            return
        time.sleep(remaining)


def run_reading(client, refresh_print, reference=None, count=None):
    count = READ_NUM if count is None else count
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 600:
        raise RuntimeError("READ_NUM 必须为 1–600 的正整数。")
    reference = BOOK_URL if reference is None else reference
    refresh_cookie(client, strict=False)
    context = load_context(client, reference)
    enter_book(client, context)
    logging.info("固定目标书籍：%s；bookId=%s；chapterUid=%s，目标 %s 段。",
                 context.title, context.book_id, context.chapter_uid, count)
    completed = seconds = repairs = auth_attempts = 0
    since = time.monotonic()
    while completed < count:
        wait_interval(since)
        elapsed = max(0, int(time.monotonic() - since))
        duration = min(INTERVAL_SECONDS, elapsed)
        if duration <= 0:
            raise RuntimeError("未观察到有效的真实等待时间，停止提交。")
        milliseconds = int(time.time() * 1000)
        nonce = random.randrange(1000)
        payload = context.position(milliseconds // 1000)
        payload.update({"rt": duration, "ts": milliseconds, "rn": nonce,
                        "sg": hashlib.sha256(f"{milliseconds}{nonce}{context.token}".encode()).hexdigest()})
        response = client.post(READ_URL, sign(payload))
        result = response_data(response)
        if response.ok and result.get("succ") == 1:
            if result.get("synckey"):
                completed += 1
                seconds += duration
                repairs = auth_attempts = 0
                since = time.monotonic()
                refresh_print(f"目标 bookId={context.book_id}：已确认 {completed}/{count} 段，提交实际等待 {seconds} 秒（非到账时长证明）")
                continue
            repairs += 1
            if repairs > SYNCKEY_REPAIR_LIMIT:
                raise RuntimeError(f"连续 {SYNCKEY_REPAIR_LIMIT} 次未恢复 synckey，终止运行。")
            logging.warning("未返回 synckey，仅修复目标书籍（%s/%s）。", repairs, SYNCKEY_REPAIR_LIMIT)
            fix_no_synckey(client, context.book_id)
        elif auth_error(response, result):
            auth_attempts += 1
            if auth_attempts > AUTH_RETRY_LIMIT:
                raise RuntimeError(f"连续 {AUTH_RETRY_LIMIT} 次续期后仍鉴权失败，停止重试。" + LOGIN_HELP)
            refresh_cookie(client)
        else:
            raise RuntimeError(f"read 接口异常：HTTP {response.status_code}，errCode={result.get('errCode')}；停止本次任务。")
        # A new session/repair starts a new measured period; never backdate it.
        context = load_context(client, reference)
        enter_book(client, context)
        since = time.monotonic()
    return ReadingResult(context.book_id, context.title, completed, seconds)


def main(check_auth=False, book_url=None, read_num=None):
    refresh_print = setup_logging()
    client = None
    try:
        client = WeReadSession(headers, cookies)
        if check_auth:
            refresh_cookie(client)
            logging.info("登录续期检查通过，未执行阅读或消息推送。")
            return 0
        result = run_reading(client, refresh_print, book_url, read_num)
        logging.info("固定书籍任务结束；已确认 %s 段，提交实际等待 %s 秒。服务端是否计入请以客户端记录为准。",
                     result.completed, result.acknowledged_seconds)
        push(f"微信读书任务完成。\n书籍：{result.title}\nbookId：{result.book_id}\n"
             f"接口确认：{result.completed} 段，提交实际等待 {result.acknowledged_seconds} 秒。\n"
             "这不是服务端到账时长证明。", PUSH_METHOD, is_success=True)
        return 0
    except (RuntimeError, requests.RequestException) as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else "微信读书网络请求失败，已停止并保留可用登录状态。"
        logging.error("%s", message)
        if not check_auth:
            push(message, PUSH_METHOD, is_success=False)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-auth", action="store_true", help="仅检查登录续期，不执行阅读或推送")
    parser.add_argument("--book-url", help="微信读书阅读器链接或数字 bookId，不指定则使用 WXREAD_BOOK_URL")
    parser.add_argument("--read-num", type=int, help="本次真实等待周期数（1–600），默认 READ_NUM")
    args = parser.parse_args()
    raise SystemExit(main(args.check_auth, args.book_url, args.read_num))

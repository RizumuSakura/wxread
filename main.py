"""Single fixed book reading with measured reporting intervals."""
import argparse
from dataclasses import dataclass
import hashlib
import logging
import random
import re
import signal
import time

import requests

from book_context import CATALOG_URL, load_context, saved_position
from config import headers, cookies, READ_NUM, BOOK_URL, PUSH_METHOD
from log_utils import setup_logging
from push import push
from reader_protocol import encode_data, cal_hash
from weread_session import WeReadSession


READ_URL = "https://weread.qq.com/web/book/read"
FIX_SYNCKEY_URL = CATALOG_URL
INTERVAL_SECONDS = 30
MAX_READ_NUM = 400
SYNCKEY_REPAIR_LIMIT = 3
AUTH_ERROR_CODES = {"-2010", "-2012", "-2013"}
AUTH_RETRY_LIMIT = 2
NETWORK_RETRY_LIMIT = 3
RETRY_BASE_DELAY = 2.0
NETWORK_FAILURE_LIMIT = 5
LOGIN_HELP = "请在 GitHub Actions 运行“扫码登录”工作流，手机确认后重新检查登录。"


@dataclass
class ReadingResult:
    book_id: str
    title: str
    completed: int
    acknowledged_seconds: int


def check_count(count):
    """Every period is one real 30 second wait; the count is bounded by the step timeout."""
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_READ_NUM:
        raise RuntimeError(f"READ_NUM 必须为 1–{MAX_READ_NUM} 的正整数（每段 {INTERVAL_SECONDS} 秒）。")
    return count


def refresh_cookie(client, strict=True):
    if client.renew():
        return True
    if strict:
        raise RuntimeError("登录会话无法续期；可能已失效或续期接口暂时不可用。" + LOGIN_HELP)
    logging.warning("启动续期未成功，使用当前 Cookie 尝试读取目标书籍。")
    return False


def with_network_retry(operation, what):
    """Retry a single request a few times before giving up on it."""
    delay = RETRY_BASE_DELAY
    for attempt in range(1, NETWORK_RETRY_LIMIT + 1):
        try:
            return operation()
        except requests.RequestException:
            if attempt >= NETWORK_RETRY_LIMIT:
                raise
            logging.warning("%s网络请求失败（第 %s/%s 次），%.0f 秒后重试。", what, attempt, NETWORK_RETRY_LIMIT, delay)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"{what}连续失败，停止本次任务。")


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


def enter_with_retry(client, context):
    return with_network_retry(lambda: enter_book(client, context), "进入书籍")


def fix_no_synckey(client, book_id):
    response = with_network_retry(lambda: client.post(FIX_SYNCKEY_URL, {"bookIds": [str(book_id)]}), "synckey 修复")
    response.raise_for_status()


def wait_interval(since):
    # Re-check monotonic time after sleep: an early wakeup cannot create 30 seconds.
    while True:
        remaining = INTERVAL_SECONDS - (time.monotonic() - since)
        if remaining <= 0:
            return
        time.sleep(remaining)


def safe_label(value):
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", str(value)).strip()[:120]


def run_single_book(client, refresh_print, reference=None, count=None):
    count = check_count(READ_NUM if count is None else count)
    reference = BOOK_URL if reference is None else reference
    refresh_cookie(client, strict=False)
    context = load_context(client, reference)
    label = safe_label(context.title)
    chapter = safe_label(context.chapter_title or f"chapterUid={context.chapter_uid}")
    logging.info("当前书籍：%s；章节：%s；起点 %s%%，章节位置 %s；本书目标 %s 段。",
                 label, chapter, context.progress, context.offset, count)
    enter_with_retry(client, context)
    result = read_periods(client, refresh_print, reference, count, context, label)
    logging.info("本书完成：%s；已按原始位置提交 %s 段。", label, count)
    return result


def rebuild_context(client, reference, original, book_id):
    context = load_context(client, reference, position=original)
    if context.book_id != book_id:
        raise RuntimeError("重建会话后书籍标识变化，停止提交。")
    return context


def read_periods(client, refresh_print, reference, count, context, label):
    original = saved_position(context.original)
    book_id = context.book_id
    completed = seconds = repairs = auth_attempts = network_failures = 0
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
        try:
            response = with_network_retry(lambda: client.post(READ_URL, sign(payload)), "read 提交")
            result = response_data(response)
        except requests.RequestException:
            network_failures += 1
            if network_failures > NETWORK_FAILURE_LIMIT:
                raise RuntimeError(f"连续 {NETWORK_FAILURE_LIMIT} 段网络失败，停止本次任务；本运行已确认 {completed}/{count} 段。")
            logging.warning("本段网络请求未完成，丢弃该段等待时间并重新进入书籍（%s/%s）。", network_failures, NETWORK_FAILURE_LIMIT)
            context = rebuild_context(client, reference, original, book_id)
            enter_with_retry(client, context)
            since = time.monotonic()
            continue
        network_failures = 0
        if response.ok and result.get("succ") == 1:
            if result.get("synckey"):
                completed += 1
                seconds += duration
                repairs = auth_attempts = 0
                since = time.monotonic()
                refresh_print(f"当前书籍：{label}；本书已确认 {completed}/{count} 段，提交实际等待 {seconds} 秒")
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
        context = rebuild_context(client, reference, original, book_id)
        enter_with_retry(client, context)
        since = time.monotonic()
    return ReadingResult(context.book_id, context.title, completed, seconds)


def run_reading(client, refresh_print, reference=None, count=None):
    count = check_count(READ_NUM if count is None else count)
    reference = (BOOK_URL if reference is None else reference).strip()
    if not reference:
        raise RuntimeError("未指定书籍：请在本次 book_url 或仓库 Variable WXREAD_BOOK_URL 填入阅读器链接或数字 bookId。")
    return run_single_book(client, refresh_print, reference, count)


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
        logging.info("任务结束；已确认 %s 段，提交实际等待 %s 秒。服务端是否计入请以客户端记录为准。",
                     result.completed, result.acknowledged_seconds)
        label = f"书籍：{result.title}\nbookId：{result.book_id}"
        push(f"微信读书任务完成。\n{label}\n"
             f"接口确认：{result.completed} 段，提交实际等待 {result.acknowledged_seconds} 秒。\n"
             "这不是服务端到账时长证明。", PUSH_METHOD, is_success=True)
        return 0
    except (RuntimeError, requests.RequestException) as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else "微信读书网络请求连续失败，已停止并保留可用登录状态。"
        logging.error("%s", message)
        if not check_auth:
            push(message, PUSH_METHOD, is_success=False)
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise RuntimeError("收到停止信号，停止提交并保留当前登录状态。")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-auth", action="store_true", help="仅检查登录续期，不执行阅读或推送")
    parser.add_argument("--book-url", help="阅读器链接或数字 bookId；默认使用 WXREAD_BOOK_URL 或《被讨厌的勇气》")
    parser.add_argument("--read-num", type=int, help=f"本次真实等待周期数（1–{MAX_READ_NUM}），默认 READ_NUM")
    args = parser.parse_args()
    raise SystemExit(main(args.check_auth, args.book_url, args.read_num))

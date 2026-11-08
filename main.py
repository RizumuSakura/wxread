"""Shelf cycling or explicit-book reading with measured reporting intervals."""
import argparse
from dataclasses import dataclass
import hashlib
import logging
import os
import random
import re
import signal
import time

import requests

from book_context import BookUnavailableError, CATALOG_URL, load_context
from config import headers, cookies, READ_NUM, BOOK_URL, PUSH_METHOD
from log_utils import setup_logging
from login_store import reading_cursor, save_reading_cursor
from progress_guard import ProgressGuard, recover_pending
from push import push
from reader_protocol import encode_data, cal_hash
from weread_session import WeReadSession
from weread_shelf import load_shelf


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
    mode: str = "single"
    books_read: int = 1


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


def safe_label(value):
    return re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", " ", str(value)).strip()[:120]


def run_single_book(client, refresh_print, reference=None, count=None, refresh_session=True, disclose=True, expected_book_id=None, display_label=None):
    count = READ_NUM if count is None else count
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 600:
        raise RuntimeError("READ_NUM 必须为 1–600 的正整数。")
    reference = BOOK_URL if reference is None else reference
    if refresh_session:
        refresh_cookie(client, strict=False)
    context = load_context(client, reference)
    if expected_book_id is not None and context.book_id != expected_book_id:
        raise RuntimeError("书架条目与阅读器返回的书籍标识不一致，停止提交。")
    label = display_label or (safe_label(context.title) if disclose else "私密书籍")
    chapter = safe_label(context.chapter_title or f"chapterUid={context.chapter_uid}") if disclose else "私密章节"
    logging.info("当前书籍：%s；章节：%s；起点 %s%%，章节位置 %s；本书目标 %s 段。",
                 label, chapter, context.progress, context.offset, count)
    with ProgressGuard(client, context, enter_book) as guard:
        guard.before_write()
        enter_book(client, context)
        result = read_periods(client, refresh_print, reference, count, context, guard, label)
    logging.info("本书完成：%s；原始进度已核对。", label)
    return result


def read_periods(client, refresh_print, reference, count, context, guard, label):
    original = context.original
    book_id = context.book_id
    completed = seconds = repairs = auth_attempts = 0
    since = time.monotonic()
    while completed < count:
        wait_interval(since)
        elapsed = max(0, int(time.monotonic() - since))
        duration = min(INTERVAL_SECONDS, elapsed)
        if duration <= 0:
            raise RuntimeError("未观察到有效的真实等待时间，停止提交。")
        guard.before_write()
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
        context = load_context(client, reference, position=original)
        if context.book_id != book_id:
            raise RuntimeError("重建会话后书籍标识变化，停止提交。")
        guard.before_write()
        enter_book(client, context)
        since = time.monotonic()
    return ReadingResult(context.book_id, context.title, completed, seconds)


def run_reading(client, refresh_print, reference=None, count=None):
    count = READ_NUM if count is None else count
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 600:
        raise RuntimeError("READ_NUM 必须为 1–600 的正整数。")
    reference = (BOOK_URL if reference is None else reference).strip()
    if reference:
        return run_single_book(client, refresh_print, reference, count)
    shelf = load_shelf()
    active = list(shelf.books)
    if not active:
        raise RuntimeError("个人书架没有可用于网页阅读的电子书。")
    cursor = reading_cursor()
    index = next((i for i, item in enumerate(active) if item.book_id == cursor), 0)
    if cursor and any(item.book_id == cursor for item in active):
        logging.info("已恢复书架阅读游标。")
    refresh_cookie(client, strict=False)
    logging.info("书架循环：电子书条目 %s，可网页阅读 %s；专辑 %s、文章收藏入口 %s 不参与。目标合计 %s 段。",
                 shelf.ebook_entries, len(active), shelf.albums, shelf.article_collection, count)
    completed = seconds = 0
    visited = set()
    while completed < count:
        candidate = active[index]
        disclose = not candidate.private or os.getenv("WXREAD_SHOW_PRIVATE_TITLES", "").lower() == "true"
        name = safe_label(candidate.title or "未命名书籍") if disclose else "私密书籍"
        label = f"[{index + 1}/{len(active)}]《{name}》"
        logging.info("书架进度：%s/%s 段；准备阅读 %s。", completed, count, label)
        try:
            result = run_single_book(client, refresh_print, candidate.reference, 1,
                                     refresh_session=False, disclose=disclose, expected_book_id=candidate.book_id, display_label=label)
        except (BookUnavailableError, requests.HTTPError) as exc:
            if isinstance(exc, requests.HTTPError) and getattr(exc.response, "status_code", None) not in (404, 410):
                raise
            logging.warning("书架中的一个条目当前不可网页阅读，本轮跳过。")
            active.pop(index)
            if not active:
                raise RuntimeError("书架没有可继续读取的电子书，停止本次任务。") from exc
            index %= len(active)
            continue
        if result.completed != 1 or not 0 < result.acknowledged_seconds <= INTERVAL_SECONDS:
            raise RuntimeError("书架单段返回结果无效，停止累计。")
        completed += 1
        seconds += result.acknowledged_seconds
        visited.add(result.book_id)
        index = (index + 1) % len(active)
        save_reading_cursor(active[index].book_id)
        refresh_print(f"书架循环：已确认 {completed}/{count} 段，涉及 {len(visited)} 本，提交实际等待 {seconds} 秒；刚完成 {label}，进度已核对")
    return ReadingResult("", "个人书架循环", completed, seconds, mode="shelf", books_read=len(visited))


def main(check_auth=False, book_url=None, read_num=None):
    refresh_print = setup_logging()
    client = None
    try:
        client = WeReadSession(headers, cookies)
        recover_pending(client, enter_book)
        if check_auth:
            refresh_cookie(client)
            logging.info("登录续期检查通过，未执行阅读或消息推送。")
            return 0
        result = run_reading(client, refresh_print, book_url, read_num)
        if result.mode == "shelf":
            logging.info("书架循环任务结束；已确认 %s 段，涉及 %s 本，提交实际等待 %s 秒。",
                         result.completed, result.books_read, result.acknowledged_seconds)
            label = f"个人书架循环：{result.books_read} 本"
        else:
            logging.info("固定书籍任务结束；已确认 %s 段，提交实际等待 %s 秒。服务端是否计入请以客户端记录为准。",
                         result.completed, result.acknowledged_seconds)
            label = f"书籍：{result.title}\nbookId：{result.book_id}"
        push(f"微信读书任务完成。\n{label}\n"
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
    def interrupted(signum, frame):
        raise RuntimeError("收到停止信号，正在核对并保护原始阅读进度。")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-auth", action="store_true", help="仅检查登录续期，不执行阅读或推送")
    parser.add_argument("--book-url", help="阅读器链接或数字 bookId；未指定且 WXREAD_BOOK_URL 为空时循环个人书架")
    parser.add_argument("--read-num", type=int, help="本次真实等待周期数（1–600），默认 READ_NUM")
    args = parser.parse_args()
    raise SystemExit(main(args.check_auth, args.book_url, args.read_num))

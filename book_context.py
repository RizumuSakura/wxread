"""Resolve one book and its own chapter/position from the logged-in reader."""
import json
import math
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from reader_protocol import app_id, encode_id


DEFAULT_BOOK_URL = "https://weread.qq.com/web/reader/8b9329607186dc198b9bdab"
CATALOG_URL = "https://weread.qq.com/web/book/chapterInfos"


class BookUnavailableError(RuntimeError):
    """A known unavailable book can be skipped in shelf mode."""


def reader_url(reference):
    value = str(reference).strip()
    if value.isascii() and value.isdigit():
        return "https://weread.qq.com/web/reader/" + encode_id(value)
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RuntimeError("书籍链接格式无效。") from exc
    if parsed.scheme != "https" or parsed.netloc != "weread.qq.com" or not re.fullmatch(r"/web/reader/[A-Za-z0-9]+", parsed.path) or parsed.query or parsed.fragment:
        raise RuntimeError("WXREAD_BOOK_URL 必须是微信读书阅读器链接或数字 bookId。")
    return value


@dataclass(frozen=True)
class BookContext:
    book_id: str
    title: str
    url: str
    chapter_uid: int
    chapter_idx: int
    offset: int
    progress: float
    summary: str
    ps: str
    pc: str
    application_id: str
    token: str
    chapter_title: str = ""
    original: dict = None

    def position(self, now):
        return {"appId": self.application_id, "b": encode_id(self.book_id), "c": encode_id(self.chapter_uid),
                "ci": self.chapter_idx, "co": self.offset, "sm": self.summary[:20], "pr": self.progress,
                "ct": now, "ps": self.ps, "pc": self.pc}


def read_reader(client, reference):
    url = reader_url(reference)
    response = client.get(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    response.raise_for_status()
    marker = re.search(r"window\.__INITIAL_STATE__\s*=\s*", response.text)
    if marker is None:
        raise RuntimeError("阅读器没有返回会话上下文，请重新扫码登录。")
    try:
        state, _ = json.JSONDecoder().raw_decode(response.text[marker.end():])
        reader = state["reader"]
        if reader.get("isBookInfoError") or reader.get("isBookForbidden"):
            raise BookUnavailableError("该书当前不可在网页阅读。")
        info = reader["bookInfo"]
        book_id = str(info["bookId"])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", book_id):
            raise ValueError("invalid book ID")
        if urlsplit(url).path.split("/")[-1].split("k", 1)[0] != encode_id(book_id):
            raise ValueError("book ID mismatch")
        progress = reader.get("progress") or {}
        progress = progress.get("book", progress)
        return url, reader, info, book_id, progress
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("阅读器上下文缺失或与目标书籍不一致，未上报阅读时长。") from exc


def saved_position(progress):
    try:
        uid, offset, percent = progress["chapterUid"], progress["chapterOffset"], progress["progress"]
        if (isinstance(uid, bool) or isinstance(offset, bool) or isinstance(percent, bool)
                or not isinstance(uid, int) or uid < 0 or not isinstance(offset, int) or offset < 0
                or not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 0 <= percent <= 100):
            raise ValueError("invalid position")
        return {"chapterUid": uid, "chapterOffset": offset, "progress": percent,
                "summary": str(progress.get("summary") or ""), "appId": str(progress.get("appId") or ""),
                "updateTime": progress.get("updateTime", 0)}
    except (KeyError, TypeError, ValueError) as exc:
        raise BookUnavailableError("没有可完整恢复的原始阅读进度，本轮跳过；请先在客户端打开该书。") from exc


def read_position(client, reference):
    _, _, _, book_id, progress = read_reader(client, reference)
    return book_id, saved_position(progress)


def load_context(client, reference, position=None):
    url, reader, info, book_id, progress = read_reader(client, reference)
    original = saved_position(progress)
    progress = saved_position(position) if position is not None else original
    try:
        ps = reader["psvts"]
        if (not isinstance(ps, str) or not ps or not isinstance(reader.get("token"), str) or not reader["token"]):
            raise ValueError("missing page session")
        # The official reader sets pclts on mount from Date.now()/1000. SSR
        # legitimately starts it at 0; this is client time, not a server secret.
        pc = encode_id(int(time.time()))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("阅读器上下文缺失或与目标书籍不一致，未上报阅读时长。") from exc
    catalog = client.post(CATALOG_URL, {"bookIds": [book_id]})
    catalog.raise_for_status()
    try:
        records = catalog.json()
        if isinstance(records, dict) and records.get("errCode", records.get("errcode")) not in (None, 0, "0"):
            raise RuntimeError("目标书籍目录接口返回错误，停止本次任务。")
        if isinstance(records, dict) and "data" in records:
            records = records["data"]
        records = [records] if isinstance(records, dict) else records
        record = next(item for item in records if str(item.get("bookId")) == book_id)
        chapters = record.get("updated") or record.get("chapterInfos") or record.get("chapters") or []
        wanted = progress["chapterUid"]
        chapter = next((item for item in chapters if str(item.get("chapterUid")) == str(wanted)), None)
        if chapter is None:
            raise BookUnavailableError("原始阅读章节不在当前目录中，本轮跳过，未改到第一章。")
        offset, percent, summary = progress["chapterOffset"], progress["progress"], progress["summary"]
        return BookContext(book_id, str(info.get("title") or book_id), url,
                           int(chapter["chapterUid"]), int(chapter.get("chapterIdx") or 0), offset, percent, summary,
                           ps, pc,
                           app_id(client.http.headers.get("User-Agent", "")), reader["token"],
                           str(chapter.get("title") or chapter.get("chapterTitle") or ""), original)
    except StopIteration as exc:
        raise BookUnavailableError("该书没有可用于网页阅读的章节。") from exc
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("无法取得目标书籍的有效章节，未使用其他书籍或旧模板代替。") from exc

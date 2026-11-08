"""Official WeRead Skill /shelf/sync integration; no query/statistics CLI."""
from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit, urlunsplit

import requests

from book_context import reader_url
from login_store import load_login
from reader_protocol import encode_id


GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.5"


@dataclass(frozen=True)
class ShelfBook:
    book_id: str
    reference: str
    title: str = ""
    private: bool = True


@dataclass
class ShelfSnapshot:
    books: list
    ebook_entries: int
    albums: int
    article_collection: int
    unsupported_entries: int


def api_key():
    value = os.getenv("WEREAD_API_KEY", "").strip()
    if not value:
        login = load_login()
        value = (login.get("api_key", "") if login else "").strip()
    if not value:
        raise RuntimeError("书架循环需要 AutoRead Secret：WEREAD_API_KEY。")
    if not value.startswith("wrk-") or "\n" in value or "\r" in value:
        raise RuntimeError("WEREAD_API_KEY 格式无效，请更新官方 Skill API Key。")
    return value


class _BearerAuth(requests.auth.AuthBase):
    def __init__(self, value):
        self._value = value

    def __call__(self, request):
        request.headers["Authorization"] = "Bearer " + self._value
        return request


def load_shelf(session=None):
    auth = _BearerAuth(api_key())
    http = session or requests.Session()
    try:
        try:
            response = http.post(GATEWAY, json={"api_name": "/shelf/sync", "skill_version": SKILL_VERSION},
                                 auth=auth, timeout=(10, 30), allow_redirects=False)
        except requests.RequestException as exc:
            raise RuntimeError("官方书架接口连接失败，请稍后重试。") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("官方 Skill API Key 鉴权失败，请更新 WEREAD_API_KEY；不需要为此重新扫码。")
        if not response.ok:
            raise RuntimeError(f"官方书架接口返回 HTTP {response.status_code}，已停止。")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("unexpected response")
            if "upgrade_info" in result:
                raise RuntimeError("官方 Skill 要求升级，请先核对 Tencent/WeChatReading 的 SKILL.md，再继续运行。")
            if result.get("errcode", result.get("errCode")) not in (None, 0, "0"):
                raise RuntimeError("官方书架接口返回业务错误，已停止。")
            data = result.get("data", result)
            records, albums = data.get("books", []), data.get("albums", [])
            if not isinstance(records, list) or not isinstance(albums, list):
                raise ValueError("unexpected shelf arrays")
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("官方书架接口返回了非预期的数据结构。") from exc
        selected, seen, unsupported = [], set(), 0
        for record in records:
            if not isinstance(record, dict):
                unsupported += 1
                continue
            book_id = str(record.get("bookId", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", book_id):
                unsupported += 1
                continue
            reference = record.get("readerLink")
            if reference:
                try:
                    parsed = urlsplit(str(reference))
                    reference = reader_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
                    if parsed.path.rsplit("/", 1)[-1].split("k", 1)[0] != encode_id(book_id):
                        raise RuntimeError("book/link mismatch")
                except (ValueError, RuntimeError):
                    unsupported += 1
                    continue
            elif book_id.isascii() and book_id.isdigit():
                # Internal reader resolution from a known ID; no fabricated links are displayed.
                reference = book_id
            else:
                unsupported += 1
                continue
            if book_id not in seen:
                seen.add(book_id)
                selected.append(ShelfBook(book_id, reference, str(record.get("title") or ""),
                                          record.get("secret") not in (0, "0")))
        if not selected:
            raise RuntimeError("个人书架没有可用于网页阅读的电子书；可在 book_url 指定书籍。")
        return ShelfSnapshot(selected, len(records), len(albums), int(bool(data.get("mp"))), unsupported)
    finally:
        if session is None:
            http.close()

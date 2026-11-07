"""Read-only book metadata and personal statistics through WeRead's official Skill."""

import argparse
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests
from login_store import load_login


GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.5"
TIMEOUT = (10, 30)
READ_APIS = {"/book/info", "/book/getprogress", "/readdata/detail"}
READER_PATH = re.compile(r"/web/reader/[a-zA-Z0-9]+/?\Z")
SHANGHAI = timezone(timedelta(hours=8))


class _BookMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.book = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("type") == "application/ld+json"
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != "script" or not self.active:
            return
        self.active = False
        try:
            document = json.loads("".join(self.parts))
        except ValueError:
            return
        records = document if isinstance(document, list) else [document]
        for record in records:
            if not isinstance(record, dict) or record.get("@type") != "Book":
                continue
            identifier = record.get("@Id", record.get("@id", record.get("identifier")))
            if identifier is not None and re.fullmatch(r"[0-9]+", str(identifier)):
                self.book = {"book_id": str(identifier), "title": record.get("name"), "isbn": record.get("isbn")}


def resolve_book(reference, fetch=requests.get):
    """Read a numeric ID from public JSON-LD, never execute page JavaScript."""
    reference = str(reference).strip()
    if re.fullmatch(r"[0-9]+", reference):
        return {"book_id": reference}
    try:
        parsed = urlsplit(reference)
    except ValueError as exc:
        raise RuntimeError("书籍链接格式无效。") from exc
    if (parsed.scheme != "https" or parsed.netloc != "weread.qq.com"
            or not READER_PATH.fullmatch(parsed.path) or parsed.query or parsed.fragment):
        raise RuntimeError("请输入数字 bookId，或完整的 https://weread.qq.com/web/reader/... 链接。")
    try:
        # Explicit auth prevents a netrc entry from turning this into a login request.
        response = fetch(reference, timeout=TIMEOUT, allow_redirects=False, auth=lambda request: request)
        if response.status_code != 200:
            raise RuntimeError(f"公开书籍页面无法读取，HTTP {response.status_code}。")
    except requests.RequestException as exc:
        raise RuntimeError("公开书籍页面连接失败，请稍后重试。") from exc
    parser = _BookMetadata()
    parser.feed(response.text)
    if parser.book is None:
        raise RuntimeError("页面未提供有效的书籍元数据，无法确认 bookId；未猜测或提交任何阅读记录。")
    return {**parser.book, "reader_url": reference}


class _BearerAuth(requests.auth.AuthBase):
    def __init__(self, key):
        self.key = key

    def __call__(self, request):
        request.headers["Authorization"] = "Bearer " + self.key
        return request


class OfficialSkill:
    def __init__(self, key=None, session=None):
        key = key or os.getenv("WEREAD_API_KEY")
        if not key:
            login = load_login()
            key = login.get("api_key") if login else None
        if not key or not key.strip():
            raise RuntimeError("缺少 WEREAD_API_KEY，请在受保护的环境变量中配置微信读书官方 API Key。")
        self.session = session or requests.Session()
        self.auth = _BearerAuth(key.strip())

    def read(self, api_name, **params):
        if api_name not in READ_APIS:
            raise RuntimeError("此命令仅支持书籍信息、个人进度和阅读统计查询。")
        try:
            response = self.session.post(
                GATEWAY, json={**params, "api_name": api_name, "skill_version": SKILL_VERSION},
                auth=self.auth, timeout=TIMEOUT, allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError("官方 Skill 连接失败，请稍后重试。") from exc
        if response.status_code in (401, 403):
            raise RuntimeError("官方 API Key 鉴权失败，请在官方 Skill 页面检查授权并更新 WEREAD_API_KEY。")
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"官方 Skill 请求失败，HTTP {response.status_code}。")
        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError("官方 Skill 返回非 JSON 响应。") from exc
        if not isinstance(result, dict):
            raise RuntimeError("官方 Skill 返回了非预期的数据结构。")
        if "upgrade_info" in result:
            raise RuntimeError("官方 Skill 要求升级；请先核对官方 SKILL.md 的版本，再继续查询。")
        if result.get("errcode") not in (None, 0, "0"):
            raise RuntimeError("官方 Skill 返回业务错误，未获取有效数据；请检查授权和请求参数。")
        data = result.get("data", result)
        if not isinstance(data, dict):
            raise RuntimeError("官方 Skill 没有返回可用的数据对象。")
        return data

    def book(self, book_id):
        info = self.read("/book/info", bookId=str(book_id))
        result = self.read("/book/getprogress", bookId=str(book_id))
        if (str(info.get("bookId", book_id)) != str(book_id)
                or str(result.get("bookId", book_id)) != str(book_id)):
            raise RuntimeError("官方返回的书籍标识与请求不一致，未展示可能属于其他书籍的进度。")
        position = result.get("book")
        position = position if isinstance(position, dict) else {}
        progress = _number(position.get("progress"))
        progress = progress if progress is not None and 0 <= progress <= 100 else None
        seconds = _number(position.get("recordReadingTime"))
        return {
            "book_id": str(book_id), "title": info.get("title"), "author": info.get("author"),
            "reader_url": info.get("readerLink"), "progress_percent": progress,
            "recorded_seconds": seconds, "recorded_time": _duration(seconds),
            "updated_on": _date(position.get("updateTime")),
        }

    def stats(self, mode="monthly"):
        if mode not in {"weekly", "monthly", "annually", "overall"}:
            raise RuntimeError("统计维度无效。")
        data = self.read("/readdata/detail", mode=mode, baseTime=0)
        seconds = _number(data.get("totalReadTime"))
        return {
            "mode": mode, "total_seconds": seconds, "total_time": _duration(seconds),
            "read_days": _number(data.get("readDays")),
            "period_start": _date(data.get("baseTime")) if mode != "overall" else None,
        }

    def close(self):
        self.session.close()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return value


def _duration(seconds):
    if seconds is None:
        return "未返回"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}小时{minutes}分钟{seconds}秒"


def _date(timestamp):
    timestamp = _number(timestamp)
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(timestamp, SHANGHAI).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="微信读书官方 Skill 的独立查询工具，不提交阅读记录")
    commands = parser.add_subparsers(dest="command", required=True)
    book = commands.add_parser("book", help="按阅读器链接或数字 bookId 查询该书的个人进度")
    book.add_argument("reference")
    book.add_argument("--metadata-only", action="store_true", help="只读取公开书籍元数据，无需 API Key")
    book.add_argument("--json", action="store_true")
    stats = commands.add_parser("stats", help="查询个人阅读统计")
    stats.add_argument("--mode", choices=("weekly", "monthly", "annually", "overall"), default="monthly")
    stats.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    client = None
    try:
        if args.command == "book" and args.metadata_only:
            result = resolve_book(args.reference)
        else:
            client = OfficialSkill()
            if args.command == "book":
                metadata = resolve_book(args.reference)
                result = client.book(metadata["book_id"])
            else:
                result = client.stats(args.mode)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "stats":
            print(f"统计范围：{result['mode']}；累计时长：{result['total_time']}；阅读天数：{result['read_days'] if result['read_days'] is not None else '未返回'}")
        else:
            print(f"书籍：{result.get('title') or '未返回'}；bookId：{result['book_id']}")
            if "progress_percent" in result:
                progress = result["progress_percent"]
                print(f"阅读进度：{str(progress) + '%' if progress is not None else '未返回'}；累计时长：{result['recorded_time']}")
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())

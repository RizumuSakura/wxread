import contextlib
import io
import os
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import requests

from official_skill import GATEWAY, SHANGHAI, OfficialSkill, main, resolve_book


BOOK_ID = "25615385"
READER_URL = "https://weread.qq.com/web/reader/8b9329607186dc198b9bdab"
PAGE = '<script type="application/ld+json">{"@type":"Book","@Id":"25615385","name":"被讨厌的勇气","isbn":"9787111495482"}</script>'


def response(data, status=200):
    reply = Mock()
    reply.status_code = status
    reply.json.return_value = data
    return reply


class BookResolutionTests(unittest.TestCase):
    def test_public_link_resolves_the_actual_book_id_without_following_redirects(self):
        fetch = Mock(return_value=Mock(status_code=200, text=PAGE))
        book = resolve_book(READER_URL, fetch=fetch)
        self.assertEqual(book["book_id"], BOOK_ID)
        self.assertEqual(book["title"], "被讨厌的勇气")
        self.assertEqual(book["reader_url"], READER_URL)
        self.assertFalse(fetch.call_args.kwargs["allow_redirects"])

    def test_numeric_book_id_needs_no_page_request(self):
        fetch = Mock(side_effect=AssertionError("unexpected network request"))
        self.assertEqual(resolve_book(BOOK_ID, fetch=fetch), {"book_id": BOOK_ID})

    def test_unrelated_urls_are_rejected_before_request(self):
        fetch = Mock()
        for url in ("http://weread.qq.com/web/reader/abc", "https://evil.example/web/reader/abc",
                    "https://weread.qq.com@evil.example/web/reader/abc", READER_URL + "?redirect=elsewhere",
                    "https://weread.qq.com:443/web/reader/abc", "https://weread.qq.com/api/private"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                resolve_book(url, fetch=fetch)
        fetch.assert_not_called()

    def test_missing_metadata_does_not_guess_book_id_from_hash(self):
        with self.assertRaisesRegex(RuntimeError, "无法确认 bookId"):
            resolve_book(READER_URL, fetch=Mock(return_value=Mock(status_code=200, text="<html>login</html>")))

    def test_login_redirect_does_not_request_another_page(self):
        fetch = Mock(return_value=Mock(status_code=302, text="redirect"))
        with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
            resolve_book(READER_URL, fetch=fetch)
        self.assertEqual(fetch.call_count, 1)

    def test_metadata_command_needs_no_api_key(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch("official_skill.resolve_book", return_value={"book_id": BOOK_ID, "title": "被讨厌的勇气"}), patch("official_skill.OfficialSkill", side_effect=AssertionError("unexpected authentication")), contextlib.redirect_stdout(output):
            self.assertEqual(main(["book", READER_URL, "--metadata-only"]), 0)
        self.assertIn(BOOK_ID, output.getvalue())


class OfficialSkillTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = OfficialSkill("wrk-test-only-token", session=self.session)

    def test_gateway_request_uses_top_level_parameters_and_bearer_auth(self):
        self.session.post.return_value = response({"bookId": BOOK_ID})
        self.client.read("/book/info", bookId=BOOK_ID)
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], GATEWAY)
        self.assertEqual(kwargs["json"], {"api_name": "/book/info", "skill_version": "1.0.5", "bookId": BOOK_ID})
        self.assertFalse(kwargs["allow_redirects"])
        request = requests.Request("POST", GATEWAY).prepare()
        kwargs["auth"](request)
        self.assertEqual(request.headers["Authorization"], "Bearer wrk-test-only-token")
        self.assertNotIn("Cookie", request.headers)

    def test_missing_api_key_fails_before_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("official_skill.load_login", return_value=None), self.assertRaisesRegex(RuntimeError, "WEREAD_API_KEY"):
            OfficialSkill(session=self.session)
        self.session.post.assert_not_called()

    def test_non_read_api_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.client.read("/book/read", rt=3600)
        self.session.post.assert_not_called()

    def test_upgrade_notice_stops_before_fetching_progress(self):
        self.session.post.return_value = response({"upgrade_info": {"message": "upgrade"}})
        with self.assertRaisesRegex(RuntimeError, "升级"):
            self.client.book(BOOK_ID)
        self.assertEqual(self.session.post.call_count, 1)

    def test_invalid_auth_and_error_bodies_do_not_leak_key(self):
        self.session.post.return_value = response({"message": "wrk-test-only-token"}, status=401)
        with self.assertRaises(RuntimeError) as error:
            self.client.read("/book/info", bookId=BOOK_ID)
        self.assertNotIn("wrk-test-only-token", str(error.exception))
        self.assertIn("鉴权失败", str(error.exception))

    def test_network_error_details_are_not_printed(self):
        self.session.post.side_effect = requests.ConnectionError("private-token-in-exception")
        with self.assertRaises(RuntimeError) as error:
            self.client.read("/book/info", bookId=BOOK_ID)
        self.assertNotIn("private-token", str(error.exception))

    def test_progress_one_means_one_percent_and_duration_is_seconds(self):
        timestamp = int(datetime(2026, 11, 8, tzinfo=SHANGHAI).timestamp())
        self.session.post.side_effect = [
            response({"bookId": BOOK_ID, "title": "被讨厌的勇气", "author": "岸见一郎 古贺史健"}),
            response({"bookId": BOOK_ID, "book": {"progress": 1, "recordReadingTime": 3661, "updateTime": timestamp}}),
        ]
        result = self.client.book(BOOK_ID)
        self.assertEqual(result["progress_percent"], 1)
        self.assertEqual(result["recorded_time"], "1小时1分钟1秒")
        self.assertEqual(result["updated_on"], "2026-11-08")

    def test_missing_progress_is_unknown_not_zero(self):
        self.session.post.side_effect = [response({"bookId": BOOK_ID}), response({})]
        result = self.client.book(BOOK_ID)
        self.assertIsNone(result["progress_percent"])
        self.assertIsNone(result["recorded_seconds"])
        self.assertEqual(result["recorded_time"], "未返回")

    def test_mismatched_progress_book_is_rejected(self):
        self.session.post.side_effect = [response({"bookId": BOOK_ID}), response({"bookId": "different"})]
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            self.client.book(BOOK_ID)

    def test_stats_uses_total_read_time_and_preserves_zero(self):
        self.session.post.return_value = response({"errcode": 0, "data": {"totalReadTime": 60, "readDays": 0, "readTimes": {"one-day": 999}}})
        result = self.client.stats("weekly")
        self.assertEqual(result["total_seconds"], 60)
        self.assertEqual(result["total_time"], "0小时1分钟0秒")
        self.assertEqual(result["read_days"], 0)
        self.assertEqual(self.session.post.call_args.kwargs["json"]["baseTime"], 0)


if __name__ == "__main__":
    unittest.main()

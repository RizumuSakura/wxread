import json
from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import Mock, patch

import requests

from book_context import DEFAULT_BOOK_URL, load_context, reader_url
from reader_protocol import app_id, cal_hash, encode_data, encode_id


def response(value):
    reply = requests.Response()
    reply.status_code = 200
    reply._content = value.encode() if isinstance(value, str) else json.dumps(value).encode()
    return reply


class BookContextTests(unittest.TestCase):
    def setUp(self):
        self.reader = {"bookInfo": {"bookId": "25615385", "title": "被讨厌的勇气"},
                       "progress": {"book": {"chapterUid": 172, "chapterOffset": 389, "progress": 45, "summary": "阅读位置"}},
                       "currentChapter": {"chapterUid": 172}, "psvts": "page-session", "pclts": "page-clock", "token": "page-token"}
        self.catalog = [{"bookId": "other-book", "updated": [{"chapterUid": 999}]},
                        {"bookId": "25615385", "updated": [{"chapterUid": 1, "chapterIdx": 0, "wordCount": 0},
                                                               {"chapterUid": 172, "chapterIdx": 15, "wordCount": 2000}]}]

    def client(self):
        client = Mock()
        client.http.headers = {"User-Agent": "test browser"}
        client.get.return_value = response('<script>window.__INITIAL_STATE__=' + json.dumps({"reader": self.reader}) + ';</script>')
        client.post.return_value = response(self.catalog)
        return client

    def test_numeric_book_id_matches_supplied_reader_url(self):
        self.assertEqual(encode_id("25615385"), "8b9329607186dc198b9bdab")
        self.assertEqual(reader_url("25615385"), DEFAULT_BOOK_URL)

    def test_resolves_book_and_its_own_chapter_and_progress(self):
        client = self.client()
        context = load_context(client, DEFAULT_BOOK_URL)
        self.assertEqual((context.book_id, context.chapter_uid, context.chapter_idx), ("25615385", 172, 15))
        self.assertEqual((context.offset, context.progress, context.summary), (389, 45, "阅读位置"))
        self.assertEqual(context.token, "page-token")
        self.assertEqual(client.post.call_args.args[1], {"bookIds": ["25615385"]})

    def test_position_fields_do_not_contain_other_books_or_duration(self):
        context = load_context(self.client(), DEFAULT_BOOK_URL)
        payload = context.position(1000)
        self.assertEqual(payload["b"], encode_id(context.book_id))
        self.assertEqual(payload["c"], encode_id(context.chapter_uid))
        self.assertNotIn("rt", payload)
        self.assertNotIn("sg", payload)

    def test_unknown_chapter_is_rejected_without_falling_back_to_first(self):
        self.reader["progress"]["book"]["chapterUid"] = 888
        self.reader["currentChapter"]["chapterUid"] = 888
        with self.assertRaisesRegex(RuntimeError, "原始阅读章节"):
            load_context(self.client(), DEFAULT_BOOK_URL)

    def test_fractional_progress_is_preserved_in_snapshot_and_payload(self):
        self.reader["progress"]["book"]["progress"] = 45.1234
        context = load_context(self.client(), DEFAULT_BOOK_URL)
        self.assertEqual(context.original["progress"], 45.1234)
        self.assertEqual(context.position(1)["pr"], 45.1234)

    def test_book_and_progress_fields_are_immutable(self):
        context = load_context(self.client(), DEFAULT_BOOK_URL)
        for field, value in (("book_id", "other"), ("chapter_uid", 1), ("chapter_idx", 0),
                             ("offset", 0), ("progress", 0)):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(context, field, value)

    def test_position_contract_rejects_another_chapter_offset_or_percentage(self):
        context = load_context(self.client(), DEFAULT_BOOK_URL)
        context.require_position(context.original)
        for change in ({"chapter_uid": 1}, {"offset": 0}, {"progress": 0}):
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "原始阅读进度"):
                replace(context, **change).require_position(context.original)

    def test_no_saved_position_is_rejected_before_catalog(self):
        self.reader["progress"] = {}
        client = self.client()
        with self.assertRaises(RuntimeError):
            load_context(client, DEFAULT_BOOK_URL)
        client.post.assert_not_called()

    def test_restore_uses_original_position_with_fresh_page_session(self):
        old = {**self.reader["progress"]["book"], "chapterOffset": 123, "progress": 12.34}
        context = load_context(self.client(), DEFAULT_BOOK_URL, position=old)
        self.assertEqual((context.offset, context.progress), (123, 12.34))
        self.assertEqual(context.ps, "page-session")
        self.assertEqual(context.original["chapterOffset"], 389)

    def test_mismatched_book_is_rejected_before_catalog(self):
        self.reader["bookInfo"]["bookId"] = "999"
        client = self.client()
        with self.assertRaises(RuntimeError):
            load_context(client, DEFAULT_BOOK_URL)
        client.post.assert_not_called()

    def test_missing_page_token_is_not_replaced_with_old_template(self):
        self.reader.pop("token")
        with self.assertRaises(RuntimeError):
            load_context(self.client(), DEFAULT_BOOK_URL)

    def test_client_clock_is_initialized_like_browser_mount_not_required_in_ssr(self):
        for server_value in (0, None, "", "old-client-clock"):
            self.reader["pclts"] = server_value
            with patch("book_context.time.time", return_value=1794090000.75):
                context = load_context(self.client(), DEFAULT_BOOK_URL)
            self.assertEqual(context.pc, encode_id(1794090000))
        self.reader.pop("pclts")
        with patch("book_context.time.time", return_value=1794090000):
            self.assertEqual(load_context(self.client(), DEFAULT_BOOK_URL).pc, encode_id(1794090000))

    def test_server_page_session_is_still_required(self):
        self.reader["psvts"] = ""
        client = self.client()
        with self.assertRaises(RuntimeError):
            load_context(client, DEFAULT_BOOK_URL)
        client.post.assert_not_called()

    def test_missing_target_catalog_is_not_replaced_with_another_book(self):
        self.catalog = self.catalog[:1]
        with self.assertRaises(RuntimeError):
            load_context(self.client(), DEFAULT_BOOK_URL)

    def test_forbidden_book_is_not_reported(self):
        self.reader["isBookForbidden"] = True
        with self.assertRaises(RuntimeError):
            load_context(self.client(), DEFAULT_BOOK_URL)

    def test_untrusted_urls_are_rejected(self):
        for reference in ("https://example.com/web/reader/abc", "http://weread.qq.com/web/reader/abc",
                          "https://weread.qq.com@evil.example/web/reader/abc", DEFAULT_BOOK_URL + "?x=1", "../secret"):
            with self.assertRaises(RuntimeError):
                reader_url(reference)


class EncodingTests(unittest.TestCase):
    def test_entry_request_contains_no_unearned_duration(self):
        import main
        from book_context import BookContext
        context = BookContext("25615385", "title", DEFAULT_BOOK_URL, 172, 15, 389, 45, "位置", "ps", "pc", "app", "token")
        client = Mock()
        client.http.headers = {}
        client.post.return_value = response({"succ": 1})
        main.enter_book(client, context)
        payload = client.post.call_args.args[1]
        self.assertNotIn("rt", payload)
        self.assertNotIn("sg", payload)
        self.assertEqual(payload["b"], "8b9329607186dc198b9bdab")
        self.assertEqual(client.http.headers["Referer"], DEFAULT_BOOK_URL)

    def test_encoding_uses_javascript_encode_uri_component_rules(self):
        self.assertEqual(encode_data({"sm": "中文 A!'()~", "b": "abc", "rt": 30, "s": "excluded"}),
                         "b=abc&rt=30&sm=%E4%B8%AD%E6%96%87%20A!'()~")

    def test_hash_empty_vector(self):
        self.assertEqual(cal_hash(""), "2a0a2a0a")

    def test_integer_valued_float_serializes_like_javascript_number(self):
        self.assertEqual(encode_data({"pr": 45.0}), "pr=45")

    def test_application_id_uses_current_user_agent(self):
        self.assertNotEqual(app_id("first browser"), app_id("second browser"))
        self.assertTrue(app_id("test browser").startswith("wb47h"))


if __name__ == "__main__":
    unittest.main()

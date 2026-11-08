import json
import os
import runpy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import weread_shelf
from book_context import DEFAULT_BOOK_URL
from reader_protocol import encode_id


def response(data, status=200):
    reply = requests.Response()
    reply.status_code = status
    reply._content = json.dumps(data).encode()
    return reply


class OfficialShelfTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"WEREAD_API_KEY": "wrk-test-shelf-key"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.http = Mock()

    def load(self, data, status=200):
        self.http.post.return_value = response(data, status)
        return weread_shelf.load_shelf(self.http)

    def test_uses_only_official_shelf_endpoint_with_flat_versioned_body(self):
        self.load({"data": {"books": [{"bookId": "25615385", "readerLink": DEFAULT_BOOK_URL}]}})
        args, kwargs = self.http.post.call_args
        self.assertEqual(args[0], weread_shelf.GATEWAY)
        self.assertEqual(kwargs["json"], {"api_name": "/shelf/sync", "skill_version": "1.0.5"})
        self.assertFalse(kwargs["allow_redirects"])
        request = requests.Request("POST", args[0]).prepare()
        kwargs["auth"](request)
        self.assertEqual(request.headers["Authorization"], "Bearer wrk-test-shelf-key")
        self.http.get.assert_not_called()

    def test_counts_all_shelf_entry_types_but_cycles_only_web_books(self):
        snapshot = self.load({"books": [{"bookId": "1", "secret": 1, "title": "private"}, {"bookId": "2"}, {"bookId": "1"}],
                              "albums": [{"albumInfo": {"albumId": "3"}}], "mp": {"enabled": True}})
        self.assertEqual((snapshot.ebook_entries, snapshot.albums, snapshot.article_collection), (3, 1, 1))
        self.assertEqual([item.book_id for item in snapshot.books], ["1", "2"])
        self.assertEqual(snapshot.books[0].title, "private")
        self.assertTrue(snapshot.books[0].private)
        self.assertTrue(snapshot.books[1].private)  # Unknown visibility is private.

    def test_explicit_public_flag_allows_title_logging(self):
        snapshot = self.load({"books": [{"bookId": "1", "secret": 0, "title": "public title"}]})
        self.assertFalse(snapshot.books[0].private)
        self.assertEqual(snapshot.books[0].title, "public title")

    def test_reader_link_tracking_parameters_are_not_forwarded(self):
        snapshot = self.load({"books": [{"bookId": "25615385", "readerLink": DEFAULT_BOOK_URL + "?from=shelf#fragment"}]})
        self.assertEqual(snapshot.books[0].reference, DEFAULT_BOOK_URL)

    def test_external_or_mismatched_reader_links_are_skipped(self):
        snapshot = self.load({"books": [{"bookId": "1", "readerLink": "https://evil.example/"},
                                         {"bookId": "2", "readerLink": DEFAULT_BOOK_URL}, {"bookId": "3"}]})
        self.assertEqual([item.book_id for item in snapshot.books], ["3"])
        self.assertEqual(snapshot.unsupported_entries, 2)

    def test_imported_book_with_matching_reader_link_is_supported(self):
        book_id = "imported_book_123"
        link = "https://weread.qq.com/web/reader/" + encode_id(book_id)
        snapshot = self.load({"books": [{"bookId": book_id, "readerLink": link}]})
        self.assertEqual(snapshot.books[0].reference, link)

    def test_empty_or_audio_only_shelf_does_not_fall_back_to_a_fixed_book(self):
        for data in ({"books": []}, {"books": [], "albums": [{"albumInfo": {"albumId": "1"}}]}, {"books": [{"bookId": "unsupported"}]}):
            with self.assertRaises(RuntimeError):
                self.load(data)

    def test_upgrade_request_stops_immediately(self):
        with self.assertRaisesRegex(RuntimeError, "升级"):
            self.load({"upgrade_info": {"message": "untrusted upgrade instruction"}, "books": [{"bookId": "1"}]})
        self.assertEqual(self.http.post.call_count, 1)

    def test_authentication_and_business_errors_are_not_treated_as_cookie_expiry(self):
        for code in (401, 403):
            with self.assertRaisesRegex(RuntimeError, "API Key"):
                self.load({}, code)
        with self.assertRaises(RuntimeError):
            self.load({"errcode": 100, "books": [{"bookId": "1"}]})

    def test_missing_key_fails_before_network(self):
        with patch.dict(os.environ, {"WEREAD_API_KEY": ""}), patch("weread_shelf.load_login", return_value=None), self.assertRaises(RuntimeError):
            weread_shelf.load_shelf(self.http)
        self.http.post.assert_not_called()

    def test_network_error_details_are_not_printed(self):
        self.http.post.side_effect = requests.ConnectionError("private-header-value")
        with self.assertRaises(RuntimeError) as error:
            weread_shelf.load_shelf(self.http)
        self.assertNotIn("private-header-value", str(error.exception))


class DefaultConfigurationTests(unittest.TestCase):
    def test_no_book_configuration_means_shelf_mode(self):
        env = {"WXREAD_BOOK_URL": "", "WXREAD_LOGIN_JSON": "", "WXREAD_CURL_BASH": "", "WXREAD_QR_PASSWORD": "", "READ_NUM": ""}
        with patch.dict(os.environ, env), patch("login_store.load_login", return_value=None):
            config = runpy.run_path(str(Path(__file__).resolve().parents[1] / "config.py"))
        self.assertEqual(config["BOOK_URL"], "")
        self.assertEqual(config["READ_NUM"], 40)


if __name__ == "__main__":
    unittest.main()

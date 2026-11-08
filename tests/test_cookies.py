import contextlib
import importlib
import io
import json
import os
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from requests.adapters import BaseAdapter, HTTPAdapter
from urllib3._collections import HTTPHeaderDict
from urllib3.response import HTTPResponse

import config
import main
from cookie_store import CookieStore
from weread_session import RENEW_URL, WeReadSession


INITIAL = {"wr_vid": "123456789", "wr_skey": "original-session", "wr_rt": "original-refresh-token-with-high-entropy"}


class CookieAdapter(BaseAdapter):
    """Real Requests CookieJar extraction with queued HTTP responses, no network."""
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        status, body, set_cookies = reply
        headers = HTTPHeaderDict({"Content-Type": "application/json"})
        message = Message()
        for value in set_cookies:
            headers.add("Set-Cookie", value)
            message.add_header("Set-Cookie", value)
        raw = HTTPResponse(
            body=io.BytesIO(json.dumps(body).encode()), status=status, headers=headers,
            original_response=SimpleNamespace(msg=message, isclosed=lambda: True, close=lambda: None),
            preload_content=False,
        )
        return HTTPAdapter().build_response(request, raw)

    def close(self):
        pass


class CurlTests(unittest.TestCase):
    def test_cookie_header_without_spaces(self):
        headers, cookies = config.convert("curl 'https://weread.qq.com/' -H 'Cookie: wr_vid=1;wr_skey=abc; wr_rt=a=b==' -H 'user-agent: browser'")
        self.assertEqual(cookies, {"wr_vid": "1", "wr_skey": "abc", "wr_rt": "a=b=="})
        self.assertNotIn("cookie", headers)
        self.assertEqual(headers["user-agent"], "browser")

    def test_double_quotes_and_long_options(self):
        headers, cookies = config.convert('curl "https://weread.qq.com/" --header="accept: application/json" --cookie "wr_skey=complete-token;wr_rt=refresh"')
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(cookies["wr_skey"], "complete-token")

    def test_multiline_and_repeated_cookie_options(self):
        command = "curl 'https://weread.qq.com/' \\\n -H 'cookie: wr_vid=1; wr_skey=old' \\\n -b 'wr_skey=new;wr_rt=refresh' --cookie='other=kept'"
        _, cookies = config.convert(command)
        self.assertEqual(cookies, {"wr_vid": "1", "wr_skey": "new", "wr_rt": "refresh", "other": "kept"})

    def test_stale_transport_headers_are_removed(self):
        headers, _ = config.convert("curl x -H 'Content-Length: 123' -H 'Host: stale' -b 'wr_skey=x'")
        self.assertNotIn("content-length", headers)
        self.assertNotIn("host", headers)

    def test_missing_option_value_is_rejected(self):
        with self.assertRaises(ValueError):
            config.convert("curl x --cookie")


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cookies.enc"

    def client(self, replies=(), initial=None):
        client = WeReadSession({"Cookie": "stale", "Content-Length": "999"}, initial or INITIAL, self.path)
        adapter = CookieAdapter(replies)
        client.http.mount("https://weread.qq.com/", adapter)
        self.addCleanup(client.close)
        return client, adapter

    def test_all_rotated_cookies_are_full_length_and_used_next_request(self):
        client, adapter = self.client([
            (200, {"succ": 1}, [
                "wr_skey=complete-new-session-token; Domain=weread.qq.com; Path=/; Secure; HttpOnly",
                "wr_rt=rotated-refresh-token; Domain=.weread.qq.com; Path=/; Secure",
                "other=new-cookie; Domain=.weread.qq.com; Path=/",
            ]),
            (200, {"succ": 1}, []),
        ])
        self.assertTrue(client.renew())
        client.post(main.READ_URL, {})
        sent = adapter.requests[-1].headers["Cookie"]
        self.assertIn("wr_skey=complete-new-session-token", sent)
        self.assertIn("wr_rt=rotated-refresh-token", sent)
        self.assertIn("other=new-cookie", sent)
        self.assertEqual(sent.count("wr_skey="), 1)
        self.assertNotIn("original-session", sent)
        self.assertNotIn("original-refresh", sent)

    def test_rotations_survive_a_new_actions_process_with_original_secret(self):
        client, _ = self.client([(200, {"succ": 1}, ["wr_rt=rotated-refresh; Path=/", "wr_skey=long-new-session; Path=/"])])
        client.renew()
        restored, adapter = self.client([(200, {"succ": 1}, [])])
        restored.post(main.READ_URL, {})
        self.assertIn("wr_rt=rotated-refresh", adapter.requests[0].headers["Cookie"])
        self.assertIn("wr_skey=long-new-session", adapter.requests[0].headers["Cookie"])

    def test_read_responses_also_rotate_and_persist_cookies(self):
        client, _ = self.client([(200, {"succ": 1, "synckey": 1}, ["wr_rt=read-rotated-refresh; Path=/"])])
        client.post(main.READ_URL, {})
        restored, _ = self.client()
        self.assertEqual(restored.http.cookies.get("wr_rt"), "read-rotated-refresh")

    def test_cookie_is_saved_even_when_read_response_is_an_error(self):
        client, _ = self.client([(500, {"errCode": 500}, ["wr_rt=already-rotated; Path=/"])])
        client.post(main.READ_URL, {})
        self.assertEqual(CookieStore(INITIAL, self.path).load().get("wr_rt"), "already-rotated")

    def test_network_failure_does_not_erase_saved_rotation(self):
        client, _ = self.client([(200, {"succ": 1}, ["wr_rt=rotated; Path=/"]), requests.ConnectionError("offline")])
        client.renew()
        with self.assertRaises(requests.ConnectionError):
            client.post(main.READ_URL, {})
        self.assertEqual(CookieStore(INITIAL, self.path).load().get("wr_rt"), "rotated")

    def test_changing_initial_secret_resets_state_and_namespace(self):
        client, _ = self.client([(200, {"succ": 1}, ["wr_skey=old-account-state; Path=/"])])
        client.renew()
        updated = {**INITIAL, "wr_skey": "new-login-session", "wr_rt": "new-login-refresh"}
        with self.assertLogs(level="WARNING"):
            restored, _ = self.client(initial=updated)
        self.assertNotEqual(restored.store.cache_id, client.store.cache_id)
        self.assertEqual(restored.http.cookies.get("wr_skey"), "new-login-session")

    def test_state_is_encrypted_and_retains_expiry(self):
        client, _ = self.client([(200, {"succ": 1}, ["wr_rt=private-rotated-refresh; Path=/; Max-Age=2592000; Secure; HttpOnly"])])
        client.renew()
        encrypted = self.path.read_bytes()
        self.assertNotIn(b"private-rotated-refresh", encrypted)
        self.assertNotIn(b"original-session", encrypted)
        restored = CookieStore(INITIAL, self.path).load()
        cookie = next(cookie for cookie in restored if cookie.name == "wr_rt")
        self.assertGreater(cookie.expires, time.time() + 2591900)
        self.assertTrue(cookie.secure)
        self.assertIn("HttpOnly", cookie._rest)

    def test_corrupt_cache_falls_back_without_logging_contents(self):
        self.path.write_bytes(b"invalid-cache-secret-contents")
        with self.assertLogs(level="WARNING") as logs:
            client, _ = self.client()
        self.assertEqual(client.http.cookies.get("wr_skey"), INITIAL["wr_skey"])
        self.assertNotIn("invalid-cache-secret-contents", "".join(logs.output))

    def test_cookie_deletion_does_not_resurrect_seed_cookie(self):
        for deletion in ("Max-Age=0", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"):
            with self.subTest(deletion=deletion):
                self.path.unlink(missing_ok=True)
                client, _ = self.client([(200, {"succ": 1}, [f"wr_rt=; Path=/; {deletion}"])])
                client.renew()
                self.assertIsNone(client.http.cookies.get("wr_rt"))
                self.assertIsNone(CookieStore(INITIAL, self.path).load().get("wr_rt"))

    def test_atomic_failure_keeps_previous_encrypted_state(self):
        client, _ = self.client([(200, {"succ": 1}, [])])
        client.renew()
        original = self.path.read_bytes()
        with patch("cookie_store.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                client.store.save(client.http.cookies)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_success_without_new_skey_is_not_misreported_as_expired(self):
        client, adapter = self.client([(200, {"succ": 1}, [])])
        self.assertTrue(client.renew())
        self.assertEqual(len(adapter.requests), 1)

    def test_renewal_retries_are_bounded(self):
        client, adapter = self.client([(200, {"errCode": -2012}, [])] * 3)
        self.assertFalse(client.renew())
        self.assertEqual(len(adapter.requests), 3)

    def test_rejected_renewal_keeps_original_cookie_and_encrypted_state(self):
        client, adapter = self.client([(200, {"errCode": -2012}, [
            "wr_skey=rejected-session; Path=/",
            "wr_rt=; Path=/; Max-Age=0",
        ])] * 3)
        client.store.save(client.http.cookies)
        original = self.path.read_bytes()
        self.assertFalse(client.renew())
        self.assertEqual(client.http.cookies.get("wr_skey"), INITIAL["wr_skey"])
        self.assertEqual(client.http.cookies.get("wr_rt"), INITIAL["wr_rt"])
        self.assertEqual(self.path.read_bytes(), original)
        for request in adapter.requests:
            self.assertIn("wr_skey=" + INITIAL["wr_skey"], request.headers["Cookie"])
            self.assertIn("wr_rt=" + INITIAL["wr_rt"], request.headers["Cookie"])

    def test_first_failed_renewal_never_writes_unconfirmed_credentials(self):
        client, _ = self.client([(401, {"succ": 0}, ["wr_rt=unconfirmed; Path=/"])] * 3)
        self.assertFalse(client.renew())
        self.assertFalse(self.path.exists())
        self.assertEqual(client.http.cookies.get("wr_rt"), INITIAL["wr_rt"])

    def test_non_json_renewal_rolls_back_response_cookies(self):
        client, _ = self.client()
        response = requests.Response()
        response.status_code = 200
        response._content = b"not JSON"
        response.cookies.set("wr_skey", "unconfirmed", domain="weread.qq.com", path="/")
        with patch.object(client.http, "post", return_value=response):
            self.assertFalse(client.renew())
        self.assertEqual(client.http.cookies.get("wr_skey"), INITIAL["wr_skey"])
        self.assertFalse(self.path.exists())

    def test_network_failure_during_renewal_rolls_back_in_memory_rotation(self):
        client, _ = self.client()
        def interrupted_request(*args, **kwargs):
            client.http.cookies.set("wr_rt", "unconfirmed", domain=".weread.qq.com", path="/")
            raise requests.ConnectionError("interrupted renewal")
        with patch.object(client.http, "post", side_effect=interrupted_request):
            self.assertFalse(client.renew())
        self.assertEqual(client.http.cookies.get("wr_rt"), INITIAL["wr_rt"])
        self.assertFalse(self.path.exists())

    def test_missing_initial_credentials_fails_before_network(self):
        with self.assertRaisesRegex(RuntimeError, "WXREAD_CURL_BASH"):
            WeReadSession({}, {"wr_vid": "1"}, self.path)

    def test_github_logs_mask_new_credentials_without_masking_short_values(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), contextlib.redirect_stdout(output):
            client, _ = self.client([(200, {"succ": 1}, ["wr_skey=new-session-token; Path=/"])], {**INITIAL, "wr_gender": "0"})
            client.renew()
        self.assertIn("::add-mask::new-session-token", output.getvalue())
        self.assertNotIn("::add-mask::0\n", output.getvalue())


class ReadingTests(unittest.TestCase):
    def setUp(self):
        from book_context import BookContext
        self.context = BookContext("25615385", "被讨厌的勇气", "https://weread.qq.com/web/reader/8b9329607186dc198b9bdab",
                                   172, 15, 389, 45, "真实服务端位置", "page-session", "page-clock", "wb-test", "token",
                                   original={"chapterUid": 172, "chapterOffset": 389, "progress": 45})
        self.clock = 1000.0
        def advance(seconds):
            self.clock += seconds
        for target, options in (("main.load_context", {"return_value": self.context}),
                                ("main.ProgressGuard", {}),
                                ("main.recover_pending", {}),
                                ("main.BOOK_URL", {"new": self.context.url}),
                                ("main.enter_book", {}),
                                ("main.time.monotonic", {"side_effect": lambda: self.clock}),
                                ("main.time.sleep", {"side_effect": advance})):
            patcher = patch(target, **options)
            self.addCleanup(patcher.stop)
            setattr(self, target.split(".")[-1] + "_mock", patcher.start())

    def response(self, body, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode()
        return response

    def test_repeated_auth_failures_stop_instead_of_looping_forever(self):
        client = Mock()
        client.renew.return_value = True
        client.post.return_value = self.response({"errCode": -2012})
        with patch.object(main, "READ_NUM", 1), self.assertRaisesRegex(RuntimeError, "停止重试"):
            main.run_reading(client, Mock())
        self.assertEqual(client.post.call_count, main.AUTH_RETRY_LIMIT + 1)
        self.assertEqual(client.renew.call_count, 1 + main.AUTH_RETRY_LIMIT)

    def test_business_error_does_not_trigger_login_renewal(self):
        client = Mock()
        client.post.return_value = self.response({"errCode": -9999})
        with self.assertRaisesRegex(RuntimeError, "errCode=-9999"):
            main.run_reading(client, Mock(), count=1)
        self.assertEqual(client.renew.call_count, 1)

    def test_startup_renewal_failure_can_still_use_valid_read_session(self):
        client = Mock()
        client.renew.return_value = False
        client.post.return_value = self.response({"succ": 1, "synckey": 1})
        result = main.run_reading(client, Mock(), count=1)
        self.assertEqual(result.completed, 1)

    def test_succ_zero_is_not_counted_as_success(self):
        client = Mock()
        client.post.return_value = self.response({"succ": 0})
        with self.assertRaises(RuntimeError):
            main.run_reading(client, Mock(), count=1)

    def test_importing_main_has_no_network_side_effect(self):
        with patch("requests.Session.request", side_effect=AssertionError("unexpected request")):
            importlib.reload(main)

    def test_non_json_401_still_attempts_renewal(self):
        client = Mock()
        unauthorized = requests.Response()
        unauthorized.status_code = 401
        unauthorized._content = b"Unauthorized"
        client.post.side_effect = [unauthorized, self.response({"succ": 1, "synckey": 1})]
        self.assertEqual(main.run_reading(client, Mock(), count=1).completed, 1)
        self.assertEqual(client.renew.call_count, 2)

    def test_book_chapter_and_position_remain_consistent(self):
        client = Mock()
        client.post.return_value = self.response({"succ": 1, "synckey": 1})
        result = main.run_reading(client, Mock(), count=3)
        from reader_protocol import encode_id, cal_hash, encode_data
        self.assertEqual(result.book_id, "25615385")
        self.assertEqual(result.acknowledged_seconds, 90)
        for args in client.post.call_args_list:
            payload = args.args[1]
            self.assertEqual(payload["b"], encode_id("25615385"))
            self.assertEqual(payload["c"], encode_id(172))
            self.assertEqual((payload["ci"], payload["co"], payload["pr"]), (15, 389, 45))
            self.assertEqual(payload["rt"], 30)
            self.assertEqual(payload["s"], cal_hash(encode_data(payload)))

    def test_first_report_waits_before_posting(self):
        client = Mock()
        clock_on_post = []
        def reply_after_wait(*args, **kwargs):
            clock_on_post.append(self.clock)
            return self.response({"succ": 1, "synckey": 1})
        client.post.side_effect = reply_after_wait
        main.run_reading(client, Mock(), count=1)
        self.assertEqual(clock_on_post, [1030.0])

    def test_slow_wait_does_not_claim_more_than_one_period(self):
        client = Mock()
        client.post.return_value = self.response({"succ": 1, "synckey": 1})
        def delayed_sleep(seconds):
            self.clock += seconds + 300
        self.sleep_mock.side_effect = delayed_sleep
        result = main.run_reading(client, Mock(), count=1)
        self.assertEqual(result.acknowledged_seconds, 30)
        self.assertEqual(client.post.call_args.args[1]["rt"], 30)

    def test_repair_uses_only_the_target_book(self):
        client = Mock()
        client.post.side_effect = [self.response({"succ": 1}), self.response({}),
                                   self.response({"succ": 1, "synckey": 1})]
        result = main.run_reading(client, Mock(), count=1)
        self.assertEqual(result.completed, 1)
        self.assertEqual(client.post.call_args_list[1].args[1], {"bookIds": ["25615385"]})
        self.assertEqual(self.load_context_mock.call_count, 2)

    def test_invalid_read_count_fails_before_network(self):
        for count in (0, -1, 601, 1.5, True):
            client = Mock()
            with self.assertRaises(RuntimeError):
                main.run_reading(client, Mock(), count=count)
            client.renew.assert_not_called()
            client.post.assert_not_called()

    @patch("main.setup_logging", return_value=Mock())
    @patch("main.push")
    @patch("main.run_reading")
    @patch("main.WeReadSession")
    def test_auth_check_does_not_read_or_push(self, session, reading, push, logging):
        session.return_value.renew.return_value = True
        self.assertEqual(main.main(check_auth=True), 0)
        reading.assert_not_called()
        push.assert_not_called()
        self.load_context_mock.assert_not_called()
        session.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

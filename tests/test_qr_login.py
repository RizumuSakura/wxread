import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from login_store import current_login, load_login, save_login
from cookie_store import CookieStore
from weread_auth import QRLogin, qr_png, device_fingerprint


LOGIN = {"version": 1, "cookies": {"wr_vid": "123456789", "wr_skey": "session-private-value", "wr_rt": "refresh-private-value"},
         "headers": {"User-Agent": "test browser"}, "api_key": "wrk-test-private-value"}


def reply(data, status=200):
    response = Mock()
    response.status_code = status
    response.cookies = requests.cookies.RequestsCookieJar()
    response.json.return_value = data
    return response


class LoginStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "login.enc"
        self.environment = patch.dict(os.environ, {"WXREAD_LOGIN_FILE": str(self.path), "WXREAD_LOGIN_JSON": "", "WXREAD_QR_PASSWORD": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_encrypted_login_roundtrip_does_not_store_plaintext(self):
        save_login(LOGIN)
        value = load_login()
        self.assertEqual(value["cookies"], LOGIN["cookies"])
        self.assertEqual(value["api_key"], LOGIN["api_key"])
        encrypted = self.path.read_bytes()
        for secret in (LOGIN["api_key"], *LOGIN["cookies"].values()):
            self.assertNotIn(secret.encode(), encrypted)

    def test_environment_bootstrap_overrides_local_login(self):
        save_login(LOGIN)
        updated = {**LOGIN, "cookies": {**LOGIN["cookies"], "wr_vid": "987654321"}}
        with patch.dict(os.environ, {"WXREAD_LOGIN_JSON": json.dumps(updated)}):
            self.assertEqual(load_login()["cookies"]["wr_vid"], "987654321")

    def test_bad_environment_value_is_not_echoed(self):
        with patch.dict(os.environ, {"WXREAD_LOGIN_JSON": "invalid-private-value"}), self.assertRaises(RuntimeError) as error:
            load_login()
        self.assertNotIn("invalid-private-value", str(error.exception))

    def test_missing_key_does_not_silently_replace_credentials(self):
        save_login(LOGIN)
        original = self.path.read_bytes()
        self.path.with_suffix(".key").unlink()
        with self.assertRaises(RuntimeError):
            load_login()
        self.assertEqual(self.path.read_bytes(), original)

    def test_fingerprint_is_generated_once_and_qr_is_a_png(self):
        fingerprint = device_fingerprint()
        self.assertEqual(fingerprint, device_fingerprint())
        self.assertTrue(0 <= int(fingerprint) <= 0xffffffff)
        data = qr_png("test-uid")
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_secret_sync_exports_latest_rotated_cookies(self):
        save_login(LOGIN)
        cache_path = Path(self.temp.name) / "cookies.enc"
        jar = requests.cookies.RequestsCookieJar()
        for name, value in {**LOGIN["cookies"], "wr_skey": "rotated-session", "wr_rt": "rotated-refresh"}.items():
            jar.set(name, value, domain=".weread.qq.com", path="/")
        with patch.dict(os.environ, {"WXREAD_COOKIE_FILE": str(cache_path)}):
            CookieStore(LOGIN["cookies"]).save(jar)
            exported = current_login()
        self.assertEqual(exported["cookies"]["wr_skey"], "rotated-session")
        self.assertEqual(exported["cookies"]["wr_rt"], "rotated-refresh")


class QRProtocolTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.http.headers = {}
        self.http.cookies = requests.cookies.RequestsCookieJar()
        self.client = QRLogin("1234567890", session=self.http)

    def test_begin_and_poll_use_the_same_device_cookie(self):
        self.http.get.side_effect = [reply({}), reply({"uid": "session-uid"}), reply({"logicCode": "WAIT_SCAN"})]
        uid = self.client.begin()
        result = self.client.poll(uid)
        self.assertEqual(result["logicCode"], "WAIT_SCAN")
        self.assertEqual(self.http.cookies.get("wr_fp"), "1234567890")
        self.assertEqual(self.http.get.call_args.kwargs["params"], {"uid": "session-uid", "otp": ""})
        self.assertFalse(self.http.get.call_args.kwargs["allow_redirects"])

    def test_network_timeout_is_pending_until_the_outer_deadline(self):
        self.http.get.side_effect = requests.ReadTimeout("waiting")
        self.assertEqual(self.client.poll("uid"), {"pending": True})

    def test_complete_keeps_full_tokens_without_requesting_an_api_key(self):
        self.http.get.return_value = reply({"name": "Test account", "vid": 123456789})
        login = self.client.complete({"succeed": True, "webLoginVid": 123456789, "accessToken": "complete-session-token", "refreshToken": "refresh/+="})
        self.assertEqual(login["cookies"]["wr_skey"], "complete-session-token")
        self.assertEqual(login["cookies"]["wr_rt"], "refresh%2F%2B%3D")
        self.assertEqual(login["cookies"]["wr_fp"], "1234567890")
        self.assertEqual(login["api_key"], "")
        self.assertEqual(self.http.get.call_count, 1)
        self.assertEqual(self.http.get.call_args.args[0], "https://weread.qq.com/api/userInfo")
        self.assertEqual(self.http.get.call_args.kwargs["params"], {"userVid": "123456789"})

    def test_unconfirmed_login_never_fetches_account_credentials(self):
        with self.assertRaises(RuntimeError):
            self.client.complete({"succeed": False, "accessToken": "unconfirmed"})
        self.http.get.assert_not_called()

    def test_web_login_only_requires_account_validation(self):
        self.http.get.return_value = reply({"name": "Test account"})
        login = self.client.complete({"succeed": True, "webLoginVid": 123, "accessToken": "valid-session"})
        self.assertEqual(login["api_key"], "")
        self.assertEqual(login["cookies"]["wr_skey"], "valid-session")
        self.assertEqual(self.http.get.call_count, 1)

    def test_wrong_account_response_is_rejected(self):
        self.http.get.return_value = reply({"name": "Other account", "vid": 999})
        with self.assertRaises(RuntimeError):
            self.client.complete({"succeed": True, "webLoginVid": 123, "accessToken": "valid-session"})

    def test_account_validation_retries_temporary_401_after_phone_confirmation(self):
        self.http.get.side_effect = [reply({}, status=401), reply({"name": "Test account"})]
        with patch("weread_auth.time.sleep"):
            login = self.client.complete({"succeed": True, "webLoginVid": 123, "accessToken": "valid-session"})
        self.assertEqual(login["cookies"]["wr_skey"], "valid-session")
        self.assertEqual(login["api_key"], "")
        self.assertEqual(self.http.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()

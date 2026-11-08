import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import requests

import actions_login
from cloud_crypto import cache_prefix, decrypt, encrypt
from cookie_store import CookieStore
from login_store import load_login, save_login, reading_cursor, save_reading_cursor, save_pending_progress, pending_progress, preserve_metadata


LOGIN = {"version": 1, "cookies": {"wr_vid": "123456789", "wr_skey": "session-private-value", "wr_rt": "refresh-private-value"},
         "headers": {"User-Agent": "test browser"}, "api_key": ""}


class CloudLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "login.enc"
        self.public = self.root / "public"
        environment = {"WXREAD_LOGIN_FILE": str(self.private), "WXREAD_COOKIE_FILE": str(self.root / "cookies.enc"),
                       "WXREAD_LOGIN_JSON": "", "WXREAD_CURL_BASH": "", "WXREAD_QR_PASSWORD": "a-long-random-test-password-123456",
                       "GITHUB_REPOSITORY": "owner/repository", "GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "1",
                       "GITHUB_REF_NAME": "main", "GITHUB_ACTIONS": "true", "WXREAD_ACCOUNT_VID": "",
                       "GITHUB_OUTPUT": str(self.root / "output"), "GITHUB_STEP_SUMMARY": str(self.root / "summary")}
        for patcher in (patch.dict(os.environ, environment), patch.object(actions_login, "PUBLIC_DIR", self.public),
                        patch.object(actions_login, "PENDING_FILE", self.root / "private-session.enc")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clients = []
        def factory(fingerprint):
            client = Mock()
            client.http.cookies = requests.cookies.RequestsCookieJar()
            for name, value in LOGIN["cookies"].items():
                client.http.cookies.set(name, value, domain=".weread.qq.com", path="/")
            client.begin.return_value = "private-qr-session-uid"
            client.complete.return_value = LOGIN
            self.clients.append(client)
            return client
        self.factory_patch = patch.object(actions_login, "QRLogin", side_effect=factory)
        self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)

    def test_envelope_roundtrip_is_bound_to_repository_and_purpose(self):
        data = encrypt(b"private login data", "qr-session")
        self.assertNotIn(b"private login data", data)
        self.assertEqual(decrypt(data, "qr-session"), b"private login data")
        with self.assertRaises(RuntimeError):
            decrypt(data, "login-state")
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/repository"}), self.assertRaises(RuntimeError):
            decrypt(data, "qr-session")

    def test_wrong_password_and_tampered_kdf_fail_closed(self):
        data = encrypt(b"secret", "qr-session")
        with self.assertRaises(RuntimeError):
            decrypt(data, "qr-session", value="wrong password")
        envelope = json.loads(data)
        envelope["iterations"] = 1
        with self.assertRaises(RuntimeError):
            decrypt(json.dumps(envelope).encode(), "qr-session")

    def test_cloud_login_needs_no_local_key_file_and_overrides_bootstrap(self):
        save_login(LOGIN)
        self.assertFalse(self.private.with_suffix(".key").exists())
        other = {**LOGIN, "cookies": {**LOGIN["cookies"], "wr_vid": "999"}}
        with patch.dict(os.environ, {"WXREAD_LOGIN_JSON": json.dumps(other)}):
            self.assertEqual(load_login()["cookies"], LOGIN["cookies"])
        self.assertNotIn(b"session-private-value", self.private.read_bytes())

    def test_cache_namespace_changes_with_password_and_repository(self):
        original = cache_prefix()
        with patch.dict(os.environ, {"WXREAD_QR_PASSWORD": "different-random-test-password"}):
            self.assertNotEqual(cache_prefix(), original)
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "other/repository"}):
            self.assertNotEqual(cache_prefix(), original)

    def test_short_password_is_rejected_before_login(self):
        with patch.dict(os.environ, {"WXREAD_QR_PASSWORD": "short"}), self.assertRaises(RuntimeError):
            actions_login.prepare()
        self.assertFalse(self.clients)

    def test_public_artifact_contains_neither_uid_nor_cookies(self):
        state = actions_login.prepare()
        for target in self.public.iterdir():
            data = target.read_bytes()
            for secret in (state["uid"], *LOGIN["cookies"].values(), os.environ["WXREAD_QR_PASSWORD"]):
                self.assertNotIn(secret.encode(), data)
        html = (self.public / "login.html").read_text(encoding="utf-8")
        self.assertIn("crypto.subtle", html)
        self.assertIn("connect-src 'none'", html)
        self.assertNotIn("__QR_ENVELOPE__", html)
        self.assertEqual({item.name for item in self.public.iterdir()}, {"login.html"})
        self.assertFalse(self.private.exists())

    def test_confirmed_login_saves_encrypted_vault_and_cookie_state(self):
        actions_login.prepare()
        def result(*args):
            return {"succeed": True, "webLoginVid": 123456789}
        client = Mock()
        client.http.cookies = self.clients[0].http.cookies.copy()
        client.poll.side_effect = result
        client.complete.return_value = LOGIN
        with patch.object(actions_login, "QRLogin", return_value=client):
            self.assertEqual(actions_login.wait_for_login(), 0)
        self.assertEqual(load_login()["cookies"], LOGIN["cookies"])
        self.assertIsNotNone(CookieStore(LOGIN["cookies"]).load())
        self.assertFalse(actions_login.PENDING_FILE.exists())
        self.assertIn("authenticated=true", (self.root / "output").read_text())

    def test_wrong_scanned_account_does_not_replace_existing_login(self):
        save_login(LOGIN)
        previous = self.private.read_bytes()
        actions_login.prepare()
        client = Mock()
        client.http.cookies = self.clients[0].http.cookies.copy()
        client.poll.return_value = {"succeed": True, "webLoginVid": 999}
        with patch.object(actions_login, "QRLogin", return_value=client), self.assertRaises(RuntimeError):
            actions_login.wait_for_login()
        client.complete.assert_not_called()
        self.assertEqual(self.private.read_bytes(), previous)

    def test_unexpected_auth_challenge_stops_without_replacing_old_login(self):
        save_login(LOGIN)
        previous = self.private.read_bytes()
        actions_login.prepare()
        client = Mock()
        client.http.cookies = self.clients[0].http.cookies.copy()
        client.poll.return_value = {"logicCode": "NEED_OTP"}
        with patch.object(actions_login, "QRLogin", return_value=client), self.assertRaises(RuntimeError):
            actions_login.wait_for_login()
        self.assertEqual(self.private.read_bytes(), previous)
        self.assertFalse(actions_login.PENDING_FILE.exists())
        self.assertNotIn("needs_otp", (self.root / "output").read_text())

    def test_pending_login_cannot_be_used_by_another_run(self):
        actions_login.prepare()
        with patch.dict(os.environ, {"GITHUB_RUN_ID": "102"}), self.assertRaises(RuntimeError):
            actions_login.wait_for_login()

    def test_expired_state_cannot_be_polled(self):
        state = actions_login.prepare()
        with patch("actions_login.time.time", return_value=state["expires_at"] + 1), self.assertRaises(RuntimeError):
            actions_login.read_pending()

    def test_shelf_cursor_does_not_change_cookie_cache_seed(self):
        save_login(LOGIN)
        jar = requests.cookies.RequestsCookieJar()
        for name, value in LOGIN["cookies"].items():
            jar.set(name, value, domain=".weread.qq.com", path="/")
        original_store = CookieStore(LOGIN["cookies"])
        original_store.save(jar)
        self.assertTrue(save_reading_cursor("25615385"))
        self.assertEqual(reading_cursor(), "25615385")
        updated = load_login()
        restored_store = CookieStore(updated["cookies"])
        self.assertEqual(restored_store.cache_id, original_store.cache_id)
        self.assertEqual(restored_store.load().get("wr_skey"), LOGIN["cookies"]["wr_skey"])

    def test_progress_journal_is_encrypted_and_preserves_cookie_seed(self):
        from book_context import DEFAULT_BOOK_URL
        save_login(LOGIN)
        original_id = CookieStore(LOGIN["cookies"]).cache_id
        record = {"version": 1, "owner": LOGIN["cookies"]["wr_vid"], "book_id": "25615385",
                  "reference": DEFAULT_BOOK_URL, "app_id": "web-test", "last_write": 1000,
                  "position": {"chapterUid": 172, "chapterOffset": 389, "progress": 45.1234}}
        save_pending_progress(record)
        self.assertEqual(pending_progress(), record)
        self.assertNotIn(b"chapterOffset", self.private.read_bytes())
        self.assertEqual(CookieStore(load_login()["cookies"]).cache_id, original_id)
        save_reading_cursor("101")
        self.assertEqual(pending_progress(), record)
        save_pending_progress(None)
        self.assertIsNone(pending_progress())
        self.assertEqual(reading_cursor(), "101")

    def test_relogin_keeps_same_account_cursor_journal_and_api_key(self):
        previous = {**LOGIN, "reading_cursor": "101", "pending_progress": {"pending": True}, "api_key": "wrk-existing"}
        renewed = {**LOGIN, "cookies": {**LOGIN["cookies"], "wr_skey": "new-session"}}
        value = preserve_metadata(renewed, previous)
        for field in ("reading_cursor", "pending_progress", "api_key"):
            self.assertEqual(value[field], previous[field])
        self.assertEqual(value["cookies"]["wr_skey"], "new-session")
        self.assertNotIn("pending_progress", renewed)

    def test_relogin_cannot_transfer_journal_to_another_account(self):
        other = {**LOGIN, "cookies": {**LOGIN["cookies"], "wr_vid": "999"}}
        with self.assertRaises(RuntimeError):
            preserve_metadata(other, LOGIN)

    def test_invalid_journal_cannot_belong_to_other_book_or_account(self):
        from book_context import DEFAULT_BOOK_URL
        record = {"version": 1, "owner": LOGIN["cookies"]["wr_vid"], "book_id": "25615385",
                  "reference": DEFAULT_BOOK_URL, "app_id": "web-test", "last_write": 1000,
                  "position": {"chapterUid": 172, "chapterOffset": 389, "progress": 45}}
        for mutation in ({"owner": "999"}, {"book_id": "101"}, {"reference": "https://evil.example"},
                         *({"last_write": value} for value in (True, -1, float("nan"), float("inf"), "1000")),
                         {"restore_from": {}}, {"restore_from": {**record["position"], "progress": float("nan")}}):
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                save_login({**LOGIN, "pending_progress": {**record, **mutation}})

    def test_command_line_does_not_allow_local_qr_login(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": ""}), contextlib.redirect_stdout(output):
            self.assertEqual(actions_login.main(["prepare"]), 1)
        self.assertIn("本地扫码入口已移除", output.getvalue())
        self.assertFalse(self.clients)


if __name__ == "__main__":
    unittest.main()

"""Offline recovery integration: real encrypted files, HTTP/signing and control flow.

All credentials, positions and responses here are synthetic. No live HTTP is used.
The checkpoint stub copies the same encrypted files as the workflow cache.
"""
from copy import deepcopy
from dataclasses import replace
from itertools import cycle
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests
from requests.adapters import BaseAdapter

from book_context import CATALOG_URL, DEFAULT_BOOK_URL, load_context
from login_store import load_login, pending_progress, reading_cursor, save_login
import main
import progress_guard as pg
from reader_protocol import app_id, cal_hash, encode_data, encode_id
from weread_session import WeReadSession


BOOK_ID = "25615385"
USER_AGENT = "wxread-offline-recovery-fixture"
LOGIN = {"version": 1, "cookies": {"wr_vid": "123", "wr_skey": "synthetic-session", "wr_rt": "synthetic-refresh"},
         "headers": {"User-Agent": USER_AGENT}, "reading_cursor": "202"}
ORIGINAL = {"chapterUid": 172, "chapterOffset": 389, "progress": 45.1234,
            "summary": "synthetic position", "appId": "phone-original", "updateTime": 1}


class ReaderService(BaseAdapter):
    """Replace the entire transport, rejecting any non-fixture request."""
    def __init__(self, position=None):
        super().__init__()
        self.position = deepcopy(ORIGINAL if position is None else position)
        self.writes = []
        self.period_outcomes = []
        self.offline = self.ignore_writes = self.drift_once = False

    def send(self, request, **kwargs):
        if self.offline:
            raise requests.ConnectionError("synthetic connection lost")
        if request.method == "GET" and request.url == DEFAULT_BOOK_URL:
            reader = {"bookInfo": {"bookId": BOOK_ID, "title": "Synthetic book"},
                      "progress": {"book": self.position}, "psvts": "synthetic-page-session", "token": "synthetic-page-token"}
            body = "<script>window.__INITIAL_STATE__=" + json.dumps({"reader": reader}) + ";</script>"
        elif request.method == "POST" and request.url == CATALOG_URL:
            body = json.dumps([{"bookId": BOOK_ID, "updated": [{"chapterUid": 172, "chapterIdx": 15}]}])
        elif request.method == "POST" and request.url == main.READ_URL:
            payload = json.loads(request.body)
            assert payload["b"] == encode_id(BOOK_ID)
            assert payload["c"] == encode_id(172)
            assert payload["s"] == cal_hash(encode_data(payload))
            self.writes.append(payload)
            outcome = self.period_outcomes.pop(0) if "rt" in payload and self.period_outcomes else {"succ": 1, "synckey": 1}
            if not self.ignore_writes and outcome.get("succ") == 1:
                self.position = {"chapterUid": 172, "chapterOffset": payload["co"], "progress": payload["pr"],
                                 "summary": payload["sm"], "appId": payload["appId"], "updateTime": payload["ct"]}
                if self.drift_once:
                    self.position["chapterOffset"] += 1
                    self.drift_once = False
            body = json.dumps(outcome)
        else:
            raise AssertionError(f"Non-fixture request blocked: {request.method}")
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        response._content = body.encode()
        return response

    def close(self):
        pass


def fixture_environment(directory):
    return {"WXREAD_LOGIN_FILE": str(Path(directory) / "login.enc"),
            "WXREAD_COOKIE_FILE": str(Path(directory) / "cookies.enc"),
            "WXREAD_QR_PASSWORD": "synthetic-password-at-least-20-characters",
            "GITHUB_REPOSITORY": "fixture/wxread", "GITHUB_ACTIONS": "",
            "WXREAD_LOGIN_JSON": "", "WXREAD_CURL_BASH": "", "WEREAD_API_KEY": "",
            "WXREAD_BOOK_URL": "", "PUSH_METHOD": "", "READ_NUM": "40"}


def fixture_client(service):
    login = load_login()
    client = WeReadSession(login["headers"], login["cookies"])
    client.http.trust_env = False
    client.http.mount("https://", service)
    client.http.mount("http://", service)
    return client


def snapshot_checkpoint(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("WXREAD_LOGIN_FILE", "WXREAD_COOKIE_FILE"):
        path = Path(os.environ[name])
        if path.exists():
            shutil.copyfile(path, directory / path.name)


def abrupt_exit_fixture(directory):
    """Exit without running guard cleanup, after saving a cloud-like checkpoint."""
    os.environ.update(fixture_environment(directory))
    service = ReaderService()
    save_login(LOGIN)
    client = fixture_client(service)
    with patch("progress_guard.time.time", return_value=1000), \
         patch("progress_guard.checkpoint", side_effect=lambda: snapshot_checkpoint(Path(directory) / "cloud")):
        context = load_context(client, DEFAULT_BOOK_URL)
        guard = pg.ProgressGuard(client, context, main.enter_book)
        guard.__enter__()
        guard.before_write()
        service.drift_once = True
        main.enter_book(client, context)
        (Path(directory) / "server.json").write_text(json.dumps(service.position), encoding="utf-8")
        os._exit(23)


class RecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wxread-recovery-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        env = patch.dict(os.environ, fixture_environment(self.directory))
        env.start()
        self.addCleanup(env.stop)
        self.clock = patch("progress_guard.time.time", return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.checkpoint = patch("progress_guard.checkpoint", side_effect=lambda: snapshot_checkpoint(self.directory / "cloud"))
        self.checkpoint.start()
        self.addCleanup(self.checkpoint.stop)
        save_login(LOGIN)
        self.service = ReaderService()
        self.client = fixture_client(self.service)
        self.addCleanup(self.client.close)

    def start_with_own_drift(self):
        context = load_context(self.client, DEFAULT_BOOK_URL)
        guard = pg.ProgressGuard(self.client, context, main.enter_book)
        guard.__enter__()
        guard.before_write()
        self.service.drift_once = True
        main.enter_book(self.client, context)
        self.assertEqual(self.service.position["chapterOffset"], 390)
        return guard

    def assert_main_stops_before_new_reading(self):
        with patch("main.WeReadSession", return_value=self.client), patch("main.run_reading") as reading, \
             patch("main.push"), patch("main.setup_logging", return_value=Mock()), self.assertLogs(level="ERROR"):
            self.assertEqual(main.main(), 1)
        reading.assert_not_called()
        self.assertEqual(reading_cursor(), "202")

    def run_pinned_periods(self, count=3):
        # Synthetic clock: this validates positions, not real waiting/credited time.
        clock = [0]
        def elapsed(since):
            clock[0] += main.INTERVAL_SECONDS
        with patch("main.wait_interval", side_effect=elapsed), \
             patch("main.time.monotonic", side_effect=lambda: clock[0]), \
             patch("main.random.randrange", side_effect=cycle((0, 999))):
            return main.run_single_book(self.client, Mock(), DEFAULT_BOOK_URL, count, refresh_session=False)

    def assert_all_submissions_keep_original_position(self):
        self.assertTrue(self.service.writes)
        for payload in self.service.writes:
            self.assertEqual((payload["c"], payload["co"], payload["pr"]),
                             (encode_id(ORIGINAL["chapterUid"]), ORIGINAL["chapterOffset"], ORIGINAL["progress"]))
        self.assertTrue(pg.same_position(self.service.position, ORIGINAL))
        self.assertEqual(reading_cursor(), "202")

    def test_all_periods_keep_original_chapter_offset_and_fractional_progress(self):
        result = self.run_pinned_periods()
        self.assertEqual(result.completed, 3)
        self.assert_all_submissions_keep_original_position()
        self.assertEqual({p["rn"] for p in self.service.writes if "rt" in p}, {0, 999})
        self.assertIsNone(pending_progress())

    def test_session_renewal_keeps_original_chapter_and_position(self):
        self.service.period_outcomes = [{"errCode": -2012}]
        with patch.object(self.client, "renew", return_value=True) as renew:
            result = self.run_pinned_periods(count=2)
        renew.assert_called_once()
        self.assertEqual(result.completed, 2)
        self.assert_all_submissions_keep_original_position()

    def test_synckey_repair_keeps_original_chapter_and_position(self):
        self.service.period_outcomes = [{"succ": 1}]
        result = self.run_pinned_periods(count=2)
        self.assertEqual(result.completed, 2)
        self.assert_all_submissions_keep_original_position()

    def test_initial_context_cannot_submit_different_chapter_offset_or_percentage(self):
        context = load_context(self.client, DEFAULT_BOOK_URL)
        for change in ({"chapter_uid": 1}, {"offset": 0}, {"progress": 0}):
            with self.subTest(change=change), patch("main.load_context", return_value=replace(context, **change)), \
                 self.assertRaisesRegex(RuntimeError, "原始阅读进度"):
                self.run_pinned_periods(count=1)
        self.assertFalse(self.service.writes)
        self.assertIsNone(pending_progress())

    def test_rebuilt_session_cannot_enter_another_chapter(self):
        context = load_context(self.client, DEFAULT_BOOK_URL)
        self.service.period_outcomes = [{"errCode": -2012}]
        with patch("main.load_context", side_effect=[context, replace(context, chapter_uid=1)]), \
             patch.object(self.client, "renew", return_value=True), self.assertRaisesRegex(RuntimeError, "原始阅读进度"):
            self.run_pinned_periods(count=1)
        self.assertEqual(len(self.service.writes), 2)  # Original entry and rejected period, no new chapter entry.
        self.assert_all_submissions_keep_original_position()
        self.assertIsNone(pending_progress())

    def test_missing_original_snapshot_never_submits_a_default_position(self):
        context = load_context(self.client, DEFAULT_BOOK_URL)
        with patch("main.load_context", return_value=replace(context, original=None)), self.assertRaises(RuntimeError):
            self.run_pinned_periods(count=1)
        self.assertFalse(self.service.writes)
        self.assertIsNone(pending_progress())

    def test_restores_changed_position_with_signed_request_and_verified_readback(self):
        guard = self.start_with_own_drift()
        guard.__exit__(None, None, None)
        self.assertTrue(pg.same_position(self.service.position, ORIGINAL))
        self.assertIsNone(pending_progress())
        self.assertEqual(len(self.service.writes), 2)
        self.assertEqual(self.service.writes[-1]["pr"], 45.1234)
        self.assertTrue(all("rt" not in payload for payload in self.service.writes))

    def test_abrupt_exit_recovers_from_encrypted_cloud_copy_with_fresh_session(self):
        crashed = self.directory / "crashed"
        crashed.mkdir()
        env = {**os.environ, **fixture_environment(crashed)}
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--abrupt-exit-fixture", str(crashed)],
                                cwd=ROOT, env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 23, result.stderr.decode("utf-8", errors="replace"))
        server = json.loads((crashed / "server.json").read_text(encoding="utf-8"))
        fresh = self.directory / "fresh-runner"
        fresh.mkdir()
        for path in (crashed / "cloud").iterdir():
            shutil.copyfile(path, fresh / path.name)
        encrypted = (fresh / "login.enc").read_bytes()
        for plaintext in (b"chapterOffset", b"synthetic-session", b"reading_cursor"):
            self.assertNotIn(plaintext, encrypted)
        with patch.dict(os.environ, fixture_environment(fresh)), patch("progress_guard.time.time", return_value=10000):
            service = ReaderService(server)
            client = fixture_client(service)
            self.addCleanup(client.close)
            pg.recover_pending(client, main.enter_book)
            self.assertTrue(pg.same_position(service.position, ORIGINAL))
            self.assertIsNone(pending_progress())
            self.assertEqual(reading_cursor(), "202")
            self.assertEqual(len(service.writes), 1)
            self.assertNotIn("rt", service.writes[0])

    def test_network_loss_preserves_encrypted_journal_then_retry_recovers(self):
        self.start_with_own_drift()
        self.service.offline = True
        self.assert_main_stops_before_new_reading()
        self.assertIsNotNone(pending_progress())
        self.assertNotIn(b"chapterOffset", Path(os.environ["WXREAD_LOGIN_FILE"]).read_bytes())
        self.service.offline = False
        with patch("progress_guard.time.time", return_value=2000):
            pg.recover_pending(self.client, main.enter_book)
        self.assertTrue(pg.same_position(self.service.position, ORIGINAL))
        self.assertIsNone(pending_progress())

    def test_phone_change_preserved_without_next_book_or_cursor_advance(self):
        self.start_with_own_drift()
        self.service.position.update(chapterOffset=900, appId="phone-new", updateTime=1001)
        self.assert_main_stops_before_new_reading()
        self.assertEqual(self.service.position["chapterOffset"], 900)
        self.assertEqual(len(self.service.writes), 1)
        self.assertIsNone(pending_progress())

    def test_later_same_browser_progress_preserves_position_and_journal(self):
        self.start_with_own_drift()
        self.service.position.update(chapterOffset=900, appId=app_id(USER_AGENT), updateTime=9999)
        with patch("progress_guard.time.time", return_value=10000):
            self.assert_main_stops_before_new_reading()
        self.assertEqual(self.service.position["chapterOffset"], 900)
        self.assertEqual(len(self.service.writes), 1)
        self.assertIsNotNone(pending_progress())

    def test_ack_without_restored_position_retains_journal_and_stops(self):
        self.start_with_own_drift()
        self.service.ignore_writes = True
        self.assert_main_stops_before_new_reading()
        self.assertEqual(len(self.service.writes), 2)
        self.assertIsNotNone(pending_progress())
        self.assertEqual(self.service.position["chapterOffset"], 390)

    def test_phone_change_during_restore_checkpoint_prevents_restore(self):
        self.start_with_own_drift()
        def changed():
            self.service.position.update(chapterOffset=900, appId="phone-new", updateTime=1001)
        with patch("progress_guard.checkpoint", side_effect=changed):
            self.assert_main_stops_before_new_reading()
        self.assertEqual(len(self.service.writes), 1)
        self.assertEqual(self.service.position["chapterOffset"], 900)

    def test_failed_restore_can_retry_exact_candidate_from_cloud_checkpoint(self):
        self.start_with_own_drift()
        self.service.ignore_writes = True
        with patch("progress_guard.time.time", return_value=2000):
            self.assert_main_stops_before_new_reading()
        self.assertEqual(pending_progress()["restore_from"]["chapterOffset"], 390)
        fresh = self.directory / "retry-runner"
        fresh.mkdir()
        for path in (self.directory / "cloud").iterdir():
            shutil.copyfile(path, fresh / path.name)
        with patch.dict(os.environ, fixture_environment(fresh)), patch("progress_guard.time.time", return_value=10000):
            service = ReaderService(self.service.position)
            client = fixture_client(service)
            self.addCleanup(client.close)
            pg.recover_pending(client, main.enter_book)
            self.assertTrue(pg.same_position(service.position, ORIGINAL))
            self.assertIsNone(pending_progress())
            self.assertNotIn("rt", service.writes[0])

    def test_cloud_retry_cannot_claim_concurrent_browser_change_during_restore(self):
        self.start_with_own_drift()
        def changed():
            snapshot_checkpoint(self.directory / "cloud")
            self.service.position.update(chapterOffset=900, appId=app_id(USER_AGENT), updateTime=2001)
        with patch("progress_guard.time.time", return_value=2000), patch("progress_guard.checkpoint", side_effect=changed):
            self.assert_main_stops_before_new_reading()
        fresh = self.directory / "retry-runner"
        fresh.mkdir()
        for path in (self.directory / "cloud").iterdir():
            shutil.copyfile(path, fresh / path.name)
        with patch.dict(os.environ, fixture_environment(fresh)), patch("progress_guard.time.time", return_value=2002):
            service = ReaderService(self.service.position)
            client = fixture_client(service)
            self.addCleanup(client.close)
            with self.assertRaises(pg.ProgressUncertain):
                pg.recover_pending(client, main.enter_book)
            self.assertEqual(service.position["chapterOffset"], 900)
            self.assertFalse(service.writes)
            self.assertIsNotNone(pending_progress())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--abrupt-exit-fixture":
        abrupt_exit_fixture(sys.argv[2])
    else:
        unittest.main()

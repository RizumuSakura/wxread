from copy import deepcopy
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from book_context import BookContext, DEFAULT_BOOK_URL, saved_position
import progress_guard as pg


class ProgressGuardTests(unittest.TestCase):
    def setUp(self):
        self.position = {"chapterUid": 172, "chapterOffset": 389, "progress": 45.1234,
                         "summary": "start", "appId": "phone-original", "updateTime": 1}
        self.context = BookContext("25615385", "title", DEFAULT_BOOK_URL, 172, 15, 389, 45.1234,
                                   "start", "ps", "pc", "web-script", "token", "chapter", self.position)
        self.client, self.writer = Mock(), Mock()
        self.pending = None
        self.server = deepcopy(self.position)
        def save(record):
            self.pending = deepcopy(record)
        for target, kwargs in (("pending_progress", {"side_effect": lambda: deepcopy(self.pending)}),
                               ("save_pending_progress", {"side_effect": save}),
                               ("load_login", {"return_value": {"cookies": {"wr_vid": "123"}}}),
                               ("read_position", {"side_effect": lambda *args: ("25615385", deepcopy(self.server))}),
                               ("checkpoint", {}), ("time.time", {"return_value": 1000})):
            patcher = patch("progress_guard." + target, **kwargs)
            setattr(self, target.replace(".", "_") + "_mock", patcher.start())
            self.addCleanup(patcher.stop)

    def guard(self):
        return pg.ProgressGuard(self.client, self.context, self.writer)

    def test_checkpoint_precedes_first_write_and_unchanged_progress_needs_no_write(self):
        with self.guard() as guard:
            self.checkpoint_mock.assert_called_once()
            self.assertEqual(self.pending["position"]["progress"], 45.1234)
            guard.before_write()
        self.writer.assert_not_called()
        self.assertIsNone(self.pending)

    def test_checkpoint_failure_prevents_book_write(self):
        self.checkpoint_mock.side_effect = RuntimeError("upload failed")
        with self.assertRaises(RuntimeError):
            with self.guard():
                self.writer()
        self.writer.assert_not_called()
        self.assertIsNotNone(self.pending)

    def test_cannot_begin_with_unresolved_journal(self):
        self.pending = {"unresolved": True}
        with self.assertRaisesRegex(RuntimeError, "未处理"):
            with self.guard():
                self.fail("not safe")
        self.checkpoint_mock.assert_not_called()

    def test_phone_change_before_write_is_preserved_and_stops(self):
        with self.assertRaises(pg.ProgressConflict):
            with self.guard() as guard:
                self.server.update(chapterOffset=400, appId="phone-new", updateTime=1001)
                guard.before_write()
        self.assertEqual(self.server["chapterOffset"], 400)
        self.writer.assert_not_called()
        self.assertIsNone(self.pending)

    def test_phone_change_during_body_is_preserved_and_stops_at_cleanup(self):
        with self.assertRaises(pg.ProgressConflict):
            with self.guard() as guard:
                guard.before_write()
                self.server.update(chapterUid=173, chapterOffset=5, appId="phone-new", updateTime=1000)
        self.assertEqual(self.server["chapterUid"], 173)
        self.writer.assert_not_called()
        self.assertIsNone(self.pending)

    def test_own_server_change_is_restored_and_read_back(self):
        restored = Mock()
        self.writer.side_effect = lambda *args: setattr(self, "server", deepcopy(self.position))
        with patch("progress_guard.load_context", return_value=restored) as load:
            with self.guard() as guard:
                guard.before_write()
                self.server.update(chapterOffset=390, appId="web-script", updateTime=1000)
        self.writer.assert_called_once_with(self.client, restored)
        self.assertEqual(load.call_args.kwargs["position"], self.position)
        self.assertIsNone(self.pending)

    def test_old_or_unknown_writer_is_not_rolled_back(self):
        for app, updated in (("", 1000), ("web-script", 100), ("phone", 1000)):
            self.pending = None
            self.server = deepcopy(self.position)
            with self.assertRaises(pg.ProgressConflict):
                with self.guard():
                    self.server.update(chapterOffset=1, appId=app, updateTime=updated)
        self.writer.assert_not_called()

    def test_failed_restore_keeps_journal_and_stops(self):
        with patch("progress_guard.load_context", return_value=Mock()), self.assertRaisesRegex(RuntimeError, "保留恢复记录"):
            with self.guard():
                self.server.update(chapterOffset=390, appId="web-script", updateTime=1000)
        self.assertIsNotNone(self.pending)

    def test_network_failure_keeps_journal_and_does_not_claim_success(self):
        with self.assertRaisesRegex(RuntimeError, "保留恢复记录"):
            with self.guard():
                self.read_position_mock.side_effect = RuntimeError("network")
        self.assertIsNotNone(self.pending)

    def test_body_error_still_verifies_original_position(self):
        with self.assertRaisesRegex(RuntimeError, "read error"):
            with self.guard():
                raise RuntimeError("read error")
        self.assertIsNone(self.pending)
        self.read_position_mock.assert_called()

    def test_phone_change_during_restore_context_fetch_prevents_restore(self):
        def changed(*args, **kwargs):
            self.server.update(chapterOffset=450, appId="phone", updateTime=1001)
            return Mock()
        with patch("progress_guard.load_context", side_effect=changed), self.assertRaises(pg.ProgressConflict):
            with self.guard():
                self.server.update(chapterOffset=390, appId="web-script", updateTime=1000)
        self.writer.assert_not_called()
        self.assertEqual(self.server["chapterOffset"], 450)

    def test_interrupted_run_journal_is_resolved_before_next_run(self):
        guard = self.guard()
        guard.__enter__()  # Simulate abrupt loss without __exit__.
        self.assertIsNotNone(self.pending)
        pg.recover_pending(self.client, self.writer)
        self.assertIsNone(self.pending)
        self.assertEqual(self.checkpoint_mock.call_count, 2)

    def test_recovery_retains_phone_progress_and_stops_new_task(self):
        self.guard().__enter__()
        self.server.update(chapterOffset=900, appId="phone-new", updateTime=1001)
        with self.assertRaises(pg.ProgressConflict):
            pg.recover_pending(self.client, self.writer)
        self.writer.assert_not_called()
        self.assertEqual(self.server["chapterOffset"], 900)

    def test_missing_position_fields_and_invalid_numbers_are_not_guessed(self):
        for progress in ({}, {**self.position, "progress": float("nan")},
                         {**self.position, "chapterOffset": -1}, {**self.position, "progress": True}):
            with self.assertRaises(RuntimeError):
                saved_position(progress)


class CheckpointProcessTests(unittest.TestCase):
    def test_local_run_does_not_need_node(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": ""}), patch("progress_guard.subprocess.run") as run:
            pg.checkpoint()
        run.assert_not_called()

    def test_failed_cloud_checkpoint_output_is_not_exposed(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
             patch("progress_guard.subprocess.run", return_value=Mock(returncode=1, stderr="private-url")), \
             self.assertRaises(RuntimeError) as error:
            pg.checkpoint()
        self.assertNotIn("private-url", str(error.exception))

    def test_cloud_checkpoint_timeout_fails_closed(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
             patch("progress_guard.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 90)), \
             self.assertRaises(RuntimeError):
            pg.checkpoint()


if __name__ == "__main__":
    unittest.main()

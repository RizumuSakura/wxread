import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import start_window


def cst(hour: int, minute: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=start_window.china_tz())


class WindowDelayTest(unittest.TestCase):
    def test_inside_the_window_starts_right_away(self):
        for hour in (1, 2, 4):
            with self.subTest(hour=hour):
                self.assertEqual(start_window.window_delay(cst(hour, 30)), 0.0)

    def test_before_dawn_waits_until_one(self):
        self.assertEqual(start_window.window_delay(cst(0, 15)), 45 * 60)

    def test_evening_waits_until_the_next_one(self):
        self.assertEqual(start_window.window_delay(cst(21, 7)), 3 * 3600 + 53 * 60)

    def test_daytime_run_is_skipped(self):
        for hour in (5, 9, 14, 19):
            with self.subTest(hour=hour):
                self.assertIsNone(start_window.window_delay(cst(hour)))

    def test_window_bounds_are_half_open(self):
        self.assertEqual(start_window.window_delay(cst(1)), 0.0)
        self.assertIsNone(start_window.window_delay(cst(5)))
        self.assertEqual(start_window.window_delay(cst(20)), 5 * 3600)


class WaitForWindowTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(start_window.FORCE_ENV, None)

    def test_sleeps_exactly_until_the_window_and_logs_once(self):
        slept = []
        logs = []
        started = start_window.wait_for_window(
            now=cst(22, 0), sleep=slept.append, log=logs.append
        )
        self.assertTrue(started)
        self.assertEqual(slept, [3 * 3600])
        self.assertIn("等待 180 分钟后开始", logs[0])

    def test_skips_without_sleeping_during_the_day(self):
        slept = []
        started = start_window.wait_for_window(
            now=cst(13, 0), sleep=slept.append, log=lambda message: None
        )
        self.assertFalse(started)
        self.assertEqual(slept, [])

    def test_force_overrides_the_window(self):
        os.environ[start_window.FORCE_ENV] = "true"
        slept = []
        started = start_window.wait_for_window(
            now=cst(13, 0), sleep=slept.append, log=lambda message: None
        )
        self.assertTrue(started)
        self.assertEqual(slept, [])

    def test_utc_input_is_converted_to_china_time(self):
        # 20:00 UTC 等于次日 04:00 CST，仍在窗口内。
        self.assertEqual(
            start_window.window_delay(datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)),
            0.0,
        )
        # 04:00 UTC 等于 12:00 CST，属于该跳过的时段。
        self.assertIsNone(
            start_window.window_delay(datetime(2026, 11, 14, 4, 0, tzinfo=timezone.utc))
        )


class PublishTest(unittest.TestCase):
    def test_writes_the_output_for_later_steps(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            target = os.path.join(folder, "output")
            with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": target}):
                start_window.publish(True)
                start_window.publish(False)
            with open(target, encoding="utf-8") as stream:
                self.assertEqual(
                    stream.read(), "window_ok=true\nwindow_ok=false\n"
                )

    def test_prints_when_no_output_file_is_configured(self):
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": ""}):
            with mock.patch("builtins.print") as printed:
                start_window.publish(True)
        printed.assert_called_once_with("window_ok=true\n", end="")


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import start_window


def cst(hour: int, minute: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=start_window.china_tz())


class FakeClock:
    """可推进的假时钟：sleep 真的把时间往前推。"""

    def __init__(self, start: datetime):
        self.now = start
        self.sleeps = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


class WindowDelayTest(unittest.TestCase):
    def test_inside_the_late_window_starts_right_away(self):
        for hour in (1, 2, 4):
            with self.subTest(hour=hour):
                self.assertEqual(start_window.window_delay(cst(hour, 30)), 0.0)

    def test_before_dawn_waits_until_one(self):
        self.assertEqual(start_window.window_delay(cst(0, 15)), 45 * 60)

    def test_evening_waits_until_the_next_one(self):
        self.assertEqual(start_window.window_delay(cst(21, 7)), 3 * 3600 + 53 * 60)

    def test_too_early_to_wait_is_skipped(self):
        # 19:00 → 01:00 需要等 6 小时，超过 240 分钟上限。
        for hour in (5, 9, 14, 19):
            with self.subTest(hour=hour):
                self.assertIsNone(start_window.window_delay(cst(hour)))

    def test_window_bounds_are_half_open(self):
        self.assertEqual(start_window.window_delay(cst(1)), 0.0)
        self.assertIsNone(start_window.window_delay(cst(5)))
        # 无提前量时目标就是 01:00，等待上限 300 分钟：20:00 正好 5 小时，19:59 就超了。
        self.assertEqual(start_window.window_delay(cst(20, 0)), 5 * 3600)
        self.assertIsNone(start_window.window_delay(cst(19, 59)))

    def test_lead_seconds_move_the_target_earlier(self):
        self.assertEqual(
            start_window.window_delay(cst(0, 15), lead_seconds=1800), 15 * 60
        )
        self.assertEqual(
            start_window.window_delay(cst(23, 0), lead_seconds=1800), 90 * 60
        )

    def test_lead_seconds_widen_the_usable_earliest_arrival(self):
        # 交棒点是 00:30：19:30 距离它 5 小时，正好在上限内；19:29 就超了。
        self.assertEqual(
            start_window.window_delay(cst(19, 30), lead_seconds=1800),
            5 * 3600,
        )
        self.assertIsNone(start_window.window_delay(cst(19, 29), lead_seconds=1800))


class WakeLeadTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(start_window.LEAD_ENV, None)

    def test_defaults_to_no_lead(self):
        self.assertEqual(start_window.wake_lead_seconds(), 0)

    def test_reads_the_environment(self):
        os.environ[start_window.LEAD_ENV] = "1800"
        self.assertEqual(start_window.wake_lead_seconds(), 1800)

    def test_rejects_non_integers(self):
        for raw in ("", "abc", "1.5"):
            with self.subTest(raw=raw):
                os.environ[start_window.LEAD_ENV] = raw
                if not raw:
                    self.assertEqual(start_window.wake_lead_seconds(), 0)
                    continue
                with self.assertRaises(RuntimeError):
                    start_window.wake_lead_seconds()

    def test_rejects_negative_values(self):
        os.environ[start_window.LEAD_ENV] = "-1"
        with self.assertRaises(RuntimeError):
            start_window.wake_lead_seconds()


class WaitForWindowTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(start_window.FORCE_ENV, None)
        os.environ.pop(start_window.LEAD_ENV, None)

    def test_sleeps_exactly_until_one_and_logs(self):
        clock = FakeClock(cst(22, 0))
        logs = []
        started = start_window.wait_for_window(
            now=cst(22, 0), clock=clock, sleep=clock.sleep, log=logs.append
        )
        self.assertTrue(started)
        self.assertEqual(clock.sleeps, [3 * 3600])
        self.assertEqual(clock.now, cst(1, day=15))
        self.assertIn("等待到 2026-11-15 01:00:00（CST）", logs[0])

    def test_lead_hands_off_early(self):
        clock = FakeClock(cst(22, 0))
        started = start_window.wait_for_window(
            now=cst(22, 0),
            clock=clock,
            sleep=clock.sleep,
            log=lambda message: None,
            lead_seconds=1800,
        )
        self.assertTrue(started)
        self.assertEqual(clock.sleeps, [2 * 3600 + 30 * 60])
        self.assertEqual(clock.now, cst(0, 30, day=15))

    def test_arriving_after_the_handoff_point_returns_immediately(self):
        clock = FakeClock(cst(0, 45))
        logs = []
        started = start_window.wait_for_window(
            now=cst(0, 45), clock=clock, sleep=clock.sleep, log=logs.append,
            lead_seconds=1800,
        )
        self.assertTrue(started)
        self.assertEqual(clock.sleeps, [])
        self.assertIn("已到交棒时刻", logs[0])

    def test_late_queue_reports_the_minutes_it_missed(self):
        clock = FakeClock(cst(2, 43))
        logs = []
        started = start_window.wait_for_window(
            now=cst(2, 43), clock=clock, sleep=clock.sleep, log=logs.append
        )
        self.assertTrue(started)
        self.assertEqual(clock.sleeps, [])
        self.assertIn("队列迟到 103 分钟", logs[0])

    def test_skips_without_sleeping_when_too_early(self):
        clock = FakeClock(cst(13, 0))
        started = start_window.wait_for_window(
            now=cst(13, 0), clock=clock, sleep=clock.sleep, log=lambda message: None
        )
        self.assertFalse(started)
        self.assertEqual(clock.sleeps, [])

    def test_force_overrides_the_schedule(self):
        os.environ[start_window.FORCE_ENV] = "true"
        clock = FakeClock(cst(13, 0))
        started = start_window.wait_for_window(
            now=cst(13, 0), clock=clock, sleep=clock.sleep, log=lambda message: None
        )
        self.assertTrue(started)
        self.assertEqual(clock.sleeps, [])

    def test_utc_input_is_converted_to_china_time(self):
        # 20:00 UTC 等于次日 04:00 CST，属于迟到的窗口内。
        self.assertEqual(
            start_window.window_delay(datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)),
            0.0,
        )
        # 04:00 UTC 等于 12:00 CST，距离次日 01:00 有 13 小时，跳过。
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

"""Hold the scheduled run until the early-morning window in Asia/Shanghai.

GitHub 的 schedule 队列没有准时保证：同一个 `cron: '7 13 * * *'`（21:07 CST）
实测会被推迟 4–7.5 小时才启动。这个脚本把不确定性收敛成一个确定结果：

    01:00–05:00 CST  立即开始
    20:00–24:00 CST  睡到次日 01:00 再开始
    00:00–01:00 CST  睡到当日 01:00 再开始
    05:00–20:00 CST  放弃本次运行（再跑两小时会撞上白天读书）

放弃只是跳过，不报错：下一次定时任务会重新进窗口。
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

WINDOW_START_HOUR = 1
WINDOW_END_HOUR = 5
SLEEP_FROM_HOUR = 20
FORCE_ENV = "WXREAD_FORCE_WINDOW"

_LOG = lambda message: print(message, flush=True)


def china_tz() -> timezone:
    """返回 UTC+8；不依赖系统 tzdata，Windows 上也成立。"""
    return timezone(timedelta(hours=8), "Asia/Shanghai")


def window_delay(now: datetime) -> Optional[float]:
    """返回开始前需要等待的秒数；`None` 表示本次应当跳过。"""
    current = now.astimezone(china_tz())
    hour = current.hour
    if WINDOW_START_HOUR <= hour < WINDOW_END_HOUR:
        return 0.0
    if WINDOW_START_HOUR <= hour < SLEEP_FROM_HOUR:
        return None
    day = current if hour < WINDOW_START_HOUR else current + timedelta(days=1)
    target = day.replace(
        hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0
    )
    return max(0.0, (target - current).total_seconds())


def wait_for_window(
    now: Optional[datetime] = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _LOG,
) -> bool:
    """等到窗口回到 True，判断本次该跳过时返回 False。"""
    if (os.getenv(FORCE_ENV) or "").strip().lower() == "true":
        log("已指定强制运行，忽略凌晨窗口限制。")
        return True
    current = now or datetime.now(timezone.utc)
    delay = window_delay(current)
    stamp = current.astimezone(china_tz()).strftime("%Y-%m-%d %H:%M:%S")
    if delay is None:
        log(f"当前 {stamp}（CST）不在凌晨窗口，跳过本次运行，等待下一次定时任务。")
        return False
    if delay <= 0:
        log(f"当前 {stamp}（CST）已在凌晨窗口，立即开始。")
        return True
    log(
        f"当前 {stamp}（CST）尚未进入凌晨窗口，等待 {int(delay // 60)} 分钟后开始。"
    )
    sleep(delay)
    return True


def publish(ok: bool) -> None:
    """把结果写进 GITHUB_OUTPUT，供后续步骤判断。"""
    target = os.getenv("GITHUB_OUTPUT")
    line = f"window_ok={'true' if ok else 'false'}\n"
    if not target:
        print(line, end="")
        return
    with open(target, "a", encoding="utf-8") as stream:
        stream.write(line)


def main() -> int:
    ok = wait_for_window()
    publish(ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Hold the scheduled run until exactly 01:00 Asia/Shanghai.

GitHub 的 schedule 队列没有准时保证：同一个 cron 实测会被推迟 4–7.5 小时才启动，
所以时间不能交给 cron 表达，只能分两步钉死：

    window job   cron '47 8 * * *'（16:47 CST），到达时刻实测 20:42–00:19（CST）
                 └─ 睡到 00:30 就交棒（WXREAD_WAKE_LEAD_SECONDS=1800），
                    只吸走长等待，不占用阅读 job 的 6 小时上限。

    deploy job   gate 步骤再对一次时，睡到 01:00:00 整，然后立刻开始阅读。

判定规则（Asia/Shanghai，UTC+8）：

    到达 < 00:30       睡到 00:30 交棒（长等待止于此，上限 300 分钟）
    00:30 ≤ 到达 < 01:00  已到交棒点，立即返回；由 deploy 的 gate 等到 01:00:00
    01:00 ≤ 到达 < 05:00  队列迟到，立即开始（日志打印迟到了多少分钟）
    到达 ≥ 05:00       放弃本次运行（再跑两小时会撞上白天读书）
    到达太早（> 300 分钟）  放弃本次运行
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

TARGET_HOUR = 1
LATE_WINDOW_END_HOUR = 5
MAX_WAIT_SECONDS = 300 * 60
LEAD_ENV = "WXREAD_WAKE_LEAD_SECONDS"
FORCE_ENV = "WXREAD_FORCE_WINDOW"

_Clock = Callable[[], datetime]
_Log = Callable[[str], None]

_LOG: _Log = lambda message: print(message, flush=True)


def china_tz() -> timezone:
    """返回 UTC+8；不依赖系统 tzdata，Windows 上也成立。"""
    return timezone(timedelta(hours=8), "Asia/Shanghai")


def wake_lead_seconds() -> int:
    """前置 job 提前交棒的秒数；0 表示一直等到 01:00:00。"""
    raw = (os.getenv(LEAD_ENV) or "0").strip()
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{LEAD_ENV} 必须是整数秒，收到：{raw!r}") from None
    if value < 0:
        raise RuntimeError(f"{LEAD_ENV} 不能为负，收到：{value}")
    return value


def day_target(now: datetime) -> datetime:
    """当天（CST 日历日）的 01:00:00，用于计算迟到了多少分钟。"""
    return now.astimezone(china_tz()).replace(
        hour=TARGET_HOUR, minute=0, second=0, microsecond=0
    )


def window_target(now: datetime, lead_seconds: int = 0) -> datetime:
    """本次应当启动（或交棒）的绝对时刻：最近的 01:00:00 减去提前量。"""
    current = now.astimezone(china_tz())
    day = current if current.hour < TARGET_HOUR else current + timedelta(days=1)
    target = day.replace(hour=TARGET_HOUR, minute=0, second=0, microsecond=0)
    return target - timedelta(seconds=lead_seconds)


def window_delay(now: datetime, lead_seconds: int = 0) -> Optional[float]:
    """返回距离目标时刻还需等待的秒数；`None` 表示本次应当跳过。"""
    current = now.astimezone(china_tz())
    if TARGET_HOUR <= current.hour < LATE_WINDOW_END_HOUR:
        return 0.0
    delay = (window_target(current, lead_seconds) - current).total_seconds()
    if delay > MAX_WAIT_SECONDS:
        return None
    return max(0.0, delay)


def sleep_until(
    target: datetime,
    clock: _Clock,
    sleep: Callable[[float], None],
    log: _Log,
) -> None:
    """分段睡眠直到 `target`，每次醒来都重新对时，避免一次性长睡漂移。"""
    while True:
        remaining = (target - clock().astimezone(china_tz())).total_seconds()
        if remaining <= 0:
            return
        log(
            f"距离 {target:%H:%M:%S}（CST）还有 "
            f"{int(remaining // 60)} 分 {int(remaining % 60)} 秒，继续等待。"
        )
        sleep(remaining)


def wait_for_window(
    now: Optional[datetime] = None,
    sleep: Callable[[float], None] = time.sleep,
    log: _Log = _LOG,
    clock: Optional[_Clock] = None,
    lead_seconds: Optional[int] = None,
) -> bool:
    """等到目标时刻再返回 True；判断本次该跳过时返回 False。"""
    if (os.getenv(FORCE_ENV) or "").strip().lower() == "true":
        log("已指定强制运行，忽略凌晨 01:00 的时间限制。")
        return True
    lead = wake_lead_seconds() if lead_seconds is None else lead_seconds
    tick: _Clock = clock or (lambda: datetime.now(timezone.utc))
    current = now or tick()
    target = window_target(current, lead)
    delay = window_delay(current, lead)
    stamp = current.astimezone(china_tz()).strftime("%Y-%m-%d %H:%M:%S")
    if delay is None:
        log(
            f"当前 {stamp}（CST）距离最近的 01:00:00 超过 {MAX_WAIT_SECONDS // 60} 分钟，"
            "跳过本次运行，等待下一次定时任务。"
        )
        return False
    if delay <= 0:
        if TARGET_HOUR <= current.astimezone(china_tz()).hour < LATE_WINDOW_END_HOUR:
            late = int(
                (
                    current.astimezone(china_tz()) - day_target(current)
                ).total_seconds()
                // 60
            )
            log(f"当前 {stamp}（CST）已过 01:00:00，队列迟到 {late} 分钟，立即开始。")
        else:
            log(f"当前 {stamp}（CST）已到交棒时刻，交由下一阶段等到 01:00:00 开始。")
        return True
    log(
        f"当前 {stamp}（CST）等待到 {target:%Y-%m-%d %H:%M:%S}（CST），"
        f"共 {int(delay // 60)} 分 {int(delay % 60)} 秒。"
    )
    sleep_until(target, tick, sleep, log)
    return True


def publish(ok: bool) -> None:
    """把结果写进 GITHUB_OUTPUT，供后续步骤 / job 判断。"""
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

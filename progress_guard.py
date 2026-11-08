"""Encrypted per-book recovery journal and conservative concurrent-reader checks."""
import logging
import os
from pathlib import Path
import subprocess
import time

from book_context import load_context, read_position, reader_url
from login_store import load_login, pending_progress, save_pending_progress


class ProgressConflict(RuntimeError):
    pass


def same_position(left, right):
    return all(left[key] == right[key] for key in ("chapterUid", "chapterOffset", "progress"))


def checkpoint():
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    script = Path(__file__).parent / ".github" / "actions" / "reading" / "checkpoint.mjs"
    try:
        result = subprocess.run(["node", str(script)], capture_output=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("进度保护检查点上传失败，未开始新的阅读提交。") from exc
    if result.returncode:
        raise RuntimeError("进度保护检查点上传失败，未开始新的阅读提交。")
    logging.info("原始进度已加密保存到云端检查点。")


class ProgressGuard:
    def __init__(self, client, context, writer):
        self.client, self.context, self.writer = client, context, writer
        self.record = None
        self.conflict = False

    def __enter__(self):
        if pending_progress() is not None:
            raise RuntimeError("仍有未处理的进度恢复记录，停止新任务。")
        login = load_login()
        if login is None or self.context.original is None:
            raise RuntimeError("缺少已保存的登录状态或原始进度，未开始阅读。")
        self.record = {"version": 1, "owner": login["cookies"]["wr_vid"],
                       "book_id": self.context.book_id, "reference": self.context.url,
                       "position": self.context.original, "app_id": self.context.application_id,
                       "last_write": time.time()}
        save_pending_progress(self.record)
        checkpoint()  # Must finish before the first /book/read write.
        return self

    def current(self):
        book_id, position = read_position(self.client, self.record["reference"])
        if book_id != self.record["book_id"]:
            raise RuntimeError("进度回读书籍标识不一致，保留恢复记录并停止。")
        return position

    def external_change(self):
        self.conflict = True
        save_pending_progress(None)
        raise ProgressConflict("检测到手机或其他客户端修改阅读进度，已保留新进度并停止脚本。")

    def before_write(self):
        if not same_position(self.current(), self.record["position"]):
            self.external_change()
        self.record["last_write"] = time.time()
        save_pending_progress(self.record)

    def finish(self):
        current = self.current()
        original = self.record["position"]
        if not same_position(current, original):
            # Unknown writers are never rolled back. Phone appIds differ from
            # this web client; timestamps must also identify this session.
            updated = current.get("updateTime")
            if (current.get("appId") != self.record["app_id"]
                    or not isinstance(updated, (int, float))
                    or not self.record["last_write"] - 2 <= updated <= time.time() + 5):
                self.external_change()
            context = load_context(self.client, self.record["reference"], position=original)
            # Recheck immediately before restoration; do not overwrite a phone
            # change that arrived during the context/catalog requests.
            latest = self.current()
            if latest != current:
                self.external_change()
            self.writer(self.client, context)  # No rt: restoring is not reading.
            if not same_position(self.current(), original):
                raise RuntimeError("原始进度恢复后回读不一致，已停止并保留恢复记录。")
            logging.info("进度保护：已恢复原始章节、位置和百分比，服务端回读一致。")
        else:
            logging.info("进度保护：原始章节、位置和百分比未变化，服务端回读一致。")
        save_pending_progress(None)

    def __exit__(self, kind, error, traceback):
        if not self.conflict:
            try:
                self.finish()
            except ProgressConflict:
                raise
            except Exception as exc:
                raise RuntimeError("无法完成原始进度核对/恢复，已停止并保留恢复记录，等待下次运行处理。") from exc
        return False


def recover_pending(client, writer):
    record = pending_progress()
    if record is None:
        return
    logging.info("发现未完成的进度保护记录，先核对或恢复，再执行新任务。")
    guard = ProgressGuard(client, None, writer)
    guard.record = record
    try:
        guard.finish()
    except ProgressConflict:
        raise
    except Exception as exc:
        raise RuntimeError("旧任务的进度恢复未完成，停止新任务并保留恢复记录。") from exc
    checkpoint()  # Publish the resolved journal before proceeding.

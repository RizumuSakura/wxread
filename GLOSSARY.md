# 领域词表（GLOSSARY）

按 `docs/agents/domain.md` 的 single-context 约定维护；只收录本仓库代码里真实出现的名词。

| 术语 | 代码位置 | 含义 |
| --- | --- | --- |
| 段 / period | `main.read_periods`、`READ_NUM` | 一次"真实等待 30 秒 + 提交一次 `/book/read`"的循环单位 |
| 真实等待 | `main.wait_interval`、`time.monotonic()` | 用单调时钟量的实际经过秒数，`rt` 上限 30，不足不上报 |
| 快照位置 | `book_context.BookContext.position()` | 进入书籍时读到的 `chapterUid`/`chapterOffset`/小数百分比；每段提交都用它，脚本从不推进 |
| 原始进度 | `book_context.saved_position(progress)` | 从阅读器状态里取出的 `progress` 字段（含 `summary`/`appId`/`updateTime`），用于重建上下文 |
| 上下文 / context | `book_context.load_context()` | `BookContext` 实例：书籍、章节、位置、`ps`/`pc`、`token` |
| synckey | `main.read_periods`、`SYNCKEY_REPAIR_LIMIT` | 阅读接口成功响应里的同步键；缺失视为未确认，走 `chapterInfos` 修复 |
| 续期 / renewal | `weread_session.WeReadSession.renew()` | 用 `wr_rt` 刷新 `wr_skey`；失败回滚 Cookie，成功立即落盘 |
| 登录态 / login state | `login_store`、`.state/login.enc` | `{version, cookies, headers}`，AES-GCM（`cloud_crypto`）或本地 Fernet 加密 |
| 云端口令 | `WXREAD_QR_PASSWORD`、`cloud_crypto.password()` | ≥20 位，参与 AES-GCM KDF；不再参与 cache key |
| 二维码查看页 | `actions_login.write_viewer()`、`qr_viewer.html` | 加密二维码的静态 HTML，作为 artifact 上传，1 天保留 |
| 检查点 / checkpoint | ~~`progress_guard.checkpoint()`~~ | 已移除；现在只有运行结束时的 cache save |
| 书架循环 | ~~`weread_shelf`~~ | 已移除；曾用官方 Skill `/shelf/sync` 循环多本书 |
| 默认书 | `config.DEFAULT_BOOK_ID` = `25615385` | 《被讨厌的勇气》，未配置 `WXREAD_BOOK_URL` 时的目标 |
| 凌晨窗口 | `start_window.window_delay()` | 01:00–05:00 CST 半开区间；窗口前等到 01:00，白天到达则跳过本次 |
| 跳过 / skip | `start_window.wait_for_window()` → `window_ok=false` | 队列把运行推到白天时放弃这一轮，不算失败，次日重来 |
| 强制运行 | `WXREAD_FORCE_WINDOW`、`inputs.force` | 手动触发时忽略窗口限制立即跑，仅用于验证 |

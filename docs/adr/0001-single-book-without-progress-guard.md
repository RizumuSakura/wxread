# ADR 0001：只读一本书，并移除进度保护机制

- 状态：已采纳（2026-11-14）
- 影响范围：`main.py`、`book_context.py`、`login_store.py`、`cloud_crypto.py`、`actions_login.py`、两个 workflow、测试

## 背景

旧版本每天按个人书架循环 21 本书，每本只提交 1 段，并且为每次写入加了一层"进度保护"：`progress_guard.py` 会在 `/book/read` 前后回读阅读器页面、比对章节与偏移、写加密 `pending_progress` 记录、并在每次进入书籍时上传一个随机 UUID 的 Actions Cache 检查点。

实际运行暴露三个问题：

1. **进度保护误杀**：手机端只要在运行期间动过进度，`appId` 不一致就判定 `external_change` 并终止整轮（run 43519775809 在 96/240 段停止，run 42913283412 在写前停止）。归属窗口只有 `last_write-2s … +60s`，落在窗口外则进入 `ProgressUncertain`，而文档自认"没有自动授权覆盖或丢弃原始记录的入口"，等于永久卡死后续每次运行。
2. **成本与噪声**：每本书一次 node 子进程 + 一次 cache 上传，实测 1405 个 cache 条目；每段还要承担 PBKDF2 600k 的加解密。
3. **目标本身变了**：只需要每天为一本书累计约 2 小时真实等待时长，不需要覆盖整个书架。

## 决定

1. 默认书籍固定为《被讨厌的勇气》`25615385`（`config.DEFAULT_BOOK_ID`），`READ_NUM` 默认 `240`（240×30s=2h）。
2. 删除 `progress_guard.py`、`.github/actions/reading/`（node24 复合 action、`checkpoint.mjs`、`run.mjs`）、`login_store` 的 `pending_progress` 读写、`book_context.require_position()`，以及对应测试。
3. 删除书架循环：`weread_shelf.py`、`WEREAD_API_KEY`、书架游标 `reading_cursor`、`api_key` 字段。
4. 保留"位置钉死"这一条核心不变量：每段提交都用快照位置（`context.position()` 恒为开始时的 `ci/co/pr`），脚本永不推进自己的进度，因此手机端不会被脚本改写。区别在于**不再拦截、不再回滚**。
5. `cache_prefix()` 改为 `sha256("wxread-cloud-cache-v3\0" + repo)`，只由仓库决定；口令仅参与 AES-GCM KDF。旧 v2 前缀作为字面量 `wxread-login-v2-` 保留一次迁移读取。

## 后果

- 少了一层"写入可归属"的保证：若手机端在运行期间移动了位置，脚本仍按快照位置提交，服务端可能把它当成回退。这是接受的代价——旧机制的实际表现是整轮中止，用户拿不到时长。
- 单段耗时从 36.4 秒降到约 31–32 秒（去掉两次读页与检查点上传）。
- 缓存条目不再随运行次数的书籍数量线性增长。
- 网络失败不再导致整轮作废：单次请求退避重试 3 次，连续 5 段失败才停止。

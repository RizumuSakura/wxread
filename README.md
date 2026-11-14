# wxread

在 **GitHub Actions** 上每天为**一本书**提交真实等待的阅读时长：默认《被讨厌的勇气》（bookId `25615385`），每天 `READ_NUM=240` 段，每段真实等待 30 秒，合计约 2 小时。

不做的事：不随机选章、不自动翻页、不推进任何阅读进度、不刷整个书架、不伪造时长。每段提交的位置恒为开始时的快照位置，等待时间用 `time.monotonic()` 实测后再上报。

## 1. GitHub Actions 配置

仓库 **Settings → Environments → AutoRead → Environment secrets** 只需要一项：

| Secret | 用途 |
| --- | --- |
| `WXREAD_QR_PASSWORD` | 二维码查看口令，至少 20 位；保管好，不要随意更换 |

可选 **Settings → Secrets and variables → Actions → Variables**：

| Variable | 默认 | 说明 |
| --- | --- | --- |
| `READ_NUM` | `240` | 每天提交多少段（1–400），每段约 30 秒 |
| `WXREAD_BOOK_URL` | `25615385` | 换书时填阅读器链接或数字 bookId |

已有口令和有效登录时，直接运行任务即可。

## 2. 扫码登录 / Cookie 失效后续期

1. **Actions → 扫码登录 → Run workflow**，选择 `main`。
2. 等待 **Publish encrypted QR viewer** 完成，下载 `wxread-login-qr` 附件。
3. 解压，用浏览器打开 `login.html`，输入二维码查看口令后用微信扫码。
4. 等待 **Save cloud login state** 成功。后续任务自动使用保存的 Cookie。

二维码等待上限为 10 分钟，过期重新运行。登录态以 AES-GCM 密文存在 Actions Cache，缓存命名只由仓库决定，不含口令派生信息。

## 3. 读哪本书

默认《被讨厌的勇气：“自我启发之父”阿德勒的哲学课》。换书有两种方式：

**只对本次生效**：**Actions → wxread → Run workflow**，在 `book_url` 填入阅读器链接或数字 bookId：

```text
https://weread.qq.com/web/reader/8b9329607186dc198b9bdab
```

阅读器链接从电脑浏览器打开该书后复制，格式为 `https://weread.qq.com/web/reader/...`；不要使用分享页或搜索页地址。

**长期生效**：把 Variables 里的 `WXREAD_BOOK_URL` 改成新值。

优先级：本次 `book_url` → 仓库 `WXREAD_BOOK_URL` → 默认 `25615385`。

## 4. READ_NUM 与运行时间

填 **1–400 的整数**，默认 `240`。每段先真实等待 30 秒再提交。

实测单段总耗时约 **36.4 秒**（30 秒等待 + 约 6.4 秒读页/提交/续期开销），所以真实总时长比 `READ_NUM ÷ 2` 分钟长约 21%：

| READ_NUM | 等待时长 | 实际运行时长（实测推算） |
| --- | --- | --- |
| `1` | 30 秒 | 约 36 秒 |
| `60` | 30 分钟 | 约 36 分钟 |
| `120` | 60 分钟 | 约 73 分钟 |
| `240` | 120 分钟 | 约 145 分钟 |

上限 400 段（约 4 小时）是给阅读步骤 `timeout-minutes: 330` 留余量；填满 600 会被超时掐断。

默认值放在 **Repository Variables**，不是 Secrets；手动输入 `read_num` 可覆盖本次。`0` 不能用来停用工作流，请直接 Disable workflow。

## 5. 定时

`deploy.yml` 声明 `cron: '7 13 * * *'`，即北京时间 **21:07**。GitHub 的 schedule 队列实测会延迟 **4–7.5 小时**，因此任务通常落在凌晨 **01:00–04:30（CST）** 真正开始，避免在早上读书时和本机客户端抢进度。

每日一次的 GitHub 计划任务会被 60 天无活动自动停用，仓库里的 `keepalive-job` 负责保活。

## 6. 可选通知

在 `AutoRead` 环境配置 Secrets，只选择一种渠道；不需要通知就将 `PUSH_METHOD` 留空。

| PUSH_METHOD | 配套 Secret |
| --- | --- |
| `wxpusher` | `WXPUSHER_SPT` |
| `pushplus` | `PUSHPLUS_TOKEN` |
| `telegram` | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` |
| `serverchan` | `SERVERCHAN_SPT` |

旧 `WXREAD_LOGIN_JSON`、`WXREAD_CURL_BASH` 仅用于兼容，完成云端扫码后不必重新配置。

`check_auth` 只检查 Cookie 续期：不阅读、不推送。

## 7. 运行行为

- **位置钉死**：每段提交都用开始时的章节、章节偏移和小数百分比，脚本永不推进自己的进度，因此手机端阅读进度不会被脚本改写。
- **真实等待**：用单调时钟测实际等待秒数，不足 30 秒的段不上报，`rt` 最大 30。
- **网络容错**：单次请求失败按 2 秒、4 秒退避重试，最多 3 次；重试仍失败就重建上下文继续下一段。连续 5 段彻底失败才停止任务，并在通知里写明已确认段数。
- **会话续期**：Cookie 轮换成功即落盘；续期失败回滚到旧 Cookie，不丢可用登录态。
- **不再有进度保护记录**：早期版本的 `pending_progress` 恢复记录、云端检查点与 `abandon` 出口已整体移除，日志里也不会再出现"无法确认变更来源"。

## 故障排查

| 问题 | 处理 |
| --- | --- |
| Cookie 失效 | 重新运行“扫码登录”，确认缓存保存成功 |
| 没生成二维码附件 | 等 Publish 步骤完成；检查 `AutoRead` 口令配置 |
| 一直 Queued | 检查是否有同账号任务运行中或环境待审批 |
| READ_NUM 不生效 | 检查 Variables、本次覆盖值和 `check_auth` 是否勾选 |
| 书籍打不开 | `book_url` 用阅读器链接或数字 bookId，查看 Run reading task 步骤的错误 |
| 日志提示阅读进度变化 | 手机或网页端正在读同一本书；脚本只提交快照位置，不再拦截或回滚 |
| 时长没到账 | 上报的是"接口确认的等待秒数"，不是服务端到账证明，以客户端记录为准 |

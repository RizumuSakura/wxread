# wxread

## 1. GitHub Actions 配置

仓库 **Settings → Environments → AutoRead → Environment secrets**：

| Secret | 用途 |
| --- | --- |
| `WXREAD_QR_PASSWORD` | 二维码查看口令，至少 20 位；保管好，不要随意更换 |
| `WEREAD_API_KEY` | [微信读书官方 Skill](https://weread.qq.com/r/weread-skills) 的 API Key，用于读取个人书架 |

已有这两个配置和有效登录时，直接运行任务即可。

## 2. 扫码登录 / Cookie 失效后续期

1. **Actions → 扫码登录 → Run workflow**，选择 `main`。
2. 等待 **Publish encrypted QR viewer** 完成，下载 `wxread-login-qr` 附件。
3. 解压，用浏览器打开 `login.html`，输入二维码查看口令后用微信扫码。
4. 等待 **Save cloud login state** 成功。后续任务自动使用保存的 Cookie。

二维码等待上限为 10 分钟，过期重新运行。

## 3. 默认：个人书架循环

打开 **Actions → wxread → Run workflow**：

- **不要勾选** `check_auth`。
- `book_url` 留空；仓库 Variable `WXREAD_BOOK_URL` 也应留空。
- `read_num` 留空使用默认 `40`，或填本次总周期数。
- 点击运行。

程序通过官方 Skill 读取个人书架，按返回顺序循环电子书，每本运行一个约 30 秒的周期，再换下一本；一轮结束后回到开头。书架游标加密保存，下次从下一本继续。

`READ_NUM` 是**整次任务的总周期数，不是每本各跑这个数量**。例如书架有 A、B、C 三本，`READ_NUM=5` 时顺序为 **A → B → C → A → B**。

听书专辑、文章收藏入口和无法网页阅读的条目不参与。

### 阅读进度与日志

- 每本开始前加密保存原始章节、章节位置和百分比，结束后回读核对；需要恢复时，恢复成功才换下一本。
- 识别到不同客户端改了进度时，**保留新进度并停止任务**。同浏览器标识不能唯一代表本次脚本：只在有限写入时间窗口内尝试自动恢复；来源不明、时间过久或恢复候选位置再次变化时，保留当前进度和加密恢复记录，停止新提交，等待人工核对。
- 恢复写入前也会上传加密检查点，上传后再次检查当前位置；上传失败不执行恢复写入。跨运行重试仅接受先前核对过的完整恢复候选位置，不能用新时间戳把其他写入认领为自己的。
- 缺少完整起始进度的书会跳过，请先在微信读书中打开一次。
- 日志显示当前书名、书架序号、章节、起点百分比、段数及恢复结果。私密书籍默认隐藏名称；要显示，在 Repository Variables 新增 `WXREAD_SHOW_PRIVATE_TITLES=true`。公开仓库的运行日志也可能公开。
- 强制终止、断网导致无法当场恢复时，保留加密恢复记录；下次运行先处理，未成功前不继续阅读。

这些检查不是服务端原子锁：最后一次回读与实际写入之间仍有竞争窗口，不能保证所有并发情况下都不覆盖。旧云端检查点的写入时间可能早于最后一次提交；不能确认归属时宁可停止，不强行恢复。验证范围和人工核对步骤见 [进度保护验证说明](docs/progress-validation.md)。

## 4. 指定一本书

### 只对本次生效

**Actions → wxread → Run workflow**，在 `book_url` 填入阅读器链接或数字 `bookId`，例如：

```text
https://weread.qq.com/web/reader/8b9329607186dc198b9bdab
```

也可以填 `25615385`，对应《被讨厌的勇气：“自我启发之父”阿德勒的哲学课》。

- `check_auth` 不勾选。
- `read_num` 例如填 `120`，本轮只运行这一本书，累计等待约 60 分钟。
- `skip_push` 勾选后，仅本次不发通知。

阅读器链接从电脑浏览器打开该书后复制，格式为 `https://weread.qq.com/web/reader/...`；不要使用分享页或搜索页地址。

### 定时任务也固定读这一本

**Settings → Secrets and variables → Actions → Variables → New repository variable**：

| Name | Value 示例 |
| --- | --- |
| `WXREAD_BOOK_URL` | `25615385` 或上面的完整阅读器链接 |
| `READ_NUM` | `120` |

书籍选择优先级：**本次 `book_url` → 仓库 `WXREAD_BOOK_URL` → 个人书架循环**。

要恢复默认书架循环，删除或清空 `WXREAD_BOOK_URL`，并将本次 `book_url` 留空。指定单本时不需要官方 API Key，也不会改变书架循环游标。

## 5. READ_NUM 与运行时间

填写 **1–600 的整数**，默认 `40`。每段先等待约 30 秒再提交，循环等待分钟数约为 `READ_NUM ÷ 2`。

| READ_NUM | 循环累计等待 |
| --- | --- |
| `1` | 约 30 秒 |
| `10` | 约 5 分钟 |
| `40` | 约 20 分钟 |
| `60` | 约 30 分钟 |
| `120` | 约 60 分钟 |
| `240` | 约 120 分钟 |

默认值放在 **Repository Variables**，不是 Secrets；手动输入 `read_num` 可覆盖本次。安装依赖、读取书架和网络请求会增加总耗时。`0` 不能用来停用工作流。

默认每天北京时间 **01:07** 运行。修改计划请编辑 `.github/workflows/deploy.yml` 的 `schedule`（UTC）。

`check_auth` 检查 Cookie 续期，不阅读、不推送、不读取书架；如有上次未完成的进度恢复记录，会先处理。

## 6. 可选通知

在 `AutoRead` 环境配置 Secrets，只选择一种渠道；不需要通知就将 `PUSH_METHOD` 留空。

| PUSH_METHOD | 配套 Secret |
| --- | --- |
| `wxpusher` | `WXPUSHER_SPT` |
| `pushplus` | `PUSHPLUS_TOKEN` |
| `telegram` | `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` |
| `serverchan` | `SERVERCHAN_SPT` |

旧 `WXREAD_LOGIN_JSON`、`WXREAD_CURL_BASH` 仅用于兼容，完成云端扫码后不必重新配置。

## 故障排查

| 问题 | 处理 |
| --- | --- |
| Cookie 失效 | 重新运行“扫码登录”，确认缓存保存成功 |
| 官方 Skill 鉴权失败 | 更新 `WEREAD_API_KEY`，不需要为此重新扫码 |
| 没生成二维码附件 | 等 Publish 步骤完成；检查 `AutoRead` 口令配置 |
| 一直 Queued | 检查是否有同账号任务运行中或环境待审批 |
| 没进入书架循环 | 清空本次 `book_url` 和仓库 `WXREAD_BOOK_URL` |
| READ_NUM 不生效 | 检查 Variables、本次覆盖值和 `check_auth` 是否勾选 |
| 指定书籍失败 | 使用阅读器链接或数字 bookId，查看 Run reading task 步骤的错误 |
| 提示无法确认写入来源 / 保留恢复记录 | 停止并发任务，按本次起始日志人工核对原始位置；不要删缓存、换账号或反复强制运行，见进度保护验证说明 |

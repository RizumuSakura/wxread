# wxread

微信读书 **GitHub Actions 云端扫码登录、Cookie 维护与指定书籍计时任务**。

无需本地 Python、无需抓包：在 GitHub 手动触发扫码工作流，用微信确认登录；后续工作流复用加密保存的状态。阅读任务使用同一本书的章节和位置，不再随机拼接书籍、章节。

> 计时按真实等待间隔提交，不做加速、随机造进度或跳过付费权限。接口成功不等于服务端认可相同时长；以微信读书客户端记录为准。Cookie 是否能继续续期也由服务端决定。

## 目录

- [功能与工作流](#功能与工作流)
- [1. 首次配置](#1-首次配置)
- [2. 全云端扫码登录](#2-全云端扫码登录)
- [3. 手机要求四位验证码时](#3-手机要求四位验证码时)
- [4. 指定书籍运行](#4-指定书籍运行)
- [5. READ_NUM 详解](#5-read_num-详解)
- [6. 通知与其他配置](#6-通知与其他配置)
- [7. Cookie 续期与恢复](#7-cookie-续期与恢复)
- [8. 故障排查](#8-故障排查)
- [开发与 Docker 兼容部署](#开发与-docker-兼容部署)

## 功能与工作流

| Actions 中的名称 | 文件 | 用途 |
| --- | --- | --- |
| **扫码登录** | `.github/workflows/login.yml` | 手动生成二维码，等待手机确认，加密保存云端登录状态 |
| **wxread** | `.github/workflows/deploy.yml` | 指定书籍任务；也可勾选“仅验证 Cookie 续期” |
| **Tests** | `.github/workflows/tests.yml` | 自动化测试 |
| **Build and Push Docker Image** | `.github/workflows/docker-publish.yml` | 构建兼容运行镜像，不负责扫码 |

原本的本地扫码命令入口已移除。`weread_auth.py` 只是供云端工作流调用的协议模块，不是新的本地登录命令。

**二维码为什么不是直接显示在公开日志中？**
公开仓库的日志与附件可能被其他人访问。二维码含登录会话标识，不能当作普通图片公开。因此附件中的网页仅在你的浏览器内解密二维码；登录、轮询和 Cookie 保存仍全部在 GitHub Runner 执行。网页不上传口令，也不访问外部服务。

## 1. 首次配置

以 `RizumuSakura/wxread` 的默认分支 `main` 为例。Fork 用户使用自己的仓库即可，无需修改源码里的用户名。

### 1.1 启用 Actions 和 AutoRead 环境

1. 在仓库 **Actions** 页面启用工作流。
2. 打开 **Settings → Environments**，创建 **`AutoRead`**。
3. 环境名必须与工作流一致。若设置了审批或分支限制，要允许默认分支运行；有审批时需先批准任务。

### 1.2 必需 Secret：二维码查看口令

在 **Settings → Environments → AutoRead → Environment secrets** 新增：

| 名称 | 内容 |
| --- | --- |
| `WXREAD_QR_PASSWORD` | 至少 20 位的随机口令，建议使用密码管理器生成并保存 |

这个口令同时保护二维码附件和云端登录状态。**不要放进 Variables、公开工作流输入、源码或日志。** GitHub 不支持读回 Secret 原值，请自行保管。

无需额外创建 GitHub PAT，也无需授予工作流写入 Secrets 的权限。工作流使用平台提供的只读 Token 下载自己的加密附件，并通过 Actions Cache 保存加密状态。

如果已经配置过 `WXREAD_LOGIN_JSON` 或 `WXREAD_CURL_BASH`，可以暂时保留用于迁移；新用户不需要先手动获取这些凭据。

## 2. 全云端扫码登录

### 2.1 生成二维码

1. 打开 **Actions → 扫码登录 → Run workflow**。
2. 分支选 `main`（该工作流只在仓库默认分支执行）。
3. **`resume_run_id` 留空**，点击 **Run workflow**。
4. 打开本次运行，等待 **Publish encrypted QR viewer** 成功。
5. 从运行摘要中的“下载加密二维码附件”链接下载 `wxread-login-qr`；也可在该运行的 Artifacts 列表中找到它。**无需等整个工作流结束。**

此时 **Wait for phone confirmation** 正在等待你扫码，不是卡住了。等待上限为 10 分钟，二维码服务端也可能提前使其过期。

### 2.2 在浏览器显示并扫描

1. 解压下载的附件。
2. 用新版 Chrome、Edge 或 Firefox 打开其中的 **`login.html`**。
3. 输入与 `WXREAD_QR_PASSWORD` 相同的口令，点击“解密并显示二维码”。
4. 用微信扫码，在手机确认登录。
5. 返回 GitHub 工作流查看结果；这个离线网页本身不会自动获知登录结果。

这一步只是浏览器查看二维码，**不需要安装或执行 Python**。不要把解密后的二维码截图公开分享。

### 2.3 确认保存成功

正常情况下，以下步骤都应成功：

- **Wait for phone confirmation**：手机确认、账号验证和加密状态写入。
- **Save cloud login state**：将新状态上传到加密缓存。
- **Update encrypted QR artifact**：更新附件，已确认登录时移除待处理会话文件。

只有确认保存步骤也成功，后续工作流才能使用这次登录。如果只扫码完成但缓存上传失败，不能当作跨次运行保存已经成功。

默认会核对既有登录的账号标识，防止扫到另一个微信读书账号后悄悄替换。可用可选 Variable `WXREAD_ACCOUNT_VID` 明确限定自己的数字用户标识；正常续期不需要改它。

### 2.4 登录后检查一次

打开 **Actions → wxread → Run workflow**：

1. 选择 `main`。
2. 勾选 **“仅验证 Cookie 续期（不阅读、不推送）”**。
3. 运行并打开 `deploy` 任务。
4. 应看到“登录续期检查通过，未执行阅读或消息推送。”。

这个模式不读取书籍章节、不提交计时、不发送通知。保活 Job 成功不能代替 `deploy` 的实际结果。

## 3. 手机要求四位验证码时

通常只需手机确认；如果服务端要求验证码，工作流会显示 **“需要手机验证码”**，保留加密的待处理会话，并以非成功状态结束。这是等待人工续接，不会覆盖原有登录。

1. 记下本次运行的**数字运行 ID**：运行 URL 中 `/actions/runs/` 后面的数字，不是列表里的 `#44` 这类序号。
2. 打开 **Settings → Environments → AutoRead → Environment secrets**。
3. 新建或更新 **`WXREAD_LOGIN_OTP`**，值填手机显示的四位数字。
4. 再次 **Actions → 扫码登录 → Run workflow**。
5. `resume_run_id` 填第 1 步的数字 ID，运行。
6. 新任务会下载旧任务的加密会话，使用 Secret 中的验证码完成登录，不重新生成登录会话。
7. 成功后删除不再需要的 `WXREAD_LOGIN_OTP`，避免下次误用旧验证码。

**请在原二维码的有效期内完成，续接不会延长原有效期。** 已过期时，将 `resume_run_id` 留空，重新生成二维码。

不要把验证码填进公开的工作流输入、Issue、提交信息或日志。运行结束后初始附件链接可能已被替换；应使用摘要底部的“下载最新加密附件”或当前 Artifacts 列表。

## 4. 指定书籍运行

默认目标：

**《被讨厌的勇气：“自我启发之父”阿德勒的哲学课》**

- 阅读器链接：<https://weread.qq.com/web/reader/8b9329607186dc198b9bdab>
- 数字 `bookId`：`25615385`

### 4.1 只为本次选择书籍

打开 **Actions → wxread → Run workflow**：

| 输入 | 怎么填 |
| --- | --- |
| `check_auth` | 运行计时任务时**不要勾选**；勾选则只检查登录 |
| `book_url` | 本次的阅读器链接或数字 bookId，例如 `25615385` |
| `read_num` | 本次周期数，例如 `40`；只测试一段可填 `1` |
| `skip_push` | 可选，勾选后仅本次不发通知，适合试运行 |

`book_url` 留空时，使用仓库 Variable `WXREAD_BOOK_URL`；该变量也没设置时，使用上面的默认书籍。

### 4.2 设置每天运行的固定书籍

打开 **Settings → Secrets and variables → Actions → Variables**，新建 **Repository Variable**：

```text
Name: WXREAD_BOOK_URL
Value: https://weread.qq.com/web/reader/8b9329607186dc198b9bdab
```

也可将 Value 填为 `25615385`。这不是敏感凭据，不必放入 Secrets。

优先级：

```text
本次手动输入 book_url → Repository Variable WXREAD_BOOK_URL → 默认书籍
```

程序会从阅读器页面解析真实书籍标识、当前章节、位置和会话字段，再核对本书目录。书籍、章节、索引和进度来自同一本书；上下文缺失、书籍不一致或目录不可用时会停止，**不会拿其他书籍或旧模板代替**。

任务不会随意跳章节或伪造翻页进度。没有阅读权限、服务端限制或同一位置未被计时时，不会通过改成随机书籍绕开。

### 4.3 默认定时计划

`.github/workflows/deploy.yml` 当前每天北京时间 **01:07** 运行：

```yaml
on:
  schedule:
    - cron: '7 17 * * *'
```

GitHub Actions 的该 cron 使用 UTC，定时也可能延迟。修改计划需提交工作流文件到默认分支；`READ_NUM`、`TZ` 和 `CRON_SCHEDULE` 都不会修改 Actions 的触发时间。

扫码与阅读工作流共用串行锁，避免同时轮换 Cookie。若阅读任务正在执行，扫码任务会先排队；二维码在它真正启动后才生成。

## 5. READ_NUM 详解

`READ_NUM` 是**单轮目标成功计时周期数**，不是分钟数、书籍数、章节数或页数。取值为 **1–600 的整数**，默认 `40`。

新版先进入目标书籍，**真实等待约 30 秒后再提交第一段**；每段最多上报已实际等待的 30 秒，不再把第一次请求倒推为已经读了 30 秒。

```text
无额外延迟时的循环等待分钟数 ≈ READ_NUM × 30 ÷ 60 = READ_NUM ÷ 2
```

| READ_NUM | 正常完成时累计等待 |
| --- | --- |
| `1` | 约 30 秒 |
| `10` | 约 5 分钟 |
| `40` | 约 20 分钟，默认 |
| `60` | 约 30 分钟 |
| `120` | 约 60 分钟 |
| `240` | 约 120 分钟 |
| `600` | 约 300 分钟，上限 |

例如希望每轮约一小时，填 `120`，不是 `60`。

### 设置位置

- **默认/定时任务：** Repository Variable `READ_NUM`。
- **仅本次手动覆盖：** Run workflow 的 `read_num`。
- 两者均留空时默认 `40`。只创建同名 Secret 不会传入该变量。

注意：

- `0` 不能关闭工作流，小数、负数、文字也无效；禁用计划请修改 `schedule` 或在 Actions 禁用工作流。
- 失败、续期、目录修复不计入成功段数；有限重试后仍失败会退出，不保证完成全部目标。
- 修复或重建会话后重新开始真实计时，不把失败耗时重复累计到下一段。
- 排队、安装依赖、网络、重试和通知会增加总耗时。不要把表格视为整个 Actions 的精确结束时间。
- 每次任务从零计数，不跨轮补齐。`check_auth` 模式不执行计时循环。
- 接口必须返回成功和 `synckey` 才计入“已确认段数”；这仍然**不是服务端到账时长证明**。
- 增大这个值不会延长 Cookie 有效期，也不会自动改变书籍或翻页。
- 阅读步骤设有 330 分钟超时，整个运行 Job 为 360 分钟，给缓存保存留出余量；GitHub 排队时间不是脚本在运行的时间。

## 6. 通知与其他配置

### AutoRead Environment Secrets

| 名称 | 必需 | 说明 |
| --- | --- | --- |
| `WXREAD_QR_PASSWORD` | 是 | 二维码及云端登录状态的加密口令，建议随机生成并保管 |
| `WXREAD_LOGIN_OTP` | 否 | 手机要求验证码时临时填写，成功后可删除 |
| `WXREAD_LOGIN_JSON` | 否 | 兼容旧登录配置；成功的云端登录缓存优先 |
| `WXREAD_CURL_BASH` | 否 | 兼容旧 cURL 配置，不再要求新用户抓包 |
| `PUSH_METHOD` | 否 | 留空不通知；只选下面一种渠道 |
| `PUSHPLUS_TOKEN` | 否 | `pushplus` 配套 Token |
| `WXPUSHER_SPT` | 否 | `wxpusher` 配套 SPT |
| `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` | 否 | `telegram` 必须配套设置 |
| `SERVERCHAN_SPT` | 否 | `serverchan` 配套 SendKey |

通知配置示例：

- 不通知：`PUSH_METHOD` 留空，所有通知 Token 不需要填写。
- WxPusher：`PUSH_METHOD=wxpusher` + `WXPUSHER_SPT`。
- PushPlus：`PUSH_METHOD=pushplus` + `PUSHPLUS_TOKEN`。
- Telegram：`PUSH_METHOD=telegram` + 机器人 Token 和 Chat ID。
- ServerChan：`PUSH_METHOD=serverchan` + `SERVERCHAN_SPT`。

通知包含目标书籍、接口确认段数及已提交的实际等待秒数，不再将其直接命名为服务端认可的阅读时长。只填 Token 而未选择渠道不会推送；仅登录检查不推送。

### Repository Variables

| 名称 | 默认值 | 作用 |
| --- | --- | --- |
| `WXREAD_BOOK_URL` | 上面的《被讨厌的勇气》链接 | 定时任务的固定书籍，可填数字 bookId |
| `READ_NUM` | `40` | 每轮目标成功段数 |
| `WXREAD_ACCOUNT_VID` | 从已有登录取得；首次无登录则以首个确认账号为准 | 可选的数字账号标识，防止扫码更换账号 |

同名 Secret 配置以环境级优先于仓库级。若要清理旧 Repository secrets：

1. 先确认“扫码登录”的缓存保存和后续“仅验证 Cookie 续期”均成功。
2. 不再依赖旧登录时，可删除旧 `WXREAD_CURL_BASH` / `WXREAD_LOGIN_JSON`；它们不是新流程的必需项。
3. 保留 `WXREAD_QR_PASSWORD`，以及仍在使用的通知配置。
4. 删除旧登录配置后，云端缓存若被清理，需要重新扫码，不能指望 GitHub 读回已删除的 Secret。

## 7. Cookie 续期与恢复

- 登录状态以 PBKDF2-SHA256 + AES-GCM 加密，密钥不与密文一起缓存。
- 二维码、待处理会话按不同用途加密，并校验仓库、运行 ID 和有效期。公开附件不含明文 UID、Cookie、验证码或解密口令。
- 云端缓存保存 `login.enc`、`cookies.enc` 和设备标识；不会上传本机解密密钥。
- 阅读任务复用同一个 HTTP Session，接收、合并并持久保存更新后的 Cookie。
- 明确续期成功才提交新状态；失败响应不会直接覆盖原有可用会话。
- 两个工作流共用同一缓存命名空间与串行锁；即使后续阅读失败，也会尝试保存已经更新的 Cookie。
- 兼容迁移时会尝试恢复旧缓存，避免只用旧 Secret 中可能已轮换的 Cookie 反复重试。
- 工作流不自动改写 `WXREAD_LOGIN_JSON` Secret，最新状态保存在加密缓存中。

**口令不可随意重置。** 改变 `WXREAD_QR_PASSWORD` 会隔离原缓存，不能解密之前的状态；需要重新扫码。长期停用、缓存被清理、服务端撤销授权、异地风控等情况也可能要求重新扫码。任何一次成功都不能证明永久有效。

## 8. 故障排查

| 现象 | 处理 |
| --- | --- |
| 看不到“扫码登录”工作流 | 确认 `login.yml` 已进入默认分支，Actions 已启用；刷新页面 |
| 缺少二维码查看口令 | 在 `AutoRead` 环境配置 `WXREAD_QR_PASSWORD`，至少 20 位随机口令 |
| 一直 Queued | Job 尚未执行，先看是否有同账号工作流正在运行或环境待审批；这不是 `READ_NUM` 导致 |
| Wait for phone confirmation 一直运行 | 正在等待扫码和手机确认；立即下载附件查看二维码，上限 10 分钟 |
| 看不到附件 | 等 Publish 步骤成功后查看摘要下载链接；不要等整个工作流结束才扫码 |
| 初始附件下载链接失效 | 运行结束时附件会更新，改用摘要底部“最新加密附件”或 Artifacts 列表 |
| 打不开二维码 | 解压后用新版浏览器打开 `login.html`，不是 GitHub 的源码预览；不需要 Python |
| 口令错误 / 解密失败 | 确认使用当前 Secret 对应口令及本次附件；GitHub 不能读回 Secret |
| 二维码或验证码过期 | `resume_run_id` 留空，重新触发生成二维码 |
| 手机要求验证码 | 按第 3 节设置一次性 Secret，再使用原运行数字 ID 续接 |
| 扫到了不同账号 | 不会替换原状态；请用原账号。确需换账号时应明确更新账号限制与旧登录配置 |
| 已扫码但下次没有登录 | 检查 Save cloud login state 是否成功，以及两个工作流是否使用同一环境、口令和分支 |
| 续期检查失败 | 分辨网络故障与授权失效；确实失效时重新运行“扫码登录”，不要反复删除所有状态 |
| READ_NUM 不生效 | 放在 Variables 而非 Secrets；检查手动 read_num 是否覆盖、是否勾选了仅验证登录 |
| 固定书籍未生效 | 检查本次 book_url 是否覆盖仓库 Variable；确认输入为阅读器链接或数字 bookId |
| 上下文/目录校验失败 | 不会回退到随机书籍；检查账号权限和书籍链接，网页协议变化时需更新解析 |
| 脚本成功但书籍时长没增加 | 接口确认不等于服务端计时认可；同一位置、同步延迟及服务端规则均可能影响结果 |
| 没收到通知 | 检查渠道与 Token 配套；仅续期检查不发送通知；通知重试可能延长总用时 |

不要公开口令、登录 JSON、Cookie、验证码或解密后的二维码。可以分享去除敏感内容后的错误和步骤名称。

## 开发与 Docker 兼容部署

普通用户只需上面的 GitHub Actions 流程。下面仅用于开发或已持有凭据的兼容运行，**不是本地扫码教程**：

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

主要模块：

| 文件 | 作用 |
| --- | --- |
| `actions_login.py` | 云端扫码、验证码续接、缓存元数据 |
| `weread_auth.py` | 扫码协议库，没有本地命令入口 |
| `qr_viewer.html` / `cloud_crypto.py` | 浏览器内二维码解密及云端加密格式 |
| `login_store.py` / `cookie_store.py` | 登录状态、Cookie 的持久保存 |
| `book_context.py` / `reader_protocol.py` | 目标书籍、章节上下文及网页请求编码 |
| `main.py` | 固定书籍与真实等待周期 |
| `scheduler.py` / `push.py` | 兼容定时运行与可选通知 |

Docker 镜像仍可运行 `main.py` / `scheduler.py`，但容器不会自动读取 GitHub Secrets 或下载云端缓存，也不再提供本地扫码。已有凭据的使用者需自行安全配置环境和持久状态；主流程无需 Docker。

`.env.example` 只是配置参考，Python 不会自动读取它。`TZ` / `CRON_SCHEDULE` 仅供容器调度，不影响 GitHub 的 `schedule`。修改 Compose 环境后使用 `docker compose up -d`，源码变更还需 `--build`。

测试使用模拟账号和响应，不以测试数量或接口成功代替实际计时效果。协议与验证边界见 [docs/session-research.md](docs/session-research.md)；会话维护参考 [finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)（第三方插件）。

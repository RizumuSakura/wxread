# wxread

微信读书扫码登录、会话续期与官方阅读数据查询工具，支持本地、Docker 和 GitHub Actions。

首次用微信扫码确认，之后复用加密保存的登录状态，无需手动抓包或复制 cURL。原有脚本入口保留，但脚本执行次数和接口返回成功不等同于服务端认可的阅读时长。

## 功能

| 功能 | 当前支持情况 |
| --- | --- |
| 微信扫码登录 | 本地生成二维码，支持手机确认和四位验证码 |
| 登录状态保存 | Cookie 与可取得的官方 API Key 加密保存在本机 |
| GitHub Actions 登录配置 | 可将登录状态加密同步为 Repository 或 Environment Secret |
| Cookie 续期 | 保留完整 Cookie，跨任务恢复；续期失败时保留原状态 |
| 指定书籍查询 | 输入阅读器链接或数字 bookId，查询书籍信息和个人进度 |
| 官方阅读统计 | 查询周、月、年或累计数据，按官方字段单位显示 |
| 结果通知 | 原脚本支持 PushPlus、WxPusher、Telegram、ServerChan |

## 快速开始

需要 Python 3.10 或以上。Windows 可以用 `py -3` 替代下面的 `python`。

```bash
git clone https://github.com/RizumuSakura/wxread.git
cd wxread
python -m pip install -r requirements.txt
python qr_login.py
```

程序会生成并打开二维码图片。用微信扫码，在手机确认登录；若手机显示四位验证码，在终端输入即可。验证码输入不会回显。

成功后，凭据保存在项目的 `.state/login.enc`，解密密钥为同目录的 `login.key`。目录已被 Git 和 Docker 构建上下文忽略；请将这两个文件留在自己控制的机器上。

登录不会自动开通官方 Skill。若账号已经开通，程序会尝试读取已有 API Key；未取得 Key 时，网页登录仍会保存，官方查询可另行配置。

不希望自动打开图片，或通过 SSH 使用时：

```bash
python qr_login.py --no-open
```

打开程序输出的本地 `login-qr.png` 扫码。二维码和等待时间有限，过期后重新执行命令；未完成的扫码不会替换原登录状态。

## 查询指定书籍

官方查询使用独立的 API Key，不需要网页 Cookie。可在[微信读书官方 Skill 页面](https://weread.qq.com/r/weread-skills)开通并获取 Key，通过受保护的 `WEREAD_API_KEY` 环境变量提供；扫码已保存 Key 时也可直接使用。

例如，《被讨厌的勇气：“自我启发之父”阿德勒的哲学课》对应 `bookId=25615385`：

```bash
# 只查询公开元数据，无需登录
python official_skill.py book "https://weread.qq.com/web/reader/8b9329607186dc198b9bdab" --metadata-only

# 查询自己的进度和接口返回的累计时长
python official_skill.py book 25615385

# 链接也可以直接用于个人进度查询
python official_skill.py book "https://weread.qq.com/web/reader/8b9329607186dc198b9bdab"

# 查询本月统计；也支持 weekly、annually、overall
python official_skill.py stats --mode monthly

# 导出带明确单位的 JSON
python official_skill.py book 25615385 --json
```

`progress=1` 显示为 `1%`。时长按照官方说明从秒换算显示，缺失数据标为“未返回”，不会当成零。对真实阅读做前后查询，可核对服务端记录的变化；普通统计不能替代某项排行榜或挑战赛的计时结果。

当前接入的官方 Skill 版本为 `1.0.5`。接口要求升级时程序会停止查询，先核对[官方文档](https://github.com/Tencent/WeChatReading/blob/main/skills/SKILL.md)再更新版本。

## GitHub Actions

工作流使用 `.github/workflows/deploy.yml`，默认每天北京时间 **01:07** 运行，也支持手动触发。

### 1. 同步扫码登录状态

在能扫码的本机运行以下命令，将仓库名替换成自己的地址：

```bash
python qr_login.py --github-repo YOUR_NAME/wxread --github-environment AutoRead
```

程序在扫码成功后，通过 GitHub 公钥加密凭据，写入该仓库 `AutoRead` 环境的 `WXREAD_LOGIN_JSON` Secret。上传使用当前 Git 的 GitHub 登录凭据，或环境变量中的 `GH_TOKEN` / `GITHUB_TOKEN`；凭据必须有目标环境 Secrets 的写入权限。无需把 GitHub Token 填进源码，也无需让 Actions 写回 Secrets。

如果扫码已完成，只想再次同步：

```bash
python qr_login.py --sync-only --github-repo YOUR_NAME/wxread --github-environment AutoRead
```

不使用 Environment Secret 时，省略 `--github-environment AutoRead`，将写入 Repository Secret。工作流可读取仓库级 Secret；若环境里存在同名项，以环境配置为准。

同步失败不会删除本地登录状态。可以处理 GitHub 登录或权限问题后，用 `--sync-only` 重试；同步时会读取已经续期过的最新 Cookie。

### 2. 启用并检查工作流

1. 确认修复代码和工作流文件已经进入默认分支。
2. 打开仓库 **Actions**，启用 `wxread` 工作流；Fork 后可能需要手动启用。
3. 点击 **Run workflow**，勾选“仅验证 Cookie 续期”。此模式不执行原阅读脚本，也不推送消息。
4. 第一次成功后，再执行一次检查。日志应显示“已恢复上次运行保存的 Cookie”。

若 GitHub 拒绝启动工作流，先处理其页面或 API 返回的具体限制；本地测试通过不代表 Actions 已经运行。

### 3. 可选配置

#### `READ_NUM`：每次任务的目标成功次数

`READ_NUM` **不是分钟数，也不是书籍数、章节数或页数**。它控制 `main.py` 每次启动后，需要完成多少次被脚本判定为成功的阅读请求；默认 `40`。

当前实现每成功一次，等待 **30 秒**，最后一次成功后也会等待。因此，在没有失败和额外延迟时：

```text
循环内的等待时间（分钟）= READ_NUM × 30 ÷ 60 = READ_NUM ÷ 2
READ_NUM = 期望的循环等待分钟数 × 2
```

| `READ_NUM` | 循环内累计等待 | 适用示例 |
| --- | --- | --- |
| `1` | 约 30 秒 | 快速检查原脚本能否完成一次请求 |
| `10` | 约 5 分钟 | 短时间试运行 |
| `40` | 约 20 分钟 | 默认值 |
| `60` | 约 30 分钟 | 半小时量级 |
| `120` | 约 60 分钟 | 一小时量级 |
| `240` | 约 120 分钟 | 两小时量级 |

**例如：想让每次任务等待约一小时，填 `120`，不是 `60`。** 这里描述的是脚本等待时间，不是微信读书保证计入的阅读时长。

需要注意：

- 填写不带引号的正整数，如 `40`、`120`。未设置或留空时回退为 `40`；`0`、负数、小数、`20分钟` 都不是有效的运行配置。`0` 不能用来关闭定时任务。
- 当前代码在 HTTP 成功、`succ=1` 且返回中存在 `synckey` 时计数加一；登录续期、章节修复及失败请求不占用成功次数。部分异常会有限重试，其他异常会提前终止，不保证一定完成设定次数。
- 当前版本是先请求、成功后再等待 30 秒；日志和通知中的“分钟”也是按成功次数乘以 `0.5` 估算，不是向服务端查询得到的已认可时长。
- 整个 Actions 的用时还包括排队、安装依赖、续期、网络请求、重试和消息推送，通常不等于表中的时间；推送失败重试也可能让任务结束得更晚。
- `READ_NUM` **只控制单次运行的数量**，不控制每天运行几次；运行频率由工作流的 `schedule` 决定。手动执行一次也是一轮，下一轮重新从零计数，不跨任务累计剩余次数。
- 勾选手动运行中的“仅验证 Cookie 续期”时，不执行阅读循环，也不推送消息；需要检查登录时优先使用该模式，而不是增大 `READ_NUM`。
- 增大该值不会延长 Cookie 有效期，也不会选择目标书籍。当前 `main.py` 仍使用原项目的随机书籍、章节逻辑，不能靠此变量指定某本书；服务端实际是否计时需另行验证。

**GitHub Actions 设置位置：**

1. 打开仓库 **Settings → Secrets and variables → Actions → Variables**。
2. 点击 **New repository variable**；已有同名变量时编辑它。
3. Name 填 `READ_NUM`，Value 填例如 `120`，保存。
4. 下次启动 `wxread` 工作流生效，已经运行中的任务不会改变。

这是普通配置，放在 **Repository Variables**，不是 Secrets。当前工作流通过 `${{ vars.READ_NUM }}` 读取它；仅创建同名 Secret 不会传给 `READ_NUM`。

#### 登录与通知：放在 Secrets

以下为 **Repository Secrets** 或 **`AutoRead` Environment Secrets**。登录至少需要一套有效凭据，其余按需配置；没有消息通知需求时，所有推送项都可留空。

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `WXREAD_LOGIN_JSON` | 无 | **推荐登录方式**。由扫码工具生成并同步的完整登录数据，不要自行拼装或提交进仓库；工作流将它作为登录与加密缓存的初始凭据。 |
| `WXREAD_CURL_BASH` | 无 | 兼容旧版抓包登录。已配置有效的 `WXREAD_LOGIN_JSON` 时无需再填；二者同时存在时优先使用 JSON，不会因 JSON 失效而自动回退到旧 cURL。 |
| `PUSH_METHOD` | 空，不通知 | 只选一个渠道：`pushplus`、`wxpusher`、`telegram`、`serverchan`；不要填中文名或逗号分隔列表。只填 Token、不选择渠道不会发送通知。 |
| `PUSHPLUS_TOKEN` | 空 | `PUSH_METHOD=pushplus` 时使用的 PushPlus Token。 |
| `WXPUSHER_SPT` | 空 | `PUSH_METHOD=wxpusher` 时使用的 WxPusher SPT。 |
| `TELEGRAM_BOT_TOKEN` | 空 | `PUSH_METHOD=telegram` 时使用的机器人 Token，必须同时提供 `TELEGRAM_CHAT_ID`。 |
| `TELEGRAM_CHAT_ID` | 空 | Telegram 接收会话的 Chat ID；与机器人 Token 配套配置。 |
| `SERVERCHAN_SPT` | 空 | `PUSH_METHOD=serverchan` 时使用的 ServerChan SendKey。 |

例如，**不需要推送**时只保留有效登录 Secret 即可；**继续使用 WxPusher** 时保留 `PUSH_METHOD=wxpusher` 和 `WXPUSHER_SPT`，其余渠道的 Token 无需配置。

仓库级 Secret 位于 **Settings → Secrets and variables → Actions → Secrets**；环境级 Secret 位于 **Settings → Environments → AutoRead → Environment secrets**。[同名 Secret 以环境级配置为准](https://docs.github.com/en/actions/reference/security/secrets#naming-your-secrets)；删除仓库级配置前先确认环境里有可用替代项，删除后无法从 GitHub 读取原值恢复。

#### 本地 / Docker 的可选环境变量

| 名称 | 默认值 | 生效范围与用途 |
| --- | --- | --- |
| `WXREAD_LOGIN_FILE` | 项目目录的 `.state/login.enc` | 本地扫码凭据文件；配套密钥位于同目录同名 `.key` 文件。更改路径时需保留两者。Compose 默认挂载 `./state` 到 `/app/.state`，不会自动转发宿主机的此变量。 |
| `WXREAD_COOKIE_FILE` | 当前工作目录的 `.state/cookies.enc` | 后续轮换 Cookie 的加密缓存；文件与初始凭据关联。Actions 和 Compose 已固定路径，一般无需改。 |
| `WEREAD_API_KEY` | 使用扫码已保存的 Key | 仅供 `official_skill.py` 的官方查询使用，不用于 `main.py` 的网页登录/续期，不能代替 Cookie。当前阅读工作流没有传入此变量。 |
| `TZ` | `Asia/Shanghai` | 容器调度时区。直接运行 `main.py` 不会因为设置它而变成定时任务。 |
| `CRON_SCHEDULE` | `0 1 * * *` | 仅 `scheduler.py` / 容器调度使用，依次为“分 时 日 月 周”；默认每天北京时间 01:00。**不改变 GitHub Actions 的 `schedule`。** |

同一轮的数量在本地和 Docker 中也通过 `READ_NUM` 设置。例如 PowerShell：

```powershell
$env:READ_NUM = "120"
python main.py
# 只检查登录：python main.py --check-auth
```

Linux / macOS：

```bash
READ_NUM=120 python main.py
```

Docker Compose 可在与 `docker-compose.yml` 相同目录的 `.env` 文件中设置：

```dotenv
READ_NUM=120
TZ=Asia/Shanghai
CRON_SCHEDULE=0 1 * * *
```

修改后执行 `docker compose up -d`，让 Compose 按新环境配置重建容器；[只执行 `docker compose restart` 不会更新容器已有的环境变量](https://docs.docker.com/reference/cli/docker/compose/restart/)。这不会修改 GitHub Actions 的配置。

Python 脚本本身不会自动读取 `.env` 文件。`.env.example` 是配置参考，请由终端或部署平台传入环境变量；Docker Compose 也可通过其 `.env` 插值机制传入。

## 本地与 Docker

检查本地登录能否续期：

```bash
python main.py --check-auth
```

运行原脚本：

```bash
python main.py
```

Docker 首次建议在宿主机完成扫码，再将登录状态目录挂载进容器。现有 `docker-compose.yml` 将宿主机 `./state` 挂载为容器 `/app/.state`；因此可以这样初始化：

```bash
# Linux / macOS：首次扫码写入 Compose 将挂载的目录
WXREAD_LOGIN_FILE=./state/login.enc python qr_login.py

```

PowerShell 首次扫码可用：

```powershell
$env:WXREAD_LOGIN_FILE = ".\state\login.enc"
python qr_login.py
```

Compose 默认从当前源码构建镜像，不再包含必须手动填写的 cURL 占位内容。初始化登录后启动：

```bash
docker compose up -d --build
docker compose logs -f
docker compose exec wxread python main.py --check-auth
```

`CRON_SCHEDULE` 是容器调度规则，默认 `0 1 * * *`；`TZ` 默认 `Asia/Shanghai`。它们不控制 GitHub Actions 的运行时间。

## 登录状态如何维护

- 所有网页请求共用 Session，接收完整 Cookie，保留过期时间，避免新旧同名凭据冲突。
- 续期响应明确成功后才提交新状态；失败、非预期响应或中断不会覆盖已保存的可用登录。
- Actions 在运行前恢复加密的 `.state/cookies.enc`，后续任务失败时也会尝试保存已确认的 Cookie 更新。
- 登录输入变化会隔离旧缓存。服务端强制注销、授权撤销或不再允许续期时，重新扫码并同步 Secret。

登录来源优先级：`WXREAD_LOGIN_JSON` → `WXREAD_CURL_BASH` → 本地扫码状态。显式的 `WEREAD_API_KEY` 优先于扫码保存的 API Key。

可用 `WXREAD_LOGIN_FILE` 指定本地扫码状态位置，用 `WXREAD_COOKIE_FILE` 指定续期缓存位置。默认文件如下：

| 文件 | 用途 |
| --- | --- |
| `.state/login.enc` | 加密的初始登录状态 |
| `.state/login.key` | 本机登录状态解密密钥 |
| `.state/device.json` | 该安装实例的设备标识 |
| `.state/cookies.enc` | 后续请求更新后的 Cookie 状态 |

源码和现有测试都不能证明 Cookie 或官方 API Key 永久有效。长期停用、缓存清理或重新授权后，可能需要再扫码一次。

## 当前边界

指定书籍的**官方查询**已支持。`main.py` 仍保留原项目的固定阅读模板和随机书籍、章节逻辑，尚未改造成根据链接维护同一本书真实位置的阅读客户端。

官方 Skill 的公开文档没有提供上传阅读时长的能力。本项目不把请求次数、等待时长或成功响应作为实际阅读效果的证明。认证方式与参考插件的研究见 [docs/session-research.md](docs/session-research.md)。

## 开发与验证

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试覆盖扫码授权状态、凭据加密、GitHub Secret 加密上传、Cookie 轮换与回滚、书籍链接解析、官方鉴权和统计单位。自动化测试使用替身账号和响应，不提交真实阅读时长。

## 参考

- [微信读书官方 Skill](https://github.com/Tencent/WeChatReading)
- [微信读书 Skill 入口](https://weread.qq.com/r/weread-skills)
- [finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)：扫码协议、会话维护方式的参考项目，非腾讯官方插件

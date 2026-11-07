# wxread

微信读书扫码登录、Cookie 维护与原有脚本运行工具，支持 **GitHub Actions、本地 Python、Docker Compose**。

首次用微信扫码确认，随后复用加密保存的登录状态，无需手动抓包或复制 cURL。Cookie 能否继续续期由微信读书服务端决定，本项目不承诺永久登录或服务端一定认可脚本计算的时长。

## 目录

- [功能与当前边界](#功能与当前边界)
- [1. 准备运行环境](#1-准备运行环境)
- [2. 首次扫码登录](#2-首次扫码登录)
- [3. GitHub Actions 完整教程](#3-github-actions-完整教程)
- [4. 可选配置详解](#4-可选配置详解)
- [5. 定时运行设置](#5-定时运行设置)
- [6. 本地运行](#6-本地运行)
- [7. Docker Compose 部署](#7-docker-compose-部署)
- [8. Cookie 保存与续期](#8-cookie-保存与续期)
- [9. 故障排查](#9-故障排查)
- [开发与测试](#开发与测试)

## 功能与当前边界

| 功能 | 说明 |
| --- | --- |
| 微信扫码登录 | 本机生成二维码，手机确认；需要时在终端输入四位验证码 |
| 本地凭据加密 | 初始登录状态、后续更新的 Cookie 分别加密保存 |
| 同步 GitHub 登录凭据 | 通过 GitHub 公钥加密，写入 Repository 或 Environment Secret |
| Cookie 续期与恢复 | 尽可能保留完整 Cookie；跨次运行恢复，续期失败不覆盖原状态 |
| 仅验证登录 | `--check-auth` 不执行阅读循环，也不发送通知 |
| 原脚本运行与通知 | 保留原入口，支持 PushPlus、WxPusher、Telegram、ServerChan |

**请先了解以下限制：**

- 当前 `main.py` 仍使用原项目的固定位置模板及随机书籍、章节逻辑，**尚未实现按链接持续阅读指定书籍**。修改 `READ_NUM` 不能解决这个问题。
- 请求成功、脚本等待了多久、日志显示多少分钟，都不能代替服务端认可的阅读记录。
- 当前二维码在**本机**生成；现有 `wxread` 工作流只负责运行和登录续期，**不提供云端生成登录二维码的独立工作流**。登录完全失效时，请按本文重新本地扫码并同步。
- 本教程只介绍当前仓库已有的入口，不将待开发能力写成已支持。

## 1. 准备运行环境

### 1.1 安装要求

- Python **3.10 或以上**。
- Git：下载仓库及使用现有 GitHub 登录凭据时需要。
- 能在手机上使用微信扫码确认。
- 使用 Actions 时，需要目标仓库的管理权限，并已启用 Actions。
- 使用 Docker 时，需要 Docker Engine/Desktop 和 Compose 插件。

先检查 Python：

```bash
python --version
python -m pip --version
```

Windows 若没有可用的 `python` 命令，可尝试 `py -3`；后面的 `python` 也应相应替换。若两者都不可用，先安装 Python，不要继续运行后续命令。

### 1.2 获取代码与依赖

以下以本仓库为例；使用自己的 Fork 时，替换仓库地址：

```bash
git clone https://github.com/RizumuSakura/wxread.git
cd wxread
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

如果 PowerShell 阻止激活脚本，不必修改系统执行策略，可直接使用虚拟环境解释器：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 后续命令中的 python 同样替换为 .\.venv\Scripts\python.exe
```

Linux / macOS：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 2. 首次扫码登录

在项目目录打开终端，执行：

```bash
python qr_login.py
```

1. 程序生成并尝试打开 `.state/login-qr.png`。
2. 用微信扫描二维码，在手机确认登录。
3. 如果手机显示四位验证码，在终端提示处输入；输入不会回显。
4. 出现“登录成功；凭据已加密保存。”后完成。

不自动打开图片的服务器或 SSH 环境可以执行：

```bash
python qr_login.py --no-open
```

将程序输出路径对应的图片安全传到自己的设备查看。**不要将二维码上传到公开仓库、Issue 或公开日志。**

默认等待扫码 300 秒，最长可设置 600 秒：

```bash
python qr_login.py --no-open --timeout 600
```

等待时间是程序的上限，不能延长服务端已经过期的二维码。超时、取消或验证失败不会替换原登录文件；需要时重新运行生成新的二维码。

### 2.1 登录后先检查，不运行阅读循环

```bash
python main.py --check-auth
```

成功时日志包含“登录续期检查通过，未执行阅读或消息推送。”，进程退出码为 0。

### 2.2 保存了哪些文件

| 文件 | 用途 |
| --- | --- |
| `.state/login.enc` | 加密的初始扫码登录状态 |
| `.state/login.key` | 上述登录文件的解密密钥 |
| `.state/device.json` | 本安装实例的设备标识 |
| `.state/cookies.enc` | 后续请求与续期更新后的加密 Cookie |
| `.state/login-qr.png` | 临时登录二维码 |

这些文件已被 Git 忽略，也不会进入 Docker 构建上下文。备份时应保留整个状态目录，并限制访问权限；**加密文件与解密密钥放在一起时，拥有整个目录的人仍可使用账号会话**。

## 3. GitHub Actions 完整教程

下面使用仓库 `RizumuSakura/wxread` 和环境 `AutoRead` 举例。Fork 用户需要将命令中的仓库名替换成自己的。

### 3.1 创建环境并启用工作流

1. 打开目标仓库 **Settings → Environments**。
2. 创建名称为 **`AutoRead`** 的环境。名称要与工作流中的 `environment: AutoRead` 一致。
3. 如为此环境设置了审批或分支限制，确保 `main` 可以运行；设置审批后，每次任务可能需要人工批准。
4. 打开仓库 **Actions**，按页面提示启用工作流。

无需把 Cookie 或登录 JSON 填进源码，也不需要先创建一个空的登录 Secret。

### 3.2 将登录状态同步到环境 Secret

**已有本地扫码状态：**

```bash
python qr_login.py --sync-only --github-repo RizumuSakura/wxread --github-environment AutoRead
```

**尚未扫码，希望扫码成功后直接同步：**

```bash
python qr_login.py --github-repo RizumuSakura/wxread --github-environment AutoRead
```

工具会取得 GitHub 的加密公钥，将登录状态加密后写入：

```text
仓库：RizumuSakura/wxread
环境：AutoRead
Secret 名称：WXREAD_LOGIN_JSON
```

成功提示为“登录状态已加密同步到 GitHub Secret WXREAD_LOGIN_JSON。”。可以到 **Settings → Environments → AutoRead → Environment secrets** 查看该名称是否存在；GitHub 不会显示 Secret 的原值。

**GitHub 授权要求：**

- 上传按顺序使用终端环境中的 `GH_TOKEN`、`GITHUB_TOKEN`，或 Git 凭据管理器中已有的 GitHub HTTPS 凭据。
- 凭据必须有目标仓库/环境 Secret 的写入权限；仅能克隆公共仓库不代表有此权限。
- 如果显式设置的 Token 已过期，它会优先于 Git 凭据；先移除失效环境变量再重试。
- 不要把 GitHub Token 写进源码、README、命令参数或提交记录。
- 权限不够会导致同步失败，但不会删除已完成的本地登录；修复权限后重新执行 `--sync-only` 即可。
- GitHub 环境 Secret 接口的权限要求见[官方文档](https://docs.github.com/en/rest/actions/secrets#create-or-update-an-environment-secret)。

不使用环境级 Secret 时，可省略 `--github-environment AutoRead`，写入 Repository Secret。若两处有同名 Secret，[环境级配置优先](https://docs.github.com/en/actions/reference/security/secrets#naming-your-secrets)；避免两处保留不同账号的登录数据。

### 3.3 第一次只检查登录

1. 打开 **Actions → wxread → Run workflow**。
2. 分支选择 **`main`**。
3. **勾选“仅验证 Cookie 续期（不阅读、不推送）”**。
4. 点击 **Run workflow**，等待任务结束。
5. 打开 `deploy` 任务，查看 `Run deployment script` 步骤。

预期看到“登录续期检查通过，未执行阅读或消息推送。”。第一次成功后，可再执行一次同样的检查，确认日志出现“已恢复上次运行保存的 Cookie。”。

`keepalive-job` 是工作流保活任务，其成功不代表登录检查成功；请看 `deploy` 的实际结果。排队中也不代表检查通过。

### 3.4 配置并运行原脚本

1. 按下一节设置 `READ_NUM`，不设置则使用默认 `40`。
2. 再次进入 **Actions → wxread → Run workflow**。
3. 选择 `main`，**不勾选**“仅验证 Cookie 续期”。
4. 运行并查看 `deploy` 日志。

手动运行会执行一轮；不会改变后续的定时计划。当前默认定时为每天北京时间 **01:07**。

### 3.5 Cookie 完全失效后重新登录

在原本保存状态的本机执行：

```bash
python qr_login.py --github-repo RizumuSakura/wxread --github-environment AutoRead
```

用手机重新确认；成功后将更新同名 Secret。然后按 3.3 再运行一次仅验证登录。

不是每次失败都需要重新扫码：网络错误、限流、业务错误、GitHub 权限不足应按日志分别排查，反复扫码不能解决所有问题。

## 4. 可选配置详解

### 4.1 READ_NUM：单次任务目标成功次数

`READ_NUM` **不是分钟数，不是书籍数、章节数或页数**。它控制 `main.py` 每次启动后，需要完成多少次被脚本判定成功的阅读请求；默认 `40`。

当前代码在 HTTP 成功、`succ=1` 且返回中存在 `synckey` 时计数加一，随后等待 30 秒，最后一次也会等待。因此：

```text
循环累计等待分钟数 = READ_NUM × 30 ÷ 60 = READ_NUM ÷ 2
READ_NUM = 希望的循环等待分钟数 × 2
```

| READ_NUM | 循环累计等待 | 说明 |
| --- | --- | --- |
| `1` | 约 30 秒 | 检查原脚本能否完成一次请求；只检查登录应使用仅续期模式 |
| `10` | 约 5 分钟 | 短时间试运行 |
| `40` | 约 20 分钟 | 默认值 |
| `60` | 约 30 分钟 | 半小时量级 |
| `120` | 约 60 分钟 | 一小时量级 |
| `240` | 约 120 分钟 | 两小时量级 |

**希望每轮循环等待约一小时，应填 `120`，不是 `60`。**

需要注意：

- 只填不带引号的正整数；未设置或留空时默认 `40`。`0`、负数、小数、`20分钟` 无效；`0` 不能用来关闭任务。
- 失败、登录续期、章节修复不计入成功次数。部分异常有限重试，其他异常会提前终止，不保证一定完成设定数量。
- 当前实现先发请求，成功后再等待。日志、通知中的“分钟”按成功次数乘以 `0.5` 估算，**不是从服务端读取的已认可时长**。
- 排队、安装依赖、续期、网络和消息推送还会增加总耗时，不能只按表格估计整个 Actions 的结束时间。
- 它只控制一轮数量，不控制每天执行几轮。每次任务重新从零计数，不延续上一轮未完成数量。
- 勾选“仅验证 Cookie 续期”时不进入阅读循环，`READ_NUM` 不决定续期次数。
- 增大该值不会延长 Cookie 有效期、不会选择书籍，也不保证服务端增加相同时长。

**GitHub 设置位置：**

1. **Settings → Secrets and variables → Actions → Variables**。
2. 点击 **New repository variable**，已有同名项则编辑。
3. Name 填 `READ_NUM`，Value 例如填 `120`。
4. 保存后，下次启动的工作流生效；不会修改正在运行的任务。

这是普通 **Variable，不是 Secret**。工作流读取 `${{ vars.READ_NUM }}`，只创建同名 Secret 不会传入该配置。

### 4.2 登录与通知 Secrets

仓库级位置：**Settings → Secrets and variables → Actions → Secrets**。

环境级位置：**Settings → Environments → AutoRead → Environment secrets**。

| 名称 | 默认值 | 用途 |
| --- | --- | --- |
| `WXREAD_LOGIN_JSON` | 无 | 推荐的登录凭据，由扫码工具生成并同步，勿手工拼装 |
| `WXREAD_CURL_BASH` | 无 | 兼容旧版抓包登录；有效扫码 Secret 存在时无需再填 |
| `PUSH_METHOD` | 空 | 空值不通知；只选 `pushplus`、`wxpusher`、`telegram`、`serverchan` 之一 |
| `PUSHPLUS_TOKEN` | 空 | PushPlus 使用的 Token |
| `WXPUSHER_SPT` | 空 | WxPusher 使用的 SPT |
| `TELEGRAM_BOT_TOKEN` | 空 | Telegram 机器人 Token，需同时配置 Chat ID |
| `TELEGRAM_CHAT_ID` | 空 | Telegram 接收会话的 Chat ID |
| `SERVERCHAN_SPT` | 空 | ServerChan 使用的 SendKey |

**按需选择，不必全部填：**

| 需求 | 需要的通知 Secrets |
| --- | --- |
| 不需要通知 | 全部留空，尤其是 `PUSH_METHOD` |
| WxPusher | `PUSH_METHOD=wxpusher` + `WXPUSHER_SPT` |
| PushPlus | `PUSH_METHOD=pushplus` + `PUSHPLUS_TOKEN` |
| Telegram | `PUSH_METHOD=telegram` + `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |
| ServerChan | `PUSH_METHOD=serverchan` + `SERVERCHAN_SPT` |

一个任务只选择一个通知渠道。只填 Token 不选 `PUSH_METHOD` 不会发送；不要填中文渠道名或逗号分隔列表。

**Repository secrets 可以全删吗？**

- 若 `AutoRead` 环境已经有可用的 `WXREAD_LOGIN_JSON`，且完全不需要通知，旧仓库级登录/通知配置可以在验证后删除。
- `WXREAD_CURL_BASH` 是旧登录方式；新版扫码登录通过 Actions 检查后，可以删除旧项。
- 仍需 WxPusher 时，保留对应的 `PUSH_METHOD` 和 `WXPUSHER_SPT`，或先迁移至环境级再删除仓库级项。
- 不要删除正在使用的环境级登录 Secret。GitHub 无法读回 Secret 原值，删除前确认有本地登录状态或可以重新扫码。

### 4.3 本地与容器的环境变量

| 名称 | 默认值 | 作用 |
| --- | --- | --- |
| `WXREAD_LOGIN_FILE` | 项目目录的 `.state/login.enc` | 本机扫码状态位置；配套密钥为同目录同名 `.key` 文件 |
| `WXREAD_COOKIE_FILE` | 当前工作目录的 `.state/cookies.enc` | 更新后的加密 Cookie 缓存；Actions/Compose 已固定自己的路径 |
| `READ_NUM` | `40` | 单次任务目标成功次数 |
| `TZ` | `Asia/Shanghai` | `scheduler.py` / 容器调度时区 |
| `CRON_SCHEDULE` | `0 1 * * *` | `scheduler.py` / 容器的五字段调度规则，默认每天 01:00 |

**Python 脚本不会自动加载 `.env`。** 直接运行时请在终端设置环境变量；`.env.example` 仅作参考。Compose 可以通过同目录 `.env` 插值传入其文件中声明的变量，但不会自动转发所有宿主机变量。

## 5. 定时运行设置

### GitHub Actions

配置文件为 `.github/workflows/deploy.yml`，`schedule` 使用 **UTC**。当前：

```yaml
on:
  schedule:
    - cron: '7 17 * * *'
```

对应每天北京时间 **01:07**。修改此文件并提交到默认分支才能改变 Actions 定时；例如 UTC `0 0 * * *` 对应北京时间每天 08:00。计划任务可能延迟，不保证准点启动，具体规则见 [GitHub 官方说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

`READ_NUM`、`TZ`、`CRON_SCHEDULE` 都不会更改这里的定时。不要只为延长一轮而增加多个触发时间；先区分“一轮数量”和“一天频率”。

### Docker / scheduler.py

`CRON_SCHEDULE` 依次为“分 时 日 月 周”，由 `TZ` 指定时区：

```dotenv
TZ=Asia/Shanghai
CRON_SCHEDULE=0 1 * * *
```

这表示每天北京时间 01:00。它不影响 GitHub Actions。直接运行 `python main.py` 只运行一轮，不会自动驻留定时执行。

## 6. 本地运行

完成第 2 节扫码后：

```bash
# 仅检查并保存登录续期，不阅读、不推送
python main.py --check-auth

# 运行原脚本一轮
python main.py
```

PowerShell 指定本轮数量：

```powershell
$env:READ_NUM = "120"
python main.py
# 后续要恢复默认：Remove-Item Env:READ_NUM
```

Linux / macOS：

```bash
READ_NUM=120 python main.py
```

本地不会读取 GitHub 中的 Secrets/Variables。若本机存在不同账号的 `WXREAD_LOGIN_JSON` 或旧 `WXREAD_CURL_BASH` 环境变量，它们会优先于本地扫码状态。

## 7. Docker Compose 部署

本仓库 Compose 默认从当前源码构建镜像，启动 `scheduler.py`，挂载：

```text
宿主机 ./state  →  容器 /app/.state
```

注意宿主机 `state` 与默认本地 `.state` 是两个不同目录。推荐直接把首次扫码写入将要挂载的目录，避免遗漏密钥或丢失更新后的 Cookie。

### 7.1 在宿主机初始化登录

先按第 1 节安装 Python 依赖，再执行：

Windows PowerShell：

```powershell
$env:WXREAD_LOGIN_FILE = ".\state\login.enc"
$env:WXREAD_COOKIE_FILE = ".\state\cookies.enc"
python qr_login.py
python main.py --check-auth
```

Linux / macOS：

```bash
WXREAD_LOGIN_FILE=./state/login.enc python qr_login.py
WXREAD_LOGIN_FILE=./state/login.enc WXREAD_COOKIE_FILE=./state/cookies.enc python main.py --check-auth
```

确认 `state/login.enc` 与 `state/login.key` 均存在。上面的检查若生成了 `state/cookies.enc`，也应保留。

### 7.2 填写 Compose 配置

可复制 `.env.example` 为与 `docker-compose.yml` 同目录的 `.env`，然后编辑需要的值。使用挂载的本地扫码状态时，`WXREAD_LOGIN_JSON` 和 `WXREAD_CURL_BASH` 保持为空，以免覆盖文件登录。

最简配置：

```dotenv
READ_NUM=40
TZ=Asia/Shanghai
CRON_SCHEDULE=0 1 * * *
PUSH_METHOD=
```

### 7.3 构建并启动

```bash
docker compose up -d --build
docker compose logs -f
```

容器启动后等待下一个调度时刻，**不会立即自动执行一轮**。需要手动检查容器登录时：

```bash
docker compose exec wxread python main.py --check-auth
```

不要在定时任务正在执行时另开同账号检查，避免同时轮换 Cookie。查看日志可确认是否已在运行。

### 7.4 修改配置、更新与停止

```bash
# 更新 .env 后，让 Compose 按新环境配置重建容器
docker compose up -d

# 修改源码或拉取新代码后，重新构建
docker compose up -d --build

# 停止并移除容器，宿主机 state 目录仍保留
docker compose down
```

[仅执行 `docker compose restart` 不会应用环境变量变更](https://docs.docker.com/reference/cli/docker/compose/restart/)。本项目需要持久保留 `state`；删除它或只复制 `login.enc` 而漏掉 `login.key`，会导致无法解密登录。

## 8. Cookie 保存与续期

登录输入优先级：

```text
WXREAD_LOGIN_JSON → WXREAD_CURL_BASH → 本地扫码登录文件
```

显式配置的来源不会因为失效而自动回退到下一项；无效 JSON 应先修复或移除，而不是保留后期待程序忽略。

维护过程：

1. 以初始登录凭据建立会话，优先恢复同一登录来源对应的加密 Cookie 缓存。
2. 复用同一个 HTTP Session，请求返回的新 Cookie 会合并并保存。
3. 续期明确成功才提交新状态；失败或异常时尽量保留原状态。
4. Actions 在开始时恢复 `.state/cookies.enc`，结束时尝试保存更新，即使后续脚本失败也会尝试保存已确认的 Cookie 轮换。
5. 更换初始登录凭据会隔离旧缓存；服务端撤销授权、缓存被清理或登录长期失效时，重新扫码并同步。

**Actions 不会每次自动修改 `WXREAD_LOGIN_JSON` Secret**，日常轮换主要保存在加密缓存中。不要把 Secret 看成每轮都会自动更新的最新 Cookie 导出。

不要同时在多个进程/机器上使用同一份状态做续期；现有 Actions 工作流已串行执行，但本机与云端之间没有跨平台互斥。长期有效性仍需实际观察，不能仅凭一次成功证明可用数周。

## 9. 故障排查

| 现象 | 优先检查与处理 |
| --- | --- |
| `python` 不存在或打开应用商店 | 检查 Python 安装及 PATH；Windows 可尝试 `py -3`，或使用虚拟环境解释器完整路径 |
| 找不到模块 / `ModuleNotFoundError` | 使用运行脚本的同一个 Python 执行 `python -m pip install -r requirements.txt` |
| 扫码图片没打开 | 使用 `--no-open`，手动打开输出的图片路径；无桌面系统不会自动显示图片 |
| 二维码 / 验证码过期 | 重新运行扫码命令，不要继续使用旧二维码 |
| 提示没有 GitHub 凭据或 HTTP 403 | 检查终端 Token、Git HTTPS 凭据、目标仓库/环境 Secret 的写入权限；处理后 `--sync-only` 重试 |
| Actions 读不到登录 | 确认 Secret 名为 `WXREAD_LOGIN_JSON`，环境名为 `AutoRead`，且环境规则允许当前分支运行 |
| 仓库 Secret 改了但仍是旧账号 | 检查环境内是否存在同名 Secret；环境配置优先 |
| 日志提示缺少登录 Cookie | 本机先扫码；Actions 先同步登录 Secret；Compose 检查挂载目录及成对的登录/密钥文件 |
| 续期失败 | 先分辨网络/服务端故障与授权失效；确实失效时重新扫码并同步，勿反复删除状态碰运气 |
| `READ_NUM` 没生效 | Actions 应设置 Variables 而不是 Secrets；检查是否勾选了仅验证登录；本地需设置环境变量，Python 不自动读取 `.env` |
| Docker 配置改了但没变化 | 执行 `docker compose up -d` 更新容器环境；源码变更还需 `--build` |
| 容器启动后没有马上运行 | 默认启动的是定时调度器，查看日志中的下次运行时间 |
| 没有收到通知 | 检查 `PUSH_METHOD` 与该渠道 Token 是否配套；仅验证登录模式不发送通知 |
| 运行时间比换算表长 | 表格仅计算成功后等待；依赖安装、续期、网络、重试和通知均可能增加用时 |
| Actions 显示排队或环境待审批 | 排队不等于执行，等待 Runner 或完成环境审批；检查仓库 Actions 是否被禁用 |
| 脚本成功但阅读记录未按预期增加 | 当前逻辑与服务端认可记录不是同一指标；不能把成功次数或推送中的分钟视为效果证明 |

排查时不要公开 Cookie、登录 JSON、Token、密钥文件或登录二维码。只分享去除敏感内容后的错误信息和步骤名称。

## 开发与测试

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

测试覆盖扫码确认、账号校验、凭据加密、GitHub Secret 加密上传、Cookie 轮换与回滚，以及删除功能后的导入和 Docker 打包引用检查。测试使用模拟响应，不代表真实阅读时长或长期登录效果。

主要文件：

| 文件 | 用途 |
| --- | --- |
| `qr_login.py` | 扫码、保存登录与可选 GitHub 同步入口 |
| `login_store.py` | 本地加密登录状态，兼容既有 Secret 格式 |
| `github_login_secret.py` | 加密上传 GitHub Secret |
| `cookie_store.py` / `weread_session.py` | Cookie 加密缓存与会话续期 |
| `main.py` / `config.py` | 原脚本运行逻辑与环境配置 |
| `scheduler.py` | 本地 / 容器定时入口 |
| `push.py` | 可选结果通知 |
| `.github/workflows/deploy.yml` | Actions 运行与仅续期检查 |
| `docker-compose.yml` / `Dockerfile` | 容器构建、持久状态与调度 |

扫码与会话维护的参考项目：[finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)（第三方 KOReader 插件，非腾讯官方插件）。认证边界与现有逻辑问题见 [docs/session-research.md](docs/session-research.md)。

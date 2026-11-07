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

Repository Variables：

| 名称 | 默认值 | 说明 |
| --- | --- | --- |
| `READ_NUM` | `40` | 原脚本请求次数，正整数；每次成功后的等待约 30 秒 |

Repository / `AutoRead` Environment Secrets：

| 名称 | 说明 |
| --- | --- |
| `WXREAD_LOGIN_JSON` | 推荐，由扫码工具生成并同步 |
| `WXREAD_CURL_BASH` | 兼容旧版配置；已使用扫码 Secret 时无需再抓包 |
| `PUSH_METHOD` | 留空不推送，或填写 `pushplus`、`wxpusher`、`telegram`、`serverchan` |
| `PUSHPLUS_TOKEN` | PushPlus 使用 |
| `WXPUSHER_SPT` | WxPusher 使用 |
| `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID` | Telegram 使用 |
| `SERVERCHAN_SPT` | ServerChan 使用 |

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

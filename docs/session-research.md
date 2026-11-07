# 微信读书认证与书籍选择核查

核查日期：2026-11-08。本文记录公开文档和源码能够支持的结论，不代表已经用真实账号验证了长期有效性或计时结果。

## 官方 Skill 与网页 Cookie 是两套认证

微信读书的[官方入口](https://weread.qq.com/r/weread-skills)指向 [Tencent/WeChatReading](https://github.com/Tencent/WeChatReading)。当前读取到的 `skills/SKILL.md` 版本为 `1.0.5`，调用通过用户自己的 `WEREAD_API_KEY` 认证；不要沿用第三方文档中的旧版本号。

官方公开能力包括搜书、书籍信息、目录、个人进度、书架、笔记和阅读统计。本次查阅的官方文档没有提供上传阅读时长的能力，也没有给出 API Key 永久有效的承诺。因此官方 API Key 可以用于减少查询功能对网页 Cookie 的依赖，不能据此认定它能替代网页版登录，或能使脚本运行时间自动计入阅读记录。

相关官方文档：

- [Skill 调用与版本规范](https://github.com/Tencent/WeChatReading/blob/main/skills/SKILL.md)
- [书籍信息、章节目录与个人阅读进度](https://github.com/Tencent/WeChatReading/blob/main/skills/book.md)
- [阅读统计及字段单位](https://github.com/Tencent/WeChatReading/blob/main/skills/readdata.md)

## 参考项目如何维护登录

[finlater/weread.koplugin](https://github.com/finlater/weread.koplugin) 是第三方 KOReader 插件，使用官方 Skill 不等于整个插件属于官方产品。源码将 API Key 查询和需要 Cookie 的网页功能分开。

其登录过程使用用户确认的扫码授权，取得 Cookie 和账号已开通的官方 API Key；Cookie 更新合并后写入本地设置。续期仍依赖服务端接受现有会话，不存在从源码可以证明的永久 Cookie。

它还为安装实例保存独立设备标识，并在登录时携带。项目文档记录了隔离设备标识后两份会话不再互相替换的一次对照测试，同时明确说明这不证明所有设备上的长期有效性。不能靠给已经签发的 Cookie 随意附加一个新标识来替代重新授权。

值得采用的错误处理是：只有续期响应明确成功，才提交新 Cookie。失败响应可能带有删除或替换 Cookie 的指令，直接写入会破坏原先仍可用的状态。本项目已补充内存回滚与不覆盖加密状态文件的保护。

参考源码与说明：

- [登录与设备标识说明](https://github.com/finlater/weread.koplugin/blob/main/docs/weread-api-reference.md)
- [扫码登录](https://github.com/finlater/weread.koplugin/blob/main/weread/lib/qr_login.lua)
- [续期校验与官方网关分离](https://github.com/finlater/weread.koplugin/blob/main/weread/lib/client.lua)

## 当前项目为何不能准确指定书籍

用户提供的[阅读器页面](https://weread.qq.com/web/reader/8b9329607186dc198b9bdab)对应《被讨厌的勇气：“自我启发之父”阿德勒的哲学课》，作者为岸见一郎、古贺史健。

现有代码存在独立于 Cookie 的问题：每次请求重新随机选择书籍和章节，而其他阅读位置字段仍来自固定模板，修复逻辑也引用固定书籍。书籍、章节和阅读位置没有构成同一份真实阅读上下文。因而修改模板或提供一个阅读器链接，并不会让当前循环准确对应指定书籍。

官方接入可通过搜索返回的 `bookId` 查询正确版本的详情、目录和个人进度，不应把阅读器 URL 中的哈希直接当作官方 `bookId`。本次未改造或执行虚构阅读时长的提交逻辑。

## 如何核查真实阅读是否同步

根据官方字段说明，个人书籍进度返回累计时长；阅读统计返回指定周期的时长。对真实阅读做前后查询可以确认服务端记录是否增加。各字段必须按各自文档解释单位和统计口径，接口请求成功或脚本等待了多久都不能替代服务端统计。

项目现已加入扫码登录和独立官方查询命令，真实扫码、登录续期与个人书籍进度查询已验证。扫码结果加密保存在本机，可在用户授权后同步为 GitHub Environment Secret；未测试排行榜、挑战赛计时或数周后的会话有效性。API Key 应存入受保护的环境变量或加密状态，不要提交到源码或聊天中。使用步骤见 [README](../README.md)。

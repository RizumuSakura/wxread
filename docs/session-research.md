# 协议与验证记录

核查日期：2026-11-08；2026-11-14 更新（书架循环与进度保护机制已从运行时移除）。

> 历史说明：第 1 节里关于官方 Skill 书架接口的条目保留了协议事实，但**书架循环已不再执行**——当前只读一本书。相关模块（`weread_shelf.py`、`WEREAD_API_KEY`、书架游标）已删除。

## 认证与书架（历史）

- 云端扫码取得网页 Cookie，用于网页阅读与续期；官方 Skill API Key 曾只用于读取个人书架。
- 官方文档：[SKILL.md](https://github.com/Tencent/WeChatReading/blob/main/skills/SKILL.md)、[shelf.md](https://github.com/Tencent/WeChatReading/blob/main/skills/shelf.md)。接入版本为 1.0.5，调用 `/shelf/sync`。
- 接口通过 Bearer Key 自动识别账号，不传 vid；请求参数和 skill_version 平铺在 body 顶层。遇到 upgrade_info 立即停止。
- books 是电子书条目，albums 是听书专辑，mp 是文章收藏入口。
- 四位验证码与跨运行续接已移除；二维码附件只包含加密查看网页，待登录会话只留在本次 Runner。

## 阅读器上下文

解析页面的 bookInfo、progress、currentChapter、psvts 和 token，并核对该书章节目录。书籍、章节和位置必须一致。

官方网页脚本：[9.625560b7.js](https://cdn.weread.qq.com/web/wrwebnjlogic/js/9.625560b7.js)。

pclts 在 SSR 默认是 0，由浏览器挂载时把当前 Unix 秒数编码后初始化；它不是必须由服务端返回的认证字段。程序按此初始化客户端时间，仍要求真实服务端 psvts 和 token。

进入书籍的请求不预支计时。每段用单调时钟实际等待约 30 秒后提交，失败重建会话重新计时，不回填失败时间或伪造翻页。

提交位置恒为开始时的快照（chapterUid / chapterOffset / 小数百分比），脚本从不推进自己的进度；早期版本的 `pending_progress` 恢复记录与云端检查点已被移除，见 `docs/adr/0001-single-book-without-progress-guard.md`。

## 已完成的验证

| 项目 | 结果 |
| --- | --- |
| [云端扫码](https://github.com/RizumuSakura/wxread/actions/runs/42433489629) | 账号校验与加密缓存保存成功 |
| [跨任务恢复与续期](https://github.com/RizumuSakura/wxread/actions/runs/42433975502) | 恢复扫码运行的缓存并成功续期 |
| [指定书籍 30 秒](https://github.com/RizumuSakura/wxread/actions/runs/42434410299) | bookId=25615385，实际等待一段，返回成功与 synckey |
| [240 段实测](https://github.com/RizumuSakura/wxread/actions/runs/43519775809) | 96 段 / 3497 秒，折合 36.4 秒每段 |

以上接口确认不代表排行榜或客户端一定认可相同秒数；数周有效性与服务端累计口径未验证。

会话维护参考：[finlater/weread.koplugin](https://github.com/finlater/weread.koplugin)。

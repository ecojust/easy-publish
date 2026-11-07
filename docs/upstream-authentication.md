# social-auto-upload 的登录方式

核对版本：[主线提交 `3847fff01f5434a8a32829833163cd6723cabc59`](https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59)。以下区分首次认证、会话复用和投稿方式。

该版本的 `sau` CLI 没有直接传入平台用户名、密码并完成自动登录的接口。`--account creator` 中的 `creator` 是**本地会话别名**，用于定位 `cookies/<平台>_<别名>.json`；网页支持密码登录，不代表 CLI 提供了密码登录参数。参见[平台注册入口][cli]、[账号文件解析][arguments]。

| 平台 | 首次认证或会话失效后 | 已认证后的投稿方式 | 固定源码依据 |
| --- | --- | --- | --- |
| 哔哩哔哩 | 调用 `biliup login` 交互登录；上游文档指引在本地终端或 `qrcode.png` 扫码。SAU 没有封装用户名／密码参数。 | 复用账号文件，调用 `biliup renew`／`upload`；此上传路径不使用浏览器。 | [命令实现][bilibili]、[使用说明][bilibili-doc] |
| 抖音、快手、小红书、视频号 | 浏览器扫码流程；无头运行时可提取二维码供用户扫描，必要验证仍由用户完成。 | 恢复本地会话，通过浏览器操作创作者后台。 | [抖音][douyin]、[快手][kuaishou]、[小红书][xiaohongshu]、[视频号][channels] |
| 百家号、支付宝生活号、微博 | 提供浏览器二维码登录并保存会话。 | 复用会话进行网页投稿。 | [百家号][baijiahao]、[支付宝][alipay]、[微博][weibo] |
| 虎扑 | 在浏览器人工完成 QQ／手机号等登录；QQ 流程可提取二维码。 | 复用会话进行网页投稿。 | [登录实现][hupu] |
| 大鱼号、雪球、知乎 | 打开可见 Chrome，用户在网页输入账号密码、验证码或采用页面提供的登录方式，核对身份后保存会话。 | 复用会话进行网页投稿。 | [大鱼号][dayu]、[雪球][xueqiu]、[知乎][zhihu] |
| YouTube | 可见 Chrome 中完成 Google 登录及必要的二步验证。 | 复用会话操作 YouTube Studio。 | [登录与会话检查][youtube] |
| Facebook／Facebook Page、Instagram | 浏览器中人工登录并完成平台验证，保存并校验会话。 | 复用会话操作网页／Business Suite。 | [Facebook][facebook]、[Page][facebook-page]、[Instagram][instagram] |
| TikTok | 现有 Chrome uploader 打开登录页，通过 `page.pause()` 等待用户登录后保存会话。 | Chrome uploader 复用会话上传；该提交没有 `sau tiktok` 子命令。 | [登录与上传器][tiktok]、[CLI 说明][tiktok-doc] |

`--headless` 只是隐藏浏览器窗口，网页仍由浏览器运行；具体平台可能要求可见窗口。完成一次认证后，正常投稿会复用已保存的会话。需要重新认证的情况包括会话缺失、过期或平台要求额外验证。以[抖音上传入口][douyin-command]为例，它先检查会话，失效时提示执行 `login`，有效时直接进入投稿；运行参数见[无头／有头选项][runtime-flags]。

Easy Publish 的微信公众号接入另用官方 API，通过 AppID／AppSecret 获取访问令牌并调用素材、草稿和发布接口；这些是应用凭据，不是微信账号的用户名／密码。公众号不在上述上游 CLI 平台注册表中，参见[本项目微信适配器](../engine/easy_publish/providers/wechat.py)。Easy Publish 的 B 站适配器也独立实现了网页登录与 HTTP 上传，未直接调用上游 `biliup`；参见[本项目 B 站适配器](../engine/easy_publish/providers/bilibili.py)。表中的上游流程不能直接当作本项目已实现能力清单。

[cli]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/sau_cli.py#L16
[arguments]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/sau_commands/arguments.py#L16
[bilibili]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/sau_commands/bilibili.py#L50
[bilibili-doc]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/docs/CLI.md#L149
[douyin]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/douyin_uploader/main.py#L265
[kuaishou]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/ks_uploader/main.py
[xiaohongshu]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/xiaohongshu_uploader/main.py#L219
[channels]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/tencent_uploader/main.py#L160
[baijiahao]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/baijiahao_uploader/main.py#L120
[alipay]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/alipay_uploader/main.py#L136
[weibo]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/weibo_uploader/main.py#L155
[hupu]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/hupu_uploader/main.py#L2
[dayu]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/dayu_uploader/session.py#L41
[xueqiu]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/xueqiu_uploader/session.py#L38
[zhihu]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/zhihu_uploader/session.py#L44
[youtube]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/youtube_uploader/main.py#L70
[facebook]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/meta_uploader/facebook_uploader/main.py#L95
[facebook-page]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/meta_uploader/facebook_page_uploader/main.py#L105
[instagram]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/meta_uploader/insgraph_uploader/main.py#L101
[tiktok]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/tk_uploader/main_chrome.py#L42
[tiktok-doc]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/docs/CLI.md#L23
[douyin-command]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/sau_commands/douyin.py#L115
[runtime-flags]: https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/sau_commands/arguments.py#L60

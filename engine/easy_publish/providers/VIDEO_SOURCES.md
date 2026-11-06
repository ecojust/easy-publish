# 视频适配器来源与验证边界

`social_video.py` 的平台入口、登录标志、视频／封面选择器来自
[dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59)，
固定提交 `3847fff01f5434a8a32829833163cd6723cabc59`：

- `uploader/douyin_uploader/main.py`：视频输入、标题、描述、上传完成标志及作品管理路径。
- `uploader/xiaohongshu_uploader/main.py`：视频、话题、封面和成功页。
- `uploader/tencent_uploader/main.py`：视频号主页入口、iframe、编辑器、短标题、封面、实名验证。

仅参考上述源文件；没有复制账号缓存、配置、凭据、上游技能或完整下载器。
本实现自行管理浏览器路径、账号会话、提交前状态、总超时和单次提交。
不沿用反复点击发布、强制删除验证弹窗、失败时重传或从 latest 下载可执行文件的行为。

## 当前能力

- 抖音、小红书、视频号：账号浏览器登录／检查、视频上传、填写、单次提交。
- 小红书及视频号：自定义封面；失败时停止，不能默默替换请求的封面。
- 视频号：明确的保存草稿按钮与草稿列表确认。
- 抖音自定义封面未启用，提交前明确返回需要处理；不忽略用户提供的封面。
- 哔哩哔哩由独立的 `bilibili.py` 提供账号连接、动态分区、分片上传和投稿回执，来源与范围见 [BILIBILI_SOURCES.md](BILIBILI_SOURCES.md)。
- 快手由独立的 `kuaishou.py` 提供视频／封面与话题、账号确认及单次投稿回执，见 [KUAISHOU_SOURCES.md](KUAISHOU_SOURCES.md)。
- 文章、图片笔记不在此模块范围。

小红书独立成功页确认只记“已提交”，不声称审核通过或已公开。
抖音／视频号仅列表跳转、同名旧作品或按钮消失不作为本次回执；必须同时观察到提交前不存在的明确成功通知。
平台没有可识别的成功通知时保守记录 unknown，即使本次作品实际上已成功提交，也需要用户到平台核对。
存在上传或编辑行为后，不明确的结果记 unknown，禁止自动重试。
浏览器验证、短信和扫码由使用者在平台浏览器中完成。
本轮测试使用隔离的页面／接口替身；没有真实账号发布验收，网站改版可能需要更新选择器。

## 抖音登录修复依据

上游 `uploader/douyin_uploader/main.py` 说明扫码后可能进入创作者首页；上传输入框不是通用的登录成功标志。本实现将账号确认与上传入口准备分开，并检查同一浏览器会话中的新标签页。

2026-11-06 只读检查抖音公开创作者页面及其 `main.b818de5a.js`，确认页面使用 `/aweme/v1/creator/pc/user/info/`，以 `status_code=0` 且有有效 `uid` 确认账号。实现通过当前同源浏览器页面发起只读查询，拒绝跳转、过期导航与未完成验证，只有布尔确认结果进入 Python；不复制公开页面业务代码或记录账号信息。实际平台登录仍待用户验证。

## 小红书上传等待修复依据

2026-11-06 只读检查[小红书视频发布入口](https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video)引用的公开资源
[`project-publish-vue.d52b51f1.js`](https://fe-static.xhscdn.com/formula-static/ugc/public/resource/js/project-publish-vue.d52b51f1.js)（SHA-256：`1c84bc689fab7cad7c51866a2125e3db9d1d8e00cd4b42c99cf96d783f77c747`）。当前视频文件控件为 `.cover-container > input.upload-input`，接受 `.mp4` 等扩展名；其同级元素 `.uploading`、`.transcoding`、`.preview-new`、`.fail`、`.canceled` 分别由上传／分析、转码、预览、失败、取消／未上传状态渲染。预览内 `.name` 显示当前文件名。100% 仍可处于上传状态；转码提示虽写着“不影响发布”，本实现等待可确认的预览后再填写信息。

原实现仅等标题框出现便填写正文、话题和封面；编辑器尚未就绪时的短超时会结束流程并关闭仍在上传的浏览器。修复保留旧控件选择器并补充当前控件，在首次选择文件前拒绝已有视频／上传任务，选择一次文件后等待与本次文件名匹配的预览，最长 20 分钟。标题框、已启用的发布按钮或 100% 不作为上传完成证明。失败、取消、登录变化、关闭窗口、处理超时和元数据填写失败各有固定阶段提示；上传后仍记录 unknown，不能自动重传或重复点击发布。

仅据公开 DOM 状态实现等待逻辑，未复制页面业务代码。回归包含隔离状态测试和可选 Chromium 全量拦截路由测试：慢上传时标题／发布按钮已经显示，上传 100% 和转码阶段仍保持窗口及等待，预览就绪后才填写并提交一次。未使用真实账号上传或发布；用户设备上的具体失败堆栈尚未取得，实际平台效果仍需新版验证。

## 视频号上传与编辑器刷新修复依据

固定 SAU 提交 `3847fff01f5434a8a32829833163cd6723cabc59` 的 `uploader/tencent_uploader/main.py`：581–622 行说明须从首页“发表视频”进入，实际编辑器在 `/micro/content/post/create` iframe；810–879 行在基本描述填写后，以 iframe 内可见且未 disabled、无 `weui-desktop-btn_disabled` 类的 `div.form-btns`“发表”按钮作为就绪信号；`div.status-msg.error` 用于上传失败识别。1098–1132 行注明上传完成后表单会刷新，短标题、封面及合集在上传等待后设置。没有证据证明 `.media-status-content` 在成功态仍存在，因此它只用于读取可见进度／错误相关文本，不能成为成功的硬性条件。

独立第三方 [Easel 固定源码](https://github.com/ZJU-REAL/Easel/blob/8e6722f25a8b8f14049e51b9132e6d77803e4596/skills/shared/scripts/web_publisher.py#L701) 的 701–711 行记录转码完成时 iframe 可能重载、须重新从 `page.frames` 查找（该文件 SHA-256：`ea0e145949efdddb84d30937a58495a0e6ac86b133aa952932c86d106de886ea`）。[SAU issue #262](https://github.com/dreammis/social-auto-upload/issues/262) 另有多个隐藏旧封面弹窗导致 `.first` 命中错误对象的报告。这些是回归场景的来源线索，并非对用户设备故障的实机确认；未复制第三方提交重试或列表 URL 即成功的逻辑。

修复只从正确同源及路径取得当前 iframe，重挂载空隙继续等待，不退回顶层壳页面中的无关文件控件／错误提示。一次选择视频后在 20 分钟总等待内处理上传、表单初始化和编辑器重载；基本文字仅在可编辑时填写，重载后核对并恢复，避免“先等按钮、后填必填字段”的循环等待。98% 和可见进行态继续等待，隐藏旧进度及“转码完成”不视为进行态。封面延后至上传就绪，定位当前可见弹窗；提交前再次定位编辑器和按钮，核对文字和已设置封面的文档是否仍有效，再点击一次。任何不明结果仍保留 unknown 和原 fence，不重选视频或自动重发。

2026-11-06 当前环境对视频号页面、iframe 和静态资源的只读访问均被网络代理以 HTTP 403 拒绝，因此没有取得当前第一方 DOM／JS 或新的上传完成 API。完成判断仍基于上述固定参考的按钮语义，并辅以可见进度、错误、验证和文字检查；不将文件名、本地预览或 100% 单独作为完成证明。实际 Chromium 隔离路由回归模拟 98% → iframe 拆除／重新挂载 → 处理完成、恢复文字、隐藏旧封面弹窗、正式发表／保存草稿各一次；未进行真实账号上传或发布。

## 上游许可证

MIT License

Copyright (c) 2023 dreammis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

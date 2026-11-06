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
- Bilibili 驱动未启用：上游依赖交互式 biliup，尚无固定且经过校验的二进制契约。
- 文章、图片笔记不在此模块范围。

小红书独立成功页确认只记“已提交”，不声称审核通过或已公开。
抖音／视频号仅列表跳转、同名旧作品或按钮消失不作为本次回执；必须同时观察到提交前不存在的明确成功通知。
平台没有可识别的成功通知时保守记录 unknown，即使本次作品实际上已成功提交，也需要用户到平台核对。
存在上传或编辑行为后，不明确的结果记 unknown，禁止自动重试。
浏览器验证、短信和扫码由使用者在平台浏览器中完成。
本轮测试使用隔离的页面／接口替身；没有真实账号发布验收，网站改版可能需要更新选择器。

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

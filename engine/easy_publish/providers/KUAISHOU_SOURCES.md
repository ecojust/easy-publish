# 快手视频适配来源与验证边界

`kuaishou.py` 使用应用已有的 Patchright 浏览器登录、上传视频和封面，再观察同一浏览器的真实投稿回执。没有额外命令行工具、账号密码截取、签名算法实现或原生运行时依赖。

## 固定参考与公开协议观察

- [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59)，固定提交 `3847fff01f5434a8a32829833163cd6723cabc59`，MIT，Copyright (c) 2023 dreammis。
- 具体参考 [`uploader/ks_uploader/main.py`](https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/ks_uploader/main.py)：passport 登录回调、创作中心入口、文件选择按钮、描述编辑器和封面弹窗。没有复制账号数据、二维码缓存、debug HTML、DOM 强制移除、重新上传或重复点击投稿的逻辑。
- 2026-11-06 只读检查快手公开的[投稿页面](https://cp.kuaishou.com/article/publish/video)和[发布器入口](https://cp.kuaishou.com/onvideo-application/index.html)，确认当前客户端协议与 DOM。公开 JavaScript 仅作为协议事实依据，没有复制其实现源码。
- 当时的公开资源：外壳 [7565.ccc7dd62.js](https://p66-plat.wsbkwai.com/kos/nlav11104/static/ks-cp/js/7565.ccc7dd62.js)、发布器公共入口 [index-D67MYQqZ.js](https://p66-plat.wsbkwai.com/kos/nlav11104/onvideo-cp/static/js/index-D67MYQqZ.js)、视频编辑器 [index-uRwi6PD7.js](https://p66-plat.wsbkwai.com/kos/nlav11104/onvideo-cp/static/js/index-uRwi6PD7.js)、话题／封面组件 [index-b4RDh7ID.js](https://p66-plat.wsbkwai.com/kos/nlav11104/onvideo-cp/static/js/index-b4RDh7ID.js)。资源 hash 记录观察版本，不代表平台接口永久稳定。

## 登录与会话

- 账号连接通过浏览器自身发出的 `/rest/v2/creator/pc/authority/account/current` 响应确认：整数 `result=1`、有效 `data.userId`，且不能有明确的 `logined=false`。v3 的合并账号接口只有同样有效的身份字段才被接受。
- 登录可以落在创作中心首页；连接成功不要求页面已经出现上传控件。公开介绍页、URL、cookie 存在、隐藏模板或名字文本均不能单独作为登录成功证明。
- 发布时另行打开真正的上传入口，验证具体上传按钮。关闭登录窗口会尽快结束等待；扫码和平台验证由使用者正常完成。
- 本地账号 ID 与服务端身份绑定。会话使用应用数据目录中的本地密钥加密存储；重新登录不允许静默替换为其他用户。原始账号响应、cookie 和请求正文不进入日志、前端或公开元数据。

## 内容与上传

- 当前支持普通视频投稿和可选 JPEG／PNG 封面，不支持快手平台草稿、定时、合集或图文笔记。标题、描述和全部话题组成同一作品描述，不以描述替换标题，也不截断输入。
- 当前发布器描述上限为 **500 个 UTF-16 单元**；本实现统计标题、描述、换行、话题标记及编辑器末尾话题分隔符，表情可能占两个单元。提交前再次检查浏览器实际完整文本。
- 当前公开客户端默认 `maxTagCount=4`，账号配置可以覆盖。本适配最多接受 4 个话题；输入真实的 `#` 按键后添加文字并以空格确认，再核对 `span.at-tag-item[data-tag-name]` 及完整描述。账号限制使话题丢失时停止，不把普通文本冒充已添加的话题。
- 部分账号需要动态“作者声明”。没有依据替用户选取声明；在浏览器中明确提示并等待使用者选择符合内容的选项，超时后保留不确定状态及具体提示。不会绕过校验或默选第一个选项。
- 若发布页面已有非空平台草稿，上传前停止，避免覆盖其他未完成内容。
- 上传文件之前等待宿主提交持久状态并回传 ACK。通过本次页面发起的 `/rest/cp/works/v2/video/pc/upload/finish`、v3 对应路径或 v2 `/upload/isolation/finish` 的 `result=1` 和 `data.fileId` 确认媒体完成；上传完成本身不表示投稿成功。
- 当前公开封面流程使用“封面设置 → 上传封面 → 确认”；账号配置或页面变化导致找不到控件时停止，不能默默丢弃用户封面。

## 投稿结果

- 普通最终接口为 `/rest/cp/works/v2/video/pc/submit` 或 v3 对应路径，隔离配置使用 v2 `/isolation/submit`。浏览器负责平台原生请求和签名，本实现只通过正常 UI 操作并观察结果。
- 本次最终请求必须在应用点击发布后发起，来自当前任务页面，且 `fileId` 对应本次上传、描述对应本次完整内容。迟到的旧响应、其他页面、其他作品和仅跳转管理列表都不是回执。
- 已匹配请求的整数 `result=1` 记为 **submitted（已提交）**。当前客户端自身也是据此显示“内容发布成功”，并不要求响应包含作品 ID；本应用仍不将其称为审核通过或已公开。
- 发布按钮只点击一次，明确的正常发布确认最多点击一次。上传或提交后的中断、缺失／失败回执、多次请求都保守记录 unknown，不自动重复上传或投稿。验证码、声明或错误提示不会被强制移除。

## 验证

`engine/tests/test_kuaishou.py` 的隔离测试覆盖认证首页连接、账号绑定、文本与话题边界、上传文件绑定、旧响应及跨页面隔离、持久提交边界、封面输入、作者声明等待和单次投稿回执。

该文件还包含显式启用的真实浏览器 fixture：

```sh
EASY_PUBLISH_CHROMIUM=/usr/bin/chromium PYTHONPATH=engine .venv/bin/python -m pytest engine/tests/test_kuaishou.py -q
```

该场景的全部网络请求由本地 HTML／JSON 路由替身处理，未知请求立即中止；检查实际文件选择、键盘话题事件、封面弹窗、上传完成和最终单次回执。未设置浏览器路径时仅跳过该浏览器场景，其余协议测试正常执行。

没有登录真实快手账号、上传或投稿。公开资源读取、fixture 和安装包构建不代表真实账号／平台验收；账号权限、平台校验和页面改版仍需实际使用反馈。

## 参考项目许可证

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

# 第三方来源与许可

Easy Publish 使用 [MIT 许可证](LICENSE)。本文件记录直接参考或包含源码的项目；第三方软件仍适用各自的原始许可证。

## social-auto-upload

- 项目：[dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload)
- 固定提交：[`3847fff01f5434a8a32829833163cd6723cabc59`](https://github.com/dreammis/social-auto-upload/tree/3847fff01f5434a8a32829833163cd6723cabc59)
- 许可证：MIT
- 版权：Copyright (c) 2023 dreammis

文章适配保留了 18 份明确列出的上游源文件，并进行了命名空间、浏览器配置、任务状态与素材处理方面的本地调整。逐文件来源和原始 SHA-256 记录在 [SOURCE.json](engine/easy_publish/vendor/social_auto_upload/SOURCE.json)，原始许可证保留于 [vendor LICENSE](engine/easy_publish/vendor/social_auto_upload/LICENSE)，变更和验证边界见 [NOTICE.md](engine/easy_publish/vendor/social_auto_upload/NOTICE.md)。

抖音、小红书、视频号和快手适配参考该提交的创作中心入口、登录标志与编辑器选择器，由本项目实现会话隔离、任务监督、单次提交及结果判定。具体源文件与完整许可另保留在 [VIDEO_SOURCES.md](engine/easy_publish/providers/VIDEO_SOURCES.md) 和 [KUAISHOU_SOURCES.md](engine/easy_publish/providers/KUAISHOU_SOURCES.md)。

没有复制上游账号、cookie、token 缓存、私有配置或整个项目。上游源码中关于“实测”的描述属于上游观察，不表示 Easy Publish 已通过真实账号发布验收。

## oDesk

- 项目：[ecojust/oDesk](https://github.com/ecojust/oDesk)
- 固定提交：[`d062c05b3442f45205ff83cc28497d435fccce99`](https://github.com/ecojust/oDesk/tree/d062c05b3442f45205ff83cc28497d435fccce99)
- 许可证：MIT
- 版权：Copyright (c) 2025 桔子桑

参考 `src/views/skillapps/WechatPublisher.vue`、`WechatPublisherTemplate.js` 与 `src-tauri/addin/skills/wechat-publisher.zip` 中文章、正文图片、封面和公众号草稿的业务流程。该参考实现通过 `draft/add` 创建草稿；这不等于正式发布或向关注者群发。

Easy Publish 的 [微信公众号适配器](engine/easy_publish/providers/wechat.py) 是独立编写的确定性官方 API 实现。它使用 AppID / AppSecret、内存访问令牌、图片上传、草稿与 `freepublish` 发布查询，分别记录草稿、受理和最终结果。文章模板另参考上述 ZIP 中 editorial、paper、forest、artdeco 与 blueprint 的版式方向，由本项目独立生成受控的内联 HTML／CSS；来源和兼容取舍见 [文章模板说明](docs/article-templates.md)。原文整理流程参考 `WechatPublisher.vue` 与 `src/service/shell/opencode.ts` 的本地 OpenCode 调用方式；本项目独立实现受限整理任务、账号模板预览和版本检查。0.1.8 起内置 Wenyan CLI 使用的官方核心并调用其八款主题，未运行 oDesk 上游排版脚本，未复制 oDesk 的调度源码、技能包、脚本、配置或任何 token 缓存文件。

## OpenCode

- 项目：[anomalyco/opencode](https://github.com/anomalyco/opencode)
- 内置版本：[v1.19.5](https://github.com/anomalyco/opencode/releases/tag/v1.19.5)
- 固定提交：[`810c0eba1a7348036f8bdf61e6632d0c36c6876c`](https://github.com/anomalyco/opencode/tree/810c0eba1a7348036f8bdf61e6632d0c36c6876c)
- 各目标归档与摘要：[opencode-runtime.json](scripts/opencode-runtime.json)
- 许可证：MIT；Copyright (c) 2025 opencode
- 完整原始许可：[vendor/opencode/LICENSE](vendor/opencode/LICENSE)

安装包内置对应系统的官方 OpenCode 可执行文件，用于将纯文本整理为 Markdown。构建使用固定版本与 SHA-256 校验，不在用户电脑上下载并执行浮动最新版。原文整理通过本应用独立的工作目录、配置和受认证本地服务调用，不导入个人 OpenCode 账号或配置；发布仍由 Easy Publish 引擎执行。免费模型属于外部服务，其可用性和条款由服务提供方决定，MIT 许可不代表模型服务永久免费。

## biliup

- 项目：[biliup/biliup](https://github.com/biliup/biliup)
- 固定提交：[`f23b7614b250caacba8a1475412515264dc30558`](https://github.com/biliup/biliup/tree/f23b7614b250caacba8a1475412515264dc30558)
- 许可证：MIT
- 版权：Copyright (c) 2019 ForgQi

参考该提交中的账号验证、创作中心分类、UPOS 上传和 Web 投稿协议。Easy Publish 独立实现浏览器登录、加密会话绑定、动态分区、持久提交边界和回执处理；不下载或执行 biliup 二进制，不复制账号或配置。具体路径与验证范围见 [BILIBILI_SOURCES.md](engine/easy_publish/providers/BILIBILI_SOURCES.md)。

## MIT 许可文本

以下文本保留上述来源的许可条件；各源码目录中已有的版权与许可声明继续保留。

```text
MIT License

Copyright (c) 2023 dreammis
Copyright (c) 2025 桔子桑
Copyright (c) 2019 ForgQi

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
```

## 运行与构建依赖

前端、Rust 宿主和 Python 引擎还分别使用 [package-lock.json](package-lock.json)、[Cargo.lock](src-tauri/Cargo.lock) 与 [requirements.txt](engine/requirements.txt) 中列明的依赖，包括 Tauri、React、Patchright、HTTPX 和 cryptography 等。它们的许可证由对应发行包提供，本文件不替代这些依赖的版权与许可声明。浏览器由用户已有安装或 Patchright 官方安装流程提供，不包含在参考项目的源码副本中。

## Wenyan

- 官方核心：[@wenyan-md/core 3.0.12](https://github.com/caol64/wenyan-core)，Apache-2.0。
- 对照的 CLI：[@wenyan-md/cli 2.0.12](https://github.com/caol64/wenyan-cli)，本应用复用其核心，不调用 CLI 发布账号。
- 固定依赖与运行源码：[runtime/wenyan](runtime/wenyan)。原始许可随生成的运行包一同分发。

预览及微信发送使用官方主题渲染结果；仅原先六款兼容模板继续使用独立实现。Wenyan 排版不联网、不执行用户 HTML 或脚本，也不获取第三方账号凭据。

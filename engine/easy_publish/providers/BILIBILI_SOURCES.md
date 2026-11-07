# 哔哩哔哩适配器来源与验证边界

`bilibili.py` 使用现有 Patchright 打开可见的登录浏览器，再通过 HTTPX 调用
哔哩哔哩的 UPOS 上传及 Web 投稿接口。不下载或执行外部 biliup 二进制，
不要求命令行登录，不增加桌面包的原生运行时依赖。

## 固定参考

协议参考 [biliup/biliup](https://github.com/biliup/biliup/tree/f23b7614b250caacba8a1475412515264dc30558)，
固定提交 `f23b7614b250caacba8a1475412515264dc30558`，MIT，Copyright (c) 2019 ForgQi：

- `crates/biliup/src/uploader/line.rs`：`preupload` 参数与平台提供的上传线路。
- `crates/biliup/src/uploader/line/upos.rs`：UPOS 上传会话、分块范围、合并确认和对象文件名。
- `crates/biliup/src/uploader/bilibili.rs`：Studio 投稿字段、JPEG 封面接口、Web `/add/v3` 和回执。
- `crates/stream-gears/biliup/plugins/bili_webup.py`：导航登录检查、封面 CDN 返回 HTTP URL 的兼容方式。
- `app/lib/use-streamers.ts` 及前端 Bilibili 投稿表单：登录后 `archive/pre` 的 `typelist` 分区树；标题 80、简介 2000、标签 12 个／每个 20 字符、封面 5 MiB 的输入约束。

2026-11-06 的无账号、只读 `GET https://member.bilibili.com/preupload?r=probe`
返回 UPOS 线路及 `probe_version`、`upcdn`、`zone` 参数。实现每次向平台获取这些参数，
不会把当时的线路硬编码成永久配置，也不执行测速上传。此只读检查不是账号投稿验收。

## 本实现的边界

- 浏览器登录只保存 Bilibili 域内 cookie；本地账号 ID、服务端 `mid` 和 `DedeUserID` 相互绑定。
  导航接口同时确认 `code=0`、`isLogin=true` 和用户身份。重新登录不能替换原账号身份。
- 会话仅进入宿主管理的临时工作区，使用应用数据目录中的本地密钥加密存储，不进入前端或任务结果。
  登录／检查只返回经筛选的分区编号、名称，不转发原始平台响应。
- 分区来自当前账号的 `archive/pre`；投稿前重新检查所选叶子分区，不使用猜测的静态分区表。
  稿件类型明确选择自制或转载；转载必须提供 HTTP(S) 来源链接，自制目标忽略已填写的转载来源，原稿字段保留。
- 支持单个视频正式投稿；标题、简介、标签不自动截断。可选 JPEG／PNG 封面在上传前转换成 JPEG，
  保留方向，透明区域使用白底；转换后仍执行 5 MiB 限制。未设置封面时交由平台生成。
- 动态 UPOS 地址只允许 HTTPS Bilibili 视频 CDN，上传客户端不携带账号 cookie；
  所有请求禁止自动跳转。参考实现中的上传重试、代理配置、日志及凭据格式不被直接沿用。
- 首次分配上传会话之前等待宿主的持久化 `submitting` 确认。每个分块及最终投稿只请求一次。
  开始上传后发生超时、错误或缺失回执，一律记 `unknown`，不得自动重发。
- 只有 `code=0` 并取得有效 `bvid` 或正整数 `aid` 才记 `submitted`；该结果代表平台接收，
  不代表审核通过或确认公开发布。不会根据同名旧稿、页面跳转或上传完成宣称发布成功。

`engine/tests/test_bilibili.py` 使用 HTTPX MockTransport 和浏览器替身，覆盖请求顺序、
身份绑定、分区刷新、分块／合并、封面转换、回执以及不确定结果不重试。
没有调用真实账号登录或发布，真实平台权限、风控、审核和页面变动仍需实际使用验证。

## 参考项目许可证

MIT License

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

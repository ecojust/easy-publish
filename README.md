# Easy Publish

把一篇文章或一个视频发布到多个平台的本地桌面工作台。文章与视频分开管理，支持多个账号、内容预检、平台草稿和逐个目标的发布记录。

面向 **macOS 13.5+（Apple Silicon / Intel）和 Windows 10+ x64**。桌面使用 Tauri 2 和系统 WebView，发布引擎随安装包提供，普通用户无需安装 Python、Node.js 或开发工具。macOS 最低版本由内置 Patchright 的 Node 24 驱动要求决定，来源见[桌面说明](docs/desktop.md)。

当前是 `0.1.0` 开发版本。已实现的平台适配使用隔离的页面与 HTTP fixture 验证，**尚未完成真实账号登录及发布验收**；网站改版、账号权限和人工验证都可能影响实际操作。安装包工作流已配置，产物是否可下载以实际成功的 [Actions 运行](https://github.com/ecojust/easy-publish/actions/workflows/build.yml) 为准；本阶段尚未发布 Release。

## 平台能力

下表对应应用实际展示的平台目录。这里的“发布”表示执行平台提交流程；任务会进一步区分已受理、已确认发布和结果未知。

| 分类 | 平台 | 当前操作 | 主要要求 |
| --- | --- | --- | --- |
| 文章 | 微信公众号 | 平台草稿、发布 | AppID / AppSecret；封面；正文图片先导入本地素材；对应官方接口权限 |
| 文章 | 知乎 | 发布 | 在发布浏览器中登录 |
| 文章 | 大鱼号 | 发布 | 在发布浏览器中登录；选择信息来源，转载需原文链接 |
| 文章 | 雪球 | 平台草稿、发布 | 在发布浏览器中登录；选择创作声明，可设置分享范围 |
| 视频 | 抖音 | 发布 | 使用平台自动封面；暂不支持指定封面 |
| 视频 | 小红书 | 发布 | 可指定封面；标题最多 20 字、话题最多 10 个 |
| 视频 | 微信视频号 | 平台草稿、发布 | 微信扫码登录；短标题为 7–15 字；可指定封面 |

微信公众号与微信视频号使用不同的账号入口。Bilibili 尚未启用，也没有把图片笔记列为文章发布能力。平台原生的审核、短信、扫码、验证码及实名验证需要使用者在打开的浏览器中完成。

## 使用流程

1. 打开“设置”，查看发布浏览器和本机凭据库状态。应用会先查找已安装的 Chrome / Edge / Chromium；没有可用浏览器时，点击“下载并准备浏览器”。下载需要网络，公众号官方 API 操作本身不依赖浏览器。
2. 在“账号管理”添加账号并连接。网页平台会打开独立的发布浏览器窗口；公众号填写开发者凭据后检查连接。
3. 在“文章”中编写 Markdown，或导入 UTF-8 的 `.md`、`.markdown`、`.txt` 文件；使用图片按钮导入正文图片。也可以在“视频”中导入 MP4、MOV、M4V、WebM，填写标题、描述、话题及封面。最终格式、大小等要求仍由目标平台检查。
4. 选择目标账号和“发布内容”或“平台草稿”，填写平台要求的来源或创作声明，点击内容检查。检查通过后创建任务。
5. 在“发布任务”查看每个账号的进度与结果。任务保存创建时的内容快照，后续修改本地稿件不会改变已创建的任务。

编辑器的“保存”只保存本地稿件。“保存平台草稿”会向所选平台提交内容。

| 任务结果 | 含义 |
| --- | --- |
| 已保存草稿 | 平台确认草稿存在，尚未正式发布 |
| 已提交 | 平台已受理，最终公开状态仍需核对 |
| 已发布 | 适配器获得平台发布确认 |
| 需要处理 | 账号、权限、验证或内容要求需要处理 |
| 结果未知 | 操作可能已产生平台内容；请先前往平台核对，避免重复提交 |

取消任务不会撤回已经产生的平台内容。应用不会自动重试结果未知的任务；只有明确尚未提交的失败或取消任务才提供重试。

## 微信公众号配置

在微信公众平台取得该公众号的 AppID 和 AppSecret，并检查素材、草稿及发布接口权限。把运行本应用的网络出口公网 IP 加入公众号的 IP 白名单；更换网络后可能需要更新白名单。

“检查连接”验证凭据能否取得访问令牌，不代表所有发布接口均已授权。文章需要 JPEG / PNG 封面；正文图片也通过本地导入后上传。适配器按顺序上传素材、创建草稿，并在选择发布时调用微信发布接口查询结果。

这里的正式发布使用 `freepublish` 接口，**不包含向关注者群发消息**。权限不足会显示可处理的错误；已经创建的草稿或已受理请求会保留其回执，连接中断不会自动重发内容。

## 本地数据

稿件、素材、账号公开信息和任务记录保存在当前电脑的应用数据目录。AppSecret 和浏览器登录会话使用 AES-GCM 加密；主密钥保存在 macOS 钥匙串或 Windows 凭据管理器中，界面不回显 AppSecret。系统凭据服务不可用时需要先解锁或恢复该服务。

发布引擎仅监听本机随机端口，由桌面宿主持有临时访问凭据。发布时会把所选内容发送到相应平台。删除本地稿件或移除本地账号不会删除平台账号及已发布内容。

## 开发运行

准备 Node.js **22.12+**、Python **3.12**、Rust stable，以及 [Tauri 系统依赖](https://v2.tauri.app/start/prerequisites/)；macOS 需要 Xcode Command Line Tools，Windows 需要 MSVC 构建工具与 WebView2 Runtime。以下命令在仓库根目录执行。

macOS：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r engine/requirements-dev.txt
npm ci
npm run desktop:dev
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r engine/requirements-dev.txt
npm ci
npm run desktop:dev
```

也可使用已安装的 uv 创建和安装同一个 `.venv`：

```sh
uv venv --python 3.12 .venv
uv pip install -r engine/requirements-dev.txt
```

开发桌面从根目录 `.venv` 启动发布引擎，无需预先冻结 Python。`npm run dev` 只启动前端服务，完整账号与发布功能请使用 `npm run desktop:dev`。本地首次启动同样需要可用的系统凭据服务。

## 检查与构建

前端及 Rust 宿主核心检查（首次 UI 测试先执行 `npx playwright install chromium`，或用 `EASY_PUBLISH_CHROMIUM` 指定现有 Chromium 路径）：

```sh
npm run check
npm run build
npm run test:ui
cargo test --manifest-path src-tauri/Cargo.toml --no-default-features --locked
cargo fmt --manifest-path src-tauri/Cargo.toml --check
```

引擎测试使用本地 fixture，不登录或发布至真实账号。macOS：

```sh
PYTHONPATH=engine .venv/bin/python -m pytest engine/tests -q
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "engine"
.\.venv\Scripts\python.exe -m pytest engine/tests -q
```

在**目标系统、目标架构**上先冻结引擎，再构建桌面安装包。以下示例使用 macOS `.venv`；Windows 将 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe`：

```sh
.venv/bin/python scripts/build-engine.py
npm run tauri -- build
```

PyInstaller 不交叉编译：Apple Silicon、Intel Mac 和 Windows 各自生成对应引擎。冻结后的引擎可用 `scripts/smoke-engine.py` 单独检查，准确文件名、目标参数和安装包说明见 [桌面开发与构建](docs/desktop.md)。

[构建工作流](.github/workflows/build.yml) 分别构建三种目标，执行检查后上传 DMG / Windows NSIS 安装包为 Actions artifact，不自动创建 Release。当前 macOS 配置为 ad-hoc 完整性签名，尚无 Developer ID 签名或 Apple 公证；Windows 也未配置商业代码签名。构建、启动检查和 fixture 通过均不等于真实平台发布验收。

## 实现与来源

- [架构与本地接口](docs/architecture.md)
- [桌面宿主与打包说明](docs/desktop.md)
- [文章适配来源及验证范围](engine/easy_publish/vendor/social_auto_upload/NOTICE.md)
- [视频适配来源及验证范围](engine/easy_publish/providers/VIDEO_SOURCES.md)
- [第三方来源与许可](THIRD_PARTY_NOTICES.md)
- [仓库协作规范](AGENTS.md)

项目使用 [MIT 许可证](LICENSE)。参考 social-auto-upload 的平台流程及 oDesk 的微信公众号发布器，固定来源与保留声明见第三方说明。

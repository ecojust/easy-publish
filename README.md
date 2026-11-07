# Easy Publish

把一篇文章或一个视频发布到多个平台的本地桌面工作台。文章与视频分开管理，支持多个账号、内容预检、平台草稿和逐个目标的发布记录。

面向 **macOS 13.5+（Apple Silicon / Intel）和 Windows 10+ x64**。桌面使用 Tauri 2 和系统 WebView，发布引擎随安装包提供，普通用户无需安装 Python、Node.js 或开发工具。macOS 最低版本由内置 Patchright 的 Node 24 驱动要求决定，来源见[桌面说明](docs/desktop.md)。

当前开发版本为 `0.1.6`，已发布安装包见 [GitHub Release](https://github.com/ecojust/easy-publish/releases/tag/v0.1.5)。已实现的平台适配使用隔离的页面与 HTTP fixture 验证，**尚未完成真实账号登录及发布验收**；网站改版、账号权限和人工验证都可能影响实际操作。

## 平台能力

下表对应应用实际展示的平台目录。这里的“发布”表示执行平台提交流程；任务会进一步区分已受理、已确认发布和结果未知。

| 分类 | 平台 | 当前操作 | 主要要求 |
| --- | --- | --- | --- |
| 文章 | 微信公众号 | 平台草稿、发布 | AppID / AppSecret；封面；正文图片先导入本地素材；对应官方接口权限 |
| 文章 | 知乎 | 发布 | 在发布浏览器中登录 |
| 文章 | 大鱼号 | 发布 | 在发布浏览器中登录；选择信息来源，转载需原文链接 |
| 文章 | 雪球 | 平台草稿、发布 | 在发布浏览器中登录；选择创作声明，可设置分享范围 |
| 视频 | 抖音 | 发布 | 使用平台自动封面；扫码后进入创作者首页也能确认登录 |
| 视频 | 快手 | 发布 | 支持封面；标题、描述与话题合计 500 字符；当前支持最多 4 个话题，声明按页面要求选择 |
| 视频 | 小红书 | 发布 | 可指定封面；标题最多 20 字、话题最多 10 个 |
| 视频 | 微信视频号 | 平台草稿、发布 | 微信扫码登录；短标题为 7–15 字；可指定封面 |
| 视频 | 哔哩哔哩 | 投稿 | 浏览器登录；选择分区和原创／转载；1–12 个标签；可指定封面 |

微信公众号与微信视频号使用不同的账号入口。图片笔记不属于当前文章发布能力。平台原生的审核、短信、扫码、验证码及实名验证需要使用者在打开的浏览器中完成。

## 安装与下载

从 [Release 页面](https://github.com/ecojust/easy-publish/releases/tag/v0.1.5) 下载对应安装包：

| 系统 | 下载 |
| --- | --- |
| macOS Apple Silicon（M1–M5） | [ARM DMG](https://github.com/ecojust/easy-publish/releases/download/v0.1.5/Easy-Publish_0.1.5_macos-arm64.dmg) |
| macOS Intel | [Intel DMG](https://github.com/ecojust/easy-publish/releases/download/v0.1.5/Easy-Publish_0.1.5_macos-x64.dmg) |
| Windows x64 | [安装程序](https://github.com/ecojust/easy-publish/releases/download/v0.1.5/Easy-Publish_0.1.5_windows-x64-setup.exe) |

升级前完全退出旧版。macOS 打开 DMG，将应用拖入“应用程序”；Windows 运行安装程序。Release 同时提供 `SHA256SUMS.txt`。后续开发构建仍可从成功的 [Actions 运行](https://github.com/ecojust/easy-publish/actions/workflows/build.yml) 下载 artifact 并解压 ZIP，Actions 下载通常需要登录 GitHub。

macOS 请使用 0.1.1 或之后的构建。0.1.0 在部分 Mac 上会因启动程序与内置 Python 库的签名身份冲突而立即退出；0.1.1 修正了 ad-hoc 签名配置。替换应用前请完全退出旧版本，包括启动错误弹窗。

当前构建尚未配置 Apple 公证或 Windows 商业代码签名。macOS 首次打开如果提示无法验证开发者，可在“系统设置 → 隐私与安全性”中允许打开这份自行确认来源的构建。无需关闭系统的整体安全保护。Windows 安装器在缺少 WebView2 时会下载其运行环境，需要网络；发布浏览器可在应用的设置中准备。

## 使用流程

1. 打开“设置”，查看发布浏览器和应用账号存储状态。应用会先查找已安装的 Chrome / Edge / Chromium；没有可用浏览器时，点击“下载并准备浏览器”。下载需要网络，公众号官方 API 操作本身不依赖浏览器。
2. 在“账号管理”添加账号并连接，通过“发布时默认选中”开关设置常用账号；公众号在这里选择自己的文章模板。抖音、小红书和视频号优先检查应用已保存的会话，需要重新登录时才打开独立浏览器；公众号填写开发者凭据后检查连接。连接期间可点击“取消连接”，等待浏览器清理后重试。
3. 文章页上方填写标题、作者、封面等资料，在一个正文输入框中输入纯文本。点击整理后，内置 OpenCode 把原文整理为 Markdown，下方按各个账号展示最终平台预览。原文独立保留，修改正文后需要重新整理；已经写好的 Markdown 稿件可直接预览。视频页仍支持导入 MP4、MOV、M4V、WebM，填写标题、描述、话题及封面。
4. 文章自动使用账号管理中已开启且连接就绪的兼容账号，无需在文章页再次勾选。选择“发布内容”或“平台草稿”，填写相应平台的来源或创作声明，检查预览及内容要求后创建任务。
5. 在“发布任务”查看每个账号的进度与结果。任务保存创建时的内容快照，后续修改本地稿件不会改变已创建的任务。

文章的发布目标跟随账号管理开关，并按连接状态、内容类型及当前操作筛选；视频保留稿件内的账号勾选区。新旧账号的开关初始均为开启。开关不会自动发布，后台刷新不会覆盖未保存内容。

已有投稿历史的稿件可以再次发布。已提交、已发布及结果不明的平台默认跳过，即使账号开关开启也一样；需要再次投稿时显式加入本次发布。平台草稿不计作已发布。每次新投稿保留独立记录；同一次请求重试不会重复创建批次。

同一稿件可填写多个平台需要的字段，发布时按目标使用。例如填写封面后，支持封面的平台会使用它，抖音仍使用平台自动封面；B 站选择原创时忽略已填写的转载来源。原稿保留完整字段，平台真正必填的内容和长度限制仍需满足。

编辑器的“保存”只保存本地稿件。“保存平台草稿”会向所选平台提交内容。

| 任务结果 | 含义 |
| --- | --- |
| 已保存草稿 | 平台确认草稿存在，尚未正式发布 |
| 已提交 | 平台已受理，最终公开状态仍需核对 |
| 已发布 | 适配器获得平台发布确认 |
| 需要处理 | 账号、权限、验证或内容要求需要处理 |
| 结果待确认 | 操作可能已产生平台内容；请先前往平台核对，避免重复提交 |

取消任务不会撤回已经产生的平台内容。应用不会自动重试结果未知的任务；只有明确尚未提交的失败或取消任务才提供重试。

小红书视频会先等待上传与转码完成，再填写标题、正文、话题和封面；传输达到 100% 仍可能处于处理中。发布任务会显示上传进度和具体失败阶段，上传期间请保持窗口打开。

视频号会在上传／处理期间持续跟踪内部编辑页面，页面重载后重新定位并核对正文，避免停在 98% 时因旧控件失效而结束任务。封面在上传就绪后设置，最终只提交一次；平台验证或弹窗仍需要在发布窗口中处理。

## 文章模板与平台排版

每个公众号在账号管理中选择自己的模板，同一篇文章可以为不同账号生成不同版式。创建发布任务时冻结模板，之后修改账号设置不会改变已排队任务：

| 模板 | 风格 |
| --- | --- |
| 清爽蓝 | 蓝色标题带与清晰留白，适合通用长文 |
| 墨色杂志 | 衬线标题、纸张底色与细分隔线，适合专栏 |
| 暖色手记 | 暖色标题与柔和引用块，适合故事和随笔 |
| 理性科技 | 青绿色层级与深色代码区，适合技术文章 |
| 森林来信 | 绿色边框与圆角卡片，适合自然和生活方式 |
| 黑金画报 | 黑金色块与几何边框，适合专题和品牌内容 |

六套版式参考 oDesk 公众号模板方向，分别设计标题、引用、列表、强调、代码块、表格和图注。账号设置中的模板卡片展示真实排版缩略图，切换模板保留原文与整理结果，不再次调用模型。图片标题可用于图注，详见 [文章模板说明](docs/article-templates.md)。

整理文章使用安装包内的 OpenCode 和官方免密钥免费模型入口，需要联网。只有点击整理才会把原文发送到该模型；保存、预览及切换模板不会发送。免费服务可能限流或暂时不可用，应用保留原文并提示重试，不自动改用付费模型。整理与发布是独立操作，生成结果应在下方预览中核对。设置页可检查内置整理服务状态，无需登录个人 OpenCode 或提供密钥。

发布前会根据目标编辑器转换 HTML。公众号与大鱼号保留其支持的内联样式；知乎、雪球使用平台原生排版，不能保证保留全部颜色和字体。不支持的表格或代码结构会转换为可读内容。选择目标平台预览可查看对应效果和说明，正文图片仍需先导入本地素材。

## MCP：供其他 agent 调用

打开“设置 → MCP”，复制本机配置到支持 stdio MCP 的客户端。配置使用应用自带引擎，无需安装 Python；使用时保持 Easy Publish 桌面应用运行，并先在账号管理中连接平台账号。

Agent 可以导入本地素材、保存文章／视频、预览、预检并发布，也可以查询每个账号的结果。Agent 写好的 Markdown 可直接保存，公众号沿用账号管理中的模板。文章只允许选择文章账号，视频只允许选择视频账号；默认只选择开启“发布时默认选中”、已连接且尚未向其平台提交过的兼容账号。明确指定账号可覆盖默认偏好并再次发布，新发布使用新的 `requestId`，重试同一次工具请求复用原 `requestId`。

MCP 配置不含账号凭据或会话，发布工具通过同一个桌面队列执行；收到“已提交”不代表审核通过，结果不明不会自动重发。工具参数和使用示例见 [MCP 接入说明](docs/mcp.md)。

## 微信公众号配置

在微信公众平台取得该公众号的 AppID 和 AppSecret，并检查素材、草稿及发布接口权限。把运行本应用的网络出口公网 IP 加入公众号的 IP 白名单；更换网络后可能需要更新白名单。

“检查连接”验证凭据能否取得访问令牌，不代表所有发布接口均已授权。文章需要 JPEG / PNG 封面；正文图片也通过本地导入后上传。适配器按顺序上传素材、创建草稿，并在选择发布时调用微信发布接口查询结果。

这里的正式发布使用 `freepublish` 接口，**不包含向关注者群发消息**。权限不足会显示可处理的错误；已经创建的草稿或已受理请求会保留其回执，连接中断不会自动重发内容。

## 哔哩哔哩视频投稿

在账号管理中添加哔哩哔哩账号，通过打开的浏览器完成登录。应用读取该账号创作中心实际可用的分区；多个 B 站账号同时投稿时，编辑器只提供共同支持的分区。若分区为空或权限变化，请重新检查账号连接。

在视频工作台选择账号、分区及“原创／转载”。转载填写原始视频链接，原创会忽略来源字段；标题最多 80 字，简介最多 2000 字，标签为 1–12 个、每个最多 20 字。可选 JPEG / PNG 封面不超过 5 MiB，不指定封面时由平台处理。

本版本支持普通视频单稿件投稿，不提供 B 站平台草稿、合集或定时发布。取得稿件回执后记为“已提交”，最终审核和公开状态在创作中心核对。上传或提交后中断会保留“结果未知”，请先检查平台，避免重复投稿。无需安装 biliup 命令行工具。

## 本地数据

稿件、素材、账号公开信息和任务记录保存在当前电脑的应用数据目录。AppSecret 和浏览器登录会话使用 AES-GCM 加密，密钥仅保存在 Easy Publish 应用数据目录内，不访问系统钥匙串／凭据管理器。发布浏览器使用独立的临时配置，只恢复本应用保存的登录信息，不读取日常浏览器的密码、Cookie 或个人资料。界面不回显 AppSecret。

从 0.1.3 升级时，旧版加密文件保留，但不会通过钥匙串解密；浏览器账号需重新连接一次，公众号凭据需重新填写。稿件、素材和发布记录保留。

密文和密钥同属应用数据目录：macOS／Linux 使用私有目录和文件权限，Windows 使用当前用户应用目录的继承权限。能够读取整个目录的进程也可能解密账号信息，备份应将其视作敏感数据。

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

开发桌面从根目录 `.venv` 启动发布引擎，无需预先冻结 Python。原文整理另需先运行 `python scripts/prepare-opencode.py` 准备当前系统的内置 OpenCode。`npm run dev` 只启动前端服务，完整账号与发布功能请使用 `npm run desktop:dev`。首次启动在应用数据目录内创建本地加密密钥。

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
.venv/bin/python scripts/prepare-opencode.py
npm run tauri -- build
```

PyInstaller 不交叉编译：Apple Silicon、Intel Mac 和 Windows 各自生成对应引擎。冻结后的引擎可用 `scripts/smoke-engine.py` 单独检查，准确文件名、目标参数和安装包说明见 [桌面开发与构建](docs/desktop.md)。

[构建工作流](.github/workflows/build.yml) 分别构建三种目标，执行检查后上传 DMG / Windows NSIS 安装包为 Actions artifact。[手动发布工作流](.github/workflows/release.yml) 核对 tag、成功构建及应用源码一致性，验证 artifact ZIP 摘要，再将原始安装包与 SHA-256 清单发布到 Release。当前 macOS 配置为 ad-hoc 完整性签名，尚无 Developer ID 签名或 Apple 公证；Windows 也未配置商业代码签名。构建、启动检查和 fixture 通过均不等于真实平台发布验收。

## 实现与来源

- [架构与本地接口](docs/architecture.md)
- [桌面宿主与打包说明](docs/desktop.md)
- [文章适配来源及验证范围](engine/easy_publish/vendor/social_auto_upload/NOTICE.md)
- [视频适配来源及验证范围](engine/easy_publish/providers/VIDEO_SOURCES.md)
- [哔哩哔哩协议来源及验证范围](engine/easy_publish/providers/BILIBILI_SOURCES.md)
- [快手适配来源及验证范围](engine/easy_publish/providers/KUAISHOU_SOURCES.md)
- [MCP 接入说明](docs/mcp.md)
- [上游 CLI 登录方式对照](docs/upstream-authentication.md)
- [第三方来源与许可](THIRD_PARTY_NOTICES.md)
- [仓库协作规范](AGENTS.md)

项目使用 [MIT 许可证](LICENSE)。参考 social-auto-upload 的平台流程、oDesk 的微信公众号发布器及 biliup 的 B 站投稿协议，固定来源与保留声明见第三方说明。

# 桌面外壳与安装包

Easy Publish 使用 Tauri 2 系统 WebView。macOS ARM、macOS Intel 与 Windows x64 分别构建原生安装包，版本为 `0.1.8`。macOS 最低为 13.5；Windows 为 Windows 10／11 x64。该下限匹配内置 Patchright 驱动的 [Node.js 24 系统要求](https://github.com/nodejs/node/blob/v24.13.0/BUILDING.md#platform-list)。Python 3.12 发布引擎由 PyInstaller 打包并随应用安装，另内置 OpenCode 1.19.5 用于原文整理，另内置 Wenyan 官方排版核心并复用已有 Node 驱动，用户无需安装 Python、Node.js、Wenyan CLI 或 OpenCode。发布浏览器由引擎管理，首次使用在应用中准备。

## 开发

开发机需要 Node.js 22.12+、Python 3.12、Rust stable 和 [Tauri 系统依赖](https://v2.tauri.app/start/prerequisites/)。在仓库根目录执行：

```sh
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install -r engine/requirements.txt
# Windows 请改用 .venv\Scripts\python.exe
npm ci
npm run prepare:wenyan
python scripts/prepare-opencode.py
npm run desktop:dev
```

开发配置 `src-tauri/tauri.dev.conf.json` 不要求预先生成独立引擎；Rust 从根目录 `.venv` 启动 `python -m easy_publish`，并将 `engine` 加入 Python 搜索路径。前端开发服务仅绑定 `127.0.0.1:1420`。原文整理使用 `src-tauri/binaries/` 下当前目标的 OpenCode；没有准备该文件时，设置页会提示缺少整理引擎，其他编辑和发布功能仍可使用。

## 原生构建

在目标系统、目标架构的 Python 3.12 环境中安装依赖后执行；PyInstaller 不交叉编译：

```sh
python -m pip install -r engine/requirements.txt pyinstaller==6.22.3
npm run prepare:wenyan
python scripts/build-engine.py
python scripts/prepare-opencode.py
python scripts/smoke-engine.py src-tauri/binaries/easy-publish-engine-<Rust target triple>
npm run tauri -- build
```

两个 sidecar 的 Windows 文件名均另有 `.exe` 后缀。`prepare-opencode.py --target <Rust target triple>` 从官方固定版本下载对应归档，校验锁定清单 `scripts/opencode-runtime.json` 的大小和 SHA-256，提取后检查版本、离线启动本机服务并验证认证及正常退出；Intel／Windows 使用 baseline 构建。也可通过 `--check <可执行文件绝对路径>` 验证已有二进制。OpenCode 原始 MIT 许可随应用资源提供。

发布引擎构建脚本收集 Patchright Node 驱动、平台驱动和许可证，明确排除 keyring，不包含用户登录数据。账号加密密钥和会话仅保存在本应用目录，启动不访问系统钥匙串。应用数据位于系统应用数据目录 `com.ecojust.easy-publish`。

工作流 `.github/workflows/build.yml` 对三个目标分别执行前端检查、Python 测试、Rust 核心测试、冻结引擎启动检查、Tauri 打包和原生宿主 `--smoke-test`。最后上传安装包为工作流 artifact；Release 由独立的手动发布工作流转存已验证产物，不重复编译。原生启动检查启动内置引擎、读取带认证的状态、确认版本和正常关闭；它不证明真实平台账号登录或发布成功。

macOS 使用 ad-hoc 签名并验证 bundle 完整性；此签名方式关闭 Hardened Runtime，避免内置 Python 与动态库没有 Developer Team ID 时被系统拒绝加载，未进行 Apple Developer ID 签名和公证。Windows 安装包内含 WebView2 安装引导程序；缺少 WebView2 时会联网安装。当前未配置商业代码签名证书。不能把这些构建描述为已签名公证的正式分发版本。

## 原文整理 sidecar

仅显式整理或设置页检查才启动 `easy-publish-opencode`，不在应用打开或普通保存时调用模型。引擎为每次操作创建应用数据目录下的独立 HOME、XDG、配置与临时会话，使用随机认证密码连接仅绑定本机的服务；不读取个人 OpenCode 登录、浏览器资料或系统钥匙串。任务结束或取消后结束进程并删除临时会话。

模型固定为官方 `opencode/big-pickle` 免费入口，调用前核对模型目录中输入、输出及缓存成本。工具、命令、插件、技能、MCP 和自动升级均关闭；文章只作为文本输入。系统管理配置若覆盖受控设置，整理会停止。免费服务的配额和可用性由提供方决定，不会自动选择收费模型。

设置页检查只核对本地服务及模型目录，不发送文章，也不保证下一次模型请求一定成功。真正整理会将原文发给外部模型服务；结果通过版本与图片占位校验后保存，预览和发布仍由本应用的受控渲染器完成。不能把这些限制描述为操作系统级沙盒。

## 原生接口边界

Rust 宿主读取引擎的单行启动握手，保存随机端口和 token，不将 token 交给 WebView 或写入日志。引擎仅监听本机；宿主禁用 HTTP 代理和重定向。接口方法、路径与 ID 都经白名单匹配，renderer 无法调用退出接口。JSON 请求最大 4 MiB、响应最大 16 MiB。

文件导入必须使用本次运行中原生文件选择器选中的路径。平台链接只接受允许域名的 HTTPS 地址；WebView 不允许导航至外部网页或打开新窗口。宿主仅暴露 `api_request`、`pick_file`、`open_external`、`mcp_configuration`，不暴露任意 shell 或文件读取 API。

重复启动应用时聚焦已有窗口，避免同一数据目录运行多个宿主。正常退出先请求引擎关闭任务，再结束宿主拥有的引擎进程；任务、浏览器进程与会话清理由引擎负责。测试密钥相关环境变量在 release 启动时清除。

## 可独立验证的宿主逻辑

```sh
cargo test --manifest-path src-tauri/Cargo.toml --no-default-features --locked
cargo fmt --manifest-path src-tauri/Cargo.toml --check
```

以上核心测试不需要图形库，覆盖 API 白名单、平台链接、导航、HTTP 认证、拒绝重定向、token 脱敏和任意文件读取限制。完整桌面编译仍需要目标平台的 Tauri 系统依赖。

启动失败时，宿主仅保留最多 16 KiB 的启动 stderr，在应用数据目录 `logs/engine-startup.log` 中记录退出结果和加载错误；弹窗显示退出结果与日志路径。stdout 握手从不写入日志，涉及凭据的诊断行和用户主目录会脱敏，引擎就绪后停止保留 stderr。

设置页提供本机 MCP 配置，复用打包内的引擎 `--mcp` 入口，配置不含连接 token。MCP 客户端需要与桌面应用以同一系统用户运行，应用保持打开；详见 [MCP 接入说明](mcp.md)。安装包 smoke 同时验证标准 stdio 初始化、工具发现、与桌面共享内容库及文章模板预览，不调用真实平台发布。

## 发布已验证版本

确认三个目标的构建和启动检查通过后，将相同应用源码合并到 master 并建立版本 tag。手动触发 `.github/workflows/release.yml`，指定版本 tag、成功构建 run ID 和精确的构建源提交。发布脚本要求 tag 指向当前 checkout，版本一致，除明确允许的发布文档、历史记录和发布脚本外，文件树与构建源一致。

流程下载并验证三个 artifact ZIP 的 SHA-256，仅提取对应的唯一安装器，生成 `SHA256SUMS.txt`，完整上传 draft 后再公开 Release。已公开版本不会被覆盖；Release 说明位于 `docs/releases/`。应用签名、最低系统和真实平台验收范围仍以上文为准。

## Wenyan 本地排版

`runtime/wenyan` 独立锁定官方核心及渲染依赖，`npm run prepare:wenyan` 生成供引擎使用的分发文件。冻结构建将其收集进引擎，运行时复用 Patchright 自带的 Node，使用受限 JSON 输入输出；不调用全局 npm 或用户安装的 CLI。预览与微信公众号发送共用渲染结果，其他文章平台仍按编辑器能力转换。原生安装包检查通过 MCP 实际渲染 Wenyan 主题，验证脱离开发环境也能排版。

# 桌面外壳与安装包

Easy Publish 使用 Tauri 2 系统 WebView。macOS ARM、macOS Intel 与 Windows x64 分别构建原生安装包，版本为 `0.1.1`。macOS 最低为 13.5；Windows 为 Windows 10／11 x64。该下限匹配内置 Patchright 驱动的 [Node.js 24 系统要求](https://github.com/nodejs/node/blob/v24.13.0/BUILDING.md#platform-list)。Python 3.12 发布引擎由 PyInstaller 打包并随应用安装，用户无需安装 Python。发布浏览器由引擎管理，首次使用在应用中准备。

## 开发

开发机需要 Node.js 22.12+、Python 3.12、Rust stable 和 [Tauri 系统依赖](https://v2.tauri.app/start/prerequisites/)。在仓库根目录执行：

```sh
python -m venv .venv
# macOS / Linux
.venv/bin/python -m pip install -r engine/requirements.txt
# Windows 请改用 .venv\Scripts\python.exe
npm ci
npm run desktop:dev
```

开发配置 `src-tauri/tauri.dev.conf.json` 不要求预先生成独立引擎；Rust 从根目录 `.venv` 启动 `python -m easy_publish`，并将 `engine` 加入 Python 搜索路径。前端开发服务仅绑定 `127.0.0.1:1420`。

## 原生构建

在目标系统、目标架构的 Python 3.12 环境中安装依赖后执行；PyInstaller 不交叉编译：

```sh
python -m pip install -r engine/requirements.txt pyinstaller==6.22.3
python scripts/build-engine.py
python scripts/smoke-engine.py src-tauri/binaries/easy-publish-engine-<Rust target triple>
npm run tauri -- build
```

Windows 独立引擎文件名另有 `.exe` 后缀。构建脚本收集 Patchright Node 驱动、平台驱动、许可证和系统凭据后端，不包含用户登录数据。应用数据位于系统应用数据目录 `com.ecojust.easy-publish`。

工作流 `.github/workflows/build.yml` 对三个目标分别执行前端检查、Python 测试、Rust 核心测试、冻结引擎启动检查、Tauri 打包和原生宿主 `--smoke-test`。最后上传安装包为工作流 artifact，不创建 Release。原生启动检查启动内置引擎、读取带认证的状态、确认版本和正常关闭；它不证明真实平台账号登录或发布成功。

macOS 使用 ad-hoc 签名并验证 bundle 完整性；此签名方式关闭 Hardened Runtime，避免内置 Python 与动态库没有 Developer Team ID 时被系统拒绝加载，未进行 Apple Developer ID 签名和公证。Windows 安装包内含 WebView2 安装引导程序；缺少 WebView2 时会联网安装。当前未配置商业代码签名证书。不能把这些构建描述为已签名公证的正式分发版本。

## 原生接口边界

Rust 宿主读取引擎的单行启动握手，保存随机端口和 token，不将 token 交给 WebView 或写入日志。引擎仅监听本机；宿主禁用 HTTP 代理和重定向。接口方法、路径与 ID 都经白名单匹配，renderer 无法调用退出接口。JSON 请求最大 4 MiB、响应最大 16 MiB。

文件导入必须使用本次运行中原生文件选择器选中的路径。平台链接只接受允许域名的 HTTPS 地址；WebView 不允许导航至外部网页或打开新窗口。宿主仅暴露 `api_request`、`pick_file`、`open_external`，不暴露任意 shell 或文件读取 API。

重复启动应用时聚焦已有窗口，避免同一数据目录运行多个宿主。正常退出先请求引擎关闭任务，再结束宿主拥有的引擎进程；任务、浏览器进程与会话清理由引擎负责。测试密钥相关环境变量在 release 启动时清除。

## 可独立验证的宿主逻辑

```sh
cargo test --manifest-path src-tauri/Cargo.toml --no-default-features --locked
cargo fmt --manifest-path src-tauri/Cargo.toml --check
```

以上核心测试不需要图形库，覆盖 API 白名单、平台链接、导航、HTTP 认证、拒绝重定向、token 脱敏和任意文件读取限制。完整桌面编译仍需要目标平台的 Tauri 系统依赖。

启动失败时，宿主仅保留最多 16 KiB 的启动 stderr，在应用数据目录 `logs/engine-startup.log` 中记录退出结果和加载错误；弹窗显示退出结果与日志路径。stdout 握手从不写入日志，涉及凭据的诊断行和用户主目录会脱敏，引擎就绪后停止保留 stderr。

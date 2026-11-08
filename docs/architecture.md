# 本地多平台发布 · 实现约定

用户要求本地桌面软件，覆盖 macOS ARM／Intel 与 Windows，内容分为文章和视频两类。桌面采用 Tauri 2、React／TypeScript，发布引擎采用独立 Python 3.12 进程；引擎随安装包提供。运行任务时使用独立发布浏览器，首次使用可在应用中准备。

## 参考与范围

- social-auto-upload 固定参考 `3847fff01f5434a8a32829833163cd6723cabc59`（MIT）；平台驱动、登录与作品回执分开。
- oDesk 固定参考 `d062c05b3442f45205ff83cc28497d435fccce99`（MIT）；借鉴公众号文章、封面和正文图片流程，使用确定性的官方 API。
- 文章：微信公众号、知乎、大鱼号、雪球；视频：抖音、快手、小红书、视频号、哔哩哔哩。知乎视频、大鱼号视频尚未接入。
- 哔哩哔哩基于 MIT 许可 biliup/biliup `f23b7614b250caacba8a1475412515264dc30558` 的 HTTP 协议独立实现，使用浏览器登录、UPOS 分片上传和单次 Web 投稿，不下载原生 CLI。
- 公众号草稿和正式提交／发布分别记录；不把图片笔记标成文章，不把请求受理、点击发布或进程退出描述成公开发布成功。

## 进程与 API

桌面启动 `easy-publish-engine` sidecar（开发时 Python module），参数 `--data-dir <应用数据目录> --parent-pid <宿主PID>`。原生窗口使用单实例插件，引擎持有内容库独占锁；宿主异常退出时引擎停止，worker 另监控引擎存活。服务仅监听 127.0.0.1 随机端口，生成一次性 bearer token；启动后 stdout 输出一行 `{"event":"ready","port":12345,"token":"..."}`。此行仅供宿主消费，不写入日志。

Tauri 命令：

- `api_request({method,path,body})`：宿主向自己的引擎转发已认证 JSON 请求，路径只允许 `/api/` 下的已定义接口。
- `pick_file({kind})`：原生选择 `video`、`image` 或 `article` 文件，返回绝对路径或 null。
- `open_external({url})`：只打开允许的 HTTPS 平台链接。
- `mcp_configuration()`：返回本机 stdio 引擎命令、参数及仅开发环境所需的 PYTHONPATH；不读取或返回 MCP token。

浏览器开发／验证可注入 `window.easyPublish` 同形接口；测试替身必须明确标注，不在产品中伪造已登录账号或发布成功。

API 成功直接返回 JSON，失败为 `{"error":"公开且已脱敏的错误","code":"..."}`。所有 API 都校验 bearer token；凭据与登录会话不返回前端状态。

| 方法／路径 | 参数／响应 |
| --- | --- |
| GET /api/state | `{platforms,accounts,contents,batches,runtime,articleTemplates}`，见 `src/shared/types.ts` |
| POST /api/preview/article | `{body,articleTemplateId,platformId?,accountId?}`；返回安全 HTML 及平台格式说明；accountId仅允许公众号并按账号模板生成，不保存或发布 |
| POST /api/mcp/call | `{name,arguments}`；仅专用 MCP token 和实例编号可用，返回 MCP CallToolResult |
| POST /api/contents | ContentInput；返回 Content |
| PUT /api/contents/:id | ContentInput；返回 Content |
| DELETE /api/contents/:id | 删除本地稿件；返回 `{ok:true}` |
| POST /api/assets/import | `{path,kind:video\|image\|article}`；返回 Asset，文章另含 `text` |
| GET /api/assets/:id | 返回 Asset 公开元数据，不返回本地路径 |
| GET /api/assets/:id/preview | 图片返回 `{dataUrl}` |
| POST /api/accounts | `{platformId,label,credentials?:{appId,appSecret}}`；返回 Account |
| PUT /api/accounts/:id/preferences | `{defaultSelected?:boolean,wechatTemplateId?:ArticleTemplateId,videoOptions?:Record<string,string>}`；非空部分更新，严格布尔；模板字段仅公众号可用，不改变登录或已创建任务 |
| DELETE /api/accounts/:id | 删除本地账号和会话，不操作平台内容 |
| POST /api/accounts/:id/login | 启动登录／公众号连接检查，返回 `{ok:true}`；state 轮询进度 |
| POST /api/accounts/:id/check | 检查会话；返回 `{ok:true}` |
| POST /api/accounts/:id/cancel | 取消当前账号的登录／检查，等待对应 worker 清理后返回 `{ok:true}`；已结束时不改写状态，不影响发布任务 |
| POST /api/preflight | `{contentId,accountIds,mode:publish\|draft}`；返回 `{ok,targets:[{accountId,errors:string[],warnings:string[]}]}` |
| POST /api/batches | 上述字段及 `requestId` UUID；同一 requestId 不重复创建；返回 Batch |
| POST /api/batches/:id/cancel | 取消未完成任务；提交后的不明结果记 unknown |
| POST /api/tasks/:id/retry | 仅重新执行明确未提交的失败／取消任务；返回 `{ok:true}` |
| POST /api/runtime/browser/install | 准备浏览器，state 显示进度 |
| POST /api/shutdown | 仅宿主退出使用，结束自身工作进程 |

## 任务与数据

SQLite 保存稿件、账号公开元数据、批次及各目标状态。`Content.platformPublicationStates` 从全部任务读取每个平台最新的 submitted／published／unknown 回执，不受界面最近 200 批限制；草稿和失败不覆盖历史成功。已发布稿件可以使用新的 requestId 再次投稿，同一 requestId 仍返回原批次。批次捕获不可变稿件快照、账号 ID／名称和目标，逐个准备目标，分别给出结果。网页目标进入 awaiting_user 后可继续准备其他账号，最多三个活动发布任务；同账号任务不会并发。已有待执行任务的账号不能被删除或重新登录。明确失败的目标可重试；成功目标不重复，结果未知的目标须先在平台核对。关闭应用后不自动重新提交未确认结果。

`Account.defaultSelected` 保存在独立的 `account_preferences` 表，旧账号缺少记录时返回 true；SQLite schema 版本 5 将公众号的旧默认 clean 一次升级为 wenyan-default，版本 6 增加可空的 video_options JSON，保留其他主题与账号开关；新账号默认 wenyan-default，已冻结批次不迁移。删除账号同步清理偏好，重新连接不覆盖偏好。文章和视频页均按偏好、连接状态、类型／操作能力和既有投稿历史自动筛选，不另设账号勾选区；已有投稿记录可显式加入本次再次发布。发布前检查绑定稿件、目标和模式，任意变化使旧检查失效，过期响应不能重新启用发布。账号开关不会创建任务，常规轮询不覆盖未保存稿件。

`ContentInput.articleTemplateId` 包含八款 wenyan-* 官方主题及 clean／editorial／journal／tech／forest／artdeco 六款兼容主题，旧 API 稿件仍默认 clean。新公众号任务以账号的wechatTemplateId为准，创建批次时在accountTemplateIds映射中冻结；之后修改账号模板不会改变队列内任务。Markdown 保持原稿，在生成每个目标的 payload 时按模板及平台转换 HTML；模板缩略图、预览与实际提交使用同一服务端渲染器；API 与 MCP 复用引擎的 ArticleTemplateId 类型。官方及兼容模板来源、图注和平台兼容说明见 [文章模板](article-templates.md)。

文章 `inputFormat` 固定为 `markdown`，`body` 直接保存用户编辑内容。没有模型调用、生成任务或免费额度状态。文章右上角“预览”打开弹窗，按所有已连接文章账号查看模板及平台效果；没有账号时使用 Wenyan 默认主题，预览不改变本次发布目标；关闭预览保留未保存编辑。官方主题通过内置 Wenyan CLI 的本地 render 路径生成，预览与发布共用同一入口。

SQLite schema 7 一次性迁移旧纯文本稿件：原文和图片指纹仍匹配的生成结果保留为 Markdown，否则将最新原文按字面文本转入 Markdown 并恢复已导入图片的占位位置。若转义会超过 50 万字符限制，则完整保留原文作为可编辑 Markdown，不截断；图片标记只在全部替换后仍符合上限时转换。精确旧原文、旧结果及来源信息归档，公开 `sourceText` 仅供“查看旧版原文”；客户端保存不能覆盖此归档。旧 Markdown 稿件和已冻结发布批次保持原数据。

`ContentInput.platformOptions` 按平台 ID 保存声明等表单值；平台通过 `Platform.fields` 声明必填项、选项及提示，前端与 preflight 都校验。适配器收到当前目标的平面 `PublishPayload.options`。预检素材读取和实际 payload 生成共用 `target_projection.py`，在副本中移除目标不使用的字段和选项：抖音不消费自定义封面，B 站原创不消费转载来源，大鱼号非转载不消费来源链接。视频忽略文章正文和正文图片；文章忽略视频素材。原稿及批次快照保留完整输入，使用到的素材、必填项及平台长度限制继续校验。

哔哩哔哩的 `copyright`、`tid`、`source` 分别表示原创／转载、投稿分区和转载来源。登录／检查连接读取账号可用分区，worker 仅返回经允许列表过滤的公开分类元数据。`Account.bilibiliCategories` 保存分区 ID 与显示名称；`PlatformField.dynamicChoices` 指向此公开选项。各账号独立选择分区，预检逐账号检查，投稿前再次向平台核实当前可用分区。会话、账号身份和 cookie 仍只保存在加密凭据库。

凭据／浏览器 storage state 通过引擎凭据库保存；密钥仅保存在应用数据目录 `vault-local-v1/master.key`，不访问系统凭据服务；macOS／Linux 为目录 0700、文件 0600，Windows 继承当前用户应用目录权限。密钥与密文放在一起，不能抵御可读取完整应用目录的进程。开发测试可使用显式测试密钥。旧 `vault/` 文件保持不变，不做需要系统钥匙串的自动迁移；旧账号提示重新连接。浏览器会话在独立临时目录解密给单个 worker，结束后清理，避免账号互用。任何上游缓存或参考仓库中的凭据不得复制。

worker 的统一输入／输出和事件由 `engine/easy_publish/providers/base.py` 定义。每个任务独立进程，准备阶段有总时限；开始平台写入之前先发送 submitting 事件，父进程 SQLite 提交完成后回传 ACK，worker 收到确认才允许上传或填写。网页发布完成准备后发送 awaiting_user 并等待父进程持久确认，暂停总时限但保留进程监管；最终发布和后续确认由用户操作，适配器只观察可靠回执。收到最终结果后恢复有界退出时限。关闭、取消、退出或无法判断提交结果时按 fence 返回 unknown，不自动重试。公众号与 B 站接口提交保持现有方式；能力目录的 publishMethod 独立于登录方式 auth。

账号连接取消与发布取消分开。取消登录／检查时，Service 保留账号占用直到对应 worker 和工作目录清理结束；重复请求共享同一次清理，即使请求断开也继续完成。清理完成前不能重连或删除账号，取消不清除之前已经保存的会话；已完成的连接不被迟到的取消降级。

本轮开发验证使用隔离的接口／页面 fixture，不向真实平台发布文章或视频。真实账号授权与发布由软件用户在应用中操作；缺乏实际平台回执时如实记录验证范围。

## MCP 连接

内置引擎 `--mcp --data-dir <同一应用数据目录>` 启动标准 stdio JSON-RPC 服务，不打开第二个数据库实例。它通过私有 `mcp-endpoint.json` 连接已运行的桌面引擎，固定 loopback 地址，不使用系统代理或跟随跳转。MCP token 与原生宿主 token 不同：前者仅可访问 `/api/mcp/call`，不能读取账号密钥、启动登录或访问普通桌面 API；renderer 同样不能调用 MCP 路由。

POSIX 目录／文件要求当前用户所有且权限为 0700／0600，拒绝符号链接和不安全文件；Windows token 使用当前用户 DPAPI 保护。每次启动更换 token 和实例编号，正常退出移除对应发现文件。配置 JSON 只包含引擎路径及参数。

MCP 工具复用 Service 的预检、不可变快照、账号类型限制和发布队列。省略 accountIds 时过滤 defaultSelected 为 true、已连接、支持内容类型／操作且没有历史 submitted／published／unknown 的平台；显式账号必须通过类型与预检。发布请求 UUID 在默认账号筛选之前查重，避免已成功的重试被当成新请求。详见 [MCP 接入说明](mcp.md)。

Wenyan 排版使用锁定的 `@wenyan-md/core@3.0.12`，运行资源通过 `runtime/wenyan` 构建并冻结进引擎，复用 Patchright Node。每个 Service 懒启动一个 JSONL 子进程，异步串行排版并缓存最近结果；设定超时、输入输出及缓存上限，退出时清理。不会从 PATH 查找用户 CLI，也不运行 npm 或读取个人配置。原文 HTML 在 Markdown 解析时转义，官方脚注插值同样转义，最终主题输出再净化；只保留静态装饰，禁止活动内容与网络资源加载。预览、MCP 及微信 payload 共享这份输出；Markdown 图片检查使用轻量解析，不启动排版进程。

视频专属参数由账号管理持久化为 `Account.videoOptions`（未设置为 null）。桌面视频 `videoOptionsSource` 明确为 account，忽略但不删除稿件旧 platformOptions。API/MCP 缺省 content：目标平台有非空稿件参数时整份使用，否则回退账号；不混合两种来源。每个新视频批次的 accountVideoOptions 映射冻结实际参数，预检与 worker payload 使用同一解析。旧批次无映射时只使用原内容快照，不读当前偏好；设置及账号分区变化使界面旧预检失效。B 站分区按各账号验证，不求多个账号的交集。

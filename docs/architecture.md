# 本地多平台发布 · 实现约定

用户要求本地桌面软件，覆盖 macOS ARM／Intel 与 Windows，内容分为文章和视频两类。桌面采用 Tauri 2、React／TypeScript，发布引擎采用独立 Python 3.12 进程；引擎随安装包提供。运行任务时使用独立发布浏览器，首次使用可在应用中准备。

## 参考与范围

- social-auto-upload 固定参考 `3847fff01f5434a8a32829833163cd6723cabc59`（MIT）；平台驱动、登录与作品回执分开。
- oDesk 固定参考 `d062c05b3442f45205ff83cc28497d435fccce99`（MIT）；借鉴公众号文章、封面和正文图片流程，使用确定性的官方 API。
- 文章：微信公众号、知乎、大鱼号、雪球；视频：抖音、小红书、视频号、哔哩哔哩。知乎视频、大鱼号视频尚未接入。
- 哔哩哔哩基于 MIT 许可 biliup/biliup `f23b7614b250caacba8a1475412515264dc30558` 的 HTTP 协议独立实现，使用浏览器登录、UPOS 分片上传和单次 Web 投稿，不下载原生 CLI。
- 公众号草稿和正式提交／发布分别记录；不把图片笔记标成文章，不把请求受理、点击发布或进程退出描述成公开发布成功。

## 进程与 API

桌面启动 `easy-publish-engine` sidecar（开发时 Python module），参数 `--data-dir <应用数据目录> --parent-pid <宿主PID>`。原生窗口使用单实例插件，引擎持有内容库独占锁；宿主异常退出时引擎停止，worker 另监控引擎存活。服务仅监听 127.0.0.1 随机端口，生成一次性 bearer token；启动后 stdout 输出一行 `{"event":"ready","port":12345,"token":"..."}`。此行仅供宿主消费，不写入日志。

Tauri 命令：

- `api_request({method,path,body})`：宿主向自己的引擎转发已认证 JSON 请求，路径只允许 `/api/` 下的已定义接口。
- `pick_file({kind})`：原生选择 `video`、`image` 或 `article` 文件，返回绝对路径或 null。
- `open_external({url})`：只打开允许的 HTTPS 平台链接。

浏览器开发／验证可注入 `window.easyPublish` 同形接口；测试替身必须明确标注，不在产品中伪造已登录账号或发布成功。

API 成功直接返回 JSON，失败为 `{"error":"公开且已脱敏的错误","code":"..."}`。所有 API 都校验 bearer token；凭据与登录会话不返回前端状态。

| 方法／路径 | 参数／响应 |
| --- | --- |
| GET /api/state | `{platforms,accounts,contents,batches,runtime}`，见 `src/shared/types.ts` |
| POST /api/contents | ContentInput；返回 Content |
| PUT /api/contents/:id | ContentInput；返回 Content |
| DELETE /api/contents/:id | 删除本地稿件；返回 `{ok:true}` |
| POST /api/assets/import | `{path,kind:video\|image\|article}`；返回 Asset，文章另含 `text` |
| GET /api/assets/:id | 返回 Asset 公开元数据，不返回本地路径 |
| GET /api/assets/:id/preview | 图片返回 `{dataUrl}` |
| POST /api/accounts | `{platformId,label,credentials?:{appId,appSecret}}`；返回 Account |
| DELETE /api/accounts/:id | 删除本地账号和会话，不操作平台内容 |
| POST /api/accounts/:id/login | 启动登录／公众号连接检查，返回 `{ok:true}`；state 轮询进度 |
| POST /api/accounts/:id/check | 检查会话；返回 `{ok:true}` |
| POST /api/preflight | `{contentId,accountIds,mode:publish\|draft}`；返回 `{ok,targets:[{accountId,errors:string[],warnings:string[]}]}` |
| POST /api/batches | 上述字段及 `requestId` UUID；同一 requestId 不重复创建；返回 Batch |
| POST /api/batches/:id/cancel | 取消未完成任务；提交后的不明结果记 unknown |
| POST /api/tasks/:id/retry | 仅重新执行明确未提交的失败／取消任务；返回 `{ok:true}` |
| POST /api/runtime/browser/install | 准备浏览器，state 显示进度 |
| POST /api/shutdown | 仅宿主退出使用，结束自身工作进程 |

## 任务与数据

SQLite 保存稿件、账号公开元数据、批次及各目标状态。批次捕获不可变稿件快照、账号 ID／名称和目标，逐个目标执行，分别给出结果。已有待执行任务的账号不能被删除或重新登录。明确失败的目标可重试；成功目标不重复，结果未知的目标须先在平台核对。关闭应用后不自动重新提交未确认结果。

`ContentInput.platformOptions` 按平台 ID 保存声明等表单值；平台通过 `Platform.fields` 声明必填项、选项及提示，前端与 preflight 都校验。适配器收到当前目标的平面 `PublishPayload.options`。

哔哩哔哩的 `copyright`、`tid`、`source` 分别表示原创／转载、投稿分区和转载来源。登录／检查连接读取账号可用分区，worker 仅返回经允许列表过滤的公开分类元数据。`Account.bilibiliCategories` 保存分区 ID 与显示名称；`PlatformField.dynamicChoices` 指向此公开选项。多个账号的界面选择使用分区交集，预检逐账号检查，投稿前再次向平台核实当前可用分区。会话、账号身份和 cookie 仍只保存在加密凭据库。

凭据／浏览器 storage state 通过引擎凭据库保存；密钥保存在系统凭据服务，开发测试可使用显式测试密钥。浏览器会话在独立临时目录解密给单个 worker，结束后清理，避免账号互用。任何上游缓存或参考仓库中的凭据不得复制。

worker 的统一输入／输出和事件由 `engine/easy_publish/providers/base.py` 定义。每个任务独立进程、总时限、只点击／提交一次；提交之前先发送 submitting 事件，父进程 SQLite 提交完成后回传 ACK，worker 收到确认才允许发生平台写入。无法判断提交结果时返回 unknown，不自动重试。

本轮开发验证使用隔离的接口／页面 fixture，不向真实平台发布文章或视频。真实账号授权与发布由软件用户在应用中操作；缺乏实际平台回执时如实记录验证范围。

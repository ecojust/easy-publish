# MCP 接入与发布流程

Easy Publish 内置标准 **stdio MCP server**，供其他 agent 使用本机已经连接的平台账号。它复用桌面应用的内容库、素材库、预检、发布队列及回执，不运行第二套发布引擎。

## 配置客户端

1. 打开 Easy Publish，在“账号管理”中连接需要使用的平台账号。
2. 打开“设置 → MCP”，复制本机生成的完整配置。
3. 将配置添加到支持 stdio MCP 的客户端，重新加载该 MCP server。使用期间保持 Easy Publish 打开，并让客户端与应用使用同一系统用户运行。

配置结构为 `mcpServers.easy-publish`，其中 `command` 指向应用内置引擎，`args` 包含 `--mcp`、`--data-dir` 和本机数据目录。不同客户端若采用 TOML 或图形化配置，请保留同样的 command、args，以及开发构建可能提供的 env。路径包含空格时仍应保留为完整数组参数，不拼接为 shell 命令。

macOS ARM、Intel 和 Windows 安装包都提供该入口，普通用户不需要安装 Python。配置不含 Cookie、AppSecret 或连接 token。客户端显示“请先打开 Easy Publish”时，先启动应用，再重试工具调用。

## 可用工具

| 工具 | 用途 |
| --- | --- |
| `list_platforms` | 查看文章／视频平台能力、必填选项及文章模板 |
| `list_accounts` | 按内容类型列出账号，默认只列已连接账号 |
| `list_contents` / `get_content` | 查找稿件及各平台历史回执 |
| `save_content` | 保存或更新本地稿件，不发布 |
| `import_asset` | 从用户授权的绝对路径导入文章、视频或图片 |
| `preview_article` | 按公众号账号模板或指定模板／平台预览安全 HTML，不发布 |
| `preflight` | 检查账号、内容、素材及平台要求 |
| `publish` | 创建发布批次；会上传到真实平台 |
| `list_batches` / `get_batch` | 查询任务和平台回执，支持历史批次 |
| `cancel_batch` | 取消未完成工作，不能撤回已产生的平台内容 |

`list_platforms` 的 `publishMethod` 区分 `browser` 与 `api`。网页发布平台（知乎、大鱼号、雪球、抖音、快手、小红书、视频号）仅自动上传填写，最终发布及确认按钮必须由用户在已打开的应用发布浏览器中点击。此时任务为非终态 `awaiting_user`；Agent 应提示用户操作并继续查询原批次，不能自动代点或重新创建任务。等待期间保持 Easy Publish 打开。微信公众号与 B 站仍通过接口提交，B 站登录使用浏览器不改变其投稿方式。

文章只允许选择文章账号，视频只允许选择视频账号。显式混选会被后端拒绝。省略 `accountIds` 时，使用开启“发布时默认选中”（`defaultSelected: true`）、已连接、支持当前内容类型和操作的账号，并排除该稿件曾有 `submitted`、`published` 或 `unknown` 结果的平台。显式指定账号可覆盖默认偏好；全部兼容账号都关闭时不会回退到全选。同一 `requestId` 重试仍使用原目标，不受后续开关变化影响。

## 文章示例

先调用 `list_platforms` 和 `list_accounts`，确认平台要求。公众号仍需要封面及相应接口权限；大鱼号、雪球仍需平台选项，MCP 不跳过这些检查。

保存本地文章的 `save_content` 参数：

```json
{
  "content": {
    "kind": "article",
    "title": "一篇文章的标题",
    "inputFormat": "markdown",
    "body": "## 开始\n\n这里是 Markdown 正文。"
  }
}
```

文章 `inputFormat` 只接受 `markdown`（省略时使用该值），agent 写好的正文直接保存并通过 Wenyan CLI 预览。模板包括八款 `wenyan-*` 官方主题和六款兼容主题；完整 ID 由 `list_platforms` 返回，说明见 [文章模板](article-templates.md)。公众号实际发布使用该账号在账号管理中设置的模板；稿件的 `articleTemplateId` 保留非公众号与旧数据兼容。返回的 `content.id` 用于后续操作。正文中的图片先使用 `import_asset` 导入，再通过 `![说明](easy-asset://素材ID)` 引用；封面使用返回的图片 `asset.id` 填入 `coverAssetId`。

公众号预览在 `preview_article` 中传入 `body` 和 `accountId`，即按该账号当前模板生成。`accountId` 仅允许公众号账号；如果同时传 `platformId`，必须是 `wechat`。省略账号时仍可按 `articleTemplateId` 和 `platformId` 预览。创建发布批次时冻结每个公众号的模板，后续调整账号模板不会改变队列内任务。

调用 `preview_article` 可查看目标平台的 HTML 和样式说明；随后用 `preflight` 检查 `contentId` 及所选 `accountIds`。只有用户请求发布时才调用 `publish`：

```json
{
  "contentId": "保存返回的稿件ID",
  "accountIds": ["已连接的文章账号ID"],
  "mode": "publish",
  "requestId": "6d0eb943-76ac-43c7-99ae-8fa011c66f8e"
}
```

示例 UUID 仅用于展示格式。每次新的发布意图生成新的 UUID；网络中断后重试**同一次**工具调用时复用原 UUID。这样返回原批次，不会重复创建投稿。随后使用 `get_batch` 查询进度，不通过重复调用 `publish` 轮询。

## 视频与再次发布

视频先通过 `import_asset` 导入，使用 `kind: "video"`、`videoAssetId`、标题、描述、标签及可选封面保存。B 站还需要分区和原创／转载信息，用户可在账号管理保存；`list_accounts` 返回 `videoOptions` 及当前账号可用分区。推荐设置 `videoOptionsSource: "account"`，逐账号采用保存的参数，支持同平台账号不同分区。

为了兼容已有 agent，省略 `videoOptionsSource` 时按 `"content"` 处理：该平台有非空 `platformOptions` 时使用这份稿件参数，否则回退到账号设置；两份参数不会混合。`"account"` 模式始终只用账号设置，即使稿件保留旧字段。桌面视频页固定使用账号模式。新批次冻结每个账号实际使用的参数，旧批次重试保持原稿件参数。同一视频可以同时填写多个平台的字段，预检与执行均只使用目标需要的字段；例如抖音忽略自定义封面，B 站原创忽略转载来源。原稿保留，真正必填的字段仍校验。快手按页面要求完成作者声明；登录验证或平台操作仍可能需要用户在发布浏览器中处理。

稿件可以再次发布。自动选择会跳过有历史回执的平台；明确指定兼容账号并使用新 UUID，可创建新的发布批次。编辑原稿不会改变已创建批次的内容快照。`draft_created` 只是平台草稿，`submitted` 是已受理／待审核，`published` 是取得公开发布确认；`unknown` 必须先到平台核对，agent 不应自动重发。

## 本地连接与验证范围

stdio 服务通过当前用户的私有发现文件连接桌面应用的 loopback API。MCP 使用独立 token，仅可调用专用工具路由；不能用它访问普通桌面 API 或读取账号凭据。POSIX 文件权限和 Windows 当前用户 DPAPI 保护会话，正常关闭应用后移除发现文件。

支持 MCP 协议版本 `2025-06-18`、`2025-03-26` 和 `2024-11-05`。工具错误以 `isError` 返回安全说明；输入参数遵循 `tools/list` 提供的 `inputSchema`，结果通过 `content` 与 `structuredContent` 返回。测试使用本地引擎、stdio、HTTP／浏览器替身；这些测试不代表真实平台账号发布验收。

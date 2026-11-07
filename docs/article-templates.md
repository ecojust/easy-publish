# 文章模板与公众号排版

在“账号管理”中为每个微信公众号选择模板，默认清爽蓝；每次发布冻结各账号当时的选择。文章页上方输入原文及标题、作者、封面等信息，通过右上角“预览”弹窗查看各目标的最终排版。切换账号模板只重新排版，不再次调用模型。旧Markdown稿件和旧批次继续使用兼容规则。

新文章通过内置OpenCode免费模型整理为Markdown，原文单独保留。只有点击“生成”才发送原文；内容更新后需重新整理，通过右上角“预览”弹窗核对各账号效果后再发布。模板不会自动添加章节编号、署名或推广文字。

| 模板 | ID | 版式方向 |
| --- | --- | --- |
| 清爽蓝 | `clean` | 蓝色标题带、清晰的层级和留白，适合知识分享 |
| 墨色杂志 | `editorial` | 衬线标题、细线分隔与纸张底色，适合观点和长文 |
| 暖色手记 | `journal` | 暖色标题卡片、柔和引用框，适合生活与故事 |
| 理性科技 | `tech` | 青绿标题与深色代码区，适合技术和产品说明 |
| 森林来信 | `forest` | 绿色边框与圆角卡片，适合自然、旅行和生活方式 |
| 黑金画报 | `artdeco` | 黑金色块、醒目标题和几何边框，适合专题和品牌内容 |

模板分别处理标题、段落、强调、引用、列表、行内代码、代码块、表格、图片和分隔线。表格行背景在渲染时写入单元格，不依赖平台额外加载样式表。代码保持原始字符、空格和换行，样式只改变展示。

图片仍通过本地素材库导入。公众号排版需要在图片下显示说明时，使用 Markdown 的图片标题：

```markdown
![替代说明](easy-asset://素材ID "雨后山谷，摄于清晨")
```

只有显式填写的图片标题作为图注，不把文件名或替代文字自动复制为图注。封面仍是独立字段。

模板卡片使用同一个服务端渲染器生成固定示例的缩略图；工作台预览当前稿件，微信公众号提交使用同一渲染规则，再将本地图片地址替换为微信素材地址。缩略图示例不会写入稿件。微信公众号可能调整个别字体、间距和链接显示，实际效果以平台预览为准。

知乎、雪球继续使用平台原生排版；大鱼号按编辑器能力转换。不支持的表格或代码结构转为可读内容，正文及素材顺序保持。模板装饰不会强行带入不支持这些样式的平台。

## oDesk 参考来源

参考 [ecojust/oDesk 固定提交 d062c05](https://github.com/ecojust/oDesk/tree/d062c05b3442f45205ff83cc28497d435fccce99) 的 [WechatPublisherTemplate.js](https://github.com/ecojust/oDesk/blob/d062c05b3442f45205ff83cc28497d435fccce99/src/views/skillapps/WechatPublisherTemplate.js)、[WechatPublisher.vue](https://github.com/ecojust/oDesk/blob/d062c05b3442f45205ff83cc28497d435fccce99/src/views/skillapps/WechatPublisher.vue)，及 [wechat-publisher.zip](https://github.com/ecojust/oDesk/blob/d062c05b3442f45205ff83cc28497d435fccce99/src-tauri/addin/skills/wechat-publisher.zip) 中的以下 CSS。上游用 Wenyan CLI 和主题 CSS 排版；本项目独立实现受控的内联 HTML／CSS，无需额外安装该 CLI。

| ZIP 内路径 | SHA-256 |
| --- | --- |
| `scripts/editorial.css` | `60f147ec500209f8feb54a35138d568ad2e93844104da2abf2f0d151d9e680dd` |
| `scripts/paper.css` | `d3a264d29eae3f8ca871cd5a56b16804d7153a1fdd24a094881abb01192a50df` |
| `scripts/forest.css` | `8116de799582849d114455affa838aa9e4d431718d6fd425cb8805a37f97cf56` |
| `scripts/artdeco.css` | `ef5f6f2a6aa99fe6bdaf352768b1d784be1df6193415dce922c4be61650e517a` |
| `scripts/blueprint.css` | `10eb491cf92745f667b971e0f942a3a09e9596fc6564fb1001e619b862c4fb5c` |

保留版式方向，以直接内联的颜色、边框、间距与真实 HTML 元素实现；不依赖 CSS 变量、伪元素、外部字体、渐变背景或动态脚本。不加载上游的账号缓存或执行上游技能。许可见 [第三方来源与许可](../THIRD_PARTY_NOTICES.md)。

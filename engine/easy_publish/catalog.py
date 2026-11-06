"""Only expose capabilities implemented by the selected adapters."""


def select_field(key, label, values, required=True):
    return {"key": key, "label": label, "type": "select", "required": required,
            "kinds": ["article"], "choices": [{"value": v, "label": v} for v in values]}


def platform(id, name, color, kind, modes, title_limit, description, url, fields=None):
    return {"id": id, "name": name, "color": color, "kinds": [kind], "modes": modes,
            "auth": "wechat_api" if id == "wechat" else "browser", "titleLimit": title_limit,
            "description": description, "homeUrl": url, "fields": fields or []}


PLATFORMS = [
    platform("wechat", "微信公众号", "#16a34a", "article", ["draft", "publish"], 64,
             "使用公众号 AppID / AppSecret；需具备素材、草稿及发布接口权限", "https://mp.weixin.qq.com/"),
    platform("zhihu", "知乎", "#2563eb", "article", ["publish"], 100,
             "浏览器登录，发布知乎文章", "https://www.zhihu.com/creator"),
    platform("dayu", "大鱼号", "#ef4444", "article", ["publish"], 50,
             "浏览器登录；文章需填写信息来源", "https://mp.dayu.com/", [
                 select_field("source", "信息来源", ["无需标注", "AI生成", "虚构演绎", "营销信息", "转载", "个人观点", "不适宜未成年人"]),
                 {"key": "source_url", "label": "转载原文链接", "type": "text", "required": False,
                  "kinds": ["article"], "hint": "信息来源选择转载时必填；其他来源请留空"}]),
    platform("xueqiu", "雪球", "#0284c7", "article", ["draft", "publish"], 100,
             "浏览器登录；文章需填写创作声明", "https://xueqiu.com/", [
                 select_field("declaration", "创作声明", ["不添加创作声明", "本文包含AI生成内容"]),
                 select_field("visibility", "分享范围", ["公开", "私人"], False)]),
    platform("douyin", "抖音", "#18181b", "video", ["publish"], 30,
             "浏览器登录；使用平台自动封面，暂不支持指定封面", "https://creator.douyin.com/"),
    platform("xiaohongshu", "小红书", "#f43f5e", "video", ["publish"], 20,
             "浏览器登录；标题最多 20 字，话题最多 10 个", "https://creator.xiaohongshu.com/"),
    platform("channels", "微信视频号", "#f59e0b", "video", ["draft", "publish"], 15,
             "微信扫码登录；短标题需为 7–15 字", "https://channels.weixin.qq.com/"),
]
BY_ID = {p["id"]: p for p in PLATFORMS}

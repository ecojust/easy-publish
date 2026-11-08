"""Only expose capabilities implemented by the selected adapters."""

import re


def bilibili_categories(value):
    """Allow only bounded public category labels across the worker boundary."""
    if not isinstance(value, list) or len(value) > 1000:
        return []
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict):
            continue
        identifier, label = item.get("value"), item.get("label")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[1-9][0-9]{0,4}", identifier)
                or int(identifier) > 65535 or not isinstance(label, str)
                or not 1 <= len(label.strip()) <= 160 or re.search(r"[\x00-\x1f\x7f]", label)):
            continue
        if identifier not in seen:
            result.append({"value": identifier, "label": label.strip()})
            seen.add(identifier)
    return result


def select_field(key, label, values, required=True):
    return {"key": key, "label": label, "type": "select", "required": required,
            "kinds": ["article"], "choices": [{"value": v, "label": v} for v in values]}


def platform(id, name, color, kind, modes, title_limit, description, url, fields=None):
    return {"id": id, "name": name, "color": color, "kinds": [kind], "modes": modes,
            "publishMethod": "api" if id in {"wechat", "bilibili"} else "browser",
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
                  "kinds": ["article"], "hint": "信息来源选择转载时必填；其他来源会忽略此字段"}]),
    platform("xueqiu", "雪球", "#0284c7", "article", ["draft", "publish"], 100,
             "浏览器登录；文章需填写创作声明", "https://xueqiu.com/", [
                 select_field("declaration", "创作声明", ["不添加创作声明", "本文包含AI生成内容"]),
                 select_field("visibility", "分享范围", ["公开", "私人"], False)]),
    platform("douyin", "抖音", "#18181b", "video", ["publish"], 30,
             "浏览器登录；使用平台自动封面，会忽略稿件中的自定义封面", "https://creator.douyin.com/"),
    platform("kuaishou", "快手", "#ff5000", "video", ["publish"], 200,
             "浏览器登录；标题、描述与话题合计最多 500 字符，可指定封面", "https://cp.kuaishou.com/"),
    platform("xiaohongshu", "小红书", "#f43f5e", "video", ["publish"], 20,
             "浏览器登录；标题最多 20 字，话题最多 10 个", "https://creator.xiaohongshu.com/"),
    platform("channels", "微信视频号", "#f59e0b", "video", ["draft", "publish"], 15,
             "微信扫码登录；短标题需为 7–15 字", "https://channels.weixin.qq.com/"),
    platform("bilibili", "哔哩哔哩", "#00a1d6", "video", ["publish"], 80,
             "浏览器登录；选择视频分区及原创／转载，投稿后等待平台审核", "https://member.bilibili.com/", [
                 {"key": "copyright", "label": "创作类型", "type": "select", "required": True,
                  "kinds": ["video"], "choices": [{"value": "1", "label": "原创"}, {"value": "2", "label": "转载"}]},
                 {"key": "tid", "label": "视频分区", "type": "select", "required": True,
                  "kinds": ["video"], "dynamicChoices": "bilibiliCategories",
                  "hint": "连接或验证账号后获取可用分区；每个账号分别设置自己的分区"},
                 {"key": "source", "label": "转载来源", "type": "text", "required": False,
                  "kinds": ["video"], "hint": "转载必填，填写原始视频链接；原创会忽略此字段"}]),
]
BY_ID = {p["id"]: p for p in PLATFORMS}

use serde::Serialize;
use url::Url;

#[derive(Debug, Serialize)]
pub struct PublicError {
    pub error: String,
    pub code: String,
}
impl PublicError {
    pub fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.into(),
            error: message.into(),
        }
    }
}

fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

/// Keep the renderer away from shutdown and any future internal endpoints.
pub fn allowed_route(method: &str, path: &str) -> bool {
    let segments: Vec<_> = path.split('/').collect();
    match (method, segments.as_slice()) {
        ("GET", ["", "api", "state"]) => true,
        ("POST", ["", "api", "contents" | "accounts" | "preflight" | "batches"]) => true,
        ("POST", ["", "api", "assets", "import"]) => true,
        ("POST", ["", "api", "preview", "article"]) => true,
        ("POST", ["", "api", "runtime", "browser", "install"]) => true,
        ("PUT" | "DELETE", ["", "api", "contents", id]) => identifier(id),
        ("DELETE", ["", "api", "accounts", id]) => identifier(id),
        ("PUT", ["", "api", "accounts", id, "preferences"]) => identifier(id),
        ("GET", ["", "api", "assets", id] | ["", "api", "assets", id, "preview"]) => identifier(id),
        ("POST", ["", "api", "accounts", id, "login" | "check" | "cancel"]) => identifier(id),
        ("POST", ["", "api", "batches", id, "cancel"]) => identifier(id),
        ("POST", ["", "api", "tasks", id, "retry"]) => identifier(id),
        _ => false,
    }
}

/// Only platform HTTPS links can leave the local application.
pub fn allowed_external_url(value: &str) -> bool {
    let Ok(url) = Url::parse(value) else {
        return false;
    };
    if url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some_and(|port| port != 443)
    {
        return false;
    }
    let Some(host) = url.host_str() else {
        return false;
    };
    [
        "weixin.qq.com",
        "channels.weixin.qq.com",
        "douyin.com",
        "kuaishou.com",
        "xiaohongshu.com",
        "bilibili.com",
        "zhihu.com",
        "dayu.com",
        "uc.cn",
        "xueqiu.com",
    ]
    .iter()
    .any(|domain| host == *domain || host.ends_with(&format!(".{domain}")))
}

pub fn allowed_navigation(url: &Url) -> bool {
    let local = (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))
        || (url.scheme() == "https" && url.host_str() == Some("tauri.localhost"));
    if local && url.port().is_none() && url.username().is_empty() && url.password().is_none() {
        return true;
    }
    cfg!(debug_assertions)
        && url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port() == Some(1420)
        && url.username().is_empty()
        && url.password().is_none()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn known_routes_only() {
        for (method, path) in [
            ("GET", "/api/state"),
            ("PUT", "/api/contents/id-1"),
            ("POST", "/api/accounts/abc/login"),
            ("POST", "/api/accounts/abc/cancel"),
            ("PUT", "/api/accounts/abc/preferences"),
            ("POST", "/api/preview/article"),
            ("POST", "/api/tasks/id_1/retry"),
        ] {
            assert!(allowed_route(method, path));
        }
        for (method, path) in [
            ("GET", "http://example.com/api/state"),
            ("POST", "/api/shutdown"),
            ("POST", "/api/mcp/call"),
            ("GET", "/api/state?x=1"),
            ("DELETE", "/api/accounts/../state"),
            ("POST", "/api/tasks/%2e%2e/retry"),
            ("PATCH", "/api/contents/id"),
            ("POST", "/api/accounts//login"),
            ("GET", "/api/accounts/abc/cancel"),
            ("DELETE", "/api/accounts/abc/cancel"),
            ("POST", "/api/accounts//cancel"),
            ("POST", "/api/accounts/%2e%2e/cancel"),
            ("POST", "/api/accounts/abc/preferences"),
            ("PUT", "/api/accounts//preferences"),
            ("PUT", "/api/accounts/%2e%2e/preferences"),
        ] {
            assert!(!allowed_route(method, path));
        }
    }
    #[test]
    fn links_cannot_launch_programs_or_lookalike_domains() {
        assert!(allowed_external_url("https://mp.weixin.qq.com/s/abc"));
        assert!(allowed_external_url("https://www.bilibili.com/video/BV123"));
        assert!(allowed_external_url(
            "https://cp.kuaishou.com/article/manage/video"
        ));
        for url in [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://douyin.com",
            "https://douyin.com.evil.com/",
            "https://douyin.com@evil.com/",
            "https://user@douyin.com",
            "https://douyin.com:444/",
            "https://cp.kuaishou.com.evil.com/",
            "https://cp.kuaishou.com@evil.com/",
        ] {
            assert!(!allowed_external_url(url));
        }
    }
    #[test]
    fn navigation_stays_in_the_local_app() {
        assert!(allowed_navigation(
            &Url::parse("tauri://localhost/index.html").unwrap()
        ));
        assert!(!allowed_navigation(
            &Url::parse("https://mp.weixin.qq.com/").unwrap()
        ));
        assert!(!allowed_navigation(
            &Url::parse("https://tauri.localhost.evil.com/").unwrap()
        ));
    }
}

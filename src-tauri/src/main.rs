#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use easy_publish_desktop::{
    engine::{Engine, McpConfiguration},
    policy::{allowed_external_url, allowed_navigation, PublicError},
};
use serde_json::Value;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use tauri::{Manager, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
async fn api_request(
    engine: State<'_, Arc<Engine>>,
    method: String,
    path: String,
    body: Option<Value>,
) -> Result<Value, PublicError> {
    engine.request(&method, &path, body).await
}

#[tauri::command]
fn mcp_configuration(app: tauri::AppHandle) -> Result<McpConfiguration, PublicError> {
    let data_dir = app.path().app_data_dir().map_err(|_| {
        PublicError::new("data_directory", "无法定位应用数据目录，请重新启动应用。")
    })?;
    easy_publish_desktop::engine::mcp_configuration(&data_dir)
}

#[tauri::command]
async fn pick_file(
    app: tauri::AppHandle,
    engine: State<'_, Arc<Engine>>,
    kind: String,
) -> Result<Option<String>, PublicError> {
    let extensions: &[&str] = match kind.as_str() {
        "video" => &["mp4", "mov", "m4v", "webm"],
        "image" => &["png", "jpg", "jpeg"],
        "article" => &["md", "markdown", "txt"],
        _ => return Err(PublicError::new("file_kind", "不支持此文件类型。")),
    };
    let dialog = app.dialog().file().add_filter("内容文件", extensions);
    let file = tauri::async_runtime::spawn_blocking(move || dialog.blocking_pick_file())
        .await
        .map_err(|_| PublicError::new("file_dialog", "无法打开文件选择窗口。"))?;
    let Some(file) = file else { return Ok(None) };
    let path = file
        .into_path()
        .map_err(|_| PublicError::new("file_path", "请选择本机文件。"))?;
    engine.remember_file(&path)?;
    path.to_str()
        .map(|s| Some(s.to_owned()))
        .ok_or_else(|| PublicError::new("file_path", "文件路径包含不支持的字符。"))
}

#[tauri::command]
async fn open_external(url: String) -> Result<(), PublicError> {
    if !allowed_external_url(&url) {
        return Err(PublicError::new(
            "link_denied",
            "只允许打开支持平台的 HTTPS 链接。",
        ));
    }
    tauri::async_runtime::spawn_blocking(move || open::that(url))
        .await
        .map_err(|_| PublicError::new("link_open", "无法打开浏览器。"))?
        .map_err(|_| PublicError::new("link_open", "无法打开浏览器。"))
}

fn smoke_test() -> bool {
    let data_dir =
        std::env::temp_dir().join(format!("easy-publish-native-smoke-{}", std::process::id()));
    let result = (|| {
        let engine = Engine::start(&data_dir)?;
        let runtime = tokio::runtime::Runtime::new()
            .map_err(|_| PublicError::new("runtime", "Cannot initialize runtime"))?;
        runtime.block_on(async {
            let result = engine.request("GET", "/api/state", None).await;
            engine.shutdown().await;
            let state = result?;
            if state
                .get("runtime")
                .and_then(|r| r.get("version"))
                .and_then(Value::as_str)
                != Some(env!("CARGO_PKG_VERSION"))
            {
                return Err(PublicError::new(
                    "smoke_state",
                    "Packaged engine version does not match desktop",
                ));
            }
            for key in ["accounts", "contents", "platforms", "batches"] {
                if !state.get(key).is_some_and(Value::is_array) {
                    return Err(PublicError::new(
                        "smoke_state",
                        "Packaged engine returned an incomplete state",
                    ));
                }
            }
            Ok(())
        })
    })();
    let _ = std::fs::remove_dir_all(data_dir);
    if let Err(error) = result {
        eprintln!("Native smoke failed: {}", error.code);
        return false;
    }
    println!("Native packaged engine smoke passed");
    true
}

fn main() {
    if std::env::args().any(|arg| arg == "--smoke-test") {
        std::process::exit(if smoke_test() { 0 } else { 1 });
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            api_request,
            mcp_configuration,
            pick_file,
            open_external
        ])
        .setup(|app| {
            let data_dir = app.path().app_data_dir()?;
            let engine = match Engine::start(&data_dir) {
                Ok(engine) => Arc::new(engine),
                Err(error) => {
                    let handle = app.handle().clone();
                    app.dialog()
                        .message(error.error)
                        .title("Easy Publish 启动失败")
                        .show(move |_| handle.exit(1));
                    return Ok(());
                }
            };
            app.manage(engine);
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Easy Publish")
                .inner_size(1280.0, 840.0)
                .min_inner_size(960.0, 640.0)
                .on_navigation(allowed_navigation)
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Unable to initialize Easy Publish desktop host");
    let closing = Arc::new(AtomicBool::new(false));
    app.run(move |app, event| match event {
        tauri::RunEvent::ExitRequested { api, .. } => {
            if !closing.swap(true, Ordering::AcqRel) {
                let Some(engine) = app
                    .try_state::<Arc<Engine>>()
                    .map(|state| state.inner().clone())
                else {
                    return;
                };
                api.prevent_exit();
                let app = app.clone();
                tauri::async_runtime::spawn(async move {
                    engine.shutdown().await;
                    app.exit(0);
                });
            }
        }
        tauri::RunEvent::Exit => {
            if let Some(engine) = app.try_state::<Arc<Engine>>() {
                engine.kill();
            }
        }
        _ => {}
    });
}

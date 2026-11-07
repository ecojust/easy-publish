use crate::policy::PublicError;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashSet},
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{mpsc, Mutex},
    time::Duration,
};

const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;
const MAX_REQUEST_BYTES: usize = 4 * 1024 * 1024;

#[derive(Deserialize)]
struct Ready {
    event: String,
    port: u16,
    token: String,
}

pub struct Engine {
    child: Mutex<Child>,
    port: u16,
    token: String,
    client: reqwest::Client,
    chosen_files: Mutex<HashSet<PathBuf>>,
}

#[derive(Debug, Serialize)]
pub struct McpConfiguration {
    pub command: String,
    pub args: Vec<String>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub env: BTreeMap<String, String>,
}

fn bundled_engine_path(executable: &Path, windows: bool) -> Result<PathBuf, PublicError> {
    if !executable.is_absolute() {
        return Err(PublicError::new("engine_missing", "无法定位内置发布引擎。"));
    }
    let parent = executable
        .parent()
        .ok_or_else(|| PublicError::new("engine_missing", "无法定位内置发布引擎。"))?;
    Ok(parent.join(if windows {
        "easy-publish-engine.exe"
    } else {
        "easy-publish-engine"
    }))
}

fn base_command() -> Result<Command, PublicError> {
    if cfg!(debug_assertions) {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let python = if cfg!(windows) {
            root.join(".venv/Scripts/python.exe")
        } else {
            root.join(".venv/bin/python")
        };
        if !python.is_file() {
            return Err(PublicError::new(
                "engine_missing",
                "开发引擎未准备，请先创建项目 .venv 并安装 engine/requirements.txt。",
            ));
        }
        let mut command = Command::new(python);
        command.args(["-m", "easy_publish"]);
        command.env("PYTHONPATH", root.join("engine"));
        Ok(command)
    } else {
        let executable = std::env::current_exe()
            .map_err(|_| PublicError::new("engine_missing", "无法定位内置发布引擎。"))?;
        let path = bundled_engine_path(&executable, cfg!(windows))?;
        if !path.is_file() {
            return Err(PublicError::new(
                "engine_missing",
                "安装包缺少发布引擎，请重新安装完整版本。",
            ));
        }
        Ok(Command::new(path))
    }
}

fn mcp_config_for(mut command: Command, data_dir: &Path) -> Result<McpConfiguration, PublicError> {
    if !Path::new(command.get_program()).is_absolute() || !data_dir.is_absolute() {
        return Err(PublicError::new(
            "mcp_configuration",
            "MCP 配置需要完整的本机路径。",
        ));
    }
    command.arg("--mcp").arg("--data-dir").arg(data_dir);
    let as_text = |value: &std::ffi::OsStr| {
        value.to_str().map(str::to_owned).ok_or_else(|| {
            PublicError::new(
                "mcp_configuration",
                "本机路径包含不支持的字符，无法生成 MCP 配置。",
            )
        })
    };
    let mut env = BTreeMap::new();
    // Only the development module lookup path is part of client configuration.
    // Never copy inherited environment, engine tokens or connection descriptors.
    for (key, value) in command.get_envs() {
        if key == "PYTHONPATH" {
            if let Some(value) = value {
                env.insert("PYTHONPATH".to_owned(), as_text(value)?);
            }
        }
    }
    Ok(McpConfiguration {
        command: as_text(command.get_program())?,
        args: command.get_args().map(as_text).collect::<Result<_, _>>()?,
        env,
    })
}

pub fn mcp_configuration(data_dir: &Path) -> Result<McpConfiguration, PublicError> {
    mcp_config_for(base_command()?, data_dir)
}

fn command(data_dir: &Path) -> Result<Command, PublicError> {
    let mut command = base_command()?;
    command
        .arg("--data-dir")
        .arg(data_dir)
        .arg("--parent-pid")
        .arg(std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // Never allow development keys to replace the application's persistent vault key in a release.
    if !cfg!(debug_assertions) {
        command
            .env_remove("EASY_PUBLISH_TEST_KEY")
            .env_remove("EASY_PUBLISH_TESTING");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW; browser login remains visible.
    }
    Ok(command)
}

impl Engine {
    pub fn start(data_dir: &Path) -> Result<Self, PublicError> {
        std::fs::create_dir_all(data_dir)
            .map_err(|_| PublicError::new("data_directory", "无法创建应用数据目录。"))?;
        let mut child = command(data_dir)?.spawn().map_err(|error| {
            crate::startup::failure(data_dir, "创建引擎进程", &error.to_string(), "")
        })?;
        let diagnostics = crate::startup::Capture::attach(child.stderr.take().unwrap());
        let stdout = child.stdout.take().unwrap();
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            // Read only one bounded handshake, then drain without logging any process output.
            let mut reader = BufReader::new(stdout);
            let mut line = String::new();
            let result = reader
                .by_ref()
                .take(4097)
                .read_line(&mut line)
                .ok()
                .filter(|_| line.len() <= 4096)
                .and_then(|_| serde_json::from_str::<Ready>(&line).ok());
            let _ = sender.send(result);
            let _ = std::io::copy(&mut reader, &mut std::io::sink());
        });
        let ready = receiver
            .recv_timeout(Duration::from_secs(90))
            .ok()
            .flatten()
            .filter(|r| {
                r.event == "ready"
                    && r.port != 0
                    && r.token.len() >= 32
                    && r.token.len() <= 256
                    && r.token
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
            });
        let Some(ready) = ready else {
            // stdout can close just before the process exit becomes observable.
            let mut exited = child.try_wait().ok().flatten();
            for _ in 0..25 {
                if exited.is_some() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(10));
                exited = child.try_wait().ok().flatten();
            }
            let status = exited
                .map(|status| status.to_string())
                .unwrap_or_else(|| "未收到有效启动握手，已停止进程".to_owned());
            let _ = child.kill();
            let _ = child.wait();
            let stderr = diagnostics.finish(true);
            return Err(crate::startup::failure(
                data_dir,
                "启动握手",
                &status,
                &stderr,
            ));
        };
        // Runtime stderr may include platform data; retain nothing after readiness.
        let _ = diagnostics.finish(false);
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(30))
            .build();
        let client = match client {
            Ok(value) => value,
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(PublicError::new(
                    "engine_start",
                    "无法建立本机发布服务连接。",
                ));
            }
        };
        Ok(Self {
            child: Mutex::new(child),
            port: ready.port,
            token: ready.token,
            client,
            chosen_files: Mutex::new(HashSet::new()),
        })
    }

    pub fn remember_file(&self, path: &Path) -> Result<(), PublicError> {
        let path = path
            .canonicalize()
            .map_err(|_| PublicError::new("file_unavailable", "所选文件已不可用。"))?;
        self.chosen_files
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(path);
        Ok(())
    }

    pub async fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<Value>,
    ) -> Result<Value, PublicError> {
        if !crate::policy::allowed_route(method, path) {
            return Err(PublicError::new("request_denied", "该本机接口不可用。"));
        }
        if path == "/api/assets/import" {
            let selected = body
                .as_ref()
                .and_then(|b| b.get("path"))
                .and_then(Value::as_str)
                .and_then(|value| Path::new(value).canonicalize().ok());
            if !selected.is_some_and(|p| {
                self.chosen_files
                    .lock()
                    .unwrap_or_else(|p| p.into_inner())
                    .contains(&p)
            }) {
                return Err(PublicError::new(
                    "file_not_selected",
                    "请通过文件选择窗口选择要导入的文件。",
                ));
            }
        }
        self.send(method, path, body).await
    }

    async fn send(
        &self,
        method: &str,
        path: &str,
        body: Option<Value>,
    ) -> Result<Value, PublicError> {
        let method = reqwest::Method::from_bytes(method.as_bytes())
            .map_err(|_| PublicError::new("request_denied", "请求方式不可用。"))?;
        let mut request = self
            .client
            .request(method, format!("http://127.0.0.1:{}{}", self.port, path))
            .bearer_auth(&self.token);
        if let Some(body) = body {
            if serde_json::to_vec(&body).map_or(true, |value| value.len() > MAX_REQUEST_BYTES) {
                return Err(PublicError::new(
                    "request_too_large",
                    "稿件内容过大，请缩小后重试。",
                ));
            }
            request = request.json(&body);
        }
        let mut response = request.send().await.map_err(|_| {
            PublicError::new("engine_unavailable", "本机发布引擎暂时不可用，请稍后重试。")
        })?;
        let success = response.status().is_success();
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| PublicError::new("engine_response", "无法读取发布引擎的响应。"))?
        {
            if bytes.len() + chunk.len() > MAX_RESPONSE_BYTES {
                return Err(PublicError::new("response_too_large", "发布引擎响应过大。"));
            }
            bytes.extend_from_slice(&chunk);
        }
        let value: Value = serde_json::from_slice(&bytes)
            .map_err(|_| PublicError::new("engine_response", "发布引擎返回了无法读取的响应。"))?;
        if success {
            return Ok(value);
        }
        let message = value
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("操作未完成，请重试。")
            .replace(&self.token, "[已脱敏]");
        let code = value
            .get("code")
            .and_then(Value::as_str)
            .filter(|s| s.len() < 64 && s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_'))
            .unwrap_or("engine_error");
        Err(PublicError::new(
            code,
            &message.chars().take(1000).collect::<String>(),
        ))
    }

    pub async fn shutdown(&self) {
        let _ = tokio::time::timeout(
            Duration::from_secs(5),
            self.send("POST", "/api/shutdown", Some(json!({}))),
        )
        .await;
        for _ in 0..200 {
            if self
                .child
                .lock()
                .unwrap_or_else(|p| p.into_inner())
                .try_wait()
                .ok()
                .flatten()
                .is_some()
            {
                return;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        self.kill();
    }

    pub fn kill(&self) {
        let mut child = self.child.lock().unwrap_or_else(|p| p.into_inner());
        if child.try_wait().ok().flatten().is_none() {
            let _ = child.kill();
        }
        let _ = child.wait();
    }
}
impl Drop for Engine {
    fn drop(&mut self) {
        self.kill();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Write, net::TcpListener};

    #[test]
    fn locates_packaged_engine_beside_desktop_on_mac_and_windows() {
        let root = std::env::temp_dir().join("Easy Publish configuration fixtures");
        let mac_dir = root.join("Easy Publish.app/Contents/MacOS");
        assert_eq!(
            bundled_engine_path(&mac_dir.join("easy-publish"), false).unwrap(),
            mac_dir.join("easy-publish-engine")
        );
        let windows_dir = root.join("Program Files/Easy Publish");
        assert_eq!(
            bundled_engine_path(&windows_dir.join("easy-publish.exe"), true).unwrap(),
            windows_dir.join("easy-publish-engine.exe")
        );
        assert!(bundled_engine_path(Path::new("relative/easy-publish"), false).is_err());
    }

    #[test]
    fn packaged_mcp_configuration_preserves_paths_as_separate_arguments() {
        let root = std::env::temp_dir().join("Easy Publish 配置 fixtures");
        let executable = root.join("Easy Publish.app/Contents/MacOS/easy-publish-engine");
        let data_dir = root.join("Library/Application Support/easy-publish");
        let configuration = mcp_config_for(Command::new(&executable), &data_dir).unwrap();
        let value = serde_json::to_value(configuration).unwrap();
        assert_eq!(value["command"], executable.to_str().unwrap());
        assert_eq!(value["args"], json!(["--mcp", "--data-dir", data_dir]));
        assert!(value.get("env").is_none());
        assert_eq!(value.as_object().unwrap().len(), 2);
    }

    #[test]
    fn development_mcp_configuration_exports_only_module_lookup_environment() {
        let root = std::env::temp_dir().join("Easy Publish source fixtures");
        let python = root.join(".venv/bin/python");
        let modules = root.join("engine");
        let data_dir = root.join("Application Support/easy-publish");
        let mut command = Command::new(&python);
        command
            .args(["-m", "easy_publish"])
            .env("PYTHONPATH", &modules)
            .env("EASY_PUBLISH_TOKEN", "fixture-secret-not-for-clients")
            .env("EASY_PUBLISH_TEST_KEY", "fixture-key-not-for-clients");
        let value = serde_json::to_value(mcp_config_for(command, &data_dir).unwrap()).unwrap();
        assert_eq!(value["command"], python.to_str().unwrap());
        assert_eq!(
            value["args"],
            json!(["-m", "easy_publish", "--mcp", "--data-dir", data_dir])
        );
        assert_eq!(value["env"], json!({"PYTHONPATH": modules}));
        assert!(!value.to_string().contains("fixture-secret"));
        assert!(!value.to_string().contains("fixture-key"));
    }

    #[test]
    fn mcp_configuration_rejects_relative_program_and_data_paths() {
        let root = std::env::temp_dir();
        assert!(mcp_config_for(Command::new("python"), &root).is_err());
        assert!(mcp_config_for(Command::new(root.join("engine")), Path::new("data")).is_err());
    }

    fn test_engine(port: u16) -> Engine {
        let child = Command::new(std::env::current_exe().unwrap())
            .arg("--help")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        Engine {
            child: Mutex::new(child),
            port,
            token: "fixture-token-that-never-reaches-renderer".into(),
            client: reqwest::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap(),
            chosen_files: Mutex::new(HashSet::new()),
        }
    }

    fn server(status: &str, body: Value) -> (u16, std::thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let body = body.to_string();
        let reply = format!("HTTP/1.1 {status}\r\nContent-Type: application/json\r\nLocation: http://127.0.0.1:1/must-not-follow\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
        let thread = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut request = String::new();
            loop {
                let mut line = String::new();
                reader.read_line(&mut line).unwrap();
                if line == "\r\n" || line.is_empty() {
                    break;
                }
                request.push_str(&line);
            }
            stream.write_all(reply.as_bytes()).unwrap();
            request
        });
        (port, thread)
    }

    #[tokio::test]
    async fn authenticates_requests_and_keeps_token_out_of_errors() {
        let (port, thread) = server(
            "403 Forbidden",
            json!({"code":"denied", "error":"fixture-token-that-never-reaches-renderer denied"}),
        );
        let engine = test_engine(port);
        let error = engine.request("GET", "/api/state", None).await.unwrap_err();
        assert_eq!(error.code, "denied");
        assert!(!error.error.contains("fixture-token"));
        let request = thread.join().unwrap();
        assert!(request.starts_with("GET /api/state HTTP/1.1"));
        assert!(request
            .to_lowercase()
            .contains("authorization: bearer fixture-token-that-never-reaches-renderer"));
    }

    #[tokio::test]
    async fn rejects_shutdown_and_arbitrary_file_reads_before_network() {
        let engine = test_engine(1);
        assert_eq!(
            engine
                .request("POST", "/api/shutdown", None)
                .await
                .unwrap_err()
                .code,
            "request_denied"
        );
        assert_eq!(
            engine
                .request(
                    "POST",
                    "/api/assets/import",
                    Some(json!({"path":std::env::current_exe().unwrap(),"kind":"article"}))
                )
                .await
                .unwrap_err()
                .code,
            "file_not_selected"
        );
    }

    #[tokio::test]
    async fn passes_valid_json_and_blocks_http_redirects() {
        let (port, thread) = server("200 OK", json!({"accounts":[]}));
        assert_eq!(
            test_engine(port)
                .request("GET", "/api/state", None)
                .await
                .unwrap(),
            json!({"accounts":[]})
        );
        thread.join().unwrap();
        // An HTTP redirect must be returned as an engine error instead of followed.
        let (port, thread) = server(
            "302 Found",
            json!({"error":"redirect refused","code":"redirect"}),
        );
        assert_eq!(
            test_engine(port)
                .request("GET", "/api/state", None)
                .await
                .unwrap_err()
                .code,
            "redirect"
        );
        thread.join().unwrap();
    }
}

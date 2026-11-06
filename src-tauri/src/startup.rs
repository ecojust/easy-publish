//! Only startup stderr is retained; private stdout handshakes are never logged.
use crate::policy::PublicError;
use std::{
    io::{Read, Write},
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
    time::Duration,
};

const MAX_STDERR: usize = 16 * 1024;

pub(crate) struct Capture {
    active: Arc<AtomicBool>,
    bytes: Arc<Mutex<Vec<u8>>>,
    finished: mpsc::Receiver<()>,
}
impl Capture {
    pub(crate) fn attach(mut reader: impl Read + Send + 'static) -> Self {
        let active = Arc::new(AtomicBool::new(true));
        let bytes = Arc::new(Mutex::new(Vec::new()));
        let (done, finished) = mpsc::channel();
        let (collecting, captured) = (active.clone(), bytes.clone());
        std::thread::spawn(move || {
            let mut buffer = [0; 2048];
            while let Ok(count) = reader.read(&mut buffer) {
                if count == 0 {
                    break;
                }
                if !collecting.load(Ordering::Acquire) {
                    let _ = std::io::copy(&mut reader, &mut std::io::sink());
                    break;
                }
                let mut output = captured.lock().unwrap_or_else(|p| p.into_inner());
                let excess = (output.len() + count).saturating_sub(MAX_STDERR);
                if excess != 0 {
                    output.drain(..excess);
                }
                output.extend_from_slice(&buffer[..count]);
            }
            let _ = done.send(());
        });
        Self {
            active,
            bytes,
            finished,
        }
    }

    pub(crate) fn finish(self, await_eof: bool) -> String {
        if await_eof {
            let _ = self.finished.recv_timeout(Duration::from_millis(500));
        }
        self.active.store(false, Ordering::Release);
        let bytes = self.bytes.lock().unwrap_or_else(|p| p.into_inner());
        sanitize(&String::from_utf8_lossy(&bytes))
    }
}

fn sanitize_with_home(text: &str, home: Option<&str>) -> String {
    text.lines()
        .map(|line| {
            let lower = line.to_ascii_lowercase();
            if ["token", "secret", "password", "authorization", "credential"]
                .iter()
                .any(|key| lower.contains(key))
            {
                return "[已隐藏可能包含凭据的诊断行]".to_owned();
            }
            let line: String = line
                .chars()
                .filter(|c| !c.is_control() || *c == '\t')
                .collect();
            match home.filter(|value| !value.is_empty()) {
                Some(home) => line.replace(home, "~"),
                None => line,
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}
fn sanitize(text: &str) -> String {
    let mut sanitized = sanitize_with_home(text, None);
    for variable in ["HOME", "USERPROFILE"] {
        if let Ok(home) = std::env::var(variable) {
            if !home.is_empty() {
                sanitized = sanitized.replace(&home, "~");
            }
        }
    }
    sanitized
}

pub(crate) fn failure(data_dir: &Path, phase: &str, status: &str, stderr: &str) -> PublicError {
    let details = sanitize(&format!(
        "Easy Publish {}\nOS: {} / {}\n阶段: {phase}\n进程结果: {status}\n启动 stderr（最多16KiB）:\n{stderr}\n",
        env!("CARGO_PKG_VERSION"), std::env::consts::OS, std::env::consts::ARCH,
    ));
    let logs = data_dir.join("logs");
    let path = logs.join("engine-startup.log");
    let saved = (|| -> std::io::Result<()> {
        std::fs::create_dir_all(&logs)?;
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
            options.mode(0o600);
            std::fs::set_permissions(&logs, std::fs::Permissions::from_mode(0o700))?;
        }
        let mut file = options.open(&path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
        }
        file.write_all(details.as_bytes())
    })()
    .is_ok();
    let summary = stderr
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or(status);
    let summary: String = sanitize(summary).chars().take(360).collect();
    let location = if saved {
        format!("诊断日志：{}", sanitize(&path.to_string_lossy()))
    } else {
        "诊断日志未能写入，请检查应用数据目录的可用空间和权限。".to_owned()
    };
    PublicError::new(
        "engine_start",
        &format!("发布引擎启动失败（{status}）。\n{summary}\n\n{location}"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn diagnostics_remove_home_credentials_and_terminal_controls() {
        let text = "Library not loaded: /Users/test/Library/Python\nAuthorization: Bearer private-value\n{\"token\":\"private-ready\"}\npassword=hidden\nloader\u{1b}[31m failed";
        let clean = sanitize_with_home(text, Some("/Users/test"));
        assert!(clean.contains("~/Library/Python"));
        for private in [
            "/Users/test",
            "private-value",
            "private-ready",
            "hidden",
            "\u{1b}",
        ] {
            assert!(!clean.contains(private));
        }
    }
    #[test]
    fn capture_bounds_stderr_and_keeps_the_loader_failure_tail() {
        let mut input = vec![b'x'; MAX_STDERR * 3];
        input.extend_from_slice(b"\nFailed to load Python shared library: different Team IDs\n");
        let captured = Capture::attach(std::io::Cursor::new(input)).finish(true);
        assert!(captured.len() <= MAX_STDERR);
        assert!(captured.contains("different Team IDs"));
    }
    #[test]
    fn failure_writes_private_log_and_exposes_actionable_location() {
        let data =
            std::env::temp_dir().join(format!("easy-publish-startup-test-{}", std::process::id()));
        let error = failure(
            &data,
            "启动握手",
            "exit code: 1",
            "Failed to load Python shared library",
        );
        let path = data.join("logs/engine-startup.log");
        assert!(error.error.contains("exit code: 1"));
        assert!(error.error.contains("engine-startup.log"));
        assert!(std::fs::read_to_string(&path)
            .unwrap()
            .contains("Failed to load Python"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        std::fs::remove_dir_all(data).unwrap();
    }
}

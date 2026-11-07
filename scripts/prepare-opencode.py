#!/usr/bin/env python3
"""Bundle a pinned, verified standalone OpenCode CLI; never install a system tool."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import queue
import re
import secrets
import signal
import stat
import struct
import subprocess
import tarfile
import tempfile
import threading
import time
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts/opencode-runtime.json"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_BINARY_BYTES = 512 * 1024 * 1024


class PreparationError(RuntimeError):
    """Only locally authored errors; upstream failures can contain signed URLs."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PreparationError(message)


def native_target() -> str:
    machine = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(platform.machine().lower())
    system = {"Darwin": "apple-darwin", "Windows": "pc-windows-msvc", "Linux": "unknown-linux-gnu"}.get(platform.system())
    require(bool(machine and system), "Unsupported OpenCode build platform")
    return f"{machine}-{system}"


def pinned_target(target: str) -> tuple[dict, dict]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    require(manifest.get("repository") == "anomalyco/opencode" and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest.get("version", "")) is not None,
            "Invalid pinned OpenCode release")
    require(re.fullmatch(r"[0-9a-f]{40}", manifest.get("sourceCommit", "")) is not None, "Invalid pinned OpenCode source")
    item = manifest.get("targets", {}).get(target)
    require(isinstance(item, dict), "No pinned OpenCode binary for this target")
    require(item.get("executable") in {"opencode", "opencode.exe"}
            and re.fullmatch(r"opencode-[a-z0-9-]+\.(zip|tar\.gz)", item.get("archive", "")) is not None
            and re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")) is not None
            and type(item.get("bytes")) is int and 0 < item["bytes"] <= MAX_ARCHIVE_BYTES,
            "Invalid pinned OpenCode archive metadata")
    license_path = ROOT / "vendor/opencode/LICENSE"
    require(hashlib.sha256(license_path.read_bytes()).hexdigest() == manifest.get("licenseSha256"), "OpenCode license does not match the pinned source")
    return manifest, item


def download_archive(url: str, destination: Path, item: dict) -> None:
    digest, size = hashlib.sha256(), 0
    deadline = time.monotonic() + 300
    try:
        with urlopen(Request(url, headers={"User-Agent": "Easy-Publish-build"}), timeout=30) as response, destination.open("xb") as output:
            while data := response.read(1024 * 1024):
                size += len(data)
                require(size <= item["bytes"] and time.monotonic() < deadline, "OpenCode archive exceeded its size or download deadline")
                output.write(data)
                digest.update(data)
    except PreparationError:
        raise
    except Exception:
        raise PreparationError("Could not download the pinned OpenCode archive; no remote output was logged") from None
    require(size == item["bytes"] and digest.hexdigest() == item["sha256"], "OpenCode archive SHA-256 or size mismatch")


def extract_binary(archive: Path, destination: Path, item: dict) -> str:
    # Recheck even caller-supplied archives before reading their directory or data.
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    require(archive.stat().st_size == item["bytes"] and digest == item["sha256"], "OpenCode archive SHA-256 or size mismatch")
    require(not destination.is_symlink(), "OpenCode destination cannot be a symbolic link")
    temporary = destination.with_name(destination.name + "." + uuid4().hex + ".preparing")
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o700)
        with os.fdopen(descriptor, "wb") as output:
            if item["archive"].endswith(".zip"):
                with zipfile.ZipFile(archive) as bundle:
                    files = bundle.infolist()
                    require(len(files) == 1, "OpenCode archive must contain only its executable")
                    entry = files[0]
                    require(entry.filename == item["executable"] and not entry.is_dir()
                            and not stat.S_ISLNK(entry.external_attr >> 16)
                            and 0 < entry.file_size <= MAX_BINARY_BYTES, "Invalid OpenCode archive entry")
                    with bundle.open(entry) as source:
                        binary_hash = copy_bounded(source, output, entry.file_size)
            else:
                with tarfile.open(archive, "r:gz") as bundle:
                    entries = bundle.getmembers()
                    require(len(entries) == 1, "OpenCode archive must contain only its executable")
                    entry = entries[0]
                    require(entry.name == item["executable"] and entry.isfile()
                            and not entry.issym() and not entry.islnk()
                            and 0 < entry.size <= MAX_BINARY_BYTES, "Invalid OpenCode archive entry")
                    with bundle.extractfile(entry) as source:
                        binary_hash = copy_bounded(source, output, entry.size)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return binary_hash


def copy_bounded(source, output, expected: int) -> str:
    require(0 < expected <= MAX_BINARY_BYTES, "Invalid OpenCode executable size")
    digest, size = hashlib.sha256(), 0
    while data := source.read(min(1024 * 1024, expected - size + 1)):
        size += len(data)
        require(size <= expected, "OpenCode executable exceeded the declared size")
        output.write(data)
        digest.update(data)
    require(size == expected, "OpenCode executable extraction was incomplete")
    return digest.hexdigest()


def verify_architecture(binary: Path, target: str) -> str | None:
    with binary.open("rb") as source:
        header = source.read(64)
        if target.endswith("apple-darwin"):
            require(len(header) >= 32 and header[:4] == b"\xcf\xfa\xed\xfe", "OpenCode is not a 64-bit Mach-O executable")
            cpu, = struct.unpack_from("<I", header, 4)
            require(cpu == (0x100000C if target.startswith("aarch64") else 0x1000007), "OpenCode macOS architecture mismatch")
            commands, = struct.unpack_from("<I", header, 16)
            require(commands <= 1024, "Invalid OpenCode Mach-O load commands")
            source.seek(32)
            minimum = None
            for _ in range(commands):
                prefix = source.read(8)
                require(len(prefix) == 8, "Truncated OpenCode Mach-O load command")
                command, size = struct.unpack("<II", prefix)
                require(8 <= size <= 1024 * 1024, "Invalid OpenCode Mach-O load command size")
                data = source.read(size - 8)
                require(len(data) == size - 8, "Truncated OpenCode Mach-O load command")
                if command == 0x32 and len(data) >= 16:  # LC_BUILD_VERSION
                    os_platform, value = struct.unpack_from("<II", data)
                    require(os_platform == 1, "OpenCode Mach-O is not built for macOS")
                    minimum = value
                elif command == 0x24 and len(data) >= 8:  # LC_VERSION_MIN_MACOSX
                    minimum, = struct.unpack_from("<I", data)
            require(minimum is not None, "OpenCode macOS deployment target is missing")
            return f"{minimum >> 16}.{(minimum >> 8) & 255}.{minimum & 255}"
        if target.endswith("windows-msvc"):
            require(header[:2] == b"MZ" and len(header) == 64, "OpenCode is not a Windows executable")
            offset, = struct.unpack_from("<I", header, 60)
            require(64 <= offset <= 1024 * 1024, "Invalid OpenCode executable header")
            source.seek(offset)
            require(source.read(6) == b"PE\0\0\x64\x86", "OpenCode Windows architecture mismatch")
        else:
            require(header[:6] == b"\x7fELF\x02\x01" and len(header) >= 20 and struct.unpack_from("<H", header, 18)[0] == 62,
                    "OpenCode Linux architecture mismatch")
    return None


def isolated_environment(directory: Path) -> dict[str, str]:
    # In particular, Bun's Windows homedir uses USERPROFILE, not HOME alone.
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "LC_CTYPE"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    for variable, name in {"HOME": "home", "USERPROFILE": "home", "APPDATA": "roaming", "LOCALAPPDATA": "local",
                           "XDG_DATA_HOME": "data", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
                           "XDG_STATE_HOME": "state", "TMPDIR": "tmp", "TMP": "tmp", "TEMP": "tmp",
                           "OPENCODE_TEST_HOME": "home", "OPENCODE_TEST_MANAGED_CONFIG_DIR": "managed"}.items():
        path = directory / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment[variable] = str(path)
    environment.update({"OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
                        "OPENCODE_DISABLE_PROJECT_CONFIG": "true", "OPENCODE_DISABLE_CLAUDE_CODE": "true", "OPENCODE_AUTH_CONTENT": "{}",
                        "OPENCODE_PURE": "true", "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true", "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
                        "OPENCODE_DISABLE_EMBEDDED_WEB_UI": "true", "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true", "OPENCODE_DISABLE_FFF": "true",
                        "OPENCODE_CONFIG_CONTENT": '{"plugin":[],"mcp":{},"permission":{"*":"deny"},"instructions":[],"snapshot":false,"share":"disabled","autoupdate":false,"lsp":false,"formatter":false}'})
    return environment


def smoke_version(binary: Path, version: str) -> None:
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink(), "Bundled OpenCode executable is missing or invalid")
    with tempfile.TemporaryDirectory(prefix="easy-publish-opencode-check-") as directory:
        environment = isolated_environment(Path(directory))
        try:
            result = subprocess.run([str(binary), "--version"], env=environment, cwd=directory, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", timeout=45,
                                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        except Exception:
            raise PreparationError("Bundled OpenCode could not complete its version check") from None
        require(result.returncode == 0 and result.stdout.strip() == version, "Bundled OpenCode version check failed")
    print(f"Bundled OpenCode {version} passed isolated startup.", flush=True)


def stop_server(child: subprocess.Popen) -> None:
    if os.name == "nt":
        if child.poll() is None:
            try:
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            except (OSError, subprocess.TimeoutExpired):
                child.kill()
    else:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            child.kill()
        else:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)


def smoke_server(binary: Path, version: str) -> None:
    """Exercise Bun's real serve path offline, without a model or article request."""
    require(binary.is_absolute() and binary.is_file() and not binary.is_symlink(), "Bundled OpenCode executable is missing or invalid")
    with tempfile.TemporaryDirectory(prefix="easy-publish-opencode-serve-") as directory:
        environment = isolated_environment(Path(directory))
        password = secrets.token_urlsafe(32)
        environment.update({"OPENCODE_SERVER_PASSWORD": password, "OPENCODE_SERVER_USERNAME": "easy-publish-check"})
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        child = subprocess.Popen([str(binary), "serve", "--hostname", "127.0.0.1", "--port", "0", "--mdns=false"],
                                 cwd=directory, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, **options)
        ready = queue.Queue(maxsize=1)

        def read_endpoint():
            found, size = False, 0
            while line := child.stdout.readline(4097):
                if not found:
                    size += len(line)
                    match = re.fullmatch(rb"opencode server listening on http://127\.0\.0\.1:([0-9]{1,5})\r?\n", line)
                    if match and 0 < int(match[1]) < 65536:
                        ready.put(int(match[1]))
                        found = True
                    elif size > 65536 or len(line) > 4096:
                        ready.put(None)
                        found = True
            if not found:
                ready.put(None)

        reader = threading.Thread(target=read_endpoint, daemon=True)
        reader.start()
        try:
            try:
                port = ready.get(timeout=45)
            except queue.Empty:
                raise PreparationError("Bundled OpenCode serve startup timed out") from None
            require(port is not None, "Bundled OpenCode did not report a loopback endpoint")

            def health(authenticated):
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                try:
                    headers = {"Authorization": "Basic " + base64.b64encode(("easy-publish-check:" + password).encode()).decode()} if authenticated else {}
                    connection.request("GET", "/global/health", headers=headers)
                    response = connection.getresponse()
                    data = response.read(16385)
                    require(len(data) <= 16384, "OpenCode health response exceeded its limit")
                    return response.status, data
                finally:
                    connection.close()

            status, _ = health(False)
            require(status in {401, 403}, "OpenCode serve accepted an unauthenticated health request")
            status, data = health(True)
            value = json.loads(data)
            require(status == 200 and value.get("healthy") is True and value.get("version", "").lstrip("v") == version,
                    "OpenCode serve health or version check failed")
        except PreparationError:
            raise
        except Exception:
            raise PreparationError("Bundled OpenCode failed its offline serve check; no process output was logged") from None
        finally:
            stop_server(child)
            reader.join(timeout=2)
            child.stdout.close()
            require(not reader.is_alive(), "OpenCode serve output reader did not stop")
    print(f"Bundled OpenCode {version} passed offline loopback serve, authentication and shutdown.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=native_target())
    parser.add_argument("--check", type=Path, help="Verify an already bundled executable without downloading")
    args = parser.parse_args()
    try:
        require(args.target == native_target(), "Prepare and check OpenCode on the target platform")
        manifest, item = pinned_target(args.target)
        if args.check:
            binary = args.check.absolute()
        else:
            suffix = ".exe" if args.target.endswith("windows-msvc") else ""
            binary = ROOT / "src-tauri/binaries" / f"easy-publish-opencode-{args.target}{suffix}"
            binary.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="easy-publish-opencode-download-") as directory:
                archive = Path(directory) / item["archive"]
                url = f"https://github.com/{manifest['repository']}/releases/download/v{manifest['version']}/{item['archive']}"
                download_archive(url, archive, item)
                extracted = extract_binary(archive, binary, item)
                print(f"Verified OpenCode archive and extracted executable SHA-256: {extracted}", flush=True)
            if os.name != "nt":
                binary.chmod(0o755)
            if platform.system() == "Darwin":
                subprocess.run(["codesign", "--force", "--sign", "-", "--options", "0", str(binary)], check=True, capture_output=True)
        minimum = verify_architecture(binary, args.target)
        if minimum:
            declared = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text())["bundle"]["macOS"]["minimumSystemVersion"]
            version_tuple = lambda value: tuple(int(p) for p in value.split(".")) + (0,) * (3 - len(value.split(".")))
            require(version_tuple(minimum) <= version_tuple(declared), f"OpenCode requires macOS {minimum}; application minimum {declared} must be updated")
            print(f"OpenCode macOS minimum: {minimum}", flush=True)
        smoke_version(binary, manifest["version"])
        smoke_server(binary, manifest["version"])
    except Exception as error:
        message = str(error) if isinstance(error, PreparationError) else type(error).__name__
        parser.exit(1, "OpenCode preparation stopped: " + message + "\n")


if __name__ == "__main__":
    main()

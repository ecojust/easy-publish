"""Private desktop discovery for the local, stdio-only MCP bridge.

The MCP credential is separate from the native host token. It is never part of
the user-facing MCP configuration or a protocol response. Windows persists the
credential with current-user DPAPI because chmod alone does not restrict ACLs.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import stat


ENDPOINT_FILE = "mcp-endpoint.json"
MAX_DESCRIPTOR_BYTES = 16 * 1024


class ConnectionUnavailable(Exception):
    """Safe fixed error; never include a descriptor or credential in its text."""


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source = ctypes.create_string_buffer(data)
    input_blob = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = None
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = "Easy Publish local MCP session"
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(input_blob), description, None, None, None, 0x1, ctypes.byref(output_blob)):
        raise ConnectionUnavailable("无法使用当前系统用户的 MCP 会话")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(output_blob.pbData, wintypes.HLOCAL))


def _check_directory(data_dir: Path) -> None:
    info = data_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or data_dir.is_symlink():
        raise ConnectionUnavailable("MCP 数据目录无效")
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ConnectionUnavailable("MCP 数据目录需要当前用户独占访问权限")


def _check_file(info) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise ConnectionUnavailable("MCP 会话文件无效")
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ConnectionUnavailable("MCP 会话文件需要当前用户独占访问权限")


def write_endpoint(data_dir: Path, port: int, token: str, instance_id: str) -> None:
    _check_directory(data_dir)
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or len(token) < 32:
        raise ValueError("Invalid local MCP endpoint")
    destination = data_dir / ENDPOINT_FILE
    if destination.exists() or destination.is_symlink():
        _check_file(destination.lstat())
    value = {"version": 1, "port": port, "instanceId": instance_id, "pid": os.getpid()}
    if os.name == "nt":
        value["protectedToken"] = base64.b64encode(_dpapi(token.encode("ascii"), decrypt=False)).decode("ascii")
    else:
        value["token"] = token
    temporary = data_dir / (".mcp-endpoint-" + secrets.token_hex(12))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def read_endpoint(data_dir: Path) -> dict:
    try:
        _check_directory(data_dir)
        path = data_dir / ENDPOINT_FILE
        before = path.lstat()
        _check_file(before)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            actual = os.fstat(source.fileno())
            _check_file(actual)
            if (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino):
                raise ConnectionUnavailable("MCP 会话已变化，请重试")
            data = source.read(MAX_DESCRIPTOR_BYTES + 1)
        if len(data) > MAX_DESCRIPTOR_BYTES:
            raise ValueError("Descriptor too large")
        value = json.loads(data)
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("Descriptor version")
        port, identity, pid = value.get("port"), value.get("instanceId"), value.get("pid")
        if type(port) is not int or not 1 <= port <= 65535 or type(pid) is not int or pid <= 0:
            raise ValueError("Descriptor endpoint")
        if not isinstance(identity, str) or not 24 <= len(identity) <= 128:
            raise ValueError("Descriptor identity")
        if os.name == "nt":
            token = _dpapi(base64.b64decode(value["protectedToken"], validate=True), decrypt=True).decode("ascii")
        else:
            token = value.get("token")
        if not isinstance(token, str) or not 32 <= len(token) <= 256 or not all(c.isalnum() or c in "_-" for c in token):
            raise ValueError("Descriptor token")
        return {"port": port, "token": token, "instanceId": identity, "pid": pid}
    except ConnectionUnavailable:
        raise
    except Exception:
        raise ConnectionUnavailable("请先打开 Easy Publish 桌面应用，再调用 MCP 工具") from None


def remove_endpoint(data_dir: Path, instance_id: str) -> None:
    # An older process must never remove a newer desktop instance's descriptor.
    try:
        if read_endpoint(data_dir)["instanceId"] == instance_id:
            (data_dir / ENDPOINT_FILE).unlink()
    except (ConnectionUnavailable, OSError):
        pass

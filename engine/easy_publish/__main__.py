"""Desktop-owned loopback engine and supervised provider worker entry point."""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import sys
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Easy Publish local desktop engine")
    parser.add_argument("--data-dir", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--worker", action="store_true")
    mode.add_argument("--mcp", action="store_true", help="Run the stdio MCP bridge to the already-running desktop")
    parser.add_argument("--parent-pid", type=int, help="Native host PID; stop when that host exits")
    args = parser.parse_args()
    if args.worker:
        from easy_publish.worker import worker_main
        return worker_main()
    if not args.data_dir:
        parser.error("--data-dir is required")
    if args.mcp:
        from easy_publish.mcp_server import mcp_main
        return mcp_main(args.data_dir)
    if os.name != "nt":
        os.umask(0o077)
    data_dir = args.data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        data_dir.chmod(0o700)
    from easy_publish.instance import InstanceLock
    try:
        instance = InstanceLock(data_dir)
    except RuntimeError:
        print("本地内容库正在使用中，请关闭已有应用后重试", file=sys.stderr)
        return 2
    # Credentials must never be emitted through third-party HTTP/debug logs.
    logging.disable(logging.CRITICAL)
    from easy_publish.app import create_app
    import uvicorn
    token = secrets.token_urlsafe(48)
    mcp_token = secrets.token_urlsafe(48)
    instance_id = secrets.token_urlsafe(32)
    app = create_app(data_dir, token, mcp_token=mcp_token, instance_id=instance_id)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, access_log=False, server_header=False)
    server = uvicorn.Server(config)
    app.state.exit_callback = lambda: setattr(server, "should_exit", True)
    stopped = threading.Event()
    monitor = None
    if args.parent_pid:
        monitor = threading.Thread(target=_watch_host, args=(args.parent_pid, stopped, app.state.exit_callback), daemon=True)
        monitor.start()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        from easy_publish.mcp_connection import remove_endpoint, write_endpoint
        try:
            write_endpoint(data_dir, sock.getsockname()[1], mcp_token, instance_id)
            # Only the native host reads this private stdout pipe. Never copy to logs.
            print(json.dumps({"event": "ready", "port": sock.getsockname()[1], "token": token}), flush=True)
            server.run(sockets=[sock])
        finally:
            remove_endpoint(data_dir, instance_id)
            stopped.set()
            instance.close()
    return 0


def _watch_host(pid, stopped, on_exit):
    # PyInstaller adds a bootloader parent, so the native host supplies its PID.
    handle = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            on_exit()
            return
    try:
        while not stopped.wait(1):
            if handle:
                alive = kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
            else:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            if not alive:
                on_exit()
                return
    finally:
        if handle:
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    sys.exit(main())

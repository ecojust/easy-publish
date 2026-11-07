"""Offline archive, process-isolation and architecture guards for the bundled CLI."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

SPEC = importlib.util.spec_from_file_location("prepare_opencode", Path(__file__).resolve().parents[1] / "prepare-opencode.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()

    def metadata(self, archive, executable="opencode"):
        return {"archive": archive.name, "bytes": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "executable": executable}

    def test_pins_release_assets_and_license_for_all_supported_targets(self):
        for target in ["aarch64-apple-darwin", "x86_64-apple-darwin", "x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"]:
            manifest, item = prepare.pinned_target(target)
            self.assertEqual(manifest["version"], "1.19.5")
            self.assertEqual(manifest["sourceCommit"], "810c0eba1a7348036f8bdf61e6632d0c36c6876c")
            self.assertEqual(len(item["sha256"]), 64)
            if target.startswith("x86_64"):
                self.assertIn("baseline", item["archive"])
        with self.assertRaises(prepare.PreparationError):
            prepare.pinned_target("unsupported-target")

    def test_valid_zip_preserves_only_binary_bytes(self):
        archive, destination = self.root / "opencode.zip", self.root / "prepared"
        content = b"MZ offline executable fixture"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("opencode.exe", content)
        digest = prepare.extract_binary(archive, destination, self.metadata(archive, "opencode.exe"))
        self.assertEqual(destination.read_bytes(), content)
        self.assertEqual(digest, hashlib.sha256(content).hexdigest())
        self.assertEqual(list(self.root.glob("*.preparing")), [])

    def test_valid_tar_preserves_only_binary_bytes(self):
        archive, destination = self.root / "opencode.tar.gz", self.root / "prepared"
        content = b"ELF fixture payload"
        with tarfile.open(archive, "w:gz") as bundle:
            entry = tarfile.TarInfo("opencode")
            entry.size = len(content)
            bundle.addfile(entry, io.BytesIO(content))
        prepare.extract_binary(archive, destination, self.metadata(archive))
        self.assertEqual(destination.read_bytes(), content)

    def test_hash_mismatch_preserves_previous_executable(self):
        archive, destination = self.root / "opencode.zip", self.root / "prepared"
        archive.write_bytes(b"untrusted archive")
        destination.write_bytes(b"previous")
        item = self.metadata(archive)
        item["sha256"] = "0" * 64
        with self.assertRaisesRegex(prepare.PreparationError, "SHA-256"):
            prepare.extract_binary(archive, destination, item)
        self.assertEqual(destination.read_bytes(), b"previous")

    def test_zip_rejects_traversal_multiple_entries_symlinks_and_oversize(self):
        for name, extra, symlink, oversized in [("../opencode", False, False, False), ("/opencode", False, False, False),
                ("folder\\opencode", False, False, False), ("opencode", True, False, False),
                ("opencode", False, True, False), ("opencode", False, False, True)]:
            with self.subTest(name=name, extra=extra, symlink=symlink, oversized=oversized):
                archive, destination = self.root / "bad.zip", self.root / "prepared"
                with zipfile.ZipFile(archive, "w") as bundle:
                    entry = zipfile.ZipInfo(name)
                    if symlink:
                        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                    bundle.writestr(entry, b"fixture")
                    if extra:
                        bundle.writestr("extra", b"fixture")
                destination.write_bytes(b"previous")
                with patch.object(prepare, "MAX_BINARY_BYTES", 1 if oversized else 1024):
                    with self.assertRaises(prepare.PreparationError):
                        prepare.extract_binary(archive, destination, self.metadata(archive))
                self.assertEqual(destination.read_bytes(), b"previous")
                self.assertEqual(list(self.root.glob("*.preparing")), [])

    def test_tar_rejects_symbolic_and_hard_links(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE]:
            archive = self.root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                entry = tarfile.TarInfo("opencode")
                entry.type = kind
                entry.linkname = "../outside"
                bundle.addfile(entry)
            with self.assertRaises(prepare.PreparationError):
                prepare.extract_binary(archive, self.root / "prepared", self.metadata(archive))
        self.assertFalse((self.root / "prepared").exists())

    def test_download_never_trusts_response_size_or_hash(self):
        data = b"fixture bytes"
        item = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for payload in [data[:-1], data + b"extra", b"x" * len(data)]:
            target = self.root / "download"
            target.unlink(missing_ok=True)
            with patch.object(prepare, "urlopen", return_value=io.BytesIO(payload)):
                with self.assertRaises(prepare.PreparationError):
                    prepare.download_archive("https://github.com/fixture", target, item)
        target.unlink(missing_ok=True)
        with patch.object(prepare, "urlopen", return_value=io.BytesIO(data)):
            prepare.download_archive("https://github.com/fixture", target, item)
        self.assertEqual(target.read_bytes(), data)

    def test_remote_errors_never_expose_urls_or_headers(self):
        with patch.object(prepare, "urlopen", side_effect=OSError("https://private.invalid/?sig=SECRET")):
            with self.assertRaises(prepare.PreparationError) as failure:
                prepare.download_archive("https://github.com/fixture", self.root / "download", {"bytes": 1})
        self.assertNotIn("SECRET", str(failure.exception))
        self.assertNotIn("private.invalid", str(failure.exception))

    def test_profile_isolation_covers_windows_native_and_xdg_paths(self):
        with patch.dict(os.environ, {"HOME": "/outside", "USERPROFILE": "C:\\outside", "APPDATA": "C:\\outside",
                                     "LOCALAPPDATA": "C:\\outside", "OPENAI_API_KEY": "secret", "OPENCODE_CONFIG": "/outside/config", "NODE_OPTIONS": "unsafe"}):
            environment = prepare.isolated_environment(self.root)
        for key in ["HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "TMP", "TEMP"]:
            self.assertTrue(Path(environment[key]).is_relative_to(self.root), key)
            self.assertTrue(Path(environment[key]).is_dir(), key)
        for key in ["OPENAI_API_KEY", "OPENCODE_CONFIG", "NODE_OPTIONS"]:
            self.assertNotIn(key, environment)
        self.assertEqual(environment["OPENCODE_AUTH_CONTENT"], "{}")
        self.assertEqual(json.loads(environment["OPENCODE_CONFIG_CONTENT"])["permission"], {"*": "deny"})

    def test_version_smoke_executes_exact_path_without_shell_and_enforces_version(self):
        binary = self.root / "OpenCode fixture.exe"
        binary.write_bytes(b"fixture")
        response = subprocess.CompletedProcess([], 0, stdout="1.19.5\n", stderr="")
        with patch.object(prepare.subprocess, "run", return_value=response) as run:
            prepare.smoke_version(binary, "1.19.5")
        self.assertEqual(run.call_args.args[0], [str(binary), "--version"])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], run.call_args.kwargs["env"]["USERPROFILE"])
        with patch.object(prepare.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="other", stderr="SECRET")):
            with self.assertRaises(prepare.PreparationError) as failure:
                prepare.smoke_version(binary, "1.19.5")
        self.assertNotIn("SECRET", str(failure.exception))

    def test_macos_architecture_and_deployment_target_are_verified(self):
        binary = self.root / "mach-o"
        header = struct.pack("<8I", 0xFEEDFACF, 0x100000C, 0, 2, 1, 24, 0, 0)
        command = struct.pack("<6I", 0x32, 24, 1, 0xD0500, 0x1A0000, 0)
        binary.write_bytes(header + command)
        self.assertEqual(prepare.verify_architecture(binary, "aarch64-apple-darwin"), "13.5.0")
        with self.assertRaises(prepare.PreparationError):
            prepare.verify_architecture(binary, "x86_64-apple-darwin")
        binary.write_bytes(header + command[:8])
        with self.assertRaises(prepare.PreparationError):
            prepare.verify_architecture(binary, "aarch64-apple-darwin")

    def test_windows_and_linux_architecture_are_verified(self):
        binary = self.root / "binary"
        header = bytearray(64)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 60, 64)
        binary.write_bytes(header + b"PE\0\0\x64\x86")
        self.assertIsNone(prepare.verify_architecture(binary, "x86_64-pc-windows-msvc"))
        with self.assertRaises(prepare.PreparationError):
            prepare.verify_architecture(binary, "x86_64-unknown-linux-gnu")
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", header, 18, 62)
        binary.write_bytes(header)
        self.assertIsNone(prepare.verify_architecture(binary, "x86_64-unknown-linux-gnu"))

    def test_serve_checks_private_loopback_authentication_then_stops(self):
        binary = self.root / "opencode"
        binary.write_bytes(b"fixture")
        child = MagicMock(stdout=io.BytesIO(b"opencode server listening on http://127.0.0.1:45678\n"))
        unauthorized = MagicMock(status=401)
        unauthorized.read.return_value = b"Unauthorized"
        healthy = MagicMock(status=200)
        healthy.read.return_value = b'{"healthy":true,"version":"1.19.5"}'
        connections = [MagicMock(), MagicMock()]
        for connection, response in zip(connections, [unauthorized, healthy]):
            connection.getresponse.return_value = response
        with patch.object(prepare.subprocess, "Popen", return_value=child) as start, \
                patch.object(prepare.http.client, "HTTPConnection", side_effect=connections) as http, \
                patch.object(prepare, "stop_server") as stop:
            prepare.smoke_server(binary, "1.19.5")
        self.assertEqual(start.call_args.args[0], [str(binary), "serve", "--hostname", "127.0.0.1", "--port", "0", "--mdns=false"])
        self.assertEqual(start.call_args.kwargs["env"]["OPENCODE_DISABLE_MODELS_FETCH"], "true")
        self.assertTrue(start.call_args.kwargs["env"]["OPENCODE_SERVER_PASSWORD"])
        self.assertNotIn("shell", start.call_args.kwargs)
        self.assertTrue(all(call.args[:2] == ("127.0.0.1", 45678) for call in http.call_args_list))
        self.assertEqual(connections[0].request.call_args.kwargs["headers"], {})
        self.assertIn("Authorization", connections[1].request.call_args.kwargs["headers"])
        stop.assert_called_once_with(child)
        self.assertTrue(child.stdout.closed)

    def test_serve_rejects_public_listener_and_always_stops(self):
        binary = self.root / "opencode"
        binary.write_bytes(b"fixture")
        for endpoint in [b"opencode server listening on http://0.0.0.0:45678\n", b"x" * 4097]:
            child = MagicMock(stdout=io.BytesIO(endpoint))
            with patch.object(prepare.subprocess, "Popen", return_value=child), \
                    patch.object(prepare.http.client, "HTTPConnection") as http, \
                    patch.object(prepare, "stop_server") as stop:
                with self.assertRaises(prepare.PreparationError):
                    prepare.smoke_server(binary, "1.19.5")
            http.assert_not_called()
            stop.assert_called_once_with(child)
            self.assertTrue(child.stdout.closed)

    def test_serve_rejects_missing_auth_and_wrong_version_without_leaking_response(self):
        binary = self.root / "opencode"
        binary.write_bytes(b"fixture")
        for statuses in [[200], [401, 200]]:
            child = MagicMock(stdout=io.BytesIO(b"opencode server listening on http://127.0.0.1:45678\n"))
            connections = []
            for status in statuses:
                response = MagicMock(status=status)
                response.read.return_value = b'{"healthy":true,"version":"SECRET wrong-version"}'
                connection = MagicMock()
                connection.getresponse.return_value = response
                connections.append(connection)
            with patch.object(prepare.subprocess, "Popen", return_value=child), \
                    patch.object(prepare.http.client, "HTTPConnection", side_effect=connections), \
                    patch.object(prepare, "stop_server") as stop:
                with self.assertRaises(prepare.PreparationError) as failure:
                    prepare.smoke_server(binary, "1.19.5")
            self.assertNotIn("SECRET", str(failure.exception))
            stop.assert_called_once_with(child)


if __name__ == "__main__":
    unittest.main()

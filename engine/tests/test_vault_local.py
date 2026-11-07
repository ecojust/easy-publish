"""Application-owned credentials never consult the OS or another app's storage."""
from __future__ import annotations

import builtins
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from easy_publish.vault import Vault, VaultError


class LocalVaultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("EASY_PUBLISH_TESTING", None)
        os.environ.pop("EASY_PUBLISH_TEST_KEY", None)

    def legacy_file(self, account_id="legacy-account", kind="session"):
        directory = self.root / "vault"
        directory.mkdir(mode=0o700, exist_ok=True)
        associated = (account_id + "\0" + kind).encode()
        path = directory / (hashlib.sha256(associated).hexdigest() + ".enc")
        nonce = secrets.token_bytes(12)
        data = b"EPV1" + nonce + AESGCM(b"o" * 32).encrypt(nonce, b"old-secret", associated)
        path.write_bytes(data)
        return path, data

    def test_production_storage_persists_without_importing_keyring(self):
        original_import = builtins.__import__

        def without_keyring(name, *args, **kwargs):
            if name == "keyring" or name.startswith("keyring."):
                self.fail("Account storage attempted to import a system keyring")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_keyring):
            first = Vault(self.root)
            self.assertTrue(first.ready, first.message)
            first.put("account", "session", b"cookie-fixture")
            first.put("account", "credentials", b"credential-fixture")
            key = (first.directory / Vault.KEY_FILE).read_bytes()
            second = Vault(self.root)
            self.assertTrue(second.ready, second.message)
            self.assertEqual(second.get("account", "session"), b"cookie-fixture")
            self.assertEqual(second.get("account", "credentials"), b"credential-fixture")
            self.assertEqual((second.directory / Vault.KEY_FILE).read_bytes(), key)
        self.assertEqual(len(key), 32)
        self.assertEqual(first.directory.parent, self.root)
        self.assertFalse((self.root / "vault").exists())
        for location in first.directory.glob("*.enc"):
            self.assertNotIn(b"fixture", location.read_bytes())

    def test_legacy_ciphertext_is_preserved_and_reconnection_uses_local_key(self):
        legacy, original = self.legacy_file()
        vault = Vault(self.root)
        self.assertTrue(vault.ready)
        self.assertTrue(vault.requires_reconnect("legacy-account", "session"))
        self.assertFalse(vault.requires_reconnect("other-account", "session"))
        self.assertFalse(vault.requires_reconnect("legacy-account", "credentials"))
        self.assertIsNone(vault.get("legacy-account", "session"))
        vault.put("legacy-account", "session", b"reconnected-cookie")
        self.assertFalse(vault.requires_reconnect("legacy-account", "session"))
        restarted = Vault(self.root)
        self.assertEqual(restarted.get("legacy-account", "session"), b"reconnected-cookie")
        vault.delete("legacy-account")
        self.assertIsNone(vault.get("legacy-account", "session"))
        self.assertEqual(legacy.read_bytes(), original)
        self.assertEqual(list(legacy.parent.iterdir()), [legacy])

    def test_missing_key_with_ciphertext_fails_without_replacing_data(self):
        vault = Vault(self.root)
        vault.put("account", "session", b"saved-cookie")
        location, _ = vault._location("account", "session")
        original = location.read_bytes()
        key_path = vault.directory / Vault.KEY_FILE
        key_path.unlink()
        restarted = Vault(self.root)
        self.assertFalse(restarted.ready)
        with self.assertRaises(VaultError):
            restarted.put("account", "session", b"replacement")
        self.assertFalse(key_path.exists())
        self.assertEqual(location.read_bytes(), original)

    def test_corrupt_key_is_never_replaced_even_with_no_ciphertext(self):
        vault = Vault(self.root)
        path = vault.directory / Vault.KEY_FILE
        for bad in (b"", b"short", b"x" * 33):
            path.write_bytes(bad)
            restarted = Vault(self.root)
            self.assertFalse(restarted.ready)
            self.assertEqual(path.read_bytes(), bad)

    def test_simultaneous_first_start_uses_one_complete_key(self):
        count = 8
        barrier = threading.Barrier(count)

        def start(index):
            barrier.wait(timeout=10)
            vault = Vault(self.root)
            self.assertTrue(vault.ready, vault.message)
            vault.put(str(index), "session", ("fixture-" + str(index)).encode())

        with ThreadPoolExecutor(max_workers=count) as executor:
            list(executor.map(start, range(count)))
        restarted = Vault(self.root)
        self.assertTrue(restarted.ready)
        for index in range(count):
            self.assertEqual(restarted.get(str(index), "session"), ("fixture-" + str(index)).encode())
        self.assertEqual(list(restarted.directory.glob("*.tmp")), [])

    def test_failed_atomic_key_install_leaves_no_partial_key_and_can_restart(self):
        def fail_install(source, target):
            self.assertEqual(len(Path(source).read_bytes()), 32)
            self.assertFalse(Path(target).exists())
            raise OSError("fixture disk failure")

        with patch("easy_publish.vault.os.link", side_effect=fail_install):
            vault = Vault(self.root)
        self.assertFalse(vault.ready)
        self.assertEqual(list(vault.directory.iterdir()), [])
        self.assertTrue(Vault(self.root).ready)

    def test_first_session_written_during_missing_key_read_uses_concurrent_key(self):
        original_read = Vault._read_private_file
        pending_first_read = True

        def concurrent_start(vault, path, limit):
            nonlocal pending_first_read
            if pending_first_read:
                pending_first_read = False
                winner = Vault(self.root)
                self.assertTrue(winner.ready)
                winner.put("winner", "session", b"saved-by-other-instance")
                raise FileNotFoundError("Key was absent before other instance started")
            return original_read(vault, path, limit)

        with patch.object(Vault, "_read_private_file", concurrent_start):
            follower = Vault(self.root)
        self.assertTrue(follower.ready, follower.message)
        self.assertEqual(follower.get("winner", "session"), b"saved-by-other-instance")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits; Windows uses inherited application directory ACLs")
    def test_permissions_protect_key_and_sessions(self):
        vault = Vault(self.root)
        vault.put("account", "session", b"cookie")
        self.assertEqual(vault.directory.stat().st_mode & 0o777, 0o700)
        for location in vault.directory.iterdir():
            self.assertEqual(location.stat().st_mode & 0o777, 0o600)
        key_path = vault.directory / Vault.KEY_FILE
        key_path.chmod(0o644)
        self.assertFalse(Vault(self.root).ready)
        key_path.chmod(0o600)
        self.assertTrue(Vault(self.root).ready)
        vault.directory.chmod(0o755)
        self.assertFalse(Vault(self.root).ready)

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows administrator privileges")
    def test_symlinked_directory_is_rejected_without_reading_external_storage(self):
        outside = self.root / "other-application"
        outside.mkdir()
        marker = outside / Vault.KEY_FILE
        marker.write_bytes(b"e" * 32)
        (self.root / Vault.DIRECTORY).symlink_to(outside, target_is_directory=True)
        vault = Vault(self.root)
        self.assertFalse(vault.ready)
        self.assertEqual(marker.read_bytes(), b"e" * 32)
        self.assertEqual(list(outside.iterdir()), [marker])

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows administrator privileges")
    def test_symlinked_key_and_ciphertext_are_rejected(self):
        vault = Vault(self.root)
        key_path = vault.directory / Vault.KEY_FILE
        key = key_path.read_bytes()
        outside = self.root / "other-application-secret"
        outside.write_bytes(key)
        outside.chmod(0o600)
        key_path.unlink()
        key_path.symlink_to(outside)
        self.assertFalse(Vault(self.root).ready)
        self.assertTrue(key_path.is_symlink())
        self.assertEqual(outside.read_bytes(), key)
        key_path.unlink()
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        location, _ = vault._location("account", "session")
        location.symlink_to(outside)
        with self.assertRaises(VaultError):
            vault.get("account", "session")
        with self.assertRaises(VaultError):
            vault.put("account", "session", b"must not overwrite")
        self.assertEqual(outside.read_bytes(), key)

    @unittest.skipIf(os.name == "nt", "Creating symlinks may require Windows administrator privileges")
    def test_legacy_directory_symlink_is_not_followed(self):
        outside = self.root / "other-application"
        outside.mkdir()
        (self.root / "vault").symlink_to(outside, target_is_directory=True)
        vault = Vault(self.root)
        self.assertTrue(vault.ready)
        self.assertTrue(vault.requires_reconnect("legacy-account", "session"))
        self.assertEqual(list(outside.iterdir()), [])
        vault.put("legacy-account", "session", b"new-cookie")
        self.assertFalse(vault.requires_reconnect("legacy-account", "session"))

    @unittest.skipUnless(os.name == "nt", "Windows junctions")
    def test_junction_directory_is_rejected_without_reading_its_target(self):
        outside = self.root / "other-application"
        outside.mkdir()
        marker = outside / Vault.KEY_FILE
        marker.write_bytes(b"e" * 32)
        junction = self.root / Vault.DIRECTORY
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        try:
            self.assertFalse(Vault(self.root).ready)
            self.assertEqual(marker.read_bytes(), b"e" * 32)
            self.assertEqual(list(outside.iterdir()), [marker])
        finally:
            junction.rmdir()


if __name__ == "__main__":
    unittest.main()

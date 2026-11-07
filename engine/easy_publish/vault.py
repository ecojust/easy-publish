"""Encrypted account storage contained entirely in the application's data directory.

The local key is protected by file permissions, not an operating-system password
store. Legacy keyring-backed ciphertext is left untouched; users reconnect their
accounts rather than granting access to Keychain to migrate it.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import secrets
import stat
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(RuntimeError):
    pass


class Vault:
    KINDS = {"credentials", "session"}
    DIRECTORY = "vault-local-v1"
    KEY_FILE = "master.key"
    MAX_ENCRYPTED_BYTES = 16 * 1024 * 1024 + 32

    def __init__(self, data_dir: Path | str):
        root = Path(data_dir).resolve()
        self.directory = root / self.DIRECTORY
        self._legacy_directory = root / "vault"
        self.ready = False
        self.message = "应用内账号存储尚未准备好"
        self._cipher: AESGCM | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._check_directory()
            key = self._load_key()
            self._cipher = AESGCM(key)
            self.ready = True
            self.message = "账号信息仅保存在 Easy Publish 应用数据目录，不访问系统钥匙串"
        except Exception:
            self.message = "应用内账号存储无法读取；请检查 Easy Publish 数据目录及本地密钥，原有数据未被覆盖"

    def _check_directory(self) -> None:
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or self._is_link(info):
            raise VaultError("Invalid application account directory")
        # Windows inherits the current user's application-data directory ACL.
        # POSIX must not expose the encryption key or sessions to other users.
        if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise VaultError("Application account directory is not private")

    @staticmethod
    def _is_link(info: os.stat_result) -> bool:
        return stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    @staticmethod
    def _check_file(info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode) or Vault._is_link(info):
            raise VaultError("Invalid application account file")
        if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise VaultError("Application account file is not private")

    def _read_private_file(self, path: Path, limit: int) -> bytes:
        self._check_directory()
        before = path.lstat()
        self._check_file(before)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as source:
            actual = os.fstat(source.fileno())
            self._check_file(actual)
            if (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino):
                raise VaultError("Application account file changed while opening")
            data = source.read(limit + 1)
        if len(data) > limit:
            raise VaultError("Application account file is too large")
        return data

    def _load_key(self) -> bytes:
        test_key = os.environ.get("EASY_PUBLISH_TEST_KEY")
        if test_key is not None:
            if os.environ.get("EASY_PUBLISH_TESTING") != "1":
                raise VaultError("Test key requires explicit test mode")
            key = base64.b64decode(test_key, validate=True)
            if len(key) != 32:
                raise VaultError("Invalid test key")
            return key

        location = self.directory / self.KEY_FILE
        try:
            key = self._read_private_file(location, 32)
        except FileNotFoundError:
            # Never replace a lost key and make existing local ciphertext
            # permanently unreadable. Legacy ciphertext has its own namespace.
            if any(self.directory.glob("*.enc")):
                # Another first-start process may have installed the key and
                # written its first session since our missing-file read.
                try:
                    key = self._read_private_file(location, 32)
                except FileNotFoundError:
                    raise VaultError("Local encryption key is missing") from None
                if len(key) != 32:
                    raise VaultError("Invalid local encryption key")
                return key
            temporary = self.directory / (".key-" + uuid4().hex + ".tmp")
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(secrets.token_bytes(32))
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    # Publish only a fully written key, without replacing a
                    # key installed by another process in the meantime.
                    os.link(temporary, location)
                    if os.name != "nt":
                        descriptor = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
            key = self._read_private_file(location, 32)
        if len(key) != 32:
            raise VaultError("Invalid local encryption key")
        return key

    def _location(self, account_id: str, kind: str) -> tuple[Path, bytes]:
        if not isinstance(account_id, str) or not account_id or len(account_id) > 512 or kind not in self.KINDS:
            raise VaultError("Invalid account storage key")
        associated = (account_id + "\0" + kind).encode("utf-8")
        return self.directory / (hashlib.sha256(associated).hexdigest() + ".enc"), associated

    def requires_reconnect(self, account_id: str, kind: str) -> bool:
        """Inspect app-owned filenames only; never read a legacy secret or key."""
        location, _ = self._location(account_id, kind)
        try:
            self._check_file(location.lstat())
            return False
        except (FileNotFoundError, VaultError):
            pass
        try:
            legacy = self._legacy_directory.lstat()
            if self._is_link(legacy):
                return True  # Do not inspect anything outside the app directory.
            if not stat.S_ISDIR(legacy.st_mode):
                return False
            (self._legacy_directory / location.name).lstat()
            return True
        except FileNotFoundError:
            return False

    def put(self, account_id: str, kind: str, data: bytes) -> None:
        if not self.ready or self._cipher is None:
            raise VaultError(self.message)
        if not isinstance(data, bytes):
            raise TypeError("Vault data must be bytes")
        if len(data) > self.MAX_ENCRYPTED_BYTES - 32:
            raise VaultError("Account data is too large")
        self._check_directory()
        destination, associated = self._location(account_id, kind)
        try:
            self._check_file(destination.lstat())
        except FileNotFoundError:
            pass
        nonce = secrets.token_bytes(12)
        encrypted = b"EPV1" + nonce + self._cipher.encrypt(nonce, data, associated)
        temporary = self.directory / (uuid4().hex + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(encrypted)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, account_id: str, kind: str) -> bytes | None:
        if not self.ready or self._cipher is None:
            raise VaultError(self.message)
        location, associated = self._location(account_id, kind)
        try:
            encrypted = self._read_private_file(location, self.MAX_ENCRYPTED_BYTES)
            if encrypted[:4] != b"EPV1" or len(encrypted) < 32:
                raise ValueError("Invalid envelope")
            return self._cipher.decrypt(encrypted[4:16], encrypted[16:], associated)
        except FileNotFoundError:
            return None
        except Exception as exc:
            raise VaultError("账号加密数据无法读取；请重新连接该账号") from exc

    def delete(self, account_id: str) -> None:
        self._check_directory()
        for kind in self.KINDS:
            location, _ = self._location(account_id, kind)
            location.unlink(missing_ok=True)

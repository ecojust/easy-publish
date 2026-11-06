"""Encrypted account storage; the master key lives in the OS credential store."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import secrets
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(RuntimeError):
    pass


class Vault:
    KINDS = {"credentials", "session"}

    def __init__(self, data_dir: Path | str):
        self.directory = Path(data_dir).resolve() / "vault"
        self.ready = False
        self.message = "系统凭据库尚未准备好"
        self._cipher: AESGCM | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                self.directory.chmod(0o700)
            key = self._load_key()
            self._cipher = AESGCM(key)
            self.ready = True
            self.message = "账号凭据和会话已启用本机加密存储"
        except Exception:
            self.message = "系统凭据服务不可用或未解锁；请解锁系统钥匙串／凭据管理器后重启应用"

    def _load_key(self) -> bytes:
        test_key = os.environ.get("EASY_PUBLISH_TEST_KEY")
        if test_key is not None:
            if os.environ.get("EASY_PUBLISH_TESTING") != "1":
                raise VaultError("Test key requires explicit test mode")
            key = base64.b64decode(test_key, validate=True)
            if len(key) != 32:
                raise VaultError("Invalid test key")
            return key

        import keyring
        backend = keyring.get_keyring()
        # Do not silently select an optional plaintext/file keyring backend.
        allowed = ("keyring.backends.macOS", "keyring.backends.Windows", "keyring.backends.SecretService", "keyring.backends.kwallet", "keyring.backends.libsecret")
        candidates = getattr(backend, "backends", [backend])
        secure = [item for item in candidates if type(item).__module__.startswith(allowed) and item.priority > 0]
        if not secure:
            raise VaultError("No OS credential backend available")
        backend = max(secure, key=lambda item: item.priority)
        identity = "master-key:" + hashlib.sha256(str(self.directory).encode()).hexdigest()
        encoded = backend.get_password("easy-publish", identity)
        if encoded is None:
            key = secrets.token_bytes(32)
            backend.set_password("easy-publish", identity, base64.b64encode(key).decode("ascii"))
            # Read it back before allowing any ciphertext to be created.
            encoded = backend.get_password("easy-publish", identity)
        if not encoded:
            raise VaultError("Could not store encryption key")
        key = base64.b64decode(encoded, validate=True)
        if len(key) != 32:
            raise VaultError("Invalid encryption key")
        return key

    def _location(self, account_id: str, kind: str) -> tuple[Path, bytes]:
        if not isinstance(account_id, str) or not account_id or len(account_id) > 512 or kind not in self.KINDS:
            raise VaultError("Invalid account storage key")
        associated = (account_id + "\0" + kind).encode("utf-8")
        return self.directory / (hashlib.sha256(associated).hexdigest() + ".enc"), associated

    def put(self, account_id: str, kind: str, data: bytes) -> None:
        if not self.ready or self._cipher is None:
            raise VaultError(self.message)
        if not isinstance(data, bytes):
            raise TypeError("Vault data must be bytes")
        destination, associated = self._location(account_id, kind)
        nonce = secrets.token_bytes(12)
        encrypted = b"EPV1" + nonce + self._cipher.encrypt(nonce, data, associated)
        temporary = self.directory / (uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, account_id: str, kind: str) -> bytes | None:
        if not self.ready or self._cipher is None:
            raise VaultError(self.message)
        location, associated = self._location(account_id, kind)
        if not location.exists():
            return None
        try:
            encrypted = location.read_bytes()
            if encrypted[:4] != b"EPV1" or len(encrypted) < 32:
                raise ValueError("Invalid envelope")
            return self._cipher.decrypt(encrypted[4:16], encrypted[16:], associated)
        except Exception as exc:
            raise VaultError("账号加密数据无法读取；请重新连接该账号") from exc

    def delete(self, account_id: str) -> None:
        for kind in self.KINDS:
            location, _ = self._location(account_id, kind)
            location.unlink(missing_ok=True)

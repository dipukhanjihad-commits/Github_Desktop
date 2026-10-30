"""Credential storage with real protection at rest (M11).

Strategy, best available first:

1. **OS keyring** (Windows Credential Manager / macOS Keychain / Secret
   Service) when the ``keyring`` package is installed.
2. **DPAPI** (Windows) or file with ``0600`` (POSIX) for a local encrypted
   blob, when keyring is unavailable.
3. Migration from the legacy plaintext ``credentials.json``, which is deleted
   once its contents are safely re-stored.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Protocol

from . import paths

SERVICE = "project-scaffolder"


class _Backend(Protocol):
    name: str
    available: bool

    def get(self, account: str) -> str | None: ...
    def set(self, account: str, token: str) -> None: ...
    def delete(self, account: str) -> None: ...


class _KeyringBackend:
    name = "keyring"

    def __init__(self) -> None:
        try:
            import keyring  # noqa: F401
            from keyring.errors import NoKeyringError

            try:
                backend = keyring.get_keyring()
                # fail.Keyring is what keyring returns when nothing works.
                if backend.__class__.__module__.startswith("keyring.backends.fail"):
                    raise NoKeyringError("no usable keyring backend")
            except NoKeyringError:
                raise
            self._keyring = keyring
            self.available = True
        except Exception:
            self.available = False

    def get(self, account: str) -> str | None:
        try:
            return self._keyring.get_password(SERVICE, account)
        except Exception:
            return None

    def set(self, account: str, token: str) -> None:
        self._keyring.set_password(SERVICE, account, token)

    def delete(self, account: str) -> None:
        try:
            self._keyring.delete_password(SERVICE, account)
        except Exception:
            pass


class _DpapiBackend:
    """Windows DPAPI-protected JSON file, scoped to the current user."""

    name = "dpapi"
    available = False

    def __init__(self, path: Path) -> None:
        self.path = path
        self.available = sys.platform.startswith("win")
        self._data: dict[str, str] | None = None

    # -- crypto ------------------------------------------------------------

    @staticmethod
    def _protect(plain: bytes) -> bytes:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        def _blob(data: bytes) -> DATA_BLOB:
            buf = ctypes.create_string_buffer(data, len(data))
            return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

        crypt32 = ctypes.windll.crypt32
        out = DATA_BLOB()
        src = _blob(plain)
        # CRYPTPROTECT_UI_FORBIDDEN
        if not crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None,
                                        0x1, ctypes.byref(out)):
            raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    @staticmethod
    def _unprotect(cipher: bytes) -> bytes:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        def _blob(data: bytes) -> DATA_BLOB:
            buf = ctypes.create_string_buffer(data, len(data))
            return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

        crypt32 = ctypes.windll.crypt32
        out = DATA_BLOB()
        src = _blob(cipher)
        if not crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None,
                                          0x1, ctypes.byref(out)):
            raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    # -- storage -----------------------------------------------------------

    def _load(self) -> dict[str, str]:
        if self._data is not None:
            return self._data
        self._data = {}
        if not self.path.exists():
            return self._data
        try:
            raw = self.path.read_bytes()
            plain = self._unprotect(raw) if self.available else raw
            self._data = dict(json.loads(plain.decode("utf-8")))
        except Exception:
            self._data = {}
        return self._data

    def _save(self) -> None:
        payload = json.dumps(self._data or {}).encode("utf-8")
        blob = self._protect(payload) if self.available else payload
        if not self.available:
            blob = base64.b64encode(blob)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    def get(self, account: str) -> str | None:
        return self._load().get(account)

    def set(self, account: str, token: str) -> None:
        self._load()[account] = token
        self._save()

    def delete(self, account: str) -> None:
        data = self._load()
        if account in data:
            del data[account]
            self._save()


class CredentialStore:
    """Facade over the best available backend."""

    def __init__(self, root: Path | str | None = None,
                 prefer_keyring: bool = True) -> None:
        self.path = paths.credentials_path(root)
        self._cache: dict[str, str] = {}
        self._file_backend = _DpapiBackend(self.path)
        self._file_backend.available = True  # DPAPI when Windows, else 0600 file
        self._backend: _Backend = self._file_backend
        self._keyring: _KeyringBackend | None = None
        if prefer_keyring:
            keyring_backend = _KeyringBackend()
            if keyring_backend.available:
                self._keyring = keyring_backend
                self._backend = keyring_backend

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def get(self, account: str) -> str | None:
        if account in self._cache:
            return self._cache[account]
        token = self._backend.get(account)
        if not token and self._keyring is not None:
            token = self._file_backend.get(account)
        if token:
            self._cache[account] = token
        return token

    def set(self, account: str, token: str) -> None:
        token = (token or "").strip()
        if not token:
            raise ValueError("token must not be empty")
        self._backend.set(account, token)
        self._cache[account] = token

    def delete(self, account: str) -> None:
        self._cache.pop(account, None)
        self._backend.delete(account)
        self._file_backend.delete(account)

    def has(self, account: str) -> bool:
        return bool(self.get(account))

    def accounts(self) -> list[str]:
        found = set(self._cache)
        try:
            found |= set(self._file_backend._load())
        except Exception:
            pass
        return sorted(found)

    # ---------------------------------------------------------------- migration

    def migrate_legacy(self, legacy: Path | None = None) -> int:
        """Import tokens from the old plaintext credentials.json, then remove it."""
        source = legacy or (paths.legacy_dir() / "credentials.json")
        if not source.exists():
            return 0
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            tokens = payload.get("tokens", {}) if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, OSError):
            return 0
        count = 0
        for account, token in tokens.items():
            if isinstance(token, str) and token.strip() and not self.get(account):
                self.set(account, token)
                count += 1
        try:
            source.unlink()
        except OSError:
            pass
        return count

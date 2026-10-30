"""Cross-process locking so two app instances cannot corrupt memory.json (S13).

``ProjectMemory`` already uses a thread lock, which only protects a single
process. This module adds an OS-level exclusive lock (``msvcrt`` on Windows,
``fcntl`` elsewhere) guarding the read-modify-write cycle of the JSON store.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockTimeout(TimeoutError):
    """Raised when the lock could not be acquired in time."""


class _FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _acquire(self, handle) -> None:  # pragma: no cover - platform specific
        if sys.platform.startswith("win"):
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(self, handle) -> None:  # pragma: no cover - platform specific
        if sys.platform.startswith("win"):
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path | str, timeout: float = 10.0,
              poll: float = 0.05) -> Iterator[None]:
    """Acquire an exclusive advisory lock on ``path`` for the block's duration."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")  # noqa: SIM115 - lifetime spans the block
    lock = _FileLock(lock_path)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                lock._acquire(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"Could not lock {lock_path} within {timeout:.0f}s. "
                        "Another instance of the app may be running."
                    ) from None
                time.sleep(poll)
        try:
            yield
        finally:
            try:
                lock._release(handle)
            except OSError:
                pass
    finally:
        handle.close()


def pid_is_running(pid: int) -> bool:
    """Best-effort check that a process is still alive."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            # STILL_ACTIVE == 259
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

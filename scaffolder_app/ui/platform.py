"""Platform integration: HiDPI (S21), single-instance guard, window state (M17).

These helpers keep OS-specific setup in one place so the app shell stays
readable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt
from PySide6.QtWidgets import QApplication, QWidget

from .. import paths

_WINDOW_GROUP = "window"


def set_scale_policy() -> None:
    """Must run before QApplication is constructed (Qt requirement)."""
    try:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except Exception:
        pass


def configure_high_dpi(app: QApplication) -> None:
    """Enable crisp scaling on HiDPI/Retina displays (S21).

    Qt 6 handles device pixel ratios automatically; the rounding policy itself
    is set by :func:`set_scale_policy` before the application exists.
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes

            # PROCESS_PER_MONITOR_DPI_AWARE
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes

                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "ProjectScaffolder.Desktop.2")
        except Exception:
            pass


def screen_scale(app: QApplication) -> float:
    screen = app.primaryScreen()
    if not screen:
        return 1.0
    try:
        ratio = screen.devicePixelRatio()
        return max(1.0, min(2.0, float(ratio)))
    except Exception:
        return 1.0


class WindowState:
    """Persists geometry, splitter sizes, filters and the selection (M17)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.window_state_path()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self._data, dict):
                self._data = {}
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2),
                                 encoding="utf-8")
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    # -- convenience ------------------------------------------------------

    def restore_window(self, window: QWidget) -> None:
        raw = self._data.get("geometry")
        if raw:
            try:
                window.restoreGeometry(QByteArray.fromBase64(raw.encode("ascii")))
                return
            except Exception:
                pass
        window.resize(1280, 820)

    def capture_geometry(self, window: QWidget) -> None:
        try:
            raw = bytes(window.saveGeometry().toBase64()).decode("ascii")
            self._data["geometry"] = raw
            self._data["maximized"] = bool(window.isMaximized())
        except Exception:
            pass

    def restore_splitter(self, splitter, key: str = "main_splitter") -> None:
        sizes = self._data.get(key)
        if isinstance(sizes, list) and all(isinstance(s, int) for s in sizes):
            try:
                splitter.setSizes(sizes)
            except Exception:
                pass

    def capture_splitter(self, splitter, key: str = "main_splitter") -> None:
        try:
            self._data[key] = list(splitter.sizes())
        except Exception:
            pass


class SingleInstanceGuard:
    """Prevents two writers on the same memory.json (S13 companion)."""

    def __init__(self, app_name: str = "project-scaffolder") -> None:
        self._lock = None
        self._name = app_name
        self.acquired = False

    def acquire(self) -> bool:
        if not sys.platform.startswith("win"):
            try:
                from PySide6.QtCore import QLockFile
                from .. import paths
                lock_path = paths.data_dir() / ".instance.lock"
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                self._lock = QLockFile(str(lock_path))
                self._lock.setStaleLockTime(0)
                self.acquired = self._lock.tryLock(100)
                return self.acquired
            except Exception:
                self.acquired = True
                return True
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._mutex = kernel32.CreateMutexW(None, False, self._name)
            ERROR_ALREADY_EXISTS = 183
            self.acquired = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
            return self.acquired
        except Exception:
            self.acquired = True
            return True

    def release(self) -> None:
        if self._lock is not None:
            try:
                self._lock.unlock()
            except Exception:
                pass
        self._mutex = None


def restyle_tree(widget: QWidget) -> None:
    """Re-polish a widget tree after the global stylesheet changes (M16)."""
    for child in [widget, *widget.findChildren(QWidget)]:
        child.style().unpolish(child)
        child.style().polish(child)
        child.update()


def clear_settings() -> None:
    QSettings().clear()

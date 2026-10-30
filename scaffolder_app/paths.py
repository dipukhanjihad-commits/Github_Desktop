"""Per-platform locations for app data, and small OS helpers.

Data lives in the platform-correct place (S24) instead of a Unix-flavoured
``~/.scaffolder``:

* Windows  -> ``%LOCALAPPDATA%\\ProjectScaffolder``
* macOS    -> ``~/Library/Application Support/ProjectScaffolder``
* Linux    -> ``$XDG_DATA_HOME/project-scaffolder`` (fallback ``~/.local/share``)

The legacy ``~/.scaffolder`` directory is still read for migration.

Every function accepts an optional ``root`` so tests can redirect all paths
without touching the real user profile.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "ProjectScaffolder"
APP_SLUG = "project-scaffolder"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None


def _default_root() -> Path:
    if is_windows():
        base = _env_path("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return base / APP_NAME
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = _env_path("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return base / APP_SLUG


def data_dir(root: Path | str | None = None) -> Path:
    """Return the writable base directory for app data."""
    if root is not None:
        return Path(root)
    override = _env_path("SCAFFOLDER_DATA_DIR")
    return override if override else _default_root()


def config_dir(root: Path | str | None = None) -> Path:
    """Return the config directory (window state, ui preferences)."""
    if root is not None:
        return Path(root)
    override = _env_path("SCAFFOLDER_CONFIG_DIR")
    if override:
        return override
    if is_windows():
        base = _env_path("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return base / APP_NAME
    if is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = _env_path("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return base / APP_SLUG


def legacy_dir() -> Path:
    """The pre-migration location (``~/.scaffolder``)."""
    return Path.home() / ".scaffolder"


def memory_path(root: Path | str | None = None) -> Path:
    return data_dir(root) / "memory.json"


def credentials_path(root: Path | str | None = None) -> Path:
    return data_dir(root) / "credentials.json"


def window_state_path(root: Path | str | None = None) -> Path:
    return config_dir(root) / "window_state.json"


def trash_dir(root: Path | str | None = None) -> Path:
    return data_dir(root) / "trash"


def logs_dir(root: Path | str | None = None) -> Path:
    return data_dir(root) / "logs"


def ensure_dirs(root: Path | str | None = None) -> None:
    for d in (data_dir(root), config_dir(root), trash_dir(root), logs_dir(root)):
        d.mkdir(parents=True, exist_ok=True)


def default_projects_path() -> Path:
    return Path.home() / "projects"


def os_open_path(path: str | Path) -> None:
    """Open a file or folder with the OS default handler."""
    target = str(path)
    if is_windows():
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
    elif is_macos():
        subprocess.Popen(["open", target], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def which(program: str) -> str | None:
    return shutil.which(program)

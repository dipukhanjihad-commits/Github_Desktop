"""Editor discovery and launching (M15/S6/N11).

VS Code is the default but the same UX backs Cursor, Zed, JetBrains IDEs and
the OS default. Launching is detached so the UI never blocks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths
from .errors import ScaffolderError

EDITOR_LABELS = {
    "vscode": "VS Code",
    "cursor": "Cursor",
    "zed": "Zed",
    "vscodium": "VSCodium",
    "idea": "IntelliJ IDEA",
    "custom": "Custom command",
    "system": "System default",
}

#: CLI names to probe on PATH, per editor id.
_CLI_CANDIDATES = {
    "vscode": ["code", "code.cmd", "code-insiders"],
    "cursor": ["cursor", "cursor.cmd"],
    "zed": ["zed", "zed.exe"],
    "vscodium": ["codium", "codium.cmd"],
    "idea": ["idea", "idea64.exe", "idea.sh"],
}


def _windows_candidates(editor: str) -> list[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "C:/Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")
    mapping = {
        "vscode": [
            Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe",
            Path(program_files) / "Microsoft VS Code" / "Code.exe",
            Path(program_files_x86) / "Microsoft VS Code" / "Code.exe",
        ],
        "cursor": [
            Path(local) / "Programs" / "cursor" / "Cursor.exe",
            Path(program_files) / "Cursor" / "Cursor.exe",
        ],
        "zed": [
            Path(local) / "Programs" / "Zed" / "zed.exe",
            Path(program_files) / "Zed" / "zed.exe",
        ],
        "vscodium": [
            Path(local) / "Programs" / "VSCodium" / "VSCodium.exe",
            Path(program_files) / "VSCodium" / "VSCodium.exe",
        ],
    }
    return mapping.get(editor, [])


def _mac_candidates(editor: str) -> list[Path]:
    apps = {
        "vscode": ["Visual Studio Code.app", "Visual Studio Code - Insiders.app"],
        "cursor": ["Cursor.app"],
        "zed": ["Zed.app"],
    }
    out = []
    for app in apps.get(editor, []):
        out.append(Path("/Applications") / app / "Contents" / "Resources" / "app" /
                   "bin" / app.replace(".app", "").lower())
    return out


def _linux_candidates(editor: str) -> list[Path]:
    mapping = {
        "vscode": ["/usr/share/code/code", "/usr/bin/code", "/snap/bin/code",
                   "/var/lib/flatpak/exports/bin/com.visualstudio.code"],
        "cursor": ["/usr/bin/cursor", "/opt/cursor/cursor"],
        "zed": ["/usr/bin/zed", "/snap/bin/zed"],
        "vscodium": ["/usr/bin/codium", "/snap/bin/codium"],
    }
    return [Path(p) for p in mapping.get(editor, [])]


class EditorLauncher:
    def __init__(self, editor: str = "vscode",
                 custom_command: str = "") -> None:
        self.editor = editor or "vscode"
        self.custom_command = custom_command.strip()

    def _platform_paths(self) -> list[Path]:
        if paths.is_windows():
            return _windows_candidates(self.editor)
        if paths.is_macos():
            return _mac_candidates(self.editor)
        return _linux_candidates(self.editor)

    def resolve(self) -> str | None:
        """Return the executable path, or None if this editor is unavailable."""
        if self.editor == "system":
            return "system"
        if self.editor == "custom":
            return self.custom_command.split()[0] if self.custom_command else None
        for cli in _CLI_CANDIDATES.get(self.editor, []):
            found = shutil.which(cli)
            if found:
                return found
        for candidate in self._platform_paths():
            try:
                if candidate.exists():
                    return str(candidate)
            except OSError:
                continue
        return None

    def available(self) -> bool:
        return self.resolve() is not None

    def label(self) -> str:
        return EDITOR_LABELS.get(self.editor, self.editor)

    def open(self, path: Path | str, goto_file: str | None = None) -> None:
        """Open ``path``; when ``goto_file`` is given, jump to it."""
        executable = self.resolve()
        if executable is None:
            raise ScaffolderError(
                f"{self.label()} was not found.",
                hint=f"Install {self.label()} or choose another editor in Settings.")
        target = Path(path).expanduser()
        if executable == "system":
            paths.os_open_path(target)
            return

        args: list[str]
        if self.editor == "custom" and self.custom_command:
            parts = self.custom_command.split()
            args = parts + [str(target)]
        elif self.editor in ("vscode", "cursor", "vscodium", "zed"):
            # --goto is a flag: the file:line value must follow it directly and
            # the folder must be passed afterwards.
            args = [executable]
            if self.editor != "zed":
                args.append("--new-window")
            if goto_file:
                args += ["--goto", goto_file]
            args.append(str(target))
        else:
            args = [executable, str(target)]
        self._popen(args)

    def open_file(self, project_root: Path, rel_path: str, line: int = 1) -> None:
        target = (Path(project_root) / rel_path).resolve()
        if not target.exists():
            raise ScaffolderError(f"File not found: {rel_path}")
        self.open(project_root, goto_file=f"{target}:{line}")

    def open_terminal(self, path: Path | str) -> None:
        """Open a terminal at ``path`` (N1-adjacent convenience)."""
        target = str(Path(path))
        try:
            if paths.is_windows():
                subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", "cd", "/d", target],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            elif paths.is_macos():
                subprocess.Popen(["open", "-a", "Terminal", target])
            else:
                subprocess.Popen(["x-terminal-emulator"], cwd=target)
        except OSError as exc:
            raise ScaffolderError(f"Could not open a terminal: {exc}") from exc

    @staticmethod
    def _popen(args: list[str]) -> None:
        try:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=not sys.platform.startswith("win"),
            )
        except OSError as exc:
            raise ScaffolderError(f"Could not launch the editor: {exc}") from exc


def detect_available_editors() -> dict[str, bool]:
    """Report which editors are installed, for settings and first-run UX."""
    return {key: EditorLauncher(key).available()
            for key in ("vscode", "cursor", "zed", "vscodium", "idea")}

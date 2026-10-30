"""Project-type detection and environment bootstrap (S12).

Long-running installers stream their output line-by-line into a callback so the
UI can show real progress instead of a frozen label.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable

from .errors import ScaffolderError, ToolMissingError
from .git_ops import require_tool

Progress = Callable[[str], None]

ENV_KINDS = ("python", "node", "rust", "go", "dotnet", "ruby", "none")

INSTALL_HINTS = {
    "python": "Install Python 3 from python.org (tick 'Add to PATH').",
    "npm": "Install Node.js from nodejs.org.",
    "cargo": "Install Rust via https://rustup.rs.",
    "go": "Install Go from go.dev/dl.",
    "dotnet": "Install the .NET SDK from dotnet.microsoft.com.",
    "bundle": "Install Ruby, then run `gem install bundler`.",
}

_MARKERS = [
    ("python", ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
                "Pipfile", "poetry.lock")),
    ("node", ("package.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")),
    ("rust", ("Cargo.toml",)),
    ("go", ("go.mod",)),
    ("dotnet", ("*.csproj", "*.fsproj", "*.sln")),
    ("ruby", ("Gemfile",)),
]

_ENV_DIRS = {
    "python": (".venv", "venv"),
    "node": ("node_modules",),
    "rust": ("target",),
    "go": ("vendor", "go.sum"),
    "dotnet": ("bin", "obj"),
    "ruby": ("vendor/bundle",),
}

#: How deep the content-based fallback detector walks (kept small for speed).
_SCAN_DEPTH = 3
_SCAN_SKIP = {"node_modules", "target", "dist", "build", ".venv", "venv",
              "__pycache__", "vendor"}


class EnvDetector:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def detect(self) -> str:
        if not self.root.is_dir():
            return "none"
        for kind, markers in _MARKERS:
            for marker in markers:
                if "*" in marker:
                    if any(self.root.glob(marker)):
                        return kind
                elif (self.root / marker).exists():
                    return kind
        # Fall back to a content scan, bounded so a huge tree stays fast.
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel = Path(dirpath).relative_to(self.root)
            if len(rel.parts) >= _SCAN_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d not in _SCAN_SKIP and not d.startswith(".")]
            if any(name.endswith((".py", ".pyw")) for name in filenames):
                return "python"
        return "none"

    def has_env(self) -> bool:
        kind = self.detect()
        if kind == "none":
            return True
        return any((self.root / name).exists() for name in _ENV_DIRS.get(kind, ()))

    def describe(self) -> tuple[str, bool]:
        return self.detect(), self.has_env()


def _stream(args: list[str], cwd: Path, progress: Progress,
            env: dict[str, str] | None = None) -> int:
    """Run a command, forwarding output lines to ``progress``."""
    import subprocess

    creationflags = (subprocess.CREATE_NO_WINDOW
                     if sys.platform.startswith("win") else 0)
    try:
        proc = subprocess.Popen(
            args, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1,
            encoding="utf-8", errors="replace",
            creationflags=creationflags,
        )
    except FileNotFoundError:
        raise ToolMissingError(args[0]) from None
    except OSError as exc:
        raise ScaffolderError(f"Could not run {args[0]}: {exc}") from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip()
        if text:
            progress(text)
    return proc.wait()


class EnvSetup:
    """Creates the language environment for a project folder."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.detector = EnvDetector(self.root)

    def setup(self, progress: Progress | None = None) -> str:
        log = progress or (lambda _m: None)
        kind = self.detector.detect()
        if kind == "none":
            raise ScaffolderError(
                "Could not detect a project type.",
                hint="Looked for pyproject.toml, package.json, Cargo.toml, "
                     "go.mod, *.csproj and Gemfile.")
        if self.detector.has_env():
            log("Environment already exists.")
            return kind
        handler = getattr(self, f"_setup_{kind}", None)
        if handler is None:
            raise ScaffolderError(f"No setup routine for '{kind}' projects.")
        handler(log)
        log("Environment ready.")
        return kind

    # ------------------------------------------------------------ python

    def _python_executable(self) -> str:
        for name in ("python3", "python", "py"):
            found = require_tool(name, INSTALL_HINTS["python"])
            if found:
                return found
        raise ToolMissingError("python", INSTALL_HINTS["python"])

    def _venv_dir(self) -> Path:
        return self.root / ".venv"

    def _venv_python(self) -> Path:
        if os.name == "nt":
            return self._venv_dir() / "Scripts" / "python.exe"
        return self._venv_dir() / "bin" / "python"

    def _setup_python(self, log: Progress) -> None:
        python = self._python_executable()
        log(f"Creating virtual environment with {python} \u2026")
        code = _stream([python, "-m", "venv", ".venv"], self.root, log)
        if code != 0:
            raise ScaffolderError("Could not create the virtual environment.",
                                  hint="Ensure the venv module is available.")
        venv_python = self._venv_python()
        log("Upgrading pip \u2026")
        _stream([str(venv_python), "-m", "pip", "install", "--upgrade", "pip",
                 "--disable-pip-version-check"], self.root, log)
        if (self.root / "requirements.txt").exists():
            log("Installing requirements.txt \u2026")
            code = _stream([str(venv_python), "-m", "pip", "install", "-r",
                            "requirements.txt", "--disable-pip-version-check"],
                           self.root, log)
            if code != 0:
                log("\u26a0 Some requirements failed to install \u2014 see the log above.")
        elif (self.root / "pyproject.toml").exists():
            log("Installing project in editable mode \u2026")
            _stream([str(venv_python), "-m", "pip", "install", "-e", ".",
                     "--disable-pip-version-check"], self.root, log)

    # -------------------------------------------------------------- node

    def _setup_node(self, log: Progress) -> None:
        if (self.root / "pnpm-lock.yaml").exists() and _has("pnpm"):
            cmd = ["pnpm", "install"]
        elif (self.root / "yarn.lock").exists() and _has("yarn"):
            cmd = ["yarn", "install"]
        else:
            cmd = [require_tool("npm", INSTALL_HINTS["npm"]), "install"]
        log("Running " + " ".join(cmd) + " \u2026")
        if _stream(cmd, self.root, log) != 0:
            raise ScaffolderError("Dependency installation failed.")

    # -------------------------------------------------------------- rust

    def _setup_rust(self, log: Progress) -> None:
        cargo = require_tool("cargo", INSTALL_HINTS["cargo"])
        log("Running cargo fetch \u2026")
        _stream([cargo, "fetch"], self.root, log)
        log("Building (cargo build) \u2026")
        if _stream([cargo, "build"], self.root, log) != 0:
            raise ScaffolderError("cargo build failed.")

    # ---------------------------------------------------------------- go

    def _setup_go(self, log: Progress) -> None:
        go = require_tool("go", INSTALL_HINTS["go"])
        log("Downloading Go modules \u2026")
        if _stream([go, "mod", "download"], self.root, log) != 0:
            raise ScaffolderError("go mod download failed.")

    # ------------------------------------------------------------ dotnet

    def _setup_dotnet(self, log: Progress) -> None:
        dotnet = require_tool("dotnet", INSTALL_HINTS["dotnet"])
        log("Running dotnet restore \u2026")
        if _stream([dotnet, "restore"], self.root, log) != 0:
            raise ScaffolderError("dotnet restore failed.")

    # -------------------------------------------------------------- ruby

    def _setup_ruby(self, log: Progress) -> None:
        bundle = require_tool("bundle", INSTALL_HINTS["bundle"])
        log("Running bundle install \u2026")
        if _stream([bundle, "install"], self.root, log) != 0:
            raise ScaffolderError("bundle install failed.")


def _has(program: str) -> bool:
    import shutil

    return shutil.which(program) is not None


class RunTask:
    """Run an arbitrary command in a project with streamed output (N2)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def detect_tasks(self) -> dict[str, list[str]]:
        tasks: dict[str, list[str]] = {}
        if (self.root / "pyproject.toml").exists() or (self.root / "pytest.ini").exists() \
                or (self.root / "tests").is_dir():
            tasks["pytest"] = [sys.executable, "-m", "pytest", "-q"]
        if (self.root / "package.json").exists():
            tasks["npm test"] = ["npm", "test"]
            tasks["npm run lint"] = ["npm", "run", "lint"]
        if (self.root / "Cargo.toml").exists():
            tasks["cargo test"] = ["cargo", "test"]
        if (self.root / "go.mod").exists():
            tasks["go test"] = ["go", "test", "./..."]
        if (self.root / "Makefile").exists():
            tasks["make"] = ["make"]
        return tasks

    def run(self, command: list[str], progress: Progress) -> int:
        return _stream(command, self.root, progress)


class BackgroundStream:
    """Bridge a streaming setup into a worker thread + Qt-safe signal."""

    def __init__(self, fn: Callable[[Progress], object],
                 emit: Callable[[str], None]) -> None:
        self._fn = fn
        self._emit = emit
        self.result: object = None
        self.error: BaseException | None = None

    def start(self) -> threading.Thread:
        def _target() -> None:
            try:
                self.result = self._fn(self._emit)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the UI thread
                self.error = exc

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        return thread

"""Shared fixtures: isolated data dirs and throw-away git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scaffolder_app.credentials import CredentialStore
from scaffolder_app.project_memory import ProjectMemory
from scaffolder_app.scaffolder import Scaffolder

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "appdata"
    root.mkdir()
    return root


@pytest.fixture()
def memory(data_root: Path) -> ProjectMemory:
    return ProjectMemory(path=data_root / "memory.json", use_file_lock=False)


@pytest.fixture()
def credentials(data_root: Path) -> CredentialStore:
    return CredentialStore(data_root, prefer_keyring=False)


@pytest.fixture()
def service(memory: ProjectMemory, credentials: CredentialStore,
            data_root: Path) -> Scaffolder:
    return Scaffolder(memory, credentials, root=data_root)


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                            text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test Author")
    git(path, "config", "user.email", "test@example.com")
    return path


def make_template(root: Path, name: str = "basic") -> Path:
    template = root / "templates" / name
    (template / "src").mkdir(parents=True)
    (template / "README.md").write_text("# {{project_name}}\n", encoding="utf-8")
    (template / "src" / "{{module_name}}.py").write_text(
        "NAME = '{{project_name}}'\n", encoding="utf-8")
    return template

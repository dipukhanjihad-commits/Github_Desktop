"""Regression tests for the MUST-level defects found in the audit.

Each test maps to a numbered item from the analysis so a future regression
fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scaffolder_app.errors import (
    AlreadyExistsError, MissingFolderError, NotARepositoryError, ValidationError,
)
from scaffolder_app.project_memory import (
    SCHEMA_VERSION, ProjectMemory, validate_folder_name,
)
from scaffolder_app.template_engine import (
    TemplateEngine, safe_join, sanitize_placeholder_value,
)

from .conftest import init_repo, make_template


# ---------------------------------------------------------------------------
# M1: the dependency story is coherent — no Qt import in the core.
# ---------------------------------------------------------------------------

def test_core_has_no_qt_dependency():
    package = Path(__file__).resolve().parent.parent / "scaffolder_app"
    offenders = []
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import PySide6" in text or "from PySide6" in text:
            if "ui" not in path.parts:
                offenders.append(path.name)
    assert not offenders, f"core modules must not import Qt: {offenders}"


# ---------------------------------------------------------------------------
# M2: deleting a project must never touch a *different* project's folder.
# ---------------------------------------------------------------------------

def test_delete_project_does_not_touch_same_prefix_project(service, memory,
                                                           data_root):
    first = init_repo(data_root / "work" / "demo")
    (first / "a.txt").write_text("keep me", encoding="utf-8")
    second_dir = data_root / "work" / "demo-v2"

    memory.add_project(name="demo", path=first)
    memory.add_project(name="demo-v2", path=second_dir)

    result = service.delete_project("demo-v2")

    assert first.exists(), "the unrelated 'demo' folder must survive"
    assert (first / "a.txt").read_text(encoding="utf-8") == "keep me"
    assert [p["name"] for p in memory.list_projects()] == ["demo"]
    assert result["trashed"] is None or not Path(result["trashed"]).exists()


def test_delete_moves_only_target_folder_to_trash(service, memory, data_root):
    folder = init_repo(data_root / "work" / "alpha")
    memory.add_project(name="alpha", path=folder)
    other = data_root / "work" / "alpha-old"
    other.mkdir()
    memory.add_project(name="alpha-old", path=other)

    result = service.delete_project("alpha")

    assert not folder.exists()
    assert other.exists()
    assert result["trashed"] and Path(result["trashed"]).exists()


# ---------------------------------------------------------------------------
# M3: placeholder values must not be able to escape the destination folder.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["../../ESCAPED", "..\\..\\ESCAPED", "..", "."])
def test_placeholder_traversal_is_rejected(value):
    with pytest.raises(ValidationError):
        sanitize_placeholder_value(value, "project_name")


def test_placeholder_filename_cannot_escape_destination(tmp_path):
    template = tmp_path / "tpl"
    template.mkdir()
    (template / "{{project_name}}.txt").write_text("x", encoding="utf-8")
    dest = tmp_path / "out" / "proj"
    engine = TemplateEngine(template)

    with pytest.raises(ValidationError):
        engine.apply(dest, {"project_name": "../../ESCAPED"})

    assert not (tmp_path / "out" / "ESCAPED.txt").exists()
    assert not (tmp_path / "ESCAPED.txt").exists()


def test_illegal_characters_are_sanitised():
    assert sanitize_placeholder_value("a/b", "k") == "a-b"
    assert sanitize_placeholder_value("my:proj?", "k") == "my-proj-"
    assert sanitize_placeholder_value("  spaced  ", "k") == "spaced"


def test_safe_join_rejects_absolute_and_parent_parts(tmp_path):
    for parts in (("..", "x"), ("/etc",), ("C:\\Windows",)):
        with pytest.raises(ValidationError):
            safe_join(tmp_path, parts)


def test_validate_folder_name_rejects_dangerous_names():
    for bad in ["", "   ", "a/b", "a\\b", "..", ".", "CON", "aux.txt", "name.",
                "a:b", "x\x00y"]:
        with pytest.raises(ValidationError):
            validate_folder_name(bad)
    assert validate_folder_name(" good-name ") == "good-name"


# ---------------------------------------------------------------------------
# M5: non-GitHub / SSH remotes must not be silently dropped.
# ---------------------------------------------------------------------------

def test_ssh_remote_is_parsed_and_kept(service, memory, data_root):
    repo = init_repo(data_root / "work" / "sshy")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    from .conftest import git

    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    git(repo, "remote", "add", "origin", "git@github.com:alice/sshy.git")

    project = service.link_existing_folder(repo, "sshy")

    assert project["repo_url"] == "https://github.com/alice/sshy"
    assert project["owner"] == "alice"
    assert project["remote_kind"] == "github"


def test_non_github_remote_is_preserved(service, memory, data_root):
    repo = init_repo(data_root / "work" / "gitlabproj")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    from .conftest import git

    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    git(repo, "remote", "add", "origin", "https://gitlab.com/team/proj.git")

    project = service.link_existing_folder(repo, "gitlabproj")

    assert project["repo_url"] == "https://gitlab.com/team/proj"
    assert project["remote_kind"] == "other"
    assert any("not GitHub" in e["message"] for e in memory.get_errors())


# ---------------------------------------------------------------------------
# M8: projects whose folders vanished are flagged, not crashed on.
# ---------------------------------------------------------------------------

def test_missing_folder_detection_and_problems_list(service, memory, data_root):
    folder = init_repo(data_root / "work" / "gone")
    memory.add_project(name="gone", path=folder)
    import shutil

    shutil.rmtree(folder)

    problems = service.validate_paths()
    assert [p["name"] for p in problems] == ["gone"]
    assert memory.get_project("gone")["missing"] is True

    with pytest.raises(MissingFolderError):
        service.git("gone")


def test_relink_project_points_at_new_location(service, memory, data_root):
    memory.add_project(name="moved", path=data_root / "missing")
    new_home = init_repo(data_root / "new" / "moved")
    service.relink_project("moved", new_home)
    assert memory.get_project("moved")["path"] == str(new_home.resolve())
    assert memory.get_project("moved")["missing"] is False


# ---------------------------------------------------------------------------
# M9: version switching must not require the user to commit or discard.
# ---------------------------------------------------------------------------

def test_switch_version_stashes_and_restores_work(service, memory, data_root):
    template = make_template(data_root)
    memory.add_template(name="basic", path=template, ignore=[".git"],
                        placeholders={"module_name": ""})
    service.create_project("proj", "basic", data_root / "work" / "proj",
                           {"module_name": "proj", "project_name": "proj"},
                           account=None, create_repo=False)
    service.create_version("proj", "v2")
    service.switch_version("proj", "v1")

    dirty = data_root / "work" / "proj" / "README.md"
    dirty.write_text("work in progress\n", encoding="utf-8")

    result = service.switch_version("proj", "v2")

    assert result["switched"] is True
    assert result["stash"], "dirty work should have been stashed"
    assert dirty.read_text(encoding="utf-8") == "work in progress\n"


def test_switch_version_with_abort_strategy_raises(service, memory, data_root):
    from scaffolder_app.errors import DirtyWorkTreeError

    template = make_template(data_root)
    memory.add_template(name="basic", path=template, ignore=[".git"])
    service.create_project("proj", "basic", data_root / "work" / "proj",
                           {"module_name": "p", "project_name": "proj"},
                           account=None, create_repo=False)
    service.create_version("proj", "v2")
    service.switch_version("proj", "v1")
    (data_root / "work" / "proj" / "README.md").write_text("x2", encoding="utf-8")

    with pytest.raises(DirtyWorkTreeError):
        service.switch_version("proj", "v2", strategy="abort")


# ---------------------------------------------------------------------------
# M13 / S13: storage robustness.
# ---------------------------------------------------------------------------

def test_corrupt_memory_is_quarantined_not_lost(data_root):
    path = data_root / "memory.json"
    path.write_text("{ not json", encoding="utf-8")
    memory = ProjectMemory(path=path, use_file_lock=False)
    assert memory.load_warnings
    backups = list(data_root.glob("memory.json.corrupt-*"))
    assert backups, "the unreadable file must be preserved"
    assert "not json" in backups[0].read_text(encoding="utf-8")


def test_v2_name_keyed_memory_migrates_to_ids(data_root):
    legacy = {
        "schema_version": 2,
        "settings": {},
        "templates": {},
        "projects": {
            "hello": {"name": "hello", "path": "/tmp/hello",
                      "versions": {"v1": {"label": "v1", "branch": "main"}}},
        },
        "accounts": {},
        "errors": [],
    }
    path = data_root / "memory.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    memory = ProjectMemory(path=path, use_file_lock=False)

    assert memory.snapshot()["schema_version"] == SCHEMA_VERSION
    projects = memory.list_projects()
    assert [p["name"] for p in projects] == ["hello"]
    assert projects[0]["id"], "projects must gain a stable id"
    assert list(memory.snapshot()["projects"]) != ["hello"]


def test_duplicate_project_name_rejected(memory, data_root):
    memory.add_project(name="dup", path=data_root / "a")
    with pytest.raises(AlreadyExistsError):
        memory.add_project(name="dup", path=data_root / "b")


def test_same_folder_cannot_be_added_twice(memory, data_root):
    folder = data_root / "same"
    folder.mkdir()
    memory.add_project(name="one", path=folder)
    with pytest.raises(AlreadyExistsError):
        memory.add_project(name="two", path=folder / "nested")


def test_atomic_save_leaves_valid_json(data_root):
    memory = ProjectMemory(path=data_root / "memory.json", use_file_lock=False)
    memory.add_project(name="p", path=data_root / "p")
    payload = json.loads((data_root / "memory.json").read_text(encoding="utf-8"))
    assert "p" in [p["name"] for p in payload["projects"].values()]


def test_rename_project_keeps_versions(memory, data_root):
    memory.add_project(name="old", path=data_root / "old")
    memory.add_version("old", "v1", branch="main")
    memory.rename_project("old", "new")
    proj = memory.get_project("new")
    assert proj is not None and "v1" in proj["versions"]
    assert memory.get_project("old") is None


# ---------------------------------------------------------------------------
# S12 / env detection.
# ---------------------------------------------------------------------------

def test_env_detection_and_has_env(tmp_path):
    from scaffolder_app.env_setup import EnvDetector

    root = tmp_path / "proj"
    root.mkdir()
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    detector = EnvDetector(root)
    assert detector.detect() == "python"
    assert detector.has_env() is False
    (root / ".venv").mkdir()
    assert detector.has_env() is True

    node = tmp_path / "node"
    node.mkdir()
    (node / "package.json").write_text("{}", encoding="utf-8")
    assert EnvDetector(node).detect() == "node"
    assert EnvDetector(tmp_path).detect() == "none"


# ---------------------------------------------------------------------------
# S26: commit message hygiene.
# ---------------------------------------------------------------------------

def test_commit_message_cleaning():
    from scaffolder_app.scaffolder import clean_commit_message

    assert clean_commit_message("  title  \n\nbody\n\n") == "title\n\nbody"
    assert clean_commit_message("t\r\nb") == "t\n\nb"
    assert clean_commit_message("x\n# comment\n\ny") == "x\n\ny"
    with pytest.raises(ValidationError):
        clean_commit_message("   \n  \n")


def test_commit_rejects_empty_tree(service, memory, data_root):
    template = make_template(data_root)
    memory.add_template(name="basic", path=template, ignore=[".git"])
    service.create_project("p", "basic", data_root / "work" / "p",
                           {"module_name": "p", "project_name": "p"},
                           account=None, create_repo=False)
    with pytest.raises(ValidationError):
        service.commit("p", "nothing to do")


def test_link_existing_folder_requires_git(service, data_root):
    plain = data_root / "plain"
    plain.mkdir()
    with pytest.raises(NotARepositoryError):
        service.link_existing_folder(plain, "plain")


def test_trash_restore_round_trip(service, memory, data_root):
    folder = init_repo(data_root / "work" / "cycler")
    (folder / "keep.txt").write_text("hello", encoding="utf-8")
    from .conftest import git

    git(folder, "add", ".")
    git(folder, "commit", "-m", "init")
    memory.add_project(name="cycler", path=folder)

    result = service.delete_project("cycler")
    stamp = Path(result["trashed"]).name
    assert not folder.exists()

    restored = service.restore_from_trash(stamp)

    assert restored["name"] == "cycler"
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "hello"
    assert memory.get_project("cycler") is not None

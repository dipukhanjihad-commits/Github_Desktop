"""Behavioural tests for git-backed project/version flows."""

from __future__ import annotations

import pytest

from scaffolder_app.errors import (
    AlreadyExistsError, RemoteUnsupportedError, ValidationError,
)
from scaffolder_app.github_api import parse_remote, normalize_clone_url
from scaffolder_app.project_memory import RepoState

from .conftest import make_template


def _new_project(service, memory, data_root, name="proj"):
    template = make_template(data_root)
    try:
        memory.add_template(name="basic", path=template, ignore=[".git"],
                            placeholders={"module_name": ""})
    except Exception:
        pass
    return service.create_project(
        name, "basic", data_root / "work" / name,
        {"module_name": name, "project_name": name},
        account=None, create_repo=False)


def test_create_project_layout(service, memory, data_root):
    project = _new_project(service, memory, data_root)
    root = data_root / "work" / "proj"
    assert project["repo_state"] == RepoState.LOCAL_ONLY.value
    assert list(project["versions"]) == ["v1"]
    assert (root / "README.md").read_text(encoding="utf-8").startswith("# proj")
    assert (root / "src" / "proj.py").exists()
    assert (root / ".git").exists()
    assert service.git("proj").current_branch() == "main"


def test_create_project_rejects_non_empty_dest(service, memory, data_root):
    dest = data_root / "work" / "busy"
    dest.mkdir(parents=True)
    (dest / "existing.txt").write_text("x", encoding="utf-8")
    template = make_template(data_root)
    memory.add_template(name="basic", path=template, ignore=[".git"])
    with pytest.raises(ValidationError):
        service.create_project("busy", "basic", dest, {}, account=None,
                               create_repo=False)


def test_commit_diff_and_history(service, memory, data_root):
    _new_project(service, memory, data_root)
    changed = data_root / "work" / "proj" / "README.md"
    changed.write_text("# proj\n\nmore\n", encoding="utf-8")

    changes = service.get_changes("proj")
    assert any(c["path"] == "README.md" and c["kind"] == "modified"
               for c in changes)
    assert "+more" in service.diff("proj", "README.md")

    info = service.commit("proj", "add a line")
    assert info["sha"]
    subjects = [c["subject"] for c in service.history("proj")]
    assert subjects[0] == "add a line"
    assert service.get_changes("proj") == []


def test_stage_and_unstage(service, memory, data_root):
    _new_project(service, memory, data_root)
    new_file = data_root / "work" / "proj" / "notes.txt"
    new_file.write_text("hello", encoding="utf-8")

    service.stage("proj", ["notes.txt"])
    entry = {c["path"]: c for c in service.get_changes("proj")}["notes.txt"]
    assert entry["staged"] is True

    service.unstage("proj", ["notes.txt"])
    entry = {c["path"]: c for c in service.get_changes("proj")}["notes.txt"]
    assert entry["staged"] is False


def test_commit_only_selected_paths(service, memory, data_root):
    _new_project(service, memory, data_root)
    root = data_root / "work" / "proj"
    (root / "one.txt").write_text("1", encoding="utf-8")
    (root / "two.txt").write_text("2", encoding="utf-8")

    service.commit("proj", "only one", paths=["one.txt"], stage_all=False)

    remaining = {c["path"] for c in service.get_changes("proj")}
    assert "one.txt" not in remaining
    assert "two.txt" in remaining


def test_discard_restores_tracked_file(service, memory, data_root):
    _new_project(service, memory, data_root)
    readme = data_root / "work" / "proj" / "README.md"
    readme.write_text("broken", encoding="utf-8")
    service.discard("proj", ["README.md"])
    assert readme.read_text(encoding="utf-8").startswith("# proj")


def test_undo_last_commit_keeps_changes(service, memory, data_root):
    _new_project(service, memory, data_root)
    readme = data_root / "work" / "proj" / "README.md"
    readme.write_text("second version", encoding="utf-8")
    service.commit("proj", "second commit")
    count = len(service.history("proj"))

    service.undo_last_commit("proj", keep_changes=True)

    assert len(service.history("proj")) == count - 1
    assert readme.read_text(encoding="utf-8") == "second version"


def test_create_version_branches_from_parent(service, memory, data_root):
    _new_project(service, memory, data_root)
    version = service.create_version("proj", "v2", parent_label="v1")
    assert version["branch"] == "version/v2"
    assert version["parent"] == "v1"
    project = memory.get_project("proj")
    assert project["current_version"] == "v2"
    assert service.git("proj").current_branch() == "version/v2"


def test_create_duplicate_version_rejected(service, memory, data_root):
    _new_project(service, memory, data_root)
    service.create_version("proj", "v2")
    with pytest.raises(AlreadyExistsError):
        service.create_version("proj", "v2")


def test_delete_current_version_switches_first(service, memory, data_root):
    _new_project(service, memory, data_root)
    service.create_version("proj", "v2")
    result = service.delete_version("proj", "v2", remove_remote=False)
    assert result["switched_to"] == "v1"
    project = memory.get_project("proj")
    assert list(project["versions"]) == ["v1"]
    assert service.git("proj").current_branch() == "main"


def test_cannot_delete_only_version(service, memory, data_root):
    _new_project(service, memory, data_root)
    with pytest.raises(ValidationError):
        service.delete_version("proj", "v1", remove_remote=False)


def test_merge_version_locally(service, memory, data_root):
    _new_project(service, memory, data_root)
    service.create_version("proj", "v2")
    (data_root / "work" / "proj" / "feature.txt").write_text("f", encoding="utf-8")
    service.commit("proj", "feature work")
    service.switch_version("proj", "v1")

    service.merge_version("proj", "v2", "v1", push=False)

    readme = data_root / "work" / "proj"
    assert (readme / "feature.txt").exists()
    assert service.git("proj").current_branch() == "main"


def test_stash_round_trip(service, memory, data_root):
    _new_project(service, memory, data_root)
    readme = data_root / "work" / "proj" / "README.md"
    readme.write_text("dirty", encoding="utf-8")
    service.stash_changes("proj", "wip")
    assert service.list_stashes("proj")
    assert readme.read_text(encoding="utf-8").startswith("# proj")
    service.apply_stash("proj")
    assert readme.read_text(encoding="utf-8") == "dirty"


def test_rename_project_and_remote_attempt(service, memory, data_root):
    _new_project(service, memory, data_root)
    result = service.rename_project("proj", "renamed")
    assert result["project"]["name"] == "renamed"
    assert memory.get_project("renamed") is not None
    assert memory.get_project("proj") is None


def test_project_status_reports_branch_and_changes(service, memory, data_root):
    _new_project(service, memory, data_root)
    (data_root / "work" / "proj" / "x.txt").write_text("x", encoding="utf-8")
    status = service.project_status("proj")
    assert status["exists"] and status["branch"] == "main"
    assert status["changes"] == 1
    assert status["env_kind"] == "python"


# ---------------------------------------------------------------------------
# Remote parsing (M5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/alice/repo.git", ("alice", "repo", "github")),
    ("https://github.com/alice/repo", ("alice", "repo", "github")),
    ("git@github.com:alice/repo.git", ("alice", "repo", "github")),
    ("ssh://git@github.com/alice/repo.git", ("alice", "repo", "github")),
    ("alice/repo", ("alice", "repo", "github")),
    ("https://gitlab.com/team/proj.git", ("team", "proj", "other")),
])
def test_parse_remote_variants(url, expected):
    info = parse_remote(url)
    assert (info.owner, info.repo, info.kind) == expected


def test_parse_remote_rejects_garbage():
    with pytest.raises(RemoteUnsupportedError):
        parse_remote("not a url")


def test_normalize_clone_url():
    assert normalize_clone_url("alice/repo") == "https://github.com/alice/repo.git"
    assert normalize_clone_url("git@github.com:a/b.git") == "https://github.com/a/b.git"
    with pytest.raises(RemoteUnsupportedError):
        normalize_clone_url("https://gitlab.com/a/b")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def test_credentials_round_trip(credentials):
    credentials.set("alice", "ghp_secret")
    assert credentials.get("alice") == "ghp_secret"
    assert credentials.has("alice")
    credentials.delete("alice")
    assert credentials.get("alice") is None


def test_legacy_plaintext_tokens_are_migrated_and_removed(tmp_path):
    from scaffolder_app.credentials import CredentialStore

    legacy = tmp_path / "legacy" / "credentials.json"
    legacy.parent.mkdir()
    legacy.write_text('{"tokens": {"bob": "ghp_old"}}', encoding="utf-8")
    store = CredentialStore(tmp_path / "new", prefer_keyring=False)

    count = store.migrate_legacy(legacy)

    assert count == 1
    assert store.get("bob") == "ghp_old"
    assert not legacy.exists(), "plaintext token file must be deleted"

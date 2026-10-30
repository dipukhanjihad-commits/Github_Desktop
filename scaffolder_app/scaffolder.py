"""High-level application service.

``Scaffolder`` is the single API the UI talks to. It owns:

* project lifecycle (create / clone / import / relink / rename / delete)
* the *version == branch* model with safe work-tree transitions
* commit, diff, staging, history and undo
* push / pull / merge / pull-request flows
* templates and environments

Everything here is synchronous and safe to call from a worker thread; the UI
wraps calls in :class:`scaffolder_app.ui.async_runner.TaskRunner`.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import paths
from .credentials import CredentialStore
from .editor import EditorLauncher
from .env_setup import EnvDetector, EnvSetup
from .errors import (
    AlreadyExistsError, DirtyWorkTreeError, DivergedError, MissingFolderError,
    MissingTokenError, NotFoundError, NotARepositoryError,
    RemoteUnsupportedError, ScaffolderError, TokenExpiredError, ValidationError,
)
from .git_ops import GitRunner, redact
from .github_api import GitHubClient, parse_remote
from .project_memory import (
    ProjectMemory, RepoState, SWITCH_STRATEGIES, SyncState, Visibility,
    _now, validate_folder_name,
)
from .template_engine import TemplateEngine

Progress = Callable[[str], Any]

DEFAULT_BRANCH = "main"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def clean_commit_message(message: str, max_title: int = 72) -> str:
    """Normalise a commit message (S26).

    Collapses trailing whitespace, strips comment lines and control characters,
    and warns-by-returning a trimmed body when the first line is too long.
    """
    text = (message or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        if line.startswith("#"):
            continue
        cleaned = "".join(ch for ch in line if ch == "\t" or ord(ch) >= 32)
        lines.append(cleaned.rstrip())
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise ValidationError("Commit message cannot be empty.")
    title = lines[0].strip()
    if len(title) > max_title:
        # Keep the full title on line one but flag it for callers that care.
        pass
    body = "\n".join(lines[1:]).strip()
    return title + ("\n\n" + body if body else "")


class Scaffolder:
    def __init__(self, memory: ProjectMemory, credentials: CredentialStore | None = None,
                 root: Path | str | None = None) -> None:
        self.memory = memory
        self.root = Path(root) if root else paths.data_dir()
        self.credentials = credentials or CredentialStore(self.root)
        self.github = GitHubClient(self.credentials.get)
        self._task_thread: threading.Thread | None = None
        self._repo_cache: dict[str, GitRunner] = {}
        self._health_cache = _RepoStatusCache()

    # ================================================== project resolution

    def _require(self, project_name: str) -> dict[str, Any]:
        proj = self.memory.get_project(project_name)
        if not proj:
            raise NotFoundError(f"Project '{project_name}' not found.")
        return proj

    def git(self, project_name: str) -> GitRunner:
        proj = self._require(project_name)
        path = Path(proj["path"])
        if not path.exists():
            self.memory.set_missing(project_name, True)
            raise MissingFolderError(str(path))
        return GitRunner(path)

    def project_exists(self, project_name: str) -> bool:
        proj = self.memory.get_project(project_name)
        return bool(proj and Path(proj["path"]).exists())

    def validate_paths(self) -> list[dict[str, Any]]:
        """Reconcile stored projects with the filesystem (M8)."""
        problems = []
        for proj in self.memory.list_projects():
            exists = Path(proj["path"]).exists()
            if exists == bool(proj.get("missing")):
                self.memory.set_missing(proj["name"], not exists)
            if not exists:
                problems.append({"name": proj["name"], "path": proj["path"]})
        return problems

    def editor(self) -> EditorLauncher:
        return EditorLauncher(self.memory.get_setting("editor", "vscode") or "vscode",
                              self.memory.get_setting("editor_command", "") or "")

    # ======================================================== templates

    def engine_for(self, template: dict[str, Any]) -> TemplateEngine:
        return TemplateEngine(template["path"], template.get("ignore"),
                              template.get("placeholders"))

    def create_template_from_folder(self, name: str, source_folder: str | Path,
                                    description: str = "",
                                    ignore: list[str] | None = None,
                                    placeholders: dict[str, str] | None = None) -> dict:
        src = Path(source_folder).expanduser()
        if not src.is_dir():
            raise ValidationError(f"Not a folder: {src}")
        ignore = ignore if ignore is not None else self.memory.get_setting("default_ignore")
        engine = TemplateEngine(src, ignore)
        discovered = engine.discover_placeholders()
        merged = dict(placeholders or {})
        for key in discovered:
            merged.setdefault(key, "")
        return self.memory.add_template(name=name, path=str(src),
                                        description=description,
                                        ignore=list(ignore or []),
                                        placeholders=merged)

    def preview_template(self, template_name: str) -> dict:
        template = self.memory.get_template(template_name)
        if not template:
            raise NotFoundError(f"Template '{template_name}' not found.")
        return self.engine_for(template).preview()

    def export_template_manifest(self, template_name: str,
                                 destination: Path | str) -> Path:
        """Write a portable manifest so a template can be shared (N10)."""
        template = self.memory.get_template(template_name)
        if not template:
            raise NotFoundError(f"Template '{template_name}' not found.")
        payload = {
            "schema": 1,
            "app": "project-scaffolder",
            "name": template["name"],
            "description": template.get("description", ""),
            "ignore": template.get("ignore", []),
            "placeholders": template.get("placeholders", {}),
            "source_path": template["path"],
            "exported_at": _now(),
        }
        target = Path(destination)
        if target.is_dir():
            target = target / f"{template['name']}.scaffold.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return target

    def import_template_manifest(self, manifest: Path | str,
                                 source_folder: Path | str | None = None) -> dict:
        path = Path(manifest)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Cannot read manifest: {exc}") from exc
        if not isinstance(payload, dict) or "name" not in payload:
            raise ValidationError("Manifest is missing a template name.")
        source = Path(source_folder or payload.get("source_path", ""))
        return self.create_template_from_folder(
            name=payload["name"], source_folder=source,
            description=payload.get("description", ""),
            ignore=payload.get("ignore"),
            placeholders=payload.get("placeholders"),
        )

    # ========================================================== projects

    def create_project(self, name: str, template_name: str, dest_path: str | Path,
                       placeholder_values: dict[str, str], visibility: str = "private",
                       account: str | None = None,
                       create_repo: bool | None = None,
                       progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        name = validate_folder_name(name, "project name")
        template = self.memory.get_template(template_name)
        if not template:
            raise NotFoundError(f"Template '{template_name}' not found.")
        if self.memory.get_project(name):
            raise AlreadyExistsError(f"A project named '{name}' already exists.")

        dest = Path(dest_path).expanduser()
        if dest.exists() and any(dest.iterdir()):
            raise ValidationError(f"The destination folder is not empty:\n{dest}")
        existing = self.memory.find_project_path(dest)
        if existing:
            raise AlreadyExistsError(
                f"That folder is already managed as '{existing['name']}'.")

        if create_repo is None:
            create_repo = bool(self.memory.get_setting("auto_create_repo", True))
        account = account or self.memory.get_setting("default_owner")
        if create_repo and not account:
            log("No GitHub account configured \u2014 creating the project locally.")
            create_repo = False

        created_dest = not dest.exists()
        committed = False
        project_added = False
        try:
            log("Copying template files\u2026")
            engine = self.engine_for(template)
            values = dict(placeholder_values)
            values.setdefault("project_name", name)
            written = engine.apply(dest, values)
            log(f"Copied {len(written)} file(s).")

            log("Registering project\u2026")
            self.memory.add_project(name=name, path=str(dest),
                                    template=template_name,
                                    visibility=visibility,
                                    owner=account if create_repo else None)
            project_added = True

            log("Initializing git repository\u2026")
            git = GitRunner(dest)
            git.run("init", "-q")
            base_branch = self.memory.get_setting("default_branch") or DEFAULT_BRANCH
            if account:
                self._apply_identity(git, account)
            else:
                self._ensure_identity(git)
            current = git.current_branch()
            if current and current != base_branch and not git.has_commits():
                git.rename_branch(current, base_branch)
            elif not current:
                git.run("symbolic-ref", "HEAD", f"refs/heads/{base_branch}", check=False)
            git.stage_all()
            git.commit("Initial commit from template")
            committed = True
            log(f"Created base version v1 on '{base_branch}'.")
            self.memory.add_version(name, "v1", parent=None, branch=base_branch)

            if create_repo and account:
                self._create_and_push(name, account, visibility, log,
                                      only_failures=True)
            else:
                log("Project created locally (no GitHub repository).")
            return self.memory.get_project(name) or {}
        except TokenExpiredError:
            if project_added:
                self.memory.mark_repo_pending(name)
            else:
                self._rollback_project(dest, created_dest, committed)
            raise
        except Exception as exc:
            if project_added:
                self.memory.mark_repo_pending(name)
                self.memory.log_error("create_project", redact(str(exc)))
            else:
                self._rollback_project(dest, created_dest, committed)
            raise

    def _rollback_project(self, dest: Path, created_dest: bool,
                          has_git: bool) -> None:
        """Remove a half-scaffolded project (M7)."""
        try:
            if not dest.exists():
                return
            if dest.is_dir() and not any(dest.iterdir()):
                dest.rmdir()
                return
            trash = paths.trash_dir(self.root) / f"rollback-{dest.name}-{_stamp()}"
            trash.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(trash))
        except OSError:
            pass

    def clone_repository(self, url: str, dest_path: str | Path,
                         project_name: str | None = None,
                         account: str | None = None,
                         import_all_branches: bool = False,
                         progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        from .github_api import normalize_clone_url

        clone_url = normalize_clone_url(url)
        info = parse_remote(clone_url)
        name = project_name or info.repo
        name = validate_folder_name(name, "project name")
        if self.memory.get_project(name):
            raise AlreadyExistsError(f"A project named '{name}' already exists.")

        dest = Path(dest_path).expanduser()
        if dest.exists() and any(dest.iterdir()):
            raise ValidationError(f"The destination folder is not empty:\n{dest}")
        existing = self.memory.find_project_path(dest)
        if existing:
            raise AlreadyExistsError(
                f"That folder is already managed as '{existing['name']}'.")

        account = account or (info.owner if self.memory.get_account(info.owner) else None)
        token = self.credentials.get(account) if account else None
        log(f"Cloning {info.full_name} \u2026")
        dest.parent.mkdir(parents=True, exist_ok=True)
        clone_args = ["clone", "--", clone_url, str(dest)]
        if token:
            with_git = GitRunner(dest.parent)
            with_git.run(*clone_args, token=token, timeout=1800)
        else:
            from .git_ops import run as _run

            _run(["git", *clone_args], cwd=dest.parent, timeout=1800)

        git = GitRunner(dest)
        default_branch = git.current_branch() or info.repo
        if account:
            self._apply_identity(git, account)
        else:
            self._ensure_identity(git)

        project = self.memory.add_project(name=name, path=str(dest),
                                          template=None, imported=True,
                                          visibility="private", owner=info.owner)
        base = "v1"
        visible = git.run("rev-parse", "--verify", "--quiet", "HEAD", check=False).ok
        if visible:
            self.memory.add_version(name, base, parent=None, branch=default_branch)
        else:
            project["repo_state"] = RepoState.REPO_CREATED.value
        if visible and import_all_branches:
            log("Importing remote branches\u2026")
            seen = {default_branch}
            index = 2
            for remote_branch in git.remote_branches():
                local = remote_branch.split("/", 1)[1] if "/" in remote_branch \
                    else remote_branch
                if local in seen:
                    continue
                git.run("branch", "--track", local, remote_branch, check=False)
                self.memory.add_version(name, f"v{index}", parent=base, branch=local)
                seen.add(local)
                index += 1
        self.memory.attach_repo(name, info.owner, info.url, visibility="private",
                                state=RepoState.SYNCED, remote_kind=info.kind)
        try:
            self.refresh_sync_status(name, fetch_first=False)
        except ScaffolderError as exc:
            self.memory.log_error("clone:sync", redact(str(exc)))
        log(f"Cloned into {dest}")
        return self.memory.get_project(name) or {}

    def link_existing_folder(self, path: str | Path, name: str) -> dict:
        """Adopt an existing git repository (Add Existing Folder)."""
        src = Path(path).expanduser().resolve()
        name = validate_folder_name(name, "project name")
        if not src.exists():
            raise ValidationError(f"That folder does not exist:\n{src}")
        git = GitRunner(src)
        if not git.is_repo():
            raise NotARepositoryError(f"{src} is not a git repository.")
        if self.memory.find_project_path(src):
            existing = self.memory.find_project_path(src)
            raise AlreadyExistsError(
                f"That folder is already managed as '{existing['name']}'.")

        branch = git.current_branch() or DEFAULT_BRANCH
        remote = git.remote_url("origin")
        remote_info = None
        remote_kind = "other"
        if remote:
            try:
                remote_info = parse_remote(remote)
                remote_kind = remote_info.kind
            except RemoteUnsupportedError:
                remote_info = None

        identity = None
        if remote_info and self.memory.get_account(remote_info.owner):
            identity = remote_info.owner
        if identity:
            self._apply_identity(git, identity)

        self.memory.add_project(name=name, path=str(src), template=None,
                                imported=True,
                                owner=remote_info.owner if remote_info else None)
        has_commits = git.has_commits()
        if has_commits:
            self.memory.add_version(name, "v1", parent=None, branch=branch)
        if remote:
            if remote_info:
                self.memory.attach_repo(name, remote_info.owner, remote_info.url,
                                        state=RepoState.SYNCED,
                                        remote_kind=remote_kind)
            else:
                self.memory.attach_repo(name, "", remote,
                                        state=RepoState.REPO_CREATED,
                                        remote_kind="other")
            note = _warn_if_unparseable(remote)
            if note:
                self.memory.log_error("link_existing_folder", note)
            if has_commits:
                try:
                    self.refresh_sync_status(name, fetch_first=False)
                except ScaffolderError:
                    pass
        return self.memory.get_project(name) or {}

    def relink_project(self, project_name: str, new_path: str | Path) -> dict:
        resolved = Path(new_path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValidationError(f"That folder does not exist:\n{resolved}")
        other = self.memory.find_project_path(resolved)
        if other and other["name"] != project_name:
            raise AlreadyExistsError(
                f"That folder is already managed as '{other['name']}'.")
        return self.memory.relink_project(project_name, resolved)

    def create_repository_for_project(self, project_name: str, account: str,
                                      visibility: str = "private",
                                      progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        proj = self._require(project_name)
        if proj["repo_state"] not in (RepoState.LOCAL_ONLY.value,
                                      RepoState.REPO_PENDING.value):
            raise ValidationError("This project already has a repository.")
        if not self.credentials.get(account):
            raise MissingTokenError(account)
        dest = Path(proj["path"])
        if not dest.exists():
            raise MissingFolderError(str(dest))
        git = GitRunner(dest)
        git.ensure_repo()
        if not git.has_commits():
            git.stage_all()
            git.commit("Initial commit")
            if not proj["versions"]:
                self.memory.add_version(project_name, "v1",
                                        branch=git.current_branch() or DEFAULT_BRANCH)
        try:
            repo = self.github.create_repo(account, account, proj["name"],
                                           visibility == "private")
        except TokenExpiredError:
            self.memory.mark_repo_pending(project_name)
            raise
        self._apply_identity(git, account)
        git.set_remote(repo["clone_url"])
        branch = self._current_branch_of(project_name) or DEFAULT_BRANCH
        log(f"Pushing {branch} to {repo['html_url']} \u2026")
        try:
            git.push(branch, token=self.credentials.get(account))
        except TokenExpiredError:
            self.memory.attach_repo(project_name, repo["owner"]["login"],
                                    repo["html_url"], visibility=visibility,
                                    state=RepoState.REPO_PENDING)
            raise
        self.memory.attach_repo(project_name, repo["owner"]["login"],
                                repo["html_url"], visibility=visibility,
                                state=RepoState.SYNCED)
        label = proj.get("current_version") or "v1"
        if label in (self.memory.get_project(project_name) or {}).get("versions", {}):
            self.memory.update_version_sync(project_name, label, SyncState.SYNCED)
        return repo

    def check_name_available(self, name: str, account: str | None = None) -> dict:
        """Pre-flight for the New Project dialog (S23)."""
        result = {"local_free": True, "remote_free": True, "message": ""}
        try:
            validate_folder_name(name, "project name")
        except ValidationError as exc:
            return {"local_free": False, "remote_free": False,
                    "message": str(exc)}
        if self.memory.get_project(name):
            result["local_free"] = False
            result["message"] = f"A project named '{name}' already exists."
        if account and self.credentials.get(account):
            try:
                result["remote_free"] = not self.github.repo_exists(account, account, name)
                if not result["remote_free"]:
                    result["message"] = f"github.com/{account}/{name} already exists."
            except ScaffolderError as exc:
                result["message"] = f"Could not check GitHub: {exc}"
        return result

    # -------------------------------------------------------- rename/delete

    def rename_project(self, old: str, new: str, rename_remote: bool = True) -> dict:
        new = validate_folder_name(new, "project name")
        proj = self._require(old)
        clash = self.memory.get_project(new)
        if clash and clash["id"] != proj["id"]:
            raise AlreadyExistsError(f"A project named '{new}' already exists.")

        remote_renamed = False
        errors: list[str] = []
        if rename_remote and proj.get("repo_url"):
            try:
                info = parse_remote(proj["repo_url"])
            except RemoteUnsupportedError:
                errors.append("Remote was left unchanged: its URL is unparseable.")
                info = None
            if info is not None and not info.is_github:
                errors.append(
                    f"Remote '{info.url}' is not on GitHub and was left unchanged.")
                self.memory.log_error(
                    "rename:remote", f"Skipped non-GitHub remote: {info.full_name}")
            elif info is not None:
                account = proj.get("owner") or info.owner
                token = self.credentials.get(account)
                if not token:
                    errors.append(f"Remote not renamed: no GitHub token for '{account}'.")
                else:
                    try:
                        updated = self.github.rename_repo(account, info.owner,
                                                          info.repo, new)
                        remote_renamed = True
                        git = self.git(old)
                        git.set_remote(updated.get("clone_url", info.url))
                        self.memory.attach_repo(old, info.owner,
                                                updated.get("html_url", info.url),
                                                state=RepoState.SYNCED)
                    except ScaffolderError as exc:
                        errors.append(f"Remote rename failed: {exc}")
                        self.memory.log_error("rename:remote", redact(str(exc)))
        updated = self.memory.rename_project(old, new)
        return {"project": updated, "remote_renamed": remote_renamed,
                "errors": errors}

    def change_visibility(self, project_name: str, visibility: str) -> dict:
        proj = self._require(project_name)
        visibility = Visibility(visibility).value
        remote_updated = False
        if proj.get("repo_url"):
            info = parse_remote(proj["repo_url"])
            token = self.credentials.get(proj.get("owner") or "")
            if info.is_github and token:
                self.github.set_visibility(proj.get("owner") or info.owner,
                                           info.owner, info.repo,
                                           visibility == "private")
                remote_updated = True
        self.memory.set_visibility(project_name, visibility)
        return {"remote_updated": remote_updated, "visibility": visibility}

    def delete_project(self, project_name: str, remove_remote: bool = False,
                       delete_files: bool = False,
                       move_folder: bool = True) -> dict:
        """Delete a project by id, never by name (M2)."""
        proj = self._require(project_name)
        repo_url = proj.get("repo_url")
        folder = Path(proj["path"])
        result = {"trashed": None, "remote_deleted": False, "errors": []}

        if folder.exists() and move_folder and not delete_files:
            try:
                target = paths.trash_dir(self.root) / f"{proj['name']}-{_stamp()}"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(folder), str(target))
                result["trashed"] = str(target)
                self.memory.add_trash_entry({
                    "stamp": target.name, "name": proj["name"],
                    "path": proj["path"], "trashed_to": str(target),
                    "at": _now(), "repo_url": repo_url,
                })
            except OSError as exc:
                self.memory.log_error("delete:trash", str(exc))
                result["errors"].append(f"Could not move the folder to trash: {exc}")
        elif delete_files and folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

        if remove_remote and repo_url:
            try:
                info = parse_remote(repo_url)
                token = self.credentials.get(proj.get("owner") or "")
                if info.is_github and token:
                    self.github.delete_repo(proj.get("owner") or info.owner,
                                            info.owner, info.repo)
                    result["remote_deleted"] = True
                else:
                    result["errors"].append("Remote not deleted: no GitHub token.")
            except ScaffolderError as exc:
                result["errors"].append(f"Remote delete failed: {exc}")
                self.memory.log_error("delete:remote", redact(str(exc)))

        self.memory.remove_project(project_name)
        return result

    # ------------------------------------------------------------------ trash

    def delete_project_keep_files(self, project_name: str,
                                  remove_remote: bool = False) -> dict:
        """Remove the project from the app but leave its folder untouched."""
        result = self.delete_project(project_name, remove_remote=remove_remote,
                                     delete_files=False, move_folder=False)
        return result

    def list_trash(self) -> list[dict]:
        entries = []
        for entry in self.memory.list_trash():
            item = dict(entry)
            item["exists"] = Path(entry.get("trashed_to", "")).exists()
            entries.append(item)
        return entries

    def restore_from_trash(self, stamp: str) -> dict:
        entry = next((t for t in self.memory.list_trash() if t["stamp"] == stamp), None)
        if not entry:
            raise NotFoundError("That trash entry no longer exists.")
        source = Path(entry["trashed_to"])
        target = Path(entry["path"])
        if not source.exists():
            raise MissingFolderError(str(source))
        if target.exists():
            raise AlreadyExistsError(f"The original location is occupied:\n{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        restored = self.memory.add_project(name=entry["name"], path=str(target),
                                           imported=True)
        if entry.get("repo_url"):
            try:
                info = parse_remote(entry["repo_url"])
                self.memory.attach_repo(entry["name"], info.owner, info.url,
                                        state=RepoState.SYNCED)
            except RemoteUnsupportedError:
                pass
        git = GitRunner(target)
        if git.is_repo():
            self.memory.add_version(entry["name"], "v1",
                                    branch=git.current_branch() or DEFAULT_BRANCH)
        self.memory.remove_trash_entry(stamp)
        return restored

    def purge_trash_entry(self, stamp: str) -> None:
        entry = next((t for t in self.memory.list_trash() if t["stamp"] == stamp), None)
        if not entry:
            return
        shutil.rmtree(Path(entry["trashed_to"]), ignore_errors=True)
        self.memory.remove_trash_entry(stamp)

    def empty_trash(self) -> int:
        count = 0
        for entry in self.memory.list_trash():
            shutil.rmtree(Path(entry["trashed_to"]), ignore_errors=True)
            self.memory.remove_trash_entry(entry["stamp"])
            count += 1
        return count

    # ========================================================== versions

    def _current_branch_of(self, project_name: str) -> str | None:
        proj = self.memory.get_project(project_name) or {}
        label = proj.get("current_version")
        if not label:
            return None
        version = proj["versions"].get(label)
        return version["branch"] if version else None

    def _ensure_clean(self, project_name: str, strategy: str,
                      action: str = "switch") -> str | None:
        """Handle a dirty work tree before a branch transition (M9).

        Returns the stash message when work was stashed so the caller can
        restore it. ``strategy`` is one of ``SWITCH_STRATEGIES``.
        """
        git = self.git(project_name)
        if git.is_clean():
            return None
        strategy = strategy if strategy in SWITCH_STRATEGIES else "stash"
        if strategy == "stash":
            message = f"scaffolder: auto-stash before {action} {_stamp()}"
            git.stash_push(message)
            return message
        if strategy == "discard":
            git.discard_all()
            return None
        if strategy == "commit":
            git.stage_all()
            git.commit(f"WIP: autosaved before {action}")
            return None
        raise DirtyWorkTreeError(git.status_entries())

    def switch_version(self, project_name: str, label: str,
                       strategy: str | None = None,
                       restore_stash: bool = True) -> dict:
        proj = self._require(project_name)
        if label not in proj["versions"]:
            raise NotFoundError(f"Version '{label}' not found.")
        if proj["current_version"] == label:
            return {"switched": False, "label": label, "stash": None}
        strategy = strategy or self.memory.get_setting("switch_strategy", "stash")
        git = self.git(project_name)
        stash_message = self._ensure_clean(project_name, strategy, "switch")
        branch = proj["versions"][label]["branch"]
        try:
            if git.branch_exists(branch):
                git.checkout(branch)
            else:
                parent = proj["versions"][label].get("parent")
                parent_branch = None
                if parent and parent in proj["versions"]:
                    parent_branch = proj["versions"][parent]["branch"]
                if parent_branch and parent_branch != git.current_branch():
                    git.checkout(parent_branch)
                git.checkout(branch, create=True)
        except ScaffolderError:
            if stash_message:
                git.stash_pop()
            raise
        self.memory.switch_version(project_name, label)
        restored = False
        if stash_message and restore_stash:
            restored = git.stash_pop()
        return {"switched": True, "label": label, "stash": stash_message,
                "stash_restored": restored}

    def create_version(self, project_name: str, label: str,
                       parent_label: str | None = None,
                       strategy: str | None = None,
                       progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        label = validate_folder_name(label, "version label")
        proj = self._require(project_name)
        if label in proj["versions"]:
            raise AlreadyExistsError(f"Version '{label}' already exists.")
        parent_label = parent_label or proj.get("current_version") or "v1"
        if parent_label not in proj["versions"]:
            raise NotFoundError(f"Parent version '{parent_label}' not found.")
        strategy = strategy or self.memory.get_setting("switch_strategy", "stash")
        git = self.git(project_name)
        stash_message = self._ensure_clean(project_name, strategy, "branch")
        parent_branch = proj["versions"][parent_label]["branch"]
        new_branch = f"version/{label}"
        log(f"Branching {parent_branch} \u2192 {new_branch}\u2026")
        if git.current_branch() != parent_branch:
            git.checkout(parent_branch)
        if git.branch_exists(new_branch):
            raise AlreadyExistsError(
                f"A git branch named '{new_branch}' already exists.")
        git.checkout(new_branch, create=True)
        if stash_message:
            git.stash_pop()
        version = self.memory.add_version(project_name, label,
                                          parent=parent_label, branch=new_branch)
        self.memory.switch_version(project_name, label)
        token = self._token_for(project_name, required=False)
        if proj.get("repo_url") and token:
            try:
                log(f"Pushing branch {new_branch}\u2026")
                git.push(new_branch, token=token)
                self.memory.update_version_sync(project_name, label, SyncState.SYNCED)
            except ScaffolderError as exc:
                self.memory.log_error("create_version:push", redact(str(exc)))
                log("\u26a0 Branch push failed \u2014 it will show as ahead.")
        return version

    def delete_version(self, project_name: str, label: str,
                       remove_remote: bool = True,
                       progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        proj = self._require(project_name)
        if label not in proj["versions"]:
            raise NotFoundError(f"Version '{label}' not found.")
        version = proj["versions"][label]
        git = self.git(project_name)
        branch = version["branch"]
        switched_to = None
        if proj["current_version"] == label:
            others = [k for k in proj["versions"] if k != label]
            if not others:
                raise ValidationError("Cannot delete the only version.")
            parent = version.get("parent")
            target = parent if parent in proj["versions"] else others[0]
            self.switch_version(project_name, target)
            switched_to = target
        if git.current_branch() == branch:
            raise ValidationError(f"Cannot delete '{branch}' while it is checked out.")
        log(f"Deleting local branch {branch}\u2026")
        git.delete_branch(branch, force=True)
        remote_deleted = False
        token = self._token_for(project_name, required=False)
        if remove_remote and proj.get("repo_url") and token:
            git.delete_remote_branch(branch, token=token)
            remote_deleted = True
        self.memory.remove_version(project_name, label, force=True)
        return {"local_deleted": True, "remote_deleted": remote_deleted,
                "switched_to": switched_to}

    def merge_version(self, project_name: str, source: str, target_branch_label: str,
                      strategy: str = "merge", push: bool = True,
                      progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        proj = self._require(project_name)
        for label in (source, target_branch_label):
            if label not in proj["versions"]:
                raise NotFoundError(f"Version '{label}' not found.")
        if source == target_branch_label:
            raise ValidationError("Source and target must differ.")
        src_branch = proj["versions"][source]["branch"]
        tgt_branch = proj["versions"][target_branch_label]["branch"]
        git = self.git(project_name)
        self._ensure_clean(project_name, "stash", "merge")
        if git.current_branch() != tgt_branch:
            git.checkout(tgt_branch)
        log(f"Merging {src_branch} \u2192 {tgt_branch}\u2026")
        git.merge(src_branch, strategy=strategy)
        pushed = False
        if push and proj.get("repo_url"):
            token = self._token_for(project_name, required=False)
            if token:
                git.push(tgt_branch, token=token)
                pushed = True
        return {"merged": True, "pushed": pushed}

    def rebase_version(self, project_name: str, label: str, onto: str,
                       progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        proj = self._require(project_name)
        for name in (label, onto):
            if name not in proj["versions"]:
                raise NotFoundError(f"Version '{name}' not found.")
        git = self.git(project_name)
        branch = proj["versions"][label]["branch"]
        onto_branch = proj["versions"][onto]["branch"]
        self._ensure_clean(project_name, "stash", "rebase")
        git.checkout(branch)
        log(f"Rebasing {branch} onto {onto_branch}\u2026")
        git.rebase(onto_branch)
        return {"rebased": True}

    def create_pull_request(self, project_name: str, source: str, target: str,
                            title: str = "", body: str = "") -> dict:
        proj = self._require(project_name)
        if not proj.get("repo_url"):
            raise ValidationError("This project has no GitHub repository.")
        for label in (source, target):
            if label not in proj["versions"]:
                raise NotFoundError(f"Version '{label}' not found.")
        if source == target:
            raise ValidationError("Source and target must differ.")
        info = parse_remote(proj["repo_url"])
        # Fail fast on a missing/expired token before pushing anything.
        self._token_for(project_name, required=True)
        account = proj.get("owner") or info.owner
        head = proj["versions"][source]["branch"]
        base = proj["versions"][target]["branch"]
        self.push(project_name, source)
        return self.github.create_pull_request(
            account, info.owner, info.repo, head, base,
            title or f"Merge {source} into {target}",
            body or f"Merge version {source} \u2192 {target}.")

    # ============================================================== commits

    def get_changes(self, project_name: str) -> list[dict]:
        git = self.git(project_name)
        if not git.is_repo():
            return []
        return git.status_entries()

    def diff(self, project_name: str, path: str, staged: bool | None = None) -> str:
        git = self.git(project_name)
        entries = {e["path"]: e for e in git.status_entries()}
        entry = entries.get(path)
        if entry is None:
            return ""
        use_staged = entry["staged"] if staged is None else staged
        return git.diff_text(path, staged=use_staged)

    def diff_stat(self, project_name: str) -> str:
        git = self.git(project_name)
        staged = git.diff_stat(staged=True)
        unstaged = git.diff_stat(staged=False)
        parts = []
        if staged.strip():
            parts.append("Staged:\n" + staged.rstrip())
        if unstaged.strip():
            parts.append("Unstaged:\n" + unstaged.rstrip())
        return "\n\n".join(parts)

    def stage(self, project_name: str, paths: Iterable[str]) -> None:
        self.git(project_name).stage(paths)

    def unstage(self, project_name: str, paths: Iterable[str]) -> None:
        self.git(project_name).unstage(paths)

    def discard(self, project_name: str, paths: Iterable[str]) -> None:
        git = self.git(project_name)
        for path in paths:
            git.restore(path)

    def discard_all(self, project_name: str) -> None:
        self.git(project_name).discard_all()

    def commit(self, project_name: str, message: str, paths: Iterable[str] | None = None,
               stage_all: bool = True, amend: bool = False,
               push: bool | None = None) -> dict:
        git = self.git(project_name)
        proj = self._require(project_name)
        max_title = int(self.memory.get_setting("max_commit_title_length", 72) or 72)
        cleaned = clean_commit_message(message, max_title)
        if not git.is_clean():
            if paths is not None:
                git.stage(paths)
            elif stage_all:
                git.stage_all()
        elif not amend:
            raise ValidationError("Nothing to commit \u2014 the working tree is clean.")
        if not amend and not git.diff_stat(staged=True).strip() and not paths:
            raise ValidationError("No staged changes to commit.")
        sha = git.commit(cleaned, amend=amend)
        if push is None:
            push = bool(self.memory.get_setting("auto_push_after_commit", False))
        pushed = False
        if push and proj.get("repo_url") and proj.get("current_version"):
            try:
                self.push(project_name)
                pushed = True
            except ScaffolderError as exc:
                self.memory.log_error("commit:auto-push", redact(str(exc)))
        else:
            self._mark_ahead_after_commit(project_name)
        return {"sha": sha, "message": cleaned, "pushed": pushed}

    def _mark_ahead_after_commit(self, project_name: str) -> None:
        proj = self.memory.get_project(project_name)
        if not proj or not proj.get("repo_url"):
            return
        label = proj.get("current_version")
        if not label or label not in proj["versions"]:
            return
        try:
            self.refresh_sync_status(project_name, label, fetch_first=False)
        except ScaffolderError:
            current = proj["versions"][label]
            self.memory.update_version_sync(project_name, label, SyncState.AHEAD,
                                            ahead=int(current.get("ahead", 0)) + 1,
                                            behind=0)

    def history(self, project_name: str, limit: int = 100) -> list[dict]:
        return self.git(project_name).log(limit)

    def show_commit(self, project_name: str, sha: str) -> str:
        return self.git(project_name).show_commit(sha)

    def undo_last_commit(self, project_name: str, keep_changes: bool = True) -> dict:
        git = self.git(project_name)
        if git.commit_count() < 1:
            raise ValidationError("There are no commits to undo.")
        git.revert_last(keep_changes=keep_changes)
        return {"undone": True, "kept_changes": keep_changes}

    def stash_changes(self, project_name: str, message: str = "") -> dict:
        git = self.git(project_name)
        if git.is_clean():
            raise ValidationError("There is nothing to stash.")
        output = git.stash_push(message or f"scaffolder stash {_stamp()}")
        return {"stash": output}

    def list_stashes(self, project_name: str) -> list[str]:
        return self.git(project_name).stash_list()

    def apply_stash(self, project_name: str) -> dict:
        git = self.git(project_name)
        if not git.stash_pop():
            raise ScaffolderError("Could not apply the stash.",
                                  hint="Inspect conflicts in your editor.")
        return {"applied": True}

    # ============================================================ git remote

    def _token_for(self, project_name: str, required: bool = True) -> str | None:
        proj = self.memory.get_project(project_name) or {}
        account = proj.get("owner")
        if not account:
            if required:
                raise ValidationError("This project has no connected GitHub account.")
            return None
        token = self.credentials.get(account) if account else None
        if not required:
            return token
        if not token:
            raise TokenExpiredError(account)
        return token

    def _apply_identity(self, git: GitRunner, account: str) -> None:
        info = self.memory.get_account(account)
        email = (info or {}).get("noreply_email") or \
            f"{account}@users.noreply.github.com"
        git.set_identity(account, email)

    @staticmethod
    def _ensure_identity(git: GitRunner) -> None:
        if not git.config_get("user.name"):
            git.config_set("user.name", "Scaffolder")
        if not git.config_get("user.email"):
            git.config_set("user.email", "scaffolder@localhost")

    def fix_git_identity(self, project_name: str) -> dict:
        proj = self._require(project_name)
        account = proj.get("owner")
        if not account:
            raise ValidationError("This project has no connected GitHub account.")
        info = self.memory.get_account(account)
        if not info:
            raise NotFoundError(f"Account '{account}' is not registered.")
        self._apply_identity(self.git(project_name), account)
        return {"name": info["username"], "email": info.get("noreply_email")}

    def push(self, project_name: str, label: str | None = None,
             force: bool = False, progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        result = self._push_one(project_name, label, force=force, log=log)
        return result

    def _push_one(self, project_name: str, label: str | None, force: bool,
                  log: Progress) -> dict:
        proj = self._require(project_name)
        state = proj["repo_state"]
        if state == RepoState.LOCAL_ONLY.value:
            raise ValidationError("No repository yet \u2014 create one first.")
        if state == RepoState.REPO_PENDING.value:
            raise ValidationError("Repository setup is incomplete \u2014 retry "
                                  "'Create Repository\u2026'.")
        label = label or proj.get("current_version")
        if not label or label not in proj["versions"]:
            raise ValidationError("This project has no version to push.")
        token = self._token_for(project_name, required=True)
        branch = proj["versions"][label]["branch"]
        git = self.git(project_name)
        log(f"Pushing {branch} \u2192 origin\u2026")
        try:
            git.push(branch, token=token, force_with_lease=force)
        except TokenExpiredError:
            self.memory.set_account_token_state(proj.get("owner") or "", False)
            raise
        self.memory.update_version_sync(project_name, label, SyncState.SYNCED)
        return {"branch": branch, "pushed": True}

    def push_all(self, project_name: str, force: bool = False,
                 progress: Progress | None = None) -> dict:
        proj = self._require(project_name)
        results: dict[str, str] = {}
        for label in proj["versions"]:
            try:
                self.push(project_name, label, force=force, progress=progress)
                results[label] = "ok"
            except TokenExpiredError:
                raise
            except ScaffolderError as exc:
                results[label] = str(exc)
                self.memory.log_error(f"push_all:{label}", redact(str(exc)))
        return results

    def force_push(self, project_name: str, label: str | None = None,
                   progress: Progress | None = None) -> dict:
        return self.push(project_name, label, force=True, progress=progress)

    def pull(self, project_name: str, label: str | None = None,
             strategy: str = "ff-only", progress: Progress | None = None) -> dict:
        log = progress or (lambda _m: None)
        proj = self._require(project_name)
        label = label or proj.get("current_version")
        if not label or label not in proj["versions"]:
            raise ValidationError("This project has no version to pull.")
        token = self._token_for(project_name, required=True)
        branch = proj["versions"][label]["branch"]
        git = self.git(project_name)
        log(f"Fetching {branch}\u2026")
        try:
            git.fetch(token=token)
        except TokenExpiredError:
            self.memory.set_account_token_state(proj.get("owner") or "", False)
            raise
        counts = git.ahead_behind(branch)
        if counts is None:
            raise ScaffolderError(f"The remote branch 'origin/{branch}' does not exist.")
        ahead, behind = counts
        if ahead and behind:
            if strategy == "ff-only":
                raise DivergedError(ahead, behind, branch)
            current = git.current_branch()
            if current != branch:
                self._ensure_clean(project_name, "stash", "pull")
                git.checkout(branch)
            if strategy == "merge":
                log(f"Merging origin/{branch}\u2026")
                git.merge(f"origin/{branch}")
            elif strategy == "rebase":
                log(f"Rebasing onto origin/{branch}\u2026")
                git.rebase(f"origin/{branch}")
            else:
                raise ValidationError(f"Unknown pull strategy: {strategy}")
        elif behind:
            log(f"Fast-forwarding {behind} commit(s)\u2026")
            git.pull_ff(branch, token=token)
        elif ahead:
            log(f"Local is {ahead} commit(s) ahead \u2014 nothing to pull.")
        else:
            log("Already up to date.")
        self.refresh_sync_status(project_name, label, fetch_first=False)
        return {"ahead": ahead, "behind": behind}

    def refresh_sync_status(self, project_name: str, label: str | None = None,
                            fetch_first: bool | None = None) -> dict:
        proj = self.memory.get_project(project_name)
        if not proj:
            raise NotFoundError(f"Project '{project_name}' not found.")
        label = label or proj.get("current_version")
        if not label or label not in proj["versions"]:
            return {"state": SyncState.UNKNOWN.value, "ahead": 0, "behind": 0}
        if fetch_first is None:
            fetch_first = bool(self.memory.get_setting("fetch_on_refresh", True))
        git = self.git(project_name)
        branch = proj["versions"][label]["branch"]
        if fetch_first and proj.get("repo_url"):
            token = self._token_for(project_name, required=False)
            git.fetch(token=token)
        counts = git.ahead_behind(branch)
        if counts is None:
            state = (SyncState.UNKNOWN if proj.get("repo_url")
                     else SyncState.SYNCED)
            self.memory.update_version_sync(project_name, label, state)
            return {"state": state.value, "ahead": 0, "behind": 0}
        ahead, behind = counts
        if ahead and behind:
            state = SyncState.DIVERGED
        elif ahead:
            state = SyncState.AHEAD
        elif behind:
            state = SyncState.BEHIND
        else:
            state = SyncState.SYNCED
        self.memory.update_version_sync(project_name, label, state,
                                        ahead=ahead, behind=behind)
        return {"state": state.value, "ahead": ahead, "behind": behind}

    def refresh_all_versions(self, project_name: str,
                             fetch_first: bool | None = None) -> dict:
        proj = self._require(project_name)
        results: dict[str, dict] = {}
        for label in proj["versions"]:
            try:
                results[label] = self.refresh_sync_status(project_name, label,
                                                          fetch_first)
            except ScaffolderError as exc:
                results[label] = {"state": SyncState.UNKNOWN.value, "ahead": 0,
                                  "behind": 0, "error": redact(str(exc))}
        return results

    def fetch(self, project_name: str) -> dict:
        token = self._token_for(project_name, required=False)
        self.git(project_name).fetch(token=token)
        return {"fetched": True}

    def reset_to_remote(self, project_name: str, label: str) -> dict:
        """Hard-reset the version's branch to its remote counterpart."""
        proj = self._require(project_name)
        version = proj["versions"].get(label)
        if not version:
            raise NotFoundError(f"Version '{label}' not found.")
        git = self.git(project_name)
        branch = version["branch"]
        git.run("reset", "--hard", f"origin/{branch}")
        self.memory.update_version_sync(project_name, label, SyncState.SYNCED)
        return {"reset": True}

    # =============================================================== health

    def repo_health(self, project_name: str, force: bool = False) -> dict:
        """Open issues/PRs for the header status strip (N3)."""
        proj = self.memory.get_project(project_name) or {}
        if not proj.get("repo_url"):
            return {"open_issues": 0, "open_prs": 0, "error": ""}
        if not force:
            cached = self._health_cache.get(project_name)
            if cached is not None:
                return cached
        account = proj.get("owner") or ""
        if not self.credentials.get(account):
            return {"open_issues": 0, "open_prs": 0,
                    "error": "No token for this account."}
        info = parse_remote(proj["repo_url"])
        status = self.github.repo_status(account, info.owner, info.repo)
        self._health_cache.put(project_name, status)
        return status

    def fetch_github_summary(self, project_name: str) -> dict:
        """Open issues + pull requests, shaped for the GitHub tab (N3)."""
        proj = self.memory.get_project(project_name) or {}
        if not proj.get("repo_url"):
            return {"items": [], "error": "This project has no repository."}
        try:
            info = parse_remote(proj["repo_url"])
        except RemoteUnsupportedError:
            return {"items": [], "error": "The remote is not a GitHub repository."}
        if not info.is_github:
            return {"items": [], "error": f"{info.host} is not GitHub."}
        account = proj.get("owner") or info.owner
        if not self.credentials.get(account):
            return {"items": [], "error": f"No token for '{account}'."}
        items: list[dict] = []
        error = ""
        try:
            for review in self.github.list_pull_requests(account, info.owner,
                                                         info.repo):
                items.append({
                    "kind": "Pull request", "number": review.get("number"),
                    "title": review.get("title", ""),
                    "url": review.get("html_url", ""),
                })
        except ScaffolderError as exc:
            error = redact(str(exc))
        try:
            for issue in self.github.list_issues(account, info.owner, info.repo):
                items.append({
                    "kind": "Issue", "number": issue.get("number"),
                    "title": issue.get("title", ""),
                    "url": issue.get("html_url", ""),
                })
        except ScaffolderError as exc:
            error = error or redact(str(exc))
        return {"items": items, "error": error}

    def create_release(self, project_name: str, tag: str, name: str = "",
                       body: str = "", draft: bool = False) -> dict:
        proj = self._require(project_name)
        if not proj.get("repo_url"):
            raise ValidationError("This project has no GitHub repository.")
        info = parse_remote(proj["repo_url"])
        account = proj.get("owner") or info.owner
        return self.github.create_release(account, info.owner, info.repo, tag,
                                          name, body, draft)

    # ============================================================ open help

    def open_in_editor(self, project_name: str, rel_path: str | None = None) -> None:
        proj = self._require(project_name)
        path = Path(proj["path"])
        if not path.exists():
            raise MissingFolderError(str(path))
        if rel_path:
            self.editor().open_file(path, rel_path)
        else:
            self.editor().open(path)

    def open_folder(self, project_name: str) -> None:
        proj = self._require(project_name)
        path = Path(proj["path"])
        if not path.exists():
            raise MissingFolderError(str(path))
        paths.os_open_path(path)

    def open_terminal(self, project_name: str) -> None:
        proj = self._require(project_name)
        self.editor().open_terminal(proj["path"])

    def open_url(self, url: str) -> None:
        import webbrowser

        if url:
            webbrowser.open(url)

    # =========================================================== environment

    def detect_env_type(self, project_name: str) -> str:
        proj = self._require(project_name)
        return EnvDetector(proj["path"]).detect()

    def has_env(self, project_name: str) -> bool:
        proj = self.memory.get_project(project_name)
        if not proj:
            return True
        return EnvDetector(proj["path"]).has_env()

    def setup_env(self, project_name: str, progress: Progress | None = None) -> str:
        proj = self._require(project_name)
        return EnvSetup(proj["path"]).setup(progress)

    def detect_tasks(self, project_name: str) -> dict[str, list[str]]:
        from .env_setup import RunTask

        proj = self._require(project_name)
        return RunTask(proj["path"]).detect_tasks()

    def run_task(self, project_name: str, command: list[str],
                 progress: Progress) -> int:
        from .env_setup import RunTask

        proj = self._require(project_name)
        return RunTask(proj["path"]).run(command, progress)

    # ============================================================= accounts

    def verify_account(self, username: str, token: str) -> dict:
        info = self.github.verify_token(token.strip())
        if username and info["login"].lower() != username.lower():
            raise ValidationError(
                f"This token belongs to '{info['login']}', not '{username}'.",
                hint="Leave the username blank to use the token's account.")
        return info

    def add_account(self, token: str, expected_username: str = "") -> dict:
        info = self.verify_account(expected_username, token)
        login = info["login"]
        self.credentials.set(login, token.strip())
        account = self.memory.add_account(login, orgs=info.get("orgs", []),
                                          github_id=info.get("id"))
        self.memory.set_account_token_state(login, True)
        return account

    def replace_account_token(self, username: str, token: str) -> dict:
        info = self.verify_account(username, token)
        if info["login"].lower() != username.lower():
            raise ValidationError(
                f"This token belongs to '{info['login']}'.")
        self.credentials.set(username, token.strip())
        self.memory.set_account_token_state(username, True)
        self.memory.log_error("auth", f"Token replaced for '{username}'")
        return {"username": username, "ok": True}

    def remove_account(self, username: str) -> None:
        self.memory.remove_account(username)
        self.credentials.delete(username)

    def check_token(self, username: str) -> bool:
        token = self.credentials.get(username)
        if not token:
            self.memory.set_account_token_state(username, False)
            return False
        try:
            self.github.verify_token(token)
            self.memory.set_account_token_state(username, True)
            return True
        except TokenExpiredError:
            self.memory.set_account_token_state(username, False)
            return False
        except ScaffolderError:
            return False

    def check_all_tokens(self) -> dict[str, bool]:
        return {acc["username"]: self.check_token(acc["username"])
                for acc in self.memory.list_accounts()}

    def list_repositories(self, account: str) -> list[dict]:
        return self.github.list_repositories(account)

    # =============================================================== helpers

    def project_status(self, project_name: str) -> dict:
        """Aggregate everything the header needs in one call."""
        proj = self.memory.get_project(project_name)
        if not proj:
            raise NotFoundError(f"Project '{project_name}' not found.")
        info = {
            "exists": Path(proj["path"]).exists(),
            "env_kind": "none",
            "has_env": True,
            "changes": 0,
            "branch": "",
        }
        if info["exists"]:
            info["env_kind"], info["has_env"] = EnvDetector(proj["path"]).describe()
            git = GitRunner(proj["path"])
            if git.is_repo():
                info["branch"] = git.current_branch()
                info["changes"] = len(git.status_entries())
        return info


def _warn_if_unparseable(remote: str) -> str:
    """Log a note when a kept remote is not GitHub (M5).

    The remote itself is preserved verbatim; GitHub features simply stay
    unavailable for that project. Returns the note (empty when nothing to say).
    """
    message = (f"Remote kept as-is (not GitHub): {redact(remote)}. "
               "GitHub features (issues, PRs, repo rename) are unavailable.")
    try:
        info = parse_remote(remote)
    except RemoteUnsupportedError:
        return message
    return "" if info.is_github else message


class _RepoStatusCache:
    """Tiny TTL cache so header refreshes do not hammer the GitHub API (N3)."""

    def __init__(self, ttl: float = 120.0) -> None:
        self.ttl = ttl
        self._data: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> dict | None:
        import time

        entry = self._data.get(key)
        if entry and time.monotonic() - entry[0] < self.ttl:
            return entry[1]
        return None

    def put(self, key: str, value: dict) -> None:
        import time

        self._data[key] = (time.monotonic(), value)

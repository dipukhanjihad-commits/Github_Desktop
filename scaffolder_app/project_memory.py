"""Persistent settings, projects, templates, versions, accounts, errors.

Design notes
------------
* Projects are stored by a **stable id**, never by display name, so renaming
  and deleting cannot collide (fixes the delete-proxy bug, M2).
* Writes are atomic (temp file + ``os.replace``) and guarded by a thread lock
  plus an optional cross-process file lock (S13).
* Schema is versioned with forward/backward migrations.
* A corrupt file is set aside rather than silently overwritten, and the user is
  told (M8/M6).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from . import paths
from .errors import (
    AlreadyExistsError, MemoryError, NotFoundError, ValidationError,
)
from .locking import file_lock

SCHEMA_VERSION = 3
MAX_ERRORS = 300
MAX_TRASH = 25

_RESERVED_WIN = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class RepoState(str, Enum):
    LOCAL_ONLY = "LOCAL_ONLY"
    REPO_PENDING = "REPO_PENDING"
    REPO_CREATED = "REPO_CREATED"
    SYNCED = "SYNCED"


class SyncState(str, Enum):
    UNKNOWN = "UNKNOWN"
    SYNCED = "SYNCED"
    AHEAD = "AHEAD"
    BEHIND = "BEHIND"
    DIVERGED = "DIVERGED"


class Visibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"


_SYNC_PRIORITY = {
    SyncState.UNKNOWN.value: 0,
    SyncState.SYNCED.value: 1,
    SyncState.AHEAD.value: 2,
    SyncState.BEHIND.value: 3,
    SyncState.DIVERGED.value: 4,
}

SWITCH_STRATEGIES = ("stash", "commit", "discard", "abort")


def default_settings() -> dict[str, Any]:
    return {
        "default_visibility": Visibility.PRIVATE.value,
        "default_owner": None,
        "default_branch": "main",
        "auto_create_repo": True,
        "auto_push_after_commit": False,
        "confirm_make_public": True,
        "default_projects_path": str(paths.default_projects_path()),
        "default_ignore": [
            ".git", ".venv", "venv", "__pycache__", "*.pyc", ".pytest_cache",
            "node_modules", "dist", "build", ".next", ".cache",
            ".DS_Store", "Thumbs.db", "*.egg-info", ".mypy_cache", ".ruff_cache",
        ],
        "theme": "system",
        "editor": "vscode",
        "editor_command": "",
        "fetch_on_refresh": True,
        "auto_refresh_seconds": 0,
        "switch_strategy": "stash",
        "confirm_destructive": True,
        "max_commit_title_length": 72,
        "language": "en",
        "update_sync_on_focus": True,
    }


# Kept for callers that imported the old constant.
DEFAULT_SETTINGS = default_settings()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def validate_folder_name(value: str, field: str = "name") -> str:
    """Validate a name used for a folder or repository (M3).

    Rejects empty, path separators, traversal, control characters, reserved
    Windows device names, and trailing dots/spaces.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} cannot be empty.")
    name = value.strip()
    bad = {"/", "\\", "\x00", ":", "*", "?", '"', "<", ">", "|"}
    if any(c in name for c in bad):
        raise ValidationError(
            f"{field} cannot contain any of: / \\ : * ? \" < > |", )
    if name in (".", "..") or ".." in Path(name).parts:
        raise ValidationError(f"{field} cannot contain '..'.")
    if any(ord(c) < 32 for c in name):
        raise ValidationError(f"{field} cannot contain control characters.")
    if name != name.rstrip(". "):
        raise ValidationError(f"{field} cannot end with a dot or space.")
    stem = name.split(".")[0].upper()
    if stem in _RESERVED_WIN:
        raise ValidationError(f"'{name}' is a reserved Windows device name.")
    if len(name) > 100:
        raise ValidationError(f"{field} is too long (max 100 characters).")
    return name


def _coerce_enum(value: Any, enum_cls: type[Enum], field: str) -> str:
    try:
        return enum_cls(value).value
    except ValueError:
        raise ValidationError(
            f"{field} must be one of {[e.value for e in enum_cls]}") from None


class ProjectMemory:
    def __init__(
        self,
        path: Path | str | None = None,
        autosave: bool = True,
        on_change: Callable[[], None] | None = None,
        use_file_lock: bool = True,
    ) -> None:
        self.path = Path(path) if path else paths.memory_path()
        self.autosave = autosave
        self._on_change = on_change
        self._lock = threading.RLock()
        self._use_file_lock = use_file_lock
        self._data: dict[str, Any] = self._fresh()
        self.load_warnings: list[str] = []
        self.load()

    # ------------------------------------------------------------------ core

    def _fresh(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "settings": deepcopy(default_settings()),
            "templates": {},
            "projects": {},
            "accounts": {},
            "errors": [],
            "trash": [],
        }

    def empty(self) -> bool:
        return not self._data["projects"] and not self._data["templates"]

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                with self.path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except json.JSONDecodeError as exc:
                self._quarantine(f"not valid JSON ({exc.msg} at line {exc.lineno})")
                return
            except OSError as exc:
                raise MemoryError(f"Cannot read {self.path}: {exc}") from exc
            if not isinstance(data, dict):
                self._quarantine("root value is not an object")
                return
            try:
                self._migrate(data)
            except Exception as exc:  # pragma: no cover - defensive
                self._quarantine(f"migration failed ({exc})")
                return
            self._data = data

    def _quarantine(self, reason: str) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            os.replace(self.path, backup)
            where = str(backup)
        except OSError:
            where = "(could not be moved aside)"
        msg = (f"memory.json was unreadable: {reason}. "
               f"The file was saved as {where}; starting with empty data.")
        self.load_warnings.append(msg)
        self._data = self._fresh()

    def import_legacy(self, legacy_path: Path | str) -> int:
        """Merge an older ``memory.json`` into this one without losing data.

        Projects are matched by folder path, templates by name and accounts by
        username, so importing twice is a no-op. Returns the number of records
        added.
        """
        source = Path(legacy_path)
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.load_warnings.append(f"Could not import {source}: {exc}")
            return 0
        if not isinstance(data, dict):
            return 0
        try:
            self._migrate(data)
        except Exception as exc:  # noqa: BLE001 - defensive
            self.load_warnings.append(f"Legacy data was skipped: {exc}")
            return 0

        added = 0
        with self._lock:
            known_paths = {str(Path(p["path"])).lower()
                           for p in self._data["projects"].values()}
            names = {p["name"] for p in self._data["projects"].values()}
            for project in data.get("projects", {}).values():
                path = str(Path(project.get("path", "")))
                if not path or path.lower() in known_paths:
                    continue
                name = project.get("name") or Path(path).name
                if name in names:
                    name = f"{name} (imported)"
                project["name"] = name
                project["id"] = project.get("id") or _new_id()
                self._data["projects"][project["id"]] = project
                known_paths.add(path.lower())
                names.add(name)
                added += 1
            for name, template in data.get("templates", {}).items():
                if name not in self._data["templates"]:
                    self._data["templates"][name] = template
                    added += 1
            for username, account in data.get("accounts", {}).items():
                if username not in self._data["accounts"]:
                    account.setdefault("token_ok", False)
                    self._data["accounts"][username] = account
                    added += 1
            for error in data.get("errors", [])[-50:]:
                self._data["errors"].append(error)
            if added:
                self.load_warnings.append(
                    f"Imported {added} item(s) from the previous data location.")
        return added

    def _migrate(self, data: dict[str, Any]) -> None:
        version = int(data.get("schema_version") or 1)
        settings = data.setdefault("settings", {})
        for key, value in default_settings().items():
            settings.setdefault(key, value)

        projects = data.get("projects")
        if isinstance(projects, list):  # very old shape: list of projects
            projects = {p["name"]: p for p in projects if isinstance(p, dict)}
            data["projects"] = projects
        projects = data.setdefault("projects", {})

        if version < 3:
            # v1/v2 keyed projects by name; introduce stable ids.
            migrated: dict[str, Any] = {}
            for key, proj in list(projects.items()):
                if not isinstance(proj, dict):
                    continue
                proj.setdefault("name", key)
                proj.setdefault("id", _new_id())
                migrated[proj["id"]] = proj
            data["projects"] = migrated
            projects = migrated

        data.setdefault("templates", {})
        data.setdefault("accounts", {})
        data.setdefault("errors", [])
        data.setdefault("trash", [])

        for proj in projects.values():
            proj.setdefault("id", _new_id())
            proj.setdefault("versions", {})
            proj.setdefault("repo_state", RepoState.LOCAL_ONLY.value)
            proj.setdefault("current_version", None)
            proj.setdefault("updated_at", proj.get("created_at", _now()))
            proj.setdefault("created_at", _now())
            proj.setdefault("notes", "")
            proj.setdefault("imported", False)
            proj.setdefault("last_refreshed_at", None)
            proj.setdefault("visibility", Visibility.PRIVATE.value)
            proj.setdefault("pinned", False)
            proj.setdefault("group", "")
            proj.setdefault("missing", False)
            proj.setdefault("remote_kind", "github")
            for ver in proj["versions"].values():
                ver.setdefault("sync_state", SyncState.UNKNOWN.value)
                ver.setdefault("ahead", 0)
                ver.setdefault("behind", 0)
                ver.setdefault("pushed_at", None)
        data["schema_version"] = SCHEMA_VERSION

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock_ctx = (file_lock(self.path.with_suffix(".lock"))
                        if self._use_file_lock else _nullcontext())
            try:
                with lock_ctx:
                    self._write_atomic()
            except OSError as exc:
                raise MemoryError(f"Could not save settings: {exc}") from exc

    def _write_atomic(self) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".memory-", suffix=".json",
                                   dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _touch(self) -> None:
        if self.autosave:
            self.save()
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    # --------------------------------------------------------------- settings

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data["settings"].get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._data["settings"][key] = value
            self._touch()

    def set_settings(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._data["settings"].update(values)
            self._touch()

    # ---------------------------------------------------------------- errors

    def log_error(self, context: str, message: str) -> None:
        with self._lock:
            self._data["errors"].append(
                {"at": _now(), "context": context, "message": message})
            if len(self._data["errors"]) > MAX_ERRORS:
                self._data["errors"] = self._data["errors"][-MAX_ERRORS:]
            self._touch()

    def get_errors(self) -> list[dict]:
        with self._lock:
            return deepcopy(self._data["errors"])

    def clear_errors(self) -> None:
        with self._lock:
            self._data["errors"] = []
            self._touch()

    # --------------------------------------------------------------- templates

    def add_template(self, name: str, path: str | Path, description: str = "",
                     ignore: list[str] | None = None,
                     placeholders: dict[str, str] | None = None,
                     ) -> dict[str, Any]:
        name = validate_folder_name(name, "template name")
        with self._lock:
            if self._find_template_key(name):
                raise AlreadyExistsError(f"A template named '{name}' already exists.")
            self._data["templates"][name] = {
                "name": name,
                "path": str(Path(path).expanduser()),
                "description": description,
                "ignore": list(ignore or []),
                "placeholders": dict(placeholders or {}),
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._touch()
            return deepcopy(self._data["templates"][name])

    def _find_template_key(self, name: str) -> str | None:
        for key, tpl in self._data["templates"].items():
            if key.lower() == name.lower():
                return key
        return None

    def update_template(self, name: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            tpl = self._require_template(name)
            if "path" in fields:
                fields["path"] = str(Path(fields["path"]).expanduser())
            for key, value in fields.items():
                tpl[key] = value
            tpl["updated_at"] = _now()
            self._touch()
            return deepcopy(tpl)

    def rename_template(self, old: str, new: str) -> dict[str, Any]:
        new = validate_folder_name(new, "template name")
        with self._lock:
            tpl = self._require_template(old)
            if new != old and self._find_template_key(new):
                raise AlreadyExistsError(f"A template named '{new}' already exists.")
            key = self._find_template_key(old) or old
            self._data["templates"].pop(key, None)
            tpl["name"] = new
            tpl["updated_at"] = _now()
            self._data["templates"][new] = tpl
            self._touch()
            return deepcopy(tpl)

    def remove_template(self, name: str) -> None:
        with self._lock:
            key = self._find_template_key(name)
            if key is None:
                raise NotFoundError(f"Template '{name}' not found.")
            self._data["templates"].pop(key)
            self._touch()

    def get_template(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            key = self._find_template_key(name)
            return deepcopy(self._data["templates"][key]) if key else None

    def list_templates(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            q = query.lower().strip()
            out = [deepcopy(t) for t in self._data["templates"].values()
                   if not q or q in t["name"].lower()
                   or q in t.get("description", "").lower()]
            return sorted(out, key=lambda t: t["name"].lower())

    # ---------------------------------------------------------------- projects

    def _find_project_key_by_name(self, name: str) -> str | None:
        for key, proj in self._data["projects"].items():
            if proj.get("name") == name:
                return key
        return None

    def find_project_path(self, path: str | Path) -> dict[str, Any] | None:
        """Return a project whose folder is ``path`` (or an ancestor of it)."""
        target = Path(path).expanduser().resolve()
        with self._lock:
            for proj in self._data["projects"].values():
                try:
                    proj_path = Path(proj["path"]).expanduser().resolve()
                except OSError:
                    continue
                if target == proj_path or proj_path in target.parents:
                    return deepcopy(proj)
        return None

    def add_project(self, name: str, path: str | Path, template: str | None = None,
                    visibility: str | None = None, owner: str | None = None,
                    imported: bool = False, group: str = "",
                    ) -> dict[str, Any]:
        name = validate_folder_name(name, "project name")
        resolved = Path(path).expanduser()
        with self._lock:
            if self._find_project_key_by_name(name):
                raise AlreadyExistsError(f"A project named '{name}' already exists.")
            existing = self.find_project_path(resolved)
            if existing:
                raise AlreadyExistsError(
                    f"That folder is already managed as '{existing['name']}'.")
            if template is not None and not self._find_template_key(template):
                raise NotFoundError(f"Template '{template}' not found.")
            vis = visibility or self.get_setting("default_visibility") \
                or Visibility.PRIVATE.value
            vis = _coerce_enum(vis, Visibility, "visibility")
            project_id = _new_id()
            self._data["projects"][project_id] = {
                "id": project_id,
                "name": name,
                "path": str(resolved),
                "template": template,
                "imported": imported,
                "visibility": vis,
                "owner": owner or self.get_setting("default_owner"),
                "repo_url": None,
                "remote_kind": "github",
                "repo_state": RepoState.LOCAL_ONLY.value,
                "current_version": None,
                "versions": {},
                "notes": "",
                "group": group,
                "pinned": False,
                "missing": False,
                "created_at": _now(),
                "updated_at": _now(),
                "last_refreshed_at": None,
            }
            self._touch()
            return deepcopy(self._data["projects"][project_id])

    def remove_project(self, name: str) -> None:
        with self._lock:
            key = self._find_project_key_by_name(name)
            if key is None:
                raise NotFoundError(f"Project '{name}' not found.")
            self._data["projects"].pop(key)
            self._touch()

    def rename_project(self, old: str, new: str) -> dict[str, Any]:
        new = validate_folder_name(new, "project name")
        with self._lock:
            key = self._find_project_key_by_name(old)
            if key is None:
                raise NotFoundError(f"Project '{old}' not found.")
            clash = self._find_project_key_by_name(new)
            if clash and clash != key:
                raise AlreadyExistsError(f"A project named '{new}' already exists.")
            proj = self._data["projects"][key]
            proj["name"] = new
            proj["updated_at"] = _now()
            self._touch()
            return deepcopy(proj)

    def get_project(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            key = self._find_project_key_by_name(name)
            return deepcopy(self._data["projects"][key]) if key else None

    def get_project_by_id(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            proj = self._data["projects"].get(project_id)
            return deepcopy(proj) if proj else None

    def list_projects(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            q = query.lower().strip()
            out = []
            for proj in self._data["projects"].values():
                if q and not (q in proj["name"].lower()
                              or q in proj.get("notes", "").lower()
                              or q in proj.get("group", "").lower()):
                    continue
                out.append(deepcopy(proj))
            return sorted(out, key=lambda p: (not p.get("pinned"),
                                              p["name"].lower()))

    def set_project_field(self, name: str, **fields: Any) -> None:
        with self._lock:
            proj = self._require_project(name)
            for key, value in fields.items():
                proj[key] = value
            proj["updated_at"] = _now()
            self._touch()

    def set_notes(self, project_name: str, notes: str) -> None:
        self.set_project_field(project_name, notes=notes)

    def set_visibility(self, project_name: str, visibility: str) -> None:
        visibility = _coerce_enum(visibility, Visibility, "visibility")
        self.set_project_field(project_name, visibility=visibility)

    def set_pinned(self, project_name: str, pinned: bool) -> None:
        self.set_project_field(project_name, pinned=bool(pinned))

    def set_group(self, project_name: str, group: str) -> None:
        self.set_project_field(project_name, group=group)

    def set_missing(self, project_name: str, missing: bool) -> None:
        self.set_project_field(project_name, missing=bool(missing))

    def relink_project(self, project_name: str, new_path: str | Path) -> dict:
        resolved = Path(new_path).expanduser()
        with self._lock:
            proj = self._require_project(project_name)
            proj["path"] = str(resolved)
            proj["missing"] = not resolved.exists()
            proj["updated_at"] = _now()
            self._touch()
            return deepcopy(proj)

    # ---------------------------------------------------------------- versions

    def add_version(self, project_name: str, label: str, parent: str | None = None,
                    branch: str | None = None) -> dict[str, Any]:
        label = validate_folder_name(label, "version label")
        with self._lock:
            proj = self._require_project(project_name)
            if label in proj["versions"]:
                raise AlreadyExistsError(
                    f"Version '{label}' already exists in '{project_name}'.")
            if parent is not None and parent not in proj["versions"]:
                raise NotFoundError(f"Parent version '{parent}' not found.")
            branch = branch or f"version/{label}"
            proj["versions"][label] = {
                "label": label,
                "branch": branch,
                "parent": parent,
                "created_at": _now(),
                "sync_state": SyncState.UNKNOWN.value,
                "ahead": 0,
                "behind": 0,
                "pushed_at": None,
            }
            if proj["current_version"] is None:
                proj["current_version"] = label
            proj["updated_at"] = _now()
            self._touch()
            return deepcopy(proj["versions"][label])

    def remove_version(self, project_name: str, label: str,
                       force: bool = False) -> None:
        with self._lock:
            proj = self._require_project(project_name)
            if label not in proj["versions"]:
                raise NotFoundError(f"Version '{label}' not found.")
            if label == proj["current_version"] and not force:
                raise ValidationError(
                    "Cannot remove the current version; switch first.")
            children = [v for v in proj["versions"].values() if v["parent"] == label]
            if children and not force:
                raise ValidationError(
                    f"Version has {len(children)} child(ren); re-parent first.")
            if force:
                grandparent = proj["versions"][label]["parent"]
                for child in children:
                    child["parent"] = grandparent
            del proj["versions"][label]
            if proj["current_version"] == label:
                proj["current_version"] = next(iter(proj["versions"]), None)
            proj["updated_at"] = _now()
            self._touch()

    def switch_version(self, project_name: str, label: str) -> None:
        with self._lock:
            proj = self._require_project(project_name)
            if label not in proj["versions"]:
                raise NotFoundError(f"Version '{label}' not found.")
            proj["current_version"] = label
            proj["updated_at"] = _now()
            self._touch()

    def update_version_sync(self, project_name: str, label: str,
                            state: SyncState | str, ahead: int = 0,
                            behind: int = 0) -> None:
        state = _coerce_enum(state, SyncState, "sync_state")
        with self._lock:
            proj = self._require_project(project_name)
            ver = proj["versions"].get(label)
            if ver is None:
                raise NotFoundError(f"Version '{label}' not found.")
            ver["sync_state"] = state
            ver["ahead"] = int(ahead)
            ver["behind"] = int(behind)
            ver["pushed_at"] = _now()
            proj["updated_at"] = _now()
            proj["last_refreshed_at"] = _now()
            self._touch()

    def touch_refreshed(self, project_name: str) -> None:
        with self._lock:
            try:
                proj = self._require_project(project_name)
            except NotFoundError:
                return
            proj["last_refreshed_at"] = _now()
            self._touch()

    # ------------------------------------------------------------ repo linkage

    def attach_repo(self, project_name: str, owner: str, repo_url: str,
                    visibility: str | None = None,
                    state: RepoState | str = RepoState.REPO_CREATED,
                    remote_kind: str = "github") -> None:
        state = _coerce_enum(state, RepoState, "repo_state")
        with self._lock:
            proj = self._require_project(project_name)
            proj["owner"] = owner
            proj["repo_url"] = repo_url
            proj["repo_state"] = state
            proj["remote_kind"] = remote_kind
            if visibility:
                proj["visibility"] = _coerce_enum(visibility, Visibility,
                                                  "visibility")
            proj["updated_at"] = _now()
            self._touch()

    def set_repo_state(self, project_name: str, state: RepoState | str) -> None:
        state = _coerce_enum(state, RepoState, "repo_state")
        self.set_project_field(project_name, repo_state=state)

    def mark_repo_pending(self, project_name: str) -> None:
        self.set_repo_state(project_name, RepoState.REPO_PENDING)

    def unlink_repo(self, project_name: str) -> None:
        with self._lock:
            proj = self._require_project(project_name)
            proj["repo_url"] = None
            proj["repo_state"] = RepoState.LOCAL_ONLY.value
            proj["updated_at"] = _now()
            self._touch()

    # ---------------------------------------------------------------- accounts

    def add_account(self, username: str, orgs: list[str] | None = None,
                    github_id: int | None = None) -> dict[str, Any]:
        username = validate_folder_name(username, "username")
        with self._lock:
            noreply = (f"{github_id}+{username}@users.noreply.github.com"
                       if github_id else f"{username}@users.noreply.github.com")
            self._data["accounts"][username] = {
                "username": username,
                "orgs": list(orgs or []),
                "github_id": github_id,
                "noreply_email": noreply,
                "added_at": _now(),
                "token_ok": True,
                "last_checked": _now(),
            }
            self._touch()
            return deepcopy(self._data["accounts"][username])

    def set_account_token_state(self, username: str, ok: bool) -> None:
        with self._lock:
            acc = self._data["accounts"].get(username)
            if not acc:
                return
            acc["token_ok"] = bool(ok)
            acc["last_checked"] = _now()
            self._touch()

    def remove_account(self, username: str) -> None:
        with self._lock:
            self._data["accounts"].pop(username, None)
            self._touch()

    def get_account(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            acc = self._data["accounts"].get(username)
            return deepcopy(acc) if acc else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(a) for a in self._data["accounts"].values()]

    # ------------------------------------------------------------------- trash

    def add_trash_entry(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._data["trash"].insert(0, entry)
            del self._data["trash"][MAX_TRASH:]
            self._touch()

    def list_trash(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._data["trash"])

    def remove_trash_entry(self, stamp: str) -> None:
        with self._lock:
            self._data["trash"] = [t for t in self._data["trash"]
                                   if t.get("stamp") != stamp]
            self._touch()

    # ------------------------------------------------------------- aggregation

    def get_project_summary(self, project_name: str) -> dict[str, Any]:
        with self._lock:
            proj = self._require_project(project_name)
            counts = {state.value: 0 for state in SyncState}
            ahead_total = behind_total = 0
            worst = SyncState.UNKNOWN.value
            for ver in proj["versions"].values():
                state = ver.get("sync_state", SyncState.UNKNOWN.value)
                counts[state] = counts.get(state, 0) + 1
                ahead_total += int(ver.get("ahead", 0))
                behind_total += int(ver.get("behind", 0))
                if _SYNC_PRIORITY.get(state, 0) > _SYNC_PRIORITY.get(worst, 0):
                    worst = state
            return {
                "repo_state": proj["repo_state"],
                "visibility": proj["visibility"],
                "current_version": proj["current_version"],
                "worst_sync": worst,
                "counts": counts,
                "ahead_total": ahead_total,
                "behind_total": behind_total,
                "version_count": len(proj["versions"]),
            }

    def _require_project(self, name: str) -> dict[str, Any]:
        key = self._find_project_key_by_name(name)
        if key is None:
            raise NotFoundError(f"Project '{name}' not found.")
        return self._data["projects"][key]

    def _require_template(self, name: str) -> dict[str, Any]:
        key = self._find_template_key(name)
        if key is None:
            raise NotFoundError(f"Template '{name}' not found.")
        return self._data["templates"][key]

    def __repr__(self) -> str:
        return (f"ProjectMemory(path={self.path!s}, "
                f"templates={len(self._data['templates'])}, "
                f"projects={len(self._data['projects'])})")


class _nullcontext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> None:
        return None

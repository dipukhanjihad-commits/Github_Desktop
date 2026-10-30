"""
project_memory.py — persistent settings, projects, templates, versions, accounts, errors.
Thread-safe, atomic saves, schema-versioned, corrupt-file safe.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


SCHEMA_VERSION = 2
MAX_ERRORS = 300


class RepoState(str, Enum):
    LOCAL_ONLY   = "LOCAL_ONLY"
    REPO_PENDING = "REPO_PENDING"
    REPO_CREATED = "REPO_CREATED"
    SYNCED       = "SYNCED"


class SyncState(str, Enum):
    UNKNOWN  = "UNKNOWN"
    SYNCED   = "SYNCED"
    AHEAD    = "AHEAD"
    BEHIND   = "BEHIND"
    DIVERGED = "DIVERGED"


class Visibility(str, Enum):
    PRIVATE = "private"
    PUBLIC  = "public"


_SYNC_PRIORITY = {
    SyncState.UNKNOWN.value:  0,
    SyncState.SYNCED.value:   1,
    SyncState.AHEAD.value:    2,
    SyncState.BEHIND.value:   3,
    SyncState.DIVERGED.value: 4,
}


DEFAULT_SETTINGS: dict[str, Any] = {
    "default_visibility":     Visibility.PRIVATE.value,
    "default_owner":          None,
    "default_branch":         "main",
    "auto_create_repo":       True,
    "auto_push_after_commit": False,
    "confirm_make_public":    True,
    "default_projects_path":  str(Path.home() / "projects"),
    "default_ignore": [
        ".git", ".venv", "venv", "__pycache__", "*.pyc", ".pytest_cache",
        "node_modules", "dist", "build", ".next", ".cache",
        ".DS_Store", "Thumbs.db",
    ],
    "theme":                  "system",
}


class ProjectMemoryError(Exception): ...
class NotFoundError(ProjectMemoryError): ...
class AlreadyExistsError(ProjectMemoryError): ...
class ValidationError(ProjectMemoryError): ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_name(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    if any(c in value for c in ("/", "\\", "\x00")):
        raise ValidationError(f"{field} cannot contain path separators")
    return value.strip()


def _coerce_enum(value: Any, enum_cls: type[Enum], field: str) -> str:
    try:
        return enum_cls(value).value
    except ValueError:
        raise ValidationError(f"{field} must be one of {[e.value for e in enum_cls]}")


class ProjectMemory:
    def __init__(
        self,
        path: Optional[Path | str] = None,
        autosave: bool = True,
        on_change: Optional[Callable[[], None]] = None,
    ):
        self.path = Path(path).expanduser() if path else Path.home() / ".scaffolder" / "memory.json"
        self.autosave = autosave
        self._on_change = on_change
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self._fresh()
        self.load()

    # ------------------------------------------------------------------ core

    def _fresh(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "settings":       deepcopy(DEFAULT_SETTINGS),
            "templates":      {},
            "projects":       {},
            "accounts":       {},
            "errors":         [],
        }

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._backup_corrupt()
                return
            if not isinstance(data, dict):
                self._backup_corrupt()
                return
            self._migrate(data)
            self._data = data

    def _backup_corrupt(self) -> None:
        try:
            backup = self.path.with_suffix(self.path.suffix + ".bak")
            os.replace(self.path, backup)
        except OSError:
            pass

    def _migrate(self, data: dict[str, Any]) -> None:
        data.setdefault("schema_version", SCHEMA_VERSION)
        settings = data.setdefault("settings", {})
        for k, v in DEFAULT_SETTINGS.items():
            settings.setdefault(k, v)
        data.setdefault("templates", {})
        data.setdefault("projects", {})
        data.setdefault("accounts", {})
        data.setdefault("errors", [])
        for proj in data["projects"].values():
            proj.setdefault("versions", {})
            proj.setdefault("repo_state", RepoState.LOCAL_ONLY.value)
            proj.setdefault("current_version", None)
            proj.setdefault("updated_at", proj.get("created_at", _now()))
            proj.setdefault("notes", "")
            proj.setdefault("imported", False)
            proj.setdefault("last_refreshed_at", None)
            for ver in proj["versions"].values():
                ver.setdefault("sync_state", SyncState.UNKNOWN.value)
                ver.setdefault("ahead", 0)
                ver.setdefault("behind", 0)
        data["schema_version"] = SCHEMA_VERSION

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".memory-", suffix=".json", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, indent=2, ensure_ascii=False)
                os.replace(tmp, self.path)
            except Exception:
                try: os.unlink(tmp)
                except OSError: pass
                raise

    def _touch(self) -> None:
        if self.autosave:
            self.save()
        if self._on_change is not None:
            try: self._on_change()
            except Exception: pass

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
            for k, v in values.items():
                self._data["settings"][k] = v
            self._touch()

    # ---------------------------------------------------------------- errors

    def log_error(self, context: str, message: str) -> None:
        with self._lock:
            self._data["errors"].append({
                "at":      _now(),
                "context": context,
                "message": message,
            })
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

    def add_template(
        self, name: str, path: str | Path, description: str = "",
        ignore: Optional[list[str]] = None,
        placeholders: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        name = _validate_name(name, "template name")
        with self._lock:
            if name in self._data["templates"]:
                raise AlreadyExistsError(f"template '{name}' already exists")
            self._data["templates"][name] = {
                "name":         name,
                "path":         str(Path(path).expanduser()),
                "description":  description,
                "ignore":       list(ignore or []),
                "placeholders": dict(placeholders or {}),
                "created_at":   _now(),
                "updated_at":   _now(),
            }
            self._touch()
            return deepcopy(self._data["templates"][name])

    def update_template(self, name: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            tpl = self._require_template(name)
            if "path" in fields:
                fields["path"] = str(Path(fields["path"]).expanduser())
            for k, v in fields.items():
                tpl[k] = v
            tpl["updated_at"] = _now()
            self._touch()
            return deepcopy(tpl)

    def remove_template(self, name: str) -> None:
        with self._lock:
            self._data["templates"].pop(name, None)
            self._touch()

    def get_template(self, name: str) -> Optional[dict[str, Any]]:
        with self._lock:
            tpl = self._data["templates"].get(name)
            return deepcopy(tpl) if tpl else None

    def list_templates(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            q = query.lower().strip()
            out = [deepcopy(t) for t in self._data["templates"].values()
                   if not q or q in t["name"].lower() or q in t.get("description", "").lower()]
            return sorted(out, key=lambda t: t["name"].lower())

    # ---------------------------------------------------------------- projects

    def add_project(
        self, name: str, path: str | Path, template: Optional[str] = None,
        visibility: Optional[str] = None, owner: Optional[str] = None,
        imported: bool = False,
    ) -> dict[str, Any]:
        name = _validate_name(name, "project name")
        with self._lock:
            if name in self._data["projects"]:
                raise AlreadyExistsError(f"project '{name}' already exists")
            if template is not None and template not in self._data["templates"]:
                raise NotFoundError(f"template '{template}' not found")
            vis = visibility or self.get_setting("default_visibility") or Visibility.PRIVATE.value
            vis = _coerce_enum(vis, Visibility, "visibility")
            self._data["projects"][name] = {
                "name":              name,
                "path":              str(Path(path).expanduser()),
                "template":          template,
                "imported":          imported,
                "visibility":        vis,
                "owner":             owner or self.get_setting("default_owner"),
                "repo_url":          None,
                "repo_state":        RepoState.LOCAL_ONLY.value,
                "current_version":   None,
                "versions":          {},
                "notes":             "",
                "created_at":        _now(),
                "updated_at":        _now(),
                "last_refreshed_at": None,
            }
            self._touch()
            return deepcopy(self._data["projects"][name])

    def remove_project(self, name: str) -> None:
        with self._lock:
            self._data["projects"].pop(name, None)
            self._touch()

    def rename_project(self, old: str, new: str) -> dict[str, Any]:
        new = _validate_name(new, "project name")
        with self._lock:
            if old not in self._data["projects"]:
                raise NotFoundError(f"project '{old}' not found")
            if new in self._data["projects"]:
                raise AlreadyExistsError(f"project '{new}' already exists")
            proj = self._data["projects"].pop(old)
            proj["name"] = new
            proj["updated_at"] = _now()
            self._data["projects"][new] = proj
            self._touch()
            return deepcopy(proj)

    def get_project(self, name: str) -> Optional[dict[str, Any]]:
        with self._lock:
            proj = self._data["projects"].get(name)
            return deepcopy(proj) if proj else None

    def list_projects(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            q = query.lower().strip()
            out = [deepcopy(p) for p in self._data["projects"].values()
                   if not q or q in p["name"].lower() or q in p.get("notes", "").lower()]
            return sorted(out, key=lambda p: p["name"].lower())

    def set_notes(self, project_name: str, notes: str) -> None:
        with self._lock:
            proj = self._require_project(project_name)
            proj["notes"] = notes
            proj["updated_at"] = _now()
            self._touch()

    def set_visibility(self, project_name: str, visibility: str) -> None:
        visibility = _coerce_enum(visibility, Visibility, "visibility")
        with self._lock:
            proj = self._require_project(project_name)
            proj["visibility"] = visibility
            proj["updated_at"] = _now()
            self._touch()

    # ---------------------------------------------------------------- versions

    def add_version(
        self, project_name: str, label: str, parent: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> dict[str, Any]:
        label = _validate_name(label, "version label")
        with self._lock:
            proj = self._require_project(project_name)
            if label in proj["versions"]:
                raise AlreadyExistsError(f"version '{label}' already exists in '{project_name}'")
            if parent is not None and parent not in proj["versions"]:
                raise NotFoundError(f"parent version '{parent}' not found")
            branch = branch or f"version/{label}"
            proj["versions"][label] = {
                "label":      label,
                "branch":     branch,
                "parent":     parent,
                "created_at": _now(),
                "sync_state": SyncState.UNKNOWN.value,
                "ahead":      0,
                "behind":     0,
            }
            if proj["current_version"] is None:
                proj["current_version"] = label
            proj["updated_at"] = _now()
            self._touch()
            return deepcopy(proj["versions"][label])

    def remove_version(self, project_name: str, label: str, force: bool = False) -> None:
        with self._lock:
            proj = self._require_project(project_name)
            if label not in proj["versions"]:
                raise NotFoundError(f"version '{label}' not found")
            if label == proj["current_version"] and not force:
                raise ValidationError("cannot remove the current version; switch first")
            children = [v for v in proj["versions"].values() if v["parent"] == label]
            if children and not force:
                raise ValidationError(f"version has {len(children)} child(ren); re-parent first")
            if force:
                grandparent = proj["versions"][label]["parent"]
                for c in children:
                    c["parent"] = grandparent
            del proj["versions"][label]
            if proj["current_version"] == label:
                proj["current_version"] = next(iter(proj["versions"]), None)
            proj["updated_at"] = _now()
            self._touch()

    def switch_version(self, project_name: str, label: str) -> None:
        with self._lock:
            proj = self._require_project(project_name)
            if label not in proj["versions"]:
                raise NotFoundError(f"version '{label}' not found")
            proj["current_version"] = label
            proj["updated_at"] = _now()
            self._touch()

    def update_version_sync(
        self, project_name: str, label: str, state: SyncState | str,
        ahead: int = 0, behind: int = 0,
    ) -> None:
        state = _coerce_enum(state, SyncState, "sync_state")
        with self._lock:
            proj = self._require_project(project_name)
            ver = proj["versions"].get(label)
            if ver is None:
                raise NotFoundError(f"version '{label}' not found")
            ver["sync_state"] = state
            ver["ahead"]      = int(ahead)
            ver["behind"]     = int(behind)
            proj["updated_at"] = _now()
            proj["last_refreshed_at"] = _now()
            self._touch()

    # ------------------------------------------------------------ repo linkage

    def attach_repo(
        self, project_name: str, owner: str, repo_url: str,
        visibility: Optional[str] = None,
        state: RepoState | str = RepoState.REPO_CREATED,
    ) -> None:
        state = _coerce_enum(state, RepoState, "repo_state")
        with self._lock:
            proj = self._require_project(project_name)
            proj["owner"]      = owner
            proj["repo_url"]   = repo_url
            proj["repo_state"] = state
            if visibility:
                proj["visibility"] = _coerce_enum(visibility, Visibility, "visibility")
            proj["updated_at"] = _now()
            self._touch()

    def set_repo_state(self, project_name: str, state: RepoState | str) -> None:
        state = _coerce_enum(state, RepoState, "repo_state")
        with self._lock:
            proj = self._require_project(project_name)
            proj["repo_state"] = state
            proj["updated_at"] = _now()
            self._touch()

    def mark_repo_pending(self, project_name: str) -> None:
        self.set_repo_state(project_name, RepoState.REPO_PENDING)

    def unlink_repo(self, project_name: str) -> None:
        with self._lock:
            proj = self._require_project(project_name)
            proj["repo_url"]   = None
            proj["repo_state"] = RepoState.LOCAL_ONLY.value
            proj["updated_at"] = _now()
            self._touch()

    # ---------------------------------------------------------------- accounts

    def add_account(
        self,
        username: str,
        orgs: Optional[list[str]] = None,
        github_id: Optional[int] = None,
    ) -> dict[str, Any]:
        username = _validate_name(username, "username")
        with self._lock:
            noreply = (
                f"{github_id}+{username}@users.noreply.github.com"
                if github_id
                else f"{username}@users.noreply.github.com"
            )
            self._data["accounts"][username] = {
                "username":      username,
                "orgs":          list(orgs or []),
                "github_id":     github_id,
                "noreply_email": noreply,
                "added_at":      _now(),
            }
            self._touch()
            return deepcopy(self._data["accounts"][username])

    def remove_account(self, username: str) -> None:
        with self._lock:
            self._data["accounts"].pop(username, None)
            self._touch()

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(a) for a in self._data["accounts"].values()]

    # ------------------------------------------------------------- aggregation

    def get_project_summary(self, project_name: str) -> dict[str, Any]:
        with self._lock:
            proj = self._require_project(project_name)
            counts = {s.value: 0 for s in SyncState}
            ahead_total = behind_total = 0
            worst = SyncState.UNKNOWN.value
            for ver in proj["versions"].values():
                state = ver.get("sync_state", SyncState.UNKNOWN.value)
                counts[state] = counts.get(state, 0) + 1
                ahead_total  += int(ver.get("ahead", 0))
                behind_total += int(ver.get("behind", 0))
                if _SYNC_PRIORITY.get(state, 0) > _SYNC_PRIORITY.get(worst, 0):
                    worst = state
            return {
                "repo_state":       proj["repo_state"],
                "visibility":       proj["visibility"],
                "current_version":  proj["current_version"],
                "worst_sync":       worst,
                "counts":           counts,
                "ahead_total":      ahead_total,
                "behind_total":     behind_total,
                "version_count":    len(proj["versions"]),
            }

    def _require_project(self, name: str) -> dict[str, Any]:
        proj = self._data["projects"].get(name)
        if proj is None:
            raise NotFoundError(f"project '{name}' not found")
        return proj

    def _require_template(self, name: str) -> dict[str, Any]:
        tpl = self._data["templates"].get(name)
        if tpl is None:
            raise NotFoundError(f"template '{name}' not found")
        return tpl

    def __repr__(self) -> str:
        return (f"ProjectMemory(path={self.path!s}, "
                f"templates={len(self._data['templates'])}, "
                f"projects={len(self._data['projects'])})")
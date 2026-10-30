"""
scaffolder.py — backend: files, git, GitHub, env setup, VS Code integration.
Every method is synchronous; the GUI wraps calls in background threads.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from project_memory import (
    ProjectMemory, RepoState, SyncState, Visibility,
    NotFoundError, AlreadyExistsError,
)


class ScaffolderError(Exception): ...
class TokenExpiredError(ScaffolderError):
    def __init__(self, account: str):
        self.account = account
        super().__init__(f"Token for '{account}' is invalid or expired")
class DivergedError(ScaffolderError):
    def __init__(self, ahead: int, behind: int):
        self.ahead = ahead
        self.behind = behind
        super().__init__(f"Branch has diverged: {ahead} ahead, {behind} behind")


class Scaffolder:
    def __init__(self, memory, credentials_path=None):
        self.memory = memory
        self.credentials_path = (
            Path(credentials_path).expanduser() if credentials_path
            else Path.home() / ".scaffolder" / "credentials.json"
        )
        self._tokens: dict[str, str] = self._load_tokens()

    # ---------------------------------------------------------- credentials

    def _load_tokens(self):
        if not self.credentials_path.exists():
            return {}
        try:
            with self.credentials_path.open("r", encoding="utf-8") as f:
                return dict(json.load(f).get("tokens", {}))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_tokens(self):
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"tokens": self._tokens}, indent=2)
        fd = os.open(str(self.credentials_path),
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)

    def set_token(self, username, token):
        self._tokens[username] = token.strip()
        self._save_tokens()

    def get_token(self, username):
        return self._tokens.get(username)

    def has_token(self, username):
        return bool(self._tokens.get(username))

    def delete_token(self, username):
        self._tokens.pop(username, None)
        self._save_tokens()

    def check_token(self, account):
        token = self.get_token(account)
        if not token:
            return False
        try:
            self._github_api("GET", "/user", token, account)
            return True
        except TokenExpiredError:
            return False
        except ScaffolderError:
            return False

    # ---------------------------------------------------------- subprocess

    def _run(self, args, cwd=None, env=None, check=True, timeout=300):
        try:
            result = subprocess.run(
                args, cwd=str(cwd) if cwd else None, env=env,
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError as e:
            raise ScaffolderError(f"Command not found: {args[0]} ({e})")
        except subprocess.TimeoutExpired:
            raise ScaffolderError(
                f"Timed out after {timeout}s: {' '.join(map(str, args))}"
            )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise ScaffolderError(f"{' '.join(map(str, args))} failed:\n{detail}")
        return result

    def _git(self, cwd, *args, check=True):
        return self._run(["git", *args], cwd=cwd, check=check)

    def _git_with_token(self, cwd, *args, token, check=True):
        env = os.environ.copy()
        fd, helper = tempfile.mkstemp(prefix="askpass-", suffix=".sh")
        try:
            os.write(fd, b'#!/bin/sh\necho "$GIT_TOKEN"\n')
        finally:
            os.close(fd)
        os.chmod(helper, 0o700)
        env["GIT_ASKPASS"] = helper
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_TOKEN"] = token
        try:
            # -c credential.helper= clears any inherited helper (GitHub Desktop,
            # osxkeychain, manager-core, etc.) so git actually invokes GIT_ASKPASS.
            return self._run(["git", "-c", "credential.helper=", *args],
                              cwd=cwd, env=env, check=check)
        finally:
            try: os.unlink(helper)
            except OSError: pass

    def _ensure_git_identity(self, cwd):
        name = self._git(cwd, "config", "user.name", check=False)
        if name.returncode != 0 or not name.stdout.strip():
            self._git(cwd, "config", "user.name", "Scaffolder")
        email = self._git(cwd, "config", "user.email", check=False)
        if email.returncode != 0 or not email.stdout.strip():
            self._git(cwd, "config", "user.email", "scaffolder@localhost")
    def _set_project_identity(self, dest, account):
        """Force per-project git identity to the given connected account,
        and clear any inherited credential helper for HTTPS pushes."""
        if not account:
            return
        info = next(
            (a for a in self.memory.list_accounts() if a["username"] == account),
            None,
        )
        if not info:
            return
        email = info.get("noreply_email") or f"{account}@users.noreply.github.com"
        self._git(dest, "config", "user.name", account, check=False)
        self._git(dest, "config", "user.email", email, check=False)
        # Local credential helper reset — prevents GitHub Desktop's cached creds
        # from hijacking pushes for THIS repo.
        self._git(dest, "config", "credential.helper", "", check=False)
   
    def fix_git_identity(self, project_name):
        """Apply the connected account as git identity for this project.
        Only affects FUTURE commits — existing commits keep their author."""
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        account = proj.get("owner")
        if not account:
            raise ScaffolderError("Project has no connected GitHub account")
        info = next(
            (a for a in self.memory.list_accounts() if a["username"] == account),
            None,
        )
        if not info:
            raise ScaffolderError(
                f"Account '{account}' is not registered — add it first.")
        self._set_project_identity(Path(proj["path"]), account)
        return {
            "name":  info["username"],
            "email": info.get("noreply_email"),
        }
    # ---------------------------------------------------------- helpers

    def _matches_ignore(self, rel_parts, patterns):
        rel = "/".join(rel_parts)
        for pat in patterns:
            pat = pat.strip()
            if not pat: continue
            if pat.startswith("*.") and rel_parts and rel_parts[-1].endswith(pat[1:]):
                return True
            if pat in rel_parts: return True
            if rel == pat or rel.startswith(pat.rstrip("/") + "/"): return True
        return False

    def _iter_source_files(self, source, ignore):
        for path in sorted(source.rglob("*")):
            if not path.is_file(): continue
            rel_parts = path.relative_to(source).parts
            if self._matches_ignore(rel_parts, ignore): continue
            yield path

    def _apply_placeholders(self, text, values):
        result = text
        for key, val in values.items():
            for form in (f"{{{{{key}}}}}", f"{{{{{key.upper()}}}}}",
                          f"{{{{{key.lower()}}}}}", f"{{{{ {key} }}}}"):
                result = result.replace(form, str(val))
        return result

    def _copy_template(self, template, dest, values):
        source = Path(template["path"]).expanduser()
        if not source.exists():
            raise ScaffolderError(f"Template source folder missing: {source}")
        ignore = template.get("ignore", []) or []
        dest.mkdir(parents=True, exist_ok=True)
        for src in self._iter_source_files(source, ignore):
            rel_parts = src.relative_to(source).parts
            new_parts = [self._apply_placeholders(p, values) for p in rel_parts]
            target = dest.joinpath(*new_parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                content = src.read_text(encoding="utf-8")
                target.write_text(self._apply_placeholders(content, values),
                                  encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                shutil.copy2(src, target)
        gitignore = dest / ".gitignore"
        if not gitignore.exists() and ignore:
            gitignore.write_text("\n".join(ignore) + "\n", encoding="utf-8")

    def _parse_repo_from_url(self, url):
        m = re.search(r"github\.com[:/]([^/]+)/([^/\s.]+?)(?:\.git)?/?$", url)
        if not m:
            raise ScaffolderError(f"Cannot parse repo URL: {url}")
        return m.group(1), m.group(2)

    # ---------------------------------------------------------- templates

    def create_template_from_folder(self, name, source_folder, description="",
                                     ignore=None, placeholders=None):
        src = Path(source_folder).expanduser().resolve()
        if not src.exists() or not src.is_dir():
            raise ScaffolderError(f"Not a folder: {src}")
        return self.memory.add_template(
            name=name, path=str(src), description=description,
            ignore=ignore or self.memory.get_setting("default_ignore"),
            placeholders=placeholders or {},
        )

    def preview_template(self, template_name):
        tpl = self.memory.get_template(template_name)
        if not tpl:
            raise ScaffolderError(f"Template not found: {template_name}")
        src = Path(tpl["path"]).expanduser()
        if not src.exists():
            return {"path": str(src), "files": [], "error": "source missing",
                    "placeholders": tpl.get("placeholders", {})}
        files = []
        for p in self._iter_source_files(src, tpl.get("ignore", []) or []):
            files.append(str(p.relative_to(src)))
        return {
            "path": str(src),
            "files": files,
            "placeholders": tpl.get("placeholders", {}),
            "ignore": tpl.get("ignore", []),
        }

    # ---------------------------------------------------------- GitHub API

    def _github_api(self, method, path, token, account=None, body=None):
        url = f"https://api.github.com{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "scaffolder-gui",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 204: return {}
                raw = resp.read().decode()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            if e.code == 401:
                raise TokenExpiredError(account or "unknown")
            raise ScaffolderError(f"GitHub {e.code}: {body_text}")
        except urllib.error.URLError as e:
            raise ScaffolderError(f"Network error: {e.reason}")

    def verify_account(self, username, token):
        user = self._github_api("GET", "/user", token)
        orgs = self._github_api("GET", "/user/orgs", token)
        return {
            "login":     user["login"],
            "id":        user.get("id", 0),
            "name":      user.get("name") or "",
            "orgs":      [o["login"] for o in orgs],
        }

    def list_repositories(self, account, max_pages=3):
        token = self.get_token(account)
        if not token:
            raise ScaffolderError(f"No token for '{account}'")
        repos = []
        for page in range(1, max_pages + 1):
            batch = self._github_api(
                "GET",
                "/user/repos?per_page=100&sort=updated"
                f"&affiliation=owner,collaborator,organization_member&page={page}",
                token, account,
            )
            if not batch: break
            repos.extend(batch)
            if len(batch) < 100: break
        return [
            {
                "name": r["name"],
                "full_name": r["full_name"],
                "owner": {"login": r["owner"]["login"]},
                "private": r["private"],
                "clone_url": r["clone_url"],
                "html_url": r["html_url"],
                "updated_at": r.get("updated_at", ""),
                "default_branch": r.get("default_branch", "main"),
            }
            for r in repos
        ]

    def _create_repo(self, account, owner, name, private):
        token = self.get_token(account)
        if not token:
            raise ScaffolderError(f"No token for '{account}'")
        me = self._github_api("GET", "/user", token, account)["login"]
        path = "/user/repos" if owner == me or not owner else f"/orgs/{owner}/repos"
        owner_for_check = owner or me
        try:
            self._github_api("GET", f"/repos/{owner_for_check}/{name}", token, account)
            raise ScaffolderError(f"Repository '{owner_for_check}/{name}' already exists")
        except TokenExpiredError:
            raise
        except ScaffolderError as e:
            if "GitHub 404" not in str(e):
                raise
        return self._github_api("POST", path, token, account,
                                 {"name": name, "private": private, "auto_init": False})

    # ---------------------------------------------------------- projects

    def create_project(self, name, template_name, dest_path, placeholder_values,
                        visibility="private", account=None, create_repo=True,
                        progress=None):
        log = progress or (lambda _m: None)
        template = self.memory.get_template(template_name)
        if not template:
            raise ScaffolderError(f"Template not found: {template_name}")
        dest = Path(dest_path).expanduser().resolve()
        if dest.exists() and any(dest.iterdir()):
            raise ScaffolderError(f"Destination is not empty: {dest}")
        log("Copying template files…")
        self._copy_template(template, dest, placeholder_values)
        log("Registering project…")
        self.memory.add_project(
            name=name, path=str(dest), template=template_name,
            visibility=visibility, owner=account if create_repo else None,
        )
        log("Initializing Git repository…")
        self._git(dest, "init")
        if account:
            self._set_project_identity(dest, account)
        else:
            self._ensure_git_identity(dest)
        current = self._git(dest, "rev-parse", "--abbrev-ref", "HEAD",
                             check=False).stdout.strip()
        if current and current != "main":
            self._git(dest, "branch", "-M", "main")
        self._git(dest, "add", ".")
        self._git(dest, "commit", "-m", "Initial commit from template", check=False)
        log("Creating base version v1…")
        self.memory.add_version(name, "v1", parent=None, branch="main")
        if create_repo and account:
            try:
                log("Creating GitHub repository…")
                repo = self._create_repo(account, account, name, visibility == "private")
                self._git(dest, "remote", "add", "origin", repo["clone_url"])
                log(f"Pushing to {repo['html_url']}…")
                self._git_with_token(dest, "push", "-u", "origin", "main",
                                      token=self.get_token(account))
                self.memory.attach_repo(name, repo["owner"]["login"], repo["html_url"],
                                          visibility=visibility, state=RepoState.SYNCED)
                self.memory.update_version_sync(name, "v1", SyncState.SYNCED)
            except TokenExpiredError:
                self.memory.mark_repo_pending(name)
                raise
            except ScaffolderError as e:
                self.memory.mark_repo_pending(name)
                self.memory.log_error("create_project:repo", str(e))
                log(f"⚠ Repo creation failed: {e}")
        else:
            log("Project created locally (no repository).")
        return self.memory.get_project(name)

    def clone_repository(self, url, dest_path, project_name=None, account=None,
                          import_all_branches=False, progress=None):
        log = progress or (lambda _m: None)
        raw = url.strip()
        if re.fullmatch(r"[\w.-]+/[\w.-]+", raw):
            raw = f"https://github.com/{raw}.git"
        elif raw.startswith("git@github.com:"):
            raw = "https://github.com/" + raw.split(":", 1)[1]
        if not raw.endswith(".git"):
            raw += ".git"
        owner, repo = self._parse_repo_from_url(raw)
        project_name = project_name or repo
        dest = Path(dest_path).expanduser().resolve()
        if dest.exists() and any(dest.iterdir()):
            raise ScaffolderError(f"Destination is not empty: {dest}")
        token = self.get_token(account) if account else None
        log(f"Cloning {owner}/{repo}…")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if token:
            self._git_with_token(dest.parent, "clone", raw, str(dest), token=token)
        else:
            self._run(["git", "clone", raw, str(dest)])
        self._git(dest, "remote", "set-url", "origin",
                   f"https://github.com/{owner}/{repo}.git", check=False)
        identity_account = account
        if not identity_account:
            if any(a["username"] == owner for a in self.memory.list_accounts()):
                identity_account = owner
        if identity_account:
            self._set_project_identity(dest, identity_account)
        else:
            self._ensure_git_identity(dest)
        self.memory.add_project(
            name=project_name, path=str(dest), template=None,
            visibility="private", owner=owner, imported=True,
        )
        default_branch = self._git(dest, "rev-parse", "--abbrev-ref", "HEAD"
                                    ).stdout.strip() or "main"
        self.memory.add_version(project_name, "v1", parent=None, branch=default_branch)
        if import_all_branches:
            log("Listing remote branches…")
            rbs = self._git(dest, "branch", "-r", "--format=%(refname:short)",
                              check=False).stdout.splitlines()
            existing = {default_branch}
            n = 2
            for rb in rbs:
                rb = rb.strip()
                if not rb or "->" in rb or "/HEAD" in rb: continue
                local = rb.split("/", 1)[1]
                if local in existing: continue
                self._git(dest, "branch", local, rb, check=False)
                self.memory.add_version(project_name, f"v{n}", parent="v1", branch=local)
                existing.add(local)
                n += 1
        self.memory.attach_repo(project_name, owner,
                                  f"https://github.com/{owner}/{repo}",
                                  visibility="private", state=RepoState.SYNCED)
        try:
            self.refresh_sync_status(project_name, "v1", fetch_first=False)
        except Exception as e:
            self.memory.log_error("clone_repository:sync", str(e))
        log(f"Cloned into {dest}")
        return self.memory.get_project(project_name)

    def link_existing_folder(self, path, name):
        src = Path(path).expanduser().resolve()
        if not (src / ".git").exists():
            raise ScaffolderError(f"Not a git repository: {src}")
        if self.memory.get_project(name):
            raise AlreadyExistsError(f"Project '{name}' already exists")
        branch = self._git(src, "rev-parse", "--abbrev-ref", "HEAD"
                            ).stdout.strip() or "main"
        remote = self._git(src, "remote", "get-url", "origin",
                            check=False).stdout.strip()
        self.memory.add_project(name=name, path=str(src), template=None, imported=True)
        self.memory.add_version(name, "v1", parent=None, branch=branch)

        identity_account = None
        if remote:
            try:
                r_owner, _ = self._parse_repo_from_url(remote)
                if any(a["username"] == r_owner for a in self.memory.list_accounts()):
                    identity_account = r_owner
            except ScaffolderError:
                pass
        if identity_account:
            self._set_project_identity(src, identity_account)

        if remote:
            try:
                owner, repo = self._parse_repo_from_url(remote)
            except ScaffolderError:
                owner, repo = (None, None)
            url = f"https://github.com/{owner}/{repo}" if owner else remote
            self.memory.attach_repo(name, owner or "", url,
                                     state=RepoState.REPO_CREATED)
        return self.memory.get_project(name)

    def create_repository_for_project(self, project_name, account, visibility="private",
                                       progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if proj["repo_state"] not in (RepoState.LOCAL_ONLY.value,
                                        RepoState.REPO_PENDING.value):
            raise ScaffolderError("Project already has a repository")
        if not self.get_token(account):
            raise ScaffolderError(f"No token for '{account}'")
        log("Creating GitHub repository…")
        repo = self._create_repo(account, account, proj["name"],
                                   visibility == "private")
        dest = Path(proj["path"])
        self._set_project_identity(dest, account)
        self._git(dest, "remote", "remove", "origin", check=False)
        self._git(dest, "remote", "add", "origin", repo["clone_url"])
        log(f"Pushing to {repo['html_url']}…")
        base_branch = next(
            (v["branch"] for k, v in proj["versions"].items() if k == "v1"), "main")
        self._git_with_token(dest, "push", "-u", "origin", base_branch,
                              token=self.get_token(account))
        self.memory.attach_repo(project_name, repo["owner"]["login"], repo["html_url"],
                                  visibility=visibility, state=RepoState.SYNCED)
        if "v1" in proj["versions"]:
            self.memory.update_version_sync(project_name, "v1", SyncState.SYNCED)
        return repo

    def rename_project(self, old, new, rename_remote=True):
        proj = self.memory.get_project(old)
        if not proj:
            raise ScaffolderError(f"Project not found: {old}")
        if rename_remote and proj.get("repo_url") and proj.get("owner"):
            token = self.get_token(proj["owner"])
            if token:
                owner, repo = self._parse_repo_from_url(proj["repo_url"])
                try:
                    updated = self._github_api(
                        "PATCH", f"/repos/{owner}/{repo}", token, proj["owner"],
                        {"name": new},
                    )
                    self._git(Path(proj["path"]), "remote", "set-url", "origin",
                              updated["clone_url"], check=False)
                except TokenExpiredError:
                    raise
                except ScaffolderError as e:
                    self.memory.log_error("rename_project:remote", str(e))
        return self.memory.rename_project(old, new)

    def change_visibility(self, project_name, visibility):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if proj.get("repo_url") and proj.get("owner"):
            token = self.get_token(proj["owner"])
            if token:
                owner, repo = self._parse_repo_from_url(proj["repo_url"])
                self._github_api("PATCH", f"/repos/{owner}/{repo}", token,
                                  proj["owner"],
                                  {"private": visibility == "private"})
        self.memory.set_visibility(project_name, visibility)

    def delete_project(self, project_name, remove_remote=False):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if remove_remote and proj.get("repo_url") and proj.get("owner"):
            token = self.get_token(proj["owner"])
            if token:
                owner, repo = self._parse_repo_from_url(proj["repo_url"])
                self._github_api("DELETE", f"/repos/{owner}/{repo}", token,
                                  proj["owner"])
        dest = Path(proj["path"])
        if dest.exists():
            from datetime import datetime, timezone
            trash = Path.home() / ".scaffolder" / "trash"
            trash.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            target = trash / f"{project_name}-{stamp}"
            try:
                shutil.move(str(dest), str(target))
            except Exception as e:
                self.memory.log_error("delete_project:trash", str(e))
        self.memory.remove_project(project_name)

    # ---------------------------------------------------------- versions

    def create_version(self, project_name, label, parent_label=None, progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        parent_label = parent_label or proj.get("current_version") or "v1"
        if parent_label not in proj["versions"]:
            raise ScaffolderError(f"Parent version not found: {parent_label}")
        dest = Path(proj["path"])
        if self._git(dest, "status", "--porcelain",
                       check=False).stdout.strip():
            raise ScaffolderError(
                "Working tree has uncommitted changes. Commit or discard first.")
        parent_branch = proj["versions"][parent_label]["branch"]
        new_branch = f"version/{label}"
        log(f"Branching {parent_branch} → {new_branch}…")
        self._git(dest, "checkout", parent_branch)
        self._git(dest, "checkout", "-b", new_branch)
        ver = self.memory.add_version(project_name, label,
                                       parent=parent_label, branch=new_branch)
        self.memory.switch_version(project_name, label)
        if proj.get("repo_url") and proj.get("owner"):
            token = self.get_token(proj["owner"])
            if token:
                try:
                    log(f"Pushing branch {new_branch}…")
                    self._git_with_token(dest, "push", "-u", "origin",
                                          new_branch, token=token)
                    self.memory.update_version_sync(project_name, label,
                                                     SyncState.SYNCED)
                except TokenExpiredError:
                    raise
                except ScaffolderError as e:
                    self.memory.log_error("create_version:push", str(e))
                    log("⚠ Branch push failed — will show as AHEAD.")
        return ver

    def switch_version(self, project_name, label):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if label not in proj["versions"]:
            raise ScaffolderError(f"Version not found: {label}")
        dest = Path(proj["path"])
        if self._git(dest, "status", "--porcelain",
                       check=False).stdout.strip():
            raise ScaffolderError("Working tree has uncommitted changes.")
        self._git(dest, "checkout", proj["versions"][label]["branch"])
        self.memory.switch_version(project_name, label)

    def delete_version(self, project_name, label, remove_remote=True, progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if label not in proj["versions"]:
            raise ScaffolderError(f"Version not found: {label}")
        ver = proj["versions"][label]
        dest = Path(proj["path"])
        if proj["current_version"] == label:
            parent = ver.get("parent")
            if parent and parent in proj["versions"]:
                self._git(dest, "checkout", proj["versions"][parent]["branch"])
            else:
                others = [v for k, v in proj["versions"].items() if k != label]
                if others:
                    self._git(dest, "checkout", others[0]["branch"])
                else:
                    raise ScaffolderError("Cannot delete the only version")
        log(f"Deleting branch {ver['branch']}…")
        self._git(dest, "branch", "-D", ver["branch"], check=False)
        if remove_remote and proj.get("repo_url") and proj.get("owner"):
            token = self.get_token(proj["owner"])
            if token:
                self._git_with_token(dest, "push", "origin", "--delete",
                                      ver["branch"], token=token, check=False)
        self.memory.remove_version(project_name, label, force=True)

    # ---------------------------------------------------------- commits

    def get_uncommitted_changes(self, project_name):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        dest = Path(proj["path"])
        if not (dest / ".git").exists():
            return []
        result = self._git(dest, "status", "--porcelain", check=False)
        changes = []
        for line in result.stdout.splitlines():
            if len(line) < 4: continue
            changes.append({"status": line[:2].strip(), "path": line[3:]})
        return changes

    def commit(self, project_name, message, stage_all=True):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if not message.strip():
            raise ScaffolderError("Commit message is required")
        dest = Path(proj["path"])
        if not self._git(dest, "status", "--porcelain",
                           check=False).stdout.strip():
            raise ScaffolderError("Nothing to commit — working tree is clean")
        if stage_all:
            self._git(dest, "add", "-A")
        result = self._git(dest, "commit", "-m", message, check=False)
        if result.returncode != 0:
            raise ScaffolderError(f"Commit failed: {result.stderr or result.stdout}")
        current = proj.get("current_version")
        if current and proj.get("repo_url"):
            try:
                self.refresh_sync_status(project_name, current, fetch_first=False)
            except Exception:
                self.memory.update_version_sync(project_name, current,
                                                 SyncState.AHEAD, ahead=1)

    def discard_changes(self, project_name):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        dest = Path(proj["path"])
        self._git(dest, "reset", "--hard", "HEAD")
        self._git(dest, "clean", "-fd")
    def get_changes_detailed(self, project_name):
        """Return uncommitted changes with classification."""
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        dest = Path(proj["path"])
        if not (dest / ".git").exists():
            return []
        result = self._git(dest, "status", "--porcelain", check=False)
        changes = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            st = line[:2]
            path = line[3:]
            changes.append({
                "status":     st.strip() or "?",
                "raw_status": st,
                "path":       path,
                "kind":       "untracked" if st == "??" else "tracked",
            })
        return changes

    def restore_file(self, project_name, rel_path):
        """Restore one file from the last commit. Untracked files are deleted."""
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        dest = Path(proj["path"])
        target = dest / rel_path

        # Unstage if staged
        self._git(dest, "reset", "HEAD", "--", rel_path, check=False)
        # Try restore from HEAD (works for modified / deleted tracked files)
        result = self._git(dest, "checkout", "HEAD", "--", rel_path, check=False)
        if result.returncode != 0:
            # Not in HEAD → delete file (was newly added / untracked)
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()

    def open_file(self, project_name, rel_path):
        """Open a single file in VS Code (falls back to OS default)."""
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        dest = Path(proj["path"])
        target = dest / rel_path
        if not target.exists():
            raise ScaffolderError(f"File not found: {target}")
        try:
            self.open_in_vscode(dest, goto_file=f"{target}:1")
        except ScaffolderError:
            import sys
            try:
                if sys.platform.startswith("darwin"):
                    subprocess.Popen(["open", str(target)])
                elif sys.platform.startswith("win"):
                    os.startfile(str(target))
                else:
                    subprocess.Popen(["xdg-open", str(target)])
            except Exception as e:
                raise ScaffolderError(f"Cannot open file: {e}")
    # ---------------------------------------------------------- push / pull

    def push(self, project_name, version_label=None, progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if proj["repo_state"] == RepoState.LOCAL_ONLY.value:
            raise ScaffolderError("No repository yet. Use 'Create Repository…' first.")
        if proj["repo_state"] == RepoState.REPO_PENDING.value:
            raise ScaffolderError(
                "Repository creation failed. Use 'Create Repository…' to retry.")
        version_label = version_label or proj.get("current_version")
        if not version_label:
            raise ScaffolderError("No current version")
        ver = proj["versions"].get(version_label)
        if not ver:
            raise ScaffolderError(f"Version not found: {version_label}")
        token = self.get_token(proj["owner"]) if proj.get("owner") else None
        if not token:
            raise ScaffolderError(f"No GitHub token for '{proj.get('owner')}'.")
        dest = Path(proj["path"])
        log(f"Pushing {ver['branch']}…")
        self._git_with_token(dest, "push", "-u", "origin", ver["branch"], token=token)
        self.memory.update_version_sync(project_name, version_label, SyncState.SYNCED)

    def push_all(self, project_name, progress=None):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        results = {}
        for label in proj["versions"]:
            try:
                self.push(project_name, label, progress=progress)
                results[label] = "ok"
            except TokenExpiredError:
                raise
            except ScaffolderError as e:
                results[label] = str(e)
                self.memory.log_error(f"push_all:{label}", str(e))
        return results

    def pull(self, project_name, version_label=None, strategy="ff-only",
              progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        version_label = version_label or proj.get("current_version")
        if not version_label:
            raise ScaffolderError("No current version")
        ver = proj["versions"].get(version_label)
        if not ver:
            raise ScaffolderError(f"Version not found: {version_label}")
        token = self.get_token(proj["owner"]) if proj.get("owner") else None
        if not token:
            raise ScaffolderError(f"No GitHub token for '{proj.get('owner')}'.")
        dest = Path(proj["path"])
        branch = ver["branch"]
        log(f"Fetching {branch}…")
        self._git_with_token(dest, "fetch", "origin", token=token, check=False)
        counts = self._git(dest, "rev-list", "--left-right", "--count",
                             f"origin/{branch}...{branch}", check=False)
        ahead = behind = 0
        if counts.returncode == 0:
            try:
                behind, ahead = (int(x) for x in counts.stdout.strip().split())
            except ValueError:
                pass
        if ahead and behind:
            if strategy == "ff-only":
                raise DivergedError(ahead, behind)
            if strategy == "merge":
                log(f"Merging origin/{branch}…")
                result = self._git(dest, "merge", f"origin/{branch}", check=False)
                if result.returncode != 0:
                    conflicts = self._git(dest, "diff", "--name-only",
                                            "--diff-filter=U", check=False
                                            ).stdout.strip()
                    raise ScaffolderError(f"Merge conflict:\n{conflicts}")
            elif strategy == "rebase":
                log(f"Rebasing onto origin/{branch}…")
                result = self._git(dest, "rebase", f"origin/{branch}", check=False)
                if result.returncode != 0:
                    self._git(dest, "rebase", "--abort", check=False)
                    raise ScaffolderError(
                        f"Rebase failed: {result.stdout or result.stderr}")
            else:
                raise ScaffolderError(f"Unknown strategy: {strategy}")
        elif behind:
            log(f"Fast-forwarding {behind} commit(s)…")
            self._git_with_token(dest, "pull", "--ff-only", "origin", branch,
                                  token=token)
        elif ahead:
            log(f"Local is {ahead} ahead — nothing to pull.")
        else:
            log("Already up to date.")
        self.refresh_sync_status(project_name, version_label, fetch_first=False)

    # ---------------------------------------------------------- merge / PR

    def merge_version(self, project_name, source, target, strategy="merge",
                       progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        for l in (source, target):
            if l not in proj["versions"]:
                raise ScaffolderError(f"Version not found: {l}")
        if source == target:
            raise ScaffolderError("Source and target must differ")
        dest = Path(proj["path"])
        src_branch = proj["versions"][source]["branch"]
        tgt_branch = proj["versions"][target]["branch"]
        current = self._git(dest, "rev-parse", "--abbrev-ref", "HEAD"
                             ).stdout.strip()
        if current != tgt_branch:
            self._git(dest, "checkout", tgt_branch)
        args = ["merge"]
        if strategy == "squash": args.append("--squash")
        elif strategy == "no-ff": args.append("--no-ff")
        args.append(src_branch)
        log(f"Merging {src_branch} → {tgt_branch}…")
        result = self._git(dest, *args, check=False)
        if result.returncode != 0:
            conflicts = self._git(dest, "diff", "--name-only",
                                    "--diff-filter=U", check=False).stdout.strip()
            raise ScaffolderError(f"Merge conflict in:\n{conflicts}")
        if strategy == "squash":
            self._git(dest, "commit", "-m",
                      f"Squash merge {source} → {target}")
        if proj.get("repo_url") and proj.get("owner"):
            self.push(project_name, target, progress=log)

    def create_pull_request(self, project_name, source, target, title, body=""):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        if not proj.get("repo_url") or not proj.get("owner"):
            raise ScaffolderError("Project has no repository")
        for l in (source, target):
            if l not in proj["versions"]:
                raise ScaffolderError(f"Version not found: {l}")
        if source == target:
            raise ScaffolderError("Source and target must differ")
        self.push(project_name, source)
        token = self.get_token(proj["owner"])
        if not token:
            raise ScaffolderError(f"No token for '{proj['owner']}'")
        owner, repo = self._parse_repo_from_url(proj["repo_url"])
        return self._github_api(
            "POST", f"/repos/{owner}/{repo}/pulls", token, proj["owner"],
            {"title": title or f"Merge {source} into {target}",
             "body": body or f"Merge version {source} → {target}",
             "head": proj["versions"][source]["branch"],
             "base": proj["versions"][target]["branch"]},
        )

    def rebase_version(self, project_name, version_label, onto, progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        for l in (version_label, onto):
            if l not in proj["versions"]:
                raise ScaffolderError(f"Version not found: {l}")
        dest = Path(proj["path"])
        branch = proj["versions"][version_label]["branch"]
        onto_branch = proj["versions"][onto]["branch"]
        self._git(dest, "checkout", branch)
        log(f"Rebasing {branch} onto {onto_branch}…")
        result = self._git(dest, "rebase", onto_branch, check=False)
        if result.returncode != 0:
            self._git(dest, "rebase", "--abort", check=False)
            raise ScaffolderError(f"Rebase failed: {result.stdout or result.stderr}")

    def force_push(self, project_name, version_label=None):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        version_label = version_label or proj.get("current_version")
        if not version_label:
            raise ScaffolderError("No current version")
        ver = proj["versions"].get(version_label)
        if not ver:
            raise ScaffolderError(f"Version not found: {version_label}")
        token = self.get_token(proj["owner"]) if proj.get("owner") else None
        if not token:
            raise ScaffolderError(f"No token for '{proj.get('owner')}'")
        dest = Path(proj["path"])
        self._git_with_token(dest, "push", "--force-with-lease", "origin",
                              ver["branch"], token=token)
        self.refresh_sync_status(project_name, version_label, fetch_first=False)

    # ---------------------------------------------------------- sync status

    def refresh_sync_status(self, project_name, version_label=None, fetch_first=False):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        version_label = version_label or proj.get("current_version")
        if not version_label:
            return {"state": "UNKNOWN", "ahead": 0, "behind": 0}
        ver = proj["versions"].get(version_label)
        if not ver:
            return {"state": "UNKNOWN", "ahead": 0, "behind": 0}
        dest = Path(proj["path"])
        branch = ver["branch"]
        token = self.get_token(proj["owner"]) if proj.get("owner") else None
        if fetch_first and token and proj.get("repo_url"):
            self._git_with_token(dest, "fetch", "origin", token=token, check=False)
        result = self._git(dest, "rev-list", "--left-right", "--count",
                             f"origin/{branch}...{branch}", check=False)
        if result.returncode != 0:
            return {"state": "UNKNOWN", "ahead": 0, "behind": 0}
        try:
            behind, ahead = (int(x) for x in result.stdout.strip().split())
        except ValueError:
            return {"state": "UNKNOWN", "ahead": 0, "behind": 0}
        if ahead and behind: state = SyncState.DIVERGED
        elif ahead: state = SyncState.AHEAD
        elif behind: state = SyncState.BEHIND
        else: state = SyncState.SYNCED
        self.memory.update_version_sync(project_name, version_label, state,
                                          ahead=ahead, behind=behind)
        return {"state": state.value, "ahead": ahead, "behind": behind}

    def refresh_all_versions(self, project_name, fetch_first=False):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        results = {}
        for label in proj["versions"]:
            try:
                results[label] = self.refresh_sync_status(project_name, label,
                                                            fetch_first)
            except TokenExpiredError:
                raise
            except ScaffolderError as e:
                results[label] = {"state": "UNKNOWN", "ahead": 0, "behind": 0,
                                    "error": str(e)}
        return results

    # ---------------------------------------------------------- open helpers

    def open_in_file_manager(self, path):
        p = str(Path(path).expanduser())
        import sys
        try:
            if sys.platform.startswith("darwin"):
                subprocess.Popen(["open", p])
            elif sys.platform.startswith("win"):
                os.startfile(p)
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            raise ScaffolderError(f"Cannot open folder: {e}")

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def open_in_vscode(self, path, goto_file=None):
        """Open VS Code in a NEW window. Falls back to common install paths
        when the `code` shell command isn't on PATH."""
        import sys
        p = str(Path(path).expanduser())
        code = shutil.which("code")

        if not code:
            if sys.platform.startswith("win"):
                candidates = [
                    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" /
                        "Microsoft VS Code" / "Code.exe",
                    Path("C:/Program Files/Microsoft VS Code/Code.exe"),
                    Path("C:/Program Files (x86)/Microsoft VS Code/Code.exe"),
                ]
            elif sys.platform == "darwin":
                candidates = [
                    Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
                    Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                ]
            else:
                candidates = [
                    Path("/usr/share/code/code"),
                    Path("/usr/bin/code"),
                    Path("/snap/bin/code"),
                    Path("/var/lib/flatpak/exports/bin/com.visualstudio.code"),
                ]
            for c in candidates:
                if c.exists():
                    code = str(c)
                    break

        if not code:
            raise ScaffolderError(
                "VS Code not found. Install it, or run 'Shell Command: "
                "Install code command in PATH' from its command palette."
            )

        args = [code, "--new-window"]
        if goto_file:
            args += ["--goto", goto_file]
        args.append(p)
        try:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            raise ScaffolderError(f"Cannot launch VS Code: {e}")
    # ---------------------------------------------------------- env detection

    def detect_env_type(self, project_name):
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        dest = Path(proj["path"])
        if (dest / "pyproject.toml").exists() \
           or (dest / "requirements.txt").exists() \
           or (dest / "setup.py").exists():
            return "python"
        if (dest / "package.json").exists():
            return "node"
        if (dest / "Cargo.toml").exists():
            return "rust"
        if (dest / "go.mod").exists():
            return "go"
        if any(dest.rglob("*.py")):
            return "python"
        return "none"

    def has_env(self, project_name):
        proj = self.memory.get_project(project_name)
        if not proj: return True
        dest = Path(proj["path"])
        kind = self.detect_env_type(project_name)
        if kind == "none": return True
        if kind == "python":
            return (dest / ".venv").exists() or (dest / "venv").exists()
        if kind == "node": return (dest / "node_modules").exists()
        if kind == "rust": return (dest / "target").exists()
        if kind == "go":
            return (dest / "go.sum").exists() or (dest / "vendor").exists()
        return True

    def setup_env(self, project_name, progress=None):
        log = progress or (lambda _m: None)
        proj = self.memory.get_project(project_name)
        if not proj:
            raise ScaffolderError(f"Project not found: {project_name}")
        dest = Path(proj["path"])
        kind = self.detect_env_type(project_name)
        if kind == "none":
            raise ScaffolderError(
                "Cannot detect project type "
                "(no pyproject/package.json/Cargo.toml/go.mod found).")
        if self.has_env(project_name):
            log("Environment already exists.")
            return kind

        if kind == "python":
            log("Creating Python virtual environment (.venv)…")
            py = shutil.which("python3") or shutil.which("python")
            if not py:
                raise ScaffolderError("Python interpreter not found on PATH.")
            self._run([py, "-m", "venv", ".venv"], cwd=dest)
            req = dest / "requirements.txt"
            if req.exists():
                log("Installing requirements.txt…")
                pip = (dest / ".venv"
                       / ("Scripts" if os.name == "nt" else "bin") / "pip")
                if pip.exists():
                    self._run([str(pip), "install", "-r", "requirements.txt"],
                              cwd=dest, check=False)
        elif kind == "node":
            log("Installing npm dependencies (this may take a while)…")
            npm = shutil.which("npm")
            if not npm:
                raise ScaffolderError("npm not found on PATH. Install Node.js.")
            self._run([npm, "install"], cwd=dest, check=False)
        elif kind == "rust":
            log("Building with cargo…")
            cargo = shutil.which("cargo")
            if not cargo:
                raise ScaffolderError("cargo not found on PATH. Install Rust.")
            self._run([cargo, "build"], cwd=dest, check=False)
        elif kind == "go":
            log("Downloading Go modules…")
            go = shutil.which("go")
            if not go:
                raise ScaffolderError("go not found on PATH. Install Go.")
            self._run([go, "mod", "download"], cwd=dest, check=False)
        log("Environment ready.")
        return kind
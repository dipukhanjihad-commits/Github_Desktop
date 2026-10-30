"""Git plumbing used by the scaffolder.

All commands go through :class:`GitRunner`, which normalises failures into the
structured error taxonomy and never leaks raw ``CalledProcessError`` objects.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence

from .errors import (
    ConflictError, MissingFolderError, NotARepositoryError, ScaffolderError,
    TokenExpiredError, ToolMissingError,
)

DEFAULT_TIMEOUT = 300


def _decode_output(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class CommandResult:
    __slots__ = ("args", "returncode", "stdout", "stderr")

    def __init__(self, args: Sequence[str], returncode: int, stdout: str,
                 stderr: str) -> None:
        self.args = list(args)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def describe(self) -> str:
        detail = (self.stderr or self.stdout or "").strip()
        return detail


def require_tool(name: str, install_hint: str = "") -> str:
    found = shutil.which(name)
    if not found:
        raise ToolMissingError(name, install_hint)
    return found


def run(args: Sequence[str], cwd: Path | str | None = None,
        env: dict[str, str] | None = None, check: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
        input_text: str | None = None) -> CommandResult:
    """Run a command; raise :class:`ToolMissingError` / ``ScaffolderError``."""
    try:
        proc = subprocess.run(
            [str(a) for a in args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            timeout=timeout,
            input=input_text.encode() if input_text is not None else None,
            stdin=None if input_text is not None else subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW
                           if sys.platform.startswith("win") else 0),
        )
    except FileNotFoundError:
        raise ToolMissingError(str(args[0])) from None
    except subprocess.TimeoutExpired:
        raise ScaffolderError(
            f"Timed out after {timeout}s: {' '.join(map(str, args))}") from None
    except OSError as exc:
        raise ScaffolderError(f"Could not run {args[0]}: {exc}") from exc
    result = CommandResult(args, proc.returncode,
                           _decode_output(proc.stdout), _decode_output(proc.stderr))
    if check and not result.ok:
        raise ScaffolderError(
            f"`{' '.join(map(str, args))}` failed.\n{result.describe()}")
    return result


class SecretAskpass:
    """Context manager giving git a one-shot askpass helper holding a token.

    The helper script is created with owner-only permissions inside a private
    temp directory and removed afterwards. ``credential.helper=`` is passed
    explicitly on each git invocation so an inherited helper (GitHub Desktop,
    manager-core, osxkeychain) cannot intercept the request.
    """

    def __init__(self, token: str | None) -> None:
        self.token = token
        self._dir: Path | None = None
        self.script: Path | None = None

    def __enter__(self) -> "SecretAskpass":
        if self.token is None:
            return self
        self._dir = Path(tempfile.mkdtemp(prefix="scaffolder-auth-"))
        try:
            os.chmod(self._dir, 0o700)
        except OSError:
            pass
        if sys.platform.startswith("win"):
            # A .cmd shim is what GIT_ASKPASS needs on Windows.
            self.script = self._dir / "askpass.cmd"
            self.script.write_text("@echo off\r\necho %SCAFFOLDER_GIT_TOKEN%\r\n",
                                   encoding="ascii")
        else:
            self.script = self._dir / "askpass.sh"
            self.script.write_text('#!/bin/sh\nprintf "%s\\n" "$SCAFFOLDER_GIT_TOKEN"\n',
                                   encoding="ascii")
            os.chmod(self.script, 0o700)
        return self

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.script is None:
            return env
        env["GIT_ASKPASS"] = str(self.script)
        env["SSH_ASKPASS"] = str(self.script)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["SCAFFOLDER_GIT_TOKEN"] = self.token or ""
        env["GCM_INTERACTIVE"] = "never"
        env["GIT_CONFIG_PARAMETERS"] = "'credential.helper='"
        return env

    def __exit__(self, *exc: object) -> None:
        if self._dir and self._dir.exists():
            shutil.rmtree(self._dir, ignore_errors=True)


class GitRunner:
    """Thin, per-repository git facade."""

    def __init__(self, repo: Path | str, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.repo = Path(repo)
        self.timeout = timeout

    # -------------------------------------------------------------- low level

    def ensure_repo(self) -> None:
        if not self.repo.exists():
            raise MissingFolderError(str(self.repo))
        if not self.is_repo():
            raise NotARepositoryError(f"{self.repo} is not a git repository.")

    def is_repo(self) -> bool:
        if not self.repo.exists():
            return False
        result = self.run("rev-parse", "--is-inside-work-tree", check=False)
        return result.ok and result.stdout.strip() == "true"

    def run(self, *args: str, check: bool = True, token: str | None = None,
            timeout: int | None = None,
            input_text: str | None = None) -> CommandResult:
        with SecretAskpass(token) as auth:
            cmd = ["git"]
            if token is not None:
                cmd += ["-c", "credential.helper="]
            cmd += [str(a) for a in args]
            result = run(cmd, cwd=self.repo, env=auth.env(), check=False,
                         timeout=timeout or self.timeout, input_text=input_text)
        if check and not result.ok:
            raise self._classify(result, args)
        return result

    @staticmethod
    def _classify(result: CommandResult, args: Sequence[str]) -> ScaffolderError:
        detail = result.describe()
        lowered = detail.lower()
        if ("authentication failed" in lowered
                or "could not read username" in lowered
                or "invalid username or password" in lowered
                or "permission denied (publickey)" in lowered
                or "403" in lowered and "push" in lowered):
            return TokenExpiredError("github")
        if "conflict" in lowered and "automatic merge failed" in lowered:
            return ConflictError([])
        return ScaffolderError(
            f"`git {' '.join(args)}` failed.\n{detail}" if detail
            else f"`git {' '.join(args)}` failed.")

    # ------------------------------------------------------------- inspection

    def current_branch(self) -> str:
        result = self.run("rev-parse", "--abbrev-ref", "HEAD", check=False)
        branch = result.stdout.strip()
        if not branch or branch == "HEAD":
            # Detached HEAD or unborn branch; try symbolic-ref.
            sym = self.run("symbolic-ref", "--short", "HEAD", check=False)
            return sym.stdout.strip() or ""
        return branch

    def branches(self) -> list[str]:
        out = self.run("branch", "--format=%(refname:short)", check=False).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    def remote_branches(self) -> list[str]:
        out = self.run("branch", "-r", "--format=%(refname:short)",
                       check=False).stdout
        return [line.strip() for line in out.splitlines()
                if line.strip() and "HEAD" not in line]

    def branch_exists(self, name: str) -> bool:
        return self.run("rev-parse", "--verify", "--quiet", f"refs/heads/{name}",
                        check=False).ok

    def remote_exists(self, remote: str = "origin") -> bool:
        return self.run("remote", "get-url", remote, check=False).ok

    def remote_url(self, remote: str = "origin") -> str:
        return self.run("remote", "get-url", remote, check=False).stdout.strip()

    def has_commits(self) -> bool:
        return self.run("rev-parse", "--verify", "--quiet", "HEAD",
                        check=False).ok

    def head_sha(self) -> str:
        return self.run("rev-parse", "HEAD", check=False).stdout.strip()

    def is_clean(self) -> bool:
        return not self.run("status", "--porcelain", check=False).stdout.strip()

    def status_entries(self) -> list[dict]:
        """Parse ``git status --porcelain=v1 -z`` into structured entries."""
        result = self.run("status", "--porcelain=v1", "-z",
                          "--untracked-files=all", check=False)
        raw = result.stdout
        entries: list[dict] = []
        if not raw:
            return entries
        parts = raw.split("\x00")
        index = 0
        while index < len(parts):
            part = parts[index]
            index += 1
            if not part or len(part) < 4:
                continue
            code = part[:2]
            path = part[3:]
            orig = None
            if code[0] in ("R", "C") and index < len(parts):
                orig = parts[index]
                index += 1
            entries.append(_classify_status(code, path, orig))
        return entries

    def tracked_file_count(self) -> int:
        out = self.run("ls-files", check=False).stdout
        return len([line for line in out.splitlines() if line.strip()])

    def commit_count(self) -> int:
        result = self.run("rev-list", "--count", "HEAD", check=False)
        try:
            return int(result.stdout.strip())
        except ValueError:
            return 0

    # ------------------------------------------------------------------ diffs

    def diff_stat(self, staged: bool = True, path: str | None = None) -> str:
        args = ["diff", "--cached", "--stat"] if staged else ["diff", "--stat"]
        if path:
            args += ["--", path]
        return self.run(*args, check=False).stdout

    def diff_text(self, path: str, staged: bool = True, context: int = 3) -> str:
        args = (["diff", "--cached"] if staged else ["diff"])
        args += [f"-U{context}", "--", path]
        text = self.run(*args, check=False).stdout
        if not text:
            # Untracked file: synthesise a diff of its whole content.
            text = self.diff_untracked(path)
        return text

    def diff_untracked(self, path: str) -> str:
        target = self.repo / path
        if not target.exists() or target.is_dir():
            return ""
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = content.splitlines()
        body = "\n".join(f"+{line}" for line in lines)
        return (f"diff --git a/{path} b/{path}\nnew file\n--- /dev/null\n"
                f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}\n")

    # --------------------------------------------------------------- staging

    def stage(self, paths: Iterable[str]) -> None:
        path_list = [p for p in paths if p]
        if not path_list:
            return
        self.run("add", "--", *path_list)

    def stage_all(self) -> None:
        self.run("add", "-A")

    def unstage(self, paths: Iterable[str]) -> None:
        path_list = [p for p in paths if p]
        if not path_list:
            return
        if self.has_commits():
            self.run("restore", "--staged", "--", *path_list, check=False)
        else:
            self.run("rm", "--cached", "-r", "--quiet", "--", *path_list,
                     check=False)

    def restore(self, path: str) -> None:
        """Discard changes in one path (tracked -> HEAD, untracked -> delete)."""
        self.run("reset", "-q", "HEAD", "--", path, check=False)
        restored = self.run("checkout", "HEAD", "--", path, check=False)
        if not restored.ok:
            target = self.repo / path
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()

    def discard_all(self) -> None:
        self.run("reset", "-q", "--hard", "HEAD", check=False)
        self.run("clean", "-fd", check=False)

    # --------------------------------------------------------------- commits

    def commit(self, message: str, amend: bool = False) -> str:
        args = ["commit", "-F", "-"]
        if amend:
            args.insert(1, "--amend")
        result = self.run(*args, input_text=message)
        sha = self.run("rev-parse", "--short", "HEAD", check=False).stdout.strip()
        return sha or result.stdout.strip()

    def log(self, limit: int = 50, path: str | None = None) -> list[dict]:
        fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s"
        args = ["log", f"-{limit}", f"--format={fmt}"]
        if path:
            args += ["--", path]
        result = self.run(*args, check=False)
        commits = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 6:
                continue
            commits.append({
                "sha": parts[0], "short": parts[1], "author": parts[2],
                "email": parts[3], "date": parts[4], "subject": parts[5],
            })
        return commits

    def show_commit(self, sha: str) -> str:
        return self.run("show", "--stat", "--patch", "--format=fuller", sha,
                        check=False).stdout

    def revert_last(self, keep_changes: bool = False) -> None:
        if keep_changes:
            self.run("reset", "--soft", "HEAD~1")
        else:
            self.run("reset", "--mixed", "HEAD~1")

    def stash_push(self, message: str = "") -> str:
        args = ["stash", "push", "--include-untracked"]
        if message:
            args += ["-m", message]
        result = self.run(*args)
        return result.stdout.strip()

    def stash_pop(self) -> bool:
        return self.run("stash", "pop", check=False).ok

    def stash_list(self) -> list[str]:
        out = self.run("stash", "list", check=False).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    # --------------------------------------------------------------- branches

    def checkout(self, branch: str, create: bool = False) -> None:
        args = ["checkout"]
        if create:
            args.append("-b")
        args.append(branch)
        self.run(*args)

    def delete_branch(self, branch: str, force: bool = False) -> None:
        self.run("branch", "-D" if force else "-d", branch, check=False)

    def rename_branch(self, old: str, new: str) -> None:
        self.run("branch", "-m", old, new)

    def merge(self, branch: str, strategy: str = "merge") -> None:
        args = ["merge"]
        if strategy == "squash":
            args.append("--squash")
        elif strategy == "no-ff":
            args.append("--no-ff")
        elif strategy == "ff":
            args.append("--ff-only")
        args.append(branch)
        result = self.run(*args, check=False)
        if not result.ok:
            conflicts = self.conflicts()
            if conflicts:
                raise ConflictError(conflicts)
            raise ScaffolderError(f"`git merge {branch}` failed.\n"
                                  f"{result.describe()}")
        if strategy == "squash":
            self.run("commit", "-m", f"Squash merge of {branch}", check=False)

    def rebase(self, onto: str) -> None:
        result = self.run("rebase", onto, check=False)
        if not result.ok:
            self.run("rebase", "--abort", check=False)
            conflicts = self.conflicts()
            if conflicts:
                raise ConflictError(conflicts)
            raise ScaffolderError(f"Rebase onto {onto} failed.\n"
                                  f"{result.describe()}")

    def conflicts(self) -> list[str]:
        out = self.run("diff", "--name-only", "--diff-filter=U",
                       check=False).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    def abort_merge(self) -> None:
        self.run("merge", "--abort", check=False)

    def in_merge(self) -> bool:
        path = self.run("rev-parse", "--git-path", "MERGE_HEAD",
                        check=False).stdout.strip()
        if not path:
            return False
        target = Path(path)
        if not target.is_absolute():
            target = self.repo / target
        return target.exists()

    # ---------------------------------------------------------------- remotes

    def set_remote(self, url: str, remote: str = "origin") -> None:
        if self.remote_exists(remote):
            self.run("remote", "set-url", remote, url)
        else:
            self.run("remote", "add", remote, url)

    def remove_remote(self, remote: str = "origin") -> None:
        self.run("remote", "remove", remote, check=False)

    def fetch(self, token: str | None = None, remote: str = "origin") -> None:
        if not self.remote_exists(remote):
            return
        self.run("fetch", "--prune", remote, token=token, check=False)

    def push(self, branch: str, token: str | None = None,
             set_upstream: bool = True, force_with_lease: bool = False) -> None:
        args = ["push"]
        if force_with_lease:
            args.append("--force-with-lease")
        if set_upstream:
            args += ["-u", "origin", branch]
        else:
            args += ["origin", branch]
        self.run(*args, token=token)

    def delete_remote_branch(self, branch: str, token: str | None = None) -> None:
        self.run("push", "origin", "--delete", branch, token=token, check=False)

    def pull_ff(self, branch: str, token: str | None = None) -> None:
        self.run("pull", "--ff-only", "origin", branch, token=token)

    def ahead_behind(self, branch: str, remote: str = "origin") -> tuple[int, int] | None:
        """Return ``(ahead, behind)`` relative to ``remote/branch`` or None."""
        ref = f"{remote}/{branch}"
        if not self.run("rev-parse", "--verify", "--quiet",
                        f"refs/remotes/{ref}", check=False).ok:
            return None
        result = self.run("rev-list", "--left-right", "--count",
                          f"{ref}...{branch}", check=False)
        if not result.ok:
            return None
        try:
            behind, ahead = (int(x) for x in result.stdout.split())
        except ValueError:
            return None
        return ahead, behind

    def config_get(self, key: str) -> str:
        return self.run("config", key, check=False).stdout.strip()

    def config_set(self, key: str, value: str) -> None:
        self.run("config", key, value, check=False)

    def set_identity(self, name: str, email: str) -> None:
        self.config_set("user.name", name)
        self.config_set("user.email", email)
        self.config_set("credential.helper", "")


def _classify_status(code: str, path: str, orig: str | None) -> dict:
    x, y = code[0], code[1]
    if code == "??":
        kind, label = "untracked", "New file"
    elif code == "!!":
        kind, label = "ignored", "Ignored"
    elif "U" in code or code in ("AA", "DD"):
        kind, label = "conflicted", "Conflict"
    elif x == "R":
        kind, label = "renamed", "Renamed"
    elif x == "D" or y == "D":
        kind, label = "deleted", "Deleted"
    elif x == "A":
        kind, label = "added", "Added"
    elif y == "M" or x == "M":
        kind, label = "modified", "Modified"
    else:
        kind, label = "changed", "Changed"
    return {
        "status": code.strip() or "?",
        "raw_status": code,
        "path": path,
        "orig_path": orig,
        "kind": kind,
        "label": label,
        "staged": x not in (" ", "?"),
        "unstaged": y not in (" ", "?"),
        "untracked": code == "??",
    }


_token_re = re.compile(r"(https?://)[^/@\s]+@", re.IGNORECASE)


def redact(text: str) -> str:
    """Strip embedded credentials from any text before it is shown or logged."""
    return _token_re.sub(r"\1", text)


def is_windows() -> bool:
    return sys.platform.startswith("win")

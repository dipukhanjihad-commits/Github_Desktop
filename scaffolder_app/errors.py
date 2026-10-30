"""Structured error taxonomy (M6).

Every failure the backend can produce is one of these types. The UI maps them
to specific remediations instead of dumping a bare exception string.
"""

from __future__ import annotations


class ScaffolderError(Exception):
    """Base class for every expected backend failure."""

    #: Short, user-facing summary. Subclasses override.
    title = "Operation failed"
    #: Concrete next step the UI can offer, or empty when none exists.
    hint = ""

    def __init__(self, message: str = "", *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message or self.title
        if hint is not None:
            self.hint = hint

    def __str__(self) -> str:
        return self.message


class NotFoundError(ScaffolderError):
    title = "Not found"


class AlreadyExistsError(ScaffolderError):
    title = "Already exists"


class ValidationError(ScaffolderError):
    title = "Invalid input"
    hint = "Check the highlighted field and try again."


class MemoryError(ScaffolderError):  # noqa: A001 - deliberate, domain-specific
    title = "Storage error"


class TokenExpiredError(ScaffolderError):
    title = "GitHub token invalid or expired"
    hint = "Update the token via GitHub \u2192 Manage Accounts \u2192 Replace Token."

    def __init__(self, account: str) -> None:
        self.account = account
        super().__init__(f"The GitHub token for '{account}' is invalid or expired.")


class MissingTokenError(ScaffolderError):
    title = "No GitHub token"
    hint = "Add the account under GitHub \u2192 Add Account."

    def __init__(self, account: str) -> None:
        self.account = account
        super().__init__(f"No stored token for '{account}'.")


class DivergedError(ScaffolderError):
    title = "Branches diverged"
    hint = "Choose to merge or rebase to reconcile them."

    def __init__(self, ahead: int, behind: int, branch: str = "") -> None:
        self.ahead = ahead
        self.behind = behind
        self.branch = branch
        where = f" for '{branch}'" if branch else ""
        super().__init__(
            f"Local{where} is {ahead} commit(s) ahead and {behind} behind the remote."
        )


class DirtyWorkTreeError(ScaffolderError):
    title = "Uncommitted changes"
    hint = "Commit, stash or discard your changes first."

    def __init__(self, changes: list[dict] | None = None) -> None:
        self.changes = changes or []
        n = len(self.changes)
        super().__init__(
            f"{n} uncommitted change(s) would be overwritten."
            if n else "The working tree has uncommitted changes."
        )


class ConflictError(ScaffolderError):
    title = "Merge conflict"
    hint = "Resolve the conflicting files, then commit the merge."

    def __init__(self, files: list[str]) -> None:
        self.files = list(files)
        listed = "\n".join(f"  \u2022 {f}" for f in self.files) or "  (unknown files)"
        super().__init__(f"Conflicts in {len(self.files)} file(s):\n{listed}")


class NotARepositoryError(ScaffolderError):
    title = "Not a git repository"
    hint = "Pick a folder that contains a .git directory."


class MissingFolderError(ScaffolderError):
    title = "Project folder is missing"
    hint = "Relink the project to a new location, or remove it from the list."

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"The project folder no longer exists:\n{path}")


class ToolMissingError(ScaffolderError):
    title = "Required tool not installed"

    def __init__(self, tool: str, install_hint: str = "") -> None:
        self.tool = tool
        self.hint = install_hint or f"Install {tool} and make sure it is on PATH."
        super().__init__(f"'{tool}' was not found on PATH.")


class RemoteUnsupportedError(ScaffolderError):
    title = "Unsupported remote"
    hint = "Add the existing remote manually, or re-add it via GitHub."

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"Cannot parse the remote URL: {url}")


class NetworkError(ScaffolderError):
    title = "Network error"
    hint = "Check your connection, then retry."


class GitHubError(ScaffolderError):
    title = "GitHub rejected the request"

    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"GitHub returned HTTP {status}. {detail}".strip())

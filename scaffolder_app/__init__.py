"""Project Scaffolder — application core.

The UI layer lives in :mod:`scaffolder_app.ui`; nothing here imports Qt, so the
backend can be exercised head-lessly in tests.
"""

from __future__ import annotations

__version__ = "2.0.0"
__app_name__ = "Project Scaffolder"

from .errors import (  # noqa: E402,F401
    AlreadyExistsError,
    ConflictError,
    DirtyWorkTreeError,
    DivergedError,
    GitHubError,
    MissingFolderError,
    MissingTokenError,
    NetworkError,
    NotFoundError,
    NotARepositoryError,
    RemoteUnsupportedError,
    ScaffolderError,
    TokenExpiredError,
    ToolMissingError,
    ValidationError,
)
from .project_memory import (  # noqa: E402,F401
    ProjectMemory,
    RepoState,
    SyncState,
    Visibility,
)
from .scaffolder import Scaffolder  # noqa: E402,F401

__all__ = [
    "__version__",
    "AlreadyExistsError",
    "ConflictError",
    "DirtyWorkTreeError",
    "DivergedError",
    "GitHubError",
    "MissingFolderError",
    "MissingTokenError",
    "NetworkError",
    "NotFoundError",
    "NotARepositoryError",
    "ProjectMemory",
    "RemoteUnsupportedError",
    "RepoState",
    "Scaffolder",
    "ScaffolderError",
    "SyncState",
    "TokenExpiredError",
    "ToolMissingError",
    "ValidationError",
    "Visibility",
]

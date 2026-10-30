"""Application-wide services bundle passed to the UI (dependency injection)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from ..credentials import CredentialStore
from ..project_memory import ProjectMemory
from ..scaffolder import Scaffolder


@dataclass
class Services:
    memory: ProjectMemory
    credentials: CredentialStore
    scaffolder: Scaffolder
    root: Path
    load_warnings: list = field(default_factory=list)
    migrated_tokens: int = 0

    @classmethod
    def bootstrap(cls, root: Path | str | None = None,
                  memory_path: Path | str | None = None,
                  use_legacy: bool = True) -> "Services":
        """Create every service, migrating legacy data when present.

        ``root`` overrides the app data directory; ``use_legacy=False`` also
        disables the ``~/.scaffolder`` migration (used by tests and smoke runs
        so they never touch the real user profile).
        """
        base = Path(root) if root else paths.data_dir()
        paths.ensure_dirs(base)
        legacy_memory = paths.legacy_dir() / "memory.json"
        legacy_credentials = paths.legacy_dir() / "credentials.json"
        if not use_legacy:
            legacy_memory = None
            legacy_credentials = None

        target_memory = Path(memory_path) if memory_path else paths.memory_path(base)
        if (legacy_memory and not target_memory.exists()
                and legacy_memory.exists()):
            memory = ProjectMemory(path=target_memory, use_file_lock=False)
            memory.import_legacy(legacy_memory)
            memory.save()
        else:
            memory = ProjectMemory(path=target_memory)

        credentials = CredentialStore(base)
        migrated = 0
        if legacy_credentials and legacy_credentials.exists():
            try:
                migrated = credentials.migrate_legacy(legacy_credentials)
            except Exception:
                migrated = 0
        scaffolder = Scaffolder(memory, credentials, root=base)
        warnings = list(memory.load_warnings)
        if migrated:
            warnings.append(
                f"{migrated} token(s) were moved from the old plaintext file "
                "into secure storage.")
        return cls(memory=memory, credentials=credentials,
                   scaffolder=scaffolder, root=base, load_warnings=warnings,
                   migrated_tokens=migrated)

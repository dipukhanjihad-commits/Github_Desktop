"""Project Scaffolder — launcher.

Kept at the repository root so ``python gui.py`` (the historical command)
still works. The implementation lives in the :mod:`scaffolder_app` package.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from scaffolder_app.ui.app import run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())

"""Alias launcher: ``python main.py`` behaves like ``python gui.py``."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from scaffolder_app.ui.app import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run(sys.argv))

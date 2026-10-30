"""Render screenshots of the window for visual review.

Usage:  python scripts/screenshot.py <output-dir> [light|dark]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform.startswith("win")
                     else "")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from scaffolder_app.ui.main_window import MainWindow  # noqa: E402
from scaffolder_app.ui.services import Services  # noqa: E402
from scaffolder_app.ui.styles import build_stylesheet  # noqa: E402
from scaffolder_app.ui.theme import Typography, build_theme  # noqa: E402


def build_demo_data(root: Path, services: Services) -> str:
    template = root / "tpl-react"
    (template / "src").mkdir(parents=True)
    (template / "README.md").write_text("# {{project_name}}\n", encoding="utf-8")
    (template / "src" / "{{module_name}}.py").write_text(
        "NAME = '{{project_name}}'\n", encoding="utf-8")
    services.scaffolder.create_template_from_folder(
        "base-python", template, "Minimal Python project with src layout",
        ignore=[".git", ".venv", "__pycache__"], placeholders={"module_name": ""})

    names = ["api-service", "portfolio", "hello-world"]
    for index, name in enumerate(names):
        services.scaffolder.create_project(
            name, "base-python", root / "work" / name,
            {"module_name": name.replace("-", "_"), "project_name": name},
            account=None, create_repo=False)
        services.memory.set_group(name, "Work" if index else "")
    services.memory.set_pinned("api-service", True)

    main = root / "work" / "hello-world" / "README.md"
    main.write_text("# hello-world\n\nA sample project with pending edits.\n",
                    encoding="utf-8")
    (root / "work" / "hello-world" / "src" / "notes.txt").write_text(
        "scratch\n", encoding="utf-8")
    services.scaffolder.commit("hello-world", "Initial README")
    main.write_text("# hello-world\n\nA sample project with pending edits.\n"
                    "\nStatus: work in progress.\n", encoding="utf-8")
    services.scaffolder.create_version("hello-world", "v2", parent_label="v1")
    services.scaffolder.switch_version("hello-world", "v1")
    return "hello-world"


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("screenshots")
    mode = sys.argv[2] if len(sys.argv) > 2 else "light"
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="scaffolder-shots-"))

    app = QApplication.instance() or QApplication(sys.argv)
    services = Services.bootstrap(root=root, use_legacy=False)
    # The window derives its theme from settings, so pin the preference here.
    services.memory.set_setting("theme", mode)
    theme = build_theme(mode)
    app.setStyleSheet(build_stylesheet(theme))
    Typography().refresh()
    app.setFont(Typography().body())

    window = MainWindow(services)
    window.resize(1360, 860)
    window.show()
    app.processEvents()

    window.grab().save(str(out / f"01-empty-{mode}.png"))

    name = build_demo_data(root, services)
    window._refresh_projects()
    window.select_project(name)
    app.processEvents()
    window.workspace.tabs.setCurrentIndex(1)
    window.refresh_changes()
    app.processEvents()
    window.grab().save(str(out / f"02-changes-{mode}.png"))

    window.workspace.tabs.setCurrentIndex(0)
    app.processEvents()
    window.grab().save(str(out / f"03-overview-{mode}.png"))

    window.workspace.tabs.setCurrentIndex(2)
    app.processEvents()
    window.grab().save(str(out / f"04-history-{mode}.png"))

    from scaffolder_app.ui.palette import Command, CommandPalette

    palette = CommandPalette(window, theme,
                             [Command("New project", "Create", lambda: None,
                                      "Ctrl+N"),
                              Command("Commit changes", "Changes",
                                      lambda: None, "Ctrl+Shift+C"),
                              Command("Push", "Remote", lambda: None,
                                      "Ctrl+Shift+P"),
                              Command("Open in editor", "Open", lambda: None,
                                      "Ctrl+E")],
                             ["api-service", "hello-world"])
    palette.resize(600, 420)
    palette.show()
    app.processEvents()
    palette.grab().save(str(out / f"05-palette-{mode}.png"))
    palette.close()

    print("screenshots written to", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

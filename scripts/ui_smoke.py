"""Headless UI test: build the real window and drive core interactions.

Runs under the Qt ``offscreen`` platform so it works without a display.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from scaffolder_app.ui.main_window import MainWindow  # noqa: E402
from scaffolder_app.ui.services import Services  # noqa: E402
from scaffolder_app.ui.styles import build_stylesheet  # noqa: E402
from scaffolder_app.ui.theme import Typography, build_theme  # noqa: E402


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="scaffolder-ui-"))
    app = QApplication.instance() or QApplication(sys.argv)
    services = Services.bootstrap(root=root, use_legacy=False)
    theme = build_theme("light")
    app.setStyleSheet(build_stylesheet(theme))
    app.setFont(Typography().body())

    checks: list[tuple[str, bool]] = []

    def check(label: str, condition: bool) -> None:
        checks.append((label, bool(condition)))
        print(f"{'PASS' if condition else 'FAIL'}  {label}")

    window = MainWindow(services)
    window.resize(1280, 820)
    window.show()
    app.processEvents()

    check("window built", window.isVisible())
    check("sidebar shows empty state",
          window.sidebar.stack.currentWidget() is window.sidebar.empty)
    check("menus present", len(window.menuBar().actions()) >= 6)
    check("empty pane shown first",
          window.right_stack.currentWidget() is window.empty_state)

    template = root / "tpl"
    (template / "src").mkdir(parents=True)
    (template / "README.md").write_text("# {{project_name}}\n", encoding="utf-8")
    (template / "src" / "{{module_name}}.py").write_text(
        "NAME = '{{project_name}}'\n", encoding="utf-8")
    services.scaffolder.create_template_from_folder(
        "basic", template, "sample", ignore=[".git"],
        placeholders={"module_name": ""})

    project = services.scaffolder.create_project(
        "demo", "basic", root / "work" / "demo",
        {"module_name": "demo", "project_name": "demo"},
        account=None, create_repo=False)
    window._refresh_projects()
    app.processEvents()
    check("project appears in sidebar", window.sidebar.list.count() == 1)

    window.select_project(project["name"])
    app.processEvents()
    check("project selected", window.current_project == "demo")
    check("workspace replaces empty pane",
          window.right_stack.currentWidget() is window.workspace)
    check("versions row present",
          window.workspace.versions.view.topLevelItemCount() == 1)
    check("header shows name", window.workspace.header.title.text() == "demo")
    check("no changes initially",
          window.workspace.changes.table.rowCount() == 0)
    check("history populated",
          window.workspace.history.view.topLevelItemCount() >= 1)

    readme = root / "work" / "demo" / "README.md"
    readme.write_text("# demo\n\nchanged\n", encoding="utf-8")
    window.refresh_changes()
    app.processEvents()
    check("change detected", window.workspace.changes.table.rowCount() == 1)
    check("diff pane rendered",
          "+changed" in window.workspace.changes.diff_view.toPlainText())
    check("banner visible for dirty tree", window.workspace.banner.isVisible())

    window.workspace.changes.table.selectRow(0)
    app.processEvents()
    window._show_diff("README.md")
    check("explicit diff request",
          "README.md" in window.workspace.changes.diff_view.toPlainText())

    window._stage_paths(["README.md"], True)
    app.processEvents()
    check("staging works",
          window.workspace.changes.table.item(0, 2).text() in ("yes", "partial"))

    check("palette commands", len(window._palette_commands()) > 20)

    before = window.theme.name
    window.toggle_theme()
    app.processEvents()
    check("theme toggled", window.theme.name != before)

    services.scaffolder.commit("demo", "edit readme")
    window.refresh_current()
    app.processEvents()
    check("commit cleared changes",
          window.workspace.changes.table.rowCount() == 0)
    check("history grew",
          window.workspace.history.view.topLevelItemCount() >= 2)

    services.scaffolder.create_version("demo", "v2")
    window.refresh_current()
    app.processEvents()
    check("second version listed",
          window.workspace.versions.view.topLevelItemCount() == 2)
    services.scaffolder.switch_version("demo", "v1")
    window.refresh_current()
    app.processEvents()
    selected = window.workspace.versions.selected_label()
    check("current version row selected", selected == "v1")

    window.workspace.notes.setPlainText("remember this")
    window._save_notes()
    check("notes persisted",
          services.memory.get_project("demo")["notes"] == "remember this")

    services.scaffolder.delete_project("demo", move_folder=True)
    window._refresh_projects()
    app.processEvents()
    check("deleted project leaves the list",
          window.sidebar.stack.currentWidget() is window.sidebar.empty)

    window.close()
    app.processEvents()
    shutil.rmtree(root, ignore_errors=True)

    failures = [label for label, ok in checks if not ok]
    print()
    print(f"{len(checks) - len(failures)}/{len(checks)} checks passed")
    if failures:
        print("failed:", ", ".join(failures))
        return 1
    print("UI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

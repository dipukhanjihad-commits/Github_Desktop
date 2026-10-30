"""Offscreen UI tests (M19, M16, M17, M20, S16, S18, S23).

These build the real widgets without a display. They are skipped when PySide6
is unavailable so the backend suite still runs on a bare interpreter.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from scaffolder_app.ui.palette import fuzzy_score  # noqa: E402
from scaffolder_app.ui.services import Services  # noqa: E402
from scaffolder_app.ui.styles import build_stylesheet  # noqa: E402
from scaffolder_app.ui.theme import (  # noqa: E402
    DARK, LIGHT, Theme, build_theme, system_is_dark,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(["pytest"])
    return app


@pytest.fixture()
def ui(qapp, tmp_path):
    services = Services.bootstrap(root=tmp_path / "app", use_legacy=False)
    app = QApplication.instance()
    app.setStyleSheet(build_stylesheet(build_theme("light")))
    return qapp, services


# ---------------------------------------------------------------------------
# Theme system (M16/M15)
# ---------------------------------------------------------------------------

def test_light_and_dark_expose_identical_tokens():
    assert set(LIGHT) == set(DARK), "both palettes must define the same tokens"


def test_theme_preference_resolution():
    assert build_theme("light").name == "light"
    assert build_theme("dark").name == "dark"
    assert build_theme("system").name in ("light", "dark")
    assert build_theme("nonsense").name in ("light", "dark")


def test_theme_token_access():
    theme = Theme("light", LIGHT)
    assert theme["bg"] == LIGHT["bg"]
    assert theme.get("missing", "fallback") == "fallback"
    assert theme.is_dark is False


def test_stylesheet_generated_from_tokens():
    for name, palette in (("light", LIGHT), ("dark", DARK)):
        css = build_stylesheet(Theme(name, palette))
        assert palette["bg"] in css
        assert palette["primary"] in css
        assert "QPushButton#Primary" in css


def test_system_is_dark_returns_bool():
    assert isinstance(system_is_dark(), bool)


# ---------------------------------------------------------------------------
# Sidebar (M19/S16/N6)
# ---------------------------------------------------------------------------

def test_sidebar_shows_empty_state_until_projects_exist(ui):
    from scaffolder_app.ui.panels.sidebar import Sidebar

    _, _ = ui
    sidebar = Sidebar(build_theme("light"))
    sidebar.set_projects([])
    assert sidebar.stack.currentWidget() is sidebar.empty

    sidebar.set_projects([{
        "name": "alpha", "path": "/tmp/alpha", "repo_state": "SYNCED",
        "visibility": "private", "pinned": False, "group": "",
        "notes": "", "missing": False,
    }])
    assert sidebar.stack.currentWidget() is sidebar.list
    assert sidebar.list.count() == 1


def test_sidebar_filter_switches_to_no_results(ui):
    from scaffolder_app.ui.panels.sidebar import Sidebar

    _, _ = ui
    sidebar = Sidebar(build_theme("light"))
    sidebar.set_projects([{
        "name": "alpha", "path": "/tmp/alpha", "repo_state": "LOCAL_ONLY",
        "visibility": "private", "pinned": False, "group": "",
        "notes": "", "missing": False,
    }], query="zzz")
    assert sidebar.stack.currentWidget() is sidebar.no_results


def test_sidebar_pinned_projects_sort_first(ui):
    from scaffolder_app.ui.panels.sidebar import Sidebar

    _, _ = ui
    sidebar = Sidebar(build_theme("light"))
    projects = [
        {"name": "zebra", "path": "/z", "repo_state": "SYNCED",
         "visibility": "private", "pinned": True, "group": "", "notes": "",
         "missing": False},
        {"name": "apple", "path": "/a", "repo_state": "SYNCED",
         "visibility": "private", "pinned": False, "group": "", "notes": "",
         "missing": False},
    ]
    sidebar.set_projects(projects)
    first = sidebar.list.item(0).data(0x0100)
    assert first == "zebra"


# ---------------------------------------------------------------------------
# Widgets (S16/M19)
# ---------------------------------------------------------------------------

def test_badge_tones_render(ui):
    from scaffolder_app.ui.widgets import Badge

    _, _ = ui
    theme = build_theme("light")
    for tone in ("neutral", "primary", "success", "warning", "danger", "info"):
        badge = Badge("label", tone, theme)
        badge.restyle(theme, tone)
        assert badge.minimumHeight() == 20


def test_empty_state_action_signal(ui):
    from scaffolder_app.ui.widgets import EmptyState

    _, _ = ui
    fired = []
    widget = EmptyState("Nothing", "Nothing here", "Create",
                        build_theme("light"))
    widget.action_triggered.connect(lambda: fired.append(True))
    widget.button.click()
    assert fired == [True]


# ---------------------------------------------------------------------------
# Async scopes (S18): a list load must not block an action.
# ---------------------------------------------------------------------------

def test_task_runner_scopes_are_independent(ui):
    from scaffolder_app.ui.async_runner import TaskRunner

    app, _ = ui
    runner = TaskRunner()
    results = []
    runner.run(lambda progress: results.append("bg"), scope="background",
               on_done=lambda _r: None)
    runner.wait_for(5000)
    app.processEvents()
    assert results == ["bg"]
    assert runner.busy("action") is False


def test_task_runner_reports_errors(ui):
    from scaffolder_app.ui.async_runner import TaskRunner

    app, _ = ui
    runner = TaskRunner()
    seen = []
    runner.run(lambda progress: (_ for _ in ()).throw(ValueError("boom")),
               scope="action", on_error=lambda exc: seen.append(exc))
    runner.wait_for(5000)
    app.processEvents()
    assert seen and isinstance(seen[0], ValueError)


# ---------------------------------------------------------------------------
# Command palette (N7/N14)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,text,expected", [
    ("np", "New project", True),
    ("commit", "Commit changes", True),
    ("xyz", "Commit changes", False),
    ("", "anything", True),
])
def test_fuzzy_matching(query, text, expected):
    assert (fuzzy_score(query, text) > 0) is expected


def test_fuzzy_prefers_prefix_matches():
    assert fuzzy_score("com", "Commit changes") > fuzzy_score("com", "Change the commit")


def test_palette_filters_and_runs(ui):
    from scaffolder_app.ui.palette import Command, CommandPalette

    _, _ = ui
    ran = []
    commands = [Command("New project", "Create", lambda: ran.append("new")),
                Command("Push", "Remote", lambda: ran.append("push"))]
    palette = CommandPalette(None, build_theme("light"), commands, ["demo"])
    palette.input.setText("push")
    assert palette.list.count() == 1
    palette._activate(palette.list.item(0))
    assert ran == ["push"]


# ---------------------------------------------------------------------------
# Whole window (M14/M17/M19)
# ---------------------------------------------------------------------------

def test_main_window_builds_and_reacts_to_selection(ui):
    from scaffolder_app.ui.main_window import MainWindow

    app, services = ui
    window = MainWindow(services)
    try:
        window.show()
        app.processEvents()
        assert window.right_stack.currentWidget() is window.empty_state

        template = services.root / "tpl"
        (template / "src").mkdir(parents=True)
        (template / "README.md").write_text("# {{project_name}}\n",
                                            encoding="utf-8")
        (template / "src" / "{{module_name}}.py").write_text("x = 1\n",
                                                             encoding="utf-8")
        services.scaffolder.create_template_from_folder(
            "basic", template, ignore=[".git"],
            placeholders={"module_name": ""})
        project = services.scaffolder.create_project(
            "demo", "basic", services.root / "work" / "demo",
            {"module_name": "demo", "project_name": "demo"},
            account=None, create_repo=False)

        window._refresh_projects()
        window.select_project(project["name"])
        app.processEvents()

        assert window.current_project == "demo"
        assert window.right_stack.currentWidget() is window.workspace
        assert window.workspace.versions.view.topLevelItemCount() == 1
        assert window.workspace.header.title.text() == "demo"
    finally:
        window.close()
        app.processEvents()


def test_window_state_persists(tmp_path):
    from scaffolder_app.ui.platform import WindowState

    path = tmp_path / "state.json"
    state = WindowState(path)
    state.set("current_project", "demo")
    state.set("filter", "de")
    state.save()

    reloaded = WindowState(path)
    assert reloaded.get("current_project") == "demo"
    assert reloaded.get("filter") == "de"


def test_window_state_tolerates_corrupt_file(tmp_path):
    from scaffolder_app.ui.platform import WindowState

    path = tmp_path / "state.json"
    path.write_text("{ not json", encoding="utf-8")
    state = WindowState(path)
    assert state.get("missing", "default") == "default"

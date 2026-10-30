"""Main application window: menus, layout, keyboard map and action routing.

The window owns no business logic; it translates UI intent into
:class:`scaffolder_app.scaffolder.Scaffolder` calls run through the task
runner, then refreshes the affected views.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu, QProgressBar,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import __app_name__, __version__, paths
from ..errors import (
    ConflictError, DivergedError, DirtyWorkTreeError, MissingFolderError,
    ScaffolderError, TokenExpiredError,
)
from ..github_api import DeviceFlow
from . import platform as ui_platform
from .async_runner import TaskRunner, error_hint, error_message
from .dialogs.account_dialogs import (
    AddAccountDialog, ConflictedDialog, DeviceFlowDialog, ManageAccountsDialog,
    ManifestImportDialog, StashDialog, TaskRunDialog, TemplateDialog,
)
from .dialogs.base import AppDialog, ErrorDialog, InfoDialog, TokenUpdateDialog
from .dialogs.project_dialogs import (
    CloneDialog, DeleteProjectDialog, DeleteVersionDialog, ForcePushDialog,
    ImportFolderDialog, NewProjectDialog, RelinkDialog, RenameDialog,
)
from .dialogs.workflow_dialogs import (
    CommitDialog, GroupDialog, MergeDialog, NotesDialog, ReleaseDialog,
    SettingsDialog, TrashDialog, VersionDialog,
)
from .palette import Command, CommandPalette
from .panels.project_view import ProjectWorkspace
from .panels.sidebar import Sidebar
from .services import Services
from .styles import build_stylesheet
from .theme import Typography, build_theme
from .widgets import EmptyState, ToastHost

MAX_REFRESH_SECONDS = 600


def _close_dialog() -> None:
    """Accept the modal dialog that is currently open, if any.

    Submit callbacks are constructed before their dialog exists, so instead of
    a forward reference they close whatever modal is on screen (these flows
    always have exactly one).
    """
    from PySide6.QtWidgets import QApplication, QDialog

    widget = QApplication.activeModalWidget()
    if isinstance(widget, QDialog):
        widget.accept()

MAX_REFRESH_SECONDS = 600


class MainWindow(QMainWindow):
    def __init__(self, services: Services,
                 state_path: Path | str | None = None) -> None:
        super().__init__()
        self.services = services
        self.memory = services.memory
        self.scaffolder = services.scaffolder
        self.state = ui_platform.WindowState(
            Path(state_path) if state_path else services.root / "window_state.json")
        pref = self.memory.get_setting("theme", "system")
        self.theme = build_theme(pref)
        self.typography = Typography()
        self.typography.scale = min(1.25, max(0.9, ui_platform.screen_scale(
            QApplication.instance()) * 0.95))
        self.runner = TaskRunner(self)
        self.toast = ToastHost(self, self.theme)

        self.current_project: str = ""
        self._current_branch: str = ""
        self._change_count: int = 0
        self._pending_switch: tuple[str, str] | None = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh)

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.setMinimumSize(980, 620)
        self._apply_theme()
        self._build_ui()
        self._build_menus()
        self._bind_shortcuts()
        self.runner.task_failed.connect(self._on_task_failed)
        self.runner.scope_changed.connect(self._on_scope_changed)

        self.state.restore_window(self)
        self._refresh_projects()
        self._refresh_accounts()
        self._validate_paths()
        self._show_load_warnings()
        self._configure_auto_refresh()
        restore = self.state.get("current_project")
        if restore and self.memory.get_project(restore):
            self.select_project(restore)
        self._apply_saved_window_state()

    # =================================================================== ui

    def _apply_theme(self) -> None:
        QApplication.instance().setStyleSheet(build_stylesheet(self.theme))
        self.typography = Typography()
        self.typography.scale = min(1.25, max(0.9, ui_platform.screen_scale(
            QApplication.instance()) * 0.95))
        app_font = self.typography.body()
        QApplication.instance().setFont(app_font)

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.sidebar = Sidebar(self.theme)
        self.sidebar.project_selected.connect(self._on_project_selected)
        self.sidebar.new_project.connect(self.new_project)
        self.sidebar.clone_requested.connect(self.clone_project)
        self.sidebar.import_requested.connect(self.import_folder)
        self.sidebar.context_action.connect(self._on_sidebar_action)
        self.sidebar.filter_changed.connect(
            lambda text: self.state.set("filter", text))
        self.splitter.addWidget(self.sidebar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.workspace = ProjectWorkspace(self.theme)
        self._wire_workspace()

        # A stack guarantees exactly one of the two panes is on screen.
        self.right_stack = QStackedWidget()
        self.empty_state = EmptyState(
            "Select a project",
            "Choose a project on the left, create one from a template, or "
            "clone something from GitHub.",
            "New project", self.theme)
        self.empty_state.action_triggered.connect(self.new_project)
        self.right_stack.addWidget(self.empty_state)
        self.right_stack.addWidget(self.workspace)
        self.right_stack.setCurrentWidget(self.empty_state)
        right_layout.addWidget(self.right_stack, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([288, 1000])
        self.state.restore_splitter(self.splitter, "splitter")
        outer.addWidget(self.splitter, 1)

        outer.addWidget(self._build_status_bar())
        self.setCentralWidget(central)
        self.sidebar.set_projects([])

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(8)
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("Subtle")
        layout.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(120)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.account_label = QLabel("")
        self.account_label.setObjectName("Subtle")
        layout.addWidget(self.account_label)
        self.backend_label = QLabel(f"tokens: {self.services.credentials.backend_name}")
        self.backend_label.setObjectName("Subtle")
        layout.addWidget(self.backend_label)
        return bar

    def _wire_workspace(self) -> None:
        workspace = self.workspace
        workspace.action.connect(self._on_workspace_action)
        workspace.version_selected.connect(self._on_version_row_selected)
        workspace.version_double_clicked.connect(self.switch_version)
        workspace.version_context.connect(self._show_version_menu)
        workspace.commit_requested.connect(self.commit_project)
        workspace.changes_refresh.connect(self.refresh_changes)
        workspace.stage_requested.connect(self._stage_paths)
        workspace.discard_requested.connect(self._discard_paths)
        workspace.open_file_requested.connect(self._open_file)
        workspace.diff_requested.connect(self._show_diff)
        workspace.history_commit_selected.connect(self._show_commit)
        workspace.open_on_github.connect(self.open_on_github)
        workspace.notes.textChanged.connect(self._schedule_notes_save)
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(700)
        self._notes_timer.timeout.connect(self._save_notes)
        self._notes_dirty = False
        self._build_more_menu()

    def _build_more_menu(self) -> None:
        menu = self.workspace.more
        menu.addAction("New version\u2026", self.new_version)
        menu.addAction("Merge / Pull request\u2026", self.merge_versions)
        menu.addAction("Stashes\u2026", self.show_stashes)
        menu.addSeparator()
        menu.addAction("Force push\u2026", self.force_push)
        menu.addAction("Fetch from GitHub", self.fetch_project)
        menu.addAction("Run a task\u2026", self.run_task)
        menu.addAction("Set up environment\u2026", self.setup_env)
        menu.addAction("Open terminal", self.open_terminal)
        menu.addSeparator()
        menu.addAction("Create repository\u2026", self.create_repository)
        menu.addAction("Change visibility\u2026", self.change_visibility)
        menu.addAction("Create release\u2026", self.create_release)
        menu.addSeparator()
        menu.addAction("Use account identity for commits", self.fix_identity)
        menu.addAction("Set group\u2026", self.set_group)
        menu.addAction("Rename\u2026", self.rename_project)
        menu.addAction("Relink folder\u2026", self.relink_project)
        menu.addAction("Delete project\u2026", self.delete_project)

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action(file_menu, "New project\u2026", self.new_project, "Ctrl+N")
        self._action(file_menu, "New template\u2026", self.new_template)
        self._action(file_menu, "Clone from GitHub\u2026", self.clone_project,
                     "Ctrl+Shift+N")
        self._action(file_menu, "Add existing folder\u2026", self.import_folder)
        file_menu.addSeparator()
        self._action(file_menu, "Settings\u2026", self.open_settings, "Ctrl+,")
        self._action(file_menu, "Trash\u2026", self.open_trash)
        self._action(file_menu, "Error log\u2026", self.open_error_log)
        file_menu.addSeparator()
        self._action(file_menu, "Quit", self.close, "Ctrl+Q")

        edit_menu = bar.addMenu("&Edit")
        self._action(edit_menu, "Commit\u2026", self.commit_project,
                     "Ctrl+Shift+C")
        self._action(edit_menu, "Stage all", self.stage_all)
        self._action(edit_menu, "Discard all changes\u2026", self.discard_all)
        edit_menu.addSeparator()
        self._action(edit_menu, "Project notes\u2026", self.edit_notes)

        view_menu = bar.addMenu("&View")
        self._action(view_menu, "Command palette\u2026", self.open_palette, "Ctrl+K")
        self._action(view_menu, "Toggle theme", self.toggle_theme, "Ctrl+Shift+T")
        self._action(view_menu, "Focus search", self.focus_search, "Ctrl+F")
        view_menu.addSeparator()
        self._action(view_menu, "Refresh", self.refresh_current, "F5")

        git_menu = bar.addMenu("&Git")
        self._action(git_menu, "Pull", self.pull_project)
        self._action(git_menu, "Push", self.push_project)
        self._action(git_menu, "Fetch", self.fetch_project)
        git_menu.addSeparator()
        self._action(git_menu, "New version\u2026", self.new_version)
        self._action(git_menu, "Merge\u2026", self.merge_versions)
        self._action(git_menu, "Switch version\u2026", self.switch_version_dialog)
        self._action(git_menu, "History\u2026", lambda: self._focus_tab(2))

        gh_menu = bar.addMenu("&GitHub")
        self._action(gh_menu, "Add account\u2026", self.add_account)
        self._action(gh_menu, "Manage accounts\u2026", self.manage_accounts)
        gh_menu.addSeparator()
        self._action(gh_menu, "Open repository", self.open_on_github)
        self._action(gh_menu, "Create repository\u2026", self.create_repository)

        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "Keyboard shortcuts", self.show_shortcuts,
                     "Ctrl+/")
        self._action(help_menu, "About", self.about)

    def _action(self, menu: QMenu, text: str, slot: Callable[[], None],
                shortcut: str = "") -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    def _bind_shortcuts(self) -> None:
        for keys, slot in (
            ("Ctrl+Shift+P", self.push_project),
            ("Ctrl+Shift+L", self.pull_project),
            ("Ctrl+O", self.open_folder),
            ("Ctrl+E", self.open_in_editor),
            ("Ctrl+Shift+Q", self.quit_confirm),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)

    # ============================================================== helpers

    def _apply_saved_window_state(self) -> None:
        if self.state.get("maximized"):
            self.showMaximized()
        saved_filter = self.state.get("filter") or ""
        if saved_filter:
            self.sidebar.search.blockSignals(True)
            self.sidebar.search.setText(saved_filter)
            self.sidebar.search.blockSignals(False)
            self.sidebar.set_projects(self.memory.list_projects(), saved_filter)

    def _error(self, exc: BaseException, context: str = "") -> None:
        ErrorDialog(self, exc, self.theme, context).exec()

    def _info(self, title: str, message: str) -> None:
        InfoDialog(self, title, message, self.theme).exec()

    def _confirm(self, title: str, message: str, ok: str = "Continue",
                 danger: bool = False) -> bool:
        from .dialogs.base import ConfirmDialog

        dialog = ConfirmDialog(self, title, message, self.theme, ok, danger)
        return dialog.exec() == AppDialog.DialogCode.Accepted

    def _require_project(self) -> str:
        if not self.current_project:
            self.toast.show("Select a project first.", "warning")
            return ""
        return self.current_project

    def _on_scope_changed(self, scope: str, busy: bool, label: str) -> None:
        if scope == "action":
            self.progress.setVisible(busy)
            if busy:
                self.set_status(label)
        elif busy:
            self.set_status(label)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Refresh sync state when the window regains focus (N9)."""
        super().changeEvent(event)
        if event.type() != QEvent.Type.ActivationChange:
            return
        if not self.isActiveWindow() or not self.current_project:
            return
        if not self.memory.get_setting("update_sync_on_focus", True):
            return
        if self.runner.busy("action"):
            return
        self._on_refresh_all()

    def _on_task_failed(self, exc: BaseException) -> None:
        self.memory.log_error("ui", error_message(exc)[:500])
        if isinstance(exc, TokenExpiredError):
            self._prompt_token(exc.account)
            return
        if isinstance(exc, MissingFolderError):
            self._handle_missing(exc)
            return
        self._error(exc)

    def _handle_missing(self, exc: MissingFolderError) -> None:
        if self._confirm("Project folder is missing",
                         f"{exc.path}\n\nRelink it to a new location?",
                         "Relink\u2026"):
            self.relink_project()
        else:
            self._refresh_projects()

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def toast_ok(self, text: str) -> None:
        self.toast.show(text, "success")
        self.set_status(text)

    def toast_warn(self, text: str) -> None:
        self.toast.show(text, "warning")
        self.set_status(text)

    # ========================================================== data refresh

    def _refresh_projects(self) -> None:
        projects = self.memory.list_projects()
        self.sidebar.set_projects(projects, self.sidebar.search.text() or "")

    def _validate_paths(self) -> None:
        problems = self.scaffolder.validate_paths()
        if problems:
            names = ", ".join(problem["name"] for problem in problems)
            self.toast_warn(f"Folder missing for: {names}")

    def _show_load_warnings(self) -> None:
        for warning in self.services.load_warnings:
            self.toast_warn(warning)

    def _refresh_accounts(self) -> None:
        accounts = self.memory.list_accounts()
        names = ", ".join(a["username"] for a in accounts)
        self.account_label.setText(names or "no GitHub account")

    def _on_project_selected(self, name: str) -> None:
        self.select_project(name)

    def select_project(self, name: str) -> None:
        project = self.memory.get_project(name)
        if not project:
            return
        self.current_project = name
        self.state.set("current_project", name)
        self.right_stack.setCurrentWidget(self.workspace)
        self._current_branch = project.get("current_version") or ""
        self.workspace.versions._current = project.get("current_version") or ""
        self.refresh_project()
        self.refresh_changes()
        self._load_history()
        self._load_github()

    def refresh_project(self) -> None:
        name = self.current_project
        project = self.memory.get_project(name)
        if not project:
            return
        summary = self.memory.get_project_summary(name)
        status = self.scaffolder.project_status(name)
        self._change_count = int(status.get("changes", 0))
        self.workspace.show_project(
            project, summary, branch=status.get("branch", ""),
            changes=self._change_count, status=status,
            notes=project.get("notes", ""))
        self._current_branch = status.get("branch", "")

    def refresh_changes(self) -> None:
        name = self.current_project
        if not name:
            return
        try:
            entries = self.scaffolder.get_changes(name)
        except ScaffolderError as exc:
            self.workspace.changes.populate([])
            self.workspace.changes.show_diff(str(exc))
            return
        self._change_count = len(entries)
        self.workspace.changes.populate(entries)
        project = self.memory.get_project(name) or {}
        self.workspace.update_banner(
            project, len(entries), self.memory.get_project_summary(name))
        if entries:
            self.workspace.changes.preview_first()

    def _load_history(self) -> None:
        name = self.current_project
        if not name:
            return
        try:
            commits = self.scaffolder.history(name, limit=120)
        except ScaffolderError:
            commits = []
        self.workspace.history.populate(commits)

    def _load_github(self) -> None:
        name = self.current_project
        project = self.memory.get_project(name) if name else None
        if not project or not project.get("repo_url"):
            self.workspace.issues.set_message("This project has no repository.")
            return
        if not self.services.credentials.get(project.get("owner") or ""):
            self.workspace.issues.set_message("No GitHub token for this account.")
            return
        self.workspace.issues.set_message("Loading\u2026")

        def work(_progress):
            return self.scaffolder.fetch_github_summary(name)

        def done(result: Any) -> None:
            if self.current_project != name:
                return
            items = (result or {}).get("items", [])
            error = (result or {}).get("error", "")
            if error:
                self.workspace.issues.set_message(error)
            else:
                self.workspace.issues.populate(items)

        self.runner.run(work, scope="background", on_done=done,
                        on_error=lambda exc: self.workspace.issues.set_message(
                            str(exc)))

    def _on_refresh_all(self) -> None:
        name = self.current_project
        if not name:
            return

        def work(_progress):
            self.scaffolder.refresh_all_versions(name)
            return self.scaffolder.project_status(name)

        def done(_result: Any) -> None:
            self.refresh_project()
            self._refresh_projects()
            self.set_status("Refreshed")

        self.runner.run(work, scope="background", label="Refreshing\u2026",
                        on_done=done, on_error=lambda _e: None)

    def refresh_current(self) -> None:
        self.refresh_project()
        self.refresh_changes()
        self._load_history()
        self._on_refresh_all()

    def _auto_refresh(self) -> None:
        if not self.current_project or self.runner.busy("action"):
            return
        self._on_refresh_all()

    def _configure_auto_refresh(self) -> None:
        seconds = int(self.memory.get_setting("auto_refresh_seconds", 0) or 0)
        seconds = max(0, min(MAX_REFRESH_SECONDS, seconds))
        if seconds:
            self._refresh_timer.start(seconds * 1000)
        else:
            self._refresh_timer.stop()

    # ========================================================= project edits

    def _on_workspace_action(self, key: str) -> None:
        handlers = {
            "open_folder": self.open_folder,
            "editor": self.open_in_editor,
            "commit": self.commit_project,
            "pull": self.pull_project,
            "push": self.push_project,
            "new_version": self.new_version,
        }
        handler = handlers.get(key)
        if handler:
            handler()

    def _focus_tab(self, index: int) -> None:
        self.workspace.tabs.setCurrentIndex(index)

    def open_folder(self) -> None:
        name = self._require_project()
        if name:
            self._guard_folder(name, lambda: self.scaffolder.open_folder(name))

    def open_in_editor(self) -> None:
        name = self._require_project()
        if name:
            self._guard_folder(name,
                               lambda: self.scaffolder.open_in_editor(name))

    def open_terminal(self) -> None:
        name = self._require_project()
        if name:
            self._guard_folder(name,
                               lambda: self.scaffolder.open_terminal(name))

    def _guard_folder(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except MissingFolderError as exc:
            self._handle_missing(exc)
        except ScaffolderError as exc:
            self._error(exc)

    def new_project(self) -> None:
        templates = self.memory.list_templates()
        if not templates:
            if self._confirm("No templates yet",
                             "Create your first template folder to scaffold from?",
                             "New template\u2026"):
                self.new_template()
            return
        accounts = [a["username"] for a in self.memory.list_accounts()]
        default_path = self.memory.get_setting("default_projects_path") or str(
            paths.default_projects_path())

        def submit(name: str, data: dict) -> None:
            _close_dialog()
            self._create_project(data)

        dialog = NewProjectDialog(
            self, self.theme, templates=templates, accounts=accounts,
            default_path=default_path,
            default_visibility=self.memory.get_setting("default_visibility",
                                                       "private"),
            on_submit=submit,
            check_name=self.scaffolder.check_name_available,
            on_preview=self.scaffolder.preview_template)
        dialog._async_check = self._async_name_check
        dialog.exec()

    def _async_name_check(self, name: str, account: str | None, done) -> None:
        def work(_progress):
            return self.scaffolder.check_name_available(name, account)

        self.runner.run(work, scope="background", on_done=done,
                        on_error=lambda _e: None)

    def _create_project(self, data: dict) -> None:
        def work(progress):
            return self.scaffolder.create_project(
                data["name"], data["template"], data["dest"],
                data["placeholders"], visibility=data["visibility"],
                account=data["account"], create_repo=data["create_repo"],
                progress=progress)

        def done(project: Any) -> None:
            self._refresh_projects()
            if isinstance(project, dict) and project.get("name"):
                self.sidebar.select(project["name"])
            self.toast_ok(f"Created '{data['name']}'")

        self.runner.run(work, scope="action", label="Creating project\u2026",
                        on_done=done)

    def clone_project(self) -> None:
        accounts = [a["username"] for a in self.memory.list_accounts()]
        if not accounts:
            self._info("No accounts", "Add a GitHub account before cloning.")
            return
        default_path = self.memory.get_setting("default_projects_path") or str(
            paths.default_projects_path())

        def load_repos(account: str, done, failed) -> None:
            def work(_progress):
                return self.scaffolder.list_repositories(account)

            self.runner.run(work, scope="background", on_done=done,
                            on_error=failed)

        def submit(name: str, data: dict) -> None:
            _close_dialog()

            def work(progress):
                return self.scaffolder.clone_repository(
                    data["url"], data["dest"], project_name=data["name"],
                    account=data["account"],
                    import_all_branches=data["import_branches"],
                    progress=progress)

            def finished(project: Any) -> None:
                self._refresh_projects()
                if isinstance(project, dict):
                    self.sidebar.select(project.get("name", ""))
                self.toast_ok(f"Cloned '{name}'")

            self.runner.run(work, scope="action",
                            label=f"Cloning {name}\u2026", on_done=finished)

        dialog = CloneDialog(self, self.theme, accounts=accounts,
                             default_path=default_path, on_submit=submit,
                             load_repos=load_repos)
        dialog.exec()

    def import_folder(self) -> None:
        def submit(name: str, data: dict) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.link_existing_folder(data["path"], name)

            def done(project: Any) -> None:
                self._refresh_projects()
                if isinstance(project, dict):
                    self.sidebar.select(project.get("name", ""))
                self.toast_ok(f"Added '{name}'")

            self.runner.run(work, scope="action", label="Adding folder\u2026",
                            on_done=done)

        dialog = ImportFolderDialog(self, self.theme, on_submit=submit)
        dialog.exec()

    def relink_project(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}

        def submit(_name: str, data: dict) -> None:
            _close_dialog()
            try:
                self.scaffolder.relink_project(name, data["path"])
            except ScaffolderError as exc:
                self._error(exc)
                return
            self._refresh_projects()
            self.refresh_current()
            self.toast_ok(f"Relinked '{name}'")

        dialog = RelinkDialog(self, self.theme, project, submit)
        dialog.exec()

    def rename_project(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}

        def submit(new: str, data: dict) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.rename_project(
                    name, data["new"], rename_remote=data["rename_remote"])

            def done(result: Any) -> None:
                if isinstance(result, dict) and result.get("errors"):
                    self.toast_warn("; ".join(result["errors"]))
                self._refresh_projects()
                self.sidebar.select(data["new"])
                self.current_project = data["new"]
                self.refresh_current()
                self.toast_ok(f"Renamed to '{data['new']}'")

            self.runner.run(work, scope="action", label="Renaming\u2026",
                            on_done=done)

        dialog = RenameDialog(self, self.theme, project, submit)
        dialog.exec()

    def delete_project(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}

        def submit(_name: str, data: dict) -> None:
            _close_dialog()
            self._perform_delete(name, data)

        dialog = DeleteProjectDialog(self, self.theme, project, submit)
        dialog.exec()

    def _perform_delete(self, name: str, data: dict) -> None:
        mode = data.get("mode", "trash")
        remove_remote = bool(data.get("remove_remote"))

        def work(_progress):
            if mode == "keep":
                result = self.scaffolder.delete_project_keep_files(
                    name, remove_remote=remove_remote)
            else:
                result = self.scaffolder.delete_project(
                    name, remove_remote=remove_remote,
                    delete_files=(mode == "delete"))
            return result

        def done(result: Any) -> None:
            if self.current_project == name:
                self.current_project = ""
                self.state.set("current_project", "")
                self.right_stack.setCurrentWidget(self.empty_state)
            self._refresh_projects()
            errors = (result or {}).get("errors") or []
            if errors:
                self.toast_warn("; ".join(errors))
            else:
                self.toast_ok(f"Deleted '{name}'")

        self.runner.run(work, scope="action", label="Deleting\u2026", on_done=done)

    def set_group(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}
        groups = sorted({p.get("group", "") for p in self.memory.list_projects()
                         if p.get("group")})

        def submit(group: str) -> None:
            _close_dialog()
            self.memory.set_group(name, group)
            self._refresh_projects()
            self.sidebar.select(name)

        dialog = GroupDialog(self, self.theme, project, groups, submit)
        dialog.exec()

    def _toggle_pin(self, name: str) -> None:
        project = self.memory.get_project(name)
        if not project:
            return
        self.memory.set_pinned(name, not project.get("pinned"))
        self._refresh_projects()
        self.sidebar.select(name)

    # ================================================================ commit

    def commit_project(self) -> None:
        name = self._require_project()
        if not name:
            return
        try:
            changes = self.scaffolder.get_changes(name)
        except ScaffolderError as exc:
            self._error(exc)
            return
        if not changes:
            self.toast_warn("Nothing to commit.")
            return
        if self._change_count == 0:
            self.refresh_changes()

        def submit(data: dict) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.commit(
                    name, data["message"], paths=data["paths"],
                    stage_all=data["stage_all"], amend=data["amend"],
                    push=data["push"])

            def done(result: Any) -> None:
                sha = (result or {}).get("sha", "")
                self.refresh_current()
                self._refresh_projects()
                self.toast_ok(f"Committed {sha}" if sha else "Committed")

            self.runner.run(work, scope="action", label="Committing\u2026",
                            on_done=done)

        dialog = CommitDialog(
            self, self.theme, name, changes,
            int(self.memory.get_setting("max_commit_title_length", 72) or 72),
            submit)
        dialog.exec()

    def stage_all(self) -> None:
        name = self._require_project()
        if not name:
            return
        try:
            self.scaffolder.stage(name, [entry["path"] for entry in
                                         self.scaffolder.get_changes(name)])
            self.refresh_changes()
            self.set_status("Staged all changes")
        except ScaffolderError as exc:
            self._error(exc)

    def _stage_paths(self, paths_list: list[str], stage: bool) -> None:
        name = self._require_project()
        if not name or not paths_list:
            return
        try:
            if stage:
                self.scaffolder.stage(name, paths_list)
            else:
                self.scaffolder.unstage(name, paths_list)
            self.refresh_changes()
        except ScaffolderError as exc:
            self._error(exc)

    def _discard_paths(self, paths_list: list[str]) -> None:
        name = self._require_project()
        if not name or not paths_list:
            return
        if not self._confirm(
                "Discard changes?",
                f"Revert {len(paths_list)} file(s) to their last committed "
                "state?\n\nUntracked files are deleted.", "Discard", danger=True):
            return
        try:
            self.scaffolder.discard(name, paths_list)
            self.refresh_changes()
            self.toast_ok("Changes discarded")
        except ScaffolderError as exc:
            self._error(exc)

    def discard_all(self) -> None:
        name = self._require_project()
        if not name:
            return
        if not self._confirm("Discard all changes?",
                             "Every uncommitted change in this project will be "
                             "lost. This cannot be undone.", "Discard all",
                             danger=True):
            return
        try:
            self.scaffolder.discard_all(name)
            self.refresh_current()
            self.toast_ok("All changes discarded")
        except ScaffolderError as exc:
            self._error(exc)

    def _show_diff(self, path: str) -> None:
        name = self.current_project
        if not name or not path:
            return
        try:
            text = self.scaffolder.diff(name, path)
        except ScaffolderError as exc:
            text = str(exc)
        self.workspace.changes.show_diff(text)

    def _open_file(self, path: str) -> None:
        name = self._require_project()
        if not name or not path:
            return
        self._guard_folder(
            name, lambda: self.scaffolder.open_in_editor(name, path))

    def edit_notes(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}

        def submit(text: str) -> None:
            _close_dialog()
            self.memory.set_notes(name, text)
            self.refresh_project()
            self._refresh_projects()
            self.toast_ok("Notes saved")

        dialog = NotesDialog(self, self.theme, project, submit)
        dialog.exec()

    def _schedule_notes_save(self) -> None:
        self._notes_dirty = True
        self._notes_timer.start()

    def _save_notes(self) -> None:
        if not self._notes_dirty or not self.current_project:
            return
        self._notes_dirty = False
        notes = self.workspace.notes.toPlainText()
        try:
            self.memory.set_notes(self.current_project, notes)
        except ScaffolderError:
            pass

    # ============================================================== versions

    def _on_version_row_selected(self, label: str) -> None:
        self._selected_version = label

    def switch_version_dialog(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}
        versions = list(project.get("versions", {}))
        if len(versions) < 2:
            self._info("Only one version", "Create another version first.")
            return

        dialog = VersionDialog.__new__(VersionDialog)  # placeholder, replaced below
        del dialog
        self._prompt_switch(name, versions,
                            project.get("current_version") or versions[0])

    def _prompt_switch(self, name: str, versions: list[str], current: str) -> None:
        from .dialogs.base import AppDialog

        dialog = AppDialog(self, "Switch version", self.theme,
                           subtitle="Choose the version to check out.",
                           width=420)
        from PySide6.QtWidgets import QComboBox

        picker = QComboBox()
        picker.addItems([v for v in versions if v != current])
        dialog.body.addWidget(picker)
        dialog.add_cancel()
        dialog.add_primary("Switch", lambda: (dialog.accept(),
                                              self.switch_version(
                                                  picker.currentText())))
        dialog.exec()

    def switch_version(self, label: str) -> None:
        name = self.current_project
        if not name or not label:
            return
        project = self.memory.get_project(name) or {}
        if project.get("current_version") == label:
            return
        strategy = self.memory.get_setting("switch_strategy", "stash")

        def work(_progress):
            return self.scaffolder.switch_version(name, label, strategy=strategy)

        def done(result: Any) -> None:
            info = result or {}
            self.refresh_current()
            self._refresh_projects()
            if info.get("stash_restored") is False and info.get("stash"):
                self.toast_warn(
                    "Your changes were stashed but could not be re-applied. "
                    "Use Stashes\u2026 to recover them.")
            else:
                self.toast_ok(f"Switched to {label}")

        def failed(exc: BaseException) -> None:
            if isinstance(exc, DirtyWorkTreeError):
                self._offer_switch_resolution(name, label, exc)
            else:
                self._on_task_failed(exc)

        self.runner.run(work, scope="action",
                        label=f"Switching to {label}\u2026", on_done=done,
                        on_error=failed)

    def _offer_switch_resolution(self, name: str, label: str,
                                 exc: DirtyWorkTreeError) -> None:
        dialog = AppDialog(self, "Uncommitted changes", self.theme,
                           subtitle="Choose how to handle the changes before "
                                    "switching.", width=480)

        def act(strategy: str) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.switch_version(name, label,
                                                      strategy=strategy)

            def done(_result: Any) -> None:
                self.refresh_current()
                self._refresh_projects()
                self.toast_ok(f"Switched to {label}")

            self.runner.run(work, scope="action", label="Switching\u2026",
                            on_done=done)

        dialog.add_button("Stash and switch", primary=True,
                          on_click=lambda: act("stash"))
        dialog.add_button("Commit and switch", on_click=lambda: act("commit"))
        dialog.add_button("Discard and switch", danger=True,
                          on_click=lambda: act("discard"))
        dialog.add_cancel("Cancel")
        dialog.exec()

    def new_version(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}

        def submit(data: dict) -> None:
            _close_dialog()

            def work(progress):
                return self.scaffolder.create_version(
                    name, data["label"], parent_label=data["parent"],
                    strategy=data["strategy"], progress=progress)

            def done(_result: Any) -> None:
                self.refresh_current()
                self._refresh_projects()
                self.toast_ok(f"Created version {data['label']}")

            self.runner.run(work, scope="action",
                            label=f"Creating {data['label']}\u2026", on_done=done)

        dialog = VersionDialog(self, self.theme, project, submit)
        dialog.exec()

    def _show_version_menu(self, label: str, global_pos) -> None:
        if not label:
            return
        menu = QMenu(self)
        menu.addAction(f"Switch to {label}",
                       lambda: self.switch_version(label))
        menu.addAction("Push this version", lambda: self.push_project(label))
        menu.addAction("Pull this version", lambda: self.pull_project(label))
        menu.addSeparator()
        menu.addAction("Create version from this\u2026", self.new_version)
        menu.addAction("Merge into another version\u2026", self.merge_versions)
        menu.addSeparator()
        menu.addAction("Delete this version\u2026",
                       lambda: self.delete_version(label))
        menu.exec(global_pos)

    def delete_version(self, label: str) -> None:
        name = self._require_project()
        if not name or not label:
            return
        project = self.memory.get_project(name) or {}

        def submit(version_label: str, data: dict) -> None:
            _close_dialog()

            def work(progress):
                return self.scaffolder.delete_version(
                    name, version_label, remove_remote=data["remove_remote"],
                    progress=progress)

            def done(_result: Any) -> None:
                self.refresh_current()
                self._refresh_projects()
                self.toast_ok(f"Deleted version {version_label}")

            self.runner.run(work, scope="action", label="Deleting version\u2026",
                            on_done=done)

        dialog = DeleteVersionDialog(self, self.theme, project, label, submit)
        dialog.exec()

    def merge_versions(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}
        if len(project.get("versions", {})) < 2:
            self._info("Need two versions", "Create another version first.")
            return

        def on_merge(data: dict) -> None:
            _close_dialog()

            def work(progress):
                return self.scaffolder.merge_version(
                    name, data["source"], data["target"],
                    strategy=data["strategy"], progress=progress)

            def done(_result: Any) -> None:
                self.refresh_current()
                self._refresh_projects()
                self.toast_ok(f"Merged {data['source']} into {data['target']}")

            self.runner.run(work, scope="action", label="Merging\u2026",
                            on_done=done, on_error=self._handle_conflict)

        def on_pr(data: dict) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.create_pull_request(
                    name, data["source"], data["target"], data["title"],
                    data["body"])

            def done(result: Any) -> None:
                url = (result or {}).get("html_url", "")
                if url and self._confirm("Pull request created",
                                         f"Open {url}?", "Open in browser"):
                    self.scaffolder.open_url(url)
                self.toast_ok("Pull request opened")

            self.runner.run(work, scope="action", label="Opening pull request\u2026",
                            on_done=done, on_error=self._handle_conflict)

        dialog = MergeDialog(self, self.theme, project, on_merge, on_pr)
        dialog.exec()

    def _handle_conflict(self, exc: BaseException) -> None:
        if not isinstance(exc, ConflictError):
            self._on_task_failed(exc)
            return
        name = self.current_project

        dialog = ConflictedDialog(
            self, self.theme, exc.files,
            on_open=lambda path: self._open_file(path),
            on_abort=lambda: self._abort_merge(name))
        dialog.exec()
        self.refresh_changes()

    def _abort_merge(self, name: str) -> None:
        try:
            self.scaffolder.git(name).abort_merge()
            self.refresh_current()
            self.toast_ok("Merge aborted")
        except ScaffolderError as exc:
            self._error(exc)

    def show_stashes(self) -> None:
        name = self._require_project()
        if not name:
            return
        try:
            stashes = self.scaffolder.list_stashes(name)
        except ScaffolderError as exc:
            self._error(exc)
            return

        def on_apply() -> None:
            def work(_progress):
                return self.scaffolder.apply_stash(name)

            def done(_result: Any) -> None:
                self.refresh_current()
                self.toast_ok("Stash applied")

            self.runner.run(work, scope="action", label="Applying stash\u2026",
                            on_done=done)

        def on_stash(message: str) -> None:
            def work(_progress):
                return self.scaffolder.stash_changes(name, message)

            def done(_result: Any) -> None:
                self.refresh_current()
                self.toast_ok("Changes stashed")

            self.runner.run(work, scope="action", label="Stashing\u2026",
                            on_done=done)

        StashDialog(self, self.theme, name, stashes, on_apply, on_stash).exec()

    # ============================================================== remote

    def push_project(self, label: str | None = None) -> None:
        name = self._require_project()
        if not name:
            return
        version = label if isinstance(label, str) and label else None

        def work(progress):
            return self.scaffolder.push(name, version, progress=progress)

        def done(_result: Any) -> None:
            self.refresh_current()
            self._refresh_projects()
            self.toast_ok("Pushed")

        self.runner.run(work, scope="action", label="Pushing\u2026", on_done=done)

    def pull_project(self, label: str | None = None) -> None:
        name = self._require_project()
        if not name:
            return
        version = label if isinstance(label, str) and label else None

        def work(progress):
            return self.scaffolder.pull(name, version, strategy="ff-only",
                                        progress=progress)

        def done(_result: Any) -> None:
            self.refresh_current()
            self._refresh_projects()
            self.toast_ok("Pulled")

        def failed(exc: BaseException) -> None:
            if isinstance(exc, DivergedError):
                self._offer_diverged(name, version or "", exc)
            elif isinstance(exc, ConflictError):
                self._handle_conflict(exc)
            else:
                self._on_task_failed(exc)

        self.runner.run(work, scope="action", label="Pulling\u2026", on_done=done,
                        on_error=failed)

    def _offer_diverged(self, name: str, label: str,
                        exc: DivergedError) -> None:
        dialog = AppDialog(
            self, "Branches have diverged", self.theme,
            subtitle=f"Local is {exc.ahead} ahead and {exc.behind} behind.",
            width=480)

        def resolve(strategy: str) -> None:
            _close_dialog()

            def work(progress):
                return self.scaffolder.pull(name, label or None,
                                            strategy=strategy, progress=progress)

            def done(_result: Any) -> None:
                self.refresh_current()
                self._refresh_projects()
                self.toast_ok(f"Reconciled using {strategy}")

            def failed(e: BaseException) -> None:
                if isinstance(e, ConflictError):
                    self._handle_conflict(e)
                else:
                    self._on_task_failed(e)

            self.runner.run(work, scope="action", label=f"{strategy.title()}…",
                            on_done=done, on_error=failed)

        dialog.add_button("Merge remote into local", primary=True,
                          on_click=lambda: resolve("merge"))
        dialog.add_button("Rebase local onto remote",
                          on_click=lambda: resolve("rebase"))
        dialog.add_button("Reset to remote (discard local commits)", danger=True,
                          on_click=lambda: self._reset_to_remote(name, label))
        dialog.add_cancel("Cancel")
        dialog.exec()

    def _reset_to_remote(self, name: str, label: str) -> None:
        if not self._confirm(
                "Reset to remote?",
                "Local commits not on the remote will be discarded.", "Reset",
                danger=True):
            return
        version = label or (self.memory.get_project(name) or {}).get(
            "current_version", "")

        def work(_progress):
            return self.scaffolder.reset_to_remote(name, version)

        def done(_result: Any) -> None:
            self.refresh_current()
            self.toast_ok("Reset to remote")

        self.runner.run(work, scope="action", label="Resetting\u2026",
                        on_done=done)

    def fetch_project(self) -> None:
        name = self._require_project()
        if not name:
            return

        def work(_progress):
            return self.scaffolder.fetch(name)

        def done(_result: Any) -> None:
            self._on_refresh_all()
            self.toast_ok("Fetched from GitHub")

        self.runner.run(work, scope="background", label="Fetching\u2026",
                        on_done=done)

    def force_push(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}
        label = project.get("current_version") or ""

        def submit(chosen: str, _data: dict) -> None:
            _close_dialog()

            def work(progress):
                return self.scaffolder.force_push(name, chosen, progress=progress)

            def done(_result: Any) -> None:
                self.refresh_current()
                self.toast_ok("Force pushed")

            self.runner.run(work, scope="action", label="Force pushing\u2026",
                            on_done=done)

        ForcePushDialog(self, self.theme, label, submit).exec()

    def create_repository(self) -> None:
        name = self._require_project()
        if not name:
            return
        accounts = [a["username"] for a in self.memory.list_accounts()]
        if not accounts:
            self._info("No accounts", "Add a GitHub account first.")
            return
        project = self.memory.get_project(name) or {}
        from PySide6.QtWidgets import QComboBox

        dialog = AppDialog(self, f"Create repository for {name}", self.theme,
                           width=480)
        account = QComboBox()
        account.addItems(accounts)
        dialog.body.addWidget(QLabel("Account"))
        dialog.body.addWidget(account)
        visibility = QComboBox()
        visibility.addItems(["private", "public"])
        visibility.setCurrentText(project.get("visibility", "private"))
        dialog.body.addWidget(QLabel("Visibility"))
        dialog.body.addWidget(visibility)

        def submit() -> None:
            _close_dialog()
            chosen = account.currentText()
            vis = visibility.currentText()

            def work(progress):
                return self.scaffolder.create_repository_for_project(
                    name, chosen, vis, progress=progress)

            def done(_result: Any) -> None:
                self.refresh_current()
                self._refresh_projects()
                self.toast_ok("Repository created and pushed")

            self.runner.run(work, scope="action", label="Creating repository\u2026",
                            on_done=done)

        dialog.add_cancel()
        dialog.add_primary("Create repository", submit)
        dialog.exec()

    def change_visibility(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}
        current = project.get("visibility", "private")
        target = "public" if current == "private" else "private"
        message = f"Make '{name}' {target}?"
        if target == "public":
            message += ("\n\nAnyone will be able to see the repository. Forks "
                        "and clones that already exist cannot be recalled.")
            if self.memory.get_setting("confirm_make_public", True):
                if not self._confirm("Make repository public?", message,
                                     "Make public", danger=True):
                    return
        if not self._confirm("Change visibility", message, f"Make {target}"):
            return

        def work(_progress):
            return self.scaffolder.change_visibility(name, target)

        def done(_result: Any) -> None:
            self.refresh_project()
            self._refresh_projects()
            self.toast_ok(f"Now {target}")

        self.runner.run(work, scope="action", label="Updating visibility\u2026",
                        on_done=done)

    def fix_identity(self) -> None:
        name = self._require_project()
        if not name:
            return
        if not self._confirm(
                "Use account identity for commits",
                "Set the git identity for this project to the connected "
                "account? This affects future commits only.", "Apply"):
            return
        try:
            info = self.scaffolder.fix_git_identity(name)
            self.toast_ok(f"Identity set to {info['name']} <{info['email']}>")
        except ScaffolderError as exc:
            self._error(exc)

    def open_on_github(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}
        url = project.get("repo_url")
        if not url:
            self._info("No repository", "This project has no GitHub repository.")
            return
        self.scaffolder.open_url(url)

    def create_release(self) -> None:
        name = self._require_project()
        if not name:
            return
        project = self.memory.get_project(name) or {}
        if not project.get("repo_url"):
            self._info("No repository", "Create a GitHub repository first.")
            return
        tags = list(project.get("versions", {}))

        def submit(data: dict) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.create_release(
                    name, data["tag"], data["name"], data["body"], data["draft"])

            def done(result: Any) -> None:
                url = (result or {}).get("html_url", "")
                self.toast_ok(f"Release {data['tag']} created")
                if url and self._confirm("Release created", f"Open {url}?",
                                         "Open in browser"):
                    self.scaffolder.open_url(url)

            self.runner.run(work, scope="action", label="Creating release\u2026",
                            on_done=done)

        ReleaseDialog(self, self.theme, project, tags, submit).exec()

    def _show_commit(self, sha: str) -> None:
        name = self.current_project
        if not name or not sha:
            return
        try:
            self.workspace.history.show_detail(
                self.scaffolder.show_commit(name, sha))
        except ScaffolderError as exc:
            self.workspace.history.show_detail(str(exc))

    # ============================================================== runtime

    def run_task(self) -> None:
        name = self._require_project()
        if not name:
            return
        try:
            tasks = self.scaffolder.detect_tasks(name)
        except ScaffolderError as exc:
            self._error(exc)
            return
        if not tasks:
            self._info("No tasks found",
                       "No known test or lint task was detected in this project.")
            return

        def on_run(command: list[str], emit: Callable[[str], None]) -> None:
            def work(progress):
                code = self.scaffolder.run_task(name, command,
                                                lambda line: progress(line))
                return code

            def done(code: Any) -> None:
                dialog.append(f"\n[exit code {code}]")
                dialog.finish()

            def failed(exc: BaseException) -> None:
                dialog.append(f"\n[error] {error_message(exc)}")
                dialog.finish()

            self.runner.run(work, scope="action", label="Running task\u2026",
                            on_done=done, on_error=failed,
                            on_progress=dialog.append)

        dialog = TaskRunDialog(self, self.theme, tasks, on_run)
        dialog.exec()

    def setup_env(self) -> None:
        name = self._require_project()
        if not name:
            return
        try:
            kind = self.scaffolder.detect_env_type(name)
        except ScaffolderError as exc:
            self._error(exc)
            return
        if kind == "none":
            self._info("No project type detected",
                       "Could not detect a Python, Node, Rust, Go, .NET or Ruby "
                       "project in this folder.")
            return
        if not self._confirm("Set up environment",
                             f"Create the {kind} environment for '{name}'? "
                             "This can take a while.", "Set up"):
            return
        self._run_env(name)

    def _run_env(self, name: str) -> None:
        dialog = AppDialog(self, "Setting up environment", self.theme,
                           subtitle="Installing dependencies\u2026", width=640)
        from PySide6.QtWidgets import QPlainTextEdit

        log = QPlainTextEdit()
        log.setObjectName("Log")
        log.setReadOnly(True)
        log.setMinimumHeight(260)
        dialog.body.addWidget(log)
        close = dialog.add_cancel("Run in background")

        def work(progress):
            return self.scaffolder.setup_env(name, progress)

        def done(kind: Any) -> None:
            log.appendPlainText(f"\nEnvironment ready ({kind}).")
            dialog.set_subtitle("Done")
            close.setText("Close")
            self.refresh_project()

        def failed(exc: BaseException) -> None:
            hint = error_hint(exc)
            log.appendPlainText(f"\n[error] {error_message(exc)}")
            if hint:
                log.appendPlainText(f"Hint: {hint}")
            close.setText("Close")

        def on_progress(message: str) -> None:
            log.appendPlainText(str(message))
            log.verticalScrollBar().setValue(log.verticalScrollBar().maximum())

        self.runner.run(work, scope="action", label="Setting up environment\u2026",
                        on_done=done, on_error=failed, on_progress=on_progress)
        dialog.exec()

    # ============================================================= accounts

    def add_account(self) -> None:
        def submit(token: str) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.add_account(token)

            def done(account: Any) -> None:
                self._refresh_accounts()
                self.toast_ok(f"Added '{account.get('username', '')}'")

            self.runner.run(work, scope="action", label="Verifying token\u2026",
                            on_done=done)

        def device_flow(parent) -> None:
            _close_dialog()
            self._start_device_flow(parent)

        AddAccountDialog(self, self.theme, submit, device_flow).exec()

    def _start_device_flow(self, parent) -> None:
        client_id = self.memory.get_setting("oauth_client_id", "") or ""
        if not client_id:
            client_id = self._ask_client_id(parent)
            if not client_id:
                return
        flow = DeviceFlow(client_id)
        try:
            code = flow.start()
        except ScaffolderError as exc:
            self._error(exc)
            return

        def poll(done, failed) -> None:
            try:
                result = flow.poll_once(code["device_code"])
            except ScaffolderError as exc:
                failed(exc)
                return
            if result and result.get("token"):
                done(result["token"])
            elif result and result.get("slow_down"):
                failed(ScaffolderError("Slow down and try again."))

        dialog = DeviceFlowDialog(self, self.theme, code, poll)
        if dialog.exec() == AppDialog.DialogCode.Accepted and getattr(
                dialog, "token", ""):
            token = dialog.token

            def work(_progress):
                return self.scaffolder.add_account(token)

            def done(account: Any) -> None:
                self._refresh_accounts()
                self.toast_ok(f"Signed in as '{account.get('username', '')}'")

            self.runner.run(work, scope="action", label="Verifying token\u2026",
                            on_done=done)

    def _ask_client_id(self, parent) -> str:
        from PySide6.QtWidgets import QLineEdit

        dialog = AppDialog(parent, "GitHub OAuth client ID", self.theme,
                           subtitle="Device sign-in needs an OAuth app client "
                                    "ID. You can create one at "
                                    "github.com/settings/developers.")
        field = QLineEdit()
        field.setPlaceholderText("Iv1.\u2026")
        dialog.body.addWidget(field)
        dialog.add_cancel()
        dialog.add_primary("Continue", dialog.accept)
        if dialog.exec() != AppDialog.DialogCode.Accepted:
            return ""
        value = field.text().strip()
        if value:
            self.memory.set_setting("oauth_client_id", value)
        return value

    def manage_accounts(self) -> None:
        def replace(account: str, token: str) -> None:
            def work(_progress):
                return self.scaffolder.replace_account_token(account, token)

            def done(_result: Any) -> None:
                self._refresh_accounts()
                self.toast_ok(f"Token replaced for '{account}'")

            self.runner.run(work, scope="action", label="Verifying token\u2026",
                            on_done=done)

        def remove(account: str) -> None:
            try:
                self.scaffolder.remove_account(account)
                self._refresh_accounts()
                self.toast_ok(f"Removed '{account}'")
            except ScaffolderError as exc:
                self._error(exc)

        def verify(account: str) -> None:
            def work(_progress):
                return self.scaffolder.check_token(account)

            def done(ok: Any) -> None:
                self._refresh_accounts()
                self.toast_ok(f"'{account}' token is valid" if ok
                              else f"'{account}' token was rejected")

            self.runner.run(work, scope="action", label="Checking token\u2026",
                            on_done=done)

        ManageAccountsDialog(
            self, self.theme, self.memory.list_accounts(),
            has_token=self.services.credentials.has,
            on_replace=replace, on_remove=remove, on_verify=verify,
            on_add=self.add_account,
            backend_name=self.services.credentials.backend_name).exec()
        self._refresh_accounts()

    def _prompt_token(self, account: str) -> None:
        def submit(token: str) -> None:
            def work(_progress):
                return self.scaffolder.replace_account_token(account, token)

            def done(_result: Any) -> None:
                self.toast_ok(f"Token updated for '{account}'")

            self.runner.run(work, scope="action", label="Verifying token\u2026",
                            on_done=done)

        dialog = TokenUpdateDialog(self, account, self.theme, submit)
        dialog.exec()
        if dialog.result() == AppDialog.DialogCode.Accepted:
            return

    # ============================================================ templates

    def new_template(self) -> None:
        def submit(data: dict) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.create_template_from_folder(
                    data["name"], data["path"], data["description"],
                    data["ignore"], data["placeholders"])

            def done(template: Any) -> None:
                self.toast_ok(f"Template '{data['name']}' created")

            self.runner.run(work, scope="action", label="Creating template\u2026",
                            on_done=done)

        dialog = TemplateDialog(self, self.theme, None, submit,
                                on_preview=self.scaffolder.preview_template)
        dialog.exec()

    def edit_template(self, name: str) -> None:
        template = self.memory.get_template(name)
        if not template:
            return

        def submit(data: dict) -> None:
            _close_dialog()
            try:
                if data["is_new"]:
                    self.scaffolder.create_template_from_folder(
                        data["name"], data["path"], data["description"],
                        data["ignore"], data["placeholders"])
                else:
                    if data["name"] != name:
                        self.memory.rename_template(name, data["name"])
                    self.memory.update_template(
                        data["name"], path=data["path"],
                        description=data["description"], ignore=data["ignore"],
                        placeholders=data["placeholders"])
                self.toast_ok(f"Template '{data['name']}' saved")
            except ScaffolderError as exc:
                self._error(exc)

        def export() -> None:
            from PySide6.QtWidgets import QFileDialog

            target = QFileDialog.getSaveFileName(
                self, "Export manifest", str(Path.home() / f"{name}.scaffold.json"),
                "Scaffolder manifest (*.json)")[0]
            if target:
                try:
                    self.scaffolder.export_template_manifest(name, target)
                    self.toast_ok("Manifest exported")
                except ScaffolderError as exc:
                    self._error(exc)

        dialog = TemplateDialog(self, self.theme, template, submit,
                                on_preview=self.scaffolder.preview_template,
                                on_export=export)
        dialog.add_button("Delete template\u2026", left=True, danger=True,
                          on_click=lambda: self._delete_template(name, dialog))
        dialog.exec()

    def _delete_template(self, name: str, parent) -> None:
        if not self._confirm("Delete template?",
                             f"Remove the template '{name}' from the app? "
                             "The folder on disk is kept.", "Delete", danger=True):
            return
        try:
            self.memory.remove_template(name)
            parent.accept()
            self.toast_ok(f"Template '{name}' removed")
        except ScaffolderError as exc:
            self._error(exc)

    def import_template_manifest(self) -> None:
        def submit(manifest: str, source: str) -> None:
            _close_dialog()

            def work(_progress):
                return self.scaffolder.import_template_manifest(
                    manifest, source or None)

            def done(template: Any) -> None:
                self.toast_ok(f"Imported template '{template.get('name', '')}'")

            self.runner.run(work, scope="action", label="Importing template\u2026",
                            on_done=done)

        ManifestImportDialog(self, self.theme, submit).exec()

    # ============================================================== settings

    def open_settings(self) -> None:
        from ..editor import detect_available_editors

        storage = (f"Tokens: {self.services.credentials.backend_name}. "
                   f"Data: {paths.data_dir(self.services.root)}")

        def submit(values: dict) -> None:
            _close_dialog()
            self.memory.set_settings(values)
            self.theme = build_theme(values.get("theme", "system"))
            self._apply_theme()
            self._configure_auto_refresh()
            self._refresh_projects()
            self.toast_ok("Settings saved")
            self._rebuild_after_theme()

        def export() -> None:
            templates = self.memory.list_templates()
            if not templates:
                self._info("No templates", "Create a template first.")
                return
            from PySide6.QtWidgets import QFileDialog

            target = QFileDialog.getSaveFileName(
                self, "Export manifest", str(Path.home() / "template.scaffold.json"),
                "Scaffolder manifest (*.json)")[0]
            if target:
                try:
                    self.scaffolder.export_template_manifest(
                        templates[0]["name"], target)
                    self.toast_ok("Manifest exported")
                except ScaffolderError as exc:
                    self._error(exc)

        dialog = SettingsDialog(
            self, self.theme, self.memory.snapshot()["settings"],
            detect_available_editors(), storage, submit,
            on_export=export, on_import=self.import_template_manifest,
            on_open_logs=lambda: paths.os_open_path(
                paths.data_dir(self.services.root)),
            on_open_trash=self.open_trash)
        dialog.exec()

    def _rebuild_after_theme(self) -> None:
        self.toast = ToastHost(self, self.theme)
        self.workspace.theme = self.theme
        ui_platform.restyle_tree(self)

    def toggle_theme(self) -> None:
        next_theme = "light" if self.theme.is_dark else "dark"
        self.memory.set_setting("theme", next_theme)
        self.theme = build_theme(next_theme)
        self._apply_theme()
        self._rebuild_after_theme()
        self.set_status(f"Theme: {next_theme}")

    # ================================================================ misc

    def open_palette(self) -> None:
        commands = self._palette_commands()
        names = [p["name"] for p in self.memory.list_projects()]
        palette = CommandPalette(self, self.theme, commands, names,
                                 on_open_project=self._palette_open_project)
        palette.exec()

    def _palette_open_project(self, name: str) -> None:
        self.sidebar.search.clear()
        self._refresh_projects()
        self.sidebar.select(name)

    def _palette_commands(self) -> list[Command]:
        def cmd(title: str, category: str, run: Callable[[], None],
                shortcut: str = "", keywords: str = "") -> Command:
            return Command(title, category, run, shortcut, keywords)

        return [
            cmd("New project", "Create", self.new_project, "Ctrl+N"),
            cmd("Clone from GitHub", "Create", self.clone_project, "Ctrl+Shift+N"),
            cmd("Add existing folder", "Create", self.import_folder),
            cmd("New template", "Create", self.new_template),
            cmd("Commit changes", "Changes", self.commit_project, "Ctrl+Shift+C",
                "save"),
            cmd("Refresh changes", "Changes", self.refresh_changes),
            cmd("Stage all", "Changes", self.stage_all),
            cmd("Discard all changes", "Changes", self.discard_all, "discard"),
            cmd("Pull", "Remote", self.pull_project, "Ctrl+Shift+L"),
            cmd("Push", "Remote", self.push_project, "Ctrl+Shift+P"),
            cmd("Fetch", "Remote", self.fetch_project),
            cmd("Force push", "Remote", self.force_push),
            cmd("New version", "Versions", self.new_version),
            cmd("Merge versions", "Versions", self.merge_versions),
            cmd("Stashes", "Versions", self.show_stashes),
            cmd("Switch version", "Versions", self.switch_version_dialog),
            cmd("Open folder", "Open", self.open_folder, "Ctrl+O"),
            cmd("Open in editor", "Open", self.open_in_editor, "Ctrl+E"),
            cmd("Open terminal", "Open", self.open_terminal),
            cmd("Open repository on GitHub", "Open", self.open_on_github),
            cmd("Set up environment", "Tools", self.setup_env),
            cmd("Run a task", "Tools", self.run_task),
            cmd("Create repository", "GitHub", self.create_repository),
            cmd("Change visibility", "GitHub", self.change_visibility),
            cmd("Create release", "GitHub", self.create_release),
            cmd("Add GitHub account", "GitHub", self.add_account),
            cmd("Manage accounts", "GitHub", self.manage_accounts),
            cmd("Rename project", "Project", self.rename_project),
            cmd("Relink folder", "Project", self.relink_project),
            cmd("Set group", "Project", self.set_group),
            cmd("Project notes", "Project", self.edit_notes),
            cmd("Delete project", "Project", self.delete_project),
            cmd("Toggle theme", "View", self.toggle_theme, "Ctrl+Shift+T",
                "dark light appearance"),
            cmd("Settings", "View", self.open_settings, "Ctrl+,"),
            cmd("Trash", "View", self.open_trash, "", "recover restore"),
            cmd("Error log", "View", self.open_error_log, "", "errors problems"),
            cmd("Keyboard shortcuts", "Help", self.show_shortcuts, "Ctrl+/"),
        ]

    def open_trash(self) -> None:
        entries = self.scaffolder.list_trash()

        def restore(stamp: str) -> None:
            try:
                project = self.scaffolder.restore_from_trash(stamp)
                self._refresh_projects()
                self.toast_ok(f"Restored '{project.get('name', '')}'")
            except ScaffolderError as exc:
                self._error(exc)

        def purge(stamp: str) -> None:
            self.scaffolder.purge_trash_entry(stamp)
            self.toast_ok("Deleted from trash")

        def empty() -> None:
            count = self.scaffolder.empty_trash()
            self.toast_ok(f"Emptied the trash ({count} entries)")

        TrashDialog(self, self.theme, entries, restore, purge, empty).exec()

    def open_error_log(self) -> None:
        errors = self.memory.get_errors()
        lines = []
        for error in reversed(errors):
            lines.append(f"[{error.get('at', '')}] {error.get('context', '')}")
            lines.append(f"    {error.get('message', '')}")
            lines.append("")
        dialog = InfoDialog(self, "Error log",
                            f"{len(errors)} recorded message(s).", self.theme,
                            detail="\n".join(lines) or "(no errors)")
        dialog.setMinimumWidth(720)
        dialog.add_button("Clear log", left=True, danger=True,
                          on_click=lambda: self._clear_errors(dialog))
        dialog.exec()

    def _clear_errors(self, dialog) -> None:
        if not self._confirm("Clear the error log?",
                             "All recorded messages will be removed.", "Clear"):
            return
        self.memory.clear_errors()
        _close_dialog()
        self.toast_ok("Error log cleared")

    def show_shortcuts(self) -> None:
        rows = [
            ("Ctrl+N", "New project"),
            ("Ctrl+Shift+N", "Clone from GitHub"),
            ("Ctrl+K", "Command palette"),
            ("Ctrl+F", "Focus project search"),
            ("Ctrl+Shift+C", "Commit"),
            ("Ctrl+Shift+P", "Push"),
            ("Ctrl+Shift+L", "Pull"),
            ("Ctrl+E", "Open in editor"),
            ("Ctrl+O", "Open folder"),
            ("Ctrl+,", "Settings"),
            ("Ctrl+Shift+T", "Toggle theme"),
            ("F5", "Refresh"),
            ("Ctrl+/", "This list"),
            ("Esc", "Close dialog"),
            ("Enter", "Confirm dialog"),
        ]
        text = "\n".join(f"{keys:<16}{label}" for keys, label in rows)
        InfoDialog(self, "Keyboard shortcuts", "Every action is reachable "
                    "without the mouse.", self.theme, detail=text).exec()

    def about(self) -> None:
        self._info("About", f"{__app_name__} {__version__}\n\n"
                   "A desktop project scaffolder with a git-backed version "
                   "model.\n\n"
                   f"Data: {paths.data_dir(self.services.root)}\n"
                   f"UI: PySide6, {sys.platform}")

    def quit_confirm(self) -> None:
        if self.runner.any_busy():
            if not self._confirm("Quit while an operation is running?",
                                 "The running operation will be interrupted.",
                                 "Quit", danger=True):
                return
        self.close()

    def focus_search(self) -> None:
        self.sidebar.search.setFocus()
        self.sidebar.search.selectAll()

    def _on_sidebar_action(self, action: str, name: str, _pos) -> None:
        if not name:
            return
        handlers = {
            "editor": lambda: self._sidebar_open_editor(name),
            "open_folder": lambda: self._sidebar_open_folder(name),
            "pin": lambda: self._toggle_pin(name),
            "group": lambda: (self.select_project(name), self.set_group()),
            "relink": lambda: (self.select_project(name), self.relink_project()),
            "rename": lambda: (self.select_project(name), self.rename_project()),
            "delete": lambda: (self.select_project(name), self.delete_project()),
        }
        handler = handlers.get(action)
        if handler:
            handler()

    def _sidebar_open_editor(self, name: str) -> None:
        self._guard_folder(name, lambda: self.scaffolder.open_in_editor(name))

    def _sidebar_open_folder(self, name: str) -> None:
        self._guard_folder(name, lambda: self.scaffolder.open_folder(name))

    # ================================================================ events

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self.toast.theme = self.theme

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if getattr(self, "_notes_timer", None) is not None:
            self.toast._layout()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._save_notes()
        self.state.capture_geometry(self)
        self.state.capture_splitter(self.splitter, "splitter")
        self.state.save()
        self.runner.wait_for(3000)
        super().closeEvent(event)

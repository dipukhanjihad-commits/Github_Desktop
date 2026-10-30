"""Project lifecycle dialogs: create, clone, import, relink, rename, delete.

Every dialog validates inline before submitting (S23) and routes long work
through the caller's task runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...errors import ScaffolderError, ValidationError
from ...project_memory import validate_folder_name
from ..theme import SPACING, Theme
from ..widgets import Badge, SearchField, SectionLabel
from .base import AppDialog

Submit = Callable[[str, dict], None]


def browse_folder(parent: QWidget | None, title: str) -> str:
    path = QFileDialog.getExistingDirectory(parent, title, str(Path.home()),
                                            QFileDialog.Option.ShowDirsOnly)
    return path or ""


class NewProjectDialog(AppDialog):
    """Create a project from a template with live name/path validation."""

    def __init__(self, parent, theme: Theme, *,
                 templates: list[dict],
                 accounts: list[str],
                 default_path: str,
                 default_visibility: str = "private",
                 on_submit: Submit,
                 check_name: Callable[[str, str | None], dict] | None = None,
                 on_preview: Callable[[str], dict] | None = None) -> None:
        super().__init__(parent, "New project", theme,
                         subtitle="Pick a template and choose where it lives.",
                         width=640)
        self.templates = templates
        self.accounts = accounts
        self.default_path = default_path
        self._on_submit = on_submit
        self._check_name = check_name
        self._on_preview = on_preview
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(400)
        self._check_timer.timeout.connect(self._run_name_check)

        self.name = QLineEdit()
        self.name.setPlaceholderText("my-project")
        self.name.textChanged.connect(self._on_name_changed)

        self.template = QComboBox()
        for template in templates:
            self.template.addItem(template["name"], template["name"])
        self.template.currentIndexChanged.connect(self._rebuild_placeholders)

        self.dest = QLineEdit(default_path)
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)

        self.visibility_private = QRadioButton("Private")
        self.visibility_public = QRadioButton("Public")
        default_private = (default_visibility or "private") == "private"
        self.visibility_private.setChecked(default_private)
        self.visibility_public.setChecked(not default_private)

        self.account = QComboBox()
        self.account.addItem("(no GitHub repository)", "")
        for name in accounts:
            self.account.addItem(name, name)
        self.account.currentIndexChanged.connect(self._sync_account_state)

        self.create_repo = QCheckBox("Create the GitHub repository and push now")
        self.create_repo.setChecked(bool(accounts))
        self.create_repo.toggled.connect(self._sync_repo_checkbox)

        self.placeholder_box = QVBoxLayout()
        self.placeholder_box.setSpacing(SPACING["xs"])
        self.placeholder_inputs: dict[str, QLineEdit] = {}

        from ..widgets import FormBuilder

        form = FormBuilder(self.body, theme)
        form.add_row("Name", self.name)
        self.name_feedback = form.add_field_error()
        form.add_row("Template", self.template)
        form.add_row("Destination", self.dest, browse)
        row = QHBoxLayout()
        row.addWidget(self.visibility_private)
        row.addWidget(self.visibility_public)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        form.add_row("Visibility", holder)
        form.add_row("Account", self.account)
        form.add_full(self.create_repo)
        self.placeholder_label = SectionLabel("Placeholders", theme)
        self.placeholder_label.setVisible(False)
        form.add_full(self.placeholder_label)
        form.add_full(self._placeholder_host())

        preview = QPushButton("Preview files")
        preview.clicked.connect(self._preview)
        self.add_cancel("Cancel")
        self.add_primary("Create project", self._submit)

        self._rebuild_placeholders()
        self._sync_account_state()

    # -------------------------------------------------------------- helpers

    def _placeholder_host(self) -> QWidget:
        host = QWidget()
        host.setLayout(self.placeholder_box)
        return host

    def _browse(self) -> None:
        parent = browse_folder(self, "Choose where to create the project")
        if parent:
            name = self.name.text().strip() or "project"
            self.dest.setText(str(Path(parent) / name))

    def _on_name_changed(self, text: str) -> None:
        self._sync_placeholder_values()
        if text.strip() and self.default_path:
            self.dest.setText(str(Path(self.default_path) / text.strip()))
        self._check_timer.start()
        self._validate_inline()

    def _rebuild_placeholders(self) -> None:
        while self.placeholder_box.count():
            item = self.placeholder_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.placeholder_inputs.clear()
        current = self.template.currentData()
        template = next((t for t in self.templates if t["name"] == current), None)
        keys = list((template or {}).get("placeholders", {})) if template else []
        self.placeholder_label.setVisible(bool(keys))
        for key in keys:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(f"{{{{ {key} }}}}")
            label.setObjectName("Mono")
            label.setFixedWidth(140)
            field = QLineEdit()
            if key == "project_name":
                field.setPlaceholderText("(uses the project name)")
                field.textChanged.connect(
                    lambda _t, f=field: self._mirror_project_name(f))
                self.project_name_field = field
            self.placeholder_inputs[key] = field
            layout.addWidget(label)
            layout.addWidget(field, 1)
            self.placeholder_box.addWidget(row)

    def _mirror_project_name(self, field: QLineEdit) -> None:
        field.setPlaceholderText(self.name.text().strip() or "(uses the project name)")

    def _sync_placeholder_values(self) -> None:
        """Keep the project_name placeholder hint in sync with the name field."""
        field = getattr(self, "project_name_field", None)
        if field is not None:
            field.setPlaceholderText(self.name.text().strip()
                                     or "(uses the project name)")

    def _sync_account_state(self) -> None:
        has_account = bool(self.account.currentData())
        self.create_repo.setEnabled(has_account)
        if not has_account:
            self.create_repo.setChecked(False)

    def _sync_repo_checkbox(self, checked: bool) -> None:
        self.visibility_private.setEnabled(checked)
        self.visibility_public.setEnabled(checked)

    def _run_name_check(self) -> None:
        if self._check_name is None:
            return
        name = self.name.text().strip()
        if not name:
            return
        account = self.account.currentData() or None

        def done(result: Any) -> None:
            if not isinstance(result, dict):
                return
            if result.get("local_free") is False:
                self._name_error = result.get("message", "That name is taken.")
                self.name_feedback.setText(self._name_error)
                self.name_feedback.setVisible(True)
                self.name.setProperty("invalid", True)
                self._restyle(self.name)
            elif result.get("remote_free") is False:
                self.name_feedback.setText(result.get("message", ""))
                self.name_feedback.setVisible(True)
            else:
                self.name_feedback.setVisible(False)
                self.name.setProperty("invalid", False)
                self._restyle(self.name)

        self._check_name_async(name, account, done)

    def _check_name_async(self, name: str, account: str | None, done) -> None:
        # Set by the host; falls back to synchronous checking.
        if getattr(self, "_async_check", None):
            self._async_check(name, account, done)
            return
        try:
            done(self._check_name(name, account) if self._check_name else {})
        except Exception:
            pass

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _preview(self) -> None:
        if self._on_preview is None:
            return
        current = self.template.currentData()
        if not current:
            return
        try:
            data = self._on_preview(current)
        except ScaffolderError as exc:
            self._show_inline_error(str(exc))
            return
        _show_preview(self, self.theme, current, data)

    def _validate_inline(self) -> bool:
        name = self.name.text().strip()
        try:
            if name:
                validate_folder_name(name, "Project name")
        except ValidationError as exc:
            self.name_feedback.setText(str(exc))
            self.name_feedback.setVisible(True)
            return False
        if not self.name_feedback.text() or name == "":
            self.name_feedback.setVisible(False)
        return True

    def payload(self) -> dict:
        values = {}
        for key, field in self.placeholder_inputs.items():
            text = field.text().strip()
            if key == "project_name":
                text = text or self.name.text().strip()
            values[key] = text
        return {
            "name": self.name.text().strip(),
            "template": self.template.currentData(),
            "dest": self.dest.text().strip(),
            "visibility": "private" if self.visibility_private.isChecked()
            else "public",
            "account": self.account.currentData() or None,
            "create_repo": self.create_repo.isChecked(),
            "placeholders": values,
        }

    def _submit(self) -> None:
        data = self.payload()
        if not data["name"]:
            self.name.setFocus()
            self._show_inline_error("Enter a project name.")
            return
        try:
            validate_folder_name(data["name"], "Project name")
        except ValidationError as exc:
            self._show_inline_error(str(exc))
            return
        if not data["template"]:
            self._show_inline_error("Choose a template.")
            return
        if not data["dest"]:
            self._show_inline_error("Choose a destination folder.")
            return
        self._on_submit(data["name"], data)


def _show_preview(parent, theme: Theme, name: str, data: dict) -> None:
    from PySide6.QtWidgets import QPlainTextEdit

    dialog = AppDialog(parent, f"Preview \u2014 {name}", theme,
                       subtitle=f"{data.get('path', '')}", width=620)
    viewer = QPlainTextEdit()
    viewer.setObjectName("Code")
    viewer.setReadOnly(True)
    files = data.get("files") or []
    if files:
        viewer.setPlainText("\n".join(files))
    else:
        viewer.setPlainText(data.get("error") or "(no files)")
    viewer.setMinimumHeight(360)
    dialog.body.addWidget(viewer)
    placeholders = ", ".join(data.get("placeholders", {})) or "\u2014"
    note = QLabel(f"Placeholders: {placeholders}")
    note.setObjectName("Subtle")
    dialog.body.addWidget(note)
    dialog.add_cancel("Close")
    dialog.exec()


class CloneDialog(AppDialog):
    """Clone from GitHub with a filterable repository picker."""

    def __init__(self, parent, theme: Theme, *, accounts: list[str],
                 default_path: str, on_submit: Submit,
                 load_repos: Callable[[str, Callable[[Any], None],
                                       Callable[[Exception], None]], None],
                 ) -> None:
        super().__init__(parent, "Clone from GitHub", theme,
                         subtitle="Pick a repository and choose where to put it.",
                         width=760)
        self._on_submit = on_submit
        self._repos: list[dict] = []
        self._load_repos = load_repos

        top = QHBoxLayout()
        top.addWidget(QLabel("Account"))
        self.account = QComboBox()
        for name in accounts:
            self.account.addItem(name, name)
        self.account.currentIndexChanged.connect(lambda _i: self.reload())
        top.addWidget(self.account)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.reload)
        top.addWidget(self.refresh_btn)
        self.status = QLabel("")
        self.status.setObjectName("Subtle")
        top.addWidget(self.status, 1)
        host = QWidget()
        host.setLayout(top)
        self.body.addWidget(host)

        self.filter = SearchField("Filter repositories\u2026", theme)
        self.filter.textChanged.connect(self._render)
        self.body.addWidget(self.filter)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Repository", "Owner", "Visibility"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(300)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.body.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        bottom.addWidget(self.name, 1)
        bottom.addWidget(QLabel("Folder"))
        self.dest = QLineEdit(default_path)
        bottom.addWidget(self.dest, 2)
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        bottom.addWidget(browse)
        host2 = QWidget()
        host2.setLayout(bottom)
        self.body.addWidget(host2)

        self.import_branches = QCheckBox(
            "Import every remote branch as a version")
        self.body.addWidget(self.import_branches)

        self.add_cancel("Cancel")
        self.clone_btn = self.add_primary("Clone", self._submit)
        self.clone_btn.setEnabled(False)
        self._default_path = default_path
        if accounts:
            self.reload()
        else:
            self.status.setText("Add a GitHub account first.")

    def reload(self) -> None:
        account = self.account.currentData()
        if not account:
            return
        self.status.setText("Loading\u2026")
        self.table.setRowCount(0)

        def done(result: Any) -> None:
            self._repos = list(result or [])
            self.status.setText(f"{len(self._repos)} repositories")
            self._render()

        def failed(exc: Exception) -> None:
            self.status.setText(f"Could not load: {exc}")

        self._load_repos(account, done, failed)

    def _render(self) -> None:
        query = self.filter.text().lower().strip()
        self.table.setRowCount(0)
        for repo in self._repos:
            if query and query not in repo.get("full_name", "").lower():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            name_item = QTableWidgetItem(repo.get("name", ""))
            name_item.setData(Qt.ItemDataRole.UserRole, repo)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(
                repo.get("owner", {}).get("login", "")))
            badge = Badge("Private" if repo.get("private") else "Public",
                          "neutral" if repo.get("private") else "primary",
                          self.theme)
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(6, 2, 6, 2)
            layout.addWidget(badge)
            layout.addStretch(1)
            self.table.setCellWidget(row, 2, holder)

    def _browse(self) -> None:
        parent = browse_folder(self, "Clone into\u2026")
        if parent:
            name = self.name.text().strip() or "repo"
            self.dest.setText(str(Path(parent) / name))

    def _on_select(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self.clone_btn.setEnabled(False)
            return
        repo = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        if not repo:
            return
        self.clone_btn.setEnabled(True)
        if not self.name.text().strip():
            self.name.setText(repo.get("name", ""))
            self.dest.setText(str(Path(self._default_path) / repo.get("name", "")))

    def _submit(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._show_inline_error("Select a repository to clone.")
            return
        repo = self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        name = self.name.text().strip() or repo.get("name", "")
        try:
            validate_folder_name(name, "Project name")
        except ValidationError as exc:
            self._show_inline_error(str(exc))
            return
        dest = self.dest.text().strip()
        if not dest:
            self._show_inline_error("Choose a destination folder.")
            return
        self._on_submit(name, {
            "url": repo.get("clone_url", ""),
            "name": name,
            "dest": dest,
            "account": self.account.currentData(),
            "import_branches": self.import_branches.isChecked(),
        })


class ImportFolderDialog(AppDialog):
    def __init__(self, parent, theme: Theme, *, on_submit: Submit) -> None:
        super().__init__(parent, "Add existing folder", theme,
                         subtitle="Point at a folder that already has a .git "
                                  "directory to manage it here.", width=560)
        self._on_submit = on_submit
        self.path = QLineEdit()
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        host = QWidget()
        host.setLayout(row)
        self.body.addWidget(QLabel("Repository folder"))
        self.body.addWidget(host)
        self.body.addWidget(QLabel("Project name"))
        self.name = QLineEdit()
        self.body.addWidget(self.name)
        self.path.textChanged.connect(self._suggest_name)
        self.add_cancel()
        self.add_primary("Add folder", self._submit)

    def _browse(self) -> None:
        chosen = browse_folder(self, "Choose a git repository folder")
        if chosen:
            self.path.setText(chosen)

    def _suggest_name(self, text: str) -> None:
        if not self.name.text().strip() and text:
            self.name.setText(Path(text).name)

    def _submit(self) -> None:
        path = self.path.text().strip()
        name = self.name.text().strip()
        if not path:
            self._show_inline_error("Choose a folder.")
            return
        try:
            validate_folder_name(name, "Project name")
        except ValidationError as exc:
            self._show_inline_error(str(exc))
            return
        self._on_submit(name, {"path": path, "name": name})


class RelinkDialog(AppDialog):
    def __init__(self, parent, theme: Theme, project: dict,
                 on_submit: Submit) -> None:
        super().__init__(parent, "Relink project folder", theme,
                         subtitle="The stored folder is missing. Point '"
                                  f"{project.get('name', '')}' at its new home.",
                         width=560)
        self._on_submit = on_submit
        self.body.addWidget(QLabel(f"Missing: {project.get('path', '')}"))
        self.path = QLineEdit()
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        host = QWidget()
        host.setLayout(row)
        self.body.addWidget(QLabel("New location"))
        self.body.addWidget(host)
        self.add_cancel()
        self.add_primary("Relink", self._submit)

    def _browse(self) -> None:
        chosen = browse_folder(self, "Choose the new project folder")
        if chosen:
            self.path.setText(chosen)

    def _submit(self) -> None:
        path = self.path.text().strip()
        if not path:
            self._show_inline_error("Choose the new location.")
            return
        self._on_submit("", {"path": path})


class RenameDialog(AppDialog):
    def __init__(self, parent, theme: Theme, project: dict, on_submit: Submit) -> None:
        super().__init__(parent, "Rename project", theme, width=480)
        self._on_submit = on_submit
        self.original = project.get("name", "")
        self.name = QLineEdit(self.original)
        self.body.addWidget(QLabel("New name"))
        self.body.addWidget(self.name)
        self.rename_remote = QCheckBox("Also rename the GitHub repository")
        self.rename_remote.setChecked(bool(project.get("repo_url")))
        self.rename_remote.setEnabled(bool(project.get("repo_url")))
        self.body.addWidget(self.rename_remote)
        self.folder_note = QLabel("The folder on disk is not moved.")
        self.folder_note.setObjectName("Subtle")
        self.body.addWidget(self.folder_note)
        self.add_cancel()
        self.add_primary("Rename", self._submit)

    def _submit(self) -> None:
        name = self.name.text().strip()
        if not name or name == self.original:
            self.reject()
            return
        try:
            validate_folder_name(name, "Project name")
        except ValidationError as exc:
            self._show_inline_error(str(exc))
            return
        self._on_submit(name, {"new": name,
                               "rename_remote": self.rename_remote.isChecked()})


class DeleteProjectDialog(AppDialog):
    """Delete with an explicit, mutually exclusive choice about the folder."""

    def __init__(self, parent, theme: Theme, project: dict, on_submit: Submit) -> None:
        super().__init__(parent, "Delete project", theme,
                         subtitle=f"This removes '{project.get('name', '')}' from "
                                  "the app.", width=540)
        self._on_submit = on_submit
        self.project = project

        self.body.addWidget(SectionLabel("What happens to the folder?", theme))
        self.trash = QRadioButton("Move it to the app trash (recoverable)")
        self.delete_files = QRadioButton("Delete it permanently")
        self.keep_files = QRadioButton("Leave it on disk")
        self.trash.setChecked(True)
        for radio in (self.trash, self.delete_files, self.keep_files):
            self.body.addWidget(radio)
        self.delete_files.toggled.connect(self._warn_permanent)
        self.permanent_warning = QLabel(
            "\u26a0 Permanent deletion cannot be undone.")
        self.permanent_warning.setObjectName("Warning")
        self.permanent_warning.setVisible(False)
        self.body.addWidget(self.permanent_warning)

        self.remove_remote = QCheckBox(
            "Also delete the GitHub repository (permanent)")
        self.remove_remote.setEnabled(bool(project.get("repo_url")))
        if not project.get("repo_url"):
            self.remove_remote.setText(
                "No GitHub repository is linked to this project")
        self.body.addWidget(self.remove_remote)
        remote_warning = QLabel("Deleting the GitHub repository cannot be undone.")
        remote_warning.setObjectName("Warning")
        self.body.addWidget(remote_warning)
        self.add_cancel()
        self.add_button("Delete project", danger=True, on_click=self._submit)

    def _warn_permanent(self, checked: bool) -> None:
        self.permanent_warning.setVisible(checked)

    def _submit(self) -> None:
        mode = ("trash" if self.trash.isChecked()
                else "delete" if self.delete_files.isChecked() else "keep")
        remove_remote = self.remove_remote.isChecked()
        message = f"Delete '{self.project.get('name', '')}'?"
        if mode == "delete":
            message += "\n\nThe folder will be deleted permanently."
        elif mode == "trash":
            message += "\n\nThe folder will be moved to the app trash."
        if remove_remote:
            message += "\n\nThe GitHub repository will also be deleted."
        if not self.confirm_destructive("Confirm deletion", message,
                                        confirm_text="Delete"):
            return
        self._on_submit("", {"mode": mode, "remove_remote": remove_remote})


class DeleteVersionDialog(AppDialog):
    def __init__(self, parent, theme: Theme, project: dict, label: str,
                 on_submit: Submit) -> None:
        version = project.get("versions", {}).get(label, {})
        children = [k for k, v in project.get("versions", {}).items()
                    if v.get("parent") == label]
        subtitle = f"Branch '{version.get('branch', '')}' will be removed."
        if children:
            subtitle += (f" {len(children)} child version(s) will be "
                         f"re-parented: {', '.join(children)}.")
        super().__init__(parent, f"Delete version {label}", theme,
                         subtitle=subtitle, width=520)
        self._on_submit = on_submit
        self.label = label
        is_current = project.get("current_version") == label
        if is_current:
            note = QLabel("This is the current version; the app will switch "
                          "to another version first.")
            note.setObjectName("Warning")
            note.setWordWrap(True)
            self.body.addWidget(note)
        self.remove_remote = QCheckBox("Also delete the branch on GitHub")
        self.remove_remote.setChecked(bool(project.get("repo_url")))
        self.remove_remote.setEnabled(bool(project.get("repo_url")))
        self.body.addWidget(self.remove_remote)
        self.add_cancel()
        self.add_button("Delete version", danger=True, on_click=self._submit)

    def _submit(self) -> None:
        if not self.confirm_destructive(
                f"Delete version {self.label}?",
                "Commits only reachable from this branch will be removed from "
                "the local repository.", confirm_text="Delete"):
            return
        self._on_submit(self.label, {
            "remove_remote": self.remove_remote.isChecked()})


class ForcePushDialog(AppDialog):
    def __init__(self, parent, theme: Theme, label: str, on_submit: Submit) -> None:
        super().__init__(parent, "Force push", theme,
                         subtitle="Force-with-lease replaces the remote branch "
                                  "with your local history.", width=520)
        self._on_submit = on_submit
        self.label = label
        warning = QLabel("Commits on the remote that are not in your local "
                         "branch will be lost.")
        warning.setObjectName("Warning")
        warning.setWordWrap(True)
        self.body.addWidget(warning)
        self.body.addWidget(QLabel(f"Branch version: {label}"))
        self.body.addWidget(QLabel("Type FORCE to confirm"))
        self.confirm = QLineEdit()
        self.confirm.setPlaceholderText("FORCE")
        self.body.addWidget(self.confirm)
        self.add_cancel()
        self.add_button("Force push", danger=True, on_click=self._submit)

    def _submit(self) -> None:
        if self.confirm.text().strip() != "FORCE":
            self._show_inline_error("Type FORCE exactly to confirm.")
            return
        self._on_submit(self.label, {"force": True})

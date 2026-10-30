"""GitHub account and template management dialogs."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QTableWidget, QTableWidgetItem, QWidget,
)

from ..theme import Theme
from ..widgets import Badge, FormBuilder, text_button
from .base import AppDialog
from .project_dialogs import browse_folder


class AddAccountDialog(AppDialog):
    """Add an account by pasting a token; the token's owner wins over any name."""

    def __init__(self, parent, theme: Theme,
                 on_submit: Callable[[str], None],
                 device_flow: Callable[[QWidget], None] | None = None) -> None:
        super().__init__(parent, "Add GitHub account", theme,
                         subtitle="Paste a personal access token. The account "
                                  "name is detected from the token.", width=560)
        self._on_submit = on_submit
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("ghp_\u2026 or github_pat_\u2026")
        form = FormBuilder(self.body, theme)
        form.add_row("Token", self.token)
        form.add_note("Create one at github.com/settings/tokens with the "
                      "repo, read:user and read:org scopes.")
        self.show = QCheckBox("Show token")
        self.show.toggled.connect(
            lambda checked: self.token.setEchoMode(
                QLineEdit.EchoMode.Normal if checked
                else QLineEdit.EchoMode.Password))
        form.add_full(self.show)
        if device_flow is not None:
            self.add_button("Use GitHub sign-in instead\u2026", left=True,
                            on_click=lambda: device_flow(self))
        self.add_cancel()
        self.add_primary("Verify & add", self._submit)

    def _submit(self) -> None:
        token = self.token.text().strip()
        if not token:
            self._show_inline_error("Paste a token first.")
            return
        self._on_submit(token)

    def _on_enter(self) -> None:
        self._submit()


class DeviceFlowDialog(AppDialog):
    """Walk the user through GitHub's device-code sign-in (S9)."""

    def __init__(self, parent, theme: Theme, code: dict,
                 on_poll: Callable[[Callable[[str], None],
                                    Callable[[Exception], None]], None]) -> None:
        super().__init__(parent, "Sign in with GitHub", theme,
                         subtitle="Open the page below and enter the code.",
                         width=480)
        self._on_poll = on_poll
        self.code = code
        link = QLabel(code.get("verification_uri", "https://github.com/login/device"))
        link.setObjectName("Mono")
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(link)
        self.code_label = QLabel(code.get("user_code", ""))
        from ..theme import Typography

        self.code_label.setFont(Typography().font(22, mono=True))
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_label.setStyleSheet(
            f"color: {theme['primary']}; padding: 12px;"
            f" border: 1px dashed {theme['border_strong']}; border-radius: 8px;")
        self.body.addWidget(self.code_label)
        self.status = QLabel("Waiting for you to authorize\u2026")
        self.status.setObjectName("Muted")
        self.body.addWidget(self.status)
        self.add_button("Copy code", left=True,
                        on_click=lambda: self._copy())
        self.add_cancel("Cancel")
        self._timer = QTimer(self)
        self._timer.setInterval(max(5, int(code.get("interval", 5))) * 1000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.code.get("user_code", ""))

    def _poll(self) -> None:
        def done(token: str) -> None:
            self._timer.stop()
            self.status.setText("Authorized.")
            self.token = token
            self.accept()

        def failed(exc: Exception) -> None:
            self.status.setText(f"Sign-in failed: {exc}")

        self._on_poll(done, failed)

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)


class ManageAccountsDialog(AppDialog):
    """List accounts, replace tokens, verify and remove (M12)."""

    def __init__(self, parent, theme: Theme, accounts: list[dict],
                 has_token: Callable[[str], bool],
                 on_replace: Callable[[str, str], None],
                 on_remove: Callable[[str], None],
                 on_verify: Callable[[str], None],
                 on_add: Callable[[], None],
                 backend_name: str) -> None:
        super().__init__(parent, "GitHub accounts", theme, width=680)
        self.theme = theme
        self._has_token = has_token
        self._on_replace = on_replace
        self._on_remove = on_remove
        self._on_verify = on_verify
        self._on_add = on_add

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Account", "Organizations",
                                              "Token", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        from PySide6.QtWidgets import QHeaderView

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(240)
        self.body.addWidget(self.table, 1)

        self.token_field = QLineEdit()
        self.token_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_field.setPlaceholderText("Paste a replacement token\u2026")
        replace_row = QHBoxLayout()
        replace_row.addWidget(self.token_field, 1)
        replace_row.addWidget(QPushButton("Replace", clicked=self._replace))
        host = QWidget()
        host.setLayout(replace_row)
        self.body.addWidget(host)

        note = QLabel(f"Tokens are stored with the {backend_name} backend.")
        note.setObjectName("Subtle")
        self.body.addWidget(note)

        self.add_button("Add account\u2026", left=True, on_click=self._on_add)
        self.add_button("Verify selected", left=True, on_click=self._verify)
        self.add_button("Remove", on_click=self._remove)
        self.add_cancel("Close")
        self.populate(accounts)

    def populate(self, accounts: list[dict]) -> None:
        self.table.setRowCount(0)
        for account in accounts:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = QTableWidgetItem(account.get("username", ""))
            name.setData(Qt.ItemDataRole.UserRole, account.get("username", ""))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, QTableWidgetItem(
                ", ".join(account.get("orgs", [])) or "\u2014"))
            has = self._has_token(account.get("username", ""))
            token_item = QTableWidgetItem("stored" if has else "missing")
            self.table.setItem(row, 2, token_item)
            state = account.get("token_ok", True)
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(6, 2, 6, 2)
            layout.addWidget(Badge("OK" if state and has else "Needs attention",
                                   "success" if state and has else "danger",
                                   self.theme))
            layout.addStretch(1)
            self.table.setCellWidget(row, 3, holder)

    def selected(self) -> str:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return ""
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _replace(self) -> None:
        account = self.selected()
        if not account:
            self._show_inline_error("Select an account first.")
            return
        token = self.token_field.text().strip()
        if not token:
            self._show_inline_error("Paste the new token first.")
            return
        self.token_field.clear()
        self._on_replace(account, token)

    def _verify(self) -> None:
        account = self.selected()
        if account:
            self._on_verify(account)

    def _remove(self) -> None:
        account = self.selected()
        if not account:
            self._show_inline_error("Select an account first.")
            return
        if not self.confirm_destructive(
                f"Remove '{account}'?",
                "The stored token will be deleted from this computer.",
                confirm_text="Remove"):
            return
        self._on_remove(account)


class TemplateDialog(AppDialog):
    """Create or edit a template (ignore rules + placeholders)."""

    def __init__(self, parent, theme: Theme, template: dict | None,
                 on_submit: Callable[[dict], None],
                 on_preview: Callable[[str], dict] | None = None,
                 on_export: Callable[[], None] | None = None) -> None:
        editing = template is not None
        super().__init__(parent, "Edit template" if editing else "New template",
                         theme, width=640)
        self._on_submit = on_submit
        self._on_preview = on_preview
        self.template = template or {}
        self.name_editable = not editing

        self.name = QLineEdit(self.template.get("name", ""))
        self.name.setEnabled(self.name_editable)
        self.path = QLineEdit(self.template.get("path", ""))
        self.description = QLineEdit(self.template.get("description", ""))
        self.ignore = QLineEdit(", ".join(self.template.get("ignore", [])))
        self.placeholders = QLineEdit(
            ", ".join(self.template.get("placeholders", {})))

        form = FormBuilder(self.body, theme)
        form.add_row("Name", self.name)
        browse = QPushButton("Browse\u2026", clicked=self._browse)
        form.add_row("Source folder", self.path, browse)
        form.add_row("Description", self.description)
        form.add_row("Ignore patterns", self.ignore)
        form.add_row("Placeholders", self.placeholders)
        form.add_note("Placeholders are written as {{name}} in file contents "
                      "and file names. Discovered names are added "
                      "automatically.")
        discover = text_button("Discover placeholders from folder", theme,
                               self._discover)
        form.add_row("", discover)
        if editing and on_export:
            form.add_row("", text_button("Export manifest\u2026", theme, on_export))

        self.add_cancel()
        if self._on_preview:
            self.add_button("Preview", on_click=self._preview)
        self.add_primary("Save template", self._submit)

    def _browse(self) -> None:
        folder = browse_folder(self, "Choose the template folder")
        if folder:
            self.path.setText(folder)
            if self.name_editable and not self.name.text().strip():
                self.name.setText(Path(folder).name)

    def _discover(self) -> None:
        from ...template_engine import TemplateEngine

        path = self.path.text().strip()
        if not path:
            self._show_inline_error("Choose a source folder first.")
            return
        engine = TemplateEngine(path, self._ignore_list())
        found = sorted(engine.discover_placeholders())
        existing = {p.strip() for p in self.placeholders.text().split(",")
                    if p.strip()}
        merged = sorted(existing | set(found))
        self.placeholders.setText(", ".join(merged))
        if not found:
            self._show_inline_error("No {{placeholders}} were found.")

    def _ignore_list(self) -> list[str]:
        return [part.strip() for part in self.ignore.text().split(",") if part.strip()]

    def _placeholder_map(self) -> dict[str, str]:
        return {part.strip(): "" for part in self.placeholders.text().split(",")
                if part.strip()}

    def _preview(self) -> None:
        if not self._on_preview:
            return
        from .project_dialogs import _show_preview

        name = self.name.text().strip()
        if not name:
            self._show_inline_error("Enter a name first.")
            return
        try:
            data = self._on_preview(name)
        except Exception as exc:  # noqa: BLE001 - shown inline
            self._show_inline_error(str(exc))
            return
        _show_preview(self, self.theme, name, data)

    def _submit(self) -> None:
        name = self.name.text().strip()
        path = self.path.text().strip()
        if not name:
            self._show_inline_error("Enter a template name.")
            return
        if not path:
            self._show_inline_error("Choose a source folder.")
            return
        self._on_submit({
            "name": name,
            "path": path,
            "description": self.description.text().strip(),
            "ignore": self._ignore_list(),
            "placeholders": self._placeholder_map(),
            "is_new": self.name_editable,
        })


class ManifestImportDialog(AppDialog):
    def __init__(self, parent, theme: Theme, on_submit: Callable[[str, str], None]) -> None:
        super().__init__(parent, "Import template manifest", theme,
                         subtitle="Point at a .scaffold.json file exported from "
                                  "another machine.", width=560)
        self._on_submit = on_submit
        self.manifest = QLineEdit()
        browse = QPushButton("Browse\u2026", clicked=self._pick)
        self.source = QLineEdit()
        source_browse = QPushButton("Browse\u2026", clicked=self._pick_source)
        form = FormBuilder(self.body, theme)
        form.add_row("Manifest", self.manifest, browse)
        form.add_row("Template folder", self.source, source_browse)
        form.add_note("Leave the folder blank to use the path recorded in "
                      "the manifest.")
        self.add_cancel()
        self.add_primary("Import", self._submit)

    def _pick(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Choose manifest", str(Path.home()),
            "Scaffolder manifest (*.json *.scaffold.json)")
        if path:
            self.manifest.setText(path)

    def _pick_source(self) -> None:
        folder = browse_folder(self, "Choose the template folder")
        if folder:
            self.source.setText(folder)

    def _submit(self) -> None:
        manifest = self.manifest.text().strip()
        if not manifest:
            self._show_inline_error("Choose a manifest file.")
            return
        self._on_submit(manifest, self.source.text().strip())


class TaskRunDialog(AppDialog):
    """Pick and run a project task with streamed output (N2)."""

    def __init__(self, parent, theme: Theme, tasks: dict[str, list[str]],
                 on_run: Callable[[list[str], Callable[[str], None]], None]) -> None:
        super().__init__(parent, "Run a task", theme,
                         subtitle="Output streams here as the command runs.",
                         width=680)
        from PySide6.QtWidgets import QComboBox, QPlainTextEdit

        self._on_run = on_run
        row = QHBoxLayout()
        self.choice = QComboBox()
        self.choice.addItems(list(tasks))
        self.commands = tasks
        self.run_btn = QPushButton("Run", clicked=self._run)
        row.addWidget(self.choice, 1)
        row.addWidget(self.run_btn)
        host = QWidget()
        host.setLayout(row)
        self.body.addWidget(host)
        self.output = QPlainTextEdit()
        self.output.setObjectName("Log")
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(300)
        self.body.addWidget(self.output, 1)
        self.add_cancel("Close")

    def append(self, text: str) -> None:
        self.output.appendPlainText(text)

    def _run(self) -> None:
        command = self.commands.get(self.choice.currentText())
        if not command:
            return
        self.output.clear()
        self.output.appendPlainText("$ " + " ".join(command))
        self.run_btn.setEnabled(False)
        self._on_run(command, self.append_runner)

    def append_runner(self, text: str) -> None:
        self.append(text)

    def finish(self) -> None:
        self.run_btn.setEnabled(True)


class StashDialog(AppDialog):
    def __init__(self, parent, theme: Theme, project: str, stashes: list[str],
                 on_apply: Callable[[], None],
                 on_stash: Callable[[str], None]) -> None:
        super().__init__(parent, "Stashes", theme,
                         subtitle=f"{len(stashes)} stash(es) in '{project}'",
                         width=560)
        self._on_apply = on_apply
        self._on_stash = on_stash
        self.list = QListWidget()
        for stash in stashes:
            QListWidgetItem(stash, self.list)
        self.list.setMinimumHeight(180)
        self.body.addWidget(self.list)
        self.message = QLineEdit()
        self.message.setPlaceholderText("Optional description for a new stash")
        row = QHBoxLayout()
        row.addWidget(self.message, 1)
        row.addWidget(QPushButton("Stash current changes", clicked=self._stash))
        host = QWidget()
        host.setLayout(row)
        self.body.addWidget(host)
        self.add_button("Apply latest stash", left=True, on_click=self._apply)
        self.add_cancel("Close")

    def _stash(self) -> None:
        self._on_stash(self.message.text().strip())
        self.accept()

    def _apply(self) -> None:
        self._on_apply()
        self.accept()


class ConflictedDialog(AppDialog):
    def __init__(self, parent, theme: Theme, files: list[str],
                 on_open: Callable[[str], None],
                 on_abort: Callable[[], None]) -> None:
        super().__init__(parent, "Resolve conflicts", theme,
                         subtitle="Open each file and resolve the markers, then "
                                  "stage the result.", width=560)
        self._on_open = on_open
        self.list = QListWidget()
        for name in files:
            QListWidgetItem(name, self.list)
        self.list.setMinimumHeight(160)
        self.body.addWidget(self.list)
        self.add_button("Abort merge", left=True, danger=True,
                        on_click=self._abort)
        self.add_button("Open selected", on_click=self._open_selected)
        self.add_cancel("Close")

    def _open_selected(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._on_open(item.text())

    def _abort(self) -> None:
        if self.confirm_destructive("Abort the merge?",
                                    "The working tree returns to its state "
                                    "before the merge.", confirm_text="Abort"):
            self._on_abort()
            self.accept()

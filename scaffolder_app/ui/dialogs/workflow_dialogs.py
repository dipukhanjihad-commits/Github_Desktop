"""Commit, merge/pull-request, notes, release and settings dialogs."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QRadioButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from ..theme import SPACING, Theme, Typography
from ..widgets import Badge, FormBuilder, SectionLabel, text_button
from .base import AppDialog
from .project_dialogs import browse_folder


class CommitDialog(AppDialog):
    """Commit with per-file staging, amend and an optional push (S1/S2/S3)."""

    def __init__(self, parent, theme: Theme, project_name: str,
                 changes: list[dict], max_title: int,
                 on_submit: Callable[[dict], None],
                 on_push_after: Callable[[], None] | None = None) -> None:
        super().__init__(parent, "Commit changes", theme,
                         subtitle=f"{len(changes)} file(s) in '{project_name}'",
                         width=700)
        self._on_submit = on_submit
        self._on_push_after = on_push_after
        self.max_title = max_title
        self._changes = {c["path"]: c for c in changes}

        self.files = QPlainTextEdit()
        self.files.setObjectName("Code")
        self.files.setReadOnly(True)
        self.files.setMaximumHeight(150)
        self.body.addWidget(SectionLabel("Changed files", theme))
        self.body.addWidget(self.files)

        summary_line = QLabel(_summary(changes))
        summary_line.setObjectName("Subtle")
        self.body.addWidget(summary_line)

        self.unstage_all = QCheckBox("Uncheck all (commit only selected files)")
        self.unstage_all.setChecked(False)
        self.body.addWidget(self.unstage_all)

        self.body.addWidget(SectionLabel("Message", theme))
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText(
            "Short summary of the change\n\nOptional longer description.")
        self.message.setMinimumHeight(110)
        self.message.textChanged.connect(self._update_counter)
        self.body.addWidget(self.message)
        self.counter = QLabel("")
        self.counter.setObjectName("Subtle")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.body.addWidget(self.counter)

        options = QHBoxLayout()
        self.amend = QCheckBox("Amend the last commit")
        self.push_after = QCheckBox("Push after committing")
        options.addWidget(self.amend)
        options.addWidget(self.push_after)
        options.addStretch(1)
        host = QWidget()
        host.setLayout(options)
        self.body.addWidget(host)

        self.suggestions = QHBoxLayout()
        self.suggestions.setSpacing(SPACING["xs"])
        self._build_suggestions()
        self.body.addLayout(self.suggestions)

        self.add_cancel()
        self.commit_btn = self.add_primary("Commit", self._submit)
        self._render_files()
        self._update_counter()

    def _build_suggestions(self) -> None:
        kinds = {c.get("kind") for c in self._changes.values()}
        suggestions = []
        if "conflicted" in kinds:
            suggestions.append("Resolve merge conflicts")
        if "untracked" in kinds or "added" in kinds:
            suggestions.append("Add new files")
        if "deleted" in kinds:
            suggestions.append("Remove deleted files")
        if "renamed" in kinds:
            suggestions.append("Rename files")
        if not suggestions:
            suggestions.append("Update files")
        label = QLabel("Suggestions:")
        label.setObjectName("Subtle")
        self.suggestions.addWidget(label)
        for text in suggestions[:3]:
            self.suggestions.addWidget(
                text_button(text, self.theme,
                            lambda _=False, t=text: self.message.setPlainText(t)))
        self.suggestions.addStretch(1)

    def _render_files(self) -> None:
        lines = []
        for entry in self._changes.values():
            lines.append(f"{entry.get('kind', 'changed'):<10} {entry.get('path', '')}")
        self.files.setPlainText("\n".join(lines))

    def selected_paths(self) -> list[str]:
        """Paths to stage; empty means 'stage everything' is disabled."""
        if self.unstage_all.isChecked():
            return []
        return list(self._changes)

    def _update_counter(self) -> None:
        text = self.message.toPlainText()
        title = text.split("\n", 1)[0].strip()
        length = len(title)
        self.counter.setText(f"{length} / {self.max_title}")
        tone = "Warning" if length > self.max_title else "Subtle"
        self.counter.setObjectName(tone)
        self.counter.style().unpolish(self.counter)
        self.counter.style().polish(self.counter)

    def _submit(self) -> None:
        text = self.message.toPlainText().strip()
        if not text:
            self.message.setFocus()
            self._show_inline_error("Enter a commit message.")
            return
        self._on_submit({
            "message": text,
            "amend": self.amend.isChecked(),
            "push": self.push_after.isChecked(),
            "paths": None if not self.unstage_all.isChecked() else [],
            "stage_all": not self.unstage_all.isChecked(),
        })


def _summary(changes: list[dict]) -> str:
    counts: dict[str, int] = {}
    for entry in changes:
        counts[entry.get("kind", "changed")] = counts.get(
            entry.get("kind", "changed"), 0) + 1
    return "  \u00b7  ".join(f"{count} {kind}" for kind, count in counts.items())


class MergeDialog(AppDialog):
    """Merge two versions locally, or open a pull request (S4)."""

    def __init__(self, parent, theme: Theme, project: dict,
                 on_merge: Callable[[dict], None],
                 on_pr: Callable[[dict], None]) -> None:
        super().__init__(parent, "Merge versions", theme,
                         subtitle="Bring one version's work into another.",
                         width=580)
        self._on_merge = on_merge
        self._on_pr = on_pr
        versions = list(project.get("versions", {}).keys())
        current = project.get("current_version") or (versions[0] if versions else "")
        self.project = project

        form = FormBuilder(self.body, theme)
        self.source = QComboBox()
        self.source.addItems(versions)
        if current in versions:
            self.source.setCurrentText(current)
        self.target = QComboBox()
        self.target.addItems(versions)
        others = [v for v in versions if v != current]
        if others:
            self.target.setCurrentText(others[0])
        form.add_row("Merge from", self.source)
        form.add_row("Merge into", self.target)

        strategy_row = QHBoxLayout()
        self.strategy_merge = QRadioButton("Merge commit")
        self.strategy_squash = QRadioButton("Squash")
        self.strategy_merge.setChecked(True)
        strategy_row.addWidget(self.strategy_merge)
        strategy_row.addWidget(self.strategy_squash)
        strategy_row.addStretch(1)
        host = QWidget()
        host.setLayout(strategy_row)
        form.add_row("Strategy", host)

        mode_row = QHBoxLayout()
        self.mode_local = QRadioButton("Merge locally and push")
        self.mode_pr = QRadioButton("Open a pull request on GitHub")
        self.mode_local.setChecked(True)
        mode_row.addWidget(self.mode_local)
        mode_row.addWidget(self.mode_pr)
        mode_row.addStretch(1)
        host2 = QWidget()
        host2.setLayout(mode_row)
        form.add_row("Mode", host2)

        self.title = QLineEdit(f"Merge {current} into {self.target.currentText()}")
        form.add_row("Title", self.title)
        self.description = QTextEdit()
        self.description.setMinimumHeight(90)
        self.description.setPlainText(
            f"Merge version {current} into {self.target.currentText()}.")
        form.add_row("Description", self.description)
        self.source.currentTextChanged.connect(self._sync_title)

        conflict_note = QLabel(
            "If the merge hits conflicts, they are reported and left for you "
            "to resolve in the editor.")
        conflict_note.setObjectName("Subtle")
        conflict_note.setWordWrap(True)
        self.body.addWidget(conflict_note)

        self.add_cancel()
        self.add_primary("Continue", self._submit)

    def _sync_title(self, _text: str) -> None:
        self.title.setText(f"Merge {self.source.currentText()} into "
                           f"{self.target.currentText()}")

    def payload(self) -> dict:
        return {
            "source": self.source.currentText(),
            "target": self.target.currentText(),
            "strategy": "squash" if self.strategy_squash.isChecked() else "merge",
            "mode": "pr" if self.mode_pr.isChecked() else "local",
            "title": self.title.text().strip(),
            "body": self.description.toPlainText().strip(),
        }

    def _submit(self) -> None:
        data = self.payload()
        if not data["source"] or not data["target"]:
            self._show_inline_error("Pick a source and a target version.")
            return
        if data["source"] == data["target"]:
            self._show_inline_error("Source and target must be different.")
            return
        if data["mode"] == "pr":
            self._on_pr(data)
        else:
            self._on_merge(data)


class NotesDialog(AppDialog):
    def __init__(self, parent, theme: Theme, project: dict,
                 on_submit: Callable[[str], None]) -> None:
        super().__init__(parent, "Project notes", theme,
                         subtitle=project.get("name", ""), width=560)
        self._on_submit = on_submit
        self.text = QTextEdit()
        self.text.setPlainText(project.get("notes", ""))
        self.text.setMinimumHeight(200)
        self.body.addWidget(self.text)
        self.add_cancel()
        self.add_primary("Save", self._submit)

    def _submit(self) -> None:
        self._on_submit(self.text.toPlainText())


class GroupDialog(AppDialog):
    def __init__(self, parent, theme: Theme, project: dict,
                 groups: list[str], on_submit: Callable[[str], None]) -> None:
        super().__init__(parent, "Set group", theme, width=440)
        self._on_submit = on_submit
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems(groups)
        self.group.setCurrentText(project.get("group", ""))
        self.body.addWidget(QLabel("Group name (blank to remove)"))
        self.body.addWidget(self.group)
        self.add_cancel()
        self.add_primary("Save", self._submit)

    def _submit(self) -> None:
        self._on_submit(self.group.currentText().strip())


class VersionDialog(AppDialog):
    """Create a new version (branch) from a parent version."""

    def __init__(self, parent, theme: Theme, project: dict,
                 on_submit: Callable[[dict], None]) -> None:
        super().__init__(parent, "New version", theme,
                         subtitle="Each version is its own git branch.",
                         width=480)
        self._on_submit = on_submit
        versions = list(project.get("versions", {}).keys())
        current = project.get("current_version") or (versions[-1] if versions else "")
        self.label = QLineEdit(_next_label(versions))
        self.parent = QComboBox()
        self.parent.addItems(versions)
        if current in versions:
            self.parent.setCurrentText(current)
        self.strategy = QComboBox()
        self.strategy.addItems(["stash", "commit", "discard"])
        self.strategy.setToolTip(
            "What to do with uncommitted changes: stash them (default), "
            "commit them, or discard them.")
        form = FormBuilder(self.body, theme)
        form.add_row("Version label", self.label)
        form.add_row("Branch from", self.parent)
        form.add_row("Uncommitted changes", self.strategy)
        self.add_cancel()
        self.add_primary("Create version", self._submit)

    def _submit(self) -> None:
        label = self.label.text().strip()
        if not label:
            self._show_inline_error("Enter a version label.")
            return
        self._on_submit({"label": label,
                         "parent": self.parent.currentText(),
                         "strategy": self.strategy.currentText()})


def _next_label(versions: list[str]) -> str:
    index = len(versions) + 1
    while f"v{index}" in versions:
        index += 1
    return f"v{index}"


class ReleaseDialog(AppDialog):
    def __init__(self, parent, theme: Theme, project: dict,
                 tags: list[str], on_submit: Callable[[dict], None]) -> None:
        super().__init__(parent, "Create release", theme,
                         subtitle=project.get("name", ""), width=560)
        self._on_submit = on_submit
        self.tag = QComboBox()
        self.tag.setEditable(True)
        self.tag.addItems(tags)
        self.tag.setCurrentText(tags[0] if tags else "v1.0.0")
        self.name = QLineEdit()
        self.body_text = QTextEdit()
        self.body_text.setMinimumHeight(110)
        self.draft = QCheckBox("Save as draft")
        form = FormBuilder(self.body, theme)
        form.add_row("Tag", self.tag)
        form.add_row("Release title", self.name)
        form.add_row("Notes", self.body_text)
        form.add_full(self.draft)
        self.add_cancel()
        self.add_primary("Create release", self._submit)

    def _submit(self) -> None:
        tag = self.tag.currentText().strip()
        if not tag:
            self._show_inline_error("Enter a tag name.")
            return
        self._on_submit({"tag": tag, "name": self.name.text().strip(),
                         "body": self.body_text.toPlainText().strip(),
                         "draft": self.draft.isChecked()})


class SettingsDialog(AppDialog):
    """All persisted preferences, grouped into tabs (S5/M16)."""

    def __init__(self, parent, theme: Theme, settings: dict,
                 editors: dict[str, bool], storage_note: str,
                 on_submit: Callable[[dict], None],
                 on_export: Callable[[], None] | None = None,
                 on_import: Callable[[], None] | None = None,
                 on_open_logs: Callable[[], None] | None = None,
                 on_open_trash: Callable[[], None] | None = None) -> None:
        super().__init__(parent, "Settings", theme, width=660)
        self._on_submit = on_submit
        self._on_export = on_export
        self._on_import = on_import
        self._settings = dict(settings)

        from PySide6.QtWidgets import QTabWidget

        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs, 1)
        self._build_general(theme, settings, editors)
        self._build_git(theme, settings)
        self._build_advanced(theme, settings, storage_note)
        if on_open_logs or on_open_trash:
            footer_note = QHBoxLayout()
            if on_open_trash:
                footer_note.addWidget(text_button("Open trash folder", theme,
                                                  on_open_trash))
            if on_open_logs:
                footer_note.addWidget(text_button("Open data folder", theme,
                                                  on_open_logs))
            footer_note.addStretch(1)
            self.body.addLayout(footer_note)

        self.add_cancel()
        self.add_primary("Save settings", self._submit)

    def _build_general(self, theme: Theme, s: dict, editors: dict) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, SPACING["md"], 4, 4)
        form = FormBuilder(layout, theme)

        self.theme_box = QComboBox()
        for key, label in (("system", "Follow system"), ("light", "Light"),
                           ("dark", "Dark")):
            self.theme_box.addItem(label, key)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(
            s.get("theme", "system"))))
        form.add_row("Appearance", self.theme_box)

        self.editor_box = QComboBox()
        from ...editor import EDITOR_LABELS

        for key, label in EDITOR_LABELS.items():
            mark = ""
            if key in editors:
                mark = "  \u2713 installed" if editors[key] else "  (not found)"
            self.editor_box.addItem(label + mark, key)
        self.editor_box.setCurrentIndex(max(0, self.editor_box.findData(
            s.get("editor", "vscode"))))
        form.add_row("Editor", self.editor_box)

        self.editor_command = QLineEdit(s.get("editor_command", "") or "")
        self.editor_command.setPlaceholderText("code  (only for Custom)")
        form.add_row("Custom command", self.editor_command)

        self.projects_path = QLineEdit(s.get("default_projects_path", ""))
        browse = _browse_button(self, self.projects_path)
        form.add_row("Default projects path", self.projects_path, browse)

        self.language = QComboBox()
        self.language.addItem("English", "en")
        self.language.setCurrentIndex(max(0, self.language.findData(
            s.get("language", "en"))))
        form.add_row("Language", self.language)
        layout.addStretch(1)
        self.tabs.addTab(page, "General")

    def _build_git(self, theme: Theme, s: dict) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, SPACING["md"], 4, 4)
        form = FormBuilder(layout, theme)

        self.default_branch = QLineEdit(s.get("default_branch", "main"))
        form.add_row("Default branch", self.default_branch)

        self.switch_strategy = QComboBox()
        for key, label in (("stash", "Stash them (recommended)"),
                           ("commit", "Commit them as WIP"),
                           ("discard", "Discard them"),
                           ("abort", "Refuse the switch")):
            self.switch_strategy.addItem(label, key)
        self.switch_strategy.setCurrentIndex(max(0, self.switch_strategy.findData(
            s.get("switch_strategy", "stash"))))
        form.add_row("Uncommitted changes", self.switch_strategy)

        self.auto_create = QCheckBox("Create a GitHub repository for new projects")
        self.auto_create.setChecked(bool(s.get("auto_create_repo", True)))
        form.add_full(self.auto_create)
        self.auto_push = QCheckBox("Push automatically after each commit")
        self.auto_push.setChecked(bool(s.get("auto_push_after_commit", False)))
        form.add_full(self.auto_push)
        self.fetch_on_refresh = QCheckBox(
            "Fetch from GitHub when refreshing sync status")
        self.fetch_on_refresh.setChecked(bool(s.get("fetch_on_refresh", True)))
        form.add_full(self.fetch_on_refresh)
        self.confirm_public = QCheckBox(
            "Ask before making a repository public")
        self.confirm_public.setChecked(bool(s.get("confirm_make_public", True)))
        form.add_full(self.confirm_public)
        layout.addStretch(1)
        self.ignore_edit = QLineEdit(", ".join(s.get("default_ignore", [])))
        form.add_row("Default ignores", self.ignore_edit)
        reset = text_button("Reset to defaults", theme,
                            lambda: self.ignore_edit.setText(
                                ", ".join(_default_ignores())))
        form.add_row("", reset)
        self.tabs.addTab(page, "Git")

    def _build_advanced(self, theme: Theme, s: dict, storage_note: str) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, SPACING["md"], 4, 4)
        form = FormBuilder(layout, theme)

        self.max_title = QSpinBox()
        self.max_title.setRange(20, 200)
        self.max_title.setValue(int(s.get("max_commit_title_length", 72) or 72))
        form.add_row("Commit title limit", self.max_title)

        self.confirm_destructive = QCheckBox(
            "Always confirm destructive operations")
        self.confirm_destructive.setChecked(
            bool(s.get("confirm_destructive", True)))
        form.add_full(self.confirm_destructive)
        self.auto_refresh = QSpinBox()
        self.auto_refresh.setRange(0, 600)
        self.auto_refresh.setSingleStep(15)
        self.auto_refresh.setValue(int(s.get("auto_refresh_seconds", 0) or 0))
        self.auto_refresh.setSuffix(" s")
        form.add_row("Auto refresh (0 = off)", self.auto_refresh)

        note = QLabel(storage_note)
        note.setWordWrap(True)
        note.setObjectName("Subtle")
        form.add_full(note)

        buttons = QHBoxLayout()
        if self._on_export:
            buttons.addWidget(text_button("Export project manifest\u2026", theme,
                                          self._on_export))
        if self._on_import:
            buttons.addWidget(text_button("Import project manifest\u2026", theme,
                                          self._on_import))
        buttons.addStretch(1)
        host = QWidget()
        host.setLayout(buttons)
        form.add_full(host)
        layout.addStretch(1)
        self.tabs.addTab(page, "Advanced")

    def payload(self) -> dict:
        return {
            "theme": self.theme_box.currentData(),
            "editor": self.editor_box.currentData(),
            "editor_command": self.editor_command.text().strip(),
            "default_projects_path": self.projects_path.text().strip(),
            "language": self.language.currentData(),
            "default_branch": self.default_branch.text().strip() or "main",
            "switch_strategy": self.switch_strategy.currentData(),
            "auto_create_repo": self.auto_create.isChecked(),
            "auto_push_after_commit": self.auto_push.isChecked(),
            "fetch_on_refresh": self.fetch_on_refresh.isChecked(),
            "confirm_make_public": self.confirm_public.isChecked(),
            "default_ignore": [part.strip() for part in
                               self.ignore_edit.text().split(",") if part.strip()],
            "max_commit_title_length": self.max_title.value(),
            "confirm_destructive": self.confirm_destructive.isChecked(),
            "auto_refresh_seconds": self.auto_refresh.value(),
        }

    def _submit(self) -> None:
        self._on_submit(self.payload())


def _default_ignores() -> list[str]:
    from ...project_memory import default_settings

    return default_settings()["default_ignore"]


def _browse_button(parent, target: QLineEdit) -> QWidget:
    from PySide6.QtWidgets import QPushButton

    button = QPushButton("Browse\u2026")
    button.clicked.connect(
        lambda: target.setText(browse_folder(parent, "Choose a folder")
                               or target.text()))
    return button


class TrashDialog(AppDialog):
    """Restore or purge recently deleted projects (S14)."""

    def __init__(self, parent, theme: Theme, entries: list[dict],
                 on_restore: Callable[[str], None],
                 on_purge: Callable[[str], None],
                 on_empty: Callable[[], None]) -> None:
        super().__init__(parent, "Trash", theme,
                         subtitle="Deleted projects are kept here until purged.",
                         width=640)
        self._on_restore = on_restore
        self._on_purge = on_purge
        self._on_empty = on_empty
        from PySide6.QtWidgets import QScrollArea

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(320)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(SPACING["sm"])
        if not entries:
            label = QLabel("Trash is empty.")
            label.setObjectName("Subtle")
            layout.addWidget(label)
        for entry in entries:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            text = QVBoxLayout()
            title = QLabel(entry.get("name", "(unnamed)"))
            title.setFont(Typography().body_strong())
            detail = QLabel(f"{entry.get('path', '')}\nDeleted {entry.get('at', '')}")
            detail.setObjectName("Subtle")
            text.addWidget(title)
            text.addWidget(detail)
            row_layout.addLayout(text, 1)
            if not entry.get("exists"):
                badge = Badge("Missing", "warning", theme)
                row_layout.addWidget(badge)
            else:
                row_layout.addWidget(text_button(                    "Restore", theme,
                    lambda _=False, stamp=entry.get("stamp"): self._restore(stamp)))
            row_layout.addWidget(text_button(
                "Delete permanently", theme,
                lambda _=False, stamp=entry.get("stamp"): self._purge(stamp)))
            layout.addWidget(row)
        layout.addStretch(1)
        scroll.setWidget(inner)
        self.body.addWidget(scroll, 1)
        if entries:
            self.add_button("Empty trash", danger=True, left=True,
                            on_click=self._empty)
        self.add_cancel("Close")

    def _restore(self, stamp: str) -> None:
        if stamp:
            self._on_restore(stamp)
        self.accept()

    def _purge(self, stamp: str) -> None:
        if stamp and self.confirm_destructive(
                "Delete permanently?",
                "This folder will be removed from disk and cannot be recovered.",
                confirm_text="Delete"):
            self._on_purge(stamp)
            self.accept()

    def _empty(self) -> None:
        if self.confirm_destructive(
                "Empty the trash?",
                "Every folder in the trash will be permanently deleted.",
                confirm_text="Empty trash"):
            self._on_empty()
            self.accept()

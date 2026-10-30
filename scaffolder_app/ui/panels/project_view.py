"""Right-hand workspace: project header, action bar and tabbed content.

Tabs: Overview (versions), Changes (diff-aware), History, GitHub.
The header carries the sync summary in words (S17) and a single action strip
where every destructive item is styled distinctly (M18).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu, QPushButton,
    QSizePolicy, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..theme import SPACING, Theme, Typography
from ..widgets import Badge, text_button

REPO_STATE_LABELS = {
    "LOCAL_ONLY": ("Local only", "neutral"),
    "REPO_PENDING": ("Repo incomplete", "warning"),
    "REPO_CREATED": ("Repo linked", "info"),
    "SYNCED": ("Synced", "success"),
}

SYNC_LABELS = {
    "UNKNOWN": ("No remote", "neutral"),
    "SYNCED": ("Up to date", "success"),
    "AHEAD": ("Ahead", "info"),
    "BEHIND": ("Behind", "warning"),
    "DIVERGED": ("Diverged", "danger"),
}

STATUS_LABELS = {
    "new file": ("New", "success"),
    "added": ("Added", "success"),
    "modified": ("Modified", "warning"),
    "deleted": ("Deleted", "danger"),
    "renamed": ("Renamed", "info"),
    "changed": ("Changed", "warning"),
    "conflicted": ("Conflict", "danger"),
    "untracked": ("Untracked", "neutral"),
}


TONE_COLORS = {
    "neutral": "text_muted",
    "primary": "primary",
    "success": "success",
    "warning": "warning",
    "danger": "danger",
    "info": "info",
}


class ProjectHeader(QWidget):
    """Title, badges, remote link and the primary action bar."""

    open_remote = Signal()
    action = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACING["sm"])

        top = QHBoxLayout()
        top.setSpacing(SPACING["sm"])
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(SPACING["sm"])
        self.title = QLabel("\u2014")
        self.title.setFont(Typography().title())
        row.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)
        self.repo_badge = Badge("", "neutral", theme)
        self.vis_badge = Badge("", "neutral", theme)
        self.sync_badge = Badge("", "neutral", theme)
        for badge in (self.repo_badge, self.vis_badge, self.sync_badge):
            row.addWidget(badge)
        row.addStretch(1)
        title_col.addLayout(row)

        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Subtitle")
        self.subtitle.setWordWrap(True)
        self.subtitle.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        title_col.addWidget(self.subtitle)
        top.addLayout(title_col, 1)

        self.remote_link = QPushButton("")
        self.remote_link.setObjectName("Link")
        self.remote_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remote_link.setVisible(False)
        self.remote_link.clicked.connect(self.open_remote.emit)
        top.addWidget(self.remote_link, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(SPACING["xs"])
        root.addLayout(self.actions)
        self._buttons: dict[str, QPushButton] = {}

    def add_action(self, key: str, label: str, tooltip: str = "",
                   primary: bool = False, menu: QMenu | None = None) -> QPushButton:
        button = QPushButton(label)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip or label)
        if primary:
            button.setObjectName("Primary")
        if menu is not None:
            button.setMenu(menu)
        else:
            button.clicked.connect(lambda: self.action.emit(key))
        self.actions.addWidget(button)
        self._buttons[key] = button
        return button

    def add_action_spacer(self) -> None:
        self.actions.addStretch(1)

    def add_widget_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)

    def set_enabled(self, key: str, enabled: bool) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setEnabled(enabled)

    def set_text(self, key: str, text: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setText(text)

    def update_state(self, project: dict[str, Any], summary: dict[str, Any],
                     branch: str = "", changes: int = 0,
                     ahead_total: int = 0, behind_total: int = 0) -> None:
        name = project.get("name", "Untitled")
        self.title.setText(name)
        folder = project.get("path", "")
        bits = [folder]
        if branch:
            bits.append(f"branch {branch}")
        if changes:
            bits.append(f"{changes} uncommitted file"
                        + ("s" if changes != 1 else ""))
        self.subtitle.setText("  \u00b7  ".join(bits))

        state_label, state_tone = REPO_STATE_LABELS.get(
            project.get("repo_state", ""), ("Unknown", "neutral"))
        self.repo_badge.set(state_label, state_tone)
        private = project.get("visibility", "private") == "private"
        self.vis_badge.set("Private" if private else "Public",
                          "neutral" if private else "primary")

        if ahead_total and behind_total:
            self.sync_badge.set(f"{ahead_total} ahead, {behind_total} behind",
                                "danger")
        elif ahead_total:
            self.sync_badge.set(f"{ahead_total} ahead", "info")
        elif behind_total:
            self.sync_badge.set(f"{behind_total} behind", "warning")
        elif project.get("repo_url"):
            self.sync_badge.set("Up to date", "success")
        else:
            self.sync_badge.set("No remote", "neutral")

        self.remote_link.setVisible(False)

    def set_remote(self, url: str) -> None:
        if url:
            from ...github_api import parse_remote

            try:
                info = parse_remote(url)
                self.remote_link.setText(f"{info.owner}/{info.repo} \u2197")
            except Exception:
                self.remote_link.setText(f"{url} \u2197")
            self.remote_link.setVisible(True)
        else:
            self.remote_link.setVisible(False)


class VersionsTable(QWidget):
    """Version tree flattened with indentation, plus inline state."""

    selection_changed = Signal(str)
    double_clicked = Signal(str)
    context_requested = Signal(str, object)

    COLUMNS = ("Version", "Branch", "Parent", "State", "\u2191", "\u2193")

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QTreeWidget()
        self.view.setColumnCount(len(self.COLUMNS))
        self.view.setHeaderLabels(list(self.COLUMNS))
        self.view.setAlternatingRowColors(False)
        self.view.setRootIsDecorated(False)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setUniformRowHeights(True)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.view.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for index in range(2, len(self.COLUMNS)):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.view)
        self.view.itemSelectionChanged.connect(self._on_select)
        self.view.itemDoubleClicked.connect(
            lambda item, _c: self.double_clicked.emit(item.data(0, Qt.ItemDataRole.UserRole)))
        self.view.customContextMenuRequested.connect(self._on_context)
        self._current = ""

    def _on_select(self) -> None:
        items = self.view.selectedItems()
        if items:
            self.selection_changed.emit(
                items[0].data(0, Qt.ItemDataRole.UserRole) or "")

    def _on_context(self, pos) -> None:
        item = self.view.itemAt(pos)
        if item is None:
            return
        label = item.data(0, Qt.ItemDataRole.UserRole) or ""
        self.context_requested.emit(label, self.view.viewport().mapToGlobal(pos))

    def selected_label(self) -> str:
        items = self.view.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else ""

    def populate(self, project: dict[str, Any]) -> None:
        self.view.clear()
        versions = project.get("versions", {})
        current = project.get("current_version")
        order = _tree_order(versions)
        for label in order:
            version = versions[label]
            depth = 0
            parent = version.get("parent")
            guard = 0
            while parent in versions and guard < 20:
                depth += 1
                parent = versions[parent].get("parent")
                guard += 1
            item = QTreeWidgetItem([
                ("    " * depth) + label,
                version.get("branch", ""),
                version.get("parent") or "\u2014",
                "",
                str(version.get("ahead", 0)) if version.get("ahead") else "",
                str(version.get("behind", 0)) if version.get("behind") else "",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, label)
            state = version.get("sync_state", "UNKNOWN")
            text, tone = SYNC_LABELS.get(state, ("Unknown", "neutral"))
            if label == current:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                text = f"\u25cf current \u00b7 {text}"
            item.setText(3, text)
            item.setForeground(3, _brush(self.theme[TONE_COLORS.get(tone, "text_muted")]))
            self.view.addTopLevelItem(item)
            if label == self._current:
                item.setSelected(True)
        self._current = current or ""

    def select(self, label: str) -> None:
        for index in range(self.view.topLevelItemCount()):
            item = self.view.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == label:
                item.setSelected(True)
                self.view.scrollToItem(item)
                return


def _tree_order(versions: dict[str, Any]) -> list[str]:
    """Order versions parent-before-child, preserving insertion order."""
    children: dict[str | None, list[str]] = {}
    for label, version in versions.items():
        parent = version.get("parent")
        if parent not in versions:
            parent = None
        children.setdefault(parent, []).append(label)
    ordered: list[str] = []

    def walk(parent: str | None, depth: int = 0) -> None:
        if depth > 12:
            return
        for label in children.get(parent, []):
            ordered.append(label)
            walk(label, depth + 1)

    walk(None)
    for label in versions:
        if label not in ordered:
            ordered.append(label)
    return ordered


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(color))


class ChangesPanel(QWidget):
    """Uncommitted changes with per-file staging, diff preview and actions (S1/S2)."""

    commit_requested = Signal()
    refresh_requested = Signal()
    stage_requested = Signal(list, bool)
    discard_requested = Signal(list)
    open_file_requested = Signal(str)
    diff_requested = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["sm"])

        bar = QHBoxLayout()
        self.summary = QLabel("No changes")
        self.summary.setObjectName("Muted")
        bar.addWidget(self.summary, 1)
        self.stage_btn = text_button("Stage all", theme,
                                     lambda: self.stage_requested.emit(
                                         self._all_paths(), True))
        self.unstage_btn = text_button("Unstage all", theme,
                                       lambda: self.stage_requested.emit(
                                           self._all_paths(), False))
        self.refresh_btn = text_button("Refresh", theme,
                                       self.refresh_requested.emit)
        self.diff_btn = text_button("Show diff", theme,
                                    lambda: self.diff_requested.emit(
                                        self.selected_path()))
        bar.addWidget(self.diff_btn)
        bar.addWidget(self.stage_btn)
        bar.addWidget(self.unstage_btn)
        bar.addWidget(self.refresh_btn)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Status", "File", "Staged"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.table)

        self.diff_view = QTextEdit()
        self.diff_view.setObjectName("Code")
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        splitter.addWidget(self.diff_view)
        splitter.setSizes([320, 240])
        layout.addWidget(splitter, 1)

        actions = QHBoxLayout()
        self.stage_selected = QPushButton("Stage selected")
        self.stage_selected.clicked.connect(
            lambda: self.stage_requested.emit(self.selected_paths(), True))
        self.unstage_selected = QPushButton("Unstage selected")
        self.unstage_selected.clicked.connect(
            lambda: self.stage_requested.emit(self.selected_paths(), False))
        self.open_btn = QPushButton("Open file")
        self.open_btn.clicked.connect(
            lambda: self.open_file_requested.emit(self.selected_path()))
        self.discard_selected = QPushButton("Discard selected\u2026")
        self.discard_selected.setObjectName("Danger")
        self.discard_selected.clicked.connect(
            lambda: self.discard_requested.emit(self.selected_paths()))
        self.commit_btn = QPushButton("Commit\u2026")
        self.commit_btn.setObjectName("Primary")
        self.commit_btn.clicked.connect(self.commit_requested.emit)
        actions.addWidget(self.stage_selected)
        actions.addWidget(self.unstage_selected)
        actions.addWidget(self.open_btn)
        actions.addWidget(self.discard_selected)
        actions.addStretch(1)
        actions.addWidget(self.commit_btn)
        layout.addLayout(actions)

        self._entries: dict[str, dict] = {}
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.cellDoubleClicked.connect(
            lambda _r, _c: self.open_file_requested.emit(self.selected_path()))

    def _on_selection(self) -> None:
        path = self.selected_path()
        if path:
            self.diff_requested.emit(path)

    def selected_path(self) -> str:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return ""
        item = self.table.item(rows[0].row(), 1)
        return item.text() if item else ""

    def selected_paths(self) -> list[str]:
        return [self.table.item(index.row(), 1).text()
                for index in self.table.selectionModel().selectedRows()]

    def _all_paths(self) -> list[str]:
        return list(self._entries)

    def populate(self, entries: list[dict]) -> None:
        self._entries = {entry["path"]: entry for entry in entries}
        self.table.setRowCount(0)
        for entry in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            status = QTableWidgetItem("")
            self.table.setItem(row, 0, status)
            path_item = QTableWidgetItem(entry["path"])
            self.table.setItem(row, 1, path_item)
            staged = QTableWidgetItem(
                "yes" if entry.get("staged") and not entry.get("untracked") else
                ("partial" if entry.get("staged") else ""))
            self.table.setItem(row, 2, staged)
            self._decorate_status(row, entry)
        total = len(entries)
        staged_count = sum(1 for e in entries if e.get("staged"))
        if total:
            self.summary.setText(
                f"{total} change{'s' if total != 1 else ''} \u00b7 "
                f"{staged_count} staged")
        else:
            self.summary.setText("Working tree is clean")
        self.commit_btn.setEnabled(self.table.rowCount() > 0)

    def _decorate_status(self, row: int, entry: dict) -> None:
        word, tone = STATUS_LABELS.get(entry.get("kind", ""), ("Changed", "neutral"))
        item = self.table.item(row, 0)
        item.setText(word)
        item.setForeground(_brush(self.theme[TONE_COLORS.get(tone, "text_muted")]))
        item.setToolTip(f"{entry.get('raw_status', '')} \u00b7 {entry.get('kind', '')}")

    def show_diff(self, text: str) -> None:
        self.diff_view.setPlainText(text or "(no diff preview)")

    def clear_diff(self) -> None:
        self.diff_view.setPlainText("")

    def preview_first(self) -> None:
        """Select the first row and show its diff without re-entering the
        selection handler (which would trigger a redundant reload)."""
        if not self.table.rowCount():
            return
        self.table.blockSignals(True)
        self.table.selectRow(0)
        path_item = self.table.item(0, 1)
        path = path_item.text() if path_item else ""
        self.table.blockSignals(False)
        if path:
            self.diff_requested.emit(path)


class HistoryPanel(QWidget):
    """Commit log with a detail pane (S3)."""

    commit_selected = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.view = QTreeWidget()
        self.view.setColumnCount(3)
        self.view.setHeaderLabels(["Commit", "Author", "When"])
        self.view.setRootIsDecorated(False)
        self.view.setUniformRowHeights(True)
        header = self.view.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self.view)
        self.detail = QTextEdit()
        self.detail.setObjectName("Code")
        self.detail.setReadOnly(True)
        splitter.addWidget(self.detail)
        splitter.setSizes([260, 300])
        layout.addWidget(splitter)
        self.view.itemSelectionChanged.connect(self._on_selection)

    def populate(self, commits: list[dict]) -> None:
        self.view.clear()
        for commit in commits:
            item = QTreeWidgetItem([
                commit.get("subject", ""), commit.get("author", ""),
                _human_time(commit.get("date", "")),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, commit.get("sha", ""))
            item.setToolTip(0, commit.get("sha", ""))
            self.view.addTopLevelItem(item)
        if commits:
            self.view.topLevelItem(0).setSelected(True)

    def _on_selection(self) -> None:
        items = self.view.selectedItems()
        if items:
            self.commit_selected.emit(
                items[0].data(0, Qt.ItemDataRole.UserRole) or "")

    def show_detail(self, text: str) -> None:
        self.detail.setPlainText(text)


class IssuesPanel(QWidget):
    """GitHub issues + pull requests for the current project (N3)."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)
        self.view = QTreeWidget()
        self.view.setColumnCount(3)
        self.view.setHeaderLabels(["Type", "#", "Title"])
        self.view.setRootIsDecorated(False)
        header = self.view.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.view, 1)

    def populate(self, items: list[dict]) -> None:
        self.view.clear()
        for item in items:
            row = QTreeWidgetItem([
                item.get("kind", "Issue"), f"#{item.get('number', '')}",
                item.get("title", ""),
            ])
            row.setData(0, Qt.ItemDataRole.UserRole, item.get("url", ""))
            self.view.addTopLevelItem(row)
        self.status.setText(
            f"{self.view.topLevelItemCount()} open item(s)" if items
            else "No open issues or pull requests.")

    def set_message(self, text: str) -> None:
        self.view.clear()
        self.status.setText(text)


class ProjectWorkspace(QWidget):
    """Container wiring header + tabs; the host connects the signals."""

    action = Signal(str)
    version_selected = Signal(str)
    version_double_clicked = Signal(str)
    version_context = Signal(str, object)
    commit_requested = Signal()
    changes_refresh = Signal()
    stage_requested = Signal(list, bool)
    discard_requested = Signal(list)
    open_file_requested = Signal(str)
    diff_requested = Signal(str)
    history_commit_selected = Signal(str)
    open_on_github = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["lg"], SPACING["lg"],
                                  SPACING["lg"], SPACING["md"])
        layout.setSpacing(SPACING["md"])

        self.header = ProjectHeader(theme)
        self.header.action.connect(self._emit_action)
        self.header.open_remote.connect(self.open_on_github.emit)
        self._build_actions()
        layout.addWidget(self.header)

        self.banner = QWidget()
        self.banner.setObjectName("Banner")
        self.banner.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Fixed)
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(SPACING["sm"])
        self.banner_text = QLabel("")
        self.banner_text.setWordWrap(True)
        banner_layout.addWidget(self.banner_text, 1)
        self.banner_action = QPushButton("Review changes")
        self.banner_action.clicked.connect(lambda: self._emit_action("changes"))
        banner_layout.addWidget(self.banner_action)
        self.banner.setVisible(False)
        layout.addWidget(self.banner)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)

        # Overview
        overview = QWidget()
        ov = QVBoxLayout(overview)
        ov.setContentsMargins(0, SPACING["sm"], 0, 0)
        row = QHBoxLayout()
        label = QLabel("Versions")
        label.setFont(Typography().heading())
        row.addWidget(label)
        row.addStretch(1)
        self.new_version_btn = text_button("New version", theme,
                                           lambda: self._emit_action("new_version"))
        row.addWidget(self.new_version_btn)
        ov.addLayout(row)
        self.versions = VersionsTable(theme)
        self.versions.selection_changed.connect(self.version_selected.emit)
        self.versions.double_clicked.connect(self.version_double_clicked.emit)
        self.versions.context_requested.connect(self.version_context.emit)
        ov.addWidget(self.versions, 1)
        ov.addWidget(self._notes_card())
        self.tabs.addTab(overview, "Overview")

        # Changes
        self.changes = ChangesPanel(theme)
        self.changes.commit_requested.connect(self.commit_requested.emit)
        self.changes.refresh_requested.connect(self.changes_refresh.emit)
        self.changes.stage_requested.connect(self.stage_requested.emit)
        self.changes.discard_requested.connect(self.discard_requested.emit)
        self.changes.open_file_requested.connect(self.open_file_requested.emit)
        self.changes.diff_requested.connect(self.diff_requested.emit)
        self.tabs.addTab(self.changes, "Changes")

        # History
        self.history = HistoryPanel(theme)
        self.history.commit_selected.connect(self.history_commit_selected.emit)
        self.tabs.addTab(self.history, "History")

        # GitHub
        self.issues = IssuesPanel(theme)
        self.tabs.addTab(self.issues, "GitHub")

        self.empty = QStackedWidget()
        self.empty.setVisible(False)
        layout.addWidget(self.empty)

    # ------------------------------------------------------------------ ui

    def _build_actions(self) -> None:
        header = self.header
        header.add_action("open_folder", "Open folder", "Open the folder (Ctrl+O)")
        header.add_action("editor", "Open in editor", "Open the project in the editor")
        header.add_action_spacer()
        header.add_action("commit", "Commit", "Commit changes (Ctrl+Shift+C)",
                          primary=True)
        header.add_action("pull", "Pull", "Pull from origin")
        header.add_action("push", "Push", "Push current version")
        self._more_menu = QMenu(self)
        header.add_action("more", "More", "More actions", menu=self._more_menu)

    @property
    def more(self) -> QMenu:
        return self._more_menu

    def _notes_card(self) -> QWidget:
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, SPACING["sm"], 0, 0)
        title = QLabel("Notes")
        title.setFont(Typography().heading())
        layout.addWidget(title)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Project notes (saved automatically)")
        self.notes.setMaximumHeight(84)
        layout.addWidget(self.notes)
        return card

    def _emit_action(self, key: str) -> None:
        if key == "changes":
            self.tabs.setCurrentWidget(self.changes)
            return
        self.action.emit(key)

    def show_project(self, project: dict[str, Any], summary: dict[str, Any],
                     branch: str = "", changes: int = 0, status: dict | None = None,
                     notes: str | None = None) -> None:
        status = status or {}
        self.header.update_state(
            project, summary, branch,
            changes=changes,
            ahead_total=summary.get("ahead_total", 0),
            behind_total=summary.get("behind_total", 0))
        self.header.set_remote(project.get("repo_url") or "")
        self.versions.populate(project)
        if notes is not None and self.notes.toPlainText() != notes:
            self.notes.blockSignals(True)
            self.notes.setPlainText(notes)
            self.notes.blockSignals(False)

        state = project.get("repo_state")
        has_repo = state in ("SYNCED", "REPO_CREATED")
        self.header.set_enabled("push", has_repo)
        self.header.set_enabled("pull", has_repo)
        missing = not status.get("exists", True)
        for key in ("open_folder", "editor", "commit"):
            self.header.set_enabled(key, not missing)
        self.update_banner(project, changes, summary)

    def update_banner(self, project: dict[str, Any], changes: int,
                      summary: dict[str, Any] | None = None) -> None:
        """Show/hide the uncommitted-work banner from the current state."""
        summary = summary or {}
        if changes:
            self.banner_text.setText(
                f"{changes} uncommitted file{'s' if changes != 1 else ''} "
                "\u2014 commit before switching versions or pushing.")
            self.banner.setVisible(True)
        elif summary.get("worst_sync") == "DIVERGED":
            self.banner_text.setText(
                "Local and remote branches have diverged. Pull to reconcile.")
            self.banner.setVisible(True)
        elif project.get("repo_state") == "REPO_PENDING":
            self.banner_text.setText(
                "Repository creation did not finish. Use Create Repository "
                "to retry.")
            self.banner.setVisible(True)
        else:
            self.banner.setVisible(False)

    def show_message_tab(self, tab: int, text: str) -> None:
        if tab == 3:
            self.issues.set_message(text)

    def set_empty(self, empty: bool) -> None:
        self.header.setVisible(not empty)
        self.tabs.setVisible(not empty)
        self.banner.setVisible(False if empty else self.banner.isVisible())


def _human_time(iso: str) -> str:
    if not iso:
        return ""
    from datetime import datetime, timezone

    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:10]
    delta = datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 86400 * 7:
        return f"{seconds // 86400}d ago"
    return stamp.strftime("%Y-%m-%d")

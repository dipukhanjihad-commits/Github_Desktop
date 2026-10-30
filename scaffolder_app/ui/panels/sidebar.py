"""Left sidebar: searchable project list plus quick actions.

Rows show a state dot, the name, and compact badges; pinned projects float to
the top and groups act as lightweight folders (N6).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMenu, QStackedWidget, QVBoxLayout, QWidget,
)

from ...project_memory import RepoState
from ..theme import SPACING, Theme, Typography
from ..widgets import EmptyState, LoadingState, SearchField, SectionLabel, icon_button, text_button

STATE_COLORS = {
    RepoState.SYNCED.value: "success",
    RepoState.REPO_CREATED.value: "info",
    RepoState.REPO_PENDING.value: "warning",
    RepoState.LOCAL_ONLY.value: "text_subtle",
}


class Sidebar(QWidget):
    project_selected = Signal(str)
    new_project = Signal()
    clone_requested = Signal()
    import_requested = Signal()
    context_action = Signal(str, str, object)   # action, project, global pos
    filter_changed = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setObjectName("Sidebar")
        self.setMinimumWidth(252)
        self.setMaximumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["md"], SPACING["md"],
                                  SPACING["md"], SPACING["md"])
        layout.setSpacing(SPACING["sm"])

        title_row = QHBoxLayout()
        label = SectionLabel("Projects", theme)
        title_row.addWidget(label)
        title_row.addStretch(1)
        self.count = QLabel("")
        self.count.setObjectName("Subtle")
        title_row.addWidget(self.count)
        layout.addLayout(title_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(SPACING["xs"])
        self.search = SearchField("Search projects\u2026", theme)
        self.search.textChanged.connect(self._on_filter)
        search_row.addWidget(self.search, 1)
        self.add_btn = icon_button("+", theme, "New project (Ctrl+N)",
                                   self.new_project.emit)
        search_row.addWidget(self.add_btn)
        layout.addLayout(search_row)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.loading = LoadingState(6, theme)
        self.stack.addWidget(self.loading)

        self.empty = EmptyState(
            "No projects yet",
            "Create a project from a template, or clone one from GitHub.",
            "New project", theme)
        self.empty.action_triggered.connect(self.new_project.emit)
        self.stack.addWidget(self.empty)

        self.no_results = EmptyState(
            "No matches", "Nothing matches that search.", "", theme)
        self.stack.addWidget(self.no_results)

        self.list = QListWidget()
        self.list.setObjectName("SidebarList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context)
        self.list.currentItemChanged.connect(self._on_current)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.setIconSize(QSize(10, 10))
        self.list.setSpacing(1)
        self.stack.addWidget(self.list)

        quick = QHBoxLayout()
        quick.setSpacing(SPACING["xs"])
        self.clone_btn = text_button("Clone\u2026", theme, self.clone_requested.emit)
        self.import_btn = text_button("Add existing\u2026", theme,
                                      self.import_requested.emit)
        quick.addWidget(self.clone_btn)
        quick.addWidget(self.import_btn)
        quick.addStretch(1)
        layout.addLayout(quick)

        self._projects: list[dict] = []
        self._rendered: list[str] = []
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(140)
        self._debounce.timeout.connect(self._apply_filter)

    # ------------------------------------------------------------------ api

    def show_loading(self) -> None:
        self.stack.setCurrentWidget(self.loading)
        self.loading.start()

    def set_projects(self, projects: list[dict[str, Any]], query: str = "") -> None:
        self._projects = projects
        self.loading.stop()
        self.count.setText(str(len(projects)) if projects else "")
        if not projects:
            self.empty.set_state(
                "No projects yet",
                "Create a project from a template, or clone one from GitHub.",
                "New project")
            self.stack.setCurrentWidget(self.empty)
            return
        self._apply_filter(query)

    def _on_filter(self, _text: str) -> None:
        self._debounce.start()
        self.filter_changed.emit(self.search.text())

    def _apply_filter(self, query: str | None = None) -> None:
        text = (query if query is not None else self.search.text()).lower().strip()
        selected = self.current_project()
        self.list.blockSignals(True)
        self.list.clear()
        shown = 0
        current_row = -1
        last_group = object()
        for project in self._projects:
            haystack = " ".join([
                project.get("name", ""), project.get("notes", ""),
                project.get("group", ""), project.get("path", ""),
            ]).lower()
            if text and text not in haystack:
                continue
            group = project.get("group") or ""
            if group and group != last_group:
                header = QListWidgetItem(group)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setFont(Typography().small_strong())
                header.setForeground(QColor(self.theme["text_subtle"]))
                header.setSizeHint(QSize(0, 22))
                self.list.addItem(header)
                last_group = group
            item = QListWidgetItem(self._label_for(project))
            item.setData(Qt.ItemDataRole.UserRole, project["name"])
            item.setIcon(self._icon_for(project))
            item.setToolTip(self._tooltip_for(project))
            item.setSizeHint(QSize(0, 34))
            self.list.addItem(item)
            if project["name"] == selected:
                current_row = self.list.count() - 1
            shown += 1
        self.list.blockSignals(False)
        if shown == 0:
            self.stack.setCurrentWidget(self.no_results)
            return
        self.stack.setCurrentWidget(self.list)
        if current_row >= 0:
            self.list.setCurrentRow(current_row)

    def _label_for(self, project: dict[str, Any]) -> str:
        name = project.get("name", "")
        suffix = ""
        if project.get("missing"):
            suffix = "   (missing)"
        elif project.get("visibility") == "public":
            suffix = "   public"
        prefix = "\u2605 " if project.get("pinned") else ""
        return f"{prefix}{name}{suffix}"

    def _icon_for(self, project: dict[str, Any]) -> QIcon:
        key = STATE_COLORS.get(project.get("repo_state", ""), "text_subtle")
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.GlobalColor.transparent)
        from PySide6.QtGui import QPainter

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#c9271f" if project.get("missing")
                                else self.theme[key]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, 8, 8)
        painter.end()
        return QIcon(pixmap)

    def _tooltip_for(self, project: dict[str, Any]) -> str:
        lines = [project.get("name", ""), project.get("path", "")]
        if project.get("repo_url"):
            lines.append(project["repo_url"])
        if project.get("missing"):
            lines.append("\u26a0 Folder is missing \u2014 right-click to relink.")
        state = {
            RepoState.SYNCED.value: "Synced with GitHub",
            RepoState.REPO_CREATED.value: "Repository linked",
            RepoState.REPO_PENDING.value: "Repository setup incomplete",
            RepoState.LOCAL_ONLY.value: "Local only",
        }.get(project.get("repo_state", ""), "")
        if state:
            lines.append(state)
        return "\n".join(line for line in lines if line)

    def current_project(self) -> str:
        item = self.list.currentItem()
        if item is None:
            return ""
        return item.data(Qt.ItemDataRole.UserRole) or ""

    def select(self, name: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.list.setCurrentRow(row)
                return

    def _on_current(self, current: QListWidgetItem | None,
                    _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        if name:
            self.project_selected.emit(name)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.context_action.emit("editor", name, None)

    def _on_context(self, pos) -> None:
        item = self.list.itemAt(pos)
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        menu = QMenu(self)
        if name:
            menu.addAction("Open in editor",
                           lambda: self.context_action.emit("editor", name, None))
            menu.addAction("Open folder",
                           lambda: self.context_action.emit("open_folder", name, None))
            menu.addSeparator()
            menu.addAction("Pin / unpin",
                           lambda: self.context_action.emit("pin", name, None))
            menu.addAction("Set group\u2026",
                           lambda: self.context_action.emit("group", name, None))
            menu.addSeparator()
            menu.addAction("Relink folder\u2026",
                           lambda: self.context_action.emit("relink", name, None))
            menu.addAction("Rename\u2026",
                           lambda: self.context_action.emit("rename", name, None))
            menu.addAction("Delete\u2026",
                           lambda: self.context_action.emit("delete", name, None))
        else:
            menu.addAction("New project\u2026", self.new_project.emit)
            menu.addAction("Clone from GitHub\u2026", self.clone_requested.emit)
            menu.addAction("Add existing folder\u2026", self.import_requested.emit)
        menu.exec(self.list.viewport().mapToGlobal(pos))

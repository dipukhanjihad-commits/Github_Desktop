"""Command palette (Ctrl+K) — actions, projects and templates (N7, N14).

Fuzzy subsequence matching keeps the list responsive; each row shows its
shortcut so the palette doubles as a discoverability surface (M20).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QVBoxLayout, QWidget,
)

from .theme import SPACING, Theme, Typography
from .widgets import KeycapLabel


@dataclass
class Command:
    title: str
    category: str
    run: Callable[[], None]
    shortcut: str = ""
    keywords: str = ""


def fuzzy_score(query: str, text: str) -> int:
    """Return a score (>0) when ``query`` is a subsequence of ``text``."""
    if not query:
        return 1
    query = query.lower()
    text_lower = text.lower()
    score = 0
    index = 0
    streak = 0
    for char in query:
        found = text_lower.find(char, index)
        if found < 0:
            return 0
        if found == index:
            streak += 1
            score += 6 + streak
        else:
            streak = 0
            score += max(1, 4 - (found - index))
        if found == 0 or text_lower[found - 1] in " ./-_":
            score += 8
        index = found + 1
    if text_lower.startswith(query):
        score += 20
    return score


class CommandPalette(QDialog):
    def __init__(self, parent: QWidget, theme: Theme,
                 commands: Iterable[Command],
                 project_names: Iterable[str] = (),
                 on_open_project: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.commands = list(commands)
        self.project_names = list(project_names)
        self.project_chosen = ""
        self._on_open_project = on_open_project
        self._results: list[Command] = []
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["md"], SPACING["md"],
                                  SPACING["md"], SPACING["sm"])
        layout.setSpacing(SPACING["sm"])

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command or project name\u2026")
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._refresh)
        layout.addWidget(self.input)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setUniformItemSizes(False)
        self.list.setMaximumHeight(360)
        self.list.itemActivated.connect(self._activate)
        layout.addWidget(self.list)

        footer = QHBoxLayout()
        hint = QLabel("Enter to run \u00b7 Esc to close")
        hint.setObjectName("Subtle")
        footer.addWidget(hint)
        footer.addStretch(1)
        keycap = KeycapLabel("Ctrl K", theme)
        footer.addWidget(keycap)
        layout.addLayout(footer)

        self.setStyleSheet(
            f"QDialog {{ background: {theme['surface']};"
            f" border: 1px solid {theme['border_strong']};"
            f" border-radius: 10px; }}")
        self._refresh("")

    # ------------------------------------------------------------------ input

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return True
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and obj is self.input:
            self._move_selection(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = self.list.currentItem()
            if item is not None:
                self._activate(item)
            return True
        if obj is self.list:
            # Typing while the list has focus should filter, not navigate.
            if event.text() and event.text().isprintable():
                self.input.setFocus()
                self.input.setText(self.input.text() + event.text())
                self.input.setCursorPosition(len(self.input.text()))
                return True
            if key == Qt.Key.Key_Backspace:
                self.input.setFocus()
                self.input.backspace()
                return True
        return super().eventFilter(obj, event)

    def _move_selection(self, step: int) -> None:
        count = self.list.count()
        if not count:
            return
        row = (self.list.currentRow() + step) % count
        self.list.setCurrentRow(row)

    # --------------------------------------------------------------- results

    def _refresh(self, query: str) -> None:
        query = (query or "").strip()
        scored: list[tuple[int, Command]] = []
        for command in self.commands:
            haystack = f"{command.category} {command.title} {command.keywords}"
            score = fuzzy_score(query, haystack)
            if score:
                scored.append((score, command))
        if query:
            for name in self.project_names:
                score = fuzzy_score(query, name)
                if score:
                    scored.append((score + 3, Command(
                        title=name, category="Project",
                        run=(lambda n=name: self._open_project(n)),
                        keywords="project open")))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        self._results = [command for _score, command in scored[:40]]
        self.list.clear()
        for command in self._results:
            item = QListWidgetItem()
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(SPACING["sm"])
            category = QLabel(command.category)
            category.setFixedWidth(86)
            category.setObjectName("Subtle")
            title = QLabel(command.title)
            title.setFont(Typography().body())
            layout.addWidget(category)
            layout.addWidget(title, 1)
            if command.shortcut:
                layout.addWidget(KeycapLabel(command.shortcut, self.theme))
            item.setSizeHint(row.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        if self._results:
            self.list.setCurrentRow(0)

    def _activate(self, item: QListWidgetItem) -> None:
        command = item.data(Qt.ItemDataRole.UserRole)
        if command is None:
            return
        self.accept()
        command.run()

    def _open_project(self, name: str) -> None:
        self.project_chosen = name
        if self._on_open_project:
            self._on_open_project(name)

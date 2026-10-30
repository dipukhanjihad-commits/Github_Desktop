"""Reusable widgets that give the app one consistent visual language.

Everything here takes the active :class:`Theme` so light/dark restyling is a
single call. Widgets avoid emoji-only signalling; labels are always readable
text (M20/S16).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPushButton, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget,
)

from .theme import SPACING, RADIUS, Theme, Typography


# ---------------------------------------------------------------------------
# Badges — one pill component for every state (S16).
# ---------------------------------------------------------------------------

TONES = {
    "neutral": ("muted_soft", "text_muted"),
    "primary": ("primary_soft", "primary"),
    "success": ("success_soft", "success"),
    "warning": ("warning_soft", "warning"),
    "danger": ("danger_soft", "danger"),
    "info": ("info_soft", "info"),
}


class Badge(QLabel):
    """A small rounded label, e.g. "Private", "3 ahead", "Conflict"."""

    def __init__(self, text: str = "", tone: str = "neutral",
                 theme: Theme | None = None, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._tone = tone
        self._theme = theme
        self.setFont(Typography().badge())
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setContentsMargins(0, 0, 0, 0)
        self.restyle(theme, tone)

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.restyle(self._theme, tone)

    def set(self, text: str, tone: str | None = None) -> None:
        self.setText(text)
        if tone:
            self.set_tone(tone)
        self.setVisible(bool(text))

    def restyle(self, theme: Theme | None, tone: str | None = None) -> None:
        if theme is None:
            return
        self._theme = theme
        tone = tone or self._tone
        bg_key, fg_key = TONES.get(tone, TONES["neutral"])
        self.setStyleSheet(
            f"QLabel {{ background: {theme[bg_key]}; color: {theme[fg_key]};"
            f" border-radius: 9px; padding: 2px 9px; }}")
        self.setMinimumHeight(20)


class StatusDot(QWidget):
    """A coloured dot used in dense rows (project list markers)."""

    def __init__(self, color: str = "#94a3b8", size: int = 9,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self.setFixedSize(size + 6, size + 6)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(self._color)
        painter.setPen(Qt.PenStyle.NoPen)
        offset = (self.width() - self._size) // 2
        painter.drawEllipse(offset, offset, self._size, self._size)


class Separator(QFrame):
    def __init__(self, theme: Theme, horizontal: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Separator")
        if horizontal:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"background: {theme['border']};")


class SectionLabel(QLabel):
    """Uppercase, letter-spaced group heading."""

    def __init__(self, text: str, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.setFont(Typography().section())
        self.setStyleSheet(f"color: {theme['text_subtle']};")


# ---------------------------------------------------------------------------
# Empty & loading states (M19)
# ---------------------------------------------------------------------------

class EmptyState(QWidget):
    """Centred illustration-free placeholder with an action."""

    action_triggered = Signal()

    def __init__(self, title: str, message: str = "", action_text: str = "",
                 theme: Theme | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(SPACING["sm"])
        layout.addStretch(1)

        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setFixedHeight(56)
        layout.addWidget(self.icon)

        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setFont(Typography().subtitle())
        layout.addWidget(self.title)

        self.message = QLabel(message)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.button = QPushButton(action_text)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.action_triggered.emit)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.button)
        row.addStretch(1)
        layout.addSpacing(SPACING["sm"])
        layout.addLayout(row)
        self.button.setVisible(bool(action_text))
        layout.addStretch(1)
        self.restyle(theme)

    def set_state(self, title: str, message: str = "",
                  action_text: str = "") -> None:
        self.title.setText(title)
        self.message.setText(message)
        self.button.setText(action_text)
        self.button.setVisible(bool(action_text))

    def restyle(self, theme: Theme | None) -> None:
        if theme is None:
            return
        self._theme = theme
        self.icon.setText("\u25cb")
        self.icon.setStyleSheet(
            f"color: {theme['border_strong']}; font-size: 46px;")
        self.title.setStyleSheet(f"color: {theme['text']};")
        self.message.setStyleSheet(
            f"color: {theme['text_muted']}; font-size: 9.5pt;")
        self.button.setStyleSheet(_ghost_button_qss(theme))


class LoadingState(QWidget):
    """Pulsing skeleton rows shown while a list loads (M19/N16)."""

    def __init__(self, rows: int = 4, theme: Theme | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = rows
        self._phase = 0.0
        self._theme = theme
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start(40)

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.05) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        if not self._theme:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QColor(self._theme["muted_soft"])
        highlight = QColor(self._theme["border_strong"])
        base.setAlphaF(0.9)
        height = 34
        gap = 8
        for index in range(self._rows):
            y = index * (height + gap)
            width = int(self.width() * (0.85 - 0.05 * (index % 3)))
            wave = max(0.0, 1.0 - abs(self._phase - (index * 0.12 % 1.0)) * 4)
            color = QColor(int(base.red() + (highlight.red() - base.red()) * wave),
                           int(base.green() + (highlight.green() - base.green()) * wave),
                           int(base.blue() + (highlight.blue() - base.blue()) * wave))
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(0, y, width, height, RADIUS["sm"], RADIUS["sm"])


# ---------------------------------------------------------------------------
# Toasts — non-blocking feedback (M18/S18)
# ---------------------------------------------------------------------------

class Toast(QFrame):
    """Transient notification anchored to a parent widget."""

    def __init__(self, parent: QWidget, theme: Theme, message: str,
                 tone: str = "neutral", timeout: int = 3800) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        bg_key, fg_key = TONES.get(tone, TONES["neutral"])
        self.setStyleSheet(
            f"QFrame#Toast {{ background: {theme[bg_key]};"
            f" border: 1px solid {theme[fg_key]}; border-radius: {RADIUS['md']}px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        label = QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme[fg_key]}; border: none;")
        label.setFont(Typography().body())
        layout.addWidget(label)
        self.setMaximumWidth(460)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        QTimer.singleShot(timeout, self._dismiss)

    def _dismiss(self) -> None:
        fade = QPropertyAnimation(self._effect, b"opacity", self)
        fade.setDuration(220)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.finished.connect(self.deleteLater)
        fade.start()
        self._closing = fade


class ToastHost:
    """Stacks toasts in the bottom-right corner of a window."""

    def __init__(self, window: QWidget, theme: Theme) -> None:
        self.window = window
        self.theme = theme
        self._toasts: list[Toast] = []

    def show(self, message: str, tone: str = "neutral") -> None:
        toast = Toast(self.window, self.theme, message, tone)
        toast.adjustSize()
        self._toasts = [t for t in self._toasts if t.isVisible()]
        self._toasts.append(toast)
        self._layout()
        toast.show()
        QTimer.singleShot(4000, self._layout)

    def _layout(self) -> None:
        margin = 18
        offset = 0
        for toast in reversed(self._toasts):
            if not toast.isVisible() and not toast.isHidden():
                pass
            toast.adjustSize()
            x = self.window.width() - toast.width() - margin
            y = self.window.height() - toast.height() - margin - offset
            toast.move(max(margin, x), max(margin, y))
            toast.raise_()
            offset += toast.height() + 8


# ---------------------------------------------------------------------------
# Cards, form builder, buttons
# ---------------------------------------------------------------------------

class Card(QFrame):
    """Surface container with border + radius (replaces ad-hoc Frames)."""

    def __init__(self, theme: Theme, padding: int = SPACING["lg"],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(SPACING["sm"])
        self.restyle(theme)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        return self._layout

    def restyle(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"QFrame#Card {{ background: {theme['surface']};"
            f" border: 1px solid {theme['border']};"
            f" border-radius: {RADIUS['md']}px; }}")


class PageHeader(QWidget):
    """Title + subtitle + right-aligned actions, used by each page."""

    def __init__(self, title: str, subtitle: str = "", theme: Theme | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["md"])
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setFont(Typography().title())
        self.subtitle = QLabel(subtitle)
        self.subtitle.setFont(Typography().small())
        self.subtitle.setWordWrap(True)
        text_col.addWidget(self.title)
        text_col.addWidget(self.subtitle)
        layout.addLayout(text_col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(SPACING["sm"])
        layout.addLayout(self.actions)
        self.restyle(theme)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)

    def restyle(self, theme: Theme | None) -> None:
        if theme is None:
            return
        self.title.setStyleSheet(f"color: {theme['text']};")
        self.subtitle.setStyleSheet(f"color: {theme['text_muted']};")


class FormBuilder:
    """Consistent label/field grid so dialogs stop hand-placing rows (M14)."""

    def __init__(self, parent_layout: QVBoxLayout, theme: Theme,
                 label_width: int = 132) -> None:
        from PySide6.QtWidgets import QGridLayout

        self.theme = theme
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(SPACING["md"])
        self.grid.setVerticalSpacing(SPACING["sm"])
        self.grid.setColumnMinimumWidth(0, label_width)
        self.grid.setColumnStretch(1, 1)
        self._row = 0
        parent_layout.addLayout(self.grid)

    def _label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(Typography().body())
        label.setStyleSheet(f"color: {self.theme['text_muted']};")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    def add_row(self, text: str, *widgets: QWidget,
                hint: str = "") -> int:
        row = self._row
        self.grid.addWidget(self._label(text), row, 0)
        from PySide6.QtWidgets import QHBoxLayout, QWidget as _W

        if len(widgets) == 1:
            self.grid.addWidget(widgets[0], row, 1, 1, 2)
        else:
            holder = _W()
            hbox = QHBoxLayout(holder)
            hbox.setContentsMargins(0, 0, 0, 0)
            hbox.setSpacing(SPACING["sm"])
            for widget in widgets:
                hbox.addWidget(widget)
            self.grid.addWidget(holder, row, 1, 1, 2)
        self._row += 1
        if hint:
            self.add_note(hint)
        return row

    def add_full(self, widget: QWidget, *, span: int = 3) -> None:
        self.grid.addWidget(widget, self._row, 0, 1, span)
        self._row += 1

    def add_note(self, text: str, tone: str = "muted") -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(Typography().small())
        color = self.theme["text_muted"] if tone == "muted" else self.theme[tone]
        label.setStyleSheet(f"color: {color};")
        self.grid.addWidget(label, self._row, 1, 1, 2)
        self._row += 1
        return label

    def add_field_error(self, text: str = "") -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(Typography().small())
        label.setStyleSheet(f"color: {self.theme['danger']};")
        label.setVisible(bool(text))
        self.grid.addWidget(label, self._row, 1, 1, 2)
        self._row += 1
        return label

    def add_spacer(self, height: int = SPACING["sm"]) -> None:
        self.grid.addItem(
            QSpacerItem(1, height, QSizePolicy.Policy.Minimum,
                        QSizePolicy.Policy.Fixed), self._row, 0)
        self._row += 1


def _ghost_button_qss(theme: Theme) -> str:
    return (
        f"QPushButton {{ background: transparent; color: {theme['primary']};"
        f" border: 1px solid {theme['border']}; border-radius: {RADIUS['sm']}px;"
        f" padding: 6px 14px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {theme['primary_soft']};"
        f" border-color: {theme['primary']}; }}")


def _text_button_qss(theme: Theme) -> str:
    return (
        f"QPushButton {{ background: transparent; color: {theme['text_muted']};"
        f" border: none; padding: 4px 6px; }}"
        f"QPushButton:hover {{ color: {theme['primary']}; }}")


def text_button(text: str, theme: Theme,
                on_click: Callable[[], None] | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFlat(True)
    button.setFont(Typography().small())
    button.setStyleSheet(_text_button_qss(theme))
    if on_click:
        button.clicked.connect(on_click)
    return button


def icon_button(text: str, theme: Theme, tooltip: str = "",
                on_click: Callable[[], None] | None = None) -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(QSize(30, 30))
    button.setStyleSheet(
        f"QPushButton {{ background: transparent; color: {theme['text_muted']};"
        f" border: 1px solid transparent; border-radius: {RADIUS['sm']}px; }}"
        f"QPushButton:hover {{ background: {theme['surface_hover']};"
        f" border-color: {theme['border']}; color: {theme['text']}; }}")
    if on_click:
        button.clicked.connect(on_click)
    return button


class SearchField(QLineEdit):
    """Search entry with placeholder + clear button and debounce-friendly API."""

    def __init__(self, placeholder: str = "Search\u2026",
                 theme: Theme | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setMinimumHeight(30)
        self.restyle(theme)

    def restyle(self, theme: Theme | None) -> None:
        if theme is None:
            return
        self.setStyleSheet(
            f"QLineEdit {{ background: {theme['surface']};"
            f" border: 1px solid {theme['border']};"
            f" border-radius: {RADIUS['sm']}px; padding: 4px 8px;"
            f" color: {theme['text']}; }}"
            f"QLineEdit:focus {{ border-color: {theme['primary']}; }}")


class KeycapLabel(QLabel):
    """Small rendered keyboard shortcut, e.g. Ctrl+K (M20)."""

    def __init__(self, keys: str, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__(keys, parent)
        self.setFont(Typography().font(8.5, QFont.Weight.DemiBold))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(20)
        self.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet(
            f"QLabel {{ background: {theme['bg_alt']};"
            f" color: {theme['text_muted']}; border: 1px solid {theme['border']};"
            f" border-radius: 4px; padding: 2px 6px; }}")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().sizeHint()
        return QSize(hint.width() + 4, max(20, hint.height()))


def hline(theme: Theme) -> QFrame:
    return Separator(theme, True)


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        else:
            child = item.layout()
            if child is not None:
                clear_layout(child)


def elide(text: str, length: int) -> str:
    return text if len(text) <= length else text[: max(0, length - 1)] + "\u2026"


def paint_path(painter: QPainter, radius: float, color: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(painter.viewport().rect(), radius, radius)
    painter.fillPath(path, color)
    painter.setPen(QPen(color))

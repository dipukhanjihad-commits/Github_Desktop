"""Shared dialog scaffolding (M14, M18, M20, M23).

All dialogs get: consistent header/body/footer layout, a primary and optional
destructive button, Enter/Escape handling, inline field validation and a
"confirm destructive" gate.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from ..theme import SPACING, Theme, Typography
from ..widgets import Separator


class AppDialog(QDialog):
    """Base class for every dialog in the app."""

    def __init__(self, parent: QWidget | None, title: str, theme: Theme,
                 subtitle: str = "", width: int = 520,
                 icon_text: str = "") -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(width)
        self._dirty_check: Callable[[], bool] | None = None
        self._confirmed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACING["xl"], SPACING["lg"],
                                SPACING["xl"], SPACING["lg"])
        root.setSpacing(SPACING["md"])

        header_row = QHBoxLayout()
        header_row.setSpacing(SPACING["md"])
        if icon_text:
            icon = QLabel(icon_text)
            icon.setFont(Typography().font(18))
            icon.setAlignment(Qt.AlignmentFlag.AlignTop)
            header_row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("Title")
        self.title_label.setFont(Typography().font(16, QFont.Weight.DemiBold))
        self.title_label.setWordWrap(True)
        text_col.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("Subtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        text_col.addWidget(self.subtitle_label)
        header_row.addLayout(text_col, 1)
        root.addLayout(header_row)

        self.body = QVBoxLayout()
        self.body.setSpacing(SPACING["sm"])
        root.addLayout(self.body, 1)

        root.addWidget(Separator(theme))

        self.footer = QHBoxLayout()
        self.footer.setSpacing(SPACING["sm"])
        self._footer_left = QHBoxLayout()
        self._footer_right = QHBoxLayout()
        self.footer.addLayout(self._footer_left)
        self.footer.addStretch(1)
        self.footer.addLayout(self._footer_right)
        root.addLayout(self.footer)

        self._escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape.activated.connect(self._on_escape)
        self._enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        self._enter.activated.connect(self._on_enter)
        self._enter2 = QShortcut(QKeySequence(Qt.Key.Key_Enter), self)
        self._enter2.activated.connect(self._on_enter)

    # ------------------------------------------------------------------ api

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def add_button(self, text: str, *, primary: bool = False,
                   danger: bool = False, on_click: Callable[[], None] | None = None,
                   left: bool = False, enabled: bool = True) -> QPushButton:
        button = QPushButton(text)
        if danger:
            button.setObjectName("Danger")
        elif primary:
            button.setObjectName("Primary")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setDefault(bool(primary))
        button.setEnabled(enabled)
        if on_click:
            button.clicked.connect(lambda: on_click())
        (self._footer_left if left else self._footer_right).addWidget(button)
        return button

    def add_cancel(self, text: str = "Cancel") -> QPushButton:
        return self.add_button(text, on_click=self.reject)

    def add_primary(self, text: str,
                    on_click: Callable[[], None]) -> QPushButton:
        return self.add_button(text, primary=True, on_click=on_click)

    def set_dirty_check(self, fn: Callable[[], bool]) -> None:
        self._dirty_check = fn

    def confirm_destructive(self, title: str, message: str,
                            checkbox_text: str = "",
                            confirm_text: str = "Delete") -> bool:
        """Explicit confirmation for destructive acts (M4).

        Honours the ``confirm_destructive`` setting. Typed confirmations
        (force push) deliberately do not use this gate.
        """
        memory = getattr(getattr(self, "app", None), "memory", None)
        if memory is not None and not memory.get_setting(
                "confirm_destructive", True):
            return True
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        headline = QLabel(title)
        headline.setFont(Typography().subtitle())
        layout.addWidget(headline)
        body = QLabel(message)
        body.setWordWrap(True)
        layout.addWidget(body)
        check = None
        if checkbox_text:
            check = QCheckBox(checkbox_text)
            layout.addWidget(check)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        ok = QPushButton(confirm_text)
        ok.setObjectName("Danger")
        ok.clicked.connect(dialog.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if accepted and check is not None:
            return check.isChecked()
        return accepted

    # ---------------------------------------------------------------- hooks

    def validate(self) -> tuple[bool, str]:
        return True, ""

    def _on_enter(self) -> None:
        for widget in (self.focusWidget(),):
            if isinstance(widget, QLineEdit) or widget is None:
                self._submit()

    def _submit(self) -> None:
        ok, message = self.validate()
        if not ok:
            self._show_inline_error(message)
            return
        self.accept()

    def _show_inline_error(self, message: str) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, "Check your input", message,
                          QMessageBox.StandardButton.Ok, self)
        box.exec()

    def _on_escape(self) -> None:
        if self._dirty_check and self._dirty_check():
            answer = QMessageBox.question(
                self, "Discard changes?",
                "You have unsaved changes. Close without saving?",
                QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        self.reject()


class InfoDialog(AppDialog):
    def __init__(self, parent, title: str, message: str, theme: Theme,
                 detail: str = "") -> None:
        super().__init__(parent, title, theme)
        label = QLabel(message)
        label.setWordWrap(True)
        self.body.addWidget(label)
        if detail:
            from PySide6.QtWidgets import QPlainTextEdit

            viewer = QPlainTextEdit(detail)
            viewer.setObjectName("Code")
            viewer.setReadOnly(True)
            viewer.setMinimumHeight(180)
            self.body.addWidget(viewer)
        self.add_cancel("Close")


class ConfirmDialog(AppDialog):
    def __init__(self, parent, title: str, message: str, theme: Theme,
                 confirm_text: str = "OK", danger: bool = False,
                 checkbox_text: str = "") -> None:
        super().__init__(parent, title, theme)
        label = QLabel(message)
        label.setWordWrap(True)
        self.body.addWidget(label)
        self.check = None
        if checkbox_text:
            self.check = QCheckBox(checkbox_text)
            self.body.addWidget(self.check)
        self.add_cancel()
        self.add_button(confirm_text, primary=not danger, danger=danger,
                        on_click=self.accept)


def open_external(parent, title: str, text: str, theme: Theme) -> None:
    dialog = InfoDialog(parent, title, "", theme, detail=text)
    dialog.setMinimumWidth(760)
    dialog.exec()


class ErrorDialog(AppDialog):
    """Structured error presentation with a copy button (M6)."""

    def __init__(self, parent, exc: BaseException, theme: Theme,
                 context: str = "") -> None:
        from ...errors import ScaffolderError

        is_domain = isinstance(exc, ScaffolderError)
        title = exc.title if is_domain else "Unexpected error"
        super().__init__(parent, title, theme)
        self.setMinimumWidth(560)

        message = QLabel(str(exc) if is_domain else f"{type(exc).__name__}: {exc}")
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(message)

        hint = getattr(exc, "hint", "") if is_domain else ""
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("Muted")
            hint_label.setWordWrap(True)
            hint_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            self.body.addWidget(hint_label)

        if context:
            ctx = QLabel(context)
            ctx.setObjectName("Subtle")
            ctx.setWordWrap(True)
            self.body.addWidget(ctx)

        trace = getattr(exc, "_traceback", "")
        if trace:
            from PySide6.QtWidgets import QPlainTextEdit

            viewer = QPlainTextEdit(trace)
            viewer.setObjectName("Code")
            viewer.setReadOnly(True)
            viewer.setMinimumHeight(150)
            self.body.addWidget(viewer)

        self.add_button("Copy details", left=True,
                        on_click=lambda: self._copy_details(exc, trace, hint))
        self.add_button("Close", primary=True, on_click=self.accept)

    def _copy_details(self, exc: BaseException, trace: str, hint: str) -> None:
        from PySide6.QtWidgets import QApplication

        parts = [f"{type(exc).__name__}: {exc}"]
        if hint:
            parts.append(f"Hint: {hint}")
        if trace:
            parts.append(trace)
        QApplication.clipboard().setText("\n\n".join(parts))


class TokenUpdateDialog(AppDialog):
    """Re-enter a token when it is rejected (M12)."""

    def __init__(self, parent, account: str, theme: Theme,
                 on_submit: Callable[[str], None]) -> None:
        super().__init__(parent, f"Update token for {account}", theme,
                         subtitle="GitHub rejected the stored token. Paste a "
                                  "replacement personal access token.")
        self.account = account
        self._on_submit = on_submit
        self.token_var = QLineEdit()
        self.token_var.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_var.setPlaceholderText("ghp_\u2026 or github_pat_\u2026")
        self.body.addWidget(self.token_var)
        note = QLabel("Needs the repo, read:user and read:org scopes.")
        note.setObjectName("Subtle")
        self.body.addWidget(note)
        self.add_cancel()
        self.add_primary("Update token", self._submit_token)

    def _submit_token(self) -> None:
        token = self.token_var.text().strip()
        if not token:
            self._show_inline_error("Enter a token first.")
            return
        self._on_submit(token)

    def _on_enter(self) -> None:
        self._submit_token()


class PasswordPrompt(AppDialog):
    def __init__(self, parent, title: str, message: str, theme: Theme,
                 placeholder: str = "") -> None:
        super().__init__(parent, title, theme, subtitle=message)
        self.value = QLineEdit()
        self.value.setPlaceholderText(placeholder)
        self.body.addWidget(self.value)
        self.add_cancel()
        self.add_primary("OK", self.accept)

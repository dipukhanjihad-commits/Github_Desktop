"""Qt application bootstrap: HiDPI, theme, services and main window."""

from __future__ import annotations

import sys
import traceback
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import __app_name__, __version__
from ..errors import ScaffolderError
from . import platform as ui_platform
from .main_window import MainWindow
from .services import Services
from .theme import Typography, build_theme
from .styles import build_stylesheet


def _install_excepthook() -> None:
    """Surface unexpected exceptions in a dialog instead of dying silently."""

    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        app = QApplication.instance()
        if app is None:
            sys.stderr.write(detail)
            return
        box = QMessageBox(QMessageBox.Icon.Critical, "Unexpected error",
                          f"{exc_type.__name__}: {exc_value}", parent=None)
        box.setDetailedText(detail)
        box.exec()

    sys.excepthook = hook


def create_application(argv: Sequence[str] | None = None) -> tuple[QApplication, Services]:
    # These must be set before the QApplication exists.
    ui_platform.set_scale_policy()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication.instance() or QApplication(list(argv or sys.argv))
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("ProjectScaffolder")
    ui_platform.configure_high_dpi(app)
    services = Services.bootstrap()
    theme = build_theme(services.memory.get_setting("theme", "system"))
    QApplication.instance().setStyleSheet(build_stylesheet(theme))
    QApplication.instance().setFont(Typography().body())
    return app, services


def run(argv: Sequence[str] | None = None) -> int:
    app, services = create_application(argv)
    _install_excepthook()
    guard = ui_platform.SingleInstanceGuard()
    if not guard.acquire():
        QMessageBox.information(
            None, __app_name__,
            "Project Scaffolder is already running.")
        return 0
    try:
        window = MainWindow(services)
        window.show()
        if services.load_warnings:
            window.toast_warn(services.load_warnings[0])
        return app.exec()
    except ScaffolderError as exc:
        QMessageBox.critical(None, exc.title, str(exc))
        return 1
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(run())

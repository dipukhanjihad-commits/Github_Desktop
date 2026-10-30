"""Reproduce the reported crash path in a real window, then verify no error."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from scaffolder_app.ui.main_window import MainWindow  # noqa: E402
from scaffolder_app.ui.services import Services  # noqa: E402
from scaffolder_app.ui.styles import build_stylesheet  # noqa: E402
from scaffolder_app.ui.theme import Typography, build_theme  # noqa: E402

errors: list[str] = []


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="backtrace-"))
    app = QApplication.instance() or QApplication(sys.argv)
    previous_hook = sys.excepthook

    def hook(exc_type, exc, tb):
        errors.append(f"{exc_type.__name__}: {exc}")
        previous_hook(exc_type, exc, tb)

    sys.excepthook = hook

    services = Services.bootstrap(root=root, use_legacy=False)
    theme = build_theme("light")
    app.setStyleSheet(build_stylesheet(theme))
    app.setFont(Typography().body())

    window = MainWindow(services)
    window.show()
    app.processEvents()

    # Exactly the sequence in the report: many notifications in a row, with
    # event-loop turns between them so earlier toasts get destroyed.
    for index in range(10):
        window.toast_ok(f"Token updated for 'acct{index}'")
        app.processEvents()

    # Let the dismiss timers fire and the C++ objects be deleted.
    QTimer.singleShot(4200, app.quit)
    app.exec()
    for _ in range(6):
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

    # Interact again after everything was destroyed.
    for index in range(5):
        window.toast_warn(f"message after cleanup {index}")
        app.processEvents()

    # Also exercise the theme-switch path that rebuilt the host.
    window.toggle_theme()
    app.processEvents()
    window.toast_ok("after theme switch")
    app.processEvents()

    window.close()
    app.processEvents()

    if errors:
        print("FAILED — exceptions were raised:")
        for item in errors:
            print(f"  {item}")
        return 1
    print("no exceptions raised; toast lifecycle stable")
    print("BACKTRACE SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

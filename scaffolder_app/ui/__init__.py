"""UI layer (PySide6). Importing this package does not create a QApplication."""

from __future__ import annotations

__all__ = ["Services", "MainWindow"]


def __getattr__(name: str):
    if name == "Services":
        from .services import Services

        return Services
    if name == "MainWindow":
        from .main_window import MainWindow

        return MainWindow
    raise AttributeError(name)

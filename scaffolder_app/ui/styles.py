"""Global stylesheet generated from theme tokens (M14/M16).

A single QSS string keeps every control consistent; per-widget inline styles
are reserved for dynamic states (badges, diffs). Regenerated on theme change.
"""

from __future__ import annotations

from .theme import RADIUS, Theme, Typography


def build_stylesheet(theme: Theme) -> str:
    t = theme.c
    ui_family = Typography().family
    mono = Typography().mono_family
    return f"""
    * {{
        font-family: "{ui_family}";
        outline: none;
    }}
    QWidget {{
        background: {t['bg']};
        color: {t['text']};
        font-size: 10pt;
    }}
    QMainWindow, QDialog {{ background: {t['bg']}; }}

    /* ---------------------------------------------------------------- text */
    QLabel {{ background: transparent; color: {t['text']}; }}
    QLabel#Title {{ font-size: 19pt; font-weight: 600; }}
    QLabel#Subtitle {{ font-size: 10pt; color: {t['text_muted']}; }}
    QLabel#Muted {{ color: {t['text_muted']}; }}
    QLabel#Subtle {{ color: {t['text_subtle']}; font-size: 9pt; }}
    QLabel#Error {{ color: {t['danger']}; font-size: 9pt; }}
    QLabel#Warning {{ color: {t['warning']}; font-size: 9pt; }}
    QLabel#Mono {{ font-family: "{mono}"; font-size: 9.5pt; }}

    /* ------------------------------------------------------------ surfaces */
    QFrame#Card {{
        background: {t['surface']};
        border: 1px solid {t['border']};
        border-radius: {RADIUS['md']}px;
    }}
    QFrame#Sidebar {{
        background: {t['surface']};
        border-right: 1px solid {t['border']};
    }}
    QFrame#Toolbar {{
        background: {t['surface']};
        border-bottom: 1px solid {t['border']};
    }}
    QFrame#StatusBar {{
        background: {t['bg_alt']};
        border-top: 1px solid {t['border']};
    }}
    QFrame#Banner {{
        background: {t['warning_soft']};
        border: 1px solid {t['warning']};
        border-radius: {RADIUS['sm']}px;
    }}
    QFrame#BannerDanger {{
        background: {t['danger_soft']};
        border: 1px solid {t['danger']};
        border-radius: {RADIUS['sm']}px;
    }}

    /* ------------------------------------------------------------- buttons */
    QPushButton {{
        background: {t['surface']};
        color: {t['text']};
        border: 1px solid {t['border_strong']};
        border-radius: {RADIUS['sm']}px;
        padding: 6px 14px;
        font-weight: 500;
        min-height: 18px;
    }}
    QPushButton:hover {{ background: {t['surface_hover']};
        border-color: {t['text_subtle']}; }}
    QPushButton:pressed {{ background: {t['surface_active']}; }}
    QPushButton:disabled {{ color: {t['text_subtle']};
        border-color: {t['border']}; background: {t['bg_alt']}; }}
    QPushButton:focus {{ border-color: {t['primary']}; }}

    QPushButton#Primary {{
        background: {t['primary']};
        color: {t['on_primary']};
        border: 1px solid {t['primary']};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background: {t['primary_hover']}; }}
    QPushButton#Primary:pressed {{ background: {t['primary_active']}; }}
    QPushButton#Primary:disabled {{ background: {t['border']};
        border-color: {t['border']}; color: {t['text_subtle']}; }}

    QPushButton#Danger {{
        background: {t['danger']};
        color: #ffffff;
        border: 1px solid {t['danger']};
        font-weight: 600;
    }}
    QPushButton#Danger:hover {{ background: #a81f18; }}
    QPushButton#Danger:disabled {{ background: {t['bg_alt']};
        border-color: {t['border']}; color: {t['text_subtle']}; }}

    QPushButton#Ghost {{
        background: transparent;
        color: {t['primary']};
        border: 1px solid {t['border']};
        font-weight: 600;
    }}
    QPushButton#Ghost:hover {{ background: {t['primary_soft']};
        border-color: {t['primary']}; }}

    QPushButton#Flat {{
        background: transparent;
        border: 1px solid transparent;
        color: {t['text_muted']};
        padding: 4px 8px;
    }}
    QPushButton#Flat:hover {{ color: {t['primary']};
        background: {t['surface_hover']}; }}

    QPushButton#Link {{
        background: transparent; border: none; padding: 0;
        color: {t['primary']}; font-weight: 500; text-align: left;
    }}
    QPushButton#Link:hover {{ color: {t['primary_hover']}; }}

    /* -------------------------------------------------------------- inputs */
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
        background: {t['surface']};
        color: {t['text']};
        border: 1px solid {t['border_strong']};
        border-radius: {RADIUS['sm']}px;
        padding: 5px 8px;
        selection-background-color: {t['selection']};
        selection-color: {t['text']};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
    QSpinBox:focus, QComboBox:focus {{ border: 1px solid {t['primary']}; }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
    QComboBox:disabled {{ background: {t['bg_alt']}; color: {t['text_subtle']}; }}
    QLineEdit[invalid="true"] {{ border: 1px solid {t['danger']}; }}
    QLineEdit#Search {{ padding-left: 8px; }}

    QTextEdit#Code, QPlainTextEdit#Code, QPlainTextEdit#Log {{
        font-family: "{mono}";
        font-size: 9.5pt;
        background: {t['surface']};
        border: 1px solid {t['border']};
        border-radius: {RADIUS['sm']}px;
        padding: 6px;
    }}
    QPlainTextEdit#Log {{ background: {t['bg_alt']}; }}

    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {t['surface']};
        border: 1px solid {t['border_strong']};
        selection-background-color: {t['primary_soft']};
        selection-color: {t['text']};
        padding: 4px;
        outline: none;
    }}

    QCheckBox, QRadioButton {{ background: transparent; spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 15px; height: 15px;
    }}
    QCheckBox::indicator:unchecked {{
        border: 1px solid {t['border_strong']}; border-radius: 3px;
        background: {t['surface']};
    }}
    QCheckBox::indicator:checked {{
        border: 1px solid {t['primary']}; border-radius: 3px;
        background: {t['primary']};
    }}
    QRadioButton::indicator:unchecked {{
        border: 1px solid {t['border_strong']}; border-radius: 8px;
        background: {t['surface']};
    }}
    QRadioButton::indicator:checked {{
        border: 4px solid {t['primary']}; border-radius: 8px;
        background: {t['surface']};
    }}

    /* ---------------------------------------------------------------- lists */
    QListView, QListWidget, QTreeView, QTableView {{
        background: {t['surface']};
        border: 1px solid {t['border']};
        border-radius: {RADIUS['sm']}px;
        outline: none;
        alternate-background-color: {t['bg_alt']};
    }}
    /* Sidebar list floats on the sidebar surface with no frame. */
    QListWidget#SidebarList {{
        background: transparent;
        border: none;
        border-radius: 0;
        padding: 2px 0;
    }}
    QListWidget#SidebarList::item {{
        border-radius: 6px;
        margin: 1px 2px;
    }}
    QListView::item, QListWidget::item, QTreeView::item {{
        padding: 5px 6px;
        border: none;
        border-radius: 4px;
    }}
    QListView::item:hover, QListWidget::item:hover, QTreeView::item:hover {{
        background: {t['surface_hover']};
    }}
    QListView::item:selected, QListWidget::item:selected,
    QTreeView::item:selected {{
        background: {t['selection']};
        color: {t['text']};
    }}
    QTreeView::item {{ border-bottom: 1px solid {t['border']}; }}
    QHeaderView::section {{
        background: {t['bg_alt']};
        color: {t['text_muted']};
        border: none;
        border-bottom: 1px solid {t['border']};
        padding: 6px 8px;
        font-size: 9pt;
        font-weight: 600;
    }}
    QTableView {{ gridline-color: {t['border']}; }}

    /* ------------------------------------------------------------- scroll */
    QScrollBar:vertical {{
        background: transparent; width: 11px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t['border_strong']}; border-radius: 5px; min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t['text_subtle']}; }}
    QScrollBar:horizontal {{
        background: transparent; height: 11px; margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t['border_strong']}; border-radius: 5px; min-width: 28px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    /* --------------------------------------------------------------- tabs */
    QTabWidget::pane {{
        border: none;
        background: {t['bg']};
    }}
    QTabBar::tab {{
        background: transparent;
        color: {t['text_muted']};
        padding: 8px 16px;
        margin-right: 2px;
        border: none;
        border-bottom: 2px solid transparent;
        font-weight: 500;
    }}
    QTabBar::tab:hover {{ color: {t['text']}; }}
    QTabBar::tab:selected {{
        color: {t['primary']};
        border-bottom: 2px solid {t['primary']};
        font-weight: 600;
    }}

    /* ------------------------------------------------------------- menus */
    QMenu {{
        background: {t['surface']};
        border: 1px solid {t['border_strong']};
        border-radius: {RADIUS['sm']}px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 26px 6px 14px;
        border-radius: 4px;
        color: {t['text']};
    }}
    QMenu::item:selected {{ background: {t['primary_soft']}; color: {t['text']}; }}
    QMenu::item:disabled {{ color: {t['text_subtle']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
    QMenuBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
    QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
    QMenuBar::item:selected {{ background: {t['surface_hover']};
        border-radius: 4px; }}

    QToolTip {{
        background: {t['surface']};
        color: {t['text']};
        border: 1px solid {t['border_strong']};
        padding: 4px 7px;
        border-radius: 4px;
    }}

    /* ---------------------------------------------------------- progress */
    QProgressBar {{
        background: {t['bg_alt']};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background: {t['primary']};
        border-radius: 3px;
    }}

    QSplitter::handle {{ background: {t['border']}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}
    QSplitter::handle:vertical {{ height: 1px; }}

    QScrollArea {{ border: none; background: {t['bg']}; }}
    QScrollArea > QWidget > QWidget {{ background: {t['bg']}; }}

    QGroupBox {{
        border: 1px solid {t['border']};
        border-radius: {RADIUS['sm']}px;
        margin-top: 12px;
        padding-top: 8px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
        color: {t['text_muted']};
    }}

    QStackedWidget {{ background: {t['bg']}; }}
    """


def palette_style(theme: Theme) -> str:
    """Minimal stylesheet for the pre-theme splash/loading window."""
    return (f"QWidget {{ background: {theme['bg']}; color: {theme['text']}; }}"
            f"QLabel {{ color: {theme['text_muted']}; }}")

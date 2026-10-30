"""Design tokens: colours, typography, spacing and platform fonts (M15/M16).

Two full palettes (light/dark) expose identical token names so widgets never
branch on theme. ``system`` resolves through the platform setting.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase

LIGHT = {
    "bg": "#f6f8fb",
    "bg_alt": "#eef2f7",
    "surface": "#ffffff",
    "surface_hover": "#f1f5f9",
    "surface_active": "#e8eef6",
    "border": "#dce3ec",
    "border_strong": "#c3cddb",
    "text": "#101828",
    "text_muted": "#5b6b80",
    "text_subtle": "#8b98a9",
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "primary_active": "#1e40af",
    "primary_soft": "#e5edff",
    "on_primary": "#ffffff",
    "success": "#12873f",
    "success_soft": "#e2f6e9",
    "warning": "#b45309",
    "warning_soft": "#fdf0dc",
    "danger": "#c9271f",
    "danger_soft": "#fde8e7",
    "info": "#0e7490",
    "info_soft": "#e0f2fe",
    "muted_soft": "#eef1f5",
    "diff_add_bg": "#e6f7ec",
    "diff_del_bg": "#fdeaea",
    "diff_add_fg": "#0d6b35",
    "diff_del_fg": "#a5201b",
    "diff_hunk_fg": "#6d28d9",
    "selection": "#d7e5ff",
    "shadow": "rgba(16, 24, 40, 0.08)",
}

DARK = {
    "bg": "#12161d",
    "bg_alt": "#0d1117",
    "surface": "#1a2029",
    "surface_hover": "#232b36",
    "surface_active": "#2a3340",
    "border": "#2b3441",
    "border_strong": "#3b4655",
    "text": "#e9eef5",
    "text_muted": "#9aa8ba",
    "text_subtle": "#71808f",
    "primary": "#5b8dfb",
    "primary_hover": "#7aa2fb",
    "primary_active": "#4b7ae8",
    "primary_soft": "#1d2a44",
    "on_primary": "#0b1220",
    "success": "#4ade80",
    "success_soft": "#12281c",
    "warning": "#fbbf5c",
    "warning_soft": "#2d2313",
    "danger": "#f87171",
    "danger_soft": "#301818",
    "info": "#67d5f0",
    "info_soft": "#10262e",
    "muted_soft": "#222a35",
    "diff_add_bg": "#12281c",
    "diff_del_bg": "#301818",
    "diff_add_fg": "#6ee7a0",
    "diff_del_fg": "#fca5a5",
    "diff_hunk_fg": "#c4b5fd",
    "selection": "#25344f",
    "shadow": "rgba(0, 0, 0, 0.45)",
}


@dataclass(frozen=True)
class Theme:
    name: str
    c: dict = field(default_factory=dict)

    def __getitem__(self, key: str) -> str:
        return self.c[key]

    def get(self, key: str, default: str = "") -> str:
        return self.c.get(key, default)

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


def system_is_dark() -> bool:
    """Best-effort read of the OS light/dark preference (M16)."""
    try:
        from PySide6.QtGui import QGuiApplication

        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    if sys.platform.startswith("win"):
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return value == 0
        except OSError:
            return False
    return False


def build_theme(preference: str = "system") -> Theme:
    pref = (preference or "system").lower()
    if pref in ("dark", "high-contrast-dark"):
        return Theme("dark", DARK)
    if pref == "light":
        return Theme("light", LIGHT)
    return Theme("dark", DARK) if system_is_dark() else Theme("light", LIGHT)


# ---------------------------------------------------------------------------
# Typography (M15) — resolve real platform fonts, never hardcode Helvetica.
# ---------------------------------------------------------------------------

_UI_STACK = {
    "win32": ["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"],
    "darwin": ["SF Pro Text", "Helvetica Neue", "Inter"],
    "linux": ["Inter", "Noto Sans", "Ubuntu", "DejaVu Sans", "Cantarell"],
}
_MONO_STACK = {
    "win32": ["Cascadia Mono", "Cascadia Code", "Consolas"],
    "darwin": ["SF Mono", "Menlo", "Monaco"],
    "linux": ["JetBrains Mono", "Fira Code", "DejaVu Sans Mono", "Liberation Mono"],
}


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    return "darwin" if sys.platform == "darwin" else "linux"


def _pick(candidates: list[str]) -> str | None:
    """Return the first candidate the platform font database knows about.

    A QGuiApplication is required to enumerate families; when none exists
    (CLI/tests before Qt starts) the first candidate is assumed.
    """
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        return candidates[0] if candidates else None
    available = set(QFontDatabase.families())
    lowered = {name.lower(): name for name in available}
    for candidate in candidates:
        if candidate in available:
            return candidate
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


class Typography:
    """Resolved font family names plus a factory for sized QFonts.

    Family resolution is lazy and cached, so building a theme or stylesheet
    never forces a font database lookup before Qt is initialised.
    """

    _family: str | None = None
    _mono: str | None = None
    _platform_key: str | None = None

    def __init__(self) -> None:
        key = "win32" if sys.platform.startswith("win") else (
            "darwin" if sys.platform == "darwin" else "linux")
        if Typography._platform_key != key:
            Typography._family = None
            Typography._mono = None
            Typography._platform_key = key
        self.scale = 1.0

    @property
    def family(self) -> str:
        if Typography._family is None:
            key = _platform_key()
            Typography._family = _pick(_UI_STACK.get(key, _UI_STACK["linux"])) \
                or "Sans Serif"
        return Typography._family

    @property
    def mono_family(self) -> str:
        if Typography._mono is None:
            key = _platform_key()
            Typography._mono = _pick(_MONO_STACK.get(key, _MONO_STACK["linux"])) \
                or "Monospace"
        return Typography._mono

    def refresh(self) -> None:
        """Re-resolve families (call after the GUI application starts)."""
        Typography._family = None
        Typography._mono = None

    def font(self, size: float = 10.0, weight: QFont.Weight = QFont.Weight.Normal,
             mono: bool = False, italic: bool = False) -> QFont:
        family = self.mono_family if mono else self.family
        font = QFont(family)
        font.setPointSizeF(max(6.5, size * self.scale))
        font.setWeight(weight)
        font.setItalic(italic)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    # Named roles keep sizes consistent across the app.
    def title(self) -> QFont:
        return self.font(19, QFont.Weight.DemiBold)

    def subtitle(self) -> QFont:
        return self.font(13, QFont.Weight.DemiBold)

    def heading(self) -> QFont:
        return self.font(11, QFont.Weight.DemiBold)

    def body(self) -> QFont:
        return self.font(10)

    def body_strong(self) -> QFont:
        return self.font(10, QFont.Weight.DemiBold)

    def small(self) -> QFont:
        return self.font(9)

    def small_strong(self) -> QFont:
        return self.font(9, QFont.Weight.DemiBold)

    def code(self) -> QFont:
        return self.font(9.5, mono=True)

    def section(self) -> QFont:
        font = self.font(9, QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        return font

    def badge(self) -> QFont:
        return self.font(8.5, QFont.Weight.DemiBold)


SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
RADIUS = {"sm": 6, "md": 10, "lg": 14, "pill": 999}

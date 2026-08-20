"""Application-wide "glass" visual theme: a Fusion dark palette plus a
glassmorphism QSS layer (translucent panels, soft borders, rounded corners,
an accent-colored slider fill) applied on top of it -- see
`assets/glass_theme.qss` for the actual rules.

Qt's native Windows style ("windowsvista", the default here) draws most
button/tab/slider chrome itself via the OS theme engine and largely ignores
stylesheets for it. Fusion is Qt's own style, entirely painted by Qt, so
switching to it first is what makes the QSS below actually take effect --
without it, most of `glass_theme.qss` would silently do nothing.

No real backdrop blur (Windows Acrylic/Mica) is attempted: that needs
native DWM composition APIs outside anything Qt exposes portably. This is
glassmorphism achieved by styling alone -- translucency, soft gradients,
and light borders standing in for a blurred backdrop.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_QSS_PATH = Path(__file__).resolve().parent.parent / "assets" / "glass_theme.qss"

# Shared accent, in case other modules need to match a color swatch/plot
# line to the theme's own blue rather than hardcoding it a second time.
ACCENT = QColor(0x5A, 0xA9, 0xFF)

_WINDOW_BG = QColor(0x18, 0x1A, 0x20)
_PANEL_BG = QColor(0x1E, 0x21, 0x29)
_TEXT = QColor(0xE4, 0xE7, 0xEE)
_DISABLED_TEXT = QColor(0x78, 0x7C, 0x86)


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.Window, _WINDOW_BG)
    p.setColor(QPalette.WindowText, _TEXT)
    p.setColor(QPalette.Base, _PANEL_BG)
    p.setColor(QPalette.AlternateBase, _WINDOW_BG)
    p.setColor(QPalette.ToolTipBase, _PANEL_BG)
    p.setColor(QPalette.ToolTipText, _TEXT)
    p.setColor(QPalette.Text, _TEXT)
    p.setColor(QPalette.Button, _PANEL_BG)
    p.setColor(QPalette.ButtonText, _TEXT)
    p.setColor(QPalette.Link, ACCENT)
    p.setColor(QPalette.Highlight, ACCENT)
    p.setColor(QPalette.HighlightedText, QColor(0x0A, 0x0C, 0x10))
    p.setColor(QPalette.PlaceholderText, _DISABLED_TEXT)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, _DISABLED_TEXT)
    return p


def apply_glass_theme(app: QApplication) -> None:
    """Switches `app` to the Fusion style, applies the dark palette above,
    and layers `assets/glass_theme.qss` on top. Call once, right after
    constructing the `QApplication` and before building any window --
    widgets already on screen when the stylesheet changes generally
    repaint fine, but nothing in this app relies on that, so there's no
    reason to test it.
    """
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(_QSS_PATH.read_text(encoding="utf-8"))

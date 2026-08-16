"""Qt key code -> legacy JS `keyCode` translation table, used to feed the
`iKeyboard` texture (`Viewport`) the same column indices a shader copied
from shadertoy.com already expects (`event.keyCode` in the browser).

For the ASCII range (letters, digits, space, and most punctuation) Qt's
`Qt.Key_*` values are already numerically identical to the legacy JS
`keyCode` — both ultimately trace back to the same old Windows virtual-key
codes — so most keys need no translation at all and are covered by the
fallback in `qt_key_to_js_keycode` below. This table only needs the keys
where the two diverge: Qt's non-ASCII keys use its own `0x0100_0000`+
range (arrows, modifiers, function keys, navigation block, ...), which has
no relationship to the JS numbering.

Deliberately a *practical subset* covering every key an interactive
Shadertoy demo is realistically likely to check (arrows, WASD already fall
under the ASCII fallback, space, shift/ctrl/alt, enter/escape/tab, the
navigation block, F1-F12) — not an exhaustive mapping of every obscure Qt
key constant, which would add a lot of table for essentially no practical
gain (Shadertoy shaders don't check e.g. `Key_LaunchMail`).
"""
from __future__ import annotations

from PySide6.QtCore import Qt

_QT_TO_JS: dict[int, int] = {
    Qt.Key_Backspace: 8,
    Qt.Key_Tab: 9,
    Qt.Key_Return: 13,
    Qt.Key_Enter: 13,
    Qt.Key_Shift: 16,
    Qt.Key_Control: 17,
    Qt.Key_Alt: 18,
    Qt.Key_Pause: 19,
    Qt.Key_CapsLock: 20,
    Qt.Key_Escape: 27,
    Qt.Key_Space: 32,
    Qt.Key_PageUp: 33,
    Qt.Key_PageDown: 34,
    Qt.Key_End: 35,
    Qt.Key_Home: 36,
    Qt.Key_Left: 37,
    Qt.Key_Up: 38,
    Qt.Key_Right: 39,
    Qt.Key_Down: 40,
    Qt.Key_Insert: 45,
    Qt.Key_Delete: 46,
    Qt.Key_Meta: 91,
    Qt.Key_F1: 112,
    Qt.Key_F2: 113,
    Qt.Key_F3: 114,
    Qt.Key_F4: 115,
    Qt.Key_F5: 116,
    Qt.Key_F6: 117,
    Qt.Key_F7: 118,
    Qt.Key_F8: 119,
    Qt.Key_F9: 120,
    Qt.Key_F10: 121,
    Qt.Key_F11: 122,
    Qt.Key_F12: 123,
    Qt.Key_NumLock: 144,
    Qt.Key_ScrollLock: 145,
    Qt.Key_Semicolon: 186,
    Qt.Key_Equal: 187,
    Qt.Key_Comma: 188,
    Qt.Key_Minus: 189,
    Qt.Key_Period: 190,
    Qt.Key_Slash: 191,
    Qt.Key_QuoteLeft: 192,
    Qt.Key_BracketLeft: 219,
    Qt.Key_Backslash: 220,
    Qt.Key_BracketRight: 221,
    Qt.Key_Apostrophe: 222,
}


def qt_key_to_js_keycode(qt_key: int) -> int | None:
    """Returns the `iKeyboard`-texture column (0-255) for a Qt key code,
    or `None` if it doesn't map to any Shadertoy keyCode (falls outside
    the 0-255 texture width, or has no sensible JS equivalent).
    """
    if qt_key in _QT_TO_JS:
        return _QT_TO_JS[qt_key]
    # ASCII fallback: letters (Qt uses uppercase A-Z, same as JS), digits,
    # and the small set of punctuation keys whose Qt constant already
    # equals its ASCII/JS value. Qt keys above 0x01000000 are all in its
    # own non-ASCII extended range (arrows, modifiers, function keys,
    # etc.) and never belong here.
    if 0 <= qt_key < 128:
        return qt_key
    return None

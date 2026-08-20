"""Registry of user-configurable keyboard shortcuts for `MainWindow`'s
menu/toolbar `QAction`s, plus their persistence in `QSettings`.

Scope: only the app-level command shortcuts declared in `SHORTCUT_SPECS`
below (menu items, toolbar buttons) go through this module. Explicitly
out of scope, and untouched by it:

  - The Monaco editor's own built-in keybindings (Ctrl+F, Ctrl+/,
    Ctrl+Space, ...) — those live entirely inside the embedded
    editor/JS layer and have their own remapping story, if any.
  - `keymap.py`'s Qt-key -> legacy-JS-keyCode table — an unrelated
    *shader-facing* translation for the `iKeyboard` texture, not an
    app command.

Previously (before this module existed) only Ctrl+Z/Ctrl+Y were wired at
all, hardcoded via `QAction.setShortcut("Ctrl+Z")` directly in
`MainWindow._build_menu`. Every other action had no shortcut. This module
keeps those two defaults, adds sensible defaults for the handful of
actions where one is genuinely useful (compile, play/pause, save project,
quit...), and leaves the rest with an empty default (`""`) — same "no
shortcut unless you set one" state they were already in, just now
overridable instead of permanently absent.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QKeySequence

SETTINGS_GROUP = "shortcuts"


@dataclass(frozen=True)
class ShortcutSpec:
    action_id: str
    label_key: str
    default: str  # QKeySequence-parseable string, "" means "no shortcut by default"


# Order here is the order the rebinding dialog (`ui/shortcuts_dialog.py`)
# lists them in — grouped by menu, matching `MainWindow._build_menu`'s own
# Fichier/Edition/toolbar grouping so a user scanning the list finds a
# given command roughly where its menu puts it.
# `label_key` points into the `actions.*` tree of `lngs/*.json` (resolved
# through `tr()` at display time, e.g. by `ui/shortcuts_dialog.py`) rather
# than baking translated text into this module-level list at import time —
# same reasoning as `ui/main_window.py`'s `_TAB_LABEL_KEYS`.
SHORTCUT_SPECS: list[ShortcutSpec] = [
    ShortcutSpec("file.new", "actions.file.new", ""),
    ShortcutSpec("file.open", "actions.file.open", "Ctrl+O"),
    ShortcutSpec("file.open_project", "actions.file.open_project", ""),
    ShortcutSpec("file.import_shadertoy", "actions.file.import_shadertoy", ""),
    ShortcutSpec("file.save_as", "actions.file.save_as", ""),
    ShortcutSpec("file.save_project", "actions.file.save_project", "Ctrl+S"),
    ShortcutSpec("file.export_golfed", "actions.file.export_golfed", ""),
    ShortcutSpec("file.export_hlsl", "actions.file.export_hlsl", ""),
    ShortcutSpec("file.export_msl", "actions.file.export_msl", ""),
    ShortcutSpec("file.export_png", "actions.file.export_png", ""),
    ShortcutSpec("file.export_video", "actions.file.export_video", ""),
    ShortcutSpec("file.preferences", "actions.file.preferences", ""),
    ShortcutSpec("file.quit", "actions.file.quit", "Ctrl+Q"),
    ShortcutSpec("edit.undo", "actions.edit.undo", "Ctrl+Z"),
    ShortcutSpec("edit.redo", "actions.edit.redo", "Ctrl+Y"),
    ShortcutSpec("edit.golf", "actions.edit.golf", ""),
    ShortcutSpec("edit.golf_all", "actions.edit.golf_all", ""),
    ShortcutSpec("edit.undo_golf", "actions.edit.undo_golf", ""),
    ShortcutSpec("toolbar.compile", "actions.toolbar.compile", "F5"),
    ShortcutSpec("toolbar.play_pause", "actions.toolbar.play_pause", "Space"),
    ShortcutSpec("toolbar.reset_time", "actions.toolbar.reset_time", ""),
    ShortcutSpec("toolbar.golf", "actions.toolbar.golf", ""),
]

_SPEC_BY_ID: dict[str, ShortcutSpec] = {spec.action_id: spec for spec in SHORTCUT_SPECS}


def default_shortcut(action_id: str) -> str:
    spec = _SPEC_BY_ID.get(action_id)
    return spec.default if spec is not None else ""


def _settings_key(action_id: str) -> str:
    return f"{SETTINGS_GROUP}/{action_id}"


def load_shortcut(settings: QSettings, action_id: str) -> str:
    return settings.value(_settings_key(action_id), default_shortcut(action_id), type=str)


def save_shortcut(settings: QSettings, action_id: str, key_sequence: str) -> None:
    settings.setValue(_settings_key(action_id), key_sequence)


def reset_all(settings: QSettings) -> None:
    """Wipes every saved override, reverting every action back to
    `SHORTCUT_SPECS`' defaults the next time each is loaded."""
    settings.beginGroup(SETTINGS_GROUP)
    settings.remove("")
    settings.endGroup()


class ShortcutRegistry:
    """Owns the live mapping from `action_id` to the `QAction` `MainWindow`
    actually created for it, so the rebinding dialog can push a new
    `QKeySequence` straight onto the running menu/toolbar without
    `MainWindow` having to rebuild either — same action instances the
    menu bar is already displaying just get `setShortcut()` called again.
    """

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings
        self._actions: dict[str, QAction] = {}

    def register(self, action_id: str, action: QAction) -> QAction:
        """Applies this action_id's saved (or default) shortcut to
        `action` and remembers it for later `apply()` calls. Returns
        `action` unchanged so call sites can register inline:
        `registry.register("edit.undo", QAction(...))`.
        """
        action.setShortcut(QKeySequence(load_shortcut(self._settings, action_id)))
        self._actions[action_id] = action
        return action

    def apply(self, action_id: str, key_sequence: str) -> None:
        """Persists `key_sequence` for `action_id` and, if that action is
        currently registered, updates its live `QAction` immediately."""
        save_shortcut(self._settings, action_id, key_sequence)
        action = self._actions.get(action_id)
        if action is not None:
            action.setShortcut(QKeySequence(key_sequence))

    def apply_many(self, key_sequences: dict[str, str]) -> None:
        for action_id, key_sequence in key_sequences.items():
            self.apply(action_id, key_sequence)

    def reset_all(self) -> None:
        reset_all(self._settings)
        for action_id, action in self._actions.items():
            action.setShortcut(QKeySequence(default_shortcut(action_id)))

    def current_sequence(self, action_id: str) -> str:
        action = self._actions.get(action_id)
        if action is not None:
            return action.shortcut().toString()
        return load_shortcut(self._settings, action_id)

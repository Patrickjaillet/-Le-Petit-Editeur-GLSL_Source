"""Language loading and lookup for the "🌍 Internationalisation (i18n)"
section of ROADMAP.md.

Every widget file under `ui/*.py` now calls `tr()` (see ROADMAP.md's
"Migration progressive des widgets", completed). This module provides the
loader/lookup machinery those call sites use: `available_languages()` to
list what exists, `load_language()` to pick one (called once, at startup,
by `main.py`), and `tr(key, **kwargs)` to resolve a dotted key against
whatever got loaded.

Design mirrors `video_export.resolve_ffmpeg_path()` on purpose (same
`sys.frozen` branch, same "packaged vs. development" resolution) since both
solve the same problem: an asset that lives outside the PyInstaller
archive, next to the executable, must be found the same way whether it's
`ffmpeg.exe` or `lngs/fr.json`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

FALLBACK_LANGUAGE_CODE = "fr"


class MissingTranslationKeyError(LookupError):
    """Raised by `tr()` when a key isn't found in the active language file
    nor in `fr.json` -- but only outside a packaged build (see `tr()`'s
    docstring). Deliberately a plain `LookupError` subclass (not `KeyError`,
    whose `repr()` wraps the message in an extra, confusing pair of quotes)
    so the traceback reads as a normal, quotable error message."""

# Populated by `load_language()`; deliberately module-level (not a class)
# since the app only ever has one active language at a time, and every
# call site just wants `tr(...)` to work without threading a loader object
# through every dialog/widget constructor.
_active: dict[str, Any] = {}
_active_code: str = ""
_fallback: dict[str, Any] = {}


def lngs_dir() -> Path:
    """Locates the `lngs/` directory — never relative to the current
    working directory, so `tr()` behaves the same whether the app was
    launched from its own folder or from anywhere else.

    Two cases, exactly matching `video_export.resolve_ffmpeg_path()`:
      - **Packaged** (`sys.frozen`, set by the PyInstaller bootloader):
        `packaging/petit_editeur_glsl.spec` copies `lngs/` to the root of
        the onedir bundle, next to `PetitEditeurGLSL.exe` — resolved
        relative to `sys.executable`'s own directory.
      - **Development** (plain `python run.py`): `lngs/` is read directly
        from the source tree, no copy step needed.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        # python_ui/i18n.py -> project root
        base = Path(__file__).resolve().parent.parent
    return base / "lngs"


def available_languages() -> dict[str, str]:
    """Maps language code -> display name (`_meta.name` from each file),
    for a language picker to list — see the "Sélecteur de langue" roadmap
    item. Built by actually scanning `lngs/*.json` rather than a hardcoded
    list, so dropping in a new file is enough to make a language appear,
    with no code change. A malformed or unreadable file is skipped rather
    than crashing the whole picker over one bad translation."""
    result: dict[str, str] = {}
    directory = lngs_dir()
    if not directory.is_dir():
        return result
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        code = path.stem
        name = data.get("_meta", {}).get("name", code)
        result[code] = name
    return result


def _read_language_file(code: str) -> dict[str, Any] | None:
    path = lngs_dir() / f"{code}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_language(code: str) -> str:
    """Loads `lngs/<code>.json` as the active language for `tr()`, and
    `lngs/fr.json` as the fallback used for any key missing from it (a
    translation lagging behind `fr.json` must never surface an empty
    string/`KeyError` to the user — see `tr()`).

    Returns the code that actually ended up active: `code` itself if its
    file loaded, else `FALLBACK_LANGUAGE_CODE` if that loaded instead
    (e.g. `code` doesn't exist in `lngs/`), else `""` if neither could be
    read at all (missing/corrupt `lngs/` directory) — `tr()` still works
    in that last case, it just returns raw keys instead of text (see its
    docstring).
    """
    global _active, _active_code, _fallback

    _fallback = _read_language_file(FALLBACK_LANGUAGE_CODE) or {}

    if code == FALLBACK_LANGUAGE_CODE:
        _active = _fallback
    else:
        loaded = _read_language_file(code)
        _active = loaded if loaded is not None else _fallback

    _active_code = code if _active is not _fallback or code == FALLBACK_LANGUAGE_CODE else FALLBACK_LANGUAGE_CODE
    if not _active and not _fallback:
        _active_code = ""
    return _active_code


def active_language_code() -> str:
    """The code `tr()` is currently resolving keys against (`""` before
    the first `load_language()` call, or if no language file could be
    read at all)."""
    return _active_code


def _lookup(tree: dict[str, Any], key: str) -> Any:
    """Resolves a dotted key against `tree`, walking one path segment at a
    time -- except a segment boundary is only "cut" if there isn't already
    a literal (flat) key matching everything from here to the end of the
    dotted string at the current node. This is what lets `actions.*`
    (stored as `{"actions": {"file.new": "...", ...}}` in `lngs/*.json` --
    a flat key that happens to contain a dot, since it mirrors
    `shortcuts.py`'s dotted `action_id` values 1:1) resolve correctly
    alongside every genuinely-nested key elsewhere in the same file (e.g.
    `dialogs.about.title`), without two different key conventions needing
    two different lookup functions.
    """
    node: Any = tree
    parts = key.split(".")
    i = 0
    while i < len(parts):
        if not isinstance(node, dict):
            return None
        remaining = ".".join(parts[i:])
        if remaining in node:
            return node[remaining]
        if parts[i] not in node:
            return None
        node = node[parts[i]]
        i += 1
    return node


def tr(key: str, **kwargs: Any) -> str:
    """Resolves a dotted key (e.g. `"dialogs.export_video.title"`) against
    the active language, falling back to `fr.json` if the key is missing
    there.

    If the key is missing from *both*, behaviour depends on how the app is
    running (same `sys.frozen` check as `lngs_dir()`):
      - **Packaged** build: degrades to the raw key, never raises -- a
        missing translation should show up as a visibly-wrong-but-present
        label for an end user, not crash the app.
      - **Development** (`python run.py`, and every test in this repo):
        raises `MissingTranslationKeyError` instead. A forgotten string
        during migration (see ROADMAP.md's "Test de cohérence des
        traductions") should surface immediately at the terminal while
        it's being written, not get discovered later as an odd dotted-key
        label on screen -- see `test_i18n_completeness.py`.

    `**kwargs`, if given, are applied via `str.format(**kwargs)` for
    parameterized strings (e.g. `tr("footer.fps", fps=60)` for
    `"FPS: {fps}"`). A value that isn't a string (e.g. a key that
    resolves to `component_labels_rgb`'s list, or to a whole sub-tree
    because the caller passed a group key by mistake) is returned as-is,
    without attempting `.format()` on it.

    `load_language()` must be called once at startup before this is used
    for real; before that (or if no language file could be read at all)
    every lookup misses, which raises/degrades exactly like a genuinely
    missing key (see above) since there's no way to tell the two apart.
    """
    value = _lookup(_active, key)
    if value is None:
        value = _lookup(_fallback, key)
    if value is None:
        if not getattr(sys, "frozen", False):
            raise MissingTranslationKeyError(
                f"i18n key {key!r} not found in {_active_code!r} nor in "
                f"the {FALLBACK_LANGUAGE_CODE!r} fallback -- add it to "
                f"lngs/{FALLBACK_LANGUAGE_CODE}.json (and every other "
                f"lngs/*.json, see test_i18n_completeness.py) rather than "
                f"letting it silently show up as a raw key on screen."
            )
        return key
    if isinstance(value, str) and kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, IndexError):
            # A caller/translation mismatch (wrong placeholder name, or a
            # translation that dropped a `{placeholder}` the source string
            # had) must not crash the UI over a cosmetic string -- show the
            # unformatted template rather than raise.
            return value
    return value

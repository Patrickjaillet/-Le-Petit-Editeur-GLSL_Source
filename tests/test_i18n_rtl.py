"""RESTE.md: "support RTL (langues écrites de droite à gauche) —
délibérément hors scope" in the original i18n work.

`i18n.active_language_is_rtl()` reads a per-language `_meta.rtl: true`
flag (same "the language file is the source of truth" convention as
`_meta.name`), and `main.py` applies it once at startup via
`QApplication.setLayoutDirection`. None of the 12 shipped languages
(de/en/es/fr/hi/it/ja/ko/no/pt/sv/zh) are actually RTL, so this is
verified against a synthetic RTL fixture language rather than a real
shipped translation -- the mechanism, not a new language, is the
deliverable (same scoping choice already made for pluralization).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))

import i18n  # noqa: E402

# ---- 1. active_language_is_rtl(): isolated fixture files ------------------

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "fr.json").write_text(json.dumps({
        "_meta": {"code": "fr", "name": "Français"},
        "greeting": "Bonjour",
    }), encoding="utf-8")
    (tmp_path / "en.json").write_text(json.dumps({
        "_meta": {"code": "en", "name": "English"},
        # No "rtl" key at all -- the common case, must default to False.
        "greeting": "Hello",
    }), encoding="utf-8")
    # A synthetic RTL fixture (not a real translation): what a future
    # Arabic/Hebrew/Farsi/Urdu lngs/*.json would look like, "_meta.rtl"
    # is the only thing that matters for this test.
    (tmp_path / "ar-test.json").write_text(json.dumps({
        "_meta": {"code": "ar-test", "name": "Test RTL", "rtl": True},
        "greeting": "مرحبا",
    }), encoding="utf-8")
    (tmp_path / "rtl-false.json").write_text(json.dumps({
        "_meta": {"code": "rtl-false", "name": "Test LTR explicit", "rtl": False},
        "greeting": "hi",
    }), encoding="utf-8")
    i18n.lngs_dir = lambda: tmp_path

    i18n.load_language("fr")
    assert i18n.active_language_is_rtl() is False, "a language file with no _meta.rtl key must default to False"
    print("no _meta.rtl key -> active_language_is_rtl() is False: ok")

    i18n.load_language("en")
    assert i18n.active_language_is_rtl() is False
    print("_meta present without 'rtl' -> False (same default): ok")

    i18n.load_language("ar-test")
    assert i18n.active_language_is_rtl() is True, "_meta.rtl: true must be honored"
    print("_meta.rtl: true -> active_language_is_rtl() is True: ok")

    i18n.load_language("rtl-false")
    assert i18n.active_language_is_rtl() is False, "_meta.rtl: false must be honored explicitly, not just absence"
    print("_meta.rtl: false (explicit) -> active_language_is_rtl() is False: ok")

    # Switching back to a non-RTL language after an RTL one must flip
    # back (this is a per-language property re-evaluated on every
    # load_language() call, not something that gets stuck).
    i18n.load_language("fr")
    assert i18n.active_language_is_rtl() is False
    print("switching from RTL back to LTR flips the flag back: ok")

# ---- 2. Every one of the 12 real shipped languages is (correctly) LTR -----

for code in sorted(Path(PROJECT_ROOT, "lngs").glob("*.json")):
    stem = code.stem
    i18n.load_language(stem)
    assert i18n.active_language_is_rtl() is False, f"{stem}: none of the 12 shipped languages are RTL scripts"
print("all 12 real shipped languages correctly report LTR (none are RTL scripts): ok")

# ---- 3. Real end-to-end: QApplication.layoutDirection() actually flips ----

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([sys.argv[0]])

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "fr.json").write_text(json.dumps({"_meta": {"code": "fr", "name": "Français"}}), encoding="utf-8")
    (tmp_path / "ar-test.json").write_text(
        json.dumps({"_meta": {"code": "ar-test", "name": "Test RTL", "rtl": True}}), encoding="utf-8"
    )
    i18n.lngs_dir = lambda: tmp_path

    i18n.load_language("fr")
    app.setLayoutDirection(Qt.RightToLeft if i18n.active_language_is_rtl() else Qt.LeftToRight)
    assert app.layoutDirection() == Qt.LeftToRight

    i18n.load_language("ar-test")
    app.setLayoutDirection(Qt.RightToLeft if i18n.active_language_is_rtl() else Qt.LeftToRight)
    assert app.layoutDirection() == Qt.RightToLeft

    # Restore LTR so this doesn't leak into any other test run in the
    # same process.
    i18n.load_language("fr")
    app.setLayoutDirection(Qt.LeftToRight)

print("QApplication.setLayoutDirection actually flips with active_language_is_rtl(): ok")

print("\nALL OK")

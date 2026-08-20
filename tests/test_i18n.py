"""Exercises `python_ui/i18n.py`: real `lngs/fr.json`/`en.json` key parity
(the guarantee the future `test_i18n_completeness.py` roadmap item will
make comprehensive), plus `tr()`'s lookup/fallback/interpolation behaviour
against small temporary fixture files so those edge cases don't depend on
the real translations staying exactly as they are today.
"""
import json
import sys, os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python_ui"))

import i18n  # noqa: E402


def _flatten(d, prefix=""):
    keys = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= _flatten(v, full)
        else:
            keys.add(full)
    return keys


# ---- real lngs/: fr.json and en.json must be valid JSON with the exact
# same key set (fr.json is the reference; a translation missing a key
# would silently fall back at runtime, which tr() is designed to survive,
# but the files themselves shouldn't drift apart unnoticed). -------------

fr_path = PROJECT_ROOT / "lngs" / "fr.json"
en_path = PROJECT_ROOT / "lngs" / "en.json"
assert fr_path.is_file(), "lngs/fr.json missing"
assert en_path.is_file(), "lngs/en.json missing"

fr_data = json.loads(fr_path.read_text(encoding="utf-8"))
en_data = json.loads(en_path.read_text(encoding="utf-8"))
fr_keys = _flatten(fr_data)
en_keys = _flatten(en_data)
assert fr_keys == en_keys, (
    f"fr.json/en.json key mismatch -- missing in en: {fr_keys - en_keys or 'none'}, "
    f"missing in fr: {en_keys - fr_keys or 'none'}"
)
assert fr_data["_meta"]["code"] == "fr"
assert en_data["_meta"]["code"] == "en"

print("lngs/fr.json <-> lngs/en.json key parity OK "
      f"({len(fr_keys)} keys)")

# ---- lngs_dir(): development mode resolves to <project root>/lngs ------

resolved = i18n.lngs_dir()
assert resolved == PROJECT_ROOT / "lngs", resolved
assert resolved.is_dir()

# ---- available_languages(): both real files are discovered -------------

langs = i18n.available_languages()
assert langs.get("fr") == "Français", langs
assert langs.get("en") == "English", langs

print("available_languages() OK:", langs)

# ---- load_language()/tr() against real fr.json/en.json -----------------

code = i18n.load_language("fr")
assert code == "fr", code
assert i18n.active_language_code() == "fr"
assert i18n.tr("menu.file.new") == "&Nouveau"
assert i18n.tr("footer.fps", fps=60) == "FPS: 60"
# `actions.*` is stored as a flat dict whose own keys contain dots
# (`"file.new"`, mirroring `shortcuts.py`'s dotted `action_id`), not as
# genuinely nested objects -- must resolve exactly like any other key.
assert i18n.tr("actions.file.new") == "Fichier : Nouveau"
assert i18n.tr("actions.toolbar.play_pause") == "Barre d'outils : Lecture/Pause"

code = i18n.load_language("en")
assert code == "en", code
assert i18n.tr("menu.file.new") == "&New"
assert i18n.tr("footer.fps", fps=60) == "FPS: 60"
assert i18n.tr("actions.file.new") == "File: New"

print("load_language()/tr() against real fr/en files OK")

# ---- tr() against temporary fixture files: fallback + missing-key + ----
# ---- bad-placeholder behaviour, isolated from the real translations ----

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "fr.json").write_text(json.dumps({
        "_meta": {"code": "fr", "name": "Français"},
        "greeting": "Bonjour {name}",
        "only_in_fr": "seulement en fr",
        "nested": {"a": "A (fr)", "b": "B (fr)"},
        "actions": {"file.new": "Nouveau (fr)", "file.open": "Ouvrir (fr)"},
    }), encoding="utf-8")
    (tmp_path / "es.json").write_text(json.dumps({
        "_meta": {"code": "es", "name": "Español"},
        "greeting": "Hola {name}",
        "nested": {"a": "A (es)"},
        # "nested.b" and "only_in_fr" deliberately absent -> must fall
        # back to fr.json rather than surface a KeyError/blank string.
    }), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")

    # Monkeypatch lngs_dir() to point at the fixture directory instead of
    # patching sys.frozen/sys.executable, which is simpler and doesn't
    # risk leaking frozen-mode state into any other test run in the same
    # process.
    i18n.lngs_dir = lambda: tmp_path

    langs = i18n.available_languages()
    assert langs == {"es": "Español", "fr": "Français"}, langs  # broken.json skipped

    i18n.load_language("es")
    assert i18n.tr("greeting", name="Ana") == "Hola Ana"
    assert i18n.tr("nested.a") == "A (es)"
    # Missing from es.json -> falls back to fr.json's value, not blank/KeyError.
    assert i18n.tr("nested.b") == "B (fr)"
    assert i18n.tr("only_in_fr") == "seulement en fr"
    # Missing from both fr.json and the active language -> raises in
    # development rather than degrading to the raw key (see
    # test_i18n_completeness.py for the dedicated coverage of this and the
    # packaged-build counter-case).
    try:
        i18n.tr("does.not.exist")
    except i18n.MissingTranslationKeyError:
        pass
    else:
        raise AssertionError("tr() should have raised for a key missing from every file")
    # `actions.*`-style flat key containing a literal dot, alongside a
    # genuinely nested key ("nested.a") in the same tree -- both resolve.
    assert i18n.tr("actions.file.new") == "Nouveau (fr)"
    assert i18n.tr("actions.file.open") == "Ouvrir (fr)"
    # kwargs mismatch (translation/caller placeholder disagreement) ->
    # unformatted template, not a crash.
    assert i18n.tr("greeting", wrong_kwarg="x") == "Hola {name}"

    # A code with no matching file at all falls back to fr.json wholesale.
    code = i18n.load_language("de")
    assert code == "fr", code
    assert i18n.tr("greeting", name="Bob") == "Bonjour Bob"

    print("tr() fallback/missing-key/bad-placeholder edge cases OK")

print("ALL OK")

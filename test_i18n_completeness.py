"""Roadmap's "🌍 Internationalisation" item: "Test de cohérence des
traductions". Three checks:

1. Every file in `lngs/*.json` has *exactly* the same key set as
   `fr.json` -- no key missing (untranslated), no key left over (a dead
   translation nobody reads anymore, the kind of drift the "Migration
   progressive des widgets" roadmap entry found by hand in `en.json`
   once already). `test_i18n.py` already checks this for `fr.json`/
   `en.json` specifically; this generalizes it to every file `lngs/`
   actually contains, scanned rather than hardcoded -- same principle as
   `i18n.available_languages()` itself -- so a third/fourth language
   dropped in later is covered automatically, no change needed here.

2. `i18n.tr()` raises `i18n.MissingTranslationKeyError` -- not a silent
   raw-key fallback -- for a key that isn't in *any* language file, but
   only in development (unfrozen); a packaged build still degrades to
   the raw key rather than crashing over a missing string. Both sides of
   that `sys.frozen` branch are exercised here against a throwaway
   fixture, isolated from the real translations.

3. A best-effort static scan for literal `tr("some.key")` call sites
   across `python_ui/**/*.py`, each checked against the real `fr.json`
   -- this is the actual "repère une chaîne oubliée pendant la
   migration" safety net the roadmap item asks for, and it works without
   needing PySide6 installed (unavailable in this sandbox, see
   COMPILATION.md / ROADMAP.md) to import and instantiate the UI. Keys
   built dynamically -- `tr(_TAB_LABEL_KEYS[tab_id])` in
   `ui/main_window.py`, `tr(spec.label_key)` in `ui/shortcuts_dialog.py`,
   `tr(f"actions.{action_id}")` in `ui/shortcuts_dialog.py` -- aren't
   literal strings and can't be recovered by a source-text regex; those
   were checked by hand when each was introduced (see ROADMAP.md's
   "Migration progressive des widgets" entry) and stay out of scope
   here.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_ui"))

import i18n  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent
LNGS_DIR = PROJECT_ROOT / "lngs"


def _flatten(d, prefix=""):
    keys = set()
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= _flatten(v, full)
        else:
            keys.add(full)
    return keys


# ---- 1. every lngs/*.json file has exactly fr.json's key set -----------

lang_files = sorted(LNGS_DIR.glob("*.json"))
assert lang_files, "lngs/ contains no .json files"

fr_path = LNGS_DIR / "fr.json"
assert fr_path.is_file(), "lngs/fr.json missing (reference language)"
fr_data = json.loads(fr_path.read_text(encoding="utf-8"))
fr_keys = _flatten(fr_data)
assert fr_keys, "lngs/fr.json has no keys at all"

mismatches = []
for path in lang_files:
    code = path.stem
    if code == "fr":
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    keys = _flatten(data)
    missing = fr_keys - keys      # in fr.json, absent from this language
    extra = keys - fr_keys        # in this language, absent from fr.json
    if missing or extra:
        mismatches.append((code, missing, extra))

if mismatches:
    lines = [f"lngs/*.json key-set drift against fr.json ({len(fr_keys)} keys):"]
    for code, missing, extra in mismatches:
        if missing:
            lines.append(f"  {code}.json is missing {len(missing)} key(s): {sorted(missing)}")
        if extra:
            lines.append(f"  {code}.json has {len(extra)} orphan key(s) absent from fr.json: {sorted(extra)}")
    raise AssertionError("\n".join(lines))

print(f"lngs/*.json key parity OK against fr.json "
      f"({len(lang_files)} file(s), {len(fr_keys)} keys each)")


# ---- 2. tr(): raises in development, degrades in a packaged build ------

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "fr.json").write_text(json.dumps({
        "_meta": {"code": "fr", "name": "Français"},
        "present": "présent",
    }), encoding="utf-8")

    # Monkeypatch lngs_dir() rather than sys.frozen/sys.executable, so this
    # is isolated from the real lngs/ files and doesn't need real ones to
    # exercise a genuinely-nowhere-to-be-found key.
    i18n.lngs_dir = lambda: tmp_path
    i18n.load_language("fr")

    assert i18n.tr("present") == "présent"

    assert not getattr(sys, "frozen", False), (
        "expected an unfrozen (development) interpreter for this check -- "
        "running this suite from a packaged build would invert the two "
        "branches being tested below"
    )
    try:
        i18n.tr("some.forgotten.key")
    except i18n.MissingTranslationKeyError as exc:
        assert "some.forgotten.key" in str(exc), exc
    else:
        raise AssertionError(
            "tr() did not raise MissingTranslationKeyError for a key "
            "missing from every lngs/*.json file while unfrozen (development)"
        )

    # Packaged (sys.frozen): the exact same missing key must NOT raise --
    # it degrades to the raw key instead, same as before this roadmap item.
    sys.frozen = True
    try:
        assert i18n.tr("some.forgotten.key") == "some.forgotten.key"
    finally:
        del sys.frozen

    print("tr() dev-raise / packaged-build-fallback behaviour OK")


# ---- 3. static scan: literal tr("...") call sites vs. the real fr.json -

# Matches `tr("literal.key")` / `tr('literal.key')` -- a negative
# lookbehind on the character right before `tr(` keeps this from matching
# `str(`, `attr(`, `instr(`, etc. (anything ending in "...tr(" that isn't
# actually a call to this module's `tr`).
TR_LITERAL_RE = re.compile(r'(?<![A-Za-z0-9_])tr\(\s*(["\'])((?:(?!\1).)*)\1')

python_ui_dir = PROJECT_ROOT / "python_ui"
call_sites_checked = 0
missing_from_source = []
for py_path in sorted(python_ui_dir.rglob("*.py")):
    if "__pycache__" in py_path.parts:
        continue
    text = py_path.read_text(encoding="utf-8")
    for match in TR_LITERAL_RE.finditer(text):
        call_sites_checked += 1
        key = match.group(2)
        if key not in fr_keys:
            line_no = text.count("\n", 0, match.start()) + 1
            missing_from_source.append((py_path.relative_to(PROJECT_ROOT), line_no, key))

if missing_from_source:
    lines = ["Literal tr(\"...\") call site(s) with no matching lngs/fr.json key:"]
    for path, line_no, key in missing_from_source:
        lines.append(f"  {path}:{line_no}: {key!r}")
    raise AssertionError("\n".join(lines))

print(f"Static scan of literal tr(\"...\") call sites in python_ui/ OK "
      f"({call_sites_checked} call site(s) checked against fr.json; "
      f"dynamically-built keys are out of scope, see module docstring)")

print("ALL OK")

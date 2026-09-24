"""RESTE.md: "pluralisation (ICU/gettext) — délibérément hors scope" in
the original i18n work. This is that ticket: `i18n.tr(key, count=n)`
selects between `{"one": "...", "other": "..."}` plural forms using a
two-category CLDR-style rule (`i18n._plural_category`) -- the only
granularity any of the 12 shipped languages actually need (none of them
fall into Slavic few/many or Arabic's 6-way split).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))

import i18n  # noqa: E402

# ---- 1. _plural_category: the CLDR rule table itself -----------------------

# Germanic/Romance "only 1 is singular" languages.
for lang in ("en", "de", "es", "it", "no", "sv", "pt"):
    assert i18n._plural_category(lang, 0) == "other", lang
    assert i18n._plural_category(lang, 1) == "one", lang
    assert i18n._plural_category(lang, 2) == "other", lang
    assert i18n._plural_category(lang, 1.5) == "other", lang
print("_plural_category: Germanic/Romance 'n==1 -> one' rule (en/de/es/it/no/sv/pt): ok")

# French/Hindi treat 0 as singular too (their own grammar, not a quirk).
for lang in ("fr", "hi"):
    assert i18n._plural_category(lang, 0) == "one", lang
    assert i18n._plural_category(lang, 1) == "one", lang
    assert i18n._plural_category(lang, 2) == "other", lang
print("_plural_category: French/Hindi 'n in {0,1} -> one' rule: ok")

# CJK has no plural distinction at all.
for lang in ("ja", "ko", "zh"):
    assert i18n._plural_category(lang, 0) == "other", lang
    assert i18n._plural_category(lang, 1) == "other", lang
    assert i18n._plural_category(lang, 100) == "other", lang
print("_plural_category: CJK (ja/ko/zh) always 'other': ok")

# Unknown/empty language code falls back to the common rule rather than
# crashing.
assert i18n._plural_category("", 1) == "one"
assert i18n._plural_category("xx", 5) == "other"
print("_plural_category: unrecognized language code falls back safely: ok")

# ---- 2. tr(key, count=n) end-to-end, isolated fixture files ---------------

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    (tmp_path / "fr.json").write_text(json.dumps({
        "_meta": {"code": "fr", "name": "Français"},
        "frames_done": {"one": "{count} frame rendue", "other": "{count} frames rendues"},
        "langs_found": {"one": "{count} langue trouvée", "other": "{count} langues trouvées"},
        "plain_string": "pas un pluriel",
    }), encoding="utf-8")
    (tmp_path / "en.json").write_text(json.dumps({
        "_meta": {"code": "en", "name": "English"},
        "frames_done": {"one": "{count} frame rendered", "other": "{count} frames rendered"},
        # "langs_found" deliberately absent from en.json -> must fall back
        # to fr.json's plural dict *and* use FRENCH's plural rule for it
        # (the string's actual grammar), not English's -- this is the
        # part naive "always use the active language's rule" would get
        # wrong for a value in {0} + n where fr and en disagree.
    }), encoding="utf-8")
    i18n.lngs_dir = lambda: tmp_path

    i18n.load_language("en")
    assert i18n.tr("frames_done", count=1) == "1 frame rendered"
    assert i18n.tr("frames_done", count=0) == "0 frames rendered"
    assert i18n.tr("frames_done", count=5) == "5 frames rendered"
    print("tr(count=...) selects the right English plural form: ok")

    i18n.load_language("fr")
    assert i18n.tr("frames_done", count=0) == "0 frame rendue", "French: 0 is grammatically singular"
    assert i18n.tr("frames_done", count=1) == "1 frame rendue"
    assert i18n.tr("frames_done", count=2) == "2 frames rendues"
    print("tr(count=...) selects the right French plural form (0 treated as singular): ok")

    # Falls back to fr.json's plural dict when missing from the active
    # language, and crucially uses FRENCH's rule for it, not English's --
    # count=0 is "other" in English but "one" in French; if this used the
    # active language's rule against the *fallback* string, it would
    # wrongly pick "langues trouvées" (other) instead of "langue trouvée"
    # (one, correct for French text with count=0).
    i18n.load_language("en")
    assert i18n.tr("langs_found", count=0) == "0 langue trouvée", (
        "a plural value inherited from the fr.json fallback must use French's "
        "own plural rule (0 -> singular), not the active language's rule"
    )
    assert i18n.tr("langs_found", count=1) == "1 langue trouvée"
    assert i18n.tr("langs_found", count=3) == "3 langues trouvées"
    print("plural rule follows the language the string was actually resolved from, not the active language: ok")

    # A plain (non-plural) string is entirely unaffected by an unrelated
    # `count=` kwarg being passed alongside other kwargs -- `count` isn't
    # a reserved word that changes ordinary string resolution.
    assert i18n.tr("plain_string") == "pas un pluriel"

    # A plural dict value with no `count=` kwarg is returned as-is (same
    # "non-string value passed through unchanged" contract `tr()` already
    # has for any other dict/list value) -- callers must opt in with
    # `count=` deliberately, never silently guess a branch.
    raw = i18n.tr("frames_done")
    assert isinstance(raw, dict) and "one" in raw and "other" in raw
    print("a plural key without count= is returned as the raw {one, other} dict: ok")

print("\nALL OK")

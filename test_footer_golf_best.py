"""Exercises `footer._load_golf_bests`/`_save_golf_bests`/`record_golf_score`/
`golf_personal_best_html` — ROADMAP.md's "Golfing" section, "comparaison en
ligne avec un score externe" item, implemented as a strictly local
per-project+pass personal-best tracker instead (see `footer.py`'s own
module-level comment on `_SETTINGS_KEY_GOLF_BEST` for why: this app has no
backend service of its own to compare against online).

Same `QSettings`-backed-by-a-temp-.ini pattern `test_export_video_dialog.py`
already uses for its own CRF calibration table, for the same reason: real
`QSettings`, but never touching the user's actual persisted settings.
"""
import sys, os, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_ui"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import i18n
i18n.load_language(i18n.FALLBACK_LANGUAGE_CODE)

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from ui.footer import (  # noqa: E402
    _load_golf_bests,
    _save_golf_bests,
    golf_personal_best_html,
    record_golf_score,
)

_ini_fd, _ini_path = tempfile.mkstemp(suffix=".ini")
os.close(_ini_fd)


def fresh_settings() -> QSettings:
    return QSettings(_ini_path, QSettings.IniFormat)


# ---- unsaved project: never tracked --------------------------------------

s = fresh_settings()
previous, is_new = record_golf_score(s, None, 0, 500)
assert previous is None and is_new is False, (previous, is_new)
assert _load_golf_bests(fresh_settings()) == {}, "an unsaved project must never write any history"

# ---- first golf of a saved project+pass: no previous best, not "new" ------

s = fresh_settings()
previous, is_new = record_golf_score(s, "/tmp/proj.json", 0, 800)
assert previous is None, previous
assert is_new is False, "the very first recorded score is not itself a 'new best' to celebrate"
bests = _load_golf_bests(fresh_settings())
assert bests == {"/tmp/proj.json": {"0": 800}}, bests

# ---- a smaller size afterwards is a new best -------------------------------

s = fresh_settings()
previous, is_new = record_golf_score(s, "/tmp/proj.json", 0, 650)
assert previous == 800, previous
assert is_new is True, "smaller than the recorded best must count as a new best"
bests = _load_golf_bests(fresh_settings())
assert bests["/tmp/proj.json"]["0"] == 650, bests

# ---- an equal or larger size is never a new best, and never overwrites ----

s = fresh_settings()
previous, is_new = record_golf_score(s, "/tmp/proj.json", 0, 650)
assert previous == 650 and is_new is False, (previous, is_new, "a tie is not an improvement")

s = fresh_settings()
previous, is_new = record_golf_score(s, "/tmp/proj.json", 0, 700)
assert previous == 650 and is_new is False, (previous, is_new)
bests = _load_golf_bests(fresh_settings())
assert bests["/tmp/proj.json"]["0"] == 650, "a worse score must never overwrite the recorded best"

# ---- different tab/pass ids never share a best with each other ------------

s = fresh_settings()
previous, is_new = record_golf_score(s, "/tmp/proj.json", 1, 200)
assert previous is None, "a different pass id must start with its own, independent history"
bests = _load_golf_bests(fresh_settings())
assert bests == {"/tmp/proj.json": {"0": 650, "1": 200}}, bests

# ---- different projects never share a best with each other ----------------

s = fresh_settings()
previous, is_new = record_golf_score(s, "/tmp/other.json", 0, 50)
assert previous is None, "a different project path must start with its own, independent history"
bests = _load_golf_bests(fresh_settings())
assert bests["/tmp/proj.json"]["0"] == 650, "unrelated project must never disturb this one's history"
assert bests["/tmp/other.json"]["0"] == 50, bests

# ---- corrupt/foreign settings value degrades to "no history", never crashes

s = fresh_settings()
s.setValue("golfPersonalBests", "not valid json at all {{{")
assert _load_golf_bests(s) == {}, "corrupt stored value must fall back to empty history, not raise"

s = fresh_settings()
s.setValue("golfPersonalBests", '{"/tmp/x.json": {"0": "not-a-number"}}')
assert _load_golf_bests(s) == {}, "a non-integer size must be dropped rather than trusted verbatim"

s = fresh_settings()
s.setValue("golfPersonalBests", '{"/tmp/x.json": {"0": -5}}')
assert _load_golf_bests(s) == {}, "a non-positive size must be dropped rather than trusted verbatim"

print("record_golf_score / _load_golf_bests round-trip and edge cases OK")


# ---- golf_personal_best_html: rendered HTML for the three states ----------

# No history yet: nothing shown, never a misleading "0% better than
# nothing" readout.
assert golf_personal_best_html(800, None, False) == ""

# A brand-new best: celebratory styling, exact byte delta spelled out.
html_new = golf_personal_best_html(650, 800, True)
assert "🏆" in html_new, html_new
assert "#4caf50" in html_new, "expected the same green used for a crossed demoscene tier"
assert "150" in html_new, f"expected the exact byte delta (800-650=150) in: {html_new}"

# Matching or falling short of the recorded best: neutral styling, no
# trophy, no alarm color either.
html_tied = golf_personal_best_html(650, 650, False)
assert "🏆" not in html_tied and "#f44336" not in html_tied, html_tied
assert "650" in html_tied, html_tied

print("golf_personal_best_html rendering for all three states OK")

os.unlink(_ini_path)
print("ALL OK")

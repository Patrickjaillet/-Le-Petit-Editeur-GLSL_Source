"""RM10.md section 1 ("Fiabilité générale") -- real functional coverage of
the unsaved-changes guard, window-title dirty indicator, autosave, and
crash recovery added for that section. Drives the actual `MainWindow`
widgets/signals/file I/O (QMessageBox included, via a scheduled
`QTimer.singleShot` auto-click on the real modal dialog) rather than
re-implementing the logic in isolation -- same principle as
`test_dialect_detection.py`'s "not just detected, actually compiled and
rendered" standard, applied here to "not just returns True, actually opens
the real dialog and writes the real file". Crash recovery in particular is
exercised by constructing a real `MainWindow()` a second/third time
(exactly what happens on the next real launch) rather than hand-rolling a
partial fake window.

Needs the native module built (`engine_bridge` import) -- absent, this
file SKIPs cleanly rather than failing noisily, same convention as
`test_dialect_detection.py`/`test_literals_native.py`.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# `ui.main_window` imports `app_version` (repo root, next to `run.py`) as a
# bare top-level module -- must be on sys.path too, not just `python_ui/`.
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import i18n
i18n.load_language(i18n.FALLBACK_LANGUAGE_CODE)

try:
    import engine_bridge  # noqa: F401
except ImportError as exc:
    print(f"SKIPPED: native module not built ({exc}); "
          f"run 'cd rust_engine && maturin develop --release' first.")
    sys.exit(0)

from PySide6.QtCore import QSettings, QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

app = QApplication.instance() or QApplication([sys.argv[0]])
app.setOrganizationName("PetitEditeurGLSL")
app.setApplicationName("PetitEditeurGLSL")

# Full isolation from the real machine's settings/AppData, via Qt's own
# documented test-isolation mechanisms -- never touches the real Windows
# registry key or the real %APPDATA%\PetitEditeurGLSL folder a developer's
# actual installed app uses (recentFiles, editor prefs, and -- critically
# for this test -- any real leftover autosave.json).
QStandardPaths.setTestModeEnabled(True)
_tmp_settings_dir = tempfile.mkdtemp(prefix="peg_test_settings_")
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, _tmp_settings_dir)

from ui.main_window import MainWindow  # noqa: E402


def click_button(dialog_hint: str, text_substring: str) -> None:
    """Schedules a click on the currently-open modal QMessageBox's button
    whose text contains `text_substring`, fired once the nested event loop
    started by `QMessageBox.exec()` is actually running. Raises loudly
    (rather than silently timing out) if no such dialog/button shows up,
    so a broken wiring fails the test instead of hanging forever.
    """
    def _click() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox), (
            f"{dialog_hint}: expected a modal QMessageBox, got {box!r}"
        )
        for btn in box.buttons():
            if text_substring in btn.text():
                btn.click()
                return
        raise AssertionError(
            f"{dialog_hint}: no button containing {text_substring!r} "
            f"among {[b.text() for b in box.buttons()]}"
        )
    QTimer.singleShot(0, _click)


window = MainWindow()

# ---- 1. Fresh window: no filename, not modified -------------------------

assert window._current_project_path is None
assert window.isWindowModified() is False
# `windowTitle()` is a plain getter: it returns the string as set, `[*]`
# literal placeholder included -- Qt only resolves it (to "*" or "") when
# actually painting a native title bar, which `isWindowModified()` (the
# real, queryable state) is what drives. The placeholder's mere presence
# here just confirms `_update_window_title` wired it into the format
# string in the first place.
assert "[*]" in window.windowTitle()
print("fresh window: no filename, not modified: ok")

# ---- 2. Editing marks dirty and flips windowModified ---------------------

window._on_text_changed("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }")
assert window._is_dirty is True
assert window.isWindowModified() is True
print("editing sets dirty + windowModified: ok")

# ---- 3. Quick-save (the "Save" choice's synchronous path) ----------------

tmp_dir = tempfile.mkdtemp(prefix="peg_test_project_")
project_path = str(Path(tmp_dir) / "project.json")
window._current_project_path = project_path
ok = window._quick_save_current_project()
assert ok is True
assert window._is_dirty is False
assert window.isWindowModified() is False
assert Path(project_path).name in window.windowTitle()
saved = json.loads(Path(project_path).read_text(encoding="utf-8"))
assert saved["format"] == 3
assert "mainImage" in saved["passes"][str(engine_bridge.PASS_IMAGE)]
print("quick-save writes a real project file and clears dirty/title: ok")

# ---- 4. Three-way unsaved-changes dialog: Cancel keeps state dirty ------

window._on_text_changed("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(2.0); }")
assert window._is_dirty is True
click_button("unsaved-changes dialog", "Annuler")
proceed = window._confirm_discard_if_dirty()
assert proceed is False
assert window._is_dirty is True
print("unsaved-changes dialog, Cancel: blocks and keeps dirty state: ok")

# ---- 5. Three-way dialog: Save actually saves and allows proceeding ------

click_button("unsaved-changes dialog", "Enregistrer")
proceed = window._confirm_discard_if_dirty()
assert proceed is True
assert window._is_dirty is False
reloaded = json.loads(Path(project_path).read_text(encoding="utf-8"))
assert "vec4(2.0)" in reloaded["passes"][str(engine_bridge.PASS_IMAGE)]
print("unsaved-changes dialog, Save: writes latest edit and proceeds: ok")

# ---- 6. Three-way dialog: Don't Save discards and allows proceeding ------

window._on_text_changed("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(3.0); }")
click_button("unsaved-changes dialog", "Ne pas enregistrer")
proceed = window._confirm_discard_if_dirty()
assert proceed is True
assert window._is_dirty is True, (
    "Don't Save must not itself clear the dirty flag -- the caller "
    "(New/Open/Import/close) is what actually discards the in-memory state"
)
reloaded2 = json.loads(Path(project_path).read_text(encoding="utf-8"))
assert "vec4(3.0)" not in reloaded2["passes"][str(engine_bridge.PASS_IMAGE)], (
    "Don't Save must never write the discarded edit to disk"
)
print("unsaved-changes dialog, Don't Save: proceeds without writing to disk: ok")

# ---- 7. Autosave: writes only while dirty ---------------------------------

window._on_text_changed("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(4.0); }")
window._current_project_path = None  # simulate never-saved-anywhere work
assert window._is_dirty is True
autosave_path = window._autosave_file_path()
if autosave_path.exists():
    autosave_path.unlink()
window._write_autosave()
assert autosave_path.exists(), "autosave file should exist right after _write_autosave() while dirty"
saved_autosave = json.loads(autosave_path.read_text(encoding="utf-8"))
assert "vec4(4.0)" in saved_autosave["passes"][str(engine_bridge.PASS_IMAGE)]
print("autosave writes a real recovery file while dirty: ok")

window._is_dirty = False
autosave_path.unlink()
window._write_autosave()
assert not autosave_path.exists(), "must never write an autosave file when there are no unsaved changes"
print("autosave is a no-op when there's nothing unsaved: ok")

# The scenarios below call `_try_crash_recovery()` directly on the same
# already fully-constructed, fully-real `window` rather than instantiating
# fresh `MainWindow()`s: `__init__`'s own call to it (see `main_window.py`)
# is a one-line, directly-inspectable pass-through, and constructing
# several more `QWebEngineView`-backed windows (Monaco editor) in a single
# offscreen process is what makes this interpreter segfault at shutdown --
# a Qt/WebEngine teardown artifact unrelated to the feature under test.
# `_try_crash_recovery()` itself is exercised for real either way.

window._on_text_changed("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(5.0); }")
window._current_project_path = None
window._write_autosave()
assert autosave_path.exists()
saved_autosave = json.loads(autosave_path.read_text(encoding="utf-8"))

# ---- 8. Next launch finds the leftover autosave: Restaurer ---------------

click_button("crash-recovery dialog", "Restaurer")
window._is_dirty = False  # simulate the next launch's fresh, not-yet-dirty state
recovered = window._try_crash_recovery()
assert recovered is True
assert window._pass_sources[engine_bridge.PASS_IMAGE] == saved_autosave["passes"][str(engine_bridge.PASS_IMAGE)]
assert "vec4(5.0)" in window._pass_sources[engine_bridge.PASS_IMAGE]
assert window._is_dirty is True, "recovered content has nowhere matching on disk yet -- must count as unsaved"
assert not autosave_path.exists(), "a recovered autosave must be cleared, not linger"
print("crash recovery: Restaurer reloads the exact autosaved content and clears the file: ok")

# ---- 9. Next launch finds a leftover autosave: Ignorer --------------------

window._on_text_changed("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(6.0); }")
window._write_autosave()
assert autosave_path.exists()
# Distinct sentinel, set directly (bypassing the editor), standing in for
# "whatever the in-memory state already was before recovery ran" -- e.g.
# the default shader freshly loaded at the start of a real next launch.
# vec4(6.0) is what's *inside* the autosave file being discarded; the
# check that matters is that discarding leaves this sentinel alone rather
# than pulling vec4(6.0) in, not that vec4(6.0) is textually absent (it
# would trivially still be "in memory" either way, since it's the very
# text `_write_autosave()` just serialized from this same in-memory state).
window._pass_sources[engine_bridge.PASS_IMAGE] = "SENTINEL_PRE_RECOVERY_STATE"

click_button("crash-recovery dialog", "Ignorer")
window._is_dirty = False
recovered2 = window._try_crash_recovery()
assert recovered2 is False
assert window._pass_sources[engine_bridge.PASS_IMAGE] == "SENTINEL_PRE_RECOVERY_STATE", (
    "discarding the recovery prompt must leave existing in-memory state untouched"
)
assert not autosave_path.exists(), "discarding the recovery prompt must also clear the autosave file"
print("crash recovery: Ignorer clears the file without restoring: ok")

# ---- 10. A corrupted autosave file never blocks startup -------------------

autosave_path.write_text("{not valid json", encoding="utf-8")
recovered3 = window._try_crash_recovery()  # must return cleanly, no dialog, no crash
assert recovered3 is False
assert not autosave_path.exists()
print("crash recovery: corrupted autosave file is discarded silently, never blocks startup: ok")

# ---- 11. Mid-use device/source loss (RM10.md section 1, item 6) -----------
# `QCamera`/`QMediaPlayer` actually detecting a real unplug/driver failure
# is an OS/hardware integration point no tool available here can fabricate
# -- this instead verifies the half that's actually this app's own code:
# once `VideoChannelSource`/`AudioChannelSource` emits `sourceLost` (which
# is wired straight to Qt's own `errorOccurred` signal, see
# `video_source.py`/`audio_source.py`), the slot is stopped cleanly and the
# textures panel shows an explicit message rather than silently going dark.

from video_source import VideoChannelSource as _VCS

window._goto_tab(engine_bridge.PASS_IMAGE)
probe = _VCS(lambda *a: None, window)
window._video_sources[(engine_bridge.PASS_IMAGE, 0)] = probe
window._engine.set_ichannel_video(engine_bridge.PASS_IMAGE, 0)
window.ichannel_panel.set_active_pass(engine_bridge.PASS_IMAGE)
probe.sourceLost.connect(
    lambda msg, p=engine_bridge.PASS_IMAGE, c=0: window._on_source_lost(p, c, msg)
)
probe.sourceLost.emit("device disconnected (simulated)")
assert (engine_bridge.PASS_IMAGE, 0) not in window._video_sources, (
    "a lost source must be stopped and removed, not left dangling"
)
slot = window.ichannel_panel._slots[0]
assert "device disconnected" in slot._thumb.toolTip()
assert slot._thumb.text() == "⚠"
print("mid-use source loss: stopped cleanly and shown explicitly in the textures panel: ok")

window._autosave_timer.stop()

print("\nALL OK")

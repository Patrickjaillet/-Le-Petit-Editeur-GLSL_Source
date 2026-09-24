"""RESTE.md: "calage bit-exact du spectre FFT" against shadertoy.com's own
undocumented formula is unverifiable from this codebase and this
environment (no published formula, no network access to compare against
a live reference either -- see shadertoy_import._cubemap_face_urls's
identical constraint). Instead of guessing at different dB constants with
no way to check they're actually closer, this adds a live, user-adjustable
gain offset (dB) per audio/microphone iChannel slot -- verified here to
actually change the analysis output, and to be wired through the UI and
project serialization.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import i18n
i18n.load_language(i18n.FALLBACK_LANGUAGE_CODE)

try:
    import engine_bridge  # noqa: F401
except ImportError as exc:
    print(f"SKIPPED: native module not built ({exc}); run 'cd rust_engine && maturin develop --release' first.")
    sys.exit(0)

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication([sys.argv[0]])
QMessageBox.warning = staticmethod(lambda *a, **k: None)

import numpy as np  # noqa: E402

import audio_source  # noqa: E402

# ---- 1. Gain offset actually shifts the normalized spectrum ---------------

source = audio_source.AudioChannelSource()
# A quiet-ish signal so a positive gain has visible headroom to push it up
# without immediately saturating every bin at 255.
source._push_samples(np.full(2048, 0.02, dtype=np.float32))

spectrum_flat, _ = source.compute_frame()
source.set_gain_db(20.0)
spectrum_boosted, _ = source.compute_frame()
source.set_gain_db(-20.0)
spectrum_cut, _ = source.compute_frame()

assert sum(spectrum_boosted) > sum(spectrum_flat), "a +20dB gain must raise the normalized spectrum"
assert sum(spectrum_cut) < sum(spectrum_flat), "a -20dB gain must lower the normalized spectrum"
print("gain offset (dB) measurably shifts the normalized spectrum: ok")

# 0 dB must be the default and a true no-op (matches pre-existing output
# exactly, for a project saved before this feature existed).
source2 = audio_source.AudioChannelSource()
source2._push_samples(np.full(2048, 0.02, dtype=np.float32))
assert source2.compute_frame()[0] == spectrum_flat, "a fresh source (0 dB default) must match the earlier no-gain spectrum"
print("0 dB (the default) matches pre-existing (no-gain) output: ok")

# ---- 2. MicrophoneChannelSource shares the same gain mechanism ------------

mic = audio_source.MicrophoneChannelSource()
mic._push_samples(np.full(2048, 0.02, dtype=np.float32))
mic.set_gain_db(20.0)
mic_spectrum, _ = mic.compute_frame()
assert mic_spectrum == spectrum_boosted, "MicrophoneChannelSource must apply gain identically to AudioChannelSource"
print("MicrophoneChannelSource shares the exact same gain mechanism: ok")

# ---- 3. IChannelPanel: gain persists per-slot, propagates, round-trips ----

from PySide6.QtCore import QEventLoop, QSettings, QStandardPaths  # noqa: E402
import tempfile  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="peg_test_settings_"))
app.setOrganizationName("PetitEditeurGLSL")
app.setApplicationName("PetitEditeurGLSL")

from ui.main_window import MainWindow  # noqa: E402

window = MainWindow()
if not window.editor._ready:
    loop = QEventLoop()
    window.editor.editorReady.connect(loop.quit)
    loop.exec()

assert window.ichannel_panel.gain_for(engine_bridge.PASS_IMAGE, 0) == 0.0, "default gain must be 0 dB"

# Simulate the UI signal chain a real spinbox edit would fire.
window.ichannel_panel._on_slot_gain_changed(0, 12.5)
assert window.ichannel_panel.gain_for(engine_bridge.PASS_IMAGE, 0) == 12.5
print("gain setting stored per (pass, channel) slot: ok")

# Starting a real audio source must pick up the stored gain immediately.
window._apply_ichannel_assignment(engine_bridge.PASS_IMAGE, 0, "audio", "/nonexistent/file/for/this/test.wav")
started_source = window._audio_sources.get((engine_bridge.PASS_IMAGE, 0))
assert started_source is not None
assert started_source._gain_db == 12.5, "a freshly started source must apply the slot's stored gain"
print("freshly started AudioChannelSource applies the stored gain immediately: ok")

# Live adjustment while a source is already running.
window._on_ichannel_gain_changed(engine_bridge.PASS_IMAGE, 0, -6.0)
assert started_source._gain_db == -6.0, "a live gain change must update the already-running source in place"
print("live gain change updates an already-running source in place: ok")

window._stop_audio_channel(engine_bridge.PASS_IMAGE, 0)

# Project round-trip: only a non-zero gain is persisted (matches the
# min/max override convention elsewhere: don't bloat every project file
# with a redundant "0.0" for the overwhelming common case).
proj_data = {
    str(engine_bridge.PASS_IMAGE): [
        {"kind": "audio", "value": "musique.mp3", "gain_db": 8.0},
        {"kind": "microphone", "value": "", "gain_db": -3.5},
        {"kind": "empty", "value": None},
    ]
}
window.ichannel_panel.load_project_data(proj_data)
assert window.ichannel_panel.gain_for(engine_bridge.PASS_IMAGE, 0) == 8.0
assert window.ichannel_panel.gain_for(engine_bridge.PASS_IMAGE, 1) == -3.5
out = window.ichannel_panel.project_data()
entries = out[str(engine_bridge.PASS_IMAGE)]
assert entries[0]["gain_db"] == 8.0
assert entries[1]["gain_db"] == -3.5
assert "gain_db" not in entries[2], "a zero-gain slot must not be persisted (matches min/max override convention)"
print("gain persists correctly through a project data round-trip: ok")

# A project saved before this feature existed (no "gain_db" key at all)
# must still load cleanly at the default.
old_proj_data = {str(engine_bridge.PASS_IMAGE): [{"kind": "audio", "value": "musique.mp3"}]}
window.ichannel_panel.load_project_data(old_proj_data)
assert window.ichannel_panel.gain_for(engine_bridge.PASS_IMAGE, 0) == 0.0
print("a project predating this feature loads with the default 0 dB gain: ok")

window._autosave_timer.stop()
print("\nALL OK")

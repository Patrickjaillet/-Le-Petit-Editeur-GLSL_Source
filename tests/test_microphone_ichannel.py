"""RESTE.md/ROADMAP.md: live microphone input as an iChannel audio source
was explicitly out of scope for the first audio-channel pass ("entrée
microphone en direct... à faire au besoin dans un ticket dédié une fois
l'audio fichier en place et éprouvé"). This is that ticket.

`MicrophoneChannelSource` (python_ui/audio_source.py) reuses
`_AudioAnalysisMixin`'s ring buffer/FFT (`compute_frame`), the exact same
analysis `AudioChannelSource` already uses for file playback, fed instead
from `QAudioSource` live capture. This environment has no real microphone
hardware (sandboxed container, no PulseAudio/ALSA input device), so this
test covers what's actually exercisable here: graceful degradation with
zero input devices (matching `_pick_webcam`'s own "no camera" handling),
the `_decode_samples` PCM-format branching shared with the file-based
source (already verified against real decoded audio in
test_audio_ichannel_real.py), and wiring into `MainWindow` (combo index
mapping, project-data round trip, no crash on an unavailable device).
"""
import os
import sys
import tempfile

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
QMessageBox.warning = staticmethod(lambda *a, **k: None)  # never block this test on a modal dialog

import numpy as np  # noqa: E402
from PySide6.QtMultimedia import QAudioFormat  # noqa: E402

import audio_source  # noqa: E402

# ---- 1. Graceful degradation with no input device (this sandbox) ----------

mic = audio_source.MicrophoneChannelSource()
assert not mic.is_active()
ok = mic.start()
assert ok is False, "start() must return False, not raise, when no microphone is available"
assert not mic.is_active()
spectrum, waveform = mic.compute_frame()
assert len(spectrum) == 512 and len(waveform) == 512
assert mic.position_seconds() == 0.0
mic.stop()  # must not raise when nothing was ever started
print("graceful degradation with zero input devices: ok")

# ---- 2. list_microphones() never raises, even with zero devices -----------

mics = audio_source.list_microphones()
assert isinstance(mics, list)
print(f"list_microphones() returned {len(mics)} device(s) without raising: ok")

# ---- 3. _decode_samples shared PCM-format branching (all 4 formats) -------

fmt = QAudioFormat()
fmt.setChannelCount(1)

fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
raw = np.array([0, 16384, -16384, 32767], dtype=np.int16).tobytes()
samples = audio_source._decode_samples(raw, fmt)
assert samples is not None and len(samples) == 4
assert abs(samples[1] - 0.5) < 0.01 and abs(samples[2] + 0.5) < 0.01
print("_decode_samples: Int16 format: ok")

fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
raw = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32).tobytes()
samples = audio_source._decode_samples(raw, fmt)
assert samples is not None and list(samples) == [0.0, 0.5, -0.5, 1.0]
print("_decode_samples: Float format: ok")

fmt.setSampleFormat(QAudioFormat.SampleFormat.UInt8)
raw = np.array([128, 255, 0], dtype=np.uint8).tobytes()
samples = audio_source._decode_samples(raw, fmt)
assert samples is not None and abs(samples[0]) < 0.01
print("_decode_samples: UInt8 format: ok")

# Stereo downmix: 2 channels of Int16, averaged to mono.
fmt.setChannelCount(2)
fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
raw = np.array([0, 32767, 0, -32767], dtype=np.int16).tobytes()  # frame1=(0,32767), frame2=(0,-32767)
samples = audio_source._decode_samples(raw, fmt)
assert samples is not None and len(samples) == 2
assert abs(samples[0] - 0.5) < 0.01 and abs(samples[1] + 0.5) < 0.01
print("_decode_samples: stereo downmix to mono: ok")

# Empty/invalid input never raises.
assert audio_source._decode_samples(b"", fmt) is None
fmt_zero_channels = QAudioFormat()
fmt_zero_channels.setChannelCount(0)
assert audio_source._decode_samples(b"\x00\x00", fmt_zero_channels) is None
print("_decode_samples: empty/invalid input returns None without raising: ok")

# ---- 4. compute_frame is identical logic to AudioChannelSource's own ------

mic2 = audio_source.MicrophoneChannelSource()
file_source = audio_source.AudioChannelSource()
mic2._push_samples(np.full(2048, 0.3, dtype=np.float32))
file_source._push_samples(np.full(2048, 0.3, dtype=np.float32))
assert mic2.compute_frame() == file_source.compute_frame(), "shared _AudioAnalysisMixin must produce identical output for identical input"
print("MicrophoneChannelSource and AudioChannelSource share identical analysis: ok")

# ---- 5. MainWindow wiring: combo index mapping + project round-trip -------

from ui.ichannel_panel import _kind_value_for, _combo_index_for, _MICROPHONE_INDEX  # noqa: E402

kind, value = _kind_value_for(_MICROPHONE_INDEX)
assert kind == "microphone" and value is None
assert _combo_index_for("microphone", None) == _MICROPHONE_INDEX
print("combo box index <-> (kind, value) mapping: ok")

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qapp = QApplication.instance() or QApplication([sys.argv[0]])
qapp.setOrganizationName("PetitEditeurGLSL")
qapp.setApplicationName("PetitEditeurGLSL")
QStandardPaths.setTestModeEnabled(True)
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="peg_test_settings_"))

from ui.main_window import MainWindow  # noqa: E402

window = MainWindow()
if not window.editor._ready:
    from PySide6.QtCore import QEventLoop
    loop = QEventLoop()
    window.editor.editorReady.connect(loop.quit)
    loop.exec()

# No real mic device here -> must warn (stubbed above) and not crash.
window._apply_ichannel_assignment(engine_bridge.PASS_IMAGE, 0, "microphone", "")
print("MainWindow._apply_ichannel_assignment('microphone', ...) with no device: ok")

proj_data = {str(engine_bridge.PASS_IMAGE): [{"kind": "microphone", "value": "some-device-id"}]}
window.ichannel_panel.load_project_data(proj_data)
data = window.ichannel_panel.project_data()
assert data[str(engine_bridge.PASS_IMAGE)][0] == {"kind": "microphone", "value": "some-device-id"}
print("project data round-trip (load_project_data -> project_data): ok")

window._autosave_timer.stop()
print("\nALL OK")

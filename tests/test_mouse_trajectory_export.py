"""RESTE.md: "pas d'enregistrement du trajet de la souris pendant l'export
(iMouse figé à (0,0,0,0) sur toute la séquence)". `Viewport.start_mouse_recording`/
`stop_mouse_recording` capture a `(time, x, y, z, w)` trajectory from real
mouse events during live preview; `video_export.capture_frames`/`_mouse_at_time`
resample it ("hold most recent sample") at each export frame's own time
instead of using the fixed default.
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

import video_export  # noqa: E402

# ---- 1. _mouse_at_time: hold-most-recent-sample resampling ----------------

trajectory = [
    (0.0, 10.0, 20.0, 10.0, 20.0),
    (1.0, 30.0, 40.0, 10.0, 20.0),
    (2.5, 50.0, 60.0, -10.0, -20.0),
]

assert video_export._mouse_at_time(trajectory, -1.0) == video_export.FIXED_MOUSE, "before the first sample -> FIXED_MOUSE"
assert video_export._mouse_at_time(trajectory, 0.0) == (10.0, 20.0, 10.0, 20.0)
assert video_export._mouse_at_time(trajectory, 0.5) == (10.0, 20.0, 10.0, 20.0), "held, not interpolated"
assert video_export._mouse_at_time(trajectory, 1.0) == (30.0, 40.0, 10.0, 20.0)
assert video_export._mouse_at_time(trajectory, 2.4) == (30.0, 40.0, 10.0, 20.0)
assert video_export._mouse_at_time(trajectory, 2.5) == (50.0, 60.0, -10.0, -20.0)
assert video_export._mouse_at_time(trajectory, 100.0) == (50.0, 60.0, -10.0, -20.0), "held past the last sample"
assert video_export._mouse_at_time([], 1.0) == video_export.FIXED_MOUSE, "empty trajectory -> FIXED_MOUSE"
print("_mouse_at_time: hold-most-recent-sample resampling: ok")

# ---- 2. capture_frames actually uses per-frame resolved mouse -------------

mouse_calls = []


class _FakeEngine:
    """Records every `render()` call's mouse argument -- doesn't need to
    actually render anything, `capture_frames` only cares about the
    pixels it returns."""

    def render(self, time, time_delta, mouse, frame, date):
        mouse_calls.append((frame, mouse))
        return b"\x00\x00\x00\x00" * (4 * 4)  # 4x4 RGBA8, all black


trajectory2 = [(0.0, 1.0, 2.0, 1.0, 2.0), (0.5, 3.0, 4.0, 1.0, 2.0)]
out_dir = video_export.capture_frames(
    _FakeEngine(), n_frames=3, fps=2.0, width=4, height=4, date=(2024, 1, 1, 0), mouse=trajectory2,
)
# capture_frames submits n_frames+1 calls (0..n_frames inclusive), frame i's
# *submitted* time is i/fps -- frame 0 -> t=0.0 (before first sample after
# resampling: matches first sample), frame 1 -> t=0.5 (second sample).
assert mouse_calls[0] == (0, (1.0, 2.0, 1.0, 2.0))
assert mouse_calls[1] == (1, (3.0, 4.0, 1.0, 2.0))
assert mouse_calls[2] == (2, (3.0, 4.0, 1.0, 2.0)), "held past the last sample"
print("capture_frames resolves a MouseTrajectory per-frame via _mouse_at_time: ok")

# A fixed 4-tuple must still behave exactly as before (backward compat).
mouse_calls.clear()
video_export.capture_frames(
    _FakeEngine(), n_frames=2, fps=1.0, width=4, height=4, date=(2024, 1, 1, 0), mouse=(5.0, 6.0, 5.0, 6.0),
)
assert all(m == (5.0, 6.0, 5.0, 6.0) for _f, m in mouse_calls), "fixed tuple must stay fixed for every frame"
print("a fixed 4-tuple mouse argument stays backward compatible: ok")

import shutil  # noqa: E402
shutil.rmtree(out_dir, ignore_errors=True)

# ---- 3. Real Viewport recording, end to end --------------------------------

from PySide6.QtCore import QSettings, QStandardPaths  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import tempfile  # noqa: E402

app = QApplication.instance() or QApplication([sys.argv[0]])
app.setOrganizationName("PetitEditeurGLSL")
app.setApplicationName("PetitEditeurGLSL")
QStandardPaths.setTestModeEnabled(True)
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, tempfile.mkdtemp(prefix="peg_test_settings_"))

from ui.main_window import MainWindow  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402

window = MainWindow()
if not window.editor._ready:
    loop = QEventLoop()
    window.editor.editorReady.connect(loop.quit)
    loop.exec()

assert not window.viewport.is_recording_mouse()
window.viewport.start_mouse_recording()
assert window.viewport.is_recording_mouse()

# Real press + move events, exactly as a user dragging in the viewport
# would generate -- exercises the exact `mousePressEvent`/`mouseMoveEvent`
# code path `_tick` samples from, not a hand-constructed `_mouse` value.
press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(10, 10), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
window.viewport.mousePressEvent(press)
move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(20, 15), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
window.viewport.mouseMoveEvent(move)

# Let a few real ticks (the ~60fps QTimer) fire so `_tick` actually
# appends samples, rather than calling the private method directly.
tick_loop = QEventLoop()
QTimer.singleShot(120, tick_loop.quit)
tick_loop.exec()

trajectory_result = window.viewport.stop_mouse_recording()
assert not window.viewport.is_recording_mouse()
assert len(trajectory_result) >= 2, f"expected several ticks to have recorded samples, got {len(trajectory_result)}"
assert all(len(sample) == 5 for sample in trajectory_result)
# The dragged-to position (20, height-15) should show up in at least one
# recorded sample (the mouse stayed there for the remaining ticks).
assert any(abs(s[1] - 20.0) < 1.0 for s in trajectory_result), "recorded trajectory never reflects the mouse move"
print(f"real Viewport mouse recording captured {len(trajectory_result)} samples reflecting real events: ok")

assert window.viewport.mouse_trajectory() == trajectory_result
window.viewport.clear_mouse_trajectory()
assert window.viewport.mouse_trajectory() == []
print("mouse_trajectory()/clear_mouse_trajectory(): ok")

window._autosave_timer.stop()
print("\nALL OK")

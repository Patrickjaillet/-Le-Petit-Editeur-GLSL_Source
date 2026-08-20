"""Regression test for the "sliders don't return to their origin, values
shown don't match the shader" bug: `Viewport.timeUpdated` fires on every
render tick regardless of pause state, so `SlidersPanel.set_time` used to
re-impose the keyframe-interpolated value on every single call — even
when the clock hadn't actually moved. That silently undid any manual
interaction (drag, typed value, reset, randomize) on a keyframed slider
within a single frame, and, because every reassertion re-armed
`MainWindow`'s single-shot compile-debounce timer, could starve
recompilation indefinitely while a keyframed animation was playing (the
shader stops following the values the panel displays).

Fix: `set_time` now returns immediately when `t` equals the previously
seen time, so a manual edit made while the clock is holding still (e.g.
paused) is left alone instead of being overwritten on the next tick.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python_ui"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import i18n
i18n.load_language(i18n.FALLBACK_LANGUAGE_CODE)

from PySide6.QtWidgets import QApplication
from ui.sliders_panel import SlidersPanel

app = QApplication.instance() or QApplication([])


class FakeFloat:
    def __init__(self, start, end, value, min_, max_, category):
        self.start, self.end, self.value = start, end, value
        self.min, self.max, self.category = min_, max_, category


def Sliders(floats):
    return (floats, [], [], [])


panel = SlidersPanel()
src = "float speed = 1.0;"
sliders = Sliders(floats=[FakeFloat(14, 17, 1.0, 0.0, 2.0, "Global")])
panel.rebuild(src, sliders)

_, spin = panel._rows[0]

# Two keyframes: (t=0, v=0.5) and (t=4, v=1.5).
panel.set_time(0.0)
spin.setValue(0.5)
panel.add_keyframe(0)
panel.set_time(4.0)
spin.setValue(1.5)
panel.add_keyframe(0)

# Pause at t=2.0 (interpolated value: 1.0).
panel.set_time(2.0)
assert abs(spin.value() - 1.0) < 1e-6

# User manually drags the slider away from the keyframe curve...
spin.setValue(1.9)
assert abs(spin.value() - 1.9) < 1e-6

# ...while the render loop keeps ticking `set_time` at the SAME time
# (this is exactly what `Viewport._tick` does every ~16ms, paused or
# not). The manual edit must stick.
for _ in range(5):
    panel.set_time(2.0)
assert abs(spin.value() - 1.9) < 1e-6, (
    f"manual edit on a keyframed slider was overwritten by a paused "
    f"clock re-ticking the same time: got {spin.value()}, expected 1.9"
)

# Same story for the "reset" button (-> initial_value captured at
# rebuild() time, here 1.0).
panel._reset_ordinals([0])
for _ in range(5):
    panel.set_time(2.0)
assert abs(spin.value() - 1.0) < 1e-6, (
    f"reset on a keyframed slider did not stick: got {spin.value()}, expected 1.0"
)

# Sanity check: genuine time progression still drives the interpolation
# as intended (this is the actual point of keyframing).
panel.set_time(1.0)  # midpoint of [0, 2] -> (0.5+1.0)/2 = 0.75
assert abs(spin.value() - 0.75) < 1e-6, spin.value()

print("test_keyframe_reset_bugfix: OK")

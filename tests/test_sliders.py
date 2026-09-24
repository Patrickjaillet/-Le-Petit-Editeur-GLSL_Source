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

class FakeInt:
    def __init__(self, start, end, value, min_, max_, category):
        self.start, self.end, self.value = start, end, value
        self.min, self.max, self.category = min_, max_, category

def Sliders(floats, ints, bools=None, vecs=None):
    return (floats, ints, bools or [], vecs or [])

panel = SlidersPanel()

src1 = "float a = 1.0; float b = 2.0; int n = 3;"
sliders1 = Sliders(
    floats=[FakeFloat(10, 13, 1.0, 0.0, 2.0, "Global"), FakeFloat(25, 28, 2.0, 0.0, 4.0, "Global")],
    ints=[FakeInt(38, 39, 3, 0, 6, "Global")],
)
panel.rebuild(src1, sliders1)
print("signature:", panel.signature_of(sliders1))

# Simulate a right-click "modifier min/max" override on the second float slider
row = panel._rows[1]
slider, spin = row
spin.setMinimum(-50.0)
spin.setMaximum(50.0)
spin.setDecimals(2)
spin.setValue(12.34)

layout = panel.export_layout()
print("exported layout:", layout)
# Row 1's `step` (0.004) is the auto-step computed for its *original*
# 0..4.0 range at rebuild time -- this simulates a raw `setMinimum`/
# `setMaximum` call bypassing `_edit_range` (which would also recompute
# `singleStep` for the new range), so `spin.singleStep()` no longer
# matches what `export_layout` would derive as "auto" for the new -50..50
# range, and gets exported as an explicit override. That's the intended
# behavior: whatever `singleStep()` a slider actually carries is treated
# as deliberate the moment it stops matching the freshly-recomputed auto
# value for its current range.
assert layout == [
    {"category": "Global", "kind": "float", "index": 0, "min": 0.0, "max": 2.0, "initial_value": 1.0, "decimals": 4},
    {"category": "Global", "kind": "float", "index": 1, "min": -50.0, "max": 50.0, "initial_value": 2.0, "decimals": 2, "step": 0.004},
    {"category": "Global", "kind": "int", "index": 0, "min": 0, "max": 6, "initial_value": 3},
], "unexpected layout export"

# Simulate a structural rebuild (source changed but same literal
# categories/kinds -> same signature) and re-apply the saved layout.
src2 = "float a = 9.0; float b = 8.0; int n = 5;"
sliders2 = Sliders(
    floats=[FakeFloat(10, 13, 9.0, 0.0, 18.0, "Global"), FakeFloat(25, 28, 8.0, 0.0, 16.0, "Global")],
    ints=[FakeInt(38, 39, 5, 0, 10, "Global")],
)
panel.rebuild(src2, sliders2)
panel.apply_layout(layout)

_, spin2 = panel._rows[1]
print("after reapply: min=%s max=%s decimals=%s value=%s" % (spin2.minimum(), spin2.maximum(), spin2.decimals(), spin2.value()))
assert spin2.minimum() == -50.0 and spin2.maximum() == 50.0 and spin2.decimals() == 2
# value should be clamped into [-50, 50] and reflect the new detected value 8.0 (within range)
assert abs(spin2.value() - 8.0) < 1e-6

_, spin0 = panel._rows[0]
print("row0 (frozen from saved layout): min=%s max=%s" % (spin0.minimum(), spin0.maximum()))
# The whole layout is restored (not just explicit overrides) -- that's the
# "figer" (freeze) behavior the roadmap item asks for: reopening the
# project reproduces the exact slider ranges you last had, not freshly
# recomputed 0..2x heuristics.
assert spin0.minimum() == 0.0 and spin0.maximum() == 2.0

# Now simulate reload with a *stale* layout that no longer matches (one fewer float)
src3 = "float a = 1.0; int n = 5;"
sliders3 = Sliders(
    floats=[FakeFloat(10, 13, 1.0, 0.0, 2.0, "Global")],
    ints=[FakeInt(20, 21, 5, 0, 10, "Global")],
)
panel.rebuild(src3, sliders3)
panel.apply_layout(layout)  # entry index=1 has nothing to match now
_, spin_only = panel._rows[0]
print("stale-layout row0: min=%s max=%s" % (spin_only.minimum(), spin_only.maximum()))
assert spin_only.minimum() == 0.0 and spin_only.maximum() == 2.0  # matched index 0 fine (identical here)

print("ALL OK")

# ---- editable step, independent of min/max -----------------------------

panel2 = SlidersPanel()
src4 = "float a = 1.0;"
sliders4 = Sliders(floats=[FakeFloat(10, 13, 1.0, 0.0, 2.0, "Global")], ints=[])
panel2.rebuild(src4, sliders4)
_, step_spin = panel2._rows[0]
auto_step = step_spin.singleStep()
assert abs(auto_step - 2.0 / 1000) < 1e-9, "a freshly built slider should start on the auto step"

# An explicit step override (what `_edit_range` does when the dialog's
# step field is non-zero) must be picked up by export_layout...
step_spin.setSingleStep(0.25)
layout2 = panel2.export_layout()
assert layout2 == [{"category": "Global", "kind": "float", "index": 0, "min": 0.0, "max": 2.0, "initial_value": 1.0, "decimals": 4, "step": 0.25}]
print("explicit step override exported: ok")

# ...and restored verbatim across a structural rebuild, exactly like
# min/max/decimals already are.
sliders4b = Sliders(floats=[FakeFloat(10, 13, 1.5, 0.0, 3.0, "Global")], ints=[])
panel2.rebuild(src4, sliders4b)
panel2.apply_layout(layout2)
_, step_spin2 = panel2._rows[0]
assert abs(step_spin2.singleStep() - 0.25) < 1e-9, "step override must survive a structural rebuild"
print("explicit step override survives structural rebuild: ok")

# A layout entry with no `step` key (old projects saved before this
# feature existed) must fall back to the auto step for the *new* range,
# not crash and not keep whatever step happened to be set before.
layout_no_step = [{"category": "Global", "kind": "float", "index": 0, "min": 0.0, "max": 4.0, "decimals": 4}]
panel2.rebuild(src4, sliders4b)
panel2.apply_layout(layout_no_step)
_, step_spin3 = panel2._rows[0]
assert abs(step_spin3.singleStep() - 4.0 / 1000) < 1e-9, "missing 'step' key must fall back to the auto step for the new range"
print("missing 'step' key in old layouts falls back to auto: ok")

# ---- overrides survive a genuine structural rebuild (RESTE.md) ---------

# Two float sliders, "b" holding a distinctive override (min/max/decimals)
# captured by index=1.
panel3 = SlidersPanel()
src5 = "float a = 1.0; float b = 100.0;"
sliders5 = Sliders(
    floats=[FakeFloat(10, 13, 1.0, 0.0, 2.0, "Global"), FakeFloat(25, 29, 100.0, 0.0, 200.0, "Global")],
    ints=[],
)
panel3.rebuild(src5, sliders5)
_, spin_b = panel3._rows[1]
spin_b.setMinimum(50.0)
spin_b.setMaximum(150.0)
spin_b.setDecimals(1)
layout3 = panel3.export_layout()
assert layout3[1]["initial_value"] == 100.0

# Structural rebuild: a brand new float ("z") is now declared *before* "b"
# in source order, so "b" shifts from index=1 to index=2 within its
# (category, kind) group -- an exact (category, kind, index) match no
# longer finds it. Its value (100.0) is unchanged, so the fallback should
# still recognize it by proximity to the recorded initial_value, rather
# than either silently dropping the override or wrongly handing it to "a"
# (whose own value, 1.0, is nowhere near 100.0).
src6 = "float z = 5.0; float a = 1.0; float b = 100.0;"
sliders6 = Sliders(
    floats=[
        FakeFloat(10, 13, 5.0, 0.0, 10.0, "Global"),
        FakeFloat(25, 28, 1.0, 0.0, 2.0, "Global"),
        FakeFloat(40, 45, 100.0, 0.0, 200.0, "Global"),
    ],
    ints=[],
)
panel3.rebuild(src6, sliders6)
panel3.apply_layout(layout3)
_, spin_z = panel3._rows[0]
_, spin_a = panel3._rows[1]
_, spin_b2 = panel3._rows[2]
assert (spin_b2.minimum(), spin_b2.maximum(), spin_b2.decimals()) == (50.0, 150.0, 1), (
    "override should have followed 'b' to its new index via value-proximity fallback, "
    f"got min={spin_b2.minimum()} max={spin_b2.maximum()} decimals={spin_b2.decimals()}"
)
assert (spin_a.minimum(), spin_a.maximum()) == (0.0, 2.0), "'a' must not have picked up 'b's override"
assert (spin_z.minimum(), spin_z.maximum()) == (0.0, 10.0), "the newly-inserted 'z' must not match anything"
print("override survives a structural rebuild via value-proximity fallback: ok")

# ---- keyframing --------------------------------------------------------

from ui.sliders_panel import _interpolate_keyframes  # noqa: E402

# Piecewise-linear interpolation, held flat outside the recorded range.
kfs = [(0.0, 10.0), (2.0, 20.0), (5.0, 5.0)]
assert _interpolate_keyframes(kfs, -1.0) == 10.0  # before first: held
assert _interpolate_keyframes(kfs, 0.0) == 10.0
assert abs(_interpolate_keyframes(kfs, 1.0) - 15.0) < 1e-9  # midpoint of seg 1
assert _interpolate_keyframes(kfs, 2.0) == 20.0
assert abs(_interpolate_keyframes(kfs, 3.5) - 12.5) < 1e-9  # midpoint of seg 2
assert _interpolate_keyframes(kfs, 5.0) == 5.0
assert _interpolate_keyframes(kfs, 99.0) == 5.0  # after last: held
assert _interpolate_keyframes([(1.0, 7.0)], 42.0) == 7.0  # single keyframe: constant

panel2 = SlidersPanel()
src_kf = "float a = 1.0;"
sliders_kf = Sliders(floats=[FakeFloat(6, 9, 1.0, 0.0, 2.0, "Global")], ints=[])
panel2.rebuild(src_kf, sliders_kf)

_, spin_kf = panel2._rows[0]
recorded = []
panel2.literalEdited.connect(lambda start, end, text: recorded.append(text))

panel2.set_time(0.0)
spin_kf.setValue(0.5)
panel2.add_keyframe(0)  # keyframe (t=0, v=0.5)

panel2.set_time(4.0)
spin_kf.setValue(1.5)
panel2.add_keyframe(0)  # keyframe (t=4, v=1.5)

state_kf = panel2._literals[0]
assert state_kf.keyframes == [(0.0, 0.5), (4.0, 1.5)], state_kf.keyframes

recorded.clear()
panel2.set_time(2.0)  # exact midpoint -> interpolated value 1.0
assert abs(spin_kf.value() - 1.0) < 1e-6, spin_kf.value()
assert recorded, "set_time should have emitted a literalEdited edit for the interpolated value"

recorded.clear()
panel2.set_time(2.0)  # same time again -> value unchanged -> no spurious edit
assert not recorded, "re-applying the same time must not re-emit an edit"

# Re-clicking "add keyframe" near an existing one updates it in place
# instead of creating a near-duplicate.
panel2.set_time(0.02)
spin_kf.setValue(0.9)
panel2.add_keyframe(0)
assert len(state_kf.keyframes) == 2, state_kf.keyframes
assert abs(state_kf.keyframes[0][1] - 0.9) < 1e-6

# Clearing keyframes removes them and stops further interpolation.
panel2.clear_keyframes(0)
assert state_kf.keyframes == []

# Keyframes round-trip through export_layout/apply_layout (project save).
panel2.set_time(0.0)
spin_kf.setValue(0.5)
panel2.add_keyframe(0)
panel2.set_time(4.0)
spin_kf.setValue(1.5)
panel2.add_keyframe(0)
kf_layout = panel2.export_layout()
assert kf_layout[0]["keyframes"] == [[0.0, 0.5], [4.0, 1.5]], kf_layout

panel2.rebuild(src_kf, sliders_kf)  # structural rebuild wipes keyframes...
assert panel2._literals[0].keyframes == []
panel2.apply_layout(kf_layout)  # ...apply_layout restores them
assert panel2._literals[0].keyframes == [(0.0, 0.5), (4.0, 1.5)]

print("KEYFRAMING OK")


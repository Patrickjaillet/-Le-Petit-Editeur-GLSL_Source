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
assert layout == [
    {"category": "Global", "kind": "float", "index": 0, "min": 0.0, "max": 2.0, "decimals": 4},
    {"category": "Global", "kind": "float", "index": 1, "min": -50.0, "max": 50.0, "decimals": 2},
    {"category": "Global", "kind": "int", "index": 0, "min": 0, "max": 6},
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


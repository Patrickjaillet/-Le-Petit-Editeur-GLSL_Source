"""Integration test for the *real* native literal detector.

See M2 in AUDIT.md: `test_sliders.py` only ever exercises `SlidersPanel`
against hand-built `FakeFloat`/`FakeInt`/... objects -- never real GLSL
text run through the actual Rust parser. This file calls
`engine_bridge.detect_all_sliders` (the same function `MainWindow` calls
on every recompile) directly, on real GLSL snippets, so a regression in
the native detector itself -- not just in how `SlidersPanel` reacts to
its output -- fails a test instead of only surfacing later as a UI bug
report.

Requires the native module to be built first (`cd rust_engine && maturin
develop --release`, see `engine_bridge.py`'s import error message); skips
with a clear message rather than failing noisily if it isn't, since a
missing compiled extension is an environment issue, not a code
regression.
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_ui"))

try:
    import engine_bridge
except ImportError as exc:
    print(f"SKIPPED: native module not built ({exc}); "
          f"run 'cd rust_engine && maturin develop --release' first.")
    sys.exit(0)


def counts(sliders):
    floats, ints, bools, vecs = sliders
    return (len(floats), len(ints), len(bools), len(vecs))


# ---- masking: comments, directives, for(...) header ------------------

r = engine_bridge.detect_all_sliders("// EPS = 0.0001, count = 4, ok = true\nfloat a = 1.0;\n")
assert counts(r) == (1, 0, 0, 0), counts(r)
assert r[0][0].value == 1.0

r = engine_bridge.detect_all_sliders("#define EPS 0.0001\nfloat a = 1.0;\n")
assert counts(r) == (1, 0, 0, 0), counts(r)

r = engine_bridge.detect_all_sliders("for(int i = 0; i < 8; i++) { float a = 1.0; }\n")
assert counts(r) == (1, 0, 0, 0), counts(r)  # top-level for(...): masked

# ---- unary minus vs. binary subtraction -------------------------------

r = engine_bridge.detect_all_sliders("float a = -1.0;\n")
assert counts(r) == (1, 0, 0, 0), counts(r)
assert r[0][0].value == -1.0

r = engine_bridge.detect_all_sliders("float a = b - 1.0;\n")
assert counts(r) == (1, 0, 0, 0), counts(r)
assert r[0][0].value == 1.0

# ---- vec2/vec3 grouping: positive / negative / mixed ------------------

r = engine_bridge.detect_all_sliders("vec3 col = vec3(0.1, 0.2, 0.3);\n")
assert counts(r) == (0, 0, 0, 1), counts(r)
assert list(r[3][0].values) == [0.1, 0.2, 0.3]

# The M1 regression (see AUDIT.md): previously fell through to 3 separate
# floats instead of one grouped VecSlider.
r = engine_bridge.detect_all_sliders("vec3 dir = vec3(-1.0, 0.5, 0.2);\n")
assert counts(r) == (0, 0, 0, 1), counts(r)
assert list(r[3][0].values) == [-1.0, 0.5, 0.2]

r = engine_bridge.detect_all_sliders("vec2 off = vec2(-0.3, 0.4);\n")
assert counts(r) == (0, 0, 0, 1), counts(r)
assert list(r[3][0].values) == [-0.3, 0.4]

# ---- splat / expressions: deliberately NOT grouped ---------------------

r = engine_bridge.detect_all_sliders("vec3 grey = vec3(0.5);\n")
assert counts(r) == (1, 0, 0, 0), counts(r)  # splat -> single float slider

r = engine_bridge.detect_all_sliders("vec3 v = vec3(a, b, c);\n")
assert counts(r) == (0, 0, 0, 0), counts(r)  # pure expression -> nothing

# ---- bool / int -------------------------------------------------------

r = engine_bridge.detect_all_sliders("bool on = true;\nbool off = false;\n")
assert counts(r) == (0, 0, 2, 0), counts(r)
assert r[2][0].value is True
assert r[2][1].value is False

r = engine_bridge.detect_all_sliders("int n = 4;\n")
assert counts(r) == (0, 1, 0, 0), counts(r)
assert r[1][0].value == 4

# ---- a realistic mainImage snippet -------------------------------------
# Exercises category assignment (function name + section marker) and vec3
# grouping together, on something shaped like real Shadertoy code rather
# than a single-line fragment. Deliberately has no `for(...)` loop inside
# the function body -- see M5 in AUDIT.md for that separate, still-open
# gap, kept out of this test so it stays focused on what's already fixed.

realistic_src = """
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    // -- Couleur --
    vec3 baseColor = vec3(-0.1, 0.4, 0.8);
    float exposure = 1.5;
    fragColor = vec4(baseColor * exposure, 1.0);
}
"""
r = engine_bridge.detect_all_sliders(realistic_src)
n_floats, n_ints, n_bools, n_vecs = counts(r)
# baseColor groups into one VecSlider (vec3, negative component -- M1);
# `exposure`'s 1.5 and the `1.0` splat argument of the (ungrouped, per m1
# in AUDIT.md) vec4(...) call surface as 2 standalone floats.
assert (n_floats, n_ints, n_bools, n_vecs) == (2, 0, 0, 1), (n_floats, n_ints, n_bools, n_vecs)
assert list(r[3][0].values) == [-0.1, 0.4, 0.8]
categories = {lit.category for lit in r[0]}  # floats
assert categories == {"mainImage — Couleur"}, categories

print("ALL OK (native detector, real GLSL)")

"""RM10.md section 1, items 7/8 ("carte graphique non prise en charge" /
"mémoire insuffisante") -- real functional coverage, exercising the actual
compiled native module rather than reading the Rust source and assuming it
behaves as written.

Background this test guards against regressing: `Engine::new` used to
request `wgpu::Limits::downlevel_defaults()`, which caps
`max_texture_dimension_2d` at 2048 regardless of what the real adapter
supports -- confirmed by reproducing the bug before the fix
(`Engine(20000, 20000)` raised an *uncatchable* `pyo3_runtime.PanicException`,
not a `RuntimeError`, crashing any call site that only had `except
RuntimeError`). Fixed by requesting the adapter's own reported limits
instead. Separately, even a resolution *within* that corrected limit can
still exceed available VRAM (reproduced: `resize(20000, 20000)` failed with
wgpu's own "Not enough memory left" deep inside `Queue::write_texture`) --
fixed with a `push_error_scope`/`pop_error_scope` pair around the
allocation in both `Engine::new` and `Engine::resize`, converting what used
to reach wgpu's default *panicking* uncaptured-error handler into a normal,
catchable `RuntimeError` instead.

Needs the native module built (`engine_bridge` import) -- absent, SKIPs
cleanly, same convention as the other native-module-dependent tests here.
Needs an actual usable graphics adapter (SKIPs if `Engine(64, 64)` itself
can't be constructed, same convention as `test_dialect_detection.py`).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python_ui"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    import engine_bridge
except ImportError as exc:
    print(f"SKIPPED: native module not built ({exc}); "
          f"run 'cd rust_engine && maturin develop --release' first.")
    sys.exit(0)

try:
    probe = engine_bridge.Engine(64, 64)
except RuntimeError as exc:
    print(f"SKIPPED: no usable graphics adapter in this environment ({exc}).")
    sys.exit(0)

# ---- 1. max_texture_dimension reflects the real adapter, not a fixed 2048 -

max_dim = probe.max_texture_dimension()
assert isinstance(max_dim, int) and max_dim > 0
assert max_dim != 2048, (
    "still looks like the old wgpu::Limits::downlevel_defaults() ceiling -- "
    "either this machine's real GPU limit genuinely happens to be exactly "
    "2048 (extremely unlikely for any GPU still receiving driver updates), "
    "or the regression this test guards against is back"
)
print(f"max_texture_dimension() reflects the real adapter ({max_dim}px), not a fixed downlevel default: ok")

# ---- 2. A resolution beyond the true limit fails cleanly, not via panic --

huge = max_dim * 4
try:
    probe.resize(huge, huge)
    raise AssertionError(f"resize({huge}, {huge}) should have failed (max is {max_dim})")
except RuntimeError as exc:
    assert str(exc), "the error message must not be empty"
print("resize() far beyond max_texture_dimension() fails as a catchable RuntimeError, not a panic: ok")

# The engine must remain fully usable after a failed resize -- left at its
# previous, still-working resolution rather than half-updated/corrupted.
probe.compile_pass(
    engine_bridge.PASS_IMAGE,
    "void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(0.0, 1.0, 0.0, 1.0); }",
)
probe.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 1.0, 1.0, 0.0))
pixels = bytes(probe.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 1.0, 1.0, 0.0)))
assert len(pixels) == 64 * 64 * 4
assert (pixels[0], pixels[1], pixels[2], pixels[3]) == (0, 255, 0, 255), (
    "engine should still render correctly after a failed resize"
)
print("engine remains fully usable (still renders correctly) after a failed resize: ok")

# ---- 3. A too-large *initial* resolution also fails cleanly (Engine::new) -

try:
    engine_bridge.Engine(huge, huge)
    raise AssertionError(f"Engine({huge}, {huge}) should have failed (max is {max_dim})")
except RuntimeError as exc:
    assert str(exc)
print("Engine(huge, huge) construction fails as a catchable RuntimeError, not a panic: ok")

# ---- 4. A resolution that fits comfortably still works (no false positive) -

small_engine = engine_bridge.Engine(256, 256)
small_engine.resize(512, 512)
small_engine.compile_pass(
    engine_bridge.PASS_IMAGE,
    "void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0, 0.0, 0.0, 1.0); }",
)
small_engine.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 1.0, 1.0, 0.0))
pixels2 = bytes(small_engine.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 1.0, 1.0, 0.0)))
assert len(pixels2) == 512 * 512 * 4
assert (pixels2[0], pixels2[1], pixels2[2], pixels2[3]) == (255, 0, 0, 255)
print("an ordinary, well-within-limits resize still works normally (no false positive): ok")

print("\nALL OK")

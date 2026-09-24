"""RESTE.md / ROADMAP.md: real GPU pixel-identical verification of the golf
transforms that were, until now, only ever checked by text-level Rust unit
tests -- no Rust toolchain capable of compiling the full `wgpu`/`image`
dependency tree was available in any prior session, so "rendu GPU
pixel-identique" was asserted in the roadmap's prose but never actually
run. This is the first time it can be.

Strategy: compile+render the *same* fragColor-computing GLSL both as
written and after `golf_shader_ex` (all combinations of rename/dead-code),
and assert the raw RGBA8 output bytes match exactly. `default.frag` is the
project's own standing regression shader (referenced throughout
`golf.rs`'s doc comments), and the extra snippets below each isolate one
of the transforms flagged in RESTE.md as "verified only by relecture
manuelle, jamais recompilé" (constant folding, ternary-from-if/else,
inlining, vector splat, `in`-qualifier removal).
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))

import engine_bridge  # noqa: E402


def render(src: str, w: int = 48, h: int = 32) -> bytes:
    eng = engine_bridge.Engine(w, h)
    eng.compile_pass(engine_bridge.PASS_IMAGE, src)
    eng.render(0.0, 0.0, (0, 0, 0, 0), 0, (2024, 1, 1, 0))  # bootstrap frame (pipelined readback)
    return eng.render(1.23, 0.016, (0, 0, 0, 0), 1, (2024, 1, 1, 0))


def check_pixel_identical(label: str, src: str) -> None:
    original_px = render(src)
    for rename in (False, True):
        for dead_code in (False, True):
            golfed = engine_bridge.golf_shader_ex(src, "", rename, dead_code)
            golfed_px = render(golfed)
            assert golfed_px == original_px, (
                f"{label}: golf (rename={rename}, dead_code={dead_code}) is NOT "
                f"pixel-identical -- {sum(a != b for a, b in zip(original_px, golfed_px))} "
                f"byte(s) differ out of {len(original_px)}"
            )
    print(f"{label}: pixel-identical across all 4 rename x dead_code combinations: ok")


# ---- 1. The project's own standing regression shader ----------------------

default_frag = open(os.path.join(PROJECT_ROOT, "python_ui", "assets", "shaders", "default.frag")).read()
check_pixel_identical("default.frag", default_frag)

# ---- 2. Constant folding (`2.*3.` -> `6.`) ---------------------------------

check_pixel_identical(
    "constant folding",
    """
    void mainImage(out vec4 fragColor, in vec2 fragCoord) {
        float a = 2.0 * 3.0 - 1.0 + 4.0 * 2.0;
        vec2 uv = fragCoord / iResolution.xy;
        fragColor = vec4(uv * a * 0.1, 0.5, 1.0);
    }
    """,
)

# ---- 3. Ternary from if/else (including a nested/composed case) -----------

check_pixel_identical(
    "ternary from if/else (nested + chained)",
    """
    void mainImage(out vec4 fragColor, in vec2 fragCoord) {
        vec2 uv = fragCoord / iResolution.xy;
        float c;
        if (uv.x > 0.5) {
            if (uv.y > 0.5) { c = 1.0; } else { c = 2.0; }
        } else if (uv.y > 0.25) {
            c = 3.0;
        } else {
            c = 4.0;
        }
        fragColor = vec4(uv, c * 0.1, 1.0);
    }
    """,
)

# ---- 4. Inlining of single- and multi-call-site helper functions ----------

check_pixel_identical(
    "function inlining (single + multi call site)",
    """
    float sdCircle(vec2 p, float r) { return length(p) - r; }
    float lum(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
    void mainImage(out vec4 fragColor, in vec2 fragCoord) {
        vec2 uv = (fragCoord - 0.5 * iResolution.xy) / iResolution.y;
        float d1 = sdCircle(uv, 0.3);
        float d2 = sdCircle(uv - vec2(0.2, 0.0), 0.2);
        vec3 col = vec3(1.0 - smoothstep(0.0, 0.02, d1), 0.5, 1.0 - smoothstep(0.0, 0.02, d2));
        fragColor = vec4(col, lum(col));
    }
    """,
)

# ---- 5. Vector constructor splat (`vec3(v,v,v)` -> `vec3(v)`) -------------

check_pixel_identical(
    "vector constructor splat",
    """
    void mainImage(out vec4 fragColor, in vec2 fragCoord) {
        vec2 uv = fragCoord / iResolution.xy;
        float v = uv.x * 0.5 + 0.25;
        vec3 col = vec3(v, v, v);
        fragColor = vec4(col, 1.0);
    }
    """,
)

# ---- 6. Default `in` qualifier removal on function parameters -------------

check_pixel_identical(
    "in-qualifier removal",
    """
    vec3 tint(in vec3 c, in float amt) { return mix(c, vec3(1.0), amt); }
    void mainImage(out vec4 fragColor, in vec2 fragCoord) {
        vec2 uv = fragCoord / iResolution.xy;
        fragColor = vec4(tint(vec3(uv, 0.5), 0.2), 1.0);
    }
    """,
)

# ---- 7. Compound assignment generalization + for-loop condensation -------

check_pixel_identical(
    "compound assignment + condensed for loop",
    """
    void mainImage(out vec4 fragColor, in vec2 fragCoord) {
        vec2 uv = fragCoord / iResolution.xy;
        float accum = 0.0;
        for (float i = 0.0; i < 8.0; i++) {
            accum = accum + uv.x * 0.1;
            accum = accum * 1.01;
        }
        fragColor = vec4(uv, accum, 1.0);
    }
    """,
)

print("\nALL OK")

"""RESTE.md: "pas de génération procédurale de cubemap (seulement
chargement depuis 6 fichiers image)". `Engine.set_ichannel_procedural_cubemap`
(rust_engine/src/texture.rs::ChannelTexture::procedural_cubemap) generates
all 6 faces of a cubemap from the existing built-in presets (checker,
white_noise, value_noise) instead of requiring 6 image files.

Real GPU verification (not just "the Rust unit test on seed derivation
passes"): compiles a real cubemap-reflecting shader, assigns each preset,
and renders through a real device (lavapipe/Vulkan in this environment).

Important ordering note this test also documents: a pass must already
have *some* successfully-compiled source (`pass_sources[pass]` populated)
before assigning a channel can trigger the "recompile automatically"
convenience -- assigning the channel before the first compile sidesteps
that entirely, since the fresh compile already sees the channel's real
kind. Compiling first with GLSL that assumes a channel kind it doesn't
have yet (e.g. `texture(iChannel0, vec3)` against a still-default 2D
channel) fails immediately, same as it always would.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))

import engine_bridge  # noqa: E402

CUBEMAP_SHADER = """
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec3 dir = normalize(vec3(fragCoord / iResolution.xy - 0.5, 1.0));
    fragColor = texture(iChannel0, dir);
}
"""


def render_with_preset(kind: str, scale: int = 8, seed: int = 0) -> bytes:
    eng = engine_bridge.Engine(24, 24)
    # Assign *before* the first compile: see module docstring.
    eng.set_ichannel_procedural_cubemap(engine_bridge.PASS_IMAGE, 0, kind, scale, seed)
    eng.compile_pass(engine_bridge.PASS_IMAGE, CUBEMAP_SHADER)
    eng.render(0.0, 0.0, (0, 0, 0, 0), 0, (2024, 1, 1, 0))  # bootstrap (pipelined readback)
    return eng.render(0.0, 0.0, (0, 0, 0, 0), 1, (2024, 1, 1, 0))


for kind in ("checker", "white_noise", "value_noise"):
    px = render_with_preset(kind)
    assert len(px) == 24 * 24 * 4
    assert any(b != 0 for b in px), f"{kind}: cubemap render is entirely black"
    print(f"{kind} procedural cubemap renders real non-black pixels: ok")

px_checker = render_with_preset("checker")
px_white = render_with_preset("white_noise")
px_value = render_with_preset("value_noise")
assert px_checker != px_white != px_value
print("the 3 presets render visibly different cubemaps: ok")

# Determinism: same preset/scale/seed must render identically every time
# (matches the existing 2D `procedural` preset's own determinism
# guarantee -- a project reload must look the same).
assert render_with_preset("white_noise", seed=42) == render_with_preset("white_noise", seed=42)
print("same seed renders identically across engine instances: ok")

# A different seed must actually change the result for a noise-based
# preset (proves the seed argument is wired through, not ignored).
assert render_with_preset("white_noise", seed=1) != render_with_preset("white_noise", seed=2)
print("different seeds render differently for a noise preset: ok")

# Reassigning an existing procedural-cubemap channel from one preset to
# another on an already-compiled pass must also work (the "type stays
# Cube, only its contents change" no-recompile-needed path).
eng = engine_bridge.Engine(16, 16)
eng.set_ichannel_procedural_cubemap(engine_bridge.PASS_IMAGE, 0, "checker", 8, 0)
eng.compile_pass(engine_bridge.PASS_IMAGE, CUBEMAP_SHADER)
eng.render(0.0, 0.0, (0, 0, 0, 0), 0, (2024, 1, 1, 0))
before = eng.render(0.0, 0.0, (0, 0, 0, 0), 1, (2024, 1, 1, 0))
eng.set_ichannel_procedural_cubemap(engine_bridge.PASS_IMAGE, 0, "value_noise", 8, 0)
eng.render(0.0, 0.0, (0, 0, 0, 0), 2, (2024, 1, 1, 0))
after = eng.render(0.0, 0.0, (0, 0, 0, 0), 3, (2024, 1, 1, 0))
assert before != after, "reassigning the preset on an already-compiled Cube channel had no effect"
print("reassigning preset on an already-compiled channel updates the render: ok")

print("\nALL OK")

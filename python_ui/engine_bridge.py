"""Thin wrapper around the compiled Rust extension module.

Keeps a single import point so the rest of the UI doesn't need to know
about the native module name or handle the "not built yet" error message.
"""
from __future__ import annotations

try:
    import shadertoy_engine as _native
except ImportError as exc:  # pragma: no cover - developer feedback path
    raise ImportError(
        "Le module natif 'shadertoy_engine' est introuvable. "
        "Compilez-le d'abord avec: cd rust_engine && maturin develop --release"
    ) from exc

Engine = _native.Engine
LiteralSlider = _native.LiteralSlider
IntSlider = _native.IntSlider
BoolSlider = _native.BoolSlider
VecSlider = _native.VecSlider
detect_literal_sliders = _native.detect_literal_sliders
detect_all_sliders = _native.detect_all_sliders
golf_shader = _native.golf_shader
golf_common = _native.golf_common
golf_shader_with_common = _native.golf_shader_with_common
golf_shader_ex = _native.golf_shader_ex
beautify_shader = _native.beautify_shader
fragment_header_line_count = _native.fragment_header_line_count
fragment_header_line_count_for_dialect = _native.fragment_header_line_count_for_dialect
detect_dialect = _native.detect_dialect
DIALECT_SHADERTOY = _native.DIALECT_SHADERTOY
DIALECT_GLSL = _native.DIALECT_GLSL
DIALECT_WGSL = _native.DIALECT_WGSL

# Shadertoy-style multi-pass indices (Buffer A-D feed each other and the
# final Image pass, which is the only one actually displayed).
PASS_BUFFER_A = _native.PASS_BUFFER_A
PASS_BUFFER_B = _native.PASS_BUFFER_B
PASS_BUFFER_C = _native.PASS_BUFFER_C
PASS_BUFFER_D = _native.PASS_BUFFER_D
PASS_IMAGE = _native.PASS_IMAGE
BUFFER_PASSES = (PASS_BUFFER_A, PASS_BUFFER_B, PASS_BUFFER_C, PASS_BUFFER_D)
ALL_PASSES = BUFFER_PASSES + (PASS_IMAGE,)
PASS_LABELS = {
    PASS_BUFFER_A: "Buffer A",
    PASS_BUFFER_B: "Buffer B",
    PASS_BUFFER_C: "Buffer C",
    PASS_BUFFER_D: "Buffer D",
    PASS_IMAGE: "Image",
}

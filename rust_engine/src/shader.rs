pub const VERTEX_SRC: &str = r#"#version 450
void main() {
    vec2 pos = vec2(float((gl_VertexIndex << 1) & 2), float(gl_VertexIndex & 2));
    gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}
"#;

/// Which combined-image-sampler type an iChannel slot's shader binding
/// declares. Plain 2D covers images, procedural presets, Buffer targets,
/// and an empty/placeholder slot — they're all sampled as `sampler2D` and
/// share one binding-layout shape; only a cubemap slot needs the distinct
/// `samplerCube` declaration (and the matching `Cube`-dimension binding
/// wgpu validates the pipeline against).
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ChannelKind {
    D2,
    Cube,
}

/// Wraps the user's `mainImage` GLSL fragment code (plain, 100%
/// Shadertoy-compatible: no custom uniforms, no annotations) with the
/// harness: globals UBO (iResolution/iTime/iMouse/...) and iChannel0-3
/// samplers exposed via separate texture+sampler bindings. `channel_kinds`
/// picks `sampler2D` or `samplerCube` per slot to match whatever's
/// actually bound there (see `renderer::Engine::compile_pass`); it never
/// changes the number of lines emitted, only the type tokens on each
/// existing line, so `header_line_count` stays valid regardless of which
/// kinds are passed in.
pub fn build_fragment_source(user_src: &str, channel_kinds: [ChannelKind; 4], force_opaque: bool) -> String {
    let mut channel_decls = String::new();
    let mut channel_defines = String::new();
    for (i, kind) in channel_kinds.iter().enumerate() {
        let (tex_type, sampler_ctor) = match kind {
            ChannelKind::D2 => ("texture2D", "sampler2D"),
            ChannelKind::Cube => ("textureCube", "samplerCube"),
        };
        let tex_binding = 1 + i * 2;
        let sampler_binding = 2 + i * 2;
        channel_decls.push_str(&format!(
            "layout(set = 0, binding = {tex_binding}) uniform {tex_type} texChannel{i};\nlayout(set = 0, binding = {sampler_binding}) uniform sampler sampChannel{i};\n"
        ));
        channel_defines.push_str(&format!(
            "#define iChannel{i} {sampler_ctor}(texChannel{i}, sampChannel{i})\n"
        ));
    }
    // Shadertoy's canvas is created opaque (WebGL `alpha: false`): whatever
    // a shader leaves in `fragColor.a` never affects what's displayed, it's
    // always shown fully opaque. Buffer A-D targets are never displayed
    // directly and are commonly used by shaders to stash arbitrary data in
    // their alpha channel for feedback into a later pass (a common
    // Shadertoy GPGPU idiom), so only the Image pass gets this override —
    // forcing it there too would silently corrupt that data.
    let opaque_override = if force_opaque {
        "\n    fragColOut.a = 1.0;"
    } else {
        ""
    };
    format!(
        r#"#version 450

layout(location = 0) out vec4 fragColOut;

layout(set = 0, binding = 0) uniform Globals {{
    vec4 iResolution;
    vec4 iMouse;
    float iTime;
    float iTimeDelta;
    int iFrame;
    float _pad0;
    vec4 iDate;
    float iSampleRate;
    float _pad1;
    float _pad2;
    float _pad3;
    vec4 iChannelResolution[4];
    float iChannelTime[4];
}};

{channel_decls}
{channel_defines}
{user_code}

void main() {{
    vec4 fragColor = vec4(0.0);
    // wgpu's fragment coordinate origin is top-left (y down); flip to match
    // Shadertoy's bottom-left origin convention.
    vec2 shadertoyFragCoord = vec2(gl_FragCoord.x, iResolution.y - gl_FragCoord.y);
    mainImage(fragColor, shadertoyFragCoord);
    fragColOut = fragColor;{opaque_override}
}}
"#,
        user_code = user_src,
    )
}

/// Number of lines that precede a given pass's own code inside the
/// generated fragment source (harness header + the `Common` source, if
/// any, exactly as `Engine::compile_pass` concatenates them), so the UI
/// can translate naga/wgpu compile-error line numbers (which refer to the
/// fully wrapped source) back to the line the user actually sees in that
/// pass's editor tab.
pub fn header_line_count(common_src: &str, user_src: &str) -> usize {
    let combined = if common_src.trim().is_empty() {
        user_src.to_string()
    } else {
        format!("{common_src}\n{user_src}")
    };
    // The kinds passed here don't affect line count (see doc comment on
    // `build_fragment_source`), so a fixed all-D2 array is fine even
    // though the pass being diagnosed might actually have a cubemap bound.
    let full = build_fragment_source(&combined, [ChannelKind::D2; 4], false);
    // `user_src` is searched for directly (not `combined`): its position
    // within `full` already accounts for both the harness header and the
    // `Common` source that precedes it, giving a line count relative to
    // user_src itself — exactly what the pass's own editor tab shows.
    match full.find(user_src) {
        Some(idx) => full[..idx].matches('\n').count(),
        None => 0,
    }
}

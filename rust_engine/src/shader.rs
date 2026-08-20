use crate::dialect;

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

// ---------------------------------------------------------------------
// Mode GLSL standalone (roadmap1.md, section "Compilation réellement
// double-dialecte") : le `void main()` de l'utilisateur est l'entrée
// réelle du fragment shader, pas de wrapper `mainImage`. Contrairement au
// chemin Shadertoy ci-dessus (jamais modifié par ce qui suit), toute
// l'injection de harness devient conditionnelle : on n'ajoute que ce que
// le code ne fournit pas déjà lui-même.
// ---------------------------------------------------------------------

/// Premier binding libre après Globals (0) et les 4 paires
/// texture+sampler des iChannel0-3 (1..=8) : c'est là que les `uniform`
/// personnalisés détectés par `detect_custom_uniforms` sont assignés,
/// chacun dans son propre petit bloc UBO (voir `CustomUniformDecl`).
const FIRST_CUSTOM_UNIFORM_BINDING: u32 = 9;

/// Noms des champs du bloc `Globals` (voir `build_fragment_source`) : si
/// aucun n'est référencé dans le code utilisateur, le bloc entier est omis
/// en mode standalone plutôt que déclaré-mais-jamais-lu.
const GLOBALS_FIELD_NAMES: [&str; 9] = [
    "iResolution",
    "iMouse",
    "iTime",
    "iTimeDelta",
    "iFrame",
    "iDate",
    "iSampleRate",
    "iChannelResolution",
    "iChannelTime",
];

/// Types GLSL scalaires/vecteurs supportés pour un `uniform` personnalisé
/// auto-bindé (mêmes types que le détecteur de sliders de `literals.rs`,
/// par cohérence — voir doc de `CustomUniformDecl`).
const CUSTOM_UNIFORM_TYPES: [&str; 6] = ["float", "int", "bool", "vec2", "vec3", "vec4"];

/// Un `uniform` déclaré par l'utilisateur dans un shader standalone, sans
/// qu'aucun mécanisme du logiciel ne lui fournisse de valeur (roadmap1.md :
/// « décider s'ils sont simplement acceptés avec une valeur par défaut à 0
/// [...], ou récupérés par le détecteur de sliders existant — à trancher
/// avant l'implémentation »). Choix retenu ici, le plus simple des deux :
/// accepté tel quel, avec une valeur par défaut à 0 dans un petit UBO
/// dédié auto-bindé (`renderer::Engine::compile_pass` crée le buffer
/// zero-fill correspondant) — le shader compile et s'affiche, quitte à
/// être visuellement "cassé" si l'utilisateur attendait une vraie valeur.
/// Le brancher sur le panneau de sliders existant (`literals.rs`) reste
/// possible plus tard sans changer cette structure : `glsl_type`/`name`
/// sont déjà exactement ce qu'un futur pont vers `literals::LiteralSlider`
/// consommerait, seul `renderer.rs` aurait à écrire autre chose que des
/// zéros dans le buffer.
#[derive(Debug, Clone)]
pub struct CustomUniformDecl {
    pub glsl_type: &'static str,
    pub name: String,
    pub binding: u32,
}

/// Repère les déclarations `uniform <type> <nom>;` de premier niveau dans
/// `stripped` (déjà débarrassé de ses commentaires par l'appelant) qui ne
/// sont ni un champ `Globals`, ni `iChannel0-3`, ni déjà précédées d'un
/// `layout(...)` explicite (l'utilisateur gère alors déjà lui-même son
/// binding — on ne touche pas à ce qu'il a manifestement fait exprès).
/// Volontairement conservateur : un seul nom par déclaration (pas de
/// `uniform float a, b;`), pas de tableau (`uniform float a[4];`), pas
/// d'initialiseur — toute déclaration qui s'écarte de ce moule simple est
/// laissée telle quelle (et échouera probablement à la compilation, comme
/// avant ce chantier, plutôt que de risquer une réécriture incorrecte).
fn detect_custom_uniforms(stripped: &str) -> Vec<(&'static str, String)> {
    let chars: Vec<char> = stripped.chars().collect();
    let n = chars.len();
    let mut out = Vec::new();
    let mut i = 0;
    while i < n {
        if !dialect::is_ident_start(chars[i]) {
            i += 1;
            continue;
        }
        let word_start = i;
        while i < n && dialect::is_ident_char(chars[i]) {
            i += 1;
        }
        let word: String = chars[word_start..i].iter().collect();
        if word != "uniform" {
            continue;
        }
        // Refuse si le token non-espace juste avant "uniform" est ")" —
        // signe d'un `layout(...)` explicite qui précède déjà.
        let mut b = word_start;
        while b > 0 && chars[b - 1].is_whitespace() {
            b -= 1;
        }
        if b > 0 && chars[b - 1] == ')' {
            continue;
        }
        // Type : mot suivant.
        let mut t = i;
        while t < n && chars[t].is_whitespace() {
            t += 1;
        }
        let type_start = t;
        while t < n && dialect::is_ident_char(chars[t]) {
            t += 1;
        }
        let glsl_type: String = chars[type_start..t].iter().collect();
        let Some(known_type) = CUSTOM_UNIFORM_TYPES.iter().find(|k| **k == glsl_type) else {
            continue;
        };
        // Nom : mot suivant.
        let mut m = t;
        while m < n && chars[m].is_whitespace() {
            m += 1;
        }
        let name_start = m;
        while m < n && dialect::is_ident_char(chars[m]) {
            m += 1;
        }
        if m == name_start {
            continue;
        }
        let name: String = chars[name_start..m].iter().collect();
        // Doit être immédiatement suivi (au-delà des espaces) d'un ";" —
        // sinon tableau/initialiseur/liste de noms, hors périmètre.
        let mut e = m;
        while e < n && chars[e].is_whitespace() {
            e += 1;
        }
        if e >= n || chars[e] != ';' {
            continue;
        }
        if GLOBALS_FIELD_NAMES.contains(&name.as_str()) || (name.starts_with("iChannel") && name.len() == 9) {
            continue;
        }
        out.push((*known_type, name));
    }
    out
}

/// Vrai si `stripped` contient déjà une déclaration `out vec4 <nom>;`
/// (avec ou sans `layout(...)` devant) — signe que l'utilisateur a son
/// propre point de sortie moderne, auquel cas `gl_FragColor`/`gl_FragData`
/// ne doivent pas être traduits automatiquement (voir
/// `translate_legacy_frag_output`).
fn has_out_vec4_declaration(stripped: &str) -> bool {
    let chars: Vec<char> = stripped.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if !dialect::is_ident_start(chars[i]) {
            i += 1;
            continue;
        }
        let start = i;
        while i < n && dialect::is_ident_char(chars[i]) {
            i += 1;
        }
        if chars[start..i].iter().collect::<String>() != "out" {
            continue;
        }
        let mut t = i;
        while t < n && chars[t].is_whitespace() {
            t += 1;
        }
        let type_start = t;
        while t < n && dialect::is_ident_char(chars[t]) {
            t += 1;
        }
        if chars[type_start..t].iter().collect::<String>() == "vec4" {
            return true;
        }
    }
    false
}

/// Remplace chaque occurrence de `word` comme identifiant à part entière
/// (jamais comme sous-chaîne, même logique que
/// `dialect::contains_whole_word`) par `replacement` dans `source`.
fn replace_whole_word(source: &str, word: &str, replacement: &str) -> String {
    let chars: Vec<char> = source.chars().collect();
    let word_chars: Vec<char> = word.chars().collect();
    let n = chars.len();
    let wn = word_chars.len();
    let mut out = String::with_capacity(source.len());
    let mut i = 0;
    while i < n {
        if wn <= n - i && chars[i..i + wn] == word_chars[..] {
            let before_ok = i == 0 || !dialect::is_ident_char(chars[i - 1]);
            let after_ok = i + wn == n || !dialect::is_ident_char(chars[i + wn]);
            if before_ok && after_ok {
                out.push_str(replacement);
                i += wn;
                continue;
            }
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

/// Traduit le style de sortie fragment historique GLSL ES 1.0 / ancien
/// OpenGL (`gl_FragColor`, `gl_FragData[0]`) — non compris par le frontend
/// GLSL de naga/wgpu, qui n'accepte que le style moderne `out vec4` — vers
/// une variable `out vec4` fraîchement déclarée. No-op si le code ne
/// contient ni l'un ni l'autre, ou s'il déclare déjà lui-même un `out
/// vec4` (cf. `has_out_vec4_declaration` : un `gl_FragColor` qui traînerait
/// à côté d'un point de sortie moderne déjà déclaré serait de toute façon
/// un shader cassé indépendamment de ce chantier — on ne devine pas).
/// `gl_FragData` n'est traduit que pour l'index `[0]` (seule sortie unique
/// supportée par ce moteur, pas de MRT) ; un autre index est laissé tel
/// quel et échouera à la compilation, comme avant ce chantier.
fn translate_legacy_frag_output(stripped_for_detection: &str, source: &str) -> (String, bool) {
    if has_out_vec4_declaration(stripped_for_detection) {
        return (source.to_string(), false);
    }
    let uses_frag_color = dialect::contains_whole_word(stripped_for_detection, "gl_FragColor");
    let uses_frag_data = dialect::contains_whole_word(stripped_for_detection, "gl_FragData");
    if !uses_frag_color && !uses_frag_data {
        return (source.to_string(), false);
    }
    const LEGACY_OUT_NAME: &str = "fragColOutLegacy";
    let mut replaced = source.to_string();
    if uses_frag_color {
        replaced = replace_whole_word(&replaced, "gl_FragColor", LEGACY_OUT_NAME);
    }
    if uses_frag_data {
        // `gl_FragData` est un tableau ; seule la forme `gl_FragData[0]`
        // (espacement variable autour des crochets) est traduite.
        replaced = replaced
            .replace("gl_FragData[0]", LEGACY_OUT_NAME)
            .replace("gl_FragData [0]", LEGACY_OUT_NAME)
            .replace("gl_FragData[ 0]", LEGACY_OUT_NAME)
            .replace("gl_FragData[0 ]", LEGACY_OUT_NAME)
            .replace("gl_FragData [ 0 ]", LEGACY_OUT_NAME);
    }
    (replaced, true)
}

/// Équivalent, pour le mode GLSL standalone, de `build_fragment_source` :
/// compile le code utilisateur quasiment tel quel, son `void main()` étant
/// l'entrée réelle du fragment shader (pas de wrapper `mainImage`).
/// Chaque morceau de harness Shadertoy n'est injecté que si le code en a
/// réellement besoin :
/// - `#version` : respecté s'il est déjà présent (une deuxième directive
///   serait une erreur de compilation garantie) ; sinon `#version 450` est
///   injecté, pour rester cohérent avec le chemin Shadertoy.
/// - Bloc `Globals` (iResolution/iTime/iMouse/...) : injecté seulement si
///   au moins un de ses champs est référencé dans le code (recherche
///   textuelle simple, même logique légère qu'ailleurs dans ce fichier) —
///   sinon un `uniform` déclaré mais jamais lu, pas nécessaire pour un
///   shader qui n'en a pas besoin.
/// - iChannel0-3 : chaque paire texture+sampler n'est déclarée que si ce
///   slot précis est référencé.
/// - `gl_FragColor`/`gl_FragData[0]` : traduits automatiquement vers un
///   `out vec4` si besoin (voir `translate_legacy_frag_output`).
/// - `uniform` personnalisés sans binding explicite : auto-bindés avec une
///   valeur par défaut à 0 (voir `CustomUniformDecl`) ; la liste retournée
///   permet à `renderer::Engine::compile_pass` de créer les buffers
///   zero-fill correspondants et d'étendre le bind group en conséquence.
///
/// Retourne `(source_complète, uniformes_personnalisés_détectés)`.
pub fn build_fragment_source_standalone(
    user_src: &str,
    channel_kinds: [ChannelKind; 4],
) -> (String, Vec<CustomUniformDecl>) {
    let stripped = dialect::strip_comments(user_src);

    let (translated_src, _did_translate) = translate_legacy_frag_output(&stripped, user_src);
    // Le texte traduit peut différer de `user_src` (gl_FragColor renommé) :
    // toute détection ultérieure sur le contenu utilisateur re-tokenise ce
    // texte traduit pour rester cohérente avec ce qui sera effectivement
    // émis, sauf la détection de `#version`/des globals/iChannel*, qui ne
    // porte que sur des identifiants jamais touchés par cette traduction.

    let version_line = if stripped.contains("#version") {
        String::new()
    } else {
        "#version 450\n".to_string()
    };

    let legacy_out_decl = if _did_translate {
        "layout(location = 0) out vec4 fragColOutLegacy;\n"
    } else {
        ""
    };

    let needs_globals = GLOBALS_FIELD_NAMES.iter().any(|f| dialect::contains_whole_word(&stripped, f));
    let globals_block = if needs_globals {
        r#"layout(set = 0, binding = 0) uniform Globals {
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
};
"#
        .to_string()
    } else {
        String::new()
    };

    let mut channel_decls = String::new();
    let mut channel_defines = String::new();
    for (i, kind) in channel_kinds.iter().enumerate() {
        let channel_name = format!("iChannel{i}");
        if !dialect::contains_whole_word(&stripped, &channel_name) {
            continue;
        }
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

    let custom_uniforms: Vec<CustomUniformDecl> = detect_custom_uniforms(&stripped)
        .into_iter()
        .enumerate()
        .map(|(idx, (glsl_type, name))| CustomUniformDecl {
            glsl_type,
            name,
            binding: FIRST_CUSTOM_UNIFORM_BINDING + idx as u32,
        })
        .collect();
    let mut custom_uniform_decls = String::new();
    for decl in &custom_uniforms {
        custom_uniform_decls.push_str(&format!(
            "layout(set = 0, binding = {}) uniform CustomUniformBlock_{} {{ {} {}; }};\n",
            decl.binding, decl.name, decl.glsl_type, decl.name
        ));
    }

    let full = format!(
        "{version_line}{legacy_out_decl}{globals_block}{channel_decls}{channel_defines}{custom_uniform_decls}{translated_src}"
    );
    (full, custom_uniforms)
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

/// Équivalent de `header_line_count` pour le mode GLSL standalone (roadmap1.md :
/// « `header_line_count`/le mapping d'erreur ligne→éditeur [...] doit avoir un
/// équivalent pour le mode standalone — sinon un message d'erreur de
/// compilation pointe sur la mauvaise ligne dans l'éditeur pour tout ce qui
/// n'est pas du Shadertoy »). Même principe : reconstruit exactement la
/// même source que celle réellement compilée pour ce dialecte
/// (`build_fragment_source_standalone`, harness conditionnel) et retrouve
/// la position de `user_src` dedans — valable quel que soit le nombre de
/// lignes de harness effectivement injectées pour ce code précis (bloc
/// Globals/iChannel*/uniforms personnalisés présents ou non).
pub fn header_line_count_standalone(common_src: &str, user_src: &str) -> usize {
    let combined = if common_src.trim().is_empty() {
        user_src.to_string()
    } else {
        format!("{common_src}\n{user_src}")
    };
    let (full, _custom_uniforms) = build_fragment_source_standalone(&combined, [ChannelKind::D2; 4]);
    match full.find(user_src) {
        Some(idx) => full[..idx].matches('\n').count(),
        None => 0,
    }
}

/// Sélectionne `header_line_count`/`header_line_count_standalone` selon le
/// dialecte détecté pour ce pass — point d'entrée unique utilisé par
/// `renderer.rs`/`lib.rs` pour ne pas avoir à dupliquer ce `match` à
/// chaque appelant.
pub fn header_line_count_for_dialect(
    common_src: &str,
    user_src: &str,
    dialect: crate::dialect::ShaderDialect,
) -> usize {
    match dialect {
        crate::dialect::ShaderDialect::Shadertoy => header_line_count(common_src, user_src),
        crate::dialect::ShaderDialect::GlslStandalone => header_line_count_standalone(common_src, user_src),
        crate::dialect::ShaderDialect::Wgsl => header_line_count_wgsl(common_src, user_src),
    }
}

// ---------------------------------------------------------------------
// Mode WGSL passthrough (RMLG.md, section "1. WGSL — dialecte d'entrée",
// 1.3 "Backend de compilation") : contrairement aux deux backends GLSL
// ci-dessus, il n'y a pas de wrapper `mainImage`→`main` à injecter — le
// `@fragment fn ...` de l'utilisateur est son propre point d'entrée réel,
// compilé quasiment tel quel (voir `dialect::wgsl_fragment_entry_point_name`,
// utilisée par `renderer.rs` pour le retrouver). Le rôle de ce backend se
// limite à injecter, uniquement si référencé, le bloc `Globals` et les
// paires texture+sampler `iChannel0-3`, et à auto-binder les
// `var<uniform>` personnalisés dépourvus de `@group`/`@binding` explicite
// (même choix que le mode GLSL standalone : acceptés avec une valeur par
// défaut à zéro, pas de branchement automatique sur le panneau de
// sliders).
// ---------------------------------------------------------------------

/// Bloc `Globals` WGSL — mêmes champs, dans le même ordre, que l'UBO
/// `Globals` GLSL (voir `build_fragment_source`). Aucun `vec3` dans ce
/// bloc (seul cas où l'alignement par défaut WGSL diverge de std140), donc
/// chaque champ garde le même octet de départ dans les deux langages...
/// sauf `iChannelTime` : std140 impose 16 octets par élément d'un tableau
/// de scalaires, alors qu'un `array<f32, 4>` WGSL n'a par défaut qu'un
/// stride de 4 octets. Exactement le même repli que celui déjà adopté côté
/// Rust pour ce champ (`GlobalsUniform::channel_time`, stocké comme
/// `[[f32; 4]; 4]` plutôt que `[f32; 4]` — voir sa doc dans `renderer.rs`)
/// est reproduit ici textuellement : `array<vec4<f32>, 4>`, dont seul le
/// premier composant de chaque élément est réellement utilisé. Ce choix
/// évite d'avoir à vérifier/forcer un stride WGSL non standard : le layout
/// mémoire par défaut du texte ci-dessous correspond déjà, octet pour
/// octet, à celui que `Engine::write_globals` écrit dans le même buffer
/// GPU partagé avec le chemin GLSL.
const WGSL_GLOBALS_BLOCK: &str = "struct Globals {\n    iResolution: vec4<f32>,\n    iMouse: vec4<f32>,\n    iTime: f32,\n    iTimeDelta: f32,\n    iFrame: i32,\n    _pad0: f32,\n    iDate: vec4<f32>,\n    iSampleRate: f32,\n    _pad1: f32,\n    _pad2: f32,\n    _pad3: f32,\n    iChannelResolution: array<vec4<f32>, 4>,\n    iChannelTime: array<vec4<f32>, 4>,\n};\n@group(0) @binding(0)\nvar<uniform> globals: Globals;\n";

/// Types WGSL scalaires/vecteurs supportés pour un `var<uniform>`
/// personnalisé auto-bindé — même ensemble que `CUSTOM_UNIFORM_TYPES` côté
/// GLSL standalone (float/int/bool/vec2/vec3/vec4), exprimé dans leur
/// syntaxe WGSL.
const WGSL_CUSTOM_UNIFORM_TYPES: [&str; 6] = ["f32", "i32", "bool", "vec2<f32>", "vec3<f32>", "vec4<f32>"];

/// Repère puis annote *en place* chaque déclaration `var<uniform> <nom>:
/// <type>;` de premier niveau dans `user_src` qui n'est pas déjà précédée
/// d'un `@group`/`@binding` explicite. Contrairement au mode GLSL
/// standalone (`detect_custom_uniforms`/`build_fragment_source_standalone`),
/// qui se contente d'*ajouter* un second bloc séparé sans toucher au texte
/// original — WGSL, à la différence de GLSL, refuse catégoriquement deux
/// déclarations d'un même identifiant au niveau module : dupliquer la
/// déclaration comme le fait le chemin GLSL serait ici une erreur de
/// compilation garantie. La déclaration existante de l'utilisateur reçoit
/// donc directement son `@group(0) @binding(N)` à l'endroit où elle se
/// trouve déjà.
///
/// Même moule volontairement conservateur que `detect_custom_uniforms` :
/// un seul nom par déclaration, pas de tableau, pas d'initialiseur — toute
/// déclaration qui s'en écarte est laissée telle quelle (et échouera
/// probablement à la compilation faute de binding, comme avant ce
/// chantier). Opère directement sur `user_src` plutôt que sur une version
/// débarrassée de ses commentaires (comme le fait déjà
/// `translate_legacy_frag_output`/`replace_whole_word` pour la traduction
/// `gl_FragColor` côté GLSL standalone, même limitation assumée) : un
/// `var<uniform> x: f32;` qui apparaîtrait textuellement à l'intérieur
/// d'un commentaire serait, en théorie, annoté à tort — cas marginal jugé
/// acceptable au vu de la spécificité du motif recherché.
///
/// Retourne `(source_réécrite, uniformes_personnalisés_détectés)`, les
/// bindings étant attribués séquentiellement à partir de
/// `first_binding` — même convention que `FIRST_CUSTOM_UNIFORM_BINDING`
/// côté GLSL standalone.
fn rewrite_wgsl_custom_uniforms(user_src: &str, first_binding: u32) -> (String, Vec<CustomUniformDecl>) {
    let chars: Vec<char> = user_src.chars().collect();
    let n = chars.len();
    let mut out = String::with_capacity(user_src.len());
    let mut customs: Vec<CustomUniformDecl> = Vec::new();
    let mut i = 0;
    while i < n {
        if !dialect::is_ident_start(chars[i]) {
            out.push(chars[i]);
            i += 1;
            continue;
        }
        let word_start = i;
        let mut j = i;
        while j < n && dialect::is_ident_char(chars[j]) {
            j += 1;
        }
        let word: String = chars[word_start..j].iter().collect();
        if word != "var" || j >= n || chars[j] != '<' {
            out.push_str(&word);
            i = j;
            continue;
        }
        let rest: String = chars[j..].iter().collect();
        if !rest.starts_with("<uniform>") {
            out.push_str(&word);
            i = j;
            continue;
        }
        // Nom.
        let mut t = j + "<uniform>".len();
        while t < n && chars[t].is_whitespace() {
            t += 1;
        }
        let name_start = t;
        while t < n && dialect::is_ident_char(chars[t]) {
            t += 1;
        }
        if t == name_start {
            out.push_str(&word);
            i = j;
            continue;
        }
        let name: String = chars[name_start..t].iter().collect();
        // Réservé au bloc `Globals` injecté par ce backend.
        if name == "globals" {
            out.push_str(&word);
            i = j;
            continue;
        }
        // ':'
        let mut c = t;
        while c < n && chars[c].is_whitespace() {
            c += 1;
        }
        if c >= n || chars[c] != ':' {
            out.push_str(&word);
            i = j;
            continue;
        }
        c += 1;
        while c < n && chars[c].is_whitespace() {
            c += 1;
        }
        // Type : identifiant, éventuellement suivi d'un `<...>` générique
        // (ex. `vec4<f32>`).
        let type_start = c;
        while c < n && dialect::is_ident_char(chars[c]) {
            c += 1;
        }
        if c < n && chars[c] == '<' {
            let mut depth = 1;
            c += 1;
            while c < n && depth > 0 {
                match chars[c] {
                    '<' => depth += 1,
                    '>' => depth -= 1,
                    _ => {}
                }
                c += 1;
            }
        }
        let wgsl_type: String = chars[type_start..c].iter().collect();
        let Some(known_type) = WGSL_CUSTOM_UNIFORM_TYPES.iter().find(|k| **k == wgsl_type) else {
            out.push_str(&word);
            i = j;
            continue;
        };
        // Doit être immédiatement suivi (au-delà des espaces) d'un ";" —
        // sinon initialiseur/forme inattendue, hors périmètre.
        let mut e = c;
        while e < n && chars[e].is_whitespace() {
            e += 1;
        }
        if e >= n || chars[e] != ';' {
            out.push_str(&word);
            i = j;
            continue;
        }
        // Refuse si déjà annoté : dernier caractère non-espace déjà émis
        // dans `out` est `)` — signe d'un `@group(...)`/`@binding(...)`
        // déjà présent, l'utilisateur gère alors lui-même son binding
        // (même principe que la vérification `)` précédant `layout(...)`
        // côté GLSL standalone, voir `detect_custom_uniforms`).
        let out_chars: Vec<char> = out.chars().collect();
        let mut bi = out_chars.len();
        while bi > 0 && out_chars[bi - 1].is_whitespace() {
            bi -= 1;
        }
        if bi > 0 && out_chars[bi - 1] == ')' {
            out.push_str(&word);
            i = j;
            continue;
        }
        let binding = first_binding + customs.len() as u32;
        out.push_str(&format!("@group(0) @binding({binding})\n"));
        out.push_str(&word);
        customs.push(CustomUniformDecl { glsl_type: known_type, name, binding });
        i = j;
    }
    (out, customs)
}

/// Équivalent, pour le mode WGSL, de `build_fragment_source`/
/// `build_fragment_source_standalone` : compile le code utilisateur
/// quasiment tel quel, son `@fragment fn ...` étant l'entrée réelle du
/// fragment shader. Voir la doc du module ci-dessus pour le détail de ce
/// qui est injecté.
pub fn wgsl_passthrough_backend(
    user_src: &str,
    channel_kinds: [ChannelKind; 4],
    _force_opaque: bool,
) -> (String, Vec<CustomUniformDecl>) {
    let (rewritten_src, custom_uniforms) = rewrite_wgsl_custom_uniforms(user_src, FIRST_CUSTOM_UNIFORM_BINDING);
    let stripped = dialect::strip_comments(&rewritten_src);

    let globals_block = if dialect::contains_whole_word(&stripped, "globals") {
        WGSL_GLOBALS_BLOCK
    } else {
        ""
    };

    let mut channel_decls = String::new();
    for (i, kind) in channel_kinds.iter().enumerate() {
        let channel_name = format!("iChannel{i}");
        if !dialect::contains_whole_word(&stripped, &channel_name) {
            continue;
        }
        let tex_type = match kind {
            ChannelKind::D2 => "texture_2d<f32>",
            ChannelKind::Cube => "texture_cube<f32>",
        };
        let tex_binding = 1 + i * 2;
        let sampler_binding = 2 + i * 2;
        channel_decls.push_str(&format!(
            "@group(0) @binding({tex_binding})\nvar {channel_name}: {tex_type};\n@group(0) @binding({sampler_binding})\nvar {channel_name}_sampler: sampler;\n"
        ));
    }

    let full = format!("{globals_block}{channel_decls}{rewritten_src}");
    (full, custom_uniforms)
}

/// Équivalent de `header_line_count`/`header_line_count_standalone` pour
/// le mode WGSL : reconstruit exactement la même source que celle
/// réellement compilée pour ce dialecte (`wgsl_passthrough_backend`,
/// harness conditionnel) et retrouve la position de `user_src` dedans.
/// Comme pour le mode standalone (voir sa doc), si `user_src` contient un
/// `var<uniform>` personnalisé réécrit en place par
/// `rewrite_wgsl_custom_uniforms`, le texte original n'apparaît plus
/// verbatim dans la source complète : `find` échoue alors et retombe sur
/// l'offset `0`, une dégradation déjà acceptée ailleurs dans ce fichier
/// plutôt qu'un cas nouveau introduit ici.
pub fn header_line_count_wgsl(common_src: &str, user_src: &str) -> usize {
    let combined = if common_src.trim().is_empty() {
        user_src.to_string()
    } else {
        format!("{common_src}\n{user_src}")
    };
    let (full, _custom_uniforms) = wgsl_passthrough_backend(&combined, [ChannelKind::D2; 4], false);
    match full.find(user_src) {
        Some(idx) => full[..idx].matches('\n').count(),
        None => 0,
    }
}

// ---------------------------------------------------------------------
// Registre de backends de compilation (roadmap1.md, section "Architecture
// extensible pour de futurs langages") : `renderer::Engine::compile_pass`
// ne fait plus un `match` à deux branches sur `ShaderDialect` pour choisir
// quelle fonction de build appeler — il demande le backend associé au
// dialecte détecté via `compile_backend_for`, qui cherche dans
// `COMPILE_BACKENDS` par identifiant stable (`ShaderDialect::id`). Ajouter
// un futur langage avec un backend fonctionnel consiste à écrire sa
// fonction de build (même signature que les deux ci-dessous) et à
// l'ajouter à ce tableau — `compile_pass` n'a rien à changer. Voir
// `ARCHITECTURE.md` pour la procédure complète (détecteur + backend +
// i18n).
// ---------------------------------------------------------------------

/// Signature commune à tout backend de compilation : source utilisateur
/// (Common + code du pass déjà concaténés par l'appelant), type de
/// sampler par slot iChannel0-3, et `force_opaque` (pertinent uniquement
/// pour le pass Image en mode Shadertoy aujourd'hui — voir
/// `build_fragment_source` — un futur backend qui n'a pas cette notion est
/// libre d'ignorer ce paramètre). Retourne la source GLSL complète prête à
/// être compilée, plus la liste des `uniform` personnalisés auto-bindés
/// détectés (vide pour un backend qui n'a pas ce concept, comme
/// Shadertoy).
pub type CompileBackendFn = fn(&str, [ChannelKind; 4], bool) -> (String, Vec<CustomUniformDecl>);

fn shadertoy_backend(user_src: &str, channel_kinds: [ChannelKind; 4], force_opaque: bool) -> (String, Vec<CustomUniformDecl>) {
    (build_fragment_source(user_src, channel_kinds, force_opaque), Vec::new())
}

fn glsl_standalone_backend(user_src: &str, channel_kinds: [ChannelKind; 4], _force_opaque: bool) -> (String, Vec<CustomUniformDecl>) {
    build_fragment_source_standalone(user_src, channel_kinds)
}

struct CompileBackendEntry {
    /// Doit correspondre exactement à `ShaderDialect::id()` pour le
    /// dialecte concerné — vérifié par
    /// `tests::compile_backends_cover_every_known_dialect` plutôt que
    /// couplé au type `ShaderDialect` lui-même, pour que ce fichier n'ait
    /// pas à connaître sa représentation interne (seulement son id texte,
    /// la même frontière stable utilisée par pyo3/le footer, voir
    /// `dialect.rs`).
    dialect_id: &'static str,
    build: CompileBackendFn,
}

/// Registre associant chaque identifiant de dialecte connu à son backend
/// de compilation. Pour ajouter un futur langage : ajouter une entrée ici
/// avec son propre `build` (voir `ARCHITECTURE.md`).
const COMPILE_BACKENDS: &[CompileBackendEntry] = &[
    CompileBackendEntry { dialect_id: "shadertoy", build: shadertoy_backend },
    CompileBackendEntry { dialect_id: "glsl", build: glsl_standalone_backend },
    CompileBackendEntry { dialect_id: "wgsl", build: wgsl_passthrough_backend },
];

/// Backend de compilation pour un dialecte détecté — point d'entrée unique
/// utilisé par `renderer::Engine::compile_pass` à la place d'un `match` en
/// dur. Retombe sur le backend Shadertoy si (bug de configuration) un
/// dialecte n'a pas d'entrée dans `COMPILE_BACKENDS` : ne devrait jamais
/// arriver en pratique, voir le test qui vérifie que le registre couvre
/// bien tous les dialectes de `ShaderDialect::ALL`.
pub fn compile_backend_for(dialect: crate::dialect::ShaderDialect) -> CompileBackendFn {
    let id = dialect.id();
    for entry in COMPILE_BACKENDS {
        if entry.dialect_id == id {
            return entry.build;
        }
    }
    shadertoy_backend
}

// ---------------------------------------------------------------------
// Export HLSL/MSL (RMLG.md, section "2. HLSL & MSL — cibles d'export,
// jamais dialectes d'entrée", 2.1/2.2) : contrairement au registre
// `COMPILE_BACKENDS` ci-dessus (réservé aux dialectes réellement
// détectables/éditables depuis l'éditeur), ce qui suit ne sert qu'à
// produire, ponctuellement, une traduction texte d'un pass déjà écrit et
// compilable dans un des trois dialectes existants -- jamais un nouveau
// dialecte d'entrée. Voir RMLG.md 2.1 pour la justification complète (pas
// de frontend `hlsl-in`/`msl-in` dans `naga`, donc pas de parcours
// possible dans l'autre sens).
// ---------------------------------------------------------------------

/// Langage cible d'un export "shader compilé vers…". Volontairement un
/// type distinct de `crate::dialect::ShaderDialect` : un `ExportTarget`
/// n'est jamais ni détecté, ni recollé dans l'éditeur, ni ajouté à
/// `ShaderDialect::ALL` -- voir la doc de `export_shader_as` et RMLG.md
/// 2.1 pour la distinction.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ExportTarget {
    Hlsl,
    Msl,
}

/// Traduit un pass déjà écrit dans un des dialectes **éditables** connus
/// (`ShaderDialect::Shadertoy`/`GlslStandalone`/`Wgsl`) vers du texte HLSL
/// ou MSL.
///
/// Réutilise très exactement le même backend de compilation que celui
/// employé pour le rendu live (`compile_backend_for` -- même fonction,
/// mêmes arguments) : la source intermédiaire produite ici est donc
/// identique, caractère pour caractère (à `force_opaque` près, voir plus
/// bas), à celle réellement soumise au GPU par
/// `renderer::Engine::compile_pass` pour ce même pass. Cette source est
/// ensuite parsée par le frontend `naga` correspondant au dialecte
/// d'origine (`glsl-in` pour Shadertoy/GLSL standalone, `wgsl-in` pour
/// WGSL -- jamais de frontend HLSL/MSL, `naga` n'en a pas, voir RMLG.md
/// 2.1), validée, puis réécrite par le backend `naga` `hlsl-out`/
/// `msl-out` correspondant sur le module IR validé.
///
/// **`force_opaque` toujours à `false`** ici : cette notion (voir
/// `build_fragment_source`) n'a de sens que pour le pass Image d'un
/// rendu Shadertoy réellement affiché sur le canvas de ce logiciel --
/// un export n'a pas la notion de "quel pass alimente le canvas", ce
/// serait à un futur appelant de la reproduire explicitement s'il en a
/// besoin (`main_window.py` connaît déjà quel pass est actif).
///
/// Volontairement **séparée** de `compile_backend_for`/`COMPILE_BACKENDS`
/// (qui restent réservés aux dialectes réellement éditables) -- voir
/// RMLG.md 2.1/2.2, "ne pas ajouter `hlsl`/`msl` à `ShaderDialect::ALL`".
///
/// **Ne référence ni n'appelle jamais `crate::golf`** (RMLG.md 2.3) : le
/// golfer est un outil textuel qui opère sur du GLSL et n'a aucun sens
/// une fois le module traduit vers l'IR `naga` puis vers HLSL/MSL. `source`
/// est pris tel quel, golfé ou non -- ce choix appartient entièrement à
/// l'appelant (qui peut très bien passer le résultat de
/// `golf::golf_shader`/`golf_shader_with_common`/`golf_shader_ex`, voir
/// `lib.rs`) -- et il n'y a jamais de second passage de golfing appliqué
/// ici après traduction. Voir `export_tests::
/// export_never_applies_golf_and_reflects_whichever_source_the_user_passed`
/// pour une vérification observable de cette garantie (pas seulement
/// déclarative) : le backend HLSL de `naga` préserve verbatim les
/// identifiants du texte source, donc un identifiant volontairement long
/// survit intact dans la sortie si et seulement si aucun golfing n'a eu
/// lieu entre-temps.
///
/// Capacités de validation `naga` (`naga::valid::Capabilities::empty()`) :
/// identiques à celles réellement en vigueur pour le rendu live -- le
/// device de `renderer::Engine::new` demande `wgpu::Features::empty()`,
/// jamais une feature qui déverrouillerait une capacité `naga`
/// supplémentaire (push constants, f64, indexation non uniforme de
/// tableaux de textures, ...) -- donc un module qui compile pour le
/// rendu live compile aussi pour cet export, et réciproquement.
///
/// Limites connues (RMLG.md 2.3, non résolues par cette fonction) : les
/// bindings `iChannel`/uniforms personnalisés traduits n'utilisent pas
/// forcément les conventions attendues par un moteur tiers, et il n'y a
/// aucune garantie de rendu pixel-identique entre le rendu live et une
/// éventuelle recompilation du texte exporté ailleurs.
pub fn export_shader_as(
    source: &str,
    dialect: crate::dialect::ShaderDialect,
    target: ExportTarget,
    channel_kinds: [ChannelKind; 4],
) -> Result<String, String> {
    let backend = compile_backend_for(dialect);
    let (compiled_src, _custom_uniforms) = backend(source, channel_kinds, false);

    // Same naga GLSL-frontend workaround as the live-render path
    // (`renderer::Engine::compile_pass`) -- see `ctor_fixup`'s module
    // docs. Not applicable to WGSL (no such "extra arguments ignored"
    // constructor rule exists there), so left untouched for that dialect.
    // `parse_src` -- not `compiled_src` -- is what naga actually parses
    // for GLSL, so it's also what any later naga error must be rendered
    // against, or line/column spans would point into the wrong text.
    let parse_src = match dialect {
        crate::dialect::ShaderDialect::Wgsl => compiled_src.clone(),
        crate::dialect::ShaderDialect::Shadertoy | crate::dialect::ShaderDialect::GlslStandalone => {
            crate::ctor_fixup::fixup_overloaded_matrix_constructors(&compiled_src)
        }
    };

    let module = match dialect {
        crate::dialect::ShaderDialect::Wgsl => naga::front::wgsl::parse_str(&parse_src)
            .map_err(|e| format!(
                "erreur de parsing WGSL (naga) pendant l'export :\n{}",
                e.emit_to_string(&parse_src)
            )),
        crate::dialect::ShaderDialect::Shadertoy | crate::dialect::ShaderDialect::GlslStandalone => {
            let options = naga::front::glsl::Options {
                stage: naga::ShaderStage::Fragment,
                defines: Default::default(),
            };
            naga::front::glsl::Frontend::default()
                .parse(&options, &parse_src)
                .map_err(|e| format!(
                    "erreur de parsing GLSL (naga) pendant l'export :\n{}",
                    e.emit_to_string(&parse_src)
                ))
        }
    }?;

    let module_info = naga::valid::Validator::new(
        naga::valid::ValidationFlags::all(),
        naga::valid::Capabilities::empty(),
    )
    .validate(&module)
    .map_err(|e| format!(
        "erreur de validation naga pendant l'export :\n{}",
        e.emit_to_string(&parse_src)
    ))?;

    match target {
        ExportTarget::Hlsl => {
            let options = naga::back::hlsl::Options::default();
            let mut out = String::new();
            let mut writer = naga::back::hlsl::Writer::new(&mut out, &options);
            writer
                .write(&module, &module_info)
                .map_err(|e| format!("erreur d'écriture HLSL (naga) pendant l'export : {e}"))?;
            Ok(out)
        }
        ExportTarget::Msl => {
            let options = naga::back::msl::Options::default();
            let pipeline_options = naga::back::msl::PipelineOptions::default();
            let (out, _info) =
                naga::back::msl::write_string(&module, &module_info, &options, &pipeline_options)
                    .map_err(|e| format!("erreur d'écriture MSL (naga) pendant l'export : {e}"))?;
            Ok(out)
        }
    }
}

// ---------------------------------------------------------------------
// Tests — export HLSL/MSL (RMLG.md, section 2.2) : mêmes trois dialectes
// d'entrée que `COMPILE_BACKENDS`, vérifiés via le vrai chemin
// frontend→validation→backend `naga`, pas seulement via la génération de
// texte GLSL/WGSL intermédiaire (déjà couverte par `standalone_tests`/
// `wgsl_tests`).
// ---------------------------------------------------------------------
#[cfg(test)]
mod export_tests {
    use super::*;

    const NO_CHANNELS: [ChannelKind; 4] = [ChannelKind::D2; 4];

    #[test]
    fn exports_shadertoy_pass_to_hlsl() {
        let user_src = "void mainImage(out vec4 c, in vec2 f) { c = vec4(iTime); }";
        let out = export_shader_as(
            user_src,
            crate::dialect::ShaderDialect::Shadertoy,
            ExportTarget::Hlsl,
            NO_CHANNELS,
        )
        .expect("l'export HLSL doit réussir pour un pass Shadertoy valide");
        assert!(out.contains("float4"), "sortie HLSL inattendue:\n{out}");
    }

    #[test]
    fn exports_shadertoy_pass_to_msl() {
        let user_src = "void mainImage(out vec4 c, in vec2 f) { c = vec4(iTime); }";
        let out = export_shader_as(
            user_src,
            crate::dialect::ShaderDialect::Shadertoy,
            ExportTarget::Msl,
            NO_CHANNELS,
        )
        .expect("l'export MSL doit réussir pour un pass Shadertoy valide");
        assert!(out.contains("#include <metal_stdlib>"), "sortie MSL inattendue:\n{out}");
    }

    #[test]
    fn exports_glsl_standalone_pass_to_hlsl() {
        let user_src = "out vec4 fragColOut;\nvoid main() { fragColOut = vec4(iTime); }";
        let out = export_shader_as(
            user_src,
            crate::dialect::ShaderDialect::GlslStandalone,
            ExportTarget::Hlsl,
            NO_CHANNELS,
        )
        .expect("l'export HLSL doit réussir pour un pass GLSL standalone valide");
        assert!(out.contains("float4"), "sortie HLSL inattendue:\n{out}");
    }

    #[test]
    fn exports_wgsl_pass_to_msl() {
        let user_src =
            "@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(globals.iTime); }";
        let out = export_shader_as(
            user_src,
            crate::dialect::ShaderDialect::Wgsl,
            ExportTarget::Msl,
            NO_CHANNELS,
        )
        .expect("l'export MSL doit réussir pour un pass WGSL valide");
        assert!(out.contains("#include <metal_stdlib>"), "sortie MSL inattendue:\n{out}");
    }

    #[test]
    fn exports_wgsl_pass_to_hlsl_with_ichannel_binding() {
        // Couvre le cas iChannel (RMLG.md 2.3) : la traduction doit passer
        // par la validation naga sans erreur, avec un `Texture2D`/
        // `SamplerState` HLSL en sortie pour le canal réellement utilisé.
        let user_src = "@fragment fn main() -> @location(0) vec4<f32> { return textureSample(iChannel0, iChannel0_sampler, vec2<f32>(0.0)); }";
        let out = export_shader_as(
            user_src,
            crate::dialect::ShaderDialect::Wgsl,
            ExportTarget::Hlsl,
            NO_CHANNELS,
        )
        .expect("l'export HLSL doit réussir pour un pass WGSL utilisant iChannel0");
        assert!(out.contains("Texture2D"), "sortie HLSL inattendue:\n{out}");
        assert!(out.contains("SamplerState"), "sortie HLSL inattendue:\n{out}");
    }

    #[test]
    fn export_reuses_the_same_intermediate_source_as_compile_backend_for() {
        // Non-régression explicite du point central de RMLG.md 2.2 :
        // `export_shader_as` ne doit jamais dupliquer/diverger de la
        // logique de `compile_backend_for` -- vérifié ici indirectement
        // (même entrée, même dialecte) en confirmant que le texte HLSL
        // produit reflète bien le harness réellement injecté par le
        // backend GLSL standalone (bloc `Globals`, ici volontairement
        // omis car non référencé -- voir `omits_globals_block_when_unreferenced`
        // côté `standalone_tests`).
        let user_src = "out vec4 fragColOut;\nvoid main() { fragColOut = vec4(1.0); }";
        let (compiled_src, _) = compile_backend_for(crate::dialect::ShaderDialect::GlslStandalone)(
            user_src, NO_CHANNELS, false,
        );
        assert!(!compiled_src.contains("Globals"));
        let out = export_shader_as(
            user_src,
            crate::dialect::ShaderDialect::GlslStandalone,
            ExportTarget::Hlsl,
            NO_CHANNELS,
        )
        .expect("export attendu réussi");
        assert!(!out.to_lowercase().contains("cbuffer"), "aucun UBO Globals ne devrait apparaître ici:\n{out}");
    }

    #[test]
    fn export_never_applies_golf_and_reflects_whichever_source_the_user_passed() {
        // RMLG.md 2.3 : "le golfing (golf.rs) ne s'applique jamais à un
        // export HLSL/MSL [...] l'export part toujours du code source
        // (golfé ou non, au choix de l'utilisateur), jamais d'un second
        // golfing post-traduction." `export_shader_as` n'appelle et ne
        // référence `crate::golf` nulle part (voir sa définition
        // ci-dessus : `compile_backend_for` -> parse `naga` -> valide ->
        // écrit HLSL/MSL, rien d'autre) -- vérifié ici de façon
        // observable plutôt que déclarative : le backend HLSL de `naga`
        // préserve verbatim les identifiants du texte source (confirmé
        // par une sonde manuelle sur ce module), donc si `export_shader_as`
        // golfait la source avant traduction, un identifiant volontairement
        // long et distinctif comme `veryVerboseLocalNameXYZ` disparaîtrait
        // de la sortie, remplacé par le nom court à une lettre que
        // `golf::golf_shader` lui aurait attribué.
        let verbose_src = "void mainImage(out vec4 fragColorOutputHere, in vec2 fragCoordInputHere) { float veryVerboseLocalNameXYZ = iTime; fragColorOutputHere = vec4(veryVerboseLocalNameXYZ); }";

        // Le golfing doit réellement raccourcir ce nom (sinon ce test ne
        // vérifie rien) -- confirmé au passage, pas supposé.
        let golfed_src = crate::golf::golf_shader(verbose_src);
        assert!(
            !golfed_src.contains("veryVerboseLocalNameXYZ"),
            "golf_shader devrait avoir renommé l'identifiant long, sinon ce \
             test ne peut pas distinguer golfé/non golfé :\n{golfed_src}"
        );

        // 1) Export de la source NON golfée : l'identifiant original doit
        //    survivre intact dans la sortie HLSL -- la preuve qu'aucun golf
        //    n'a été appliqué en interne par `export_shader_as`.
        let out_verbose = export_shader_as(
            verbose_src,
            crate::dialect::ShaderDialect::Shadertoy,
            ExportTarget::Hlsl,
            NO_CHANNELS,
        )
        .expect("l'export HLSL doit réussir sur la source non golfée");
        assert!(
            out_verbose.contains("veryVerboseLocalNameXYZ"),
            "l'identifiant original doit survivre tel quel dans la sortie \
             HLSL -- sa disparition indiquerait qu'un golfing a été \
             appliqué avant traduction, ce que RMLG.md interdit \
             explicitement:\n{out_verbose}"
        );

        // 2) Export de la source déjà golfée par l'utilisateur (choix de
        //    l'utilisateur, fait *avant* l'export, jamais par
        //    `export_shader_as` lui-même) : doit réussir de façon tout
        //    aussi indépendante, sur ce texte-là tel quel -- pas de
        //    "second golfing post-traduction", et pas de golfing implicite
        //    supplémentaire non plus (les noms à une lettre déjà présents
        //    dans `golfed_src` ne doivent pas être raccourcis davantage,
        //    il n'y a de toute façon plus rien à raccourcir).
        let out_golfed = export_shader_as(
            &golfed_src,
            crate::dialect::ShaderDialect::Shadertoy,
            ExportTarget::Hlsl,
            NO_CHANNELS,
        )
        .expect("l'export HLSL doit réussir tout aussi bien sur la source déjà golfée par l'utilisateur");
        assert!(out_golfed.contains("float4"), "sortie HLSL inattendue:\n{out_golfed}");
    }
}

// ---------------------------------------------------------------------
// Tests — mode GLSL standalone : injection conditionnelle du #version, du
// bloc Globals, des iChannel*, traduction gl_FragColor/gl_FragData, et
// auto-binding des uniforms personnalisés (roadmap1.md, section
// "Compilation réellement double-dialecte").
// ---------------------------------------------------------------------
#[cfg(test)]
mod standalone_tests {
    use super::*;

    const NO_CHANNELS: [ChannelKind; 4] = [ChannelKind::D2; 4];

    #[test]
    fn injects_version_when_absent() {
        let (full, _) = build_fragment_source_standalone(
            "out vec4 fragColOut;\nvoid main() { fragColOut = vec4(1.0); }",
            NO_CHANNELS,
        );
        assert!(full.starts_with("#version 450"));
        assert_eq!(full.matches("#version").count(), 1);
    }

    #[test]
    fn respects_existing_version_directive() {
        let user = "#version 460\nout vec4 fragColOut;\nvoid main() { fragColOut = vec4(1.0); }";
        let (full, _) = build_fragment_source_standalone(user, NO_CHANNELS);
        // Une seule directive #version au total (celle de l'utilisateur) :
        // en injecter une seconde serait une erreur de compilation.
        assert_eq!(full.matches("#version").count(), 1);
        assert!(full.contains("#version 460"));
    }

    #[test]
    fn omits_globals_block_when_unreferenced() {
        let (full, _) = build_fragment_source_standalone(
            "out vec4 fragColOut;\nvoid main() { fragColOut = vec4(1.0); }",
            NO_CHANNELS,
        );
        assert!(!full.contains("uniform Globals"));
    }

    #[test]
    fn injects_globals_block_when_referenced() {
        let (full, _) = build_fragment_source_standalone(
            "out vec4 fragColOut;\nvoid main() { fragColOut = vec4(iTime); }",
            NO_CHANNELS,
        );
        assert!(full.contains("uniform Globals"));
    }

    #[test]
    fn only_declares_referenced_ichannels() {
        let (full, _) = build_fragment_source_standalone(
            "out vec4 fragColOut;\nvoid main() { fragColOut = texture(iChannel2, vec2(0.0)); }",
            NO_CHANNELS,
        );
        assert!(full.contains("texChannel2"));
        assert!(!full.contains("texChannel0"));
        assert!(!full.contains("texChannel1"));
        assert!(!full.contains("texChannel3"));
    }

    #[test]
    fn translates_gl_frag_color_when_no_out_vec4_declared() {
        let (full, _) = build_fragment_source_standalone(
            "void main() { gl_FragColor = vec4(1.0); }",
            NO_CHANNELS,
        );
        assert!(!full.contains("gl_FragColor"));
        assert!(full.contains("out vec4 fragColOutLegacy"));
        assert!(full.contains("fragColOutLegacy = vec4(1.0);"));
    }

    #[test]
    fn translates_gl_frag_data_index_zero() {
        let (full, _) = build_fragment_source_standalone(
            "void main() { gl_FragData[0] = vec4(1.0); }",
            NO_CHANNELS,
        );
        assert!(!full.contains("gl_FragData"));
        assert!(full.contains("fragColOutLegacy = vec4(1.0);"));
    }

    #[test]
    fn does_not_translate_when_out_vec4_already_declared() {
        // Shader déjà "cassé" (gl_FragColor à côté d'un out vec4 moderne) —
        // on ne devine pas, on laisse tel quel.
        let user = "out vec4 myOut;\nvoid main() { gl_FragColor = vec4(1.0); myOut = vec4(0.0); }";
        let (full, _) = build_fragment_source_standalone(user, NO_CHANNELS);
        assert!(full.contains("gl_FragColor"));
        assert!(!full.contains("fragColOutLegacy"));
    }

    #[test]
    fn detects_and_auto_binds_custom_uniform() {
        let (full, customs) = build_fragment_source_standalone(
            "uniform float speed;\nout vec4 fragColOut;\nvoid main() { fragColOut = vec4(speed); }",
            NO_CHANNELS,
        );
        assert_eq!(customs.len(), 1);
        assert_eq!(customs[0].name, "speed");
        assert_eq!(customs[0].glsl_type, "float");
        assert_eq!(customs[0].binding, FIRST_CUSTOM_UNIFORM_BINDING);
        assert!(full.contains("CustomUniformBlock_speed"));
        assert!(full.contains(&format!("binding = {FIRST_CUSTOM_UNIFORM_BINDING}")));
    }

    #[test]
    fn assigns_sequential_bindings_to_multiple_custom_uniforms() {
        let (_, customs) = build_fragment_source_standalone(
            "uniform float speed;\nuniform vec3 tint;\nout vec4 fragColOut;\nvoid main() { fragColOut = vec4(tint * speed, 1.0); }",
            NO_CHANNELS,
        );
        assert_eq!(customs.len(), 2);
        assert_eq!(customs[0].binding, FIRST_CUSTOM_UNIFORM_BINDING);
        assert_eq!(customs[1].binding, FIRST_CUSTOM_UNIFORM_BINDING + 1);
    }

    #[test]
    fn does_not_rebind_uniform_with_explicit_layout() {
        let (full, customs) = build_fragment_source_standalone(
            "layout(set = 0, binding = 42) uniform float manual;\nout vec4 fragColOut;\nvoid main() { fragColOut = vec4(manual); }",
            NO_CHANNELS,
        );
        assert!(customs.is_empty());
        assert!(full.contains("binding = 42"));
        assert!(!full.contains("CustomUniformBlock_manual"));
    }

    #[test]
    fn ignores_shadertoy_global_names_as_custom_uniforms() {
        // Un utilisateur qui redéclare iTime lui-même (au lieu de laisser
        // le bloc Globals le fournir) ne doit pas être traité comme un
        // uniform personnalisé — cas de toute façon marginal/mal formé.
        let (_, customs) = build_fragment_source_standalone(
            "uniform float iTime;\nout vec4 fragColOut;\nvoid main() { fragColOut = vec4(iTime); }",
            NO_CHANNELS,
        );
        assert!(customs.is_empty());
    }

    #[test]
    fn header_line_count_standalone_matches_generated_source() {
        let user_src = "out vec4 fragColOut;\nvoid main() {\n    fragColOut = vec4(iTime);\n}\n";
        let offset = header_line_count_standalone("", user_src);
        let (full, _) = build_fragment_source_standalone(user_src, NO_CHANNELS);
        let lines: Vec<&str> = full.lines().collect();
        // La ligne à `offset` (0-indexée) doit être la toute première ligne
        // de `user_src` telle qu'elle apparaît dans la source complète.
        assert_eq!(lines[offset], "out vec4 fragColOut;");
    }

    #[test]
    fn header_line_count_for_dialect_dispatches_correctly() {
        let user_src = "void mainImage(out vec4 c, in vec2 f) { c = vec4(1.0); }";
        let shadertoy_offset = header_line_count_for_dialect("", user_src, crate::dialect::ShaderDialect::Shadertoy);
        assert_eq!(shadertoy_offset, header_line_count("", user_src));

        let glsl_src = "out vec4 fragColOut;\nvoid main() { fragColOut = vec4(1.0); }";
        let glsl_offset = header_line_count_for_dialect("", glsl_src, crate::dialect::ShaderDialect::GlslStandalone);
        assert_eq!(glsl_offset, header_line_count_standalone("", glsl_src));

        let wgsl_src = "@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(globals.iTime); }";
        let wgsl_offset = header_line_count_for_dialect("", wgsl_src, crate::dialect::ShaderDialect::Wgsl);
        assert_eq!(wgsl_offset, header_line_count_wgsl("", wgsl_src));
    }

    // -----------------------------------------------------------------
    // Registre de backends de compilation (roadmap1.md, section
    // "Architecture extensible pour de futurs langages") : vérifie que
    // `compile_backend_for` reste en phase avec `ShaderDialect::ALL` et
    // produit exactement le même résultat que les anciens appels directs
    // à `build_fragment_source`/`build_fragment_source_standalone` — ce
    // chantier ne change que *comment* le backend est choisi, jamais ce
    // qu'il produit.
    // -----------------------------------------------------------------

    #[test]
    fn compile_backends_cover_every_known_dialect() {
        for dialect in crate::dialect::ShaderDialect::ALL {
            let id = dialect.id();
            assert!(
                COMPILE_BACKENDS.iter().any(|e| e.dialect_id == id),
                "aucun backend enregistré pour le dialecte {id:?}"
            );
        }
    }

    #[test]
    fn compile_backend_for_shadertoy_matches_direct_call() {
        let user_src = "void mainImage(out vec4 c, in vec2 f) { c = vec4(iTime); }";
        let backend = compile_backend_for(crate::dialect::ShaderDialect::Shadertoy);
        let (via_registry, customs) = backend(user_src, NO_CHANNELS, true);
        let direct = build_fragment_source(user_src, NO_CHANNELS, true);
        assert_eq!(via_registry, direct);
        assert!(customs.is_empty());
    }

    #[test]
    fn compile_backend_for_glsl_standalone_matches_direct_call() {
        let user_src = "uniform float speed;\nout vec4 fragColOut;\nvoid main() { fragColOut = vec4(speed); }";
        let backend = compile_backend_for(crate::dialect::ShaderDialect::GlslStandalone);
        let (via_registry, customs_via_registry) = backend(user_src, NO_CHANNELS, false);
        let (direct, customs_direct) = build_fragment_source_standalone(user_src, NO_CHANNELS);
        assert_eq!(via_registry, direct);
        assert_eq!(customs_via_registry.len(), customs_direct.len());
        assert_eq!(customs_via_registry[0].name, customs_direct[0].name);
    }

    #[test]
    fn compile_backend_for_wgsl_matches_direct_call() {
        let user_src = "@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(globals.iTime); }";
        let backend = compile_backend_for(crate::dialect::ShaderDialect::Wgsl);
        let (via_registry, _) = backend(user_src, NO_CHANNELS, false);
        let (direct, _) = wgsl_passthrough_backend(user_src, NO_CHANNELS, false);
        assert_eq!(via_registry, direct);
    }
}

// ---------------------------------------------------------------------
// Tests — mode WGSL passthrough (RMLG.md, section "1. WGSL — dialecte
// d'entrée", 1.3 "Backend de compilation") : injection conditionnelle du
// bloc `Globals`, des paires texture+sampler `iChannel0-3`, et
// auto-binding en place des `var<uniform>` personnalisés.
// ---------------------------------------------------------------------
#[cfg(test)]
mod wgsl_tests {
    use super::*;

    const NO_CHANNELS: [ChannelKind; 4] = [ChannelKind::D2; 4];

    #[test]
    fn omits_globals_block_when_unreferenced() {
        let (full, _) = wgsl_passthrough_backend(
            "@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(1.0); }",
            NO_CHANNELS,
            false,
        );
        assert!(!full.contains("struct Globals"));
        assert!(!full.contains("var<uniform> globals"));
    }

    #[test]
    fn injects_globals_block_when_referenced() {
        let (full, _) = wgsl_passthrough_backend(
            "@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(globals.iTime); }",
            NO_CHANNELS,
            false,
        );
        assert!(full.contains("struct Globals"));
        assert!(full.contains("@group(0) @binding(0)"));
        assert!(full.contains("var<uniform> globals: Globals;"));
    }

    #[test]
    fn globals_block_pads_ichanneltime_to_vec4_stride() {
        // RMLG.md 1.3 : le stride WGSL par défaut d'un `array<f32, 4>`
        // (4 octets) ne correspond pas au stride std140 d'un tableau de
        // scalaires (16 octets) — le champ doit donc être un
        // `array<vec4<f32>, 4>`, jamais un `array<f32, 4>` brut.
        let (full, _) = wgsl_passthrough_backend(
            "@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(globals.iTime); }",
            NO_CHANNELS,
            false,
        );
        assert!(full.contains("iChannelTime: array<vec4<f32>, 4>"));
        assert!(!full.contains("iChannelTime: array<f32, 4>"));
    }

    #[test]
    fn only_declares_referenced_ichannels() {
        let (full, _) = wgsl_passthrough_backend(
            "@fragment fn main() -> @location(0) vec4<f32> { return textureSample(iChannel2, iChannel2_sampler, vec2<f32>(0.0)); }",
            NO_CHANNELS,
            false,
        );
        assert!(full.contains("var iChannel2: texture_2d<f32>;"));
        assert!(full.contains("var iChannel2_sampler: sampler;"));
        assert!(!full.contains("iChannel0:"));
        assert!(!full.contains("iChannel1:"));
        assert!(!full.contains("iChannel3:"));
    }

    #[test]
    fn declares_cube_type_for_cube_channel_kind() {
        let mut kinds = NO_CHANNELS;
        kinds[1] = ChannelKind::Cube;
        let (full, _) = wgsl_passthrough_backend(
            "@fragment fn main() -> @location(0) vec4<f32> { return textureSample(iChannel1, iChannel1_sampler, vec3<f32>(0.0)); }",
            kinds,
            false,
        );
        assert!(full.contains("var iChannel1: texture_cube<f32>;"));
    }

    #[test]
    fn detects_and_auto_binds_custom_uniform_in_place() {
        let (full, customs) = wgsl_passthrough_backend(
            "var<uniform> speed: f32;\n@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(speed); }",
            NO_CHANNELS,
            false,
        );
        assert_eq!(customs.len(), 1);
        assert_eq!(customs[0].name, "speed");
        assert_eq!(customs[0].glsl_type, "f32");
        assert_eq!(customs[0].binding, FIRST_CUSTOM_UNIFORM_BINDING);
        assert!(full.contains(&format!("@group(0) @binding({FIRST_CUSTOM_UNIFORM_BINDING})\nvar<uniform> speed: f32;")));
        // Une seule déclaration de `speed` au total — jamais dupliquée
        // (contrairement au mode GLSL standalone, WGSL interdit deux
        // déclarations d'un même identifiant au niveau module).
        assert_eq!(full.matches("var<uniform> speed").count(), 1);
    }

    #[test]
    fn assigns_sequential_bindings_to_multiple_custom_uniforms() {
        let (_, customs) = wgsl_passthrough_backend(
            "var<uniform> speed: f32;\nvar<uniform> tint: vec3<f32>;\n@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(tint * speed, 1.0); }",
            NO_CHANNELS,
            false,
        );
        assert_eq!(customs.len(), 2);
        assert_eq!(customs[0].binding, FIRST_CUSTOM_UNIFORM_BINDING);
        assert_eq!(customs[1].binding, FIRST_CUSTOM_UNIFORM_BINDING + 1);
    }

    #[test]
    fn does_not_rebind_uniform_with_explicit_group_binding() {
        let (full, customs) = wgsl_passthrough_backend(
            "@group(0) @binding(42)\nvar<uniform> manual: f32;\n@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(manual); }",
            NO_CHANNELS,
            false,
        );
        assert!(customs.is_empty());
        assert!(full.contains("@binding(42)"));
        assert_eq!(full.matches("var<uniform> manual").count(), 1);
    }

    #[test]
    fn ignores_globals_instance_name_as_custom_uniform() {
        // Un utilisateur qui redéclarerait lui-même `globals` entrerait en
        // collision avec le bloc injecté — cas de toute façon marginal/mal
        // formé, traité comme non éligible plutôt que rebindé.
        let (_, customs) = wgsl_passthrough_backend(
            "var<uniform> globals: f32;\n@fragment fn main() -> @location(0) vec4<f32> { return vec4<f32>(globals); }",
            NO_CHANNELS,
            false,
        );
        assert!(customs.is_empty());
    }

    #[test]
    fn header_line_count_wgsl_matches_generated_source() {
        let user_src = "@fragment fn main() -> @location(0) vec4<f32> {\n    return vec4<f32>(globals.iTime);\n}\n";
        let offset = header_line_count_wgsl("", user_src);
        let (full, _) = wgsl_passthrough_backend(user_src, NO_CHANNELS, false);
        let lines: Vec<&str> = full.lines().collect();
        assert_eq!(lines[offset], "@fragment fn main() -> @location(0) vec4<f32> {");
    }
}


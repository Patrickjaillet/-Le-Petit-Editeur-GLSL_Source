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
}

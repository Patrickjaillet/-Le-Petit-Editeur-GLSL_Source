//! Détection automatique du dialecte d'un shader collé/tapé par
//! l'utilisateur : Shadertoy (`void mainImage(...)`) ou GLSL "standalone"
//! (`void main()` classique, avec ou sans `#version`/`gl_FragColor`).
//!
//! Volontairement structuré comme une petite liste ordonnée de signaux
//! plutôt qu'une seule fonction figée, pour que le support d'un futur
//! langage soit "ajouter un signal/détecteur" plutôt que réécrire cette
//! fonction en profondeur (voir roadmap1.md, section "Architecture
//! extensible pour de futurs langages").

/// Dialecte détecté. `id()`/`from_id()` sont la frontière stable utilisée
/// par pyo3 (`lib.rs`) et par le footer Python : le reste du logiciel ne
/// manipule que ces identifiants texte, jamais l'enum Rust directement,
/// pour ne pas coupler `main_window.py`/`footer.py` à la représentation
/// interne.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShaderDialect {
    Shadertoy,
    GlslStandalone,
    Wgsl,
}

impl ShaderDialect {
    /// Tous les dialectes connus, dans un seul endroit — utilisé par les
    /// tests (`shader.rs`, `dialect.rs`) pour vérifier qu'un registre
    /// (détecteurs, backends de compilation) reste en phase avec cette
    /// liste sans avoir à l'énumérer une deuxième fois à la main. Ajouter
    /// un futur langage impose de l'ajouter ici — un seul endroit à
    /// modifier plutôt qu'un `match` par site d'appel (voir roadmap1.md,
    /// section "Architecture extensible pour de futurs langages").
    /// `#[allow(dead_code)]` : usage actuel limité aux tests (`cfg(test)`)
    /// et à `shader.rs`'s tests ; volontairement gardé `pub` pour l'usage
    /// documenté côté Python/pyo3 dans `ARCHITECTURE.md` (itérer sur les
    /// dialectes connus sans les lister en dur) même si rien ne l'appelle
    /// encore hors des tests.
    #[allow(dead_code)]
    pub const ALL: [ShaderDialect; 3] = [
        ShaderDialect::Shadertoy,
        ShaderDialect::GlslStandalone,
        ShaderDialect::Wgsl,
    ];

    pub fn id(self) -> &'static str {
        match self {
            ShaderDialect::Shadertoy => "shadertoy",
            ShaderDialect::GlslStandalone => "glsl",
            ShaderDialect::Wgsl => "wgsl",
        }
    }

    pub fn from_id(id: &str) -> Option<Self> {
        match id {
            "shadertoy" => Some(ShaderDialect::Shadertoy),
            "glsl" => Some(ShaderDialect::GlslStandalone),
            "wgsl" => Some(ShaderDialect::Wgsl),
            _ => None,
        }
    }
}

/// Quel signal précis a justifié la détection, pour que le tooltip du
/// footer puisse s'expliquer ("détecté via `mainImage()`") au lieu d'être
/// une boîte noire.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DialectSignal {
    MainImage,
    VoidMain,
    VersionDirective,
    FragColorLegacy,
    /// Attribut d'entry point WGSL (`@fragment fn ...`) — voir
    /// `matches_wgsl_entry_point`. Signal fort, syntaxe absente des deux
    /// dialectes GLSL déjà supportés (`@` n'est pas un caractère valide en
    /// GLSL), donc aucune ambiguïté possible avec `MainImage`/`VoidMain`.
    WgslEntryPoint,
    /// Signal secondaire WGSL, plus faible : qualificatif de stockage
    /// (`var<uniform>`/`var<storage>`) ou constructeur de type générique
    /// natif (`vec4<f32>(...)`) — utile pour un onglet WGSL qui ne
    /// contient que des fonctions utilitaires sans point d'entrée
    /// (équivalent WGSL d'un onglet `Common`). Voir
    /// `matches_wgsl_uniform_or_generic_type`.
    WgslUniformOrGeneric,
    /// Aucun signal dans le texte actuel (ex. onglet Common pur, ou tab
    /// vide) : le mode précédemment affiché est conservé tel quel.
    NoneKept,
}

impl DialectSignal {
    /// Clé i18n correspondante, à ajouter en parité stricte dans les 12
    /// fichiers `lngs/*.json` (voir roadmap1.md).
    pub fn i18n_key(self) -> &'static str {
        match self {
            DialectSignal::MainImage => "footer.dialect_signal_mainimage",
            DialectSignal::VoidMain => "footer.dialect_signal_voidmain",
            DialectSignal::VersionDirective => "footer.dialect_signal_version",
            DialectSignal::FragColorLegacy => "footer.dialect_signal_fragcolor",
            DialectSignal::WgslEntryPoint => "footer.dialect_signal_wgslentrypoint",
            DialectSignal::WgslUniformOrGeneric => "footer.dialect_signal_wgsluniformorgeneric",
            DialectSignal::NoneKept => "footer.dialect_signal_none",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DialectDetection {
    pub dialect: ShaderDialect,
    pub signal: DialectSignal,
}

pub(crate) fn is_ident_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

pub(crate) fn is_ident_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// Retire `//...` et `/* ... */` du source, en préservant les retours à la
/// ligne des blocs `/* */` (comptage de lignes non nécessaire ici, mais on
/// garde la même logique que `golf::strip_comments` par cohérence).
///
/// `pub(crate)` : réutilisé par `shader.rs` (mode GLSL standalone) pour que
/// la détection d'`uniform`/`gl_FragColor` personnalisés ignore elle aussi
/// le contenu des commentaires, sans dupliquer cette fonction une
/// troisième fois (elle existe déjà en miroir dans `golf.rs`).
pub(crate) fn strip_comments(src: &str) -> String {
    let chars: Vec<char> = src.chars().collect();
    let mut out = String::with_capacity(src.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '/' && i + 1 < chars.len() && chars[i + 1] == '/' {
            while i < chars.len() && chars[i] != '\n' {
                i += 1;
            }
        } else if c == '/' && i + 1 < chars.len() && chars[i + 1] == '*' {
            i += 2;
            while i + 1 < chars.len() && !(chars[i] == '*' && chars[i + 1] == '/') {
                if chars[i] == '\n' {
                    out.push('\n');
                }
                i += 1;
            }
            i += 2;
        } else {
            out.push(c);
            i += 1;
        }
    }
    out
}

/// Vrai si `word` apparaît dans `stripped` (déjà débarrassé de ses
/// commentaires) comme un identifiant à part entière — jamais comme
/// sous-chaîne d'un identifiant plus long (`mainCamera`, `domain` ne
/// doivent jamais matcher `main`). `pub(crate)` pour la même raison que
/// `strip_comments` ci-dessus.
pub(crate) fn contains_whole_word(stripped: &str, word: &str) -> bool {
    let chars: Vec<char> = stripped.chars().collect();
    let word_chars: Vec<char> = word.chars().collect();
    let n = chars.len();
    let wn = word_chars.len();
    if wn == 0 || wn > n {
        return false;
    }
    let mut i = 0;
    while i + wn <= n {
        if chars[i..i + wn] == word_chars[..] {
            let before_ok = i == 0 || !is_ident_char(chars[i - 1]);
            let after_ok = i + wn == n || !is_ident_char(chars[i + wn]);
            if before_ok && after_ok {
                return true;
            }
        }
        i += 1;
    }
    false
}

/// Vrai si `stripped` contient une définition `void main ( )` de premier
/// niveau (signature d'entrée GLSL classique, sans paramètre). Insensible
/// aux espaces/retours à la ligne entre les tokens. Ne matche jamais un
/// `mainImage`, `mainCamera`, etc. (mot entier requis), ni un simple appel
/// `main()` sans le `void` qui précède — ce qui exclurait une fonction
/// utilisateur mal nommée mais garde l'heuristique volontairement simple,
/// cohérente avec le reste de la détection.
fn has_top_level_void_main(stripped: &str) -> bool {
    let chars: Vec<char> = stripped.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if is_ident_start(chars[i]) {
            let start = i;
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            if chars[start..i].iter().collect::<String>() != "main" {
                continue;
            }
            // Remonte au-delà des espaces jusqu'au mot précédent : doit
            // être exactement "void".
            let mut b = start;
            while b > 0 && chars[b - 1].is_whitespace() {
                b -= 1;
            }
            let void_end = b;
            let mut void_start = void_end;
            while void_start > 0 && is_ident_char(chars[void_start - 1]) {
                void_start -= 1;
            }
            if chars[void_start..void_end].iter().collect::<String>() != "void" {
                continue;
            }
            // Avance au-delà des espaces : doit être "(" puis, au-delà des
            // espaces, ")" (liste de paramètres vide).
            let mut f = i;
            while f < n && chars[f].is_whitespace() {
                f += 1;
            }
            if f >= n || chars[f] != '(' {
                continue;
            }
            f += 1;
            while f < n && chars[f].is_whitespace() {
                f += 1;
            }
            if f < n && chars[f] == ')' {
                return true;
            }
        } else {
            i += 1;
        }
    }
    false
}

// ---------------------------------------------------------------------
// Registre de détecteurs (roadmap1.md, section "Architecture extensible
// pour de futurs langages") : plutôt qu'une cascade `if/else` figée où
// chaque nouvelle règle doit être insérée au bon endroit à la main,
// chaque signal est une entrée indépendante du tableau `DETECTORS`
// ci-dessous, avec un score de confiance explicite. `detect_dialect`
// évalue chaque entrée et retient celle dont le score est le plus haut
// parmi celles qui matchent — jamais un ordre `if/else` où la position
// dans le code fait office de priorité implicite. Ajouter un futur
// langage (WGSL, HLSL, Slang...) consiste à ajouter une entrée ici (et,
// séparément, un backend de compilation dans `shader.rs`, voir
// `ARCHITECTURE.md`), sans toucher à `detect_dialect` elle-même.
//
// Les scores eux-mêmes encodent la même priorité que l'ancienne cascade
// (mainImage > void main() > #version > gl_FragColor/Data), donc le
// comportement observable de cette section est strictement inchangé —
// seule la façon dont la priorité est exprimée change.
// ---------------------------------------------------------------------

/// Une règle de détection : si `matches` renvoie vrai sur la source (déjà
/// débarrassée de ses commentaires), le dialecte/signal associés sont un
/// candidat avec ce score de confiance. `matches` est un simple pointeur
/// de fonction (pas de closure/trait object) : le registre reste une
/// donnée statique, sans allocation ni indirection dynamique — cohérent
/// avec le choix de rester une "fondation légère" plutôt qu'un système de
/// plugins (voir roadmap1.md).
pub struct DialectDetector {
    pub dialect: ShaderDialect,
    pub signal: DialectSignal,
    /// Score arbitraire, seul l'ordre relatif entre détecteurs compte
    /// (pas de sémantique absolue). Deux détecteurs ne devraient jamais
    /// avoir la même valeur ; en cas d'égalité (bug de configuration),
    /// le premier rencontré dans `DETECTORS` gagne.
    pub confidence: u8,
    matches: fn(&str) -> bool,
}

fn matches_main_image(stripped: &str) -> bool {
    contains_whole_word(stripped, "mainImage")
}

fn matches_void_main(stripped: &str) -> bool {
    has_top_level_void_main(stripped)
}

fn matches_version_directive(stripped: &str) -> bool {
    stripped.contains("#version")
}

fn matches_frag_color_legacy(stripped: &str) -> bool {
    contains_whole_word(stripped, "gl_FragColor") || contains_whole_word(stripped, "gl_FragData")
}

/// Vrai si `stripped` contient un attribut d'entry point WGSL `@fragment`
/// suivi (à distance raisonnable, en tolérant d'autres attributs comme
/// `@compute`/`@vertex`/`@workgroup_size(...)` placés entre les deux) d'un
/// mot-clé `fn` — signature de fonction WGSL. `@` n'étant pas un caractère
/// valide en GLSL, ce signal ne peut jamais se confondre avec
/// `mainImage`/`main()`.
fn matches_wgsl_entry_point(stripped: &str) -> bool {
    let chars: Vec<char> = stripped.chars().collect();
    let n = chars.len();
    let target: Vec<char> = "@fragment".chars().collect();
    let tn = target.len();
    let mut i = 0;
    while i + tn <= n {
        if chars[i..i + tn] == target[..] {
            let before_ok = i == 0 || !is_ident_char(chars[i - 1]);
            let after_idx = i + tn;
            let after_ok = after_idx >= n || !is_ident_char(chars[after_idx]);
            if before_ok && after_ok && wgsl_fn_follows_attributes(&chars, after_idx) {
                return true;
            }
        }
        i += 1;
    }
    false
}

/// À partir de `start` (juste après un attribut `@xxx` déjà reconnu),
/// avance au-delà des espaces et d'éventuels autres attributs WGSL
/// (`@compute`, `@workgroup_size(8, 8)`, ...) jusqu'à trouver le mot-clé
/// `fn`. Renvoie faux si autre chose qu'un attribut ou `fn` est rencontré
/// en chemin (ex. attribut orphelin jamais suivi d'une fonction).
fn wgsl_fn_follows_attributes(chars: &[char], start: usize) -> bool {
    wgsl_fn_keyword_end_after_attributes(chars, start).is_some()
}

/// Cœur commun à `wgsl_fn_follows_attributes` (détection, section 1.2) et
/// `wgsl_fragment_entry_point_name` (extraction du nom, section 1.3) : à
/// partir de `start` (juste après un attribut `@xxx` déjà reconnu), avance
/// au-delà des espaces et d'éventuels autres attributs WGSL (`@compute`,
/// `@workgroup_size(8, 8)`, ...) jusqu'au mot-clé `fn`, et renvoie l'index
/// juste après ce `fn` si trouvé (`None` sinon — attribut orphelin jamais
/// suivi d'une fonction).
fn wgsl_fn_keyword_end_after_attributes(chars: &[char], start: usize) -> Option<usize> {
    let n = chars.len();
    let mut i = start;
    loop {
        while i < n && chars[i].is_whitespace() {
            i += 1;
        }
        if i < n && chars[i] == '@' {
            i += 1;
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            while i < n && chars[i].is_whitespace() {
                i += 1;
            }
            if i < n && chars[i] == '(' {
                let mut depth = 1;
                i += 1;
                while i < n && depth > 0 {
                    match chars[i] {
                        '(' => depth += 1,
                        ')' => depth -= 1,
                        _ => {}
                    }
                    i += 1;
                }
            }
            continue;
        }
        break;
    }
    if i + 2 <= n && chars[i..i + 2] == ['f', 'n'] {
        let before_ok = i == 0 || !is_ident_char(chars[i - 1]);
        let after_ok = i + 2 == n || !is_ident_char(chars[i + 2]);
        if before_ok && after_ok {
            return Some(i + 2);
        }
    }
    None
}

/// Nom du point d'entrée `@fragment fn <nom>(...)` détecté dans `stripped`
/// (même repérage que `matches_wgsl_entry_point`, mais retourne le nom
/// plutôt qu'un simple booléen) — RMLG.md, section 1.3 : contrairement au
/// harness GLSL, dont le point d'entrée est toujours le `void main()` fixe
/// qu'il construit lui-même (voir `shader::build_fragment_source`), le
/// mode WGSL passthrough ne réécrit pas le code utilisateur : son point
/// d'entrée réel peut porter n'importe quel nom, que `renderer.rs` doit
/// donc retrouver pour le passer à `wgpu::FragmentState::entry_point`.
/// `None` si aucun `@fragment fn ...` n'est trouvé (filet de sécurité : ne
/// devrait pas arriver pour un pass dont le dialecte détecté est déjà
/// `Wgsl`, `matches_wgsl_entry_point` cherchant exactement le même motif).
pub(crate) fn wgsl_fragment_entry_point_name(stripped: &str) -> Option<String> {
    let chars: Vec<char> = stripped.chars().collect();
    let n = chars.len();
    let target: Vec<char> = "@fragment".chars().collect();
    let tn = target.len();
    let mut i = 0;
    while i + tn <= n {
        if chars[i..i + tn] == target[..] {
            let before_ok = i == 0 || !is_ident_char(chars[i - 1]);
            let after_idx = i + tn;
            let after_ok = after_idx >= n || !is_ident_char(chars[after_idx]);
            if before_ok && after_ok {
                if let Some(fn_end) = wgsl_fn_keyword_end_after_attributes(&chars, after_idx) {
                    let mut m = fn_end;
                    while m < n && chars[m].is_whitespace() {
                        m += 1;
                    }
                    let name_start = m;
                    while m < n && is_ident_char(chars[m]) {
                        m += 1;
                    }
                    if m > name_start {
                        return Some(chars[name_start..m].iter().collect());
                    }
                }
            }
        }
        i += 1;
    }
    None
}

/// Vrai si `stripped` contient `var<uniform` / `var<storage` (qualificatif
/// de stockage WGSL, `var` suivi immédiatement de `<`, sans espace, comme
/// l'exige la syntaxe) — jamais une simple sous-chaîne libre.
fn matches_wgsl_storage_qualifier(stripped: &str) -> bool {
    let chars: Vec<char> = stripped.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if is_ident_start(chars[i]) {
            let start = i;
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            let word: String = chars[start..i].iter().collect();
            if word == "var" && i < n && chars[i] == '<' {
                let rest: String = chars[i..].iter().collect();
                if rest.starts_with("<uniform") || rest.starts_with("<storage") {
                    return true;
                }
            }
        } else {
            i += 1;
        }
    }
    false
}

/// Vrai si `stripped` contient un motif syntaxique réel de constructeur de
/// type générique natif WGSL sans équivalent GLSL valide :
/// `identifiant<...>(` (ex. `vec4<f32>(`) — la syntaxe générique `<...>`
/// n'existe pas comme constructeur en GLSL. Porte sur `stripped` (donc
/// jamais sur le contenu d'un commentaire) et sur ce motif syntaxique
/// précis, jamais sur une sous-chaîne libre comme `vec4<T>` dans un texte
/// quelconque.
fn matches_wgsl_generic_constructor(stripped: &str) -> bool {
    let chars: Vec<char> = stripped.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if is_ident_start(chars[i]) {
            while i < n && is_ident_char(chars[i]) {
                i += 1;
            }
            if i < n && chars[i] == '<' {
                let mut j = i + 1;
                let mut depth = 1;
                while j < n && depth > 0 {
                    match chars[j] {
                        '<' => depth += 1,
                        '>' => depth -= 1,
                        _ => {}
                    }
                    j += 1;
                }
                if depth == 0 {
                    let mut k = j;
                    while k < n && chars[k].is_whitespace() {
                        k += 1;
                    }
                    if k < n && chars[k] == '(' {
                        return true;
                    }
                }
                i = j;
                continue;
            }
        } else {
            i += 1;
        }
    }
    false
}

fn matches_wgsl_uniform_or_generic_type(stripped: &str) -> bool {
    matches_wgsl_storage_qualifier(stripped) || matches_wgsl_generic_constructor(stripped)
}

/// Registre ordonné (l'ordre du tableau ne sert qu'à départager une
/// éventuelle égalité de score, voir `DialectDetector::confidence` — la
/// priorité réelle vient des scores). Pour ajouter un futur langage :
/// ajouter une entrée avec un nouveau `ShaderDialect`/`DialectSignal` et
/// un score cohérent avec les autres (voir `ARCHITECTURE.md`).
const DETECTORS: &[DialectDetector] = &[
    DialectDetector {
        dialect: ShaderDialect::Shadertoy,
        signal: DialectSignal::MainImage,
        // Signal fort, quasi jamais un faux positif (nom de fonction très
        // spécifique au site) : prioritaire même si un `void main()` est
        // aussi présent (ex. `mainImage` appelant un helper nommé `main`).
        confidence: 100,
        matches: matches_main_image,
    },
    DialectDetector {
        dialect: ShaderDialect::Wgsl,
        signal: DialectSignal::WgslEntryPoint,
        // Signal fort comme MainImage (aucune ambiguïté possible : `@`
        // n'est pas un caractère valide en GLSL) — décalé à 95 uniquement
        // pour garder tous les scores distincts (voir
        // `registry_confidence_scores_are_strictly_ordered_and_unique`),
        // l'ordre relatif avec MainImage n'a pas de sens puisqu'aucun
        // texte ne peut matcher les deux à la fois.
        confidence: 95,
        matches: matches_wgsl_entry_point,
    },
    DialectDetector {
        dialect: ShaderDialect::GlslStandalone,
        signal: DialectSignal::VoidMain,
        confidence: 80,
        matches: matches_void_main,
    },
    DialectDetector {
        dialect: ShaderDialect::Wgsl,
        signal: DialectSignal::WgslUniformOrGeneric,
        // Signal secondaire, plus faible que VoidMain (80) mais plus fort
        // que VersionDirective (50) : utile pour un onglet WGSL "Common"
        // sans point d'entrée.
        confidence: 60,
        matches: matches_wgsl_uniform_or_generic_type,
    },
    DialectDetector {
        dialect: ShaderDialect::GlslStandalone,
        signal: DialectSignal::VersionDirective,
        confidence: 50,
        matches: matches_version_directive,
    },
    DialectDetector {
        dialect: ShaderDialect::GlslStandalone,
        signal: DialectSignal::FragColorLegacy,
        // Style GLSL ES 1.0 / ancien OpenGL : signal le plus faible, un
        // shader moderne bien formé ne devrait jamais avoir besoin qu'on
        // s'y fie s'il fournit aussi #version ou void main().
        confidence: 40,
        matches: matches_frag_color_legacy,
    },
];

/// Détecte le dialecte d'un shader. `previous` est le dialecte
/// actuellement affiché pour cet onglet (avant cette recompilation), pour
/// pouvoir le conserver quand le texte actuel ne contient plus aucun
/// signal exploitable (ex. onglet Common pur de helpers) plutôt que de
/// retomber sur une valeur par défaut arbitraire à chaque frappe.
///
/// Évalue chaque entrée de `DETECTORS` et retient celle qui matche avec le
/// score de confiance le plus haut (voir la doc du registre ci-dessus pour
/// la justification de chaque score). Aucun signal ne matche → dialecte
/// précédent conservé (ou Shadertoy par défaut à la toute première
/// détection, onglet neuf/vide).
pub fn detect_dialect(source: &str, previous: Option<ShaderDialect>) -> DialectDetection {
    let stripped = strip_comments(source);

    let mut best: Option<&DialectDetector> = None;
    for detector in DETECTORS {
        if !(detector.matches)(&stripped) {
            continue;
        }
        let should_replace = match best {
            Some(b) => detector.confidence > b.confidence,
            None => true,
        };
        if should_replace {
            best = Some(detector);
        }
    }

    match best {
        Some(d) => DialectDetection {
            dialect: d.dialect,
            signal: d.signal,
        },
        None => DialectDetection {
            dialect: previous.unwrap_or(ShaderDialect::Shadertoy),
            signal: DialectSignal::NoneKept,
        },
    }
}

// ---------------------------------------------------------------------
// Tests — couvrent explicitement chaque cas listé dans roadmap1.md :
// chaque signal isolé, l'ambiguïté (priorité Shadertoy), l'absence de
// signal (mode conservé), les variations d'espacement, et la
// non-détection sur un identifiant contenant "main" en sous-chaîne.
// ---------------------------------------------------------------------
#[cfg(test)]
mod tests {
    use super::*;

    fn det(source: &str, previous: Option<ShaderDialect>) -> DialectDetection {
        detect_dialect(source, previous)
    }

    // -----------------------------------------------------------------
    // RMLG.md, section 1.3 : `wgsl_fragment_entry_point_name`, utilisée
    // par `renderer.rs::compile_pass` pour retrouver le vrai nom du point
    // d'entrée fragment WGSL (jamais forcément `main`, contrairement au
    // harness GLSL).
    // -----------------------------------------------------------------

    #[test]
    fn wgsl_entry_point_name_simple() {
        let name = wgsl_fragment_entry_point_name("@fragment fn frag_main() -> @location(0) vec4<f32> { return vec4<f32>(1.0); }");
        assert_eq!(name.as_deref(), Some("frag_main"));
    }

    #[test]
    fn wgsl_entry_point_name_with_other_attributes_between() {
        // `@fragment` n'est pas forcément le tout premier attribut, ni
        // immédiatement suivi de `fn` sans autre attribut entre les deux.
        let name = wgsl_fragment_entry_point_name("@fragment @must_use fn main() -> @location(0) vec4<f32> { return vec4<f32>(0.0); }");
        assert_eq!(name.as_deref(), Some("main"));
    }

    #[test]
    fn wgsl_entry_point_name_ignores_other_functions() {
        let name = wgsl_fragment_entry_point_name(
            "fn helper() -> f32 { return 1.0; }\n@vertex fn vs_main() {}\n@fragment fn fs_main() -> @location(0) vec4<f32> { return vec4<f32>(1.0); }",
        );
        assert_eq!(name.as_deref(), Some("fs_main"));
    }

    #[test]
    fn wgsl_entry_point_name_none_when_absent() {
        let name = wgsl_fragment_entry_point_name("fn helper() -> f32 { return 1.0; }");
        assert_eq!(name, None);
    }

    #[test]
    fn detects_shadertoy_via_main_image() {
        let d = det("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }", None);
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::MainImage);
    }

    #[test]
    fn detects_shadertoy_via_main_image_without_in_qualifier() {
        // Le golfing retire déjà `in` par endroits — la détection doit
        // rester valide même sans ce qualificatif explicite.
        let d = det("void mainImage(out vec4 fragColor, vec2 fragCoord) { fragColor = vec4(1.0); }", None);
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::MainImage);
    }

    #[test]
    fn detects_glsl_via_void_main() {
        let d = det("out vec4 fragColOut;\nvoid main() { fragColOut = vec4(1.0); }", None);
        assert_eq!(d.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d.signal, DialectSignal::VoidMain);
    }

    #[test]
    fn detects_glsl_via_version_directive_alone() {
        // #version seul, sans main() ni mainImage dans ce fragment (ex.
        // detection lancée sur un extrait partiel en cours de frappe).
        let d = det("#version 450\n// TODO\n", None);
        assert_eq!(d.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d.signal, DialectSignal::VersionDirective);
    }

    #[test]
    fn detects_glsl_via_gl_frag_color_legacy() {
        let d = det("void main() { gl_FragColor = vec4(1.0); }", None);
        assert_eq!(d.dialect, ShaderDialect::GlslStandalone);
        // void main() est testé avant gl_FragColor dans l'ordre de
        // priorité, donc c'est ce signal-là qui remonte ici — cas couvert
        // séparément ci-dessous avec gl_FragData isolé.
        assert_eq!(d.signal, DialectSignal::VoidMain);
    }

    #[test]
    fn detects_glsl_via_gl_frag_data_when_no_other_signal() {
        // gl_FragData seul, sans void main() ni #version explicites dans
        // ce fragment (ex. extrait partiel collé dans un onglet Common).
        let d = det("gl_FragData[0] = vec4(1.0);\n", None);
        assert_eq!(d.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d.signal, DialectSignal::FragColorLegacy);
    }

    #[test]
    fn ambiguous_both_signals_present_main_image_wins() {
        // mainImage appelle un helper nommé "main" sans lien avec l'entrée
        // du programme -- mainImage doit rester prioritaire.
        let d = det(
            "float main(float x) { return x * 2.0; }\nvoid mainImage(out vec4 c, in vec2 f) { c = vec4(main(1.0)); }",
            None,
        );
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::MainImage);
    }

    #[test]
    fn no_signal_keeps_previous_mode() {
        // Fragment Common pur de helpers : ni mainImage, ni main(), ni
        // #version, ni gl_FragColor/Data.
        let d = det("float helper(float x) { return x + 1.0; }\n", Some(ShaderDialect::GlslStandalone));
        assert_eq!(d.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d.signal, DialectSignal::NoneKept);

        let d2 = det("float helper(float x) { return x + 1.0; }\n", Some(ShaderDialect::Shadertoy));
        assert_eq!(d2.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d2.signal, DialectSignal::NoneKept);
    }

    #[test]
    fn no_signal_and_no_previous_defaults_to_shadertoy() {
        // Tout premier onglet, jamais encore compilé : défaut Shadertoy
        // (comportement historique du logiciel avant ce chantier).
        let d = det("", None);
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::NoneKept);
    }

    #[test]
    fn ignores_signals_inside_comments() {
        let d = det("// void main() { gl_FragColor = vec4(1.0); }\nvoid mainImage(out vec4 c, in vec2 f) { c = vec4(1.0); }", None);
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::MainImage);

        let d2 = det("/* void mainImage(out vec4 c, in vec2 f) {} */\nvoid main() { gl_FragColor = vec4(1.0); }", None);
        assert_eq!(d2.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d2.signal, DialectSignal::VoidMain);
    }

    #[test]
    fn spacing_and_newline_variations_around_void_main() {
        let variants = [
            "void main(){gl_FragColor=vec4(1.0);}",
            "void   main  (  )  { gl_FragColor = vec4(1.0); }",
            "void\nmain\n(\n)\n{ gl_FragColor = vec4(1.0); }",
            "void\tmain\t(\t)\t{ gl_FragColor = vec4(1.0); }",
        ];
        for src in variants {
            let d = det(src, None);
            assert_eq!(d.dialect, ShaderDialect::GlslStandalone, "échec sur: {src:?}");
            assert_eq!(d.signal, DialectSignal::VoidMain, "échec sur: {src:?}");
        }
    }

    #[test]
    fn does_not_match_main_as_substring_of_longer_identifier() {
        // "mainCamera"/"domain" ne doivent jamais être pris pour "main" ou
        // "mainImage".
        let d = det(
            "uniform vec3 mainCamera;\nfloat domain(float x) { return x; }\nvoid mainImage(out vec4 c, in vec2 f) { c = vec4(domain(mainCamera.x)); }",
            None,
        );
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::MainImage);

        // Sans mainImage du tout : mainCamera/domain seuls ne doivent
        // déclencher ni VoidMain ni aucun autre signal.
        let d2 = det(
            "uniform vec3 mainCamera;\nfloat domain(float x) { return x; }\n",
            Some(ShaderDialect::Shadertoy),
        );
        assert_eq!(d2.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d2.signal, DialectSignal::NoneKept);
    }

    #[test]
    fn registry_confidence_scores_are_strictly_ordered_and_unique() {
        // Le registre s'appuie sur des scores tous distincts pour
        // départager les cas ambigus (roadmap1.md, section "Architecture
        // extensible") : une égalité accidentelle entre deux détecteurs
        // rendrait le résultat dépendant de l'ordre du tableau plutôt que
        // du score, ce que ce test empêche de merger silencieusement.
        for (i, a) in DETECTORS.iter().enumerate() {
            for b in DETECTORS.iter().skip(i + 1) {
                assert_ne!(
                    a.confidence, b.confidence,
                    "deux détecteurs partagent le même score de confiance ({:?} vs {:?})",
                    a.signal, b.signal
                );
            }
        }
    }

    #[test]
    fn registry_dialects_are_all_known_to_shader_dialect_all() {
        // Chaque dialecte référencé par un détecteur doit apparaître dans
        // `ShaderDialect::ALL` — sinon un code qui itère sur `ALL` (ex.
        // tests de `shader.rs` vérifiant les backends de compilation)
        // manquerait silencieusement ce dialecte.
        for detector in DETECTORS {
            assert!(
                ShaderDialect::ALL.contains(&detector.dialect),
                "dialecte {:?} absent de ShaderDialect::ALL",
                detector.dialect
            );
        }
    }

    #[test]
    fn detects_wgsl_via_fragment_entry_point() {
        let d = det(
            "@fragment\nfn fs_main() -> @location(0) vec4<f32> { return vec4<f32>(1.0); }",
            None,
        );
        assert_eq!(d.dialect, ShaderDialect::Wgsl);
        assert_eq!(d.signal, DialectSignal::WgslEntryPoint);
    }

    #[test]
    fn detects_wgsl_entry_point_tolerating_other_attributes_and_spacing() {
        let variants = [
            "@fragment fn fs_main() -> @location(0) vec4<f32> { return vec4<f32>(0.0); }",
            "@fragment\n\nfn fs_main() { }",
            // Point d'entrée compute ailleurs dans le fichier : ne doit
            // jamais empêcher la détection du `@fragment` réel.
            "@compute @workgroup_size(8, 8)\nfn cs_main() { }\n@fragment\nfn fs_main() { }",
        ];
        for src in variants {
            let d = det(src, None);
            assert_eq!(d.dialect, ShaderDialect::Wgsl, "échec sur: {src:?}");
            assert_eq!(d.signal, DialectSignal::WgslEntryPoint, "échec sur: {src:?}");
        }
    }

    #[test]
    fn detects_wgsl_via_storage_qualifier_without_entry_point() {
        // Onglet WGSL "Common" : que des uniforms, aucun point d'entrée.
        let d = det(
            "struct Globals { iTime: f32 };\n@group(0) @binding(0) var<uniform> globals: Globals;\n",
            None,
        );
        assert_eq!(d.dialect, ShaderDialect::Wgsl);
        assert_eq!(d.signal, DialectSignal::WgslUniformOrGeneric);
    }

    #[test]
    fn detects_wgsl_via_generic_constructor_without_entry_point() {
        let d = det("fn helper() -> f32 { let v = vec4<f32>(1.0, 0.0, 0.0, 1.0); return v.x; }", None);
        assert_eq!(d.dialect, ShaderDialect::Wgsl);
        assert_eq!(d.signal, DialectSignal::WgslUniformOrGeneric);
    }

    #[test]
    fn wgsl_entry_point_wins_over_secondary_signal() {
        let d = det(
            "var<uniform> globals: Globals;\n@fragment\nfn fs_main() -> vec4<f32> { return vec4<f32>(1.0); }",
            None,
        );
        assert_eq!(d.dialect, ShaderDialect::Wgsl);
        assert_eq!(d.signal, DialectSignal::WgslEntryPoint);
    }

    #[test]
    fn does_not_confuse_glsl_generic_looking_comparisons_with_wgsl() {
        // `a<b>(c)` n'existe pas vraiment en GLSL idiomatique, mais on
        // vérifie que de simples comparaisons juxtaposées (`x<y>(z)` ne
        // formant pas un vrai identifiant<...>() valide côté opérandes) ne
        // suffisent pas à elles seules à faire basculer un GLSL standard
        // qui a par ailleurs un signal GLSL clair et prioritaire.
        let d = det(
            "void main() { float a = 1.0; float b = 2.0; gl_FragColor = vec4(a < b ? 1.0 : 0.0); }",
            None,
        );
        assert_eq!(d.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d.signal, DialectSignal::VoidMain);
    }

    #[test]
    fn ignores_wgsl_entry_point_signal_inside_comments() {
        // Le texte `@fragment fn` dans un commentaire ne doit jamais faire
        // basculer un shader GLSL vers WGSL — couvert structurellement par
        // `strip_comments`, documenté ici par un test dédié.
        let d = det(
            "// @fragment fn fs_main() {}\nvoid main() { gl_FragColor = vec4(1.0); }",
            None,
        );
        assert_eq!(d.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d.signal, DialectSignal::VoidMain);

        let d2 = det(
            "/* @fragment\nfn fs_main() {} */\nvoid main() { gl_FragColor = vec4(1.0); }",
            None,
        );
        assert_eq!(d2.dialect, ShaderDialect::GlslStandalone);
        assert_eq!(d2.signal, DialectSignal::VoidMain);
    }

    #[test]
    fn no_signal_keeps_previous_wgsl_mode() {
        let d = det("fn helper(x: f32) -> f32 { return x + 1.0; }\n", Some(ShaderDialect::Wgsl));
        assert_eq!(d.dialect, ShaderDialect::Wgsl);
        assert_eq!(d.signal, DialectSignal::NoneKept);
    }

    #[test]
    fn does_not_match_main_call_without_preceding_void() {
        // Un appel "main()" sans "void" devant (ex. helper mal nommé,
        // jamais l'entrée réelle du shader) ne doit pas déclencher VoidMain.
        let d = det("float result = main();\n", Some(ShaderDialect::Shadertoy));
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::NoneKept);
    }
}

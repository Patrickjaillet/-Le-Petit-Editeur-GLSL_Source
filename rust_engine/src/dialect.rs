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
    pub const ALL: [ShaderDialect; 2] = [ShaderDialect::Shadertoy, ShaderDialect::GlslStandalone];

    pub fn id(self) -> &'static str {
        match self {
            ShaderDialect::Shadertoy => "shadertoy",
            ShaderDialect::GlslStandalone => "glsl",
        }
    }

    pub fn from_id(id: &str) -> Option<Self> {
        match id {
            "shadertoy" => Some(ShaderDialect::Shadertoy),
            "glsl" => Some(ShaderDialect::GlslStandalone),
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
        dialect: ShaderDialect::GlslStandalone,
        signal: DialectSignal::VoidMain,
        confidence: 80,
        matches: matches_void_main,
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
    fn does_not_match_main_call_without_preceding_void() {
        // Un appel "main()" sans "void" devant (ex. helper mal nommé,
        // jamais l'entrée réelle du shader) ne doit pas déclencher VoidMain.
        let d = det("float result = main();\n", Some(ShaderDialect::Shadertoy));
        assert_eq!(d.dialect, ShaderDialect::Shadertoy);
        assert_eq!(d.signal, DialectSignal::NoneKept);
    }
}

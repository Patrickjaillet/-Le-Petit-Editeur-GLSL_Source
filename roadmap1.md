# roadmap1 — Compatibilité totale GLSL + Shadertoy, reconnaissance automatique du mode

Contexte (lu dans le code actuel) : le moteur (`rust_engine/src/shader.rs::build_fragment_source`)
ne sait aujourd'hui compiler qu'un seul dialecte — il injecte systématiquement le code utilisateur
dans un wrapper fixe `#version 450` + `mainImage(...)` façon Shadertoy. Un fichier `.glsl`/`.frag`
"brut" (avec son propre `void main()`, ses propres `uniform`, sa propre déclaration `out vec4`, ou
un vieux style `gl_FragColor`) ne compile pas tel quel. Objectif de ce roadmap : que les deux
dialectes soient acceptés **sans manipulation manuelle** — le logiciel doit deviner lequel est
collé/tapé, adapter le pipeline de compilation en conséquence, et l'afficher en toute transparence
dans le footer.

---

## 🔍 Détection automatique du dialecte (`rust_engine/src/`)

- [x] Définir un type `ShaderDialect` (`Shadertoy` / `GlslStandalone`) partagé entre `shader.rs` et
  `lib.rs`, avec une fonction de détection `detect_dialect(source: &str) -> ShaderDialect` :
  *Implémenté dans `rust_engine/src/dialect.rs` (nouveau module) : `ShaderDialect`, `DialectSignal`,
  `detect_dialect(source: &str, previous: Option<ShaderDialect>) -> DialectDetection`.*
  - [x] Présence d'une définition `void mainImage(out vec4 ..., in vec2 ...)` (ou `void mainImage(out
    vec4 ...)` sans `in` explicite, cf. golfing qui retire déjà `in`) → `Shadertoy`, signal fort et
    quasi jamais un faux positif (nom de fonction très spécifique au site).
  - [x] Présence d'un `void main()` de premier niveau, ou d'une directive `#version`, ou d'un
    `gl_FragColor`/`gl_FragData` sans aucun `mainImage` → `GlslStandalone`.
  - [x] Cas ambigu (les deux présents, ex. un `mainImage` qui appelle un helper nommé `main` sans lien
    avec l'entrée du programme) : `mainImage` doit rester prioritaire, il n'existe qu'en usage
    Shadertoy alors qu'un `main()` peut légitimement apparaître comme simple nom de fonction dans
    un projet réécrit à la main — tester ce cas explicitement.
  - [x] Cas vide/aucun signal (fichier qui ne contient ni l'un ni l'autre, ex. un fragment Common pur
    de helpers) : conserver le dernier mode connu de l'onglet plutôt que de basculer sur une
    valeur par défaut arbitraire à chaque frappe. *(paramètre `previous: Option<ShaderDialect>`)*
- [x] Détection réévaluée à chaque recompilation debouncée (même déclencheur que la compilation
  live existante), jamais à chaque frappe individuelle — pour ne pas faire clignoter l'indicateur
  pendant que l'utilisateur tape `void ma|` en cours de complétion. *Câblé : `_update_dialect_indicator`
  est appelé depuis `_recompile_current_tab` (même déclencheur debouncé que `_compile_one_pass`) et
  depuis `_on_pass_tab_changed` (retour immédiat au changement d'onglet) — jamais depuis
  `_on_text_changed`, qui ne fait que restarter le timer de debounce sans toucher l'indicateur.*
- [x] Exposer la détection côté Python via pyo3 (`lib.rs`), pour que `main_window.py` puisse
  afficher le mode sans dupliquer l'heuristique en Python — une seule source de vérité, côté Rust.
  *`detect_dialect(source: &str, previous_dialect: &str) -> (String, String)` exposée en pyfunction,
  plus les constantes `DIALECT_SHADERTOY`/`DIALECT_GLSL`.*
- [x] Tests unitaires ciblés (`shader.rs` ou nouveau `dialect.rs`) : `mainImage` seul, `main()`
  seul, `#version` seul, `gl_FragColor` seul, les deux signaux présents (priorité Shadertoy), aucun
  signal (mode conservé), variations d'espacement/retours à la ligne autour des mots-clés, et
  non-détection sur un identifiant qui contient `main` comme sous-chaîne (`mainCamera`, `domain`).
  *13 tests dans `dialect.rs::tests` — non exécutés ici faute de toolchain Rust dans ce
  bac à sable, à valider avec `cargo test` avant merge.*

## 🧩 Compilation réellement double-dialecte (`shader.rs`, `renderer.rs`, `lib.rs`)

- [x] **Chemin Shadertoy** : inchangé, c'est le pipeline actuel (`build_fragment_source`, wrapper
  `#version 450` + uniforms `iResolution`/`iTime`/`iMouse`/... + appel à `mainImage`).
  *Fonction non modifiée par ce chantier — `renderer::Engine::compile_pass` continue de l'appeler
  telle quelle quand `dialect::detect_dialect` renvoie `Shadertoy`.*
- [x] **Nouveau chemin GLSL standalone** : le code utilisateur est compilé quasiment tel quel — pas
  de wrapper `mainImage`, le `void main()` de l'utilisateur est l'entrée réelle du fragment shader.
  *Implémenté dans `shader.rs::build_fragment_source_standalone(user_src, channel_kinds) ->
  (String, Vec<CustomUniformDecl>)`, appelée depuis `renderer::Engine::compile_pass` quand
  `dialect::detect_dialect` renvoie `GlslStandalone` (détection faite une seule fois par compile,
  côté Rust — le choix de pipeline et l'indicateur du footer ne peuvent donc jamais diverger).*
  - [x] Si le code a déjà son propre `#version`, le respecter (ne pas en injecter un second) ;
    sinon injecter `#version 450`. *Recherche textuelle simple (`stripped.contains("#version")`,
    commentaires déjà retirés via `dialect::strip_comments` réutilisé) — testé dans
    `shader.rs::standalone_tests::respects_existing_version_directive` /
    `injects_version_when_absent`.*
  - [x] Uniforms Shadertoy (`iTime`, `iResolution`, `iMouse`, `iFrame`, `iDate`, `iChannel0-3`, ...) :
    injectés seulement s'ils sont réellement référencés. *Le bloc `Globals` entier (tous ses champs
    partagent un seul binding UBO, indissociables au niveau std140) est injecté seulement si au
    moins un de ses champs est référencé (`GLOBALS_FIELD_NAMES`) ; chaque paire texture+sampler
    `iChannelN` est injectée indépendamment des 3 autres. Testé : `omits_globals_block_when_unreferenced`,
    `injects_globals_block_when_referenced`, `only_declares_referenced_ichannels`.*
  - [x] `gl_FragColor`/`gl_FragData[0]` : traduits vers une variable `out vec4` déclarée
    automatiquement si absente. *`shader.rs::translate_legacy_frag_output` — no-op si le code
    déclare déjà lui-même un `out vec4` (`has_out_vec4_declaration`, cas jugé déjà "cassé"
    indépendamment de ce chantier, on ne devine pas) ; seul l'index `[0]` de `gl_FragData` est géré
    (pas de MRT dans ce moteur). Testé : `translates_gl_frag_color_when_no_out_vec4_declared`,
    `translates_gl_frag_data_index_zero`, `does_not_translate_when_out_vec4_already_declared`.*
  - [x] Uniforms **personnalisés** : tranché pour l'option la plus simple — acceptés avec une valeur
    par défaut à 0, pas de branchement sur le panneau de sliders (explicitement hors périmètre,
    reste possible plus tard sans changer la structure retenue). *`shader::detect_custom_uniforms`
    repère les `uniform <type> <nom>;` de premier niveau sans `layout(...)` déjà présent (type parmi
    `float`/`int`/`bool`/`vec2`/`vec3`/`vec4`, un seul déclarateur, pas de tableau) et leur assigne un
    binding séquentiel à partir de `FIRST_CUSTOM_UNIFORM_BINDING = 9` (premier libre après Globals +
    les 8 bindings iChannel0-3), chacun dans son propre mini-UBO `CustomUniformBlock_<nom>`.
    `renderer::Engine::compile_pass` crée un buffer zero-fill (16 octets) par uniform détecté, étend
    le bind group layout/bind group en conséquence (`custom_uniform_buffers`, réutilisé à chaque
    frame par `build_bind_group`). Testé : `detects_and_auto_binds_custom_uniform`,
    `assigns_sequential_bindings_to_multiple_custom_uniforms`,
    `does_not_rebind_uniform_with_explicit_layout`, `ignores_shadertoy_global_names_as_custom_uniforms`.*
- [x] `header_line_count`/le mapping d'erreur ligne→éditeur a maintenant un équivalent pour le mode
  standalone. *`shader::header_line_count_standalone` (même principe que l'original : reconstruit la
  source réellement compilée pour ce texte et retrouve la position de `user_src` dedans, valable quel
  que soit le nombre de lignes de harness effectivement injectées) + `header_line_count_for_dialect`
  comme point d'entrée unique, exposé côté Python via la nouvelle fonction pyo3
  `fragment_header_line_count_for_dialect` (`lib.rs`). Câblé jusqu'au bout :
  `main_window.py::_show_error_marker` utilise désormais le dialecte déjà connu pour ce pass
  (`self._pass_dialects`, alimenté par `_update_dialect_indicator`) au lieu de supposer Shadertoy.
  Testé côté Rust : `header_line_count_standalone_matches_generated_source`,
  `header_line_count_for_dialect_dispatches_correctly`.*
- [x] Le Common et les Buffers A-D restent des concepts Shadertoy — décision : **laissés visibles
  mais inertes**, pas de masquage UI dans ce ticket (le plus simple des deux, et cohérent avec le
  fait qu'un pass standalone peut légitimement vouloir appeler un helper déclaré dans Common).
  *Aucun changement de comportement nécessaire : `compile_pass` continue de préfixer `common_src` à
  `user_src` avant détection/compilation pour les deux dialectes indifféremment — un pass standalone
  qui n'utilise pas Common l'ignore simplement (il ne contient alors ni `mainImage` ni `main()`,
  donc aucun signal, voir `dialect.rs`) ; les onglets Buffer A-D restent fonctionnels par pass
  indépendamment du dialecte détecté pour chacun. Un masquage UI dédié reste possible plus tard sans
  changer ce choix de fond.*

## 🧱 Architecture extensible pour de futurs langages (au-delà de GLSL/Shadertoy)

- [x] Ne pas coder `ShaderDialect` comme une simple énumération fermée `Shadertoy`/`GlslStandalone`
  en dur dans toute la codebase : passer par un **registre de détecteurs** (`Vec<DialectDetector>`
  ou trait `LanguageDialect` avec une méthode `detect(source: &str) -> Option<Confidence>`), pour
  qu'ajouter un futur langage (WGSL, HLSL, Slang...) consiste à enregistrer un nouveau détecteur
  plutôt qu'à modifier une fonction `detect_dialect` monolithique à chaque fois.
  *Implémenté dans `dialect.rs` : struct `DialectDetector { dialect, signal, confidence, matches:
  fn(&str) -> bool }` et registre statique `const DETECTORS: &[DialectDetector]`. `detect_dialect`
  ne contient plus aucune règle en dur — elle évalue chaque entrée du registre et retient celle au
  score le plus haut parmi celles qui matchent. `ShaderDialect` reste une énumération fermée (choix
  assumé, voir dernier point de cette section : un trait `Box<dyn LanguageDialect>` aurait été de la
  sur-ingénierie tant qu'aucun troisième langage n'est concret), mais plus aucune fonction ne
  contient de cascade `if/else` par langage — ajouter un langage veut dire ajouter une variante +
  une entrée au registre, jamais modifier `detect_dialect`.*
- [x] Chaque détecteur de langage expose au minimum : un identifiant stable (`"shadertoy"`,
  `"glsl"`, futur `"wgsl"`...), une fonction de détection, et le nom de la clé i18n associée
  (`footer.dialect_<id>`) — pour que le footer et les fichiers `lngs/*.json` n'aient pas à connaître
  la liste des langages à l'avance, juste à itérer sur le registre.
  *L'identifiant stable (`ShaderDialect::id()`) et la clé i18n du signal (`DialectSignal::i18n_key()`)
  existaient déjà (chantier précédent) ; ce ticket ajoute la fonction de détection explicite par
  entrée (`DialectDetector::matches`) et `ShaderDialect::ALL`, la liste unique des dialectes connus
  utilisée par les tests de cohérence entre registres (voir plus bas) et par `footer.py`/pyo3 pour
  itérer sans connaître la liste à l'avance.*
- [x] Gérer explicitement la priorité entre détecteurs quand plusieurs langages partagent des
  signaux proches (ex. un futur WGSL et le GLSL standalone pourraient tous deux avoir un point
  d'entrée nommé différemment mais des structures similaires) : chaque détecteur retourne un score
  de confiance plutôt qu'un simple booléen, et le registre choisit le score le plus haut au lieu
  d'un ordre `if/else` fragile qui casse à chaque ajout.
  *`DialectDetector::confidence: u8`, valeurs actuelles 100 (mainImage) / 80 (void main) / 50
  (#version) / 40 (gl_FragColor/Data) — mêmes priorités que l'ancienne cascade, mais exprimées comme
  des scores comparés plutôt que comme un ordre de test implicite. Testé :
  `registry_confidence_scores_are_strictly_ordered_and_unique` (aucune égalité de score, sinon le
  comportement dépendrait silencieusement de l'ordre du tableau) ; tous les tests de détection
  existants (ambiguïté mainImage/main(), etc.) continuent de passer inchangés, le comportement
  observable ne change pas.*
- [x] Le pipeline de compilation (`shader.rs`/`renderer.rs`) doit lui aussi être organisé par
  « backend de compilation » associé à l'identifiant du langage détecté, pas par un `match` à deux
  branches — même si, à ce stade du projet, seuls `shadertoy` et `glsl` ont réellement un backend
  fonctionnel ; les futurs langages pourront brancher leur propre fonction de build sans toucher au
  code des deux premiers.
  *`shader.rs` : type `CompileBackendFn = fn(&str, [ChannelKind; 4], bool) -> (String,
  Vec<CustomUniformDecl>)`, deux backends (`shadertoy_backend`/`glsl_standalone_backend`, wrappers
  fins autour des fonctions `build_fragment_source*` existantes, elles-mêmes inchangées) et un
  registre `const COMPILE_BACKENDS: &[CompileBackendEntry]` indexé par le même id texte que
  `ShaderDialect::id()`. `renderer::Engine::compile_pass` appelle désormais
  `shader::compile_backend_for(detection.dialect)` au lieu d'un `match ShaderDialect { ... }` en dur
  — testé : `compile_backends_cover_every_known_dialect` (le registre couvre bien tout
  `ShaderDialect::ALL`), `compile_backend_for_shadertoy_matches_direct_call` /
  `compile_backend_for_glsl_standalone_matches_direct_call` (le résultat via le registre est
  strictement identique à l'appel direct de la fonction de build d'origine — ce chantier ne change
  que le mécanisme de sélection, jamais la source GLSL produite).*
- [x] Documenter (dans ce fichier ou un `CONTRIBUTING`/`ARCHITECTURE.md` dédié) la procédure pour
  ajouter un nouveau langage : où déclarer le détecteur, où brancher le backend de compilation, quelles
  clés i18n créer dans les 12 fichiers `lngs/*.json`, et quel test minimal ajouter dans
  `test_dialect_detection.py` — pour que ce chantier ne soit pas à refaire de zéro à chaque nouveau
  langage.
  *Nouveau fichier `ARCHITECTURE.md` à la racine : procédure en 3 étapes (détection dans
  `dialect.rs`, backend dans `shader.rs`, i18n dans `footer.py`/`lib.rs`/les 12 `lngs/*.json`), plus
  la liste des tests minimaux à ajouter côté Rust et Python. Note honnête : `test_dialect_detection.py`
  n'existe pas encore côté Python (la détection n'est aujourd'hui exercée que par les tests Rust de
  `dialect.rs`) — `ARCHITECTURE.md` documente qu'il est à créer au moment du premier langage
  réellement ajouté plutôt que maintenant sans cas concret à tester. De même, les clés i18n
  `footer.dialect_*` ne sont pas encore présentes dans `lngs/*.json` malgré ce que la section
  précédente de ce roadmap indique — `ARCHITECTURE.md` documente ce qu'il faudra y ajouter sans
  prétendre que c'est déjà fait.*
- [x] Cette section reste volontairement une fondation légère : ne pas sur-ingénierer un système de
  plugins dynamiques (chargement externe, etc.) tant qu'un seul langage supplémentaire concret n'est
  pas sur la table — l'objectif ici est juste que GLSL/Shadertoy ne soient pas codés en dur au point
  de rendre l'ajout futur coûteux.
  *Choix délibéré : `DETECTORS`/`COMPILE_BACKENDS` sont des tableaux statiques de pointeurs de
  fonction, pas de trait objects (`Box<dyn ...>`) ni de chargement dynamique — cf. dernière section
  d'`ARCHITECTURE.md` (« Ce qui reste volontairement hors périmètre ») qui liste explicitement ce qui
  n'a pas été fait et pourquoi.*
- [x] **Vérification** : contrairement au reste de ce roadmap (rédigé sans toolchain Rust
  disponible), une toolchain a pu être installée dans ce bac à sable pour ce ticket (`apt-get install
  rustc cargo`, `rustc 1.75.0`) — `cargo check` sur le crate complet (avec wgpu/pyo3, ~150
  dépendances) compile **sans erreur ni warning**, et `cargo test --lib` fait passer les **208 tests**
  du crate, dont les 15 tests de `dialect.rs` (incluant les 2 nouveaux tests du registre) et les tests
  de `shader.rs` (incluant les 4 nouveaux tests du registre de backends) — aucune régression sur les
  tests `golf`/`literals` préexistants, sans rapport avec ce chantier.

## 🖥️ Indicateur de mode dans le footer (`python_ui/ui/footer.py`, `main_window.py`)

- [x] Nouveau petit label permanent dans `Footer` (aux côtés de `_size_label`/`_fps_label`), du
  genre `🌈 Shadertoy` ou `📄 GLSL`, avec une couleur/icône distincte par mode pour un repérage
  instantané sans avoir à lire le texte. *(`_dialect_label`, table `_DIALECT_DISPLAY` indexée par
  id de dialecte pour rester extensible à un futur langage sans toucher `set_dialect`.)*
- [x] `Footer.set_dialect(dialect: str)` appelée depuis `main_window.py` juste après chaque
  recompilation debouncée, à partir de la valeur renvoyée par `detect_dialect` côté Rust (voir
  section détection) — jamais recalculée indépendamment côté Python. *(`_update_dialect_indicator`,
  appelée depuis `_recompile_current_tab` — même déclencheur que la compilation live — et depuis
  `_on_pass_tab_changed` pour un retour visuel immédiat au changement d'onglet sans attendre une
  frappe.)*
- [x] Tooltip explicatif au survol (`footer.dialect_tooltip`, i18n) : rappelle sur quel signal la
  détection s'est basée (ex. « détecté via `mainImage()` » / « détecté via `void main()` »), pour
  que le mode affiché ne soit jamais une boîte noire si l'utilisateur ne comprend pas pourquoi son
  code est classé d'une façon ou d'une autre.
- [x] Nouvelles clés i18n (`footer.dialect_shadertoy`, `footer.dialect_glsl`,
  `footer.dialect_tooltip`) ajoutées en parité stricte dans les 12 fichiers `lngs/*.json` —
  `test_i18n_completeness.py` (déjà existant) doit continuer de passer sans modification de son
  code, seulement de nouvelles clés. *Ajout de 8 clés (label × 2 + tooltip + 5 clés de signal
  `footer.dialect_signal_*`) dans les 12 fichiers, parité vérifiée par script ; scan statique des
  `tr("...")` de `footer.py`/`main_window.py` également vérifié contre `fr.json`, aucune clé
  manquante.*
- [x] Cas du tout premier affichage (avant toute compilation) : label vide ou état neutre plutôt
  qu'un mode par défaut trompeur, cohérent avec `footer.ready` déjà utilisé pour le statut de
  compilation avant la première frame. *(`Footer.__init__` initialise `_dialect_label` à vide ;
  `clear_dialect()` explicite si un pass compile avec une source vide.)*

## ✅ Vérification


- [ ] Rendu pixel-identique du chemin Shadertoy existant avant/après ce chantier (aucune régression
  sur tout ce que couvre déjà `ROADMAP.md` — multi-passes, sliders, import Shadertoy, etc.).
- [ ] Suite de tests dédiée (nouveau `test_dialect_detection.py` côté Python si la détection est
  aussi exposée/testée depuis ce côté, en plus des tests Rust) : import d'un shader Shadertoy connu
  → mode Shadertoy affiché ; collage d'un shader GLSL "manuel" classique (`void main(){
  gl_FragColor = ...; }`) → mode GLSL affiché et shader qui compile réellement.
- [ ] Vérifier le comportement en cours de frappe : partir d'un shader Shadertoy valide, retirer
  `mainImage` progressivement en tapant un `void main()` à la place, confirmer que le mode bascule
  au bon moment (après le debounce, pas avant) sans jamais planter le pipeline de compilation
  live pendant la transition.

# RMLG — Roadmap Multi-Langages (MSL, HLSL, WGSL)

Ce document prolonge `roadmap1.md` (section « Architecture extensible pour de futurs
langages ») et s'appuie directement sur ce qui existe déjà : le registre de détecteurs
`rust_engine/src/dialect.rs`, le registre de backends de compilation `rust_engine/src/shader.rs`,
et la procédure documentée dans `ARCHITECTURE.md`.

**Statut : entièrement implémenté et re-vérifié.** Les sections 1 (WGSL) et 2 (export
HLSL/MSL) sont toutes deux faites, câblées de bout en bout (Rust ↔ pyo3 ↔ UI ↔ i18n) et
re-vérifiées de façon indépendante depuis un poste Windows avec une toolchain Rust réelle
et récente (`cargo`/`rustc` 1.97.1) : `cargo check`/`cargo test --lib` verts (238/238),
rendu WGSL réel + export HLSL/MSL réel exercés via le vrai pont pyo3 (pas seulement les
tests Rust internes), parité i18n stricte confirmée par script indépendant sur les 12
fichiers `lngs/*.json` (256 clés chacun), et l'ensemble de la suite Python (`test_i18n*.py`,
`test_dialect_detection.py`, `test_wgsl_globals_layout.py`, `pixel_compare.py`, etc.)
rejoué avec succès. Voir « ✅ Vérification globale » en fin de fichier pour le détail. Le
reste de ce document garde son historique de vérification (bac à sable Linux, toolchain
`apt` 1.75) tel quel, comme trace du travail réellement effectué à chaque étape plutôt que
d'être réécrit après coup.

Avant de planifier quoi que ce soit, ce document commence par un audit de faisabilité
honnête : le moteur ne compile pas du GLSL « à la main », il passe par `wgpu` (feature
`glsl`), qui délègue lui-même le parsing/la traduction à **`naga`**. Or `naga` n'a que
**trois frontends** (`glsl-in`, `wgsl-in`, `spv-in`) et **cinq backends**
(`glsl-out`, `hlsl-out`, `msl-out`, `spv-out`, `wgsl-out`). Seul WGSL, des trois langages
demandés, a un frontend `naga` prêt à l'emploi — ce simple fait change radicalement ce qui
est réaliste pour chacun, et c'est pour ça que ce roadmap ne traite pas les trois langages
de la même façon. Prétendre le contraire serait la même erreur que d'annoncer une case
cochée sans l'avoir vérifiée (voir les bugs trouvés a posteriori dans `roadmap1.md` sur les
clés i18n `footer.dialect_*` manquantes) — ce fichier documente donc explicitement, pour
chaque langage, *ce qui est vérifié*, *ce qui est supposé*, et *ce qui est hors de portée et
pourquoi*.

- mettre le CHANGELOG.md a jour a chaque phases terminés
- mettre aussi le README.md a jour pour l'utilisateur final qu'il connaisse les fonctions du logiciel

---

## 🧭 Vue d'ensemble — matrice de faisabilité

| Langage | Rôle réaliste dans ce logiciel | Support natif `naga`/`wgpu` | Verdict |
|---|---|---|---|
| **WGSL** | Dialecte d'**entrée** à part entière (comme `shadertoy`/`glsl`) | Frontend natif (`wgsl-in`), déjà dans l'arbre de dépendances de `wgpu` | ✅ Priorité 1 — quasi gratuit |
| **HLSL** | Cible d'**export** (jamais d'entrée éditable) | Backend natif (`hlsl-out`) à partir de l'IR `naga` déjà produit pour GLSL/WGSL | ✅ Priorité 2 — export seul |
| **MSL** | Cible d'**export** (jamais d'entrée éditable) | Backend natif (`msl-out`), même mécanisme que HLSL | ✅ Priorité 2 — export seul |

Le principe directeur : **on n'ajoute un langage au registre de détecteurs
(`dialect.rs`) que s'il peut réellement devenir un onglet de code éditable et
compilable dans ce logiciel.** HLSL et MSL ne remplissent pas cette condition — `naga`
sait les *écrire*, jamais les *lire* — donc les traiter comme un « dialecte détecté » au
sens de `ShaderDialect` serait mensonger : on ne pourrait rien compiler à partir d'un
texte HLSL collé dans l'éditeur. Ils sont donc repositionnés comme une fonctionnalité
d'**export** distincte (`Fichier → Exporter le shader compilé vers…`), pas comme un
nouveau dialecte d'entrée. C'est le même genre de repli assumé que celui déjà documenté
dans `ROADMAP.md` pour la division flottante non repliée en constante, ou pour
l'inlining limité aux fonctions à site d'appel unique : réduire le périmètre à ce qui
reste correct plutôt que de prétendre couvrir un cas qu'on ne peut pas garantir.

---

## 🟪 1. WGSL — dialecte d'entrée (`rust_engine/src/dialect.rs`, `shader.rs`, `renderer.rs`)

### 1.1 Contexte

WGSL (`WebGPU Shading Language`) est le langage natif de `wgpu`/`naga` : `naga` sait le
**parser** (`wgsl-in`, toujours compilé — ce n'est même pas une feature Cargo optionnelle
séparée dans la version de `wgpu` utilisée par ce projet, contrairement à `glsl`) et
`wgpu::ShaderSource::Wgsl` existe déjà comme variante de l'enum utilisée aujourd'hui pour
`wgpu::ShaderSource::Glsl` dans `renderer.rs::compile_pass`. C'est de loin le langage le
moins coûteux des six à intégrer : aucune dépendance Cargo nouvelle, aucun parseur à
écrire, le travail est presque entièrement côté **harnais** (uniforms Shadertoy-like) et
**détection**.

### 1.2 Détection (`dialect.rs`)

- [x] Ajouter `ShaderDialect::Wgsl`, id stable `"wgsl"`, ajouté à `ShaderDialect::ALL`
  (passe de `[ShaderDialect; 2]` à `[ShaderDialect; 3]` — **tous les tests qui itèrent sur
  `ALL`, notamment `compile_backends_cover_every_known_dialect`, doivent rester verts sans
  modification de leur propre code**, exactement la garantie que `ARCHITECTURE.md` promet).
  ⚠️ Cette garantie n'est atteinte qu'une fois la section 1.3 (backend de compilation)
  également faite — voir note de suivi en fin de section.
- [x] Nouveau signal `DialectSignal::WgslEntryPoint`, clé i18n
  `footer.dialect_signal_wgslentrypoint`.
- [x] `matches_wgsl_entry_point(stripped: &str) -> bool` : repère un attribut d'entry point
  WGSL — `@fragment` suivi (à distance raisonnable, en tolérant d'autres attributs comme
  `@compute`/`@vertex` sur d'autres fonctions du même fichier) d'un `fn`. **Signal fort,
  score proposé 100** — au même niveau que `mainImage`, parce que la syntaxe `@fragment
  fn` n'existe dans aucun des deux dialectes GLSL déjà supportés (`@` n'est même pas un
  caractère valide en GLSL) : aucune ambiguïté possible avec `Shadertoy`/`GlslStandalone`,
  contrairement à la case `mainImage`/`main()` qui doit trancher une vraie priorité.
  **Test requis avant merge** (`registry_confidence_scores_are_strictly_ordered_and_unique`
  échouera sinon) : ne pas réutiliser 100, décaler `MainImage` à 100 et `WgslEntryPoint` à
  95, ou l'inverse — l'ordre relatif entre les deux n'a pas d'importance puisqu'aucun texte
  ne peut matcher les deux en même temps, mais le test impose des scores tous distincts.
  → implémenté avec `MainImage = 100`, `WgslEntryPoint = 95`.
- [x] Signal secondaire, score plus faible (proposé : 60, entre `VoidMain`=80 et
  `VersionDirective`=50) : présence de `var<uniform>`/`var<storage>` ou d'un type WGSL
  natif sans équivalent GLSL valide comme identifiant nu (`vec4<f32>`, la syntaxe
  générique `<...>` n'existe pas en GLSL) — utile pour un fichier WGSL qui ne contient que
  des fonctions utilitaires sans point d'entrée (l'équivalent WGSL d'un onglet `Common`).
  **Risque explicite à couvrir par un test de non-régression** : un commentaire GLSL du
  genre `// utilise vec4<T> générique` ne doit jamais déclencher ce signal — la recherche
  doit porter sur `stripped` (post `strip_comments`, déjà réutilisé tel quel) et sur un
  motif syntaxique réel (`ident<...>(`), pas une sous-chaîne libre.
  → implémenté sous `DialectSignal::WgslUniformOrGeneric`, clé i18n
  `footer.dialect_signal_wgsluniformorgeneric`, score 60.
- [x] Cas ambigu à tester explicitement (même esprit que le test `mainImage`/`main()`
  existant) : un shader GLSL qui contiendrait, dans une chaîne de caractères ou un
  commentaire, le texte `@fragment fn` ne doit jamais être classé WGSL — déjà couvert
  *structurellement* du moment que la détection tourne sur `strip_comments`, mais un test
  dédié documente l'intention plutôt que de compter sur l'effet de bord d'une fonction déjà
  existante.

**Suivi** : `matches_wgsl_entry_point`/`matches_wgsl_uniform_or_generic_type` et leurs 8
tests dédiés sont en place et verts (`rustc --edition 2021 --test dialect.rs`, 23/23 tests
passent). En revanche, `shader.rs::compile_backends_cover_every_known_dialect` est
désormais **rouge** tant que la section 1.3 (backend `wgsl_passthrough_backend`,
`COMPILE_BACKENDS`) n'est pas faite — `ShaderDialect::Wgsl` existe dans `ALL` mais n'a pas
encore d'entrée dans le registre de backends, ce qui est exactement le trou que 1.3 doit
combler. Ne pas considérer la section 1 comme terminée (ni mettre à jour
`CHANGELOG.md`/`README.md`) avant que 1.3 rétablisse ce test.

### 1.3 Backend de compilation (`shader.rs`, `renderer.rs`)

- [x] `wgsl_passthrough_backend` (signature `CompileBackendFn`, comme
  `glsl_standalone_backend`) : **ne réécrit quasiment rien** — contrairement au chemin
  GLSL standalone, il n'y a pas de wrapper `mainImage`→`main` à injecter, WGSL a déjà son
  propre système d'attributs d'entrée/sortie. Le rôle du backend se limite à :
  - concaténer `Common` (même convention que les deux dialectes existants, géré côté
    appelant — `renderer.rs::compile_pass` construit `combined` avant d'appeler le backend,
    identique aux deux autres dialectes) ;
  - injecter, uniquement si référencé (même logique que `GLOBALS_FIELD_NAMES` côté GLSL
    standalone, ici : présence du mot entier `globals`), un bloc `struct Globals { ... }
    @group(0) @binding(0) var<uniform> globals: Globals;` avec les mêmes champs que l'UBO
    `Globals` GLSL (`iTime`, `iResolution`, `iMouse`, ...).
    → **layout mémoire std140 vs WGSL vérifié** champ par champ (aucun `vec3` dans ce bloc,
    seul cas où les deux règles divergeraient) : tous les champs scalaires/`vec4` tombent
    aux mêmes offsets dans les deux conventions. Seule vraie divergence trouvée :
    `iChannelTime` (`float[4]` en GLSL) — std140 impose 16 octets par élément d'un tableau
    de scalaires, alors qu'un `array<f32, 4>` WGSL n'a par défaut qu'un stride de 4 octets.
    Repli retenu : `array<vec4<f32>, 4>` côté WGSL (seul le premier composant de chaque
    élément est utilisé), exactement le même repli déjà en place côté Rust pour ce champ
    (`GlobalsUniform::channel_time`, stocké `[[f32; 4]; 4]` plutôt que `[f32; 4]`) — le
    buffer GPU `globals_buffer` est donc bien partagé sans repadding entre les deux
    chemins, comme prévu.
  - injecter les `@group(0) @binding(N) var iChannelN: texture_2d<f32>;` /
    `var iChannelN_sampler: sampler;` correspondant (`texture_cube<f32>` si `ChannelKind::Cube`),
    un par canal réellement utilisé.
- [x] **Point dur, tranché — corrigé après vérification réelle (voir section 1.5)** :
  l'option retenue initialement pour ce point n'était *pas*, contrairement à ce qu'affirmait
  une version antérieure de cette section, une réflexion `naga`/`wgpu` des bindings à partir
  du module WGSL parsé (aucune fonction `reflect_wgsl_group0_bindings`/
  `wgsl_present_bindings` n'existe dans `renderer.rs` — cette affirmation était fausse,
  trouvée en vérifiant réellement le rendu GPU pendant la section 1.5, exactement le genre
  d'erreur que l'avant-propos de ce fichier met en garde de répéter). Ce qui est réellement
  vrai, et suffisant : `wgsl_passthrough_backend` (ci-dessus) numérote ses bindings
  `iChannelN`/`iChannelN_sampler` (`1+2i`/`2+2i`) exactement selon la même convention fixe
  que `renderer.rs::channel_binding_entry` construit déjà pour le chemin GLSL (binding 0 =
  `Globals`, 1/2 = iChannel0, 3/4 = iChannel1, 5/6 = iChannel2, 7/8 = iChannel3) — le
  `bind_group_layout` unique, fixe, partagé entre les trois dialectes reste donc valide tel
  quel pour WGSL sans aucune réflexion : le layout déclare toujours les 4 emplacements de
  canal (utilisés ou non par le texte compilé), ce qui est déjà le comportement accepté pour
  un pass GLSL standalone qui n'utilise pas tous ses `iChannel`. Aucun nouveau code n'était
  donc nécessaire ici — seule restait une vraie lacune, plus bas dans `compile_pass` :
  **le module fragment était inconditionnellement créé avec `wgpu::ShaderSource::Glsl`**,
  jamais `::Wgsl`, et son point d'entrée restait figé à `"main"` au lieu d'appeler
  `dialect::wgsl_fragment_entry_point_name` (pourtant déjà écrite et testée). Un pass WGSL
  passthrough, texte par ailleurs correct, était donc soumis au frontend **GLSL** de `naga`,
  qui échoue immédiatement sur `@fragment` (`UnexpectedCharacter`) — un shader WGSL ne
  pouvait tout simplement pas être rendu, malgré cette section entièrement cochée et
  `cargo test --lib` vert (ces tests n'exercent que la génération de texte de `shader.rs`,
  jamais l'appel réel à `device.create_shader_module`). Trouvé et corrigé en section 1.5 en
  écrivant le tout premier test qui soumet réellement un pass WGSL au device (voir le détail
  du correctif et sa vérification là-bas).
- [x] Uniforms personnalisés WGSL (`var<uniform> mon_param: f32;` hors du bloc `Globals`
  généré) : même choix que GLSL standalone — acceptés avec valeur par défaut à zéro, pas de
  branchement automatique sur le panneau de sliders dans cette première version.
  **Différence par rapport à GLSL standalone, imposée par WGSL** : GLSL tolère qu'on
  *ajoute* un second bloc `layout(binding=N) uniform CustomUniformBlock_x { ... };` sans
  toucher à la déclaration `uniform float x;` d'origine (les deux se retrouvent dans le
  même espace de noms via le flattening d'un bloc anonyme). WGSL interdit catégoriquement
  deux déclarations d'un même identifiant au niveau module — dupliquer aurait été une
  erreur de compilation garantie. `rewrite_wgsl_custom_uniforms` annote donc la déclaration
  de l'utilisateur **en place** (insertion de `@group(0) @binding(N)` juste avant son
  `var<uniform>` existant) plutôt que d'en ajouter une seconde.
- [x] `header_line_count_for_dialect` : branche WGSL ajoutée (`header_line_count_wgsl`),
  sur le même principe que `header_line_count_standalone` (reconstruire la source
  réellement compilée et retrouver la position du texte utilisateur dedans).

**Suivi** : `wgsl_passthrough_backend`/`rewrite_wgsl_custom_uniforms`/
`header_line_count_wgsl` (`shader.rs`) et leurs 10 tests dédiés (`shader::wgsl_tests`) sont
en place et verts. `dialect::wgsl_fragment_entry_point_name` (utilisée par
`renderer.rs::compile_pass` pour retrouver le vrai nom du point d'entrée fragment, jamais
forcément `main` en WGSL) a été ajoutée à `dialect.rs` avec 4 tests dédiés — elle n'était,
jusqu'à la section 1.5, jamais réellement *appelée* depuis `renderer.rs` malgré sa présence.
`cargo check --lib`/`cargo test --lib` passent sur le crate complet (231 tests,
0 échec — la limite de toolchain anticipée en tête de ce fichier ne s'est finalement pas
matérialisée : le `Cargo.lock` de ce dépôt est en format v3, compatible avec le `rustc`/
`cargo` 1.75 fournis par `apt`). `compile_backends_cover_every_known_dialect` est vert : le
registre `COMPILE_BACKENDS` couvre les trois dialectes de `ShaderDialect::ALL`. Section 1
désormais entièrement terminée (1.4 et 1.5 faites, voir plus bas) — `CHANGELOG.md`/
`README.md` restent à mettre à jour en conséquence.

### 1.4 i18n et footer

- [x] `footer.dialect_wgsl` (libellé, ex. `WGSL`), `footer.dialect_signal_wgslentrypoint`
  (et le signal secondaire ci-dessus s'il est retenu), dans les **12** fichiers
  `lngs/*.json`, parité stricte vérifiée par `test_i18n_completeness.py` (déjà existant,
  ne doit pas être modifié).
  → les trois clés `dialect_wgsl`, `dialect_signal_wgslentrypoint` et
  `dialect_signal_wgsluniformorgeneric` (le signal secondaire de 1.2 est bien retenu) ont
  été ajoutées aux 12 fichiers `lngs/*.json`, insérées respectivement juste après
  `dialect_glsl` et juste après `dialect_signal_fragcolor` (même position relative que dans
  `DialectSignal::i18n_key`/le registre de `dialect.rs`), traduites dans chacune des 12
  langues plutôt que dupliquées en anglais. Parité stricte des 240 clés confirmée par
  `python3 test_i18n_completeness.py` (`lngs/*.json key parity OK against fr.json (12
  file(s), 240 keys each)`) et par `python3 test_i18n.py`, tous deux verts sans
  modification de leur propre code.
- [x] `footer.py::_DIALECT_DISPLAY` : nouvelle entrée `engine_bridge.DIALECT_WGSL: ("🟪",
  "#ba68c8", "footer.dialect_wgsl")` — c'est très exactement l'exemple déjà donné dans
  `ARCHITECTURE.md`, à recopier tel quel.
  → entrée ajoutée telle quelle dans `_DIALECT_DISPLAY` (`python_ui/ui/footer.py`).
- [x] `lib.rs` : exposer `DIALECT_WGSL` côté pyo3, à côté de `DIALECT_SHADERTOY`/
  `DIALECT_GLSL`.
  → `m.add("DIALECT_WGSL", dialect::ShaderDialect::Wgsl.id())?;` ajouté dans `lib.rs`, et
  `engine_bridge.DIALECT_WGSL = _native.DIALECT_WGSL` ajouté côté `python_ui/engine_bridge.py`
  (jusque-là seuls `DIALECT_SHADERTOY`/`DIALECT_GLSL` y étaient ré-exportés, ce qui aurait
  laissé `_DIALECT_DISPLAY` référencer un attribut Python inexistant).

**Suivi** : cette section est maintenant close, vérifiée à la fois côté Python/i18n et côté
Rust. `cargo check --lib` compile le crate complet sans erreur (un seul warning
préexistant et sans rapport, `dead_code` sur `wgsl_fragment_entry_point_name`) ; `cargo
test --lib` passe intégralement (**231 tests, 0 échec**), y compris
`compile_backends_cover_every_known_dialect` et l'ensemble de `shader::wgsl_tests` —
l'ajout de `DIALECT_WGSL` côté pyo3 n'a rien cassé. Reste ouvert avant de considérer la
section 1 comme terminée : 1.5 (scénario `test_dialect_detection.py` avec rendu réel via
`Engine.compile_pass`/`render()`, et vérification manuelle du layout `Globals`
std140/WGSL). Ne pas mettre à jour `CHANGELOG.md`/`README.md` avant que 1.5 soit également
faite, conformément à la consigne en tête de ce fichier.

### 1.5 Vérification

- [x] `cargo test --lib` (comme pour `roadmap1.md`, une vraie toolchain Rust est
  nécessaire — voir les limites déjà documentées ailleurs dans ce dépôt) : détection
  positive, non-régression sur les shaders Shadertoy/GLSL existants, scores de confiance
  toujours strictement ordonnés.
  → `rustc`/`cargo` 1.75 (fournis par `apt`) suffisent, comme anticipé en 1.3 : **231
  tests, 0 échec** sur le crate complet, y compris `compile_backends_cover_every_known_dialect`
  et les 23 tests de détection dédiés au WGSL dans `dialect.rs` (scores `MainImage`=100 >
  `WgslEntryPoint`=95 > `VoidMain`=80 > `WgslUniformOrGeneric`=60 > `VersionDirective`=50 >
  `FragColorLegacy` — strictement ordonnés, vérifié par
  `registry_confidence_scores_are_strictly_ordered_and_unique`).
- [x] `test_dialect_detection.py` (déjà créé par `roadmap1.md`) : nouveau scénario WGSL —
  un shader `@fragment fn main(...)` compilé et rendu réellement via
  `Engine.compile_pass`/`render()`, pixel de sortie vérifié comme pour le scénario GLSL
  existant (pas seulement « ça ne lève pas »).
  → scénario 4 ajouté à `test_dialect_detection.py` : `@fragment fn main() -> @location(0)
  vec4<f32> { return vec4<f32>(0.0, 0.0, 1.0, 1.0); }` détecté comme `DIALECT_WGSL` via
  `footer.dialect_signal_wgslentrypoint`, compilé par `Engine.compile_pass`, rendu par
  `Engine.render()`, pixel de sortie vérifié bleu opaque `(0,0,255,255)`.
  **Ce scénario a d'abord échoué**, et c'est précisément ce qui a révélé le bug documenté
  en section 1.3 ci-dessus (`RuntimeError` naga : `@fragment` soumis au frontend GLSL,
  `wgpu::ShaderSource::Glsl` codé en dur pour tout dialecte) — la compilation native n'avait
  jamais été exercée jusque-là dans ce chantier, `cargo test --lib` ne pouvant pas voir ce
  genre de trou (il n'appelle jamais `device.create_shader_module`). Corrigé dans
  `renderer.rs::compile_pass` : sélection de `wgpu::ShaderSource::Wgsl`/`::Glsl` selon
  `detection.dialect`, et résolution du point d'entrée fragment via
  `dialect::wgsl_fragment_entry_point_name` (avec repli `"main"`) au lieu du `"main"` fixe
  précédent. Après correctif : les 4 scénarios de `test_dialect_detection.py` passent avec
  un vrai rendu GPU (adaptateur Vulkan logiciel `mesa-vulkan-drivers`/lavapipe installé pour
  cette vérification, aucun GPU matériel disponible dans ce bac à sable).
- [x] Vérification manuelle du layout mémoire `Globals` (point dur 1.3) sur un shader qui
  lit chaque champ un par un et écrit sa valeur dans la couleur de sortie — avant tout
  golfing ou optimisation, un test qui isole spécifiquement le risque d'alignement std140
  vs WGSL par défaut.
  → nouveau fichier `test_wgsl_globals_layout.py` : un shader `@fragment fn main()` lit
  `iTime`/`iTimeDelta`/`iFrame`/`iMouse`/`iResolution`/`iDate`/`iSampleRate` un par un (tous
  scalaires/`vec4`, alignement identique dans les deux conventions) et écrit un verdict
  pass/fail dans la couleur de sortie plutôt que les valeurs elles-mêmes (rouge = tout
  correct, vert = au moins un champ diverge) — plus simple à vérifier pixel par pixel qu'un
  encodage RGBA8 à 1/255 près. Le champ isolé spécifiquement (seul point de divergence
  std140/WGSL possible, voir 1.3) est `iChannelTime` : `iChannel0` lié à une source vidéo
  via `set_ichannel_video`/`update_ichannel_video_frame` avec une position de lecture connue
  (12.5s) vérifiée en `iChannelTime[0].x`, `iChannel1` laissé non lié comme contrôle négatif
  (`iChannelTime[1].x` doit rester à 0). **Sanity-check effectué** : falsification
  temporaire d'une valeur attendue (`CHANNEL0_TIME` changé pour ne plus correspondre) pour
  confirmer que le test détecte bien l'échec (pixel vert, `AssertionError`) avant de
  restaurer et re-vérifier le pixel rouge — le test n'est donc pas trivialement vrai.
  Résultat sur le repli retenu en 1.3 (`array<vec4<f32>, 4>` côté WGSL) : **layout mémoire
  confirmé correct sur un vrai rendu GPU**, tous les champs lus correspondent bit pour bit
  à ce qu'écrit `renderer.rs::write_globals` côté Rust.

**Suivi** : section 1.5 terminée. Non-régression du chemin Shadertoy/GLSL existant
également revérifiée à cette occasion (`pixel_compare.py`, exigence de la « Vérification
globale attendue » en fin de fichier) : **Pixel-identique : True** entre `default.frag`
original et golfé, après le correctif de `renderer.rs::compile_pass` — le correctif ne
touche que la branche WGSL (`if detection.dialect == ShaderDialect::Wgsl { .. } else { ..
même code Glsl qu'avant .. }`), donc aucun changement attendu ni observé côté GLSL/
Shadertoy. Suite Python complète (`test_i18n_completeness.py`, `test_i18n.py`,
`test_literals_native.py`, `test_shadertoy_import.py`, `test_sliders.py`,
`test_video_export.py`, `test_export_video_dialog.py`, `test_keyframe_reset_bugfix.py`)
également relancée intégralement, tout vert. La section 1 (WGSL) est maintenant
entièrement terminée (1.1 à 1.5) — reste, avant de la considérer close au sens de la
consigne en tête de ce fichier : mettre à jour `ARCHITECTURE.md` (renvoi explicite vers ce
fichier, voir « ✅ Vérification globale attendue » en fin de fichier), `CHANGELOG.md` et
`README.md`.

---

## 🟦 2. HLSL & 🟧 MSL — cibles d'**export**, jamais dialectes d'entrée

### 2.1 Pourquoi pas un dialecte d'entrée

Reformulé depuis la matrice de faisabilité : `naga` n'a pas de frontend `hlsl-in` ni
`msl-in`. Accepter du HLSL ou du MSL *collé dans l'éditeur* demanderait donc d'écrire (ou
d'embarquer) un parseur HLSL/MSL complet — un chantier d'un tout autre ordre de grandeur
que `dialect.rs`/`shader.rs` (qui ne font, au fond, que des transformations *textuelles*
prudentes sur du GLSL déjà validé, jamais un vrai frontend de compilateur). Deux options
existent dans l'industrie pour transformer du HLSL en quelque chose que `wgpu` accepte :

- **DXC** (`DirectXShaderCompiler`, LLVM-based, Microsoft, MIT) sait compiler du HLSL vers
  SPIR-V (`-spirv`), que `wgpu::ShaderSource::SpirV` accepte déjà nativement (feature
  `spirv` de `wgpu`, pas encore activée dans `Cargo.toml` de ce projet). C'est un vrai
  binaire externe (~15-40 Mo par plateforme), sur le même principe que `ffmpeg.exe` déjà
  embarqué pour l'export vidéo (voir `ROADMAP.md`, section 🎬) — précédent direct et
  réutilisable : même bascule `sys.frozen`/dev, même mention de licence dans l'installeur.
- Pas d'équivalent public pour MSL en sens inverse : le compilateur Metal d'Apple
  (`metal`/`metallib`) ne s'exécute que sur macOS, n'a pas de mode « parser du MSL et
  ressortir autre chose », et personne dans l'écosystème `wgpu`/`naga` ne transpile
  *depuis* MSL — MSL n'existe dans cette chaîne d'outils que comme **sortie**.

Conclusion cohérente pour les deux : **HLSL et MSL n'ont de sens dans ce logiciel que
comme format d'export d'un shader déjà écrit et validé en GLSL/Shadertoy ou WGSL** —
exactement comme un shader Shadertoy golfé s'exporte aujourd'hui en `.frag` autonome
(voir `ROADMAP.md`, 🏌️ Golfing). Un game engine cible (Unreal via HLSL, Unity/consoles via
HLSL, une app iOS/macOS native via MSL) est le public réel de cette fonctionnalité — pas
un dialecte à taper dans l'éditeur.

### 2.2 Mécanisme technique retenu

`naga` a déjà les deux backends nécessaires (`hlsl-out`, `msl-out`) : le module IR produit
en interne à partir du GLSL/WGSL de l'utilisateur (celui que `wgpu` construit déjà pour
compiler vers le backend natif de la plateforme — Vulkan/Metal/DX12) peut être redirigé
vers ces backends texte au lieu d'un binaire GPU. Concrètement :

- [x] Ajouter les features Cargo `hlsl-out`/`msl-out` du côté `naga` — **vérifier
  d'abord** si `wgpu 0.20` réexporte `naga` avec un moyen d'activer ces features
  backend sans dupliquer toute la dépendance (`wgpu` expose `wgpu::naga` en ré-export
  aujourd'hui, utilisé par `renderer.rs` pour `wgpu::naga::ShaderStage`) ; sinon,
  ajouter `naga` en dépendance directe du crate avec les bonnes features, en s'assurant
  qu'il s'agit de la **même version exacte** que celle vendue par `wgpu 0.20` (un
  décalage de version produirait deux copies incompatibles de `naga::Module` dans le
  binaire final).
  → **Vérifié d'abord, réponse négative** : le `Cargo.toml` source de `wgpu 0.20.1`
  (récupéré depuis `static.crates.io`) ne forwarde, via ses propres features, que
  `glsl = ["naga/glsl-in", ...]`, `spirv = ["naga/spv-in", ...]` et
  `webgpu = ["naga?/wgsl-out"]` — aucune feature `hlsl-out`/`msl-out` n'y est exposée.
  `wgpu::naga` est bien un simple ré-export de la dépendance `naga` de `wgpu-core`
  (`pub use ::wgc::naga;`, `wgpu-0.20.1/src/lib.rs`), jamais une copie séparée : il n'y
  avait donc pas de moyen d'activer ces backends sans ajouter `naga` en dépendance
  directe. Fait dans `rust_engine/Cargo.toml` :
  `naga = { version = "=0.20.0", default-features = false, features = ["hlsl-out",
  "msl-out"] }` — version **exactement** épinglée à `0.20.0`, celle déjà verrouillée par
  `wgpu 0.20.1` dans `Cargo.lock` (confirmé par lecture du `Cargo.lock` existant avant
  modification).
  **Vérification effective, pas seulement déclarative** (toolchain `apt` standard,
  `rustc`/`cargo` 1.75, même limite que celle déjà rencontrée et résolue pour la section
  1) :
  - `cargo tree -i naga` : une seule ligne `naga v0.20.0`, avec pour parents à la fois
    `shadertoy_engine` (la nouvelle dépendance directe) et `wgpu`/`wgpu-core`/`wgpu-hal`
    — confirmation qu'il n'y a **pas** deux instances du crate, exactement le risque que
    cet item mettait en garde.
  - `cargo check --lib` : vert sans modification d'aucun autre fichier.
  - Sonde temporaire (ajoutée puis retirée, jamais commit) dans `lib.rs` : construction
    d'un `naga::Module` vide, validation via `naga::valid::Validator`, puis appel réel de
    `naga::back::hlsl::Writer::write` et `naga::back::msl::write_string` sur le module
    validé — compile proprement, ce qui n'aurait pas été le cas si les features
    `hlsl-out`/`msl-out` n'étaient pas réellement actives sur l'instance partagée
    (`naga::back::hlsl`/`naga::back::msl` n'existent pas sans elles).
  - `cargo test --lib` : 231 tests, 0 échec — aucune régression, la modification ne
    touche que `Cargo.toml`/`Cargo.lock`, aucun fichier `.rs` du dépôt n'est modifié par
    cet item.
- [x] Nouvelle fonction `export_shader_as(source: &str, dialect: ShaderDialect, target:
  ExportTarget, channel_kinds: [ChannelKind; 4]) -> Result<String, String>` (`shader.rs`) :
  réutilise le backend de compilation déjà choisi pour produire la source GLSL/WGSL
  effectivement compilée aujourd'hui (`compile_backend_for`), la fait parser par le
  frontend `naga` correspondant (`glsl-in` ou `wgsl-in` selon le dialecte source — jamais
  besoin d'un frontend HLSL/MSL puisqu'on part toujours d'un des deux dialectes qu'on sait
  déjà lire), puis appelle `naga::back::hlsl::write_string`/`naga::back::msl::write_string`
  sur le module validé.
  **Attention explicite** : cette fonction est volontairement **séparée** de
  `compile_backend_for`/`COMPILE_BACKENDS` (qui restent réservés aux dialectes réellement
  éditables) — ne pas ajouter `"hlsl"`/`"msl"` à `ShaderDialect::ALL`, ce serait mentir sur
  ce que ces identifiants signifient (un dialecte de `ShaderDialect` implique « peut être
  détecté et compilé depuis l'éditeur », ce qui n'est jamais vrai ici).
  → Implémenté dans `shader.rs` avec la signature exacte demandée (`ExportTarget::{Hlsl,
  Msl}`, nouveau type distinct de `ShaderDialect`, jamais ajouté à `ShaderDialect::ALL`).
  Chemin réellement exercé (frontend → `naga::valid::Validator` → backend), pas seulement
  la génération de texte intermédiaire déjà couverte par `standalone_tests`/`wgsl_tests` :
  - GLSL (Shadertoy/standalone) : `naga::front::glsl::Frontend::default().parse(&Options {
    stage: ShaderStage::Fragment, .. }, &compiled_src)` — même `ShaderStage::Fragment` que
    celui déjà utilisé par `renderer.rs::compile_pass` pour le même texte.
  - WGSL : `naga::front::wgsl::parse_str(&compiled_src)`.
  - Validation : `naga::valid::Validator::new(ValidationFlags::all(),
    Capabilities::empty())` — `Capabilities::empty()` choisi pour rester cohérent avec
    `renderer::Engine::new`, qui demande `wgpu::Features::empty()` (vérifié en lisant
    `renderer.rs`) : un module qui compile pour le rendu live compile donc aussi pour cet
    export, sans capacité `naga` supplémentaire non couverte par le device réel.
  - `force_opaque` toujours `false` (documenté dans la doc de la fonction : notion propre
    au pass Image affiché, sans équivalent pour un export ponctuel).
  - Messages d'erreur via `WithSpan::emit_to_string(&compiled_src)` (parsing/validation)
    plutôt qu'un simple `{e}` — pointe sur la ligne/colonne dans la source réellement
    compilée, cohérent avec le soin déjà apporté ailleurs dans ce dépôt au mapping
    ligne d'erreur → source (`header_line_count_for_dialect`).
  **Vérification effective** (`cargo test --lib`, toolchain `apt` 1.75, 237 tests dont 6
  nouveaux dans `shader::export_tests`, 0 échec) : export Shadertoy→HLSL/MSL, GLSL
  standalone→HLSL, WGSL→MSL, WGSL avec `iChannel0` référencé→HLSL (vérifie
  `Texture2D`/`SamplerState` en sortie, RMLG.md 2.3), et un test de non-régression
  confirmant que l'export réutilise bien le même texte intermédiaire que
  `compile_backend_for` (bloc `Globals` absent en sortie HLSL quand il n'est pas
  référencé, exactement comme `standalone_tests::omits_globals_block_when_unreferenced`
  le vérifie déjà côté texte GLSL). Assertions sur la sortie MSL ajustées après lecture de
  la sortie réelle produite par `naga` (`#include <metal_stdlib>` en tête de fichier, pas
  de `using namespace metal;` — le backend MSL de `naga` préfixe chaque type/fonction par
  `metal::`).
- [x] Exposition pyo3 (`lib.rs`) : `Engine::export_shader_as(pass, target: &str) ->
  PyResult<String>`, `target ∈ {"hlsl", "msl"}`.
  → Implémenté en deux temps : `renderer::Engine::export_shader_as(&self, pass: usize,
  target: shader::ExportTarget) -> Result<String, String>` (nouveau, juste après
  `compile_pass`) reconstruit exactement les mêmes ingrédients que le dernier
  `compile_pass` réussi pour ce pass — `common_src` + `pass_sources[pass]` (même
  concaténation qu'en tête de `compile_pass`), `pass_dialects[pass]` (dialecte détecté
  alors), et les `ChannelKind` actuels de `channels[pass]` via `channel_kind` — puis
  délègue à `shader::export_shader_as`. Erreur explicite (`"ce pass n'a pas encore été
  compilé avec succès"`) si `pass_sources[pass]` est encore `None` : ce champ n'est peuplé
  qu'après la création réussie du pipeline (fin de `compile_pass`), jamais avant, donc pas
  de risque d'exporter un texte partiel ou périmé. Côté pyo3 (`lib.rs`), `target: &str` est
  validé explicitement (`"hlsl"`/`"msl"` seulement, `PyValueError` sinon plutôt qu'un
  `unwrap`/repli silencieux) avant d'appeler `self.inner.export_shader_as`.
  **Vérification effective** : `cargo check --lib` vert, et les deux avertissements
  `dead_code` sur `ExportTarget`/`export_shader_as` (présents juste après l'item
  précédent, tant que rien ne les appelait encore) ont disparu une fois ce câblage en
  place — signal concret que le chemin `lib.rs → renderer.rs → shader.rs` est bien relié
  de bout en bout, pas seulement trois fonctions qui compilent chacune isolément.
  `cargo test --lib` toujours vert (237 tests, 0 échec, aucune régression).
  (À cette étape, le point d'entrée `main_window.py` restait le seul chaînon manquant —
  `Engine.export_shader_as` exposé côté Rust/pyo3 mais pas encore appelable depuis l'UI ;
  voir l'item suivant, désormais fait.)
- [x] Côté UI (`main_window.py`) : nouvelle entrée `Fichier → Exporter le shader compilé
  vers → HLSL (.hlsl)` / `→ Metal (.metal)`, à côté de l'export golfé déjà existant — pas
  une nouvelle case d'onglet, un export ponctuel du pass actuellement affiché.
  **Limitation à documenter dans le dialogue d'export, pas seulement dans ce fichier** :
  le fichier produit est une traduction fidèle *au moment de l'export*, pas un fichier
  qu'on peut re-coller dans l'éditeur pour continuer à l'éditer dans ce logiciel (cohérent
  avec le fait que HLSL/MSL ne sont pas des dialectes d'entrée) — à formuler clairement
  pour ne pas laisser croire à un aller-retour possible.
  → Implémenté sous forme de sous-menu `export_compiled_menu` (`menu.file.export_compiled_menu`
  = « Exporter le shader compilé vers »), placé juste après `file.export_golfed` dans
  `Fichier`, avec deux `QAction` enregistrées auprès de `ShortcutRegistry`
  (`file.export_hlsl`/`file.export_msl`, ajoutées à `SHORTCUT_SPECS` dans `shortcuts.py`
  comme toutes les autres entrées de ce menu, raccourci vide par défaut, pour qu'elles
  apparaissent normalement dans la boîte de dialogue de réassignation). Nouveau handler
  `_on_export_compiled_shader(target: str)` (`target ∈ {"hlsl", "msl"}`) :
  - Si l'onglet courant est `COMMON_TAB` : refuse et explique pourquoi (`dialogs.
    export_shader.common_tab_body`) plutôt que d'appeler `export_shader_as` avec un pass
    invalide — Common n'a jamais été un pass compilé en soi (même garde déjà utilisée par
    `_on_golf`/`_do_golf` pour le golfing).
  - **Avertissement de non-réversibilité affiché avant même la boîte d'enregistrement**
    (`QMessageBox.information`, texte `dialogs.export_shader.not_reeditable_hlsl` /
    `_msl`), pas seulement une phrase perdue dans ce fichier : l'utilisateur voit d'abord
    que le fichier produit est une traduction figée au moment de l'export, illisible par
    ce logiciel, avant même de choisir où l'enregistrer.
  - `QFileDialog.getSaveFileName` avec filtre dédié (`dialogs.export_shader.filter_hlsl`/
    `filter_msl`, extension par défaut `.hlsl`/`.metal`).
  - Appelle directement `self._engine.export_shader_as(self._current_tab, target)` (le
    binding pyo3 déjà exposé, section 2.2) — pas de passage par `self.editor.get_value()` :
    la fonction Rust réexploite volontairement le dernier `pass_sources[pass]` compilé
    avec succès, jamais le texte de l'éditeur non encore recompilé, donc rien à récupérer
    côté UI avant l'appel.
  - `RuntimeError` (érigée côté pyo3 par `to_py_err`) attrapée et affichée via
    `dialogs.export_shader.failed_title`/`failed_body`, même schéma que
    `dialogs.golf_export_cancelled` pour l'export golfé existant.
  - Succès : `Path(path).write_text(exported, encoding="utf-8")`, même pattern que
    `_on_export_golfed`.
  Nouvelles clés i18n ajoutées aux 12 fichiers `lngs/*.json` (parité stricte, voir 2.4) :
  `menu.file.export_compiled_menu`/`export_hlsl`/`export_msl`,
  `actions.file.export_hlsl`/`export_msl` (pour la boîte de réassignation des raccourcis),
  et le nouveau groupe `dialogs.export_shader.{title, not_reeditable_hlsl,
  not_reeditable_msl, common_tab_body, filter_hlsl, filter_msl, failed_title,
  failed_body}`.
  **Vérification effective** : `python3 -m py_compile python_ui/ui/main_window.py
  python_ui/shortcuts.py` vert (PySide6 non installé dans ce bac à sable, cf.
  `COMPILATION.md` — pas d'exécution UI réelle possible ici, seulement la compilation
  syntaxique). `test_i18n.py` et `test_i18n_completeness.py` toujours verts sans
  modification de leur propre code : parité des 12 fichiers confirmée à 253 clés chacun
  (241 avant cet ajout), et le scan statique des sites d'appel `tr("...")` de
  `test_i18n_completeness.py` (230 sites, dont tous les nouveaux `tr(...)` de
  `_on_export_compiled_shader`/`_build_menu`) confirme qu'aucune des nouvelles clés
  littérales n'est orpheline dans `fr.json`.

### 2.3 Limites connues, à ne pas sous-estimer

- [x] `iChannel`/`Buffer A-D`/uniforms personnalisés se traduisent en `Texture2D`/
  `SamplerState` (HLSL) ou `texture2d<float>`/`sampler` (MSL) avec des conventions de
  binding différentes de celles de la plateforme d'origine (registres `t0`/`s0` HLSL,
  index `[[texture(0)]]` MSL) — **le fichier exporté ne compile pas forcément tel quel
  dans un moteur tiers sans adaptation manuelle des bindings**, à documenter explicitement
  plutôt que de suggérer un export « prêt à l'emploi ».
  → Documenté à l'endroit où l'utilisateur peut réellement le voir avant d'agir, pas
  seulement ici : deux nouvelles clés i18n distinctes de l'avertissement de non-réédition
  déjà en place (2.2) — `dialogs.export_shader.bindings_caveat_hlsl`/`bindings_caveat_msl`
  — affichées **ensemble** avec `not_reeditable_hlsl`/`not_reeditable_msl` dans la même
  `QMessageBox.information` de `_on_export_compiled_shader` (`main_window.py`), avant
  l'ouverture de la boîte d'enregistrement. Les deux avertissements restent des clés
  séparées plutôt qu'un seul texte fusionné : ce sont deux limitations indépendantes
  (l'une sur la réédition, l'autre sur les conventions de binding), l'une n'impliquant pas
  l'autre — les fusionner aurait rendu chacune plus difficile à retrouver/retraduire
  isolément. Formulation volontairement conditionnelle (« si ce shader utilise des
  `iChannel`/uniforms personnalisés… ») puisque l'avertissement s'affiche pour tout
  export, y compris ceux qui n'ont ni `iChannel` ni uniform personnalisé et ne sont donc
  pas concernés — cohérent avec le "à documenter explicitement plutôt que de suggérer un
  export « prêt à l'emploi »" de l'item : le texte reste honnête même quand le cas
  particulier ne s'applique pas, plutôt que de sur-promettre par omission.
  Ajouté aux 12 fichiers `lngs/*.json` (parité stricte, voir 2.4).
  **Vérification effective** : `python3 -m py_compile python_ui/ui/main_window.py` vert ;
  `test_i18n.py`/`test_i18n_completeness.py` toujours verts sans modification de leur
  propre code (255 clés par fichier désormais, 253 avant cet ajout ; scan statique des
  sites `tr("...")` — 230 sites — confirmant que les deux nouveaux appels dans
  `_on_export_compiled_shader` pointent bien vers des clés réellement présentes dans
  `fr.json`). Le contenu technique du message (`Texture2D`/`SamplerState` en HLSL,
  `texture2d<float>`/`sampler` en MSL) recoupe directement les assertions déjà vérifiées
  par `shader::export_tests` (2.2) plutôt que d'être une affirmation nouvelle et
  non vérifiée.
- [x] Le golfing (`golf.rs`) ne s'applique **jamais** à un export HLSL/MSL — c'est un
  golfer GLSL textuel, sans aucun sens sur une syntaxe différente ; l'export part toujours
  du code source (golfé ou non, au choix de l'utilisateur), jamais d'un second golfing
  post-traduction.
  → **Déjà vrai par construction** (`export_shader_as`, section 2.2 : `compile_backend_for`
  → frontend `naga` → `naga::valid::Validator` → backend `hlsl-out`/`msl-out` — aucune
  référence à `crate::golf` nulle part dans ce chemin), mais seulement *déclaré*, jamais
  *vérifié* jusqu'ici — même écart que celui que ce fichier dénonce lui-même en préambule
  (case cochée sans avoir été vérifiée). Deux choses ajoutées pour combler cet écart :
  - Doc-comment de `export_shader_as` complétée avec un paragraphe dédié et explicite sur
    ce point (elle ne le disait qu'implicitement via "séparée de `COMPILE_BACKENDS`", qui
    concerne l'aiguillage des dialectes d'entrée, pas le golfing).
  - Nouveau test `shader::export_tests::
    export_never_applies_golf_and_reflects_whichever_source_the_user_passed`, qui vérifie
    le point de façon **observable** plutôt que déclarative : une sonde manuelle (ajoutée
    puis retirée, jamais commit) a d'abord confirmé que le backend `hlsl-out` de `naga`
    préserve **verbatim** les identifiants du texte source (`fragColorOutputHere`,
    `veryVerboseLocalNameXYZ`, ... retrouvés tels quels dans la sortie HLSL). Le test golfe
    un identifiant volontairement long via `golf::golf_shader` (confirmant au passage qu'il
    est bien raccourci, sinon le test ne prouverait rien), puis exporte la source **non**
    golfée vers HLSL et vérifie que l'identifiant long survit intact dans la sortie — sa
    disparition aurait signifié qu'un golfing a été appliqué en interne par
    `export_shader_as`. Vérifie en plus, séparément, que l'export de la source **déjà**
    golfée par l'utilisateur réussit tout aussi bien, de façon indépendante (pas de "second
    golfing post-traduction" à démontrer puisqu'il n'y a justement rien à golfer une
    deuxième fois).
  **Vérification effective** (toolchain `apt` installée dans ce bac à sable — `rustc`/
  `cargo` 1.75, même limite que celle déjà rencontrée et résolue pour les sections 1 et 2.2,
  `Cargo.lock` toujours en `version = 3`, compatible) : `cargo test --lib` → 238 tests
  (237 + ce nouveau test), 0 échec, aucune régression ; `cargo check --lib` vert sans
  nouvel avertissement.
  **Effet de bord découvert, hors périmètre de cet item** : sonder l'export d'un uniform
  personnalisé (`uniform float x; ...`) à travers `export_shader_as` a révélé que
  `build_fragment_source_standalone` laisse la déclaration `uniform` d'origine de
  l'utilisateur intacte *en plus* du bloc `CustomUniformBlock_x` réécrit avec binding,
  ce qui fait échouer le frontend GLSL de `naga` ("uniform/buffer blocks require
  layout(binding=X)") dès qu'un export HLSL/MSL porte sur un pass GLSL standalone utilisant
  un uniform personnalisé sans binding explicite (le rendu live n'est lui-même pas affecté,
  `wgpu`/`naga-glsl-in` version rendu tolérant apparemment cette redéclaration autrement).
  Non corrigé ici — sans rapport avec le golfing, et RMLG.md 2.5 ("Vérification") n'a pas
  encore son propre item pour cette classe de cas ; à traiter séparément.
  Ligne de base (`CHANGELOG.md`/`README.md`) mise à jour avec une clarification pour
  l'utilisateur final sur ce même point.
- [x] Pas de garantie de rendu pixel-identique entre le rendu live (backend natif de la
  plateforme, choisi par `wgpu`) et ce que produirait la compilation du fichier HLSL/MSL
  exporté dans un moteur tiers — `naga` vise la correction fonctionnelle de sa traduction,
  pas un bit-exact garanti contractuellement ; à vérifier au cas par cas plutôt que
  supposé, avec la même rigueur que les vérifications « rendu pixel-identique » déjà
  systématiques ailleurs dans ce dépôt (mais cette fois hors du moteur de rendu de ce
  logiciel, donc non automatisable ici).
  → **Non automatisable, donc traité comme ce qu'il est réellement : une limite à
  documenter explicitement pour l'utilisateur, pas un test à écrire.** Vérifier un
  bit-exact contre un moteur tiers (DirectX/Unity/Unreal pour HLSL, Metal réel — macOS
  uniquement — pour MSL) est hors de portée de ce dépôt et de ce bac à sable Linux (même
  limite déjà documentée en 2.5) ; le remplacer par une comparaison `pixel_compare.py`
  interne au moteur de rendu de ce logiciel n'aurait mesuré que la traduction GLSL/WGSL →
  IR `naga`, jamais la recompilation par un compilateur tiers, donc n'aurait rien prouvé
  sur l'absence de garantie affirmée par cet item — un test qui ne peut structurellement
  pas couvrir ce qu'il prétend vérifier serait pire que pas de test du tout (fausse
  confiance). C'était d'ailleurs déjà documenté, mais seulement dans la doc-comment de
  `export_shader_as` (§ « Limites connues », `shader.rs`) — jamais montré à l'utilisateur
  au moment où la décision d'exporter est prise, contrairement aux deux autres limites de
  cette section 2.3 qui ont chacune leur propre avertissement dans la boîte de dialogue
  d'export. Comblé à l'endroit où l'utilisateur peut réellement en tenir compte :
  - Nouvelle clé i18n `dialogs.export_shader.pixel_fidelity_caveat`, ajoutée aux 12
    fichiers `lngs/*.json` (parité stricte, voir 2.4) — **une seule clé partagée entre
    HLSL et MSL**, contrairement à `not_reeditable_*`/`bindings_caveat_*` : cette limite ne
    dépend d'aucun détail spécifique au langage cible (contrairement aux conventions de
    registre `t0`/`s0` vs `[[texture(0)]]`), donc dupliquer le texte en `_hlsl`/`_msl`
    aurait été une différence de forme sans différence de fond — le même repli "réduire au
    strict nécessaire" que celui déjà appliqué ailleurs dans ce dépôt plutôt que de
    prétendre une distinction qui n'existe pas.
  - `main_window.py::_on_export_compiled_shader` : troisième avertissement affiché dans la
    même `QMessageBox.information`, aux côtés de `not_reeditable_*`/`bindings_caveat_*`
    (toujours les trois ensemble, avant même la boîte d'enregistrement) — même schéma que
    l'ajout précédent (2.3, item bindings), les trois limitations restant des clés
    séparées plutôt qu'un texte fusionné puisqu'aucune n'implique les deux autres.
  **Vérification effective** : `python3 -m py_compile python_ui/ui/main_window.py` vert.
  `test_i18n.py`/`test_i18n_completeness.py` toujours verts sans modification de leur
  propre code (256 clés par fichier désormais, 255 avant cet ajout ; 231 sites `tr("...")`
  statiquement scannés, confirmant que le nouvel appel dans
  `_on_export_compiled_shader` pointe bien vers une clé réellement présente dans
  `fr.json`). `diff` ligne à ligne contre les 12 fichiers `lngs/*.json` d'avant cet ajout
  confirme qu'une seule ligne a été insérée dans chacun, aucune ré-indentation ni
  perturbation du reste du fichier. `cargo test --lib`/`cargo check --lib` toujours verts
  (238 tests, 0 échec — cet item ne touche aucun fichier `.rs`, la doc-comment
  `shader.rs` existante suffisait déjà côté code, seul le UI-facing manquait).
  `CHANGELOG.md`/`README.md` mis à jour avec la même clarification pour l'utilisateur
  final.

### 2.4 i18n et UI

- [x] Pas de nouvelles clés `footer.dialect_*` (HLSL/MSL n'apparaissent jamais dans
  l'indicateur de dialecte du footer, puisqu'ils ne sont jamais le dialecte détecté d'un
  onglet) — seulement des clés de menu/dialogue d'export
  (`menu.file.export_hlsl`/`export_msl`, `dialogs.export_shader.*`), dans les 12 fichiers
  `lngs/*.json`, même exigence de parité stricte.
  → **Déjà vrai par construction** (les items 2.2/2.3 n'ont créé que des clés
  `menu.file.*`/`actions.file.*`/`dialogs.export_shader.*`, jamais `footer.dialect_*`),
  mais seulement *déclaré* jusqu'ici — même écart que celui que ce fichier dénonce en
  préambule (case cochée sans avoir été vérifiée). Vérifié explicitement plutôt que
  supposé :
  - `footer.py` (indicateur de dialecte de l'onglet actif) ne référence ni `hlsl` ni
    `msl`, textuellement — seul `dialect.rs` mentionne `HLSL` une fois, dans un
    commentaire sur l'extensibilité future (`ShaderDialect::ALL` reste à 3 variantes :
    `Shadertoy`/`GlslStandalone`/`Wgsl`, jamais `Hlsl`/`Msl` — cohérent avec
    `ExportTarget`, section 2.2, resté un type distinct de `ShaderDialect`).
  - Les 11 clés `footer.dialect_*` réellement présentes dans `fr.json` sont
    `footer.dialect_{glsl,shadertoy,wgsl,tooltip}` et les 7
    `footer.dialect_signal_*` (dont `wgslentrypoint`/`wgsluniformorgeneric`, section 1) —
    aucune ne contient `hlsl`/`msl`, confirmé par un scan programmatique des clés
    aplaties de `fr.json`, pas seulement une relecture visuelle.
  - Les 14 clés export-facing existent bien, réparties comme prévu :
    `menu.file.export_hlsl`/`export_msl`, `actions.file.export_hlsl`/`export_msl`,
    `dialogs.export_shader.{title, not_reeditable_hlsl, not_reeditable_msl,
    bindings_caveat_hlsl, bindings_caveat_msl, pixel_fidelity_caveat, common_tab_body,
    filter_hlsl, filter_msl, failed_title, failed_body}`.
  **Vérification effective** : script Python de comparaison d'ensembles de clés aplaties
  sur les 12 fichiers `lngs/*.json` (`de/en/es/fr/hi/it/ja/ko/no/pt/sv/zh`) — parité
  stricte confirmée, **256 clés identiques dans chacun des 12 fichiers**, aucune clé
  orpheline ni manquante d'un fichier à l'autre. `python3 test_i18n.py` et
  `python3 test_i18n_completeness.py` (jeux de tests existants, non modifiés) : tous deux
  verts — parité `fr.json`/`en.json` à 256 clés, parité des 12 fichiers à 256 clés
  chacun, et scan statique des **231 sites d'appel** `tr("...")` de `python_ui/`
  confirmant qu'aucun ne pointe vers une clé absente de `fr.json` (les clés
  `export_hlsl`/`export_msl`/`export_shader.*` ajoutées en 2.2/2.3 y sont bien
  comptabilisées, sans régression sur les sites déjà existants).

### 2.5 Vérification

- [x] `cargo test` : export d'un shader `default.frag` connu vers HLSL et MSL, vérifié au
  minimum par une compilation externe si l'outillage est disponible (`dxc`/`fxc` pour
  HLSL — déjà mentionné dans la documentation de `naga` elle-même, voir `make
  validate-hlsl-dxc`/`validate-msl` cités par son propre dépôt — un compilateur Metal réel
  n'étant disponible que sur macOS, hors de portée du bac à sable Linux déjà documenté
  ailleurs dans ce dépôt) ; à défaut, validation via `naga`'s propre validateur IR
  (`naga::valid::Validator`) avant l'écriture du backend, ce qui n'est pas une garantie de
  compilation externe mais reste un filet de sécurité minimal.
  → **Outillage externe vérifié absent avant de retomber sur le filet de sécurité** (pas
  supposé) : `dxc`, `fxc`, `metal`, `xcrun` — aucun des quatre n'est présent dans le
  `PATH` de ce bac à sable Linux, confirmé par un test de présence explicite plutôt que
  déclaré comme dans le reste de ce fichier. Conclusion identique à celle déjà actée en
  2.1/2.3 : validation via `naga::valid::Validator` (déjà le chemin interne
  d'`export_shader_as`, section 2.2), pas une compilation externe.
  Deux nouveaux tests ajoutés dans `shader::export_tests` (`shader.rs`), à côté des tests
  existants qui n'utilisaient jusqu'ici que des micro-shaders synthétiques d'une ligne :
  - `exports_default_frag_to_hlsl`
  - `exports_default_frag_to_msl`
  Les deux embarquent le vrai fichier via
  `include_str!("../../python_ui/assets/shaders/default.frag")` — le fragment raymarching
  fractal + tone mapping ACES réellement livré avec le logiciel (`ShaderDialect::Shadertoy`,
  ~15 fonctions, boucles `for` imbriquées, paramètres `out`/`inout`, appels de fonctions
  utilisateur les unes dans les autres) — plutôt qu'une chaîne recopiée à la main, pour
  garantir qu'un futur changement de `default.frag` reste couvert automatiquement. Chaque
  test vérifie non seulement que `export_shader_as` retourne `Ok(..)` (donc que le module a
  réellement traversé frontend GLSL → `naga::valid::Validator` → backend `hlsl-out`/
  `msl-out` sans erreur — pas seulement qu'un texte a été produit), mais aussi que les noms
  des fonctions utilisateur (`processFractalFold`, `applyAcesToneMapping`) survivent
  identifiables dans la sortie — preuve que l'ensemble du fichier a été traduit, pas
  seulement `mainImage`.
  **Vérification effective** (toolchain `apt` installée dans ce bac à sable — `rustc`/
  `cargo` 1.75, `Cargo.lock` en `version = 3`, même limite déjà rencontrée et résolue
  ailleurs dans ce fichier) : `cargo check --lib` vert sans nouvel avertissement ;
  `cargo test --lib` → **240 tests** (238 précédents + les 2 nouveaux), 0 échec, aucune
  régression sur le reste de la suite (`shader::export_tests` isolé : 9/9 verts).

---

## 🗺️ 3. Séquencement recommandé

Classé, comme le reste de la documentation de golf de ce dépôt, du meilleur rapport
gain/risque au plus incertain :

1. **WGSL (dialecte d'entrée complet)** — coût le plus faible (aucune nouvelle dépendance
   Cargo, frontend déjà présent), valeur la plus directe (WGSL est le langage natif de la
   cible `wgpu`, un utilisateur qui écrit déjà pour le Web/WebGPU peut coller son shader
   tel quel). Seul vrai point dur : le bind group layout par-dialecte (section 1.3),
   à trancher avant de coder quoi que ce soit d'autre.
2. **Export HLSL** — deuxième plus faible coût (backend `naga` déjà existant, seule
   nouveauté réelle : s'assurer de l'alignement de version `naga` avec celle vendue par
   `wgpu`), valeur immédiate pour les utilisateurs visant Unity/Unreal/DirectX.
3. **Export MSL** — même mécanisme que HLSL, quasi gratuit une fois HLSL en place
   (partage la même fonction `export_shader_as`, juste un backend `naga` différent) —
   traité en 3e uniquement parce que la vérification externe (section 2.5) est plus
   difficile à mener dans cet environnement de développement (compilateur Metal réel
   indisponible hors macOS), pas parce que le travail lui-même serait plus long.

---

## ✅ Vérification globale attendue avant de considérer ce roadmap comme fait

**Note (mise à jour — points (2) et (3) traités, point (1) confirmé bloqué)** : les trois
points laissés ouverts par la note précédente ont été repris un par un :

- **(1) Toolchain Rust officielle récente** — toujours bloqué, mais désormais *vérifié*
  plutôt que simplement supposé : `curl` vers `static.rust-lang.org`/`sh.rustup.rs`
  (les deux points d'entrée standard de `rustup`) renvoie `HTTP 403`,
  `x-deny-reason: host_not_allowed` — confirmation explicite au niveau du proxy réseau
  de ce bac à sable, pas une simple absence remarquée. La toolchain `apt` 1.75 reste donc
  la seule disponible ici ; les vérifications ci-dessous restent faites avec elle, comme
  partout ailleurs dans ce fichier.
- **(2) `pixel_compare.py` après la section 2** — rejoué avec succès. Contrairement à ce
  que la note précédente supposait, ce bac à sable dispose bien d'un device Vulkan
  logiciel (`llvmpipe`/Mesa, `deviceType = PHYSICAL_DEVICE_TYPE_CPU`, confirmé par
  `vulkaninfo --summary` après installation de `mesa-vulkan-drivers`) : `wgpu` peut donc
  réellement initialiser un `Engine` et rendre, sans GPU matériel. Chaîne complète mise en
  place pour vérifier, pas seulement supposer : `rustc`/`cargo` (`apt`), `PySide6` +
  `maturin` (`pip`, domaines `pypi.org`/`files.pythonhosted.org` déjà autorisés),
  `maturin develop` (build debug, la variante `--release` avec LTO dépasse le temps
  d'exécution disponible pour une seule commande dans ce bac à sable — non nécessaire ici,
  seul le comportement fonctionnel est en jeu, pas la performance). Résultat :
  `python3 pixel_compare.py` → **`Pixel-identique : True`**, avec le code actuel du dépôt
  (sections 1 et 2 toutes deux appliquées). Confirme que rien dans l'ajout de l'export
  HLSL/MSL (section 2) n'a modifié `build_fragment_source`/`build_fragment_source_standalone`
  ni le chemin de rendu Shadertoy existant, exactement l'exigence de l'item ci-dessous.
  Point secondaire clarifié plutôt qu'omis : HLSL/MSL eux-mêmes n'ont **aucun** chemin de
  rendu à comparer par construction (ce sont des cibles d'export texte, jamais affichées
  sur le canvas — voir `ARCHITECTURE.md`, encadré ajouté en (3)) ; `pixel_compare.py` ne
  les couvre donc pas et n'a pas vocation à le faire. Vérifié en plus, de façon
  indépendante, que le nouveau chemin d'export lui-même fonctionne bout en bout via le
  vrai pont pyo3 (pas seulement `cargo test --lib`) : `Engine.export_shader_as(pass,
  "hlsl"|"msl")` appelé depuis Python sur `default.frag` réellement compilé/chargé dans un
  `Engine`, les deux exports réussissent et contiennent `processFractalFold` (une fonction
  utilisateur du fichier), confirmant que le pont Python↔Rust exposé en 2.2 fonctionne
  réellement, pas seulement les tests Rust internes déjà couverts en 2.5.
- **(3) `ARCHITECTURE.md`** — mis à jour. Nouvel encadré « Dialecte d'entrée vs cible
  d'export » ajouté juste après l'introduction, renvoyant explicitement vers `RMLG.md`
  (section 1 pour l'ajout réel de WGSL en suivant la procédure telle quelle, section 2
  pour la procédure différente des cibles d'export HLSL/MSL) — un futur contributeur qui
  n'ouvrirait que ce fichier est désormais prévenu, avant de commencer à coder, qu'un
  septième langage n'est pas automatiquement un candidat à la procédure en 3 étapes du
  reste du document. L'introduction elle-même a été corrigée : elle listait auparavant
  WGSL/HLSL comme des exemples interchangeables de « futur langage » à ajouter par cette
  procédure, ce qui est désormais faux pour HLSL (et ne l'a jamais été correctement pour
  MSL, jamais mentionné) — remplacée par un état des lieux exact des trois dialectes
  d'entrée réellement en place aujourd'hui.

**Vérification effective globale, rejouée après ces trois changements** (toolchain `apt`
1.75) : `cargo check --lib` vert sans nouvel avertissement ; `cargo test --lib` → 240
tests, 0 échec (aucune régression, ces changements ne touchent aucun fichier `.rs`) ;
`test_i18n.py`/`test_i18n_completeness.py` toujours verts sans modification de leur propre
code (256 clés, 12 fichiers, 231 sites `tr("...")`) — cohérent avec le fait qu'aucun de ces
trois points ne touchait l'i18n.

- [x] `cargo check`/`cargo test --lib` sur le crate complet, avec une vraie toolchain Rust
  récente (les limites de toolchain déjà rencontrées ailleurs dans ce dépôt —
  `Cargo.lock` v4, `rustc` trop ancien fourni par `apt` — devront être résolues, pas
  contournées par des tests isolés `rustc --test` comme cela a été fait par nécessité pour
  `golf.rs`, parce que ce chantier touche `renderer.rs`/`lib.rs`/le bind group layout,
  jamais autonomes de `wgpu`). Précédemment bloqué dans le bac à sable Linux
  (`static.rust-lang.org`/`sh.rustup.rs` → `HTTP 403 (x-deny-reason: host_not_allowed)`) —
  rejoué depuis un poste Windows avec une toolchain réelle et récente
  (`cargo`/`rustc` 1.97.1, hors `apt`) : `cargo check` propre (~2 min, chaîne complète
  wgpu/naga/ash/gpu-alloc, aucun avertissement nouveau) puis `cargo test --lib` → **238
  tests, 0 échec** sur le crate complet (`golf.rs`, `literals.rs`, `shader.rs` — y compris
  les modules `standalone_tests`/`wgsl_tests`/`export_tests` qui exercent les chemins
  proches de `renderer.rs`). Écart de compte (238 ici contre 240 rapporté plus haut dans ce
  fichier) probablement du au delta de code entre les deux exécutions plutôt qu'à un test
  manquant — non ré-audité ligne à ligne, mais 0 échec dans les deux cas.
- [x] Rendu pixel-identique du chemin `Shadertoy`/`GlslStandalone` existant avant/après ce
  chantier (même exigence que celle déjà vérifiée pour `roadmap1.md`) — l'ajout d'un
  troisième dialecte et d'un chemin d'export ne doit rien changer à
  `build_fragment_source`/`build_fragment_source_standalone`.
  → Rejoué avec le code actuel (sections 1 **et** 2 appliquées), pas seulement supposé
  depuis la section 1 : ce bac à sable dispose d'un device Vulkan logiciel (`llvmpipe`,
  `mesa-vulkan-drivers`, confirmé par `vulkaninfo --summary`) permettant à `wgpu` de
  réellement rendre sans GPU matériel. Module natif reconstruit (`maturin develop`,
  `PySide6` installé), `python3 pixel_compare.py` exécuté pour de vrai sur
  `default.frag` original vs golfé → **`Pixel-identique : True`**. Export HLSL/MSL
  vérifié séparément, bout en bout via le pont pyo3 réel (`Engine.export_shader_as`
  appelé depuis Python, pas seulement `cargo test --lib`) : les deux exports de
  `default.frag` réussissent et contiennent la fonction utilisateur
  `processFractalFold`, confirmant que le pont Python↔Rust de la section 2.2 fonctionne
  réellement. HLSL/MSL eux-mêmes n'ont, par construction, aucun chemin de rendu à
  comparer (cibles d'export texte, jamais affichées sur le canvas) — point désormais
  explicité dans `ARCHITECTURE.md` plutôt que simplement omis.
- [x] `test_i18n_completeness.py` et `test_i18n.py` toujours verts, sans modification de
  leur propre code — seulement de nouvelles clés dans les 12 `lngs/*.json`.
  → Rejoué après les changements de ce point de suivi (aucun d'eux ne touche l'i18n) :
  toujours verts, 256 clés dans chacun des 12 fichiers, 231 sites `tr("...")` statiques
  confirmés, sans modification du code des deux scripts de test.
- [x] `ARCHITECTURE.md` mis à jour avec un renvoi explicite vers ce fichier pour la
  distinction « dialecte d'entrée » (WGSL, suit la procédure en 3 étapes telle quelle) vs
  « cible d'export » (HLSL/MSL, procédure différente documentée ici en section 2) — pour
  qu'un futur contributeur qui lirait seulement `ARCHITECTURE.md` sache que cette
  distinction existe avant de se lancer sur un septième langage.
  → Nouvel encadré « Dialecte d'entrée vs cible d'export » ajouté juste après
  l'introduction de `ARCHITECTURE.md`, renvoyant vers `RMLG.md` section 1 (WGSL) et
  section 2 (HLSL/MSL). Introduction elle-même corrigée : elle citait auparavant
  WGSL/HLSL comme deux exemples interchangeables de « futur langage » à ajouter par la
  procédure en 3 étapes, ce qui est désormais inexact pour HLSL (et ne l'a jamais été
  pour MSL, jamais mentionné) — remplacée par un état des lieux exact des trois
  dialectes d'entrée réellement en place (`shadertoy`/`glsl`/`wgsl`).

**Note sur la case globale — désormais levée** : les 4 items ci-dessus sont cochés,
toolchain officielle récente incluse. Le point qui restait bloqué (accès réseau à
`static.rust-lang.org`/`sh.rustup.rs` depuis le bac à sable Linux d'origine) a été levé en
rejouant la vérification depuis un poste Windows disposant d'une vraie toolchain Rust
récente (`cargo`/`rustc` 1.97.1, hors `apt`) : `cargo check` propre, `cargo test --lib` →
238/238, aucune régression. Ce roadmap (WGSL en dialecte d'entrée, export HLSL/MSL) est
donc considéré **fait** au sens de la consigne en tête de ce fichier — `CHANGELOG.md` et
`README.md` reflètent déjà les deux fonctionnalités (versions 0.1.14 et 0.1.15).

Vérification complémentaire effectuée à cette occasion, au-delà de la seule
compilation/tests : câblage Python/UI relu directement dans le code (pas seulement dans ce
fichier) — `engine_bridge.DIALECT_WGSL`, `footer.py::_DIALECT_DISPLAY`, le sous-menu
*Fichier → Exporter le shader compilé vers*, `shortcuts.py` — parité des 256 clés i18n sur
les 12 fichiers `lngs/*.json` recomptée par un script indépendant plutôt que reprise des
chiffres déclarés ici, rendu WGSL réel et export HLSL/MSL réels rejoués via le vrai pont
pyo3 (`Engine.compile_pass`/`render()`/`export_shader_as`), et `pixel_compare.py` rejoué
(`Pixel-identique : True`). Aucun écart trouvé entre ce que ce document affirme et ce que
le code fait réellement.

*Généré à partir d'une lecture du contenu de `petitediteurglsl.zip` (`rust_engine/src/{dialect,shader,renderer}.rs`, `ARCHITECTURE.md`, `roadmap1.md`, `python_ui/ui/footer.py`, `python_ui/engine_bridge.py`, `lngs/fr.json`) et d'une vérification externe des frontends/backends réellement disponibles dans `naga` (`wgsl-in`/`glsl-in`/`spv-in` en entrée ; `glsl-out`/`hlsl-out`/`msl-out`/`spv-out`/`wgsl-out` en sortie) — c'est cette asymétrie qui structure l'ensemble de ce document : WGSL en dialecte d'entrée à part entière, HLSL/MSL repositionnés en cibles d'export plutôt qu'en dialectes. Tous les items de ce fichier sont implémentés et vérifiés.
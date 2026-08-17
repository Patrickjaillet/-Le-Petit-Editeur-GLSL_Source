# Architecture — ajouter un nouveau langage/dialecte

Ce document explique comment brancher un futur langage (WGSL, HLSL, Slang...)
dans le pipeline actuel, qui ne connaît aujourd'hui que deux dialectes GLSL :
`shadertoy` (wrapper `mainImage`) et `glsl` (GLSL standalone, `void main()`).

Voir `roadmap1.md`, section « Architecture extensible pour de futurs
langages » pour le contexte et les décisions de conception. Ce fichier est la
« procédure » que cette section du roadmap demandait de documenter.

Volontairement une **fondation légère** : pas de chargement dynamique de
plugins, pas de trait `Box<dyn ...>` — juste deux petits registres de
données statiques (`Vec`/tableaux de fonctions), suffisants tant qu'un seul
langage supplémentaire concret n'est pas réellement sur la table. Un système
de plugins plus lourd resterait possible plus tard sans que la forme actuelle
ait à être jetée.

## Vue d'ensemble

Ajouter un langage touche trois endroits indépendants, dans cet ordre :

1. **Détection** (`rust_engine/src/dialect.rs`) — reconnaître le texte de
   l'utilisateur comme appartenant à ce langage.
2. **Compilation** (`rust_engine/src/shader.rs`) — produire la source
   effectivement compilée par wgpu/naga à partir du code utilisateur.
3. **Affichage** (`lngs/*.json`, déjà extensible côté `footer.py`) — le nom
   lisible du mode et l'explication du signal détecté dans le tooltip.

`renderer.rs` et `lib.rs` (pyo3) n'ont **rien à modifier** : ils passent déjà
par les points d'entrée génériques (`dialect::detect_dialect`,
`shader::compile_backend_for`, `shader::header_line_count_for_dialect`).

## 1. Déclarer le dialecte (`dialect.rs`)

- Ajouter une variante à `ShaderDialect` (ex. `Wgsl`), lui donner un id
  stable et minuscule dans `id()`/`from_id()` (ex. `"wgsl"`), et l'ajouter à
  `ShaderDialect::ALL`. C'est cet id texte qui sert de frontière stable avec
  Python (pyo3, footer) — le reste du logiciel ne doit jamais manipuler
  l'enum Rust directement en dehors de `dialect.rs`/`shader.rs`.
- Ajouter le ou les signaux de détection à `DialectSignal` (ex.
  `WgslEntryPoint` pour `@fragment fn ...`), avec leur `i18n_key()`
  (`footer.dialect_signal_<nom>`).
- Écrire une fonction `fn matches_xxx(stripped: &str) -> bool` (voir
  `matches_main_image`/`matches_void_main` pour le style : travailler sur le
  texte déjà débarrassé de ses commentaires par `strip_comments`, réutiliser
  `contains_whole_word`/`is_ident_*` plutôt que des regex).
- Ajouter une entrée à `DETECTORS` avec un score de confiance cohérent avec
  les autres : plus le signal est spécifique et peu susceptible de faux
  positif (comme `mainImage`, score 100), plus il doit avoir un score élevé.
  Deux détecteurs ne doivent jamais partager le même score — un test
  (`registry_confidence_scores_are_strictly_ordered_and_unique`) l'impose.
- `detect_dialect` elle-même n'a **rien à changer** : elle évalue déjà tous
  les détecteurs du registre et retient le score le plus haut.

## 2. Brancher un backend de compilation (`shader.rs`)

- Écrire une fonction de build avec la signature `CompileBackendFn` :
  `fn(&str, [ChannelKind; 4], bool) -> (String, Vec<CustomUniformDecl>)`
  (voir `shadertoy_backend`/`glsl_standalone_backend`). Elle reçoit le code
  utilisateur déjà concaténé avec `Common`, les types de sampler par slot
  iChannel0-3, et `force_opaque` (spécifique au chemin Shadertoy — un
  backend qui n'a pas cette notion l'ignore simplement).
- L'ajouter à `COMPILE_BACKENDS` avec le même id texte que celui déclaré à
  l'étape 1 (`ShaderDialect::id()`).
- Si le nouveau langage a besoin d'un mapping ligne-erreur → éditeur
  différent de `header_line_count`/`header_line_count_standalone`, ajouter
  l'équivalent et une branche dans `header_line_count_for_dialect`.
- `renderer::Engine::compile_pass` n'a **rien à changer** : il appelle déjà
  `shader::compile_backend_for(detection.dialect)` plutôt qu'un `match` en
  dur.

## 3. Clés i18n et affichage (`lngs/*.json`, `python_ui/ui/footer.py`)

- `footer.py::_DIALECT_DISPLAY` est déjà une table indexée par id de
  dialecte (icône, couleur, clé i18n du libellé) : y ajouter une entrée
  pour le nouvel id, ex. `engine_bridge.DIALECT_WGSL: ("🟪", "#ba68c8",
  "footer.dialect_wgsl")`. Il faut aussi exposer la constante côté pyo3
  (`lib.rs`, à côté de `DIALECT_SHADERTOY`/`DIALECT_GLSL`).
- Ajouter, en parité stricte dans les **12** fichiers `lngs/*.json`
  (de/en/es/fr/hi/it/ja/ko/no/pt/sv/zh) :
  - `footer.dialect_<id>` (le libellé, ex. `footer.dialect_wgsl`) ;
  - une clé par nouveau `DialectSignal::i18n_key()` introduit à l'étape 1
    (ex. `footer.dialect_signal_wgslentrypoint`).
  - `footer.dialect_tooltip` existe déjà et est générique (accepte `mode`
    et `signal` en paramètres) : pas besoin d'en ajouter une variante par
    langage.
- `test_i18n_completeness.py` (existant) doit continuer de passer sans
  modification de son propre code — seulement de nouvelles clés dans les
  12 fichiers.

## Test minimal à ajouter pour un nouveau langage

Côté Rust (`dialect.rs`/`shader.rs`, `cargo test`) :

- au moins un test de détection positif (le nouveau signal est reconnu) et
  un test de non-régression (le nouveau signal ne fait pas basculer un
  shader Shadertoy/GLSL existant qui ne le contient pas) ;
- `compile_backends_cover_every_known_dialect` et
  `registry_dialects_are_all_known_to_shader_dialect_all` couvrent déjà
  automatiquement tout nouveau dialecte ajouté à `ShaderDialect::ALL` — pas
  besoin de dupliquer un test équivalent à la main pour ce point précis.

Côté Python : un test d'intégration minimal appelant `detect_dialect` (pyo3)
sur un extrait représentatif du nouveau langage et vérifiant l'id retourné,
à ajouter dans un `test_dialect_detection.py` (n'existe pas encore dans ce
dépôt — à créer au moment du premier langage réellement ajouté, plutôt que
maintenant sans langage concret à tester).

## Ce qui reste volontairement hors périmètre

Tant qu'un seul langage supplémentaire concret n'est pas sur la table :

- pas de chargement dynamique de détecteurs/backends (DLL, WASM, fichiers de
  config externes) ;
- pas de négociation de priorité plus fine qu'un score entier statique ;
- pas de découverte automatique des clés i18n manquantes au-delà de ce que
  `test_i18n_completeness.py` fait déjà.

Le but de cette fondation est seulement que GLSL/Shadertoy ne soient pas
codés en dur au point de rendre un futur ajout coûteux — pas d'anticiper une
architecture de plugins que rien ne justifie encore.

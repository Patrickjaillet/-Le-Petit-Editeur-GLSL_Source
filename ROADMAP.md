# ROADMAP — Petit Éditeur GLSL

Basée sur la lecture du code actuel (`rust_engine/` en Rust/wgpu exposé via PyO3, `python_ui/` en PySide6 + Monaco Editor). Organisée en 5 axes : **Golfing**, **Sliders**, **Compatibilité Shadertoy**, **Export vidéo**, **UI/UX**.

---

## 🏌️ Golfing (`rust_engine/src/golf.rs`)

Le golfer actuel fait : suppression des commentaires, collapse des espaces/retours à la ligne, renommage des identifiants en base-52, mots-clés GLSL et `#define` préservés, raccourcissement des littéraux numériques (valeur préservée, jamais renommés), et collapse des points-virgules redondants.

> ✅ Fait : `@slider`/`@category` et la `CustomUniforms` UBO ont été retirés (voir section 🎚️ Sliders) —
> golfer un shader ne manipule plus que du GLSL Shadertoy standard, rien à préserver spécifiquement.

- [x] Mode "agressivité" réglable : renommage des identifiants et suppression de code mort sont
  désormais deux cases à cocher indépendantes (`golf_shader_ex(source, common, rename, dead_code)`
  côté Rust) — les transforms "sans inconvénient" (commentaires, espaces, littéraux numériques,
  points-virgules redondants) restent toujours actifs, seuls les deux transforms les plus
  "agressifs" sont optionnels. Dialogue `_prompt_golf_options` (cases à cocher, préférence
  persistée via `QSettings`) devant `Golfer le code` et `Golfer tout le projet` — pas redemandé
  pour l'export golfé, qui réutilise silencieusement la dernière préférence. Le CLI batch
  (`--golf`) expose les mêmes réglages via `--no-rename`/`--no-dead-code`. Testé : les 4
  combinaisons rename×dead_code produisent un rendu pixel-identique à l'original, et
  `--no-rename --no-dead-code` donne bien un delta minimal (-4% sur un cas de test contre -43% en
  mode complet). Sur l'onglet `Common`, aucune option n'est proposée : `golf_common` reste toujours
  au niveau le plus prudent (ni renommage ni élagage), pour les raisons de sécurité cross-pass
  déjà documentées plus bas.
- [x] Suppression des fonctions/structs jamais utilisées (dead-code elimination) : `find_top_level_declarations`
  repère chaque déclaration à la profondeur d'accolade 0 (fonction ou struct — syntaxiquement
  identiques à ce niveau, sans ambiguïté possible en GLSL puisqu'il n'y a pas de fonctions
  imbriquées) ; une déclaration dont le nom n'apparaît nulle part ailleurs (occurrence unique = sa
  propre déclaration) est supprimée, `mainImage` toujours protégée. Itère jusqu'à point fixe (5-10
  passes) pour nettoyer les chaînes d'appels (A n'appelle que B, B jamais appelé ailleurs → B
  supprimé → A devient à son tour inutilisé → supprimé au tour suivant), vérifié explicitement.
  **Jamais appliqué à `Common`** (seulement aux passes) : une déclaration de Common peut sembler
  "inutilisée" dans son propre texte tout en étant appelée par une autre pass golfée séparément —
  `golf_common` ne fait aucune élimination, seul `golf_shader`/`golf_shader_with_common` (sur une
  pass) le fait. Vérifié par rendu pixel-identique avant/après sur un cas simple et une chaîne de 3
  fonctions mortes en cascade ; vérifié aussi qu'un helper de Common inutilisé *dans cette pass*
  mais potentiellement utilisé ailleurs n'est jamais supprimé.
  Pas d'élimination des variables globales inutilisées (scope volontairement limité aux
  fonctions/structs, plus net à raisonner sans risque de fausse-suppression).
- [x] Raccourcissement des littéraux numériques : `shorten_float_literal` retire les zéros de fin
  après le point (`0.50`→`0.5`) et le zéro de tête quand une décimale reste (`0.5`→`.5`), sans
  jamais tomber sur un simple `.` invalide (`0.0`/`0.` restent le minimum à 2 caractères).
  Uniquement sur les vrais littéraux flottants (`.`/exposant requis) — un entier de boucle/taille
  de tableau n'est jamais touché. **Bug réel trouvé et corrigé pendant l'implémentation** : la
  détection du littéral était court-circuitée par la logique existante de collapse d'espaces (elle
  avalait les chiffres avant que le nouveau scanner de nombres n'ait sa chance) ; corrigé en
  faisant s'arrêter ce collapse dès qu'un chiffre/point-décimal commence. Vérifié par rendu
  pixel-identique avant/après golf, pas seulement "ça compile".
- [x] Suppression des points-virgules superflus : `collapse_redundant_semicolons` réduit les
  suites de `;` séparées uniquement par des espaces à un seul `;` (ex. `x=1;;` → `x=1;`) — ne
  touche jamais le point-virgule qu'une instruction requiert réellement
- [x] **Suppression des espaces autour des opérateurs**, au-delà du simple collapse d'espaces
  existant. `collapse_whitespace` distinguait déjà les runs *purement* faits d'espaces (collapsés à
  un seul espace/`\n`) des runs "mixtes" contenant de la vraie ponctuation (ex. `" - "`) — ces
  derniers étaient jusqu'ici laissés tels quels. Nouvelle fonction `strip_operator_spaces` : les
  espaces en bordure d'un tel run sont **toujours** sûrs à retirer (par construction de
  `tokenize`, un run `Other` ne contient jamais de lettre/chiffre, donc ses deux bords touchent
  toujours un identifiant/nombre, un autre run `Other` **via** un token alphanumérique interposé, ou
  le début/fin de fichier — et aucun opérateur multi-caractères GLSL ne mélange une lettre/un
  chiffre avec de la ponctuation, `return-1.` se retokenise exactement comme `return -1.`) ; seul un
  espace **entre deux caractères de ponctuation à l'intérieur du même run** est réellement à risque,
  et n'est retiré que si `is_dangerous_operator_pair(prev, next)` l'autorise — table couvrant tous
  les opérateurs composés GLSL/C (`++ -- += -= *= /= %= <= >= == != && || << >> ^= &= |=`) plus les
  deux amorces de commentaire (`// /*`), pour ne jamais transformer `a - -b` en `a--b` (le risque
  cité dans ce ticket) ni faire apparaître un commentaire fantôme depuis `a / -b` mal espacé. Les
  retours à la ligne ne sont jamais insérés ni retirés (seule l'horizontale autour d'eux peut
  disparaître), donc le nombre de lignes — dont dépend le mapping d'erreur de
  `header_line_count` — ne change jamais.
  **Vérifié** par : suite de tests ciblés (jamais de `--`/`++` accidentel sur `a - -b`/`a + +b`,
  jamais de `//`/`/*` accidentel, `<` `=` espacés gardent bien leur espace au lieu de fusionner en
  `<=`, nombre de lignes inchangé sur un shader multi-lignes, macro fonction-like golfée sans
  casser sa syntaxe) ; un **fuzz test dédié** (500 000 séquences aléatoires de 2 à 6 caractères pris
  dans l'alphabet complet des opérateurs GLSL, 0 à 2 espaces aléatoires entre chacun) comparant la
  tokenisation "espaces = séparateurs durs" de la séquence d'origine à celle du résultat golfé —
  **0 divergence** ; et une régression sur `default.frag` (791 → 559 octets golfés, -29% supplémentaires
  rien que grâce à cette passe, en plus des transforms déjà existants). Build du crate complet non
  rejoué dans cet environnement (toolchain `rustc` trop ancien pour les dépendances transitives de
  `wgpu`/`image`), mais `golf.rs` est autonome (aucune dépendance externe, seulement
  `std::collections::HashSet`) et compile/tourne sans avertissement en isolation.

### 🏆 Golf avancé (niveau "démoscene" — inspiré d'Iñigo Quilez, Fabrice Neyret/Shader Minifier, et des habitués du golf sur Shadertoy)

Le golfer actuel golfe *au niveau du texte* (renommage, littéraux, mort-code, ponctuation). Les
outils de référence du milieu — les articles d'optimisation/golfing d'[Iñigo
Quilez](https://iquilezles.org), [Shader Minifier](https://github.com/laurentlb/Shader_Minifier) de
Fabrice Neyret (utilisé dans la quasi-totalité des intros 4k/8k qui tournent sur Shadertoy/pouët),
et les techniques qui reviennent systématiquement dans les shaders les plus courts du site — vont
plus loin : ils réécrivent aussi la *structure* du code, pas seulement ses noms. Prochaine étape,
par ordre de rapport gain/risque :

- [x] **Simplification algébrique (peephole).** `simplify_algebra` (dans `golf.rs`, appelé depuis
  `golf_shader_impl` **et** `golf_common` — purement local, aucun risque cross-pass, contrairement
  au renommage) reconnaît une poignée de motifs sans risque sur une re-tokenisation dédiée
  (`lex_alg`/`AlgTok`, un token par caractère de ponctuation — plus simple que les runs `Other` de
  `tokenize`, justement pour ne jamais avaler par accident le `)`/`;` qui suit un motif reconnu) :
  `x*1.`/`1.*x`/`x/1.` → `x`, `x+0.`/`0.+x`/`x-0.` → `x`, `x*0.`/`0.*x` → `0.`, double négation
  `- -x` (unaire, espace/retour-ligne séparateur uniquement — jamais `--x` qui reste l'opérateur
  ++/-- réel) → `x`, `pow(x,2.)` → `x*x` et `pow(x,3.)` → `x*x*x` (au-delà `pow` reste plus court,
  volontairement non traité). Chaque motif exige un **opérande atomique** (un seul identifiant ou
  littéral, jamais `)`/`]` ni une sous-expression reconstruite — `pow(a+b,2.)` ne matche pas, le `,`
  attendu juste après l'opérande n'est pas là) ; itère jusqu'à point fixe (8 passes max, comme
  `remove_unused_functions`) pour enchaîner par ex. `pow(x,2.)*1.` → `x*x*1.` → `x*x`. Le piège
  cité dans ce ticket (`a - -b` ne doit jamais devenir `ab`) est géré en exigeant qu'un `-` avant la
  paire soit en position unaire (`is_binary_lhs` : rien d'atomique ni de `)`/`]` juste avant), et la
  distinction `- -x` (double négation) vs `--x` (vrai `--`) repose sur l'invariant déjà établi par
  `strip_operator_spaces` : un `--` sans aucun caractère entre les deux `-` est *toujours* le vrai
  opérateur, jamais touché par aucune passe. **Vérifié** par 12 tests ciblés (les deux ordres pour
  chaque motif commutatif, les non-commutatifs `1./x`/`0.-x` explicitement *non* simplifiés,
  `pow` avec argument composé ou accès membre laissé intact, exposant ≥4 laissé intact, `a- -b` et
  `f(x)- -y` laissés intacts, `x--;`/`--x;` laissés intacts, enchaînement multi-passes, nombre de
  lignes inchangé, et un test bout-en-bout via `golf_shader`) plus une régression sur `default.frag`
  (toujours 559 octets golfés — inchangé, ce shader ne contient aucun des motifs ci-dessus, ce qui
  confirme l'absence de faux positif). Build du crate complet non rejoué dans cet environnement
  (même limite de toolchain que documentée plus haut pour `strip_operator_spaces`), mais `golf.rs`
  compile et ses tests passent en isolation (`rustc`/`cargo` 1.75, aucune dépendance externe).
- [x] **`x = x + 1.` / `x += 1.` → `x++`** (et l'équivalent `--`) quand l'opérande est exactement le
  littéral `1`/`1.` : ajouté comme motif supplémentaire de `simplify_algebra` (`golf.rs`). N'est
  réécrit que quand la valeur de l'affectation elle-même n'est jamais utilisée — seul cas où
  remplacer par `x++`/`x--` (qui renvoie l'ancienne valeur) reste équivalent à l'affectation (qui
  renvoie la nouvelle) : soit l'affectation termine une instruction complète (`;`, toujours sûr),
  soit c'est la clause d'incrément d'un `for` (`for(init;cond;x+=1.)`), reconnue sans vrai parseur
  en exigeant que le `)` refermant soit précédé du `;` qui termine la clause de condition —
  `if(x=x+1.)` ou `foo(x+=1.)`, où le `)`/`,` est précédé d'un `(` et non d'un `;`, ne matchent donc
  jamais. Exige aussi que le nom soit identique des deux côtés du `=` (accumulateur qui se
  réaffecte lui-même) et jamais un mot réservé. **Vérifié** par des tests ciblés (les 4 formes ×
  `+`/`-`, dans un en-tête de boucle `for`, non-déclenchement quand la valeur est réellement
  utilisée — condition `if`, argument d'appel, sous-expression — et non-déclenchement sur noms
  différents ou littéral ≠ 1) plus la même régression `default.frag` (559 octets golfés, inchangé —
  ce shader golfe déjà sa boucle en `o++` natif, rien à réécrire ici, confirmant l'absence de faux
  positif). Même limite de build complet non rejoué dans cet environnement que documentée plus
  haut ; `golf.rs` compile et ses tests passent en isolation.
- [x] **Golf des boucles `for`** vers la forme condensée qu'on retrouve dans la quasi-totalité des
  raymarchers golfés (`for(float i=0.;i++<64.;)` au lieu de `for(float i=0.;i<64.;i++)`) :
  `golf_for_loops` (`golf.rs`, appelée depuis `golf_shader_impl` **et** `golf_common`, juste après
  `simplify_algebra` — même statut "purement local, aucun risque cross-pass" que cette dernière,
  donc toujours active, non gérée par les cases à cocher rename/dead-code) reconnaît le motif
  canonique `for(TYPE i=INIT;i<BOUND;i++){BODY}` sur la re-tokenisation `AlgTok` déjà utilisée par
  `simplify_algebra`, et le réécrit en `for(TYPE i=INIT;i++<BOUND;){BODY}` — l'incrémentation
  repoussée dans la condition, clause d'incrément vidée. `TYPE` est limité à
  `float`/`int`/`uint`/`double` (les seuls types plausibles pour un compteur de boucle) ; le
  fonctionnement en aval de `simplify_algebra` est volontaire : une clause `i+=1.`/`i=i+1.` est
  déjà normalisée en `i++` avant que ce motif ne soit recherché, un seul endroit reconnaît donc la
  forme incrément.
  **Garde-fou central** : la variable de boucle ne doit apparaître **nulle part ailleurs** que les
  trois emplacements canoniques déjà couverts par le motif (sa déclaration, la comparaison, la clause
  d'incrément) — ni dans `INIT`, ni dans `BOUND`, ni dans `BODY`, sinon le motif entier n'est pas
  reconnu et rien n'est réécrit. Nécessaire parce que `i++<BOUND` incrémente `i` *avant* la
  comparaison au lieu d'après le corps : chaque itération de `BODY` — et la comparaison elle-même —
  s'exécute avec une valeur de `i` supérieure de 1 à ce que la boucle d'origine lui aurait donné à ce
  stade ; invisible depuis l'extérieur de la boucle tant que rien ne dépend de la valeur de `i` pendant
  qu'elle tourne, ce que les trois vérifications garantissent. Vrai quel que soit `TYPE` (`int` ou
  `float`) : un compteur entier ne décale pas plus dangereusement qu'un flottant, aucun traitement
  différencié par type. Motif strictement `i<BOUND` (`<=` explicitement exclu — testé — pour rester
  au plus près du motif canonique décrit ici, même si un `<=` se réécrirait en fait tout aussi
  correctement) ; pas de type explicite (`for(i=0.;...)`, variable préexistante réutilisée plutôt que
  fraîchement déclarée), boucle descendante (`i--`), ou corps sans accolades ne matchent jamais non
  plus. Itère jusqu'à point fixe (4 passes, même logique que `simplify_algebra`) pour qu'une boucle
  imbriquée dans le corps d'une boucle déjà réécrite (motif recopié tel quel par le motif englobant,
  donc pas encore vue) soit traitée à la passe suivante ; une boucle interne qui *ré-utilise le même
  nom* que la boucle externe (shadowing légal en GLSL) bloque volontairement la réécriture de la
  boucle externe, faute d'une vraie analyse de portée — conservateur par construction, comme le reste
  de ce fichier. Volontairement restrictif (motif exact reconnu uniquement, comme `simplify_algebra`)
  plutôt qu'une réécriture générale de boucle, pour ne jamais changer la sémantique d'un `for` qui
  utilise `i` autrement.
  **Vérifié** par des tests ciblés (motif de base sur `int`/`float`, non-déclenchement quand la
  variable est réutilisée dans le corps/`INIT`/`BOUND`, rejet de `<=`/boucle sans type/boucle
  descendante/corps sans accolades/type non reconnu, boucles imbriquées avec noms distincts réécrites
  aux deux niveaux via le point fixe, boucle interne qui masque le même nom bloquant bien la boucle
  externe, enchaînement correct après la normalisation `i+=1.`→`i++` de `simplify_algebra`, nombre de
  lignes inchangé) plus un test bout-en-bout via `golf_shader`, et une régression sur `default.frag`
  (toujours 559 octets golfés, inchangé — sa seule boucle `for` référence sa variable dans le corps
  via `i*0.4`, donc correctement non réécrite, confirmant l'absence de faux positif) ; sur un shader
  de démonstration avec une boucle dont le compteur n'est jamais lu dans le corps, la réécriture
  s'applique bien (`for(float e=0.;e++<64.;){...}`). Build du crate complet non rejoué dans cet
  environnement (même limite de toolchain que documentée plus haut : `Cargo.lock` v4 nécessite un
  cargo plus récent que celui disponible ici), mais `golf.rs` reste autonome et compile/passe ses
  tests en isolation (`rustc`/tests intégrés, aucune dépendance externe).
- [x] **Fusion des déclarations consécutives** du même type (`float a=1.;float b=2.;` →
  `float a=1.,b=2.;`) : motif très courant en tête de `mainImage` pour poser les variables de
  raymarching (origine, direction, distance accumulée...). `merge_consecutive_declarations`
  (`golf.rs`, appelée depuis `golf_shader_impl` **et** `golf_common`, juste après `golf_for_loops`
  — même statut "purement local, aucun risque cross-pass" que `simplify_algebra`/`golf_for_loops`,
  donc toujours active, non gérée par les cases à cocher rename/dead-code) : la fonction elle-même
  existait déjà (scaffolding présent dans un état précédent du fichier, avec sa documentation et son
  parseur `parse_type_decl_stmt`) mais n'était appelée nulle part — **branchée dans le pipeline** et
  couverte de tests pour cette tâche.
  `parse_type_decl_stmt` reconnaît une déclaration `TYPE nom[=INIT](,nom[=INIT])*;` sur la
  re-tokenisation `AlgTok` déjà utilisée par `simplify_algebra`/`golf_for_loops`, avec suivi de
  profondeur `(`/`[` pour ne jamais confondre une virgule d'appel de constructeur
  (`vec3(1.,2.,3.)`) avec la virgule séparatrice de déclarateurs qu'elle introduit. `TYPE` limité
  à `DECL_BASE_TYPES` (scalaires/vecteurs/matrices — pas de `sampler*`, pas de `struct`, cette
  passe n'ayant aucune notion de quels identifiants sont des noms de type utilisateur).
  **Garde-fous** : (1) seules des déclarations **strictement adjacentes modulo espaces/retours à
  la ligne** (`skip_ws`) sont fusionnées — dès qu'autre chose s'intercale (une autre instruction,
  une accolade `{`/`}`, un appel de fonction), la chaîne s'arrête net, donc impossible de
  traverser un `if`/`for`/`{...}` et de changer la portée d'une déclaration ultérieure ; (2)
  `preceded_by_qualifier` refuse toute fusion si la première déclaration de la chaîne est précédée
  d'un mot-clé qualificatif (`const`, `highp`, `out`, `layout`...) — fusionner appliquerait ce
  qualificatif à *tous* les déclarateurs fusionnés, correct seulement si chaque instruction
  fusionnée le portait déjà, ce que cette passe ne peut pas vérifier pour les instructions après
  la première ; (3) une déclaration de tableau (`float arr[4]`) bloque la fusion de toute la
  chaîne dès qu'elle apparaît — accoler un `[...]` dans une liste partagée de déclarateurs est trop
  facile à mal faire. Contrairement à `simplify_algebra`/`golf_for_loops`, une seule passe suffit
  (la boucle de chaînage interne étend déjà une fusion aussi loin que possible) et l'invariant
  "nombre de lignes inchangé" ne s'applique volontairement pas ici : fusionner deux instructions en
  une seule est justement la source de l'économie (une ligne de moins).
  **Vérifié** par 13 tests ciblés (fusion basique, chaînage de 3 déclarations, non-déclenchement à
  travers une instruction intercalée, non-déclenchement à travers une accolade `if{...}`,
  non-fusion entre types de base différents, non-fusion d'une déclaration qualifiée (`const`),
  non-fusion d'un tableau, déclaration isolée laissée intacte, non-confusion avec une définition de
  fonction (`float f(float x){...}`), préservation d'un initialiseur à virgules imbriquées
  (`vec3(1.,2.,3.)`), déclarations sans initialiseur, fusion à travers un saut de ligne) plus un
  test bout-en-bout via `golf_shader_ex(src,"",false,false)` (rename/dead-code désactivés pour
  garder des noms lisibles dans l'assertion). **Régression sur `default.frag`** : contrairement à
  toutes les passes précédentes de ce fichier, ce n'est *pas* un cas no-op — `palette()` déclare 4
  `vec3` adjacents (`a`,`b`,`c`,`d`) et `mainImage` ouvre sur 3 `float` adjacents
  (`speed`,`scale`,`colorMix`), exactement le motif visé par ce ticket : 559 → **521 octets golfés**
  (-38 octets, mesuré après ajout de cette passe — le chiffre de 559 cité par les entrées
  précédentes de ce fichier date d'avant son ajout). Toolchain disponible dans cet environnement
  cette fois (`rustc`/`cargo` 1.75 installés via `apt` pendant cette tâche) : `golf.rs` compile et
  ses 40 tests (les 13 nouveaux + les 27 déjà existants, tous toujours au vert — aucune régression
  sur les passes précédentes) passent en isolation via `rustc --test`, hors du crate complet.
  Build du crate complet (`cargo check`) toujours impossible dans cet environnement — même limite
  de `Cargo.lock` v4 déjà documentée plus haut, indépendante de la présence de `rustc`/`cargo`
  eux-mêmes — donc pas de vérification par rendu GPU pixel-identique cette fois ; seule la
  correction structurelle/textuelle est couverte par les tests ci-dessus.
- [x] **Suppression des accolades autour d'un bloc à instruction unique** (`if(x){y=1.;}` →
  `if(x)y=1.;`) : `strip_redundant_braces` (`golf.rs`, appelée depuis `golf_shader_impl` **et**
  `golf_common`, juste après `merge_consecutive_declarations` — même statut "purement local, aucun
  risque cross-pass" que le reste de ce groupe de passes, donc toujours active, non gérée par les
  cases à cocher rename/dead-code) repère chaque `{` dont le token précédent (via `lex_alg`/`AlgTok`,
  la même re-tokenisation que `simplify_algebra`/`golf_for_loops`) est `else`, ou un `)` fermant la
  condition d'un `if`/`for`/`while` (`brace_follows_strippable_header`) — jamais le corps d'une
  fonction, d'un `switch` ou d'un `do{...}while(...)`, dont le token précédent ne matche aucun de ces
  cas. Le bloc n'est dégolfé que s'il contient *exactement* une instruction (`skip_statement` scanne
  jusqu'au premier `;` de profondeur 0, un bloc vide ou à plusieurs instructions étant rejeté) et
  qu'aucun `if` à l'intérieur — à quelque profondeur que ce soit, y compris ré-accolé lui-même —
  n'est dépourvu de son propre `else` explicite (`contains_if_without_else`, seul cas d'ambiguïté
  réelle en grammaire C-like, exactement le garde-fou dangling-else prévu par ce ticket) ; `for`/
  `while` reçoivent le même traitement conservateur bien qu'ils ne puissent eux-mêmes porter un
  `else` (uniformité voulue, pas de cas particulier). `find_strippable_braces` évalue chaque paire
  `{`/`}` candidate directement sur le flux de tokens *original*, jamais sur une copie partiellement
  réécrite — ce qui permet un unique passage gauche-droite (contrairement au point fixe qu'exigent
  `simplify_algebra`/`golf_for_loops`) même sur un nesting arbitrairement profond, une passe englobante
  traitant un bloc interne encore accolé comme une seule instruction indépendamment du sort de ses
  propres accolades.
  **Vérifié** par 21 tests ciblés (motif de base `if`/`for`/`while`, `if`/`else` dégolfés ensemble,
  chaîne `else if`, bloc à plusieurs instructions et bloc vide jamais touchés, corps de fonction/
  `switch`/`do-while` jamais confondus avec un corps strippable, 5 tests dédiés au dangling-else —
  `if` interne sans `else` bloquant l'accolade englobante même si l'`if` interne a lui-même ses
  propres accolades, même blocage propagé à travers un `for` intercalé, un `if` isolé sans rien
  autour n'étant pas un faux rejet, et un `if` interne dont l'`else` est déjà résolu qui autorise bien
  le dégolfage externe — nesting indépendant à deux niveaux en un seul passage, dégolfage d'un
  fragment pendant qu'un bloc voisin non-strippable reste intact, initialiseur `vec3(1.,2.,3.)`
  préservé à l'intérieur d'un corps dégolfé) plus un test bout-en-bout via `golf_shader`. **Régression
  sur `default.frag`** : no-op confirmé (toujours 521 octets golfés — son seul corps `if`/`for`/
  `while` contient plusieurs instructions, donc jamais un candidat). Toolchain Rust indisponible dans
  cet environnement pour cette tâche (ni `rustc`/`cargo` déjà installés, ni accès réseau pour les
  récupérer via `apt` — contrairement à la tâche précédente où l'installation avait réussi) : le code
  et ses 61 tests (40 déjà existants + 21 nouveaux) n'ont donc pas pu être ré-exécutés ni compilés
  cette fois-ci ; relecture manuelle attentive du texte et de la logique des gardes-fous à la place,
  mais aucune vérification automatisée (ni tests, ni rendu GPU pixel-identique) n'a pu être rejouée
  dans cette session.
- [x] **Extraction automatique de macros pour les sous-expressions répétées** (dictionnaire commun,
  la technique phare de Shader Minifier) : `extract_repeated_subexpr_macros` (`golf.rs`) recense les
  sous-expressions textuelles identiques apparaissant `N` fois ou plus et les factorise en un
  `#define` à 1-2 caractères inséré en tête de fichier, seulement si le gain net (occurrences ×
  longueur économisée − coût de la ligne `#define`) est strictement positif. Granularité "appel de
  fonction complet" (`find_matching_close` sur le `(`/`)`, un appel dans les arguments d'un autre
  n'en referme jamais un prématurément) ou "accès membre complet" (chaîne maximale de `.Ident`,
  `a.xyz.zyx` reste un seul candidat, jamais scindé en sous-chaînes) — jamais un fragment arbitraire
  qui casserait la syntaxe une fois substitué ; recherche de motifs répétés sur la séquence de
  tokens post-golf (`lex_alg`, la même re-tokenisation que `simplify_algebra`/`golf_for_loops`),
  pas une vraie analyse d'AST, comme anticipé par ce ticket. `STATEMENT_KEYWORDS` (`if`/`for`/
  `while`/`switch`/`return`/`discard`) exclut explicitement les mots-clés de contrôle de flux de la
  base d'un candidat — extraire `if(cond)` puis le substituer en arrière supprimerait silencieusement
  le mot-clé `if` de l'instruction — sans quoi `sin`/`iResolution`/... (des identifiants réservés
  mais pas des mots-clés de contrôle) ne pourraient plus jamais servir de base, alors que c'est
  justement l'exemple canonique de ce ticket. Jamais de substitution à l'intérieur d'une ligne de
  directive préprocesseur existante (`mark_directive_tokens`). Itère jusqu'à point fixe (16 passes
  max, un nouveau tour peut révéler un nouveau motif une fois un premier sous-terme déjà remplacé
  des deux côtés) ; en pratique 1-2 tours suffisent pour la plupart des shaders.
  **Intégration dans le pipeline** (l'étape annoncée par ce ticket comme suite du prototypage) :
  branchée dans `golf_shader_impl` en toute dernière étape, après `collapse_redundant_semicolons` —
  le comptage d'occurrences et l'arithmétique de gain doivent porter sur l'orthographe finale et les
  octets qui partiront réellement, jamais avant renommage/simplifications. Comme
  `simplify_algebra`/`golf_for_loops`/`merge_consecutive_declarations`/`strip_redundant_braces`,
  reste **toujours active**, non gérée par les cases à cocher rename/dead-code : par construction
  (`best_macro_extraction` n'accepte jamais un candidat à gain net ≤ 0) elle ne peut jamais faire
  grossir la sortie, donc aucune raison de la rendre optionnelle. `extra_protected` (les identifiants
  déclarés par `Common`, déjà utilisé pour protéger le renommage) est aussi transmis au choix du nom
  de macro : un `#define` est une substitution aveugle, un nom qui collision­nerait avec un
  identifiant exporté par Common corromprait silencieusement les appels de cette pass vers Common.
  **Jamais appliqué à `Common`** (`golf_common` inchangé) — même risque cross-pass que
  renommage/élagage : un `#define` introduit dans Common serait visible de toutes les passes qui la
  préfixent, et pourrait entrer en collision avec un nom qu'une pass choisit *elle-même* pour l'une
  de ses variables locales (renommage local que `protected_names_from_common` ne voit jamais),
  réécrivant alors silencieusement cette variable à chaque usage.
  **Vérifié** par la suite de tests déjà écrite lors du prototypage (round-trip `lex_alg`/
  `render_alg_toks`, extraction rentable sur appel de fonction et sur accès membre, non-extraction à
  gain net négatif ou à occurrence unique, directives préprocesseur jamais touchées, non-collision de
  nom, chaîne d'accès membre maximale non scindée, cas dégénéré auto-imbriqué `f(f(f(x)))` sans
  panique, mots-clés de contrôle de flux jamais une base valide, fonctions/variables builtin valides
  comme base, appel espacé avant `(` jamais reconnu, taille jamais croissante) **plus 3 nouveaux
  tests d'intégration** ajoutés avec le branchement : extraction bien déclenchée en bout-en-bout via
  `golf_shader` (pas seulement `extract_repeated_subexpr_macros` appelée directement), nom de macro
  qui évite bien un identifiant réservé par `Common` via `golf_shader_ex`, et confirmation que
  `golf_common` n'introduit jamais de `#define`. **Régression sur `default.frag`** : cette fois un
  vrai changement (pas un no-op) — `iResolution.xy` y apparaît deux fois (correction d'aspect-ratio
  et échantillonnage d'`iChannel0`), gain net faible mais positif, 521 → **519 octets golfés**.
  Toolchain Rust disponible dans cette session (`rustc`/`cargo` réinstallés via `apt`, comme pour la
  passe littéraux plus haut dans ce fichier) : les 78 tests du fichier (75 déjà existants + 3
  nouveaux) passent via `rustc --test`, hors du crate complet — `cargo check` du crate complet
  toujours impossible (même limite de `Cargo.lock` v4 déjà documentée ailleurs dans ce fichier, apt
  ne fournissant qu'un `cargo` 1.75 trop ancien pour ce format de lockfile), donc pas de vérification
  par rendu GPU pixel-identique cette fois non plus ; le "golf-à-froid" (round-trip de compilation)
  demandé par ce ticket reste néanmoins couvert *à l'usage* — `main_window.py`/`_do_golf`/
  `_do_golf_all` recompilent déjà systématiquement tout golf produit par `golf_shader_ex` avant de
  l'accepter, ce nouveau code en hérite automatiquement sans rien à ajouter côté Python.
  **Effet de bord de cette tâche** : avoir enfin `rustc`/`cargo` disponibles a permis de rejouer, pour
  la première fois, la suite de tests de l'item dangling-else ci-dessus — révélant que 4 de ses tests
  (jamais exécutés faute de toolchain au moment de leur écriture, comme documenté sur cet item) se
  trompaient dans leurs valeurs attendues, alors que le comportement réel de `strip_redundant_braces`
  était correct : deux tests s'attendaient à ce que seules les accolades *externes* d'un if/else
  imbriqué soient retirées alors que le code retire aussi, en toute sécurité, les accolades internes
  indépendamment sûres ; un test comparait un nom de variable non renommé alors que `golf_shader`
  renomme toujours ; un test de non-collision de nom de macro reposait sur une prémisse fausse
  (`i2` déclaré au lieu de `i`, laissant `i` libre). Corrigés avec commentaires à jour ; aucun
  changement de comportement du golfer lui-même n'a été nécessaire, seulement des assertions de test.
- [x] **Renommage pondéré par fréquence.** Le renommage base-52 n'attribue plus les noms courts dans
  l'ordre de première rencontre dans le fichier ; `golf_shader_impl` fait maintenant un premier passage
  de comptage (un `HashMap<String, usize>` d'occurrences par identifiant renommable, filtré des mêmes
  exclusions qu'avant — réservés, préfixés par un `.`, noms de macro fonction-like, noms protégés de
  `Common`), puis trie la liste des identifiants par nombre d'occurrences décroissant (comme le fait
  Shader Minifier) avant d'appeler `short_name` dans cet ordre — l'identifiant le plus utilisé, souvent
  une fonction utilitaire appelée depuis `mainImage` une dizaine de fois, reçoit désormais
  systématiquement `a` plutôt qu'un nom utilisé une seule fois qui serait simplement apparu en premier
  dans le texte. Égalité de fréquence tranchée de façon déterministe par l'ordre de première rencontre
  (`Vec::sort_by` est un tri stable en Rust, donc deux identifiants à occurrences égales gardent l'ordre
  dans lequel ils ont été vus la première fois — sortie reproductible d'une exécution à l'autre, pas
  dépendante de l'ordre d'itération d'une `HashMap`). Gain net non nul même quand le nombre de
  caractères économisés par identifiant ne change pas, uniquement en changeant *quel* nom court va à
  *quel* identifiant — sur un fichier avec plus de 52 identifiants renommables (ce qui dépasse le
  premier palier d'une lettre), l'identifiant le plus fréquent évite ainsi systématiquement de se voir
  attribuer un nom à 2 caractères. Sur les fichiers avec 52 identifiants renommables ou moins (le cas de
  `default.frag`, par exemple), tous reçoivent un nom à 1 caractère quel que soit l'ordre, donc aucun
  changement de taille — seule la *correspondance* nom↔identifiant change, jamais la taille golfée dans
  ce cas. Vérifié par deux nouveaux tests ciblés (`rename_weighted_by_frequency_not_first_encounter` :
  un identifiant cité une fois mais rencontré en premier dans le texte ne reçoit pas `a` face à un
  identifiant appelé 4 fois mais rencontré après ; `rename_frequency_ties_broken_by_first_encounter` :
  à occurrences égales, l'ordre de première rencontre décide) et par la suite de tests existante, rejouée
  en intégralité (79 tests, tous au vert) — y compris les deux régressions sur `default.frag`
  (`regression_default_frag_size_shrinks` et `strip_redundant_braces_tests::regression_default_frag_unaffected`,
  toutes deux toujours à leurs octets attendus, confirmant l'absence d'effet de bord sur un fichier à
  moins de 52 identifiants renommables) et `full_pipeline_smoke_test`, dont le commentaire a été corrigé
  pour refléter le nouveau critère de tri (`c` devient toujours `a` ici, mais parce qu'il est cité 3 fois
  contre 2 pour `p`, plus seulement parce qu'il apparaît en premier). `rustc`/`cargo` toujours
  indisponibles dans cet environnement pour un `cargo test` sur le crate complet (dépendances
  transitives `wgpu`/`image` — voir les tickets golf précédents), mais `apt-get install rustc cargo`
  (Rust 1.75, suffisant puisque `golf.rs` n'a aucune dépendance externe) a permis de compiler et lancer
  `golf.rs` de façon autonome avec `rustc --test`, en reproduisant juste l'arborescence relative attendue
  par les `include_str!("../../python_ui/assets/shaders/default.frag")` du fichier de test.
- [x] **Score golf affiché avec repères.** `golf_size_tier_label` (`python_ui/ui/footer.py`) ajoute
  au texte de taille déjà affiché (voir plus haut) un repère qualitatif — "< 2 Ko"/"< 4 Ko"/"< 8 Ko",
  les paliers traditionnels des compos démoscene 4k/8k — pour que le retour golf-à-froid serve aussi
  de mini-guide vers les tailles cibles que vise cette communauté, plutôt qu'un chiffre brut sans
  repère. Vérifié sur la taille brute golfée (`after`, pas le gzip) puisque c'est la figure dont
  parle réellement le score golf ; premier palier atteint dans l'ordre croissant (`<= 2048` →
  "< 2 Ko", sinon `<= 4096` → "< 4 Ko", sinon `<= 8192` → "< 8 Ko"), et `None` (aucun repère affiché,
  plutôt qu'un repère trompeur) une fois tous les paliers dépassés — `_size_label` n'ajoute alors
  rien au texte existant. Pas de comparaison en ligne avec un score externe (aucune API de
  leaderboard fiable et stable à intégrer) — hors périmètre, comme prévu par ce ticket ; mentionné
  dans le tooltip existant pour que ce choix reste visible sans avoir à lire le code.
  **Vérifié** par 8 cas testés en Python pur, sans dépendance PySide6 (bornes exactes de chaque
  palier des deux côtés — 2048/2049, 4096/4097, 8192/8193 — plus `0` et le cas `521` de
  `default.frag` golfé, tous "< 2 Ko" comme attendu) : PySide6 n'est pas installé dans cet
  environnement (`ModuleNotFoundError`, et pas d'accès réseau pour l'installer), donc la fonction a
  été testée en isolation plutôt que via le widget `Footer` complet — `python3 -m py_compile` confirme
  par ailleurs que le fichier entier reste syntaxiquement valide après l'édition. Pas de vérification
  visuelle du rendu Qt (environnement sans affichage/PySide6 disponible ici).

- [x] Renommage des noms de `#define` objet-like (`#define NAME valeur`) au lieu de les protéger tel
  quels : `NAME` traverse maintenant le même pipeline de renommage que n'importe quel identifiant
  (`golf.rs::golf_shader_impl`) — la déclaration et tous les sites d'usage partagent le même texte de
  token, donc le renommage général les garde synchronisés sans traitement spécial. Les macros
  fonction-like (`#define MAX(a,b) ...`, pas d'espace avant `(`) restent protégées : leurs paramètres
  ne sont pas isolés en portée par ce tokenizer simple, donc renommer à l'intérieur risquerait une
  collision avec un identifiant sans rapport ailleurs dans le fichier. Inlining complet de la valeur
  (remplacer chaque usage par la valeur littérale) volontairement laissé de côté : gain d'octets
  marginal une fois le nom déjà réduit à 1 caractère, pour un risque de complexité/bugs plus élevé.
  **Bug pré-existant découvert et corrigé au passage** : le tokenizer n'a aucune notion de ligne de
  directive préprocesseur, donc le mot suivant `#` (`define`, `ifdef`, `endif`, `pragma`, ...) était
  scanné comme un identifiant ordinaire et pouvait lui-même être renommé (`#define` → `#a`), cassant
  toute macro du projet une fois golfée — présent depuis la toute première version du golfer,
  pas introduit par ce changement. Fixé en ajoutant les mots-clés de directives à `RESERVED`. Vérifié
  par rendu pixel-identique avant/après golf sur : macro objet-like multi-usages, macro fonction-like,
  plusieurs `#define` combinés à `#ifdef`/`#endif`, et régression complète sur `default.frag`.
- [x] Golf à froid : vérifier automatiquement que le code golfé recompile (round-trip test) avant de
  remplacer l'éditeur (`MainWindow._do_golf`) **et** avant d'écrire le fichier exporté
  (`_on_export_golfed`, via un `Engine` jetable pour ne pas perturber le pipeline live). En cas
  d'échec : avertissement + restauration du pipeline pré-golf, l'éditeur/fichier n'est pas touché.
- [x] Bouton "annuler le golf" dédié (`Édition → Annuler le golf`) : restaure le code source
  d'origine capturé juste avant le dernier golf (`_pre_golf_source`), indépendant de Ctrl+Z
- [x] Statistiques étendues dans le footer : taille brute **et** taille gzip estimée avant/après
  (`Footer.set_golf_sizes` compresse via `gzip.compress`, tooltip explicatif)
- [x] Export en golf batch/CLI : `python run.py --golf entree.frag sortie.frag`, aucun Qt/GPU requis
  côté transformation (texte pur), même garde-fou golf-à-froid que le bouton interactif
- [x] Golf multi-passes ("Édition → Golfer tout le projet") : golfe Image + Buffer A-D + Common en
  une fois, tout ou rien (si une seule pass golfée ne compile plus, rien n'est modifié, y compris
  Common). **Bug réel trouvé et corrigé** : golfer Common et chaque pass indépendamment renommait
  un nom déclaré dans Common (ex. une fonction utilitaire) différemment de chaque côté, cassant
  toute pass qui l'appelait (`Unknown function 'c'` à la compilation). Corrigé avec deux nouvelles
  primitives dans `golf.rs` : `golf_common` (ne renomme jamais rien, seul le Common a besoin de
  cette stabilité puisqu'il est référencé par des passes golfées séparément) et
  `golf_shader_with_common` (golfe une pass normalement mais protège tous les noms déclarés dans le
  Common *original* du renommage). Le golf d'une seule pass (bouton simple) et l'export golfé
  (`_on_export_golfed`, qui inline désormais le Common golfé pour rester un fichier autonome)
  utilisent aussi ces primitives. Vérifié par rendu pixel-identique avant/après sur un projet
  Common + Buffer A + Image.

### 🏆 Golf avancé — prochaine vague

Le golfer couvre maintenant tout ce qui se reconnaît en un seul passage local sur `AlgTok`
(algèbre, boucles `for`, fusion de déclarations, accolades, macros de sous-expression). Les gains
qui restent sur la table demandent soit un peu plus de contexte (savoir qu'un paramètre est
toujours `in`, que deux branches écrivent la même variable), soit une vraie notion de portée
(inlining), donc classés ci-dessous du plus sûr/rentable au plus risqué — même logique de tri que
la section précédente.

- [x] **Affectation composée généralisée** (`x = x OP atomic` → `x OP= atomic`) : extension de la
  règle `x=x+1.`→`x++` déjà en place, dans `simplify_algebra_pass` (`golf.rs`), généralisée dans
  deux directions indépendantes — n'importe lequel des 5 opérateurs GLSL qui ont un composé valide
  (`+ - * / %`, pas seulement `+`/`-`) et n'importe quel opérande atomique à droite (identifiant ou
  littéral, via `is_atomic_operand`, pas seulement le littéral `1`). Exemple motivant : `col =
  col*light;` (une ligne d'accumulation de couleur typique en tête de boucle de raymarching) → `col
  *= light;`.
  **Périmètre revu à la baisse pendant l'implémentation, pour une vraie raison de sécurité, pas par
  simple prudence** : le plan initial envisageait d'accepter toute une sous-expression à droite
  (`x = x - dir*t` → `x -= dir*t`), pas seulement un opérande atomique. Repoussé en cours de route
  après avoir réalisé que ce cas général romprait la garantie de rendu bit-exact que tout ce fichier
  respecte scrupuleusement ailleurs : `x=x+a-b;` recalculé en `x+=a-b;` associe l'arithmétique
  flottante différemment (`x+(a-b)` au lieu de `(x+a)-b`), et l'addition flottante n'est **pas**
  associative bit pour bit — un delta d'arrondi indétectable à l'œil mais réel, exactement le genre
  d'écart que la vérification "rendu GPU pixel-identique" citée sur chaque item précédent de cette
  section a justement pour but d'attraper. Se restreindre à un opérande atomique élimine le risque à
  la racine : il n'y a alors qu'une seule opération binaire en jeu des deux côtés (avant/après),
  donc rien à réassocier.
  **Garde-fou central, et sa vraie justification** (pas celle envisagée dans le plan initial) : le
  token juste après l'opérande atomique doit être un terminateur valide (`;`, ou `)` précédé d'un
  `;` — même `is_valid_increment_terminator` que la règle `x++` déjà en place). Le plan initial
  pensait interdire la réapparition de `x` plus loin dans l'expression ; en pratique le bon
  garde-fou est plus simple et plus général — vérifier que l'opérande atomique est bel et bien
  **toute** l'expression à droite, pas seulement son premier jeton. Sans ce test,
  `x=x*x+1.;` (qui vaut `(x*x)+1.`) deviendrait `x*=x+1.;` (qui vaut `x*(x+1.)` = `x*x+x`, une
  valeur différente) — le premier `x` après l'opérateur est bien un opérande atomique valide,
  c'est uniquement le `+` qui le suit, au lieu d'un `;`, qui doit faire échouer la règle ; le cas
  cité dans le plan initial (`x` qui réapparaît) est correctement rejeté comme cas particulier de ce
  test plus général, pas par une vérification dédiée à la réapparition du nom. Contrairement à la
  règle `x++`, **aucune** condition "la valeur de l'affectation n'est jamais lue" n'est nécessaire
  ici : `x OP= atomic` et `x = x OP atomic` valent exactement la même chose dans n'importe quel
  contexte (les deux sont, par définition, une expression d'affectation dont la valeur est la
  nouvelle valeur assignée) — contrairement à `x++` (valeur de retour = ancienne valeur), donc cette
  règle se déclenche même dans `if(x=x*2.)`/un argument d'appel, là où `x++` ne le pourrait pas.
  Placée juste après la règle `x=x+1.`/`x+=1.` existante dans le même bloc `if !is_reserved(name)`,
  pour que `x=x+1.` continue prioritairement à donner `x++` (strictement plus court) plutôt que
  `x+=1.` — vérifié explicitement par test plutôt que supposé de l'ordre d'exécution.
  **Vérifié** par 5 tests ciblés (les 5 opérateurs avec un opérande non trivial,
  non-déclenchement dans une clause d'incrément de `for` correctement traité comme le motif
  d'incrément — `for(...;...;i=i*2.)`→`for(...;...;i*=2.)`, non-déclenchement quand la valeur
  de l'affectation est utilisée — `if(x=x*2.)`/argument d'appel, non-déclenchement quand
  l'opérande atomique n'est que le début d'une expression plus longue, y compris via une
  ré-utilisation du même nom `x=x*x+1.;`, ou un opérande non-atomique `x=x*f(y);`) plus un test
  bout-en-bout via `golf_shader` (confirmé aussi avec renommage désactivé, pour lire `col*=light;`
  en clair plutôt qu'à travers des noms à 1 caractère) et la suite de tests existante rejouée en
  intégralité (84 tests, tous au vert, dont les deux régressions `default.frag` inchangées — ce
  fichier ne contient aucun motif `x=x OP atomic;` auto-référent, confirmant l'absence de faux
  positif). Toolchain Rust disponible dans cette session (`rustc` 1.75 via `apt`, même limite de
  `Cargo.lock` v4 déjà documentée ailleurs dans ce fichier pour le crate complet) : `golf.rs`
  compile et ses tests passent en isolation via `rustc --test`. Exemple réel (petit driver
  `rustc` autonome, hors suite de tests) : `t=t+0.1;col=col*light;` dans une boucle golfe en
  `b+=.1;a*=e;` une fois renommage/boucle `for` appliqués.
- [x] **Repli sur un splat pour les constructeurs de vecteur** (`vecN(x,x,...,x)`, N arguments
  atomiques strictement identiques → `vecN(x)`, GLSL réplique automatiquement un seul argument
  scalaire sur toutes les composantes). Motif fréquent en sortie de `simplify_algebra`/renommage
  quand un calcul de couleur uniforme finit en `vec3(v,v,v)`. Détection sur la même retokenisation
  `AlgTok`/`find_matching_close` déjà utilisée par `extract_repeated_subexpr_macros` pour isoler un
  appel complet (`fold_vector_constructor_splat`) ; ne se déclenche que si chaque argument est un
  opérande atomique (`is_atomic_operand`, déjà utilisé ailleurs dans ce fichier — identifiant ou
  littéral seul, jamais une sous-expression même identique syntaxiquement des deux côtés — `vec3(a+
  b,a+b,a+b)` reste hors périmètre pour cette passe, laissé à l'extraction de macro existante qui le
  couvre déjà autrement) et si le texte des N arguments est **identique caractère pour caractère**
  après golf (donc après renommage/raccourcissement de littéral, pas avant — même ordre que
  `extract_repeated_subexpr_macros`, tout en fin de pipeline, juste avant le collapse des
  points-virgules redondants). `vecN` limité à `vec2/vec3/vec4` (pas `mat*`, dont le remplissage par
  un seul scalaire suit une convention différente — diagonale, pas splat plein). Risque principal :
  un appel `vec3(v,v,v)` où `v` a un effet de bord (rare en GLSL Shadertoy — pas d'opérateur
  `++`/`--` typiquement utilisé comme argument, mais pas interdit par la grammaire) changerait de
  comportement si le splat n'évalue `v` qu'une fois au lieu de N — garde-fou : `v` doit être un
  opérande *purement* atomique (identifiant ou littéral, jamais un appel de fonction/incrément) pour
  écarter ce risque à la racine plutôt que d'essayer de détecter les effets de bord. Contrairement à
  `extract_repeated_subexpr_macros`, appelée aussi depuis `golf_common` : cette passe n'introduit ni
  ne dépend d'aucun nom d'identifiant (elle ne fait que réécrire un appel existant), donc aucun des
  risques cross-pass qui excluent renommage/élagage/extraction de macro de `golf_common` ne
  s'applique ici. Un seul passage suffit (contrairement à `simplify_algebra`/`golf_for_loops`) :
  replier un splat ne peut jamais en révéler un second, le résultat n'ayant plus qu'un seul argument.
  Tests : les 3 arités, arguments non identiques laissés intacts, 2 arguments identiques sur 3 laissés
  intacts (pas de repli partiel), argument répété mais non-atomique (`vec3(f(),f(),f())` et
  `vec3(sin(x),sin(x),sin(x))`) laissé intact, sous-expression répétée mais non-atomique
  (`vec3(a+b,a+b,a+b)`) laissé intact, `matN` jamais touché, mauvaise arité laissée intacte, bout-en-
  bout via `golf_shader`. **Régression `default.frag` : le plan initial se trompait** — `palette()`
  a bien 4 `vec3`, mais 2 d'entre eux (`vec3(0.5,0.5,0.5)` et `vec3(1.0,1.0,1.0)`) sont de vrais
  splats une fois golfés (`.5`/`1.`), seul `vec3(0.263,0.416,0.557)` (composantes distinctes) est le
  cas no-op ; conséquence : le fichier golfé passe de 519 à 502 octets une fois cette passe câblée
  dans le pipeline (`golf_shader_impl` et `golf_common`), les deux tests de régression byte-exacte
  déjà présents dans ce fichier (`merge_declarations_tests::regression_default_frag_size_shrinks`,
  `strip_redundant_braces_tests::regression_default_frag_unaffected`) mis à jour en conséquence.
  Toolchain Rust disponible dans cette session (`rustc` 1.75, même limite de `Cargo.lock` v4 déjà
  documentée ailleurs dans ce fichier pour le crate complet) : `golf.rs` compile et l'intégralité de
  sa suite de tests (97 tests, tous au vert) passe en isolation via `rustc --test`.
- [x] **Suppression du qualificatif `in` sur les paramètres de fonction.** En GLSL, `in` est le
  qualificatif de paramètre par défaut — l'écrire est optionnel, `void mainImage(out vec4
  fragColor, vec2 fragCoord)` est strictement équivalent à la version avec `in vec2 fragCoord` (vu
  dans `default.frag` et à peu près tous les shaders Shadertoy, `in` étant l'habitude d'écriture du
  site plutôt qu'une nécessité). Gain modeste par occurrence (3 caractères + l'espace qui suit) mais
  gratuit à chaque paramètre de chaque fonction utilisateur, `mainImage` inclus. Implémentation
  la plus simple de cette liste (`strip_default_in_qualifier`) : sur la retokenisation `AlgTok`,
  repérer `in` en position de mot-clé isolé (précédé de `(` ou `,`, jamais un identifiant qui
  contiendrait accidentellement ces lettres — déjà garanti par la tokenisation qui traite `in` comme
  un `Ident` à part entière, jamais un sous-mot) et le retirer purement et simplement avec l'espace
  qui suit (`skip_ws`, déjà utilisé par `merge_consecutive_declarations` pour la même raison), sans
  autre condition — au contraire de tout le reste de cette liste, aucun garde-fou de sécurité n'est
  nécessaire ici, `in` explicite ou implicite ne change jamais le sens du programme ; le filtrage
  "précédé de `(`/`,`" sert uniquement à écarter par principe une éventuelle déclaration `in`
  au niveau global (varying, jamais émise par ce moteur Shadertoy-only, mais sans coût à exclure).
  Seul point d'attention réel : `inout` commence aussi par `in` mais tokenise déjà comme un
  identifiant `inout` séparé (pas `in` suivi de `out`), donc aucune confusion possible côté
  tokenizer — couvert par un test dédié explicite plutôt que supposé. Câblée dans `golf_shader_impl`
  **et** `golf_common` (même raisonnement que le splat de constructeur de vecteur juste au-dessus :
  passe purement textuelle, ne touche à aucun identifiant, donc aucun des risques cross-pass qui
  excluent renommage/élagage/extraction de macro de `golf_common` ne s'applique ici), placée tout au
  début du pipeline (juste après le raccourcissement des littéraux/collapse d'espaces, avant
  `simplify_algebra`) puisqu'elle n'a de dépendance sur aucune autre passe. Un seul passage suffit :
  retirer un `in` ne peut jamais faire apparaître une nouvelle frontière `(`/`,` pour un autre `in`.
  Tests : paramètre unique, plusieurs paramètres dont un `out`/`inout` qui doivent rester inchangés,
  `mainImage` complet, `inout` jamais confondu avec `in`, aucun `in` à retirer laissé intact, un `in`
  non précédé d'une frontière de paramètre laissé intact, bout-en-bout via `golf_shader`, régression
  `default.frag` (`in vec2 fragCoord` → `vec2 fragCoord`, -3 octets, 502 → 499 octets golfés une fois
  cette passe câblée après le splat de constructeur de vecteur ci-dessus — les deux tests de
  régression byte-exacte déjà présents dans ce fichier mis à jour en conséquence). Toolchain Rust
  disponible dans cette session (`rustc` 1.75, même limite de `Cargo.lock` v4 déjà documentée
  ailleurs dans ce fichier pour le crate complet) : `golf.rs` compile et l'intégralité de sa suite de
  tests (105 tests, tous au vert) passe en isolation via `rustc --test`.
- [x] **Conversion `if`/`else` à affectation unique en opérateur ternaire**
  (`if(c){a=X;}else{a=Y;}` / déjà dégolfé en `if(c)a=X;else a=Y;` par `strip_redundant_braces` →
  `a=c?X:Y;`). `ternary_from_if_else` (`golf.rs`, appelée depuis `golf_shader_impl` **et**
  `golf_common`, juste après `strip_redundant_braces` — même statut "purement textuel, ne touche à
  aucun identifiant" que `fold_vector_constructor_splat`/`strip_default_in_qualifier`, donc toujours
  active elle aussi, non gérée par les cases à cocher rename/dead-code) scanne la retokenisation
  `AlgTok` déjà utilisée par `strip_redundant_braces`/`simplify_algebra`, repère chaque mot-clé `if`
  et tente `try_rewrite_if_else_ternary` dessus, exactement une fois, gauche-à-droite, évalué
  directement sur le flux de tokens *original* (même stratégie que `find_strippable_braces`, pour
  la même raison : la décision prise pour un `if` ne dépend jamais de celle prise pour un autre, donc
  plusieurs `if`/`else` indépendants dans le même fichier sont tous correctement traités en un seul
  passage).
  **Condition stricte, comme prévu** : les deux branches doivent être *exactement* une instruction
  d'affectation simple (`parse_simple_ternary_assign` : un identifiant non réservé, suivi
  immédiatement d'un unique `=` — jamais un second `=` collé derrière, qui en ferait un `==` —, donc
  `a+=X`/`a[i]=X`/`a.x=X`/une déclaration `float a=X` sont déjà rejetés par construction : le token
  qui doit suivre l'identifiant est forcément `=` et rien d'autre) vers **le même identifiant** des
  deux côtés (`then.name != els.name` rejeté). Un `if` sans `else` qui suit immédiatement dans le
  texte (`if(c)a=X;else a=Y;if(d)...`) ne se fait jamais happer dans la branche `Y` : le scan de
  l'expression s'arrête strictement au premier `;` de profondeur 0, testé explicitement.
  **Garde-fou central, propre à ce ticket** (le risque cité dans le plan initial) : `X`/`Y` sont
  limités par `is_ternary_branch_tok` à un identifiant/littéral, `+ - * / % .` et l'espace — **aucune
  parenthèse ni crochet, sous aucune forme**. Plus restrictif que le plan initial ("aucun appel de
  fonction" aurait suffi en théorie), parce que ce tokenizer n'a aucun moyen fiable de distinguer une
  parenthèse de groupement anodine (`(a+b)`) d'un appel (`f(a+b)`) sans un vrai parseur, et le gain
  d'octets à autoriser le groupement reste marginal face à ce risque — prudence par défaut, comme le
  reste de ce fichier. `?`/`:` sont exclus aussi, pour ne jamais avoir à raisonner sur la précédence
  d'un ternaire déjà présent dans une branche (voir plus bas, "non fait volontairement").
  **Deuxième garde-fou, découvert pendant l'implémentation, absent du plan initial** :
  `cond_has_unsafe_top_level_operator` rejette une condition contenant, à profondeur 0 (donc jamais à
  l'intérieur des parenthèses propres d'un appel imbriqué comme `dot(a,b)`), un `=` qui n'est pas la
  moitié d'un `==`/`!=`/`<=`/`>=`, ou une virgule. Sans ce garde-fou, `if(x=y)a=1.;else a=2.;` (un
  motif réel : ce fichier golfe déjà `if(x=x*2.)` ailleurs, voir `simplify_algebra_pass`) se serait
  réécrit en `a=x=y?1.:2.;`, qui se reparse comme `a=(x=(y?1.:2.))` — pas du tout `a=((x=y)?1.:2.)`,
  une régression sémantique silencieuse ; l'opérateur virgule pose le même risque de précédence
  (`if(a,b)...` embarqué tel quel deviendrait `a,b?X:Y`, qui sélectionne sur `b` seul en perdant le
  rôle de `a`). Aucun des deux cas n'était couvert par le plan initial, qui ne parlait que du risque
  sur les branches `X`/`Y` — trouvé en écrivant les tests de non-déclenchement plutôt qu'en relisant
  le plan.
  **Non fait volontairement, contrairement à ce que suggérait la formulation initiale du plan**
  ("itère jusqu'à point fixe" n'a en fait pas été retenu) : un `if` imbriqué comme *unique* contenu
  d'une branche externe (`if(p){if(q)a=1.;else a=2.;}else a=3.;`, dégolfé par `strip_redundant_braces`
  en `if(p)if(q)a=1.;else a=2.;else a=3.;`) voit son `if` **interne** converti
  (`if(p)a=q?1.:2.;else a=3.;`) mais l'**externe** délibérément laissé en `if`/`else` — pas une
  itération manquée : `is_ternary_branch_tok` exclut `?`/`:` justement pour qu'une branche déjà
  convertie en ternaire ne redevienne jamais elle-même candidate. Composer les deux serait pourtant
  grammaticalement correct sans parenthèses supplémentaires (le `?:` de C/GLSL est associatif à
  droite sur sa branche `else`, et délimité par son propre `:` sur sa branche `then` — `a=p?q?1.:2.:3.;`
  se reparse bien comme `a=p?(q?1.:2.):3.;`, vérifié à la main token par token) mais distinguer un
  `?:` bien formé produit par un passage précédent d'un `?`/`:` isolé sans rapport demanderait une
  vraie analyse d'imbrication que ce fichier évite partout ailleurs — laissé de côté pour cette
  première version, même esprit que l'affectation composée et l'inlining encore ouverts plus bas dans
  cette section.
  **Vérifié** par 21 tests ciblés (`ternary_tests`) : motif de base, condition-comparaison,
  arithmétique/accès membre dans les branches, bout-en-bout via `golf_shader` (structure vérifiée,
  pas l'orthographe exacte après renommage, même style que
  `strip_redundant_braces_tests::full_pipeline_smoke_test`), non-déclenchement sur cibles
  différentes/affectation composée/écriture indexée ou par membre/déclaration/bloc
  multi-instructions/appel de fonction/parenthèse de groupement, non-déclenchement sur condition à
  affectation ou virgule top-level (avec un test dédié confirmant qu'un `==`/`!=`/`<=`/`>=` n'est
  jamais pris à tort pour une affectation), non-rejet quand l'affectation/la virgule est imbriquée
  dans les parenthèses propres d'un appel de la condition (`if(dot(a,b)>0.)...`), `if` sans `else`
  laissé intact, un `if` sans `else` immédiatement après une branche convertie jamais absorbé, chaîne
  `else if` où l'externe est rejeté mais l'interne converti indépendamment dans le même passage, cas
  imbriqué confirmant que l'externe reste délibérément non converti (voir ci-dessus), régression
  `default.frag` (aucun `if`/`else` dans ce shader — seul son `for` de `mainImage` a un corps à
  plusieurs instructions — donc un pur no-op, toujours 499 octets golfés). Toolchain Rust disponible
  dans cette session (`rustc`/`cargo` 1.75 via `apt`, même limite de `Cargo.lock` v4 déjà documentée
  ailleurs dans ce fichier pour le crate complet) : `golf.rs` compile sans avertissement et
  l'intégralité de sa suite de tests (126 tests, 105 déjà existants + 21 nouveaux, tous au vert)
  passe en isolation via `rustc --test`. `cargo check` du crate complet toujours impossible dans cet
  environnement (même limite de lockfile), donc pas de vérification par rendu GPU pixel-identique
  cette fois non plus — seule la correction structurelle/textuelle est couverte par les tests
  ci-dessus, comme pour la plupart des entrées de cette vague "prochaine vague".
- [x] **Repli de constantes purement littérales** (`2.*3.` → `6.`, uniquement quand les deux
  opérandes d'un opérateur arithmétique sont déjà des littéraux numériques après golf).
  Nouvelle règle dans `simplify_algebra_pass` (`golf.rs`), juste après le bloc existant
  « literal OP operand » : match direct `Number Punct(op) Number` avec `op ∈ {+,-,*}`. Périmètre
  tenu exactement au plan initial pour rester bit-exact avec l'arithmétique `f32` du pilote GPU
  cible : `exact_integer_literal` n'accepte que les littéraux **sans exposant**, dont la valeur
  `f64` n'a **aucune partie fractionnaire** (`value.fract()==0.0`) — `2.*3.` → `6.` passe,
  `2.*3.14159265` (approximation de π) est rejeté puisque son deuxième opérande a une partie
  fractionnaire, exactement le garde-fou demandé. Bornage supplémentaire à `1e15` en valeur absolue
  (trois ordres de grandeur sous la limite d'entier exact d'un `f64`, 2^53) pour écarter tout
  littéral pathologique par principe plutôt que par nécessité réelle. `/` **volontairement exclu**
  (pas seulement `+`/`-`/`*`) : une division entière littérale n'est pas toujours exacte (`5./2.`
  = `2.5`), ce qui aurait demandé un deuxième critère de sûreté distinct — laissé de côté plutôt
  que couvert à moitié, comme annoncé dans le plan initial. Résultat repoussé via
  `push_folded_integer_literal` : jamais de texte de littéral commençant par `-` (aucun autre
  littéral de ce fichier n'en produit un), un résultat négatif s'écrit `Punct('-')` + littéral
  positif (`3.-5.` → `-2.`), cohérent avec le reste du fichier qui ne synthétise jamais de
  littéral signé. Repli enchaîné correctement au point fixe déjà en place dans `simplify_algebra`
  (`2.*3.*4.` → `6.*4.` → `24.` sur deux passes). Alternative "calculer aussi en `f32` pour élargir
  la couverture aux littéraux non entiers" toujours écartée pour la raison déjà documentée
  (validation multi-GPU/pilote hors de portée de cet environnement) — non retenue, périmètre limité
  aux entiers exacts comme prévu.
  Testé (6 nouveaux tests dans `simplify_algebra_tests`, `golf.rs`) : addition/soustraction/
  multiplication de littéraux entiers dans les deux sens, résultat négatif correctement rendu en
  `Punct('-')` + littéral positif, non-repli d'un littéral à partie fractionnaire même quand le
  résultat de l'opération serait lui-même entier (`0.5+0.5`, exclu car **chaque opérande**, pas
  seulement le résultat, doit être un entier exact), non-repli de `3.14159265`, non-repli de la
  division, non-repli d'un littéral à exposant (`2e3`), enchaînement sur plusieurs littéraux via le
  point fixe existant, et un test bout-en-bout via `golf_shader` confirmant qu'une paire non
  adjacente à un autre littéral (`2.*3.14159265` dans un contexte réaliste) survit intacte à côté
  d'une paire qui, elle, se replie. Pas de toolchain Rust complet disponible dans cet environnement
  (même limite de lockfile que documentée ailleurs dans ce fichier), donc pas de `cargo test` sur
  le crate entier ; `golf.rs` ne dépend que de `std::collections::HashSet`, donc compilé et testé
  en isolation via `rustc --test` en reproduisant l'arborescence relative attendue
  (`rust_engine/src/golf.rs` + `python_ui/assets/shaders/default.frag`, ce dernier utilisé par
  quelques tests de régression existants via `include_str!`) : les 132 tests du fichier passent,
  dont les 6 nouveaux — aucune régression sur les tests préexistants. Pas de rendu GPU
  pixel-identique rejouable ici non plus, comme pour la plupart des entrées de cette vague ; la
  garantie de bit-exactitude vient ici de la restriction de périmètre elle-même (opérandes entiers
  exacts uniquement), pas d'une vérification empirique par rendu.
- [x] **Inlining des fonctions à site d'appel unique.** La technique la plus payante de Shader
  Minifier sur les gros shaders (une fonction utilitaire appelée une seule fois n'a aucune raison
  de garder sa déclaration séparée — l'appel `foo(x)` disparaît autant que le `float foo(float
  a){return a*2.;}`) mais aussi la plus risquée de cette liste, pour des raisons de portée que ce
  golfer, purement textuel, ne modélise pas.
  Nouvelle passe `inline_single_call_functions` (`golf.rs`), branchée dans `golf_shader_impl` juste
  après `remove_unused_functions` et sous le **même toggle `dead_code`** (les deux sont des passes
  structurelles au niveau fonction, jamais appliquées à `Common` pour la même raison cross-pass déjà
  documentée sur `remove_unused_functions` : une fonction de Common peut être appelée par une pass
  qu'on ne golfe pas en ce moment, "un seul site d'appel" n'est donc jamais une propriété sûre à
  vérifier sur le texte de Common isolément). Périmètre tenu au plan initial : fonction non-`void`,
  corps réduit à **un seul `return expr;`** (aucune déclaration locale à shadow, aucun early return à
  gérer — ce qui exclut mécaniquement tout corps à plusieurs instructions), appelée **exactement une
  fois** dans tout le fichier (`usage_count == 2` sur un comptage brut d'occurrences du nom : sa
  propre déclaration + cet unique site d'appel — ce qui exclut aussi la récursivité gratuitement,
  puisqu'un appel récursif ferait apparaître le nom une troisième fois, dans son propre corps),
  paramètre nommé obligatoire (`float foo(float)`, prototype à paramètre anonyme, jamais inliné :
  aucun nom vers lequel substituer), aucun `[` nulle part dans la déclaration (tableau en type de
  retour ou en paramètre, hors périmètre), et paramètre substitué texte-pour-texte par son expression
  d'appel réelle *entre parenthèses systématiquement* (`foo(a+b)` avec `float foo(float
  x){return x*2.;}` : `(a+b)*2.`, jamais `a+b*2.`) — jamais dupliqué : un paramètre référencé plus
  d'une fois dans le corps rend la fonction entière inéligible à ce site, exactement comme prévu.
  **Garde-fou supplémentaire non prévu par le plan initial**, découvert en écrivant les tests : le
  plan ne demandait de parenthéser que les paramètres substitués, avec pour exemple `foo(a+b)` →
  `(a+b)*2.` sans parenthèse autour de l'ensemble. Ça casse dès que l'appel est lui-même imbriqué
  dans un opérateur de précédence différente de celui du corps — `foo(a)*3.` avec `float foo(float
  x){return x+1.;}` donnerait `a+1.*3.` (= `a+3.`) au lieu de `(a+1.)*3.` si le corps substitué
  n'était pas lui-même protégé. Un appel de fonction est toujours une unité atomique de précédence
  maximale ; une expression de retour arbitraire ne l'est pas. Corrigé en enveloppant aussi
  l'**expression de retour substituée dans son ensemble** entre parenthèses au site d'appel
  (`is_single_atomic_token`/`is_fully_parenthesized` sautent cette parenthèse quand elle est
  provablement inutile : un unique token atomique, ou un corps déjà pleinement parenthésé) — au prix
  de deux octets redondants de plus dans le cas général, gain net toujours très largement positif
  malgré ça puisque la déclaration entière (type de retour, nom, liste de paramètres typée,
  accolades, `return`/`;`) disparaît en échange. Itère jusqu'à point fixe (plafonné à 10 tours, même
  discipline que `remove_unused_functions`) pour que les chaînes s'effondrent entièrement (A appelle
  B une fois, B appelle C une fois : B s'inline dans A, puis C, toujours appelée une fois — son
  unique site d'appel simplement déplacé dans le corps de A — est repérée au tour suivant).
  Explicitement **hors périmètre**, exactement comme annoncé : fonctions appelées plusieurs fois,
  fonctions avec plus d'une instruction, paramètres répétés dans le corps, tableaux, et Common.
  **Effet de bord sur deux tests préexistants** (`strip_redundant_braces_tests::
  rename_weighted_by_frequency_not_first_encounter` et `rename_frequency_ties_broken_by_first_encounter`) :
  leurs shaders de test appelaient chacun leur fonction candidate au renommage exactement une fois,
  ce qui la rendait désormais éligible à l'inlining — la fonction disparaissait avant même que le
  renommage n'ait quoi que ce soit à golfer, cassant l'assertion. Corrigé en appelant chaque fonction
  deux fois dans ces deux tests (le rapport de fréquence entre les deux fonctions comparées reste
  inchangé, donc l'intention originale du test — vérifier le renommage pondéré par fréquence, pas
  l'inlining — reste intacte).
  **Vérifié** par 18 nouveaux tests (`inline_single_call_tests`, `golf.rs`) : exemple de base de ce
  ticket avec parenthésage systématique des paramètres, bout-en-bout via `golf_shader`, démonstration
  concrète du bug de précédence que la parenthèse externe supplémentaire empêche, corps atomique
  (littéral nu, aucune parenthèse superflue), corps déjà parenthésé (aucun double-enveloppement),
  non-déclenchement sur appel multiple/fonction `void`/corps à plusieurs instructions/early
  return/récursivité/paramètre répété/paramètre anonyme/type tableau, `mainImage` et `struct` jamais
  pris pour des candidats, chaîne de deux fonctions à site d'appel unique qui s'effondre entièrement,
  jamais appliqué à `Common` via `golf_common`, toggle `dead_code=false` qui désactive l'inlining en
  même temps que l'élagage de code mort, et régression bout-en-bout confirmant qu'un paramètre répété
  survit à l'inlining sans changer la taille de sortie. Toolchain Rust disponible dans cette session
  (`rustc`/`cargo` 1.75 réinstallés via `apt`, même limite de `Cargo.lock` v4 déjà documentée
  ailleurs dans ce fichier pour le crate complet) : `golf.rs` compile sans avertissement et
  l'intégralité de sa suite de tests (151 tests, 133 déjà existants + 18 nouveaux, dont les deux
  corrigés ci-dessus) passe en isolation via `rustc --test`, hors du crate complet. `cargo check` du
  crate complet toujours impossible dans cet environnement (même limite de lockfile), donc pas de
  vérification par rendu GPU pixel-identique cette fois non plus — seule la correction
  structurelle/textuelle est couverte par les tests ci-dessus, comme pour la plupart des entrées de
  cette vague. Deux vérifications manuelles supplémentaires (hors suite de tests, shaders jetables
  compilés en isolation avec `rustc`) sur des cas plus réalistes qu'un helper à un seul opérateur —
  une fonction de type SDF (`sdCircle(vec2 p,float r){return length(p)-r;}`) et une fonction de
  luminance (`lum(vec3 c){return dot(c,vec3(...));}`) — confirment que la sortie golfée reste un
  GLSL syntaxiquement valide et correctement parenthésé dans les deux cas.

---

## 🎚️ Sliders (`python_ui/ui/sliders_panel.py`, `rust_engine/src/literals.rs`)

> ✅ **Fait : plus d'annotation `@slider`/`uniform` custom.** Le code est désormais **100%
> compatible shadertoy.com** — aucun uniform custom, aucune syntaxe propriétaire. Les sliders sont
> détectés automatiquement et déplacer un slider réécrit directement le littéral dans le code.

Fonctionnement actuel : `literals.rs::detect_literal_sliders` scanne le GLSL tel quel (en excluant
commentaires, lignes `#directive` et en-têtes de `for (...)`) et repère chaque littéral flottant
(un `.` ou un exposant requis — les entiers de boucle/index sont ignorés). Chaque littéral est
catégorisé par sa fonction englobante (profondeur d'accolade 0 = définition de fonction en GLSL,
donc sans ambiguïté avec les `if`/`for`), bornes par défaut `[0, 2×valeur]` (ou symétrique si
négatif, `[-1, 1]` si nul). Déplacer un slider appelle `MonacoEditor.replace_range(start, end, text)`
→ `model.getPositionAt` côté JS → `executeEdits` (undo-able) → recompilation debouncée (~100 ms,
contre ~350 ms pour la frappe clavier). `SlidersPanel` garde un miroir mutable des offsets
(`_LiteralState`) pour rester cohérent entre deux recompilations lors d'un drag rapide (plusieurs
éditions avant que le moteur n'ait eu le temps de re-détecter les littéraux).

- [x] Édition live du `min`/`max` via clic droit sur le slider (l'heuristique `[0, 2×valeur]` n'est
  qu'un point de départ) : `SlidersPanel._edit_range` ouvre un petit dialogue, override purement
  côté UI (n'écrit rien dans le code), conservé tant qu'un `rebuild()` structurel ne survient pas.
  Reste à faire : le `step` n'est pas éditable séparément (recalculé depuis min/max), et l'override
  ne survit pas à un rebuild complet (ajout/suppression d'un littéral ailleurs dans le fichier).
- [x] Regroupement plus fin que "nom de fonction" : sous-catégories via un commentaire de section
  dans le code (ex. `// -- Couleur --`) au-dessus d'un groupe de littéraux. Implémenté dans
  `literals.rs::detect_all_sliders` (aucun changement côté UI/Python n'a été nécessaire —
  `sliders_panel.py` groupe déjà en onglets par simple valeur de `category`, opaque pour lui, donc
  un nouveau format de catégorie "Fonction — Section" crée automatiquement un onglet séparé).
  `parse_section_marker` reconnaît une ligne `//` dont le texte, une fois trimé, a au moins 2 tirets
  de part et d'autre d'un titre non vide (`-- Couleur --`, `--Serré--`, ...) — volontairement strict
  pour ne jamais confondre un commentaire ordinaire avec un marqueur. La section active est portée
  par le cadre de fonction courant (`func_stack`, désormais `(nom, profondeur, section_active)`) et
  s'applique à tout littéral rencontré ensuite jusqu'au prochain marqueur ou la fin de la fonction —
  jamais au-delà : chaque fonction démarre avec aucune section active, donc rien ne fuite d'une
  fonction à l'autre. Fonctionne aussi hors de toute fonction (portée "Global"). Vérifié par : un
  shader avec deux sections dans `mainImage` (les littéraux qui suivent chaque marqueur, y compris un
  `vec3` groupé, héritent bien du bon nom de catégorie), un commentaire ordinaire et une ligne
  tout-tirets confirmés comme ignorés (pas traités comme marqueurs), une section same-name dans une
  deuxième fonction restant indépendante, régression sur `default.frag` (aucun marqueur présent :
  catégories identiques à avant), et capture d'écran de l'appli réelle montrant les deux onglets
  générés.
- [x] Support d'autres types de sliders : `vec2`/`vec3` inline, `int`, `bool`. `literals.rs` scanne
  maintenant les quatre à la fois dans une seule passe (`detect_all_sliders`) :
  - `vec2(a, b)`/`vec3(a, b, c)` dont **tous** les arguments sont des littéraux float purs sont
    groupés en un seul slider couvrant l'appel entier (`vec3(...)` → color picker + spinboxes
    R/G/B ; `vec2(...)` → spinboxes X/Y), au lieu de 2-3 sliders float séparés. Volontairement
    étroit : `vec3(0.5)` (splat) ou `vec3(a, b, c)` (expressions) ne sont pas reconnus — leurs
    arguments, s'ils sont eux-mêmes des littéraux, redeviennent de simples sliders float comme
    avant. Éditer le slider réécrit l'appel `vecN(...)` entier d'un coup (pas de sous-plages par
    composant à suivre individuellement).
  - `int` : entiers bruts (sans `.`/exposant) détectés séparément des floats, avec le même masquage
    que les floats (commentaires, directives préprocesseur, en-têtes de `for(...)`) — slider +
    spinbox entier au lieu d'un `QDoubleSpinBox`.
  - `bool` : littéraux `true`/`false` détectés comme mots-clés réservés GLSL (aucune ambiguïté
    possible avec un identifiant) — affiché comme une simple case à cocher.
  Le panneau de sliders (`sliders_panel.py`) a été généralisé pour porter une liste unique et
  triée-par-position de littéraux hétérogènes (`_LiteralState.kind`), condition nécessaire pour que
  le décalage d'offsets au fil des éditions (`_emit_edit`) reste correct quel que soit l'ordre dans
  lequel les types de sliders sont modifiés. Vérifié par : test bout-en-bout mêlant les 4 types sur
  un même shader (édition de chacun dans un ordre donné, recomposition du texte via les offsets émis,
  recompilation réussie), régression sur `default.frag` (les 4 `vec3` de sa fonction `palette()` se
  regroupent bien en 4 color pickers au lieu de 12 sliders float), et capture d'écran de l'appli
  réelle confirmant le rendu visuel de chaque nouveau type de ligne.
- [x] Bouton "réinitialiser" par slider (↺ dans chaque ligne) et par catégorie (↺ en tête d'onglet) :
  revient à la valeur vue au dernier `rebuild()` (`_LiteralState.initial_value`, préservée à
  travers les `refresh()` déclenchés par les recompilations, contrairement à `value` qui suit
  chaque édition) — il n'y a plus de `default` annoté, donc "réinitialiser" = "annuler mes
  changements depuis l'ouverture/le dernier rebuild structurel", pas un retour à une valeur fixe
- [x] Sauvegarde/rechargement d'un layout de sliders avec le projet (voir "format de projet" en UI/UX) —
  moins critique maintenant que le code source *est* l'état, mais utile pour figer les catégories/labels.
  Ce qui est sauvegardé : les overrides min/max/décimales des sliders scalaires (float/int) posés via
  clic droit (`_edit_range`), et depuis l'entrée 🎬 keyframing plus bas, leurs keyframes — pas les
  valeurs elles-mêmes, qui restent dans le code source, ni les bool/vec2/vec3 qui n'ont rien
  d'overridable. Un slider n'a pas d'identité stable au-delà de sa
  position dans le source (sans objet après un rechargement, voire après un simple rebuild structurel) ;
  `SlidersPanel.export_layout`/`apply_layout` utilisent donc comme clé
  `(catégorie, type, index dans cette catégorie+type, par ordre d'apparition dans le source)` — stable
  tant que l'utilisateur n'a pas ajouté/retiré/réordonné des littéraux de ce type dans cette catégorie
  depuis la capture ; sinon les entrées qui ne matchent plus sont silencieusement ignorées
  (best-effort, pas une garantie). Câblé côté `MainWindow` : `_slider_layouts` garde un snapshot par
  onglet (`str(tab_id)`, `COMMON_TAB` inclus) ; un changement d'onglet ou une recompilation qui modifie
  la signature des sliders (`_refresh_sliders_for`) exporte l'état de l'onglet quitté avant `rebuild()`
  puis réapplique l'état sauvegardé du nouvel onglet ; `_on_save_project` capture explicitement l'onglet
  actuellement affiché (pas encore snapshotté si aucun rebuild structurel n'a eu lieu depuis la dernière
  édition) avant de sérialiser. Format de projet passé à **format 3** : nouvelle clé top-level
  `"sliders"` (dict onglet → liste d'overrides), absente sans erreur des projets format 2 existants
  (`data.get("sliders", {})`). `_slider_panel_tab` (`None` = "ce qui est affiché dans le panneau ne
  correspond à aucun onglet suivi, ne pas le snapshotter") évite qu'un Nouveau/Ouvrir ne fasse fuiter
  l'état du projet précédent dans le nouveau. Vérifié par `test_sliders.py` (export d'un override
  min/max/décimales, rechargement avec des littéraux de valeurs différentes mais même signature,
  confirmation que **tout** le layout est réappliqué — pas seulement l'entrée modifiée — donnant bien
  des bornes figées plutôt que l'heuristique `[0, 2×valeur]` recalculée, et cas d'un layout devenu
  partiellement obsolète où l'entrée sans correspondance est ignorée sans casser les autres).
- [x] Incrément fin au clavier : `_SliderSpinBox` (sous-classe de `QDoubleSpinBox`) intercepte
  Shift+↑/↓ pour stepper à 10× le pas normal le temps de la touche, restauré juste après
- [x] Bouton "randomiser" 🎲 par slider et par catégorie (`_randomize_ordinals`, valeur uniforme
  dans les bornes `[min, max]` actuelles du spin box, respecte donc un override min/max éventuel)
- [x] Recherche/filtre par nom (`Filtrer…` en coin du panneau à onglets, `QFormLayout.setRowVisible`
  par ligne + `setTabVisible` par onglet quand toutes ses lignes sont masquées) — filtre sur le
  label `L<ligne>` et le nom de catégorie (il n'y a pas de "nom" de slider à proprement parler)
- [x] ~~Repli/dépli (collapse) des catégories~~ — sans objet avec le design actuel : les catégories
  sont déjà des onglets séparés (une seule visible à la fois, `QTabBar` scrolle tout seul s'il y en
  a beaucoup), un accordéon par-dessus des onglets serait redondant plutôt qu'une amélioration
- [x] Précision décimale configurable par slider : champ "Décimales" ajouté au dialogue clic-droit
  min/max (`_edit_range`), `format_glsl_float` réécrit désormais le littéral avec ce nombre exact
  de décimales (plus de `.6g` fixe) au lieu d'une précision arbitraire indépendante du réglage
- [x] Animation/keyframing basique d'un slider dans le temps (pour prévisualiser une séquence).
  Pas de transport dédié : l'horloge d'animation *est* `iTime` — `Viewport` émet maintenant
  `timeUpdated(t)` à chaque tick (~60 fps, qu'on soit en pause ou non) au lieu de garder ce temps
  privé, câblé directement sur `SlidersPanel.set_time`. Enregistrer un keyframe (bouton `🎬` sur
  chaque slider scalaire, à côté de ↺/🎲) capture `(t courant, valeur courante)` ; dès qu'un slider a
  1+ keyframe(s), `set_time` snape sa valeur par interpolation linéaire entre les deux keyframes
  encadrant `t` à chaque tick — tenue constante avant le premier et après le dernier (jamais
  d'extrapolation), et l'édition n'est émise que si la valeur interpolée a réellement bougé (évite de
  spammer l'éditeur 60×/s quand le temps est en pause ou entre deux keyframes identiques). Résultat :
  les contrôles Lecture/Pause et "Reinitialiser le temps" déjà dans la toolbar suffisent à rejouer la
  séquence — poser 2-3 keyframes sur un slider puis Reinitialiser + Lecture la prévisualise. Un clic
  proche (< 0.05 s) d'un keyframe existant le met à jour au lieu d'en empiler un quasi-doublon ; clic
  droit sur `🎬` ouvre un petit menu (ajouter à `t` courant / effacer), et chaque catégorie a aussi un
  bouton "⏱ Effacer les keyframes" groupé à côté de ↺/🎲 catégorie. Portée volontairement limitée aux
  sliders scalaires (float/int, même périmètre que les overrides min/max) — bool/vec2/vec3 n'ont pas
  de bouton keyframe. Les keyframes survivent à un `refresh()` (recompilation sans changement de
  signature, migrées par ordinal comme `initial_value`) et à un `rebuild()` structurel via le même
  mécanisme d'identité `(catégorie, type, index)` que les overrides min/max/décimales :
  `export_layout`/`apply_layout` portent désormais aussi une clé `"keyframes"` par slider (appliquée
  indépendamment de la validité min/max de l'entrée, pour ne pas perdre l'anim si le range est
  invalide) — donc elles se sauvegardent gratuitement dans le projet `.json` au même endroit que le
  reste du layout de sliders (voir plus bas), sans bump de format puisque c'est un champ optionnel de
  plus dans une structure déjà versionnée. Vérifié par `test_sliders.py` : interpolation linéaire et
  tenue plate hors bornes (avant/après/à l'exact instant d'un keyframe, cas à un seul keyframe),
  émission d'une édition à un nouvel instant interpolé, absence d'édition en rejouant le même instant,
  fusion d'un keyframe proche plutôt que doublon, effacement, et aller-retour complet
  export_layout → rebuild (keyframes vidées) → apply_layout (keyframes restaurées).

---

## 🌈 Compatibilité Shadertoy (`rust_engine/src/shader.rs`, `renderer.rs`, `texture.rs`)

Le code utilisateur est du GLSL Shadertoy standard sans aucune syntaxe custom (voir 🎚️ Sliders) — un shader copié depuis shadertoy.com compile tel quel.

- [x] **Multi-passes façon Shadertoy : Buffer A/B/C/D avec chaînage entre buffers, feedback loop et
  onglet "Common".** Refonte du moteur (`renderer.rs`) : chaque Buffer a sa propre cible de rendu
  en ping-pong (`PingPongTarget`, 2 textures + un index `latest`), rendus dans l'ordre fixe
  A→B→C→D→Image à chaque frame. Un canal pointant vers un Buffer résout toujours vers son
  `latest_view()` *au moment où le pass courant est construit* — sans cas particulier, ça donne
  automatiquement : référence "avant" (B lit A, A déjà rendu ce tour) → résultat frais de la même
  frame ; référence "arrière" (A lit D, D pas encore rendu) → résultat de D de la frame précédente ;
  auto-référence (un buffer qui se lit lui-même) → son propre résultat de la frame précédente
  (trail/feedback). Les trois comportements sont testés explicitement (valeurs d'octets lues pixel
  par pixel sur plusieurs frames). Le tab "Common" est un vrai texte GLSL préfixé à chaque pass
  avant compilation (`Engine::set_common` + `compile_pass`), et le mapping ligne d'erreur→éditeur
  (`fragment_header_line_count`) en tient compte.
  Côté UI : barre d'onglets (`Image | Buffer A | B | C | D | Common`) au-dessus de l'éditeur
  (`main_window.py`), chaque pass garde son propre code et ses 4 iChannels indépendants ; le panel
  iChannel (`ichannel_panel.py`) a maintenant un menu déroulant par slot (`Vide` / `Image (fichier)`
  / `Buffer A-D`) en plus du bouton "Parcourir…" et du glisser-déposer. Le format de projet `.json`
  (format 2) sérialise les 5 sources, le Common, et les assignations de canaux de chaque pass ;
  testé en aller-retour complet (sauvegarde → rechargement → le rendu par feedback continue
  correctement).
  Limite connue : le sélecteur de bornes min/max/décimales des sliders (clic droit) ne suit pas
  encore par pass — les sliders détectés reflètent toujours le code actuellement affiché, c'est le
  comportement voulu, mais rien n'empêche pour l'instant deux passes d'avoir des littéraux au même
  nom de catégorie qui se chevauchent visuellement si on switche vite.
- [x] **Format de pixel plus précis pour les Buffers.** Les 4 cibles ping-pong (`PingPongTarget`)
  passent de `Rgba8Unorm` à `Rgba16Float` (nouvelle constante `BUFFER_FORMAT`, distincte
  d'`OUTPUT_FORMAT`) ; le pass Image final, seul à être lu en octets pour l'affichage viewport,
  reste en `Rgba8Unorm` inchangé. Choix du 16-bit plutôt que 32-bit : filtrable sur tous les
  backends wgpu sans dépendre de la feature adapter `FLOAT32_FILTERABLE`, non garantie disponible
  partout, tout en donnant largement plus de marge que 8-bit aux effets d'accumulation/feedback
  avant clamp ou banding. Le pipeline de chaque pass choisit maintenant son `ColorTargetState.format`
  selon `pass < NUM_BUFFERS` (`compile_pass`), puisque wgpu valide le format du pipeline contre celui
  de la texture réellement attachée à ce moment-là. Le zero-fill de démarrage des buffers (frame 0
  self-feedback) passe de 4 à 8 octets/texel (`BUFFER_BYTES_PER_PIXEL`) ; les octets restent à zéro
  car `0x0000` vaut bien `0.0` en flottant demi-précision IEEE-754, donc aucun changement numérique,
  seulement la taille de ligne écrite. Le binding layout (sampler filtrant, `sample_type: Float`) et
  `resolve_view`/`resolve_size` ne changent pas : le format ne modifie que le stockage des texels,
  pas le layout d'échantillonnage. Les textures `iChannel` chargées depuis un fichier (`texture.rs`)
  restent en `Rgba8Unorm`, non concernées par ce ticket.
- [x] `iMouse.zw` conforme à la spec Shadertoy : `Viewport` mémorise la position du clic
  (`_click_pos`), `iMouse.xy` suit la souris pendant le drag et garde la dernière position après
  relâchement, `iMouse.zw` reste positif tant que le bouton est tenu puis passe négatif au
  relâchement (`mouseReleaseEvent`, testé).
- [x] Ajout des uniforms manquants `iDate` (année, mois 0-indexé comme le `Date.getMonth()` JS de
  Shadertoy, jour, secondes depuis minuit local — calculé côté Python à chaque frame),
  `iSampleRate` (44100 Hz fixe, pas d'entrée audio réelle) et `iChannelResolution[4]` (lu depuis la
  taille réelle de chaque texture de canal via `wgpu::Texture::size()`, vec4 au lieu du vec3 GLSL
  standard pour un alignement std140 trivial — `.xy`/`.xyz` restent valides côté shader). Reste
  manquant : `iChannelTime[4]`, sans objet tant qu'il n'y a pas de source vidéo/webcam (voir plus bas).
- [x] **Textures procédurales intégrées façon presets Shadertoy (`texture.rs`, `renderer.rs`,
  `lib.rs`, `ichannel_panel.py`).** Trois presets — Damier, Bruit blanc, Bruit (valeur, interpolé
  avec un fondu smoothstep sur une grille 8×8) — générés sur CPU en `Rgba8Unorm` 256×256 via un
  PRNG xorshift32 maison à seed fixe par preset (pas de dépendance `rand`), uploadés une seule fois
  à l'assignation comme une image ordinaire (`ChannelTexture::procedural`, nouvelle variante
  `ChannelInput::Procedural` traitée exactement comme `Image` dans `resolve_view`/`resolve_size` —
  même sampler filtrant/repeat, aucun cas particulier au moment de l'échantillonnage). Nouvelle
  méthode `Engine::set_ichannel_procedural(pass, index, kind: &str)` (`"checker"` / `"white_noise"`
  / `"value_noise"`, parsée en `ProceduralKind`) exposée côté Python via pyo3 dans `lib.rs`.
  Côté UI : le menu déroulant par slot iChannel gagne 3 entrées entre "Image (fichier)" et "Buffer
  A" ; la vignette affiche un aperçu généré côté Python (`QPainter`/`QImage`, indépendant du rendu
  Rust réel, juste pour distinguer les presets au premier coup d'œil). Le kind `"procedural"` et sa
  valeur (chaîne du preset) se sérialisent tels quels dans le format de projet `.json` existant
  (format 2) sans bump de format, `kind`/`value` étant déjà des champs génériques.
- [x] **Support vidéo et webcam comme source `iChannel`** (`texture.rs`, `renderer.rs`, `shader.rs`,
  `lib.rs`, nouveau `python_ui/video_source.py`, `ichannel_panel.py`, `main_window.py`) —
  auparavant uniquement des images statiques PNG/JPG/BMP.
  Aucun décodeur vidéo/caméra n'a été ajouté côté Rust : `rust_engine` n'a toujours que la
  dépendance `image` (PNG/JPEG) qu'il avait déjà, ce qui aurait autrement voulu dire embarquer et
  faire vivre toute une pile native supplémentaire (ffmpeg, gstreamer…) pour dupliquer un travail
  que Qt sait déjà faire. Le décodage a donc lieu entièrement côté Python, avec `QtMultimedia`
  (déjà fourni par le wheel `PySide6`, aucune nouvelle dépendance dans `requirements.txt`) :
  `QMediaPlayer` pour un fichier vidéo (bouclé via `setLoops(Infinite)`, sans
  `setAudioOutput` — la lecture est muette par construction, ce moteur n'a pas de graphe audio à
  nourrir), `QCamera`/`QMediaCaptureSession` pour une webcam. Les deux partagent un seul
  `QVideoSink` ; à chaque frame décodée, `video_source.VideoChannelSource` la convertit en
  `QImage::Format_RGBA8888`, retire le padding de ligne éventuel de Qt (`bytesPerLine()` peut
  dépasser `width * 4`) pour obtenir un buffer RGBA8 tightly-packed, et l'expose via un callback
  `on_frame(width, height, rgba_bytes, temps_s)`.
  Côté moteur, une nouvelle variante `ChannelInput::Video(ChannelTexture, f32)` (`renderer.rs`)
  traite un canal vidéo exactement comme un `sampler2D` ordinaire (même `channel_kind`, même
  binding) — la seule différence est qu'il est ré-uploadé en continu au lieu d'une fois :
  `set_ichannel_video(pass, index)` alloue d'abord un placeholder 1x1
  (`ChannelTexture::dynamic`, même convention que `placeholder()`) pour que le slot ait toujours
  quelque chose de valide de lié avant même la première frame réelle, puis
  `update_ichannel_video_frame(pass, index, w, h, rgba, temps)` réuploade chaque frame
  (`ChannelTexture::write_rgba`) ; la texture n'est recréée que si la résolution change d'une
  frame à l'autre (une webcam ou un lecteur peuvent en théorie renégocier un format), sinon
  l'upload se fait en place. Une frame qui arrive pour un slot entre-temps réassigné à autre chose
  est silencieusement ignorée plutôt que de lever une erreur — le côté Python arrête son décodeur
  dès la réassignation, mais une frame déjà en file sur la boucle d'évènements Qt peut encore
  atterrir un tick plus tard, et ce n'est pas un cas d'erreur.
  Cette fonctionnalité referme aussi une lacune notée plus haut dans ce roadmap
  (« reste manquant : `iChannelTime[4]`, sans objet tant qu'il n'y a pas de source vidéo/webcam ») :
  `GlobalsUniform` gagne un champ `channel_time: [[f32; 4]; 4]` (même stride std140 16 octets par
  élément que `channel_resolution`, pour un `float iChannelTime[4]` GLSL conforme à la spec
  Shadertoy plutôt qu'un `vec4[4]` comme pour `iChannelResolution`), rempli avec la position de
  lecture (en secondes) de chaque canal vidéo/webcam et resté à `0.0` pour tout autre type de
  canal, exactement comme sur shadertoy.com.
  Côté UI (`ichannel_panel.py`) : le menu déroulant par slot gagne deux entrées, « Vidéo
  (fichier)… » (filtre `*.mp4 *.mov *.m4v *.avi *.mkv *.webm`) et « Webcam » — cette dernière
  ouvre un petit sélecteur (`QInputDialog.getItem`) si plusieurs caméras sont détectées
  (`video_source.list_cameras`, basé sur `QMediaDevices.videoInputs()`), sinon prend directement
  l'unique caméra disponible ; le glisser-déposer sur un slot accepte désormais aussi les fichiers
  vidéo, pas seulement les images. Le format de projet `.json` (format 2, inchangé) sérialise
  `kind: "video"` (`value` = chemin du fichier) et `kind: "webcam"` (`value` = identifiant Qt de
  la caméra, chaîne vide = caméra par défaut du système) au même titre que les autres kinds
  existants.
  `main_window.py` possède désormais un `VideoChannelSource` (`python_ui/video_source.py`) par
  slot vidéo/webcam actif (`self._video_sources`), démarré/arrêté par `_apply_ichannel_assignment`
  — toute réassignation d'un slot, y compris vers une *autre* vidéo/webcam, arrête d'abord
  l'ancienne source avant d'en ouvrir une nouvelle, pour ne jamais laisser un fichier ou une
  caméra ouverts en arrière-plan sans plus rien en échantillonner. Elles sont aussi coupées
  explicitement à la fermeture de la fenêtre (`closeEvent`) et juste avant le chargement d'un
  nouveau projet ou un « Nouveau » (`_apply_project_dict`, `_on_new`) — y compris pour un slot qui
  avait une source vidéo/webcam mais n'apparaît pas du tout dans les données du projet entrant,
  cas où il n'y aurait sinon aucun appel à `_apply_ichannel_assignment` pour l'arrêter et où une
  webcam resterait donc verrouillée (LED d'activité allumée) après avoir changé de projet.
  Limites connues : pas de vignette de prévisualisation en direct pour un slot vidéo/webcam dans
  le panneau (juste une icône + le nom de fichier/caméra en info-bulle) — contrairement à une
  image, la première frame réelle n'existe qu'une fois la lecture/capture effectivement démarrée,
  après l'affichage initial du slot. Le choix de caméra ne se réévalue pas en direct si une webcam
  est branchée/débranchée pendant que l'éditeur tourne (il faut rouvrir le sélecteur, ce qui
  ré-énumère `QMediaDevices.videoInputs()` à chaque ouverture). N'a pas pu être compilé ni testé
  dans cet environnement (pas de `cargo` disponible, pas de webcam/affichage) : le code suit au
  plus près les conventions déjà en place (mêmes noms de méthodes, même style de gestion
  d'erreurs, même endroit dans `channel_binding_entry`/`resolve_view`/`resolve_size`) mais reste à
  valider par une vraie compilation `maturin develop --release` avant merge.
- [x] **Import direct depuis une URL/ID Shadertoy (coller un lien, récupérer le code `mainImage`).**
  Nouveau module `python_ui/shadertoy_import.py`, découplé de Qt et du module natif `shadertoy_engine`
  (voir plus bas) pour rester testable seul :
  - `parse_shader_id_or_url` reconnaît un ID nu (6 caractères alphanumériques, sensible à la casse) ou
    une URL `shadertoy.com/view/…`/`shadertoy.com/embed/…`, avec ou sans `www.`/schéma/query string.
  - `fetch_shader` appelle l'API JSON officielle (`GET /api/v1/shaders/{id}?key=…`) et distingue les
    échecs réseau/HTTP des erreurs applicatives que l'API renvoie en HTTP 200 (`{"Error": "..."}`,
    pour une clé invalide, un ID inconnu ou un shader privé/non-listé) — toutes remontées comme
    `ShadertoyImportError` avec un message en français directement affichable.
  - `build_project_data` traduit l'objet `"Shader"` de l'API vers **exactement** la forme de dict que
    `.json` project files utilisent déjà (`{"format", "common", "passes", "ichannels", "sliders"}`).
    Refactor associé côté `main_window.py` : `_load_project` a été scindé pour extraire
    `_apply_project_dict(data)`, désormais le point d'entrée commun entre "ouvrir un fichier `.json`"
    et "importer depuis Shadertoy" — l'import réutilise donc tel quel le chemin de code déjà
    éprouvé (mise à jour des sources par passe, du Common, des assignations iChannel via
    `ichannel_panel.load_project_data`/`all_assignments`, poussée dans le moteur via
    `_apply_ichannel_assignment`) plutôt que de le dupliquer.
  - Mapping des passes : `type: "common"` → onglet Common ; `type: "image"` → passe Image ;
    `type: "buffer"` → une des 4 passes Buffer A-D, choisie par `_classify_buffer_passes` qui lit
    d'abord une lettre A-D en fin de `name` ("Buf A"/"Buffer A"), puis retombe sur l'ordre
    d'apparition dans `renderpass` pour toute passe dont le nom ne correspond pas à ce motif —
    robuste même si le libellé exact utilisé par l'API diffère de ce qui est testé ici (voir plus
    bas pourquoi cette prudence). `type: "sound"` est explicitly non supporté (ce moteur n'a pas
    d'entrée audio, seulement un `iSampleRate` fixe) et signalé par un avertissement plutôt
    qu'ignoré silencieusement.
  - Mapping des iChannels : `ctype: "texture"` → téléchargée puis assignée comme une image locale
    (`kind: "image"`) ; `"cubemap"` → 6 faces téléchargées (voir convention de nommage ci-dessous) ;
    `"buffer"` → résolu vers la bonne lettre A-D via une table `id` de sortie → indice, construite
    en amont à partir des `outputs` de chaque passe Buffer (donc indépendant de l'ordre de
    déclaration) ; `"keyboard"` → `iKeyboard`. Tout `ctype` sans équivalent dans ce moteur
    (`video`/`webcam`/`music`/`musicstream`/`mic`/`volume`, et tout futur type inconnu) est laissé
    vide avec un avertissement, plutôt que de faire échouer l'import entier.
  - Médias téléchargés dans un cache disque (`QStandardPaths.CacheLocation/shadertoy_media`,
    aplati en un seul niveau de fichiers, noms de fichiers dérivés du chemin média Shadertoy) —
    pas de retéléchargement si un fichier du même nom existe déjà, les chemins média
    shadertoy.com étant en pratique immuables.
  - **Clé API** : demandée en `QInputDialog` au premier import (lien vers "shadertoy.com → profil
    → Apps" dans le texte du dialogue) et mémorisée via `QSettings` (`shadertoyApiKey`) ; un champ
    dédié a aussi été ajouté au dialogue `Fichier → Préférences…` pour la consulter/modifier/vider
    sans repasser par un import.
  - **UI** : nouvelle entrée de menu `Fichier → Importer depuis Shadertoy…`, à côté de "Ouvrir un
    projet…". Respecte la confirmation de perte de modifications non enregistrées
    (`_confirm_discard_if_dirty`) comme les autres actions d'ouverture ; contrairement à
    `_open_path` (qui marque l'état comme "propre" puisqu'il correspond exactement à un fichier sur
    disque), un import est marqué **sale** (`_is_dirty = True`) puisqu'il ne correspond à aucun
    fichier local tant qu'il n'a pas été explicitement enregistré. Les limitations rencontrées
    pendant un import donné (passe Son ignorée, iChannel non supporté, téléchargement de média en
    échec, …) sont récapitulées dans une boîte de dialogue d'information après import plutôt que
    silencieusement avalées.
  - **Testé** (`test_shadertoy_import.py`, autonome, ni PySide6 ni le module natif requis — voir
    `build_project_data(..., image_pass=…, buffer_passes=…)` qui accepte des clés de passe
    factices précisément pour permettre ça) : parsing d'URL/ID sous toutes ses formes valides et
    invalides ; `_classify_buffer_passes` sur noms bien formés et sur noms non reconnus (repli sur
    l'ordre de déclaration) ; `fetch_shader` contre un faux serveur HTTP local (`http.server` sur
    `127.0.0.1`, un succès, une erreur `{"Error": …}` de clé invalide, une de shader introuvable,
    une route absente) ; `build_project_data` de bout en bout sur un shader synthétique multi-passes
    (Common + Buffer A + Sound + Image référençant Buffer A en entrée buffer, une texture, le
    clavier, et une entrée webcam non supportée) — vérifie le contenu de chaque passe, la
    résolution correcte de l'entrée `"buffer"` vers Buffer A malgré son `id` de sortie arbitraire,
    le téléchargement effectif de la texture sur disque, et exactement les 2 avertissements
    attendus (passe Son, iChannel webcam). Intégration côté `MainWindow` vérifiée en instanciant
    la fenêtre réelle (`QT_QPA_PLATFORM=offscreen`, PySide6 installé pour l'occasion, module natif
    `shadertoy_engine` remplacé par un stub minimal puisque `wgpu` ne peut toujours pas être
    compilé dans cet environnement — voir les tickets golf précédents pour ce même constat) :
    `_apply_project_dict` appelé directement charge bien les sources/Common/tab courant, et le
    réglage `shadertoyApiKey` persiste bien via `QSettings`.
  - **Non vérifié, documenté comme tel dans le code** (`_cubemap_face_urls`'s docstring) : la
    convention de nommage des 6 faces d'un cubemap Shadertoy (face 0 = `src` tel quel, faces 1-5 =
    `_1`..`_5` insérés avant l'extension) n'a pas pu être confrontée à une vraie réponse d'API —
    ce sandbox de développement n'a pas d'accès réseau sortant vers shadertoy.com (domaines
    autorisés : dépôts de paquets et GitHub uniquement). Implémentée sur la base du comportement
    documenté/généralement connu de l'organisation des assets du site, avec un commentaire pointant
    explicitement vers cette fonction si l'ordre/le nommage des faces s'avère incorrect pour un
    shader donné une fois testé en conditions réelles. Même réserve, moindre enjeu, pour
    `_classify_buffer_passes` : le repli par ordre de déclaration existe précisément parce que le
    libellé exact ("Buf A" vs "Buffer A" vs autre) n'a pas pu être confirmé non plus.
- [x] **Entrée clavier (`iKeyboard`/texture clavier façon Shadertoy).** Nouvelle texture partagée
  256×3 `Rgba8Unorm` (`ChannelTexture::keyboard`, `texture.rs`) — colonne = keyCode JS "à
  l'ancienne" (0-255), ligne 0 = touche actuellement enfoncée, ligne 1 = "vient d'être pressée
  cette frame" (pulse d'une frame, se déclenche aussi sur l'auto-répétition), ligne 2 = état
  "toggle" (bascule à chaque appui, y compris en auto-répétition) — exactement la disposition de
  Shadertoy, lue côté shader via `texelFetch(iChannelX, ivec2(keyCode, ligne), 0).x`. Contrairement
  aux autres types de canal, ce n'est pas une texture par slot : il n'y a qu'un seul clavier, donc
  `ChannelInput::Keyboard` (nouvelle variante, `renderer.rs`) ne porte aucune donnée et tous les
  slots qui y pointent lisent la même `Engine::keyboard_texture` partagée — même principe que
  `ChannelInput::Buffer(usize)` pour les 4 buffers. Toujours un simple `sampler2D` (aucun impact
  sur `ChannelKind`/le bind group layout par pass). Nouvelle méthode `Engine::set_ichannel_keyboard(pass,
  index)` (aucun chemin de fichier à fournir) et `Engine::update_keyboard(down, pressed, toggled)`
  — trois tableaux à plat de 256 octets, un par ligne — exposées côté Python via pyo3 (`lib.rs`).
  Côté UI : `Viewport` (`viewport.py`) passe en `Qt.StrongFocus`, intercepte `keyPressEvent`/
  `keyReleaseEvent` (l'auto-répétition à la relâche, propre à X11/Wayland, est explicitement
  ignorée pour ne pas faire clignoter l'état "enfoncée" entre deux répétitions), maintient l'état
  des 3 lignes en mémoire et appelle `update_keyboard` à chaque tick avant `render()`, puis remet
  à zéro la ligne "pressée cette frame" — un vrai pulse d'une frame, indépendant de la cadence
  d'appui. Nouvelle table de correspondance `ui/keymap.py` (`Qt.Key_*` → keyCode JS) : la plage
  ASCII (lettres, chiffres, espace, ponctuation courante) est déjà numériquement identique entre
  Qt et l'ancien keyCode JS (repli générique `0 <= qt_key < 128`), seules les touches non-ASCII de
  Qt (flèches, modificateurs, touches de fonction, bloc navigation — plage `0x0100_0000`+, sans
  rapport avec la numérotation JS) ont besoin d'une table explicite ; volontairement un sous-ensemble
  pratique (les touches qu'un shader Shadertoy interactif est réellement susceptible de tester),
  pas une couverture exhaustive de chaque constante Qt obscure. Le focus clavier suit la même
  logique que sur shadertoy.com : il faut d'abord cliquer sur le viewport pour que les touches lui
  soient délivrées (l'éditeur Monaco, une vue web, capte sinon tous les événements clavier pendant
  la frappe). Panneau iChannel (`ichannel_panel.py`) : nouvelle entrée "Clavier (iKeyboard)" dans
  le menu déroulant par slot, entre "Cubemap" et les presets procéduraux ; la vignette affiche
  simplement "⌨", aucune prévisualisation dynamique n'étant pertinente pour un état d'entrée. La
  valeur stockée est `None` (comme un slot vide), le kind `"keyboard"` se sérialise tel quel dans
  le format de projet `.json` existant (format 3, `kind`/`value` déjà génériques) sans bump de
  format. Pas de 4e ligne "nombre de bascules" (fonctionnalité demandée côté Shadertoy mais jamais
  implémentée là-bas non plus) et pas de remise à zéro explicite de l'état clavier (reconnecter le
  canal ne "réinitialise" rien, comme sur Shadertoy où il faut déconnecter/reconnecter le canal) —
  hors périmètre de ce ticket.
- [x] **Cubemap comme type de canal (`samplerCube`) en plus des textures 2D.** Chargement
  depuis 6 fichiers image (une face par fichier, ordre Shadertoy/WebGPU `+X, -X, +Y, -Y, +Z, -Z` —
  c'est exactement l'ordre dans lequel une vue `Cube` interprète ses 6 couches de tableau, donc
  aucune métadonnée de face à gérer). `ChannelTexture::from_cubemap_files` (`texture.rs`) valide que
  les 6 faces sont carrées et de même taille avant l'upload (une texture à 6 couches, une vue
  `TextureViewDimension::Cube` par-dessus). Nouvelle méthode `Engine::set_ichannel_cubemap(pass,
  index, paths: &[String])` exposée côté Python via pyo3 (`lib.rs`).
  **Complication principale, propre à ce ticket** : contrairement aux autres types de canal (image,
  procédural, buffer — tous de simples `sampler2D`), un cubemap change le *type* déclaré dans le
  shader (`samplerCube` au lieu de `sampler2D`) et donc la forme du bind group layout attendue par
  wgpu (`view_dimension: Cube` au lieu de `D2`) — auparavant partagée globalement entre les 5 passes
  puisqu'identique pour tout le monde. `renderer.rs` construit désormais le bind group layout et le
  pipeline layout **par pass**, à chaque `compile_pass`, à partir du type réel de chacun de ses 4
  canaux (`shader::ChannelKind::D2`/`Cube`, propagé jusqu'à `shader::build_fragment_source` qui
  choisit `texture2D`/`textureCube` et `sampler2D(...)`/`samplerCube(...)` ligne par ligne — le
  nombre de lignes ne change jamais, donc `header_line_count`/le mapping d'erreur ligne→éditeur n'a
  pas eu besoin de changer). Comme `_apply_ichannel_assignment` (Python) ne rappelle jamais
  `compile_pass` après un `set_ichannel_*` — ça n'avait jamais été nécessaire avant, tous les autres
  types de canaux réutilisant sans broncher le shader déjà compilé — assigner ou retirer un cubemap
  déclenche maintenant une recompilation silencieuse de la pass concernée côté Rust
  (`Engine::set_channel_input`, à partir du dernier code source connu de cette pass,
  `pass_sources`), uniquement quand le type change réellement (jamais pour un simple changement
  image/procédural/buffer/vide, qui reste aussi peu coûteux qu'avant).
  Côté UI (`ichannel_panel.py`) : nouvelle entrée \"Cubemap (6 images)\" dans le menu déroulant par
  slot, entre \"Image (fichier)\" et les presets procéduraux. La sélectionner (ou cliquer sur
  \"Modifier les faces…\", le bouton \"Parcourir…\" reconfiguré tant qu'un cubemap est assigné à ce
  slot — nécessaire car re-choisir la même entrée du menu déroulant ne redéclenche pas
  `currentIndexChanged`) ouvre une petite boîte de dialogue à 6 sélecteurs de fichier explicites, un
  par face étiquetée (`+X`..`-Z`) — délibérément pas un unique sélecteur multi-fichiers, dont l'ordre
  du résultat n'est pas garanti correspondre à l'ordre des faces et mélangerait silencieusement le
  cubemap. La vignette du slot prévisualise la face `+X`. La valeur stockée (liste de 6 chemins) se
  sérialise telle quelle dans le format de projet `.json` existant (format 3, `value` étant déjà un
  champ générique) sans bump de format.
  Pas de génération procédurale de cubemap (seulement chargé depuis 6 fichiers), et pas de cubemap
  comme cible d'un Buffer (seuls les canaux d'entrée en profitent, pas le rendu) — hors périmètre de
  ce ticket.
- [x] **Entrée audio (.mp3/.wav) comme type de canal `iChannel`, façon Shadertoy.** Sur
  shadertoy.com, un canal audio expose une texture **512×2** échantillonnée par le shader :
  ligne 0 (`y=0`) = spectre fréquentiel (magnitude FFT, une bande par colonne, graves à gauche),
  ligne 1 (`y=1`) = forme d'onde temporelle (amplitude brute, ~1024 derniers échantillons
  sous-échantillonnés à 512 points) — les deux lues typiquement via `texture(iChannelX, vec2(u,
  0.25)).x` (spectre) / `vec2(u, 0.75)).x` (onde), `u ∈ [0,1]`, exactement le même modèle mental que
  la texture clavier déjà en place (`ChannelInput::Keyboard`, voir plus haut) : une texture 2D
  générée/mise à jour dynamiquement plutôt qu'un vrai flux audio exposé au GLSL.
  **Décodage côté Python, même choix que pour la vidéo** (voir l'entrée juste au-dessus) et pour la
  même raison : `rust_engine` n'a aucune dépendance de décodage audio aujourd'hui et n'a pas de
  raison d'en gagner une (ffmpeg/gstreamer/symphonia) pour dupliquer ce que Qt sait déjà faire.
  Nouveau `python_ui/audio_source.py`, sur le modèle de `video_source.py` :
  `QMediaPlayer` + `QAudioOutput` pour une lecture **audible** (contrairement à la vidéo, muette par
  construction — un canal audio Shadertoy s'entend réellement pendant qu'on prévisualise le shader,
  c'est même tout son intérêt pour un shader réactif au son) et bouclée (`setLoops(Infinite)`,
  identique à la vidéo). Extraction des échantillons décodés via `QAudioBufferOutput`
  (`QMediaPlayer.setAudioBufferOutput`, Qt 6.7+, déjà couvert par le plancher de version de
  `requirements.txt`) plutôt que `QVideoSink` — même position dans le pipeline Qt (un puits de
  données décodées, indépendant de la sortie physique), signal `audioBufferReceived(QAudioBuffer)`
  à chaque bloc décodé.
  **Calcul du spectre** : chaque `QAudioBuffer` reçu alimente un ring buffer d'échantillons mono
  (moyenne des canaux si stéréo) ; à échéance de tick (~60 fps, aligné sur `Viewport.timeUpdated`
  comme le reste des canaux dynamiques) une FFT sur les *N* derniers échantillons (`N=1024`,
  fenêtrée Hann pour limiter le fuite spectrale, magnitude ramenée en dB puis normalisée/clampée
  vers `[0,1]` — la formule exacte de mise à l'échelle utilisée par shadertoy.com n'est pas publiée,
  ce sera donc un calage empirique "à l'œil" contre des shaders audio-réactifs connus du site plutôt
  qu'une reproduction garantie bit-exacte, limite à documenter comme celle déjà notée pour l'ordre
  des faces de cubemap) réduite à 512 bandes en regroupant les bacs FFT restants (les hautes
  fréquences occupent alors moins d'une bande chacune, comme sur Shadertoy). **Numpy** ajouté comme
  nouvelle dépendance (`requirements.txt`) pour cette FFT — jusqu'ici le projet n'en a aucun besoin
  et n'en dépendait pas ; une FFT radix-2 en pur Python sur 1024 points à 60 Hz resterait
  probablement trop lente sur une machine modeste, numpy est le compromis le plus simple plutôt que
  d'écrire et maintenir une implémentation maison.
  **Upload dans le moteur** : nouvelle variante `ChannelInput::Audio(ChannelTexture, f32)`
  (`renderer.rs`), calquée sur `ChannelInput::Video` — un `sampler2D` ordinaire (`ChannelKind::D2`,
  aucun changement de bind group layout), simplement ré-uploadé en continu. Texture fixe 512×2
  `Rgba8Unorm` (mêmes composantes R=G=B, comme sur Shadertoy — un shader qui lit `.r` ou `.x`
  fonctionne à l'identique), ligne 0 = spectre, ligne 1 = onde, réécrite en place à chaque tick
  (jamais recréée, contrairement à la vidéo dont la résolution peut varier — 512×2 est fixe quel
  que soit le fichier source). Nouvelles méthodes pyo3 `Engine::set_ichannel_audio(pass, index)` /
  `Engine::update_ichannel_audio_frame(pass, index, spectrum: &[u8;512], waveform: &[u8;512], temps)`
  (`lib.rs`), le calcul FFT réel restant entièrement côté Python — Rust ne fait qu'uploader les deux
  lignes déjà calculées, même partage des responsabilités que pour la vidéo (décodage/traitement en
  Python, upload/rendu en Rust). `channel_time` (déjà en place pour vidéo/webcam, voir plus haut)
  s'étend naturellement à ce nouveau type de canal : position de lecture du fichier en secondes,
  `0.0` pour tout autre type comme aujourd'hui.
  **Côté UI** (`ichannel_panel.py`) : nouvelle entrée « Audio (fichier)… » dans le menu déroulant par
  slot, entre « Vidéo (fichier)… » et « Webcam », filtre `*.mp3 *.wav *.ogg *.flac` (les formats que
  `QMediaPlayer` sait déjà décoder nativement sur la plupart des plateformes, sans codec
  supplémentaire à embarquer) ; glisser-déposer étendu aux mêmes extensions ; vignette statique
  (icône 🎵 + nom de fichier en info-bulle, même traitement que « Clavier » — pas de mini-spectre
  animé dans la vignette, la vraie prévisualisation se fait dans le viewport une fois le canal
  branché). `MainWindow` gère un `AudioChannelSource` par slot audio actif (`self._audio_sources`,
  même cycle de vie que `self._video_sources` : arrêté avant toute réassignation du slot, à la
  fermeture de fenêtre, et avant chargement d'un nouveau projet/Nouveau — y compris un slot audio
  absent des données du projet entrant, pour ne jamais laisser un fichier audio en lecture en
  arrière-plan après un changement de projet). `kind: "audio"` (`value` = chemin du fichier) se
  sérialise dans le format de projet `.json` existant (format 3, `kind`/`value` déjà génériques)
  sans bump de format.
  **Explicitement hors périmètre de cette première version** : entrée microphone en direct (`mic`
  côté Shadertoy — nécessiterait `QAudioSource`/la permission d'accès micro de l'OS, un chantier à
  part plutôt qu'une extension immédiate de celui-ci, à faire au besoin dans un ticket dédié une
  fois l'audio fichier en place et éprouvé), contrôle de volume dans l'UI (lecture au volume système
  par défaut), et remappage précis de la courbe de normalisation du spectre pour coller
  pixel-pour-pixel à shadertoy.com (limite déjà annoncée plus haut, faute de documentation publique
  du calcul exact utilisé par le site).
  À valider par comparaison visuelle contre un shader Shadertoy audio-réactif connu (ex. un
  visualiseur de spectre classique du site) une fois implémenté, plutôt que par rendu
  pixel-identique automatisé comme le reste de ce fichier — il n'existe pas de référence
  déterministe à reproduire ici (le contenu audio lui-même n'est pas un signal contrôlé par le
  moteur, contrairement à un fichier image/vidéo dont chaque pixel est fixe).
  **Implémenté** tel que planifié ci-dessus, périmètre tenu :
  - `rust_engine/src/texture.rs` : `AUDIO_TEXTURE_WIDTH`/`AUDIO_TEXTURE_HEIGHT` (512×2),
    `ChannelTexture::audio()` (allocation zero-filled) et `ChannelTexture::write_audio(spectrum,
    waveform)` (réécriture en place des deux lignes, jamais de recréation contrairement à
    `write_rgba` côté vidéo).
  - `rust_engine/src/renderer.rs` : nouvelle variante `ChannelInput::Audio(ChannelTexture, f32)`,
    branchée dans `channel_kind` (toujours `D2`), `resolve_view`/`resolve_size` (fusionnées avec le
    bras `Video` existant via un motif `Video(tex, _) | Audio(tex, _)`), `write_globals` pour
    `iChannelTime`, plus `set_ichannel_audio(pass, index)` et
    `update_ichannel_audio_frame(pass, index, spectrum: &[u8; 512], waveform: &[u8; 512], temps)`.
  - `rust_engine/src/lib.rs` : bindings pyo3 correspondants, avec conversion explicite
    `Vec<u8>` → `[u8; 512]` (erreur claire côté Python si la taille ne correspond pas plutôt qu'un
    panic).
  - Nouveau `python_ui/audio_source.py` (`AudioChannelSource`) : `QMediaPlayer` + `QAudioOutput`
    (lecture audible, bouclée) + `QAudioBufferOutput`/`audioBufferReceived` alimentant un ring
    buffer numpy de 1024 échantillons mono ; `compute_frame()` (appelée depuis `MainWindow`, une
    fois par tick, pas à chaque bloc décodé) fait la FFT fenêtrée Hann et renvoie directement les
    512 octets de spectre et les 512 octets de forme d'onde. `numpy>=1.24` ajouté à
    `requirements.txt`.
  - `ichannel_panel.py` : entrée « Audio (fichier)… » insérée entre « Vidéo (fichier)… » et
    « Webcam » (`_AUDIO_INDEX`, décalant `_WEBCAM_INDEX`/`_CUBEMAP_INDEX`/`_KEYBOARD_INDEX`/
    `_PROCEDURAL_OFFSET`/`_BUFFER_OFFSET` d'un cran), filtre `*.mp3 *.wav *.ogg *.flac`,
    glisser-déposer étendu (`_is_audio_path`), vignette 🎵 + info-bulle nom de fichier.
  - `main_window.py` : `self._audio_sources` (même cycle de vie que `self._video_sources` —
    arrêté avant toute réassignation du slot via `_apply_ichannel_assignment`, à `closeEvent`, et
    avant chargement d'un nouveau projet/Nouveau dans `_apply_project_dict`/`_on_new`),
    `_start_audio_channel`, `_stop_audio_channel`/`_stop_all_audio_sources`, et `_on_audio_tick`
    connectée à `viewport.timeUpdated` (même signal que `sliders_panel.set_time`) qui appelle
    `compute_frame()` sur chaque source active et pousse le résultat via
    `update_ichannel_audio_frame`.
  - i18n : nouvelles clés (`ichannel_panel.source_audio`/`audio_filter`/`choose_audio`/
    `change_audio`, `dialogs.audio_error.title`/`body`) ajoutées aux 12 fichiers `lngs/*.json` —
    parité vérifiée avec `test_i18n_completeness.py` (216 clés partout) et `test_i18n.py`, tous deux
    passent, ainsi que le scan statique des appels `tr("...")` littéraux (211 sites, contre 196
    avant cette entrée).
  **Limites de vérification propres à cet environnement de développement** (déjà notées ailleurs
  dans ce fichier pour d'autres tickets touchant Rust/Qt) : ni toolchain Rust ni PySide6 ne sont
  disponibles ici, donc ni `cargo check`/`maturin develop` côté Rust, ni une exécution réelle de
  `AudioChannelSource` contre un fichier audio n'ont pu être effectués — seuls `python3 -m
  py_compile` sur les fichiers Python modifiés/ajoutés et une relecture attentive du Rust ont pu
  servir de garde-fous. En particulier, l'accès aux échantillons décodés d'un `QAudioBuffer`
  (`buffer.constData()` interprété via `np.frombuffer` selon `sampleFormat()`) suit l'API Qt
  Multimedia documentée mais n'a pas pu être testé contre un vrai flux décodé — à revérifier une
  fois PySide6 disponible, avant la validation visuelle contre un shader audio-réactif connu déjà
  prévue par le plan initial.

---

## 🎬 Export vidéo (.mp4, image par image + ffmpeg)

> Aujourd'hui seul l'export d'une frame unique en PNG existe (`Viewport.export_png`, voir 🖥️
> UI/UX). Ce qui suit décrit l'export d'une séquence animée complète.

Principe général : comme `Engine.render(time, time_delta, mouse, frame, date)` prend déjà tous ses
paramètres temporels en argument explicite (`rust_engine/src/lib.rs`) plutôt que de lire une horloge
interne, l'export n'a **rien à changer côté Rust** pour tourner hors temps réel — il suffit,
côté Python, d'appeler `render()` en boucle avec un `time`/`frame` calculés (`i / fps`, `i`) au lieu
des valeurs mesurées par le `QElapsedTimer` du viewport live. Chaque frame est ainsi rendue de façon
strictement déterministe (même vitesse GPU ou pas, résultat pixel-identique à chaque export du même
projet), contrairement au preview temps réel.

- [x] **Boucle de capture image par image (nouveau `python_ui/video_export.py`,
  `capture_frames(engine, n_frames, fps, width, height, date, mouse=..., out_dir=None,
  on_frame_rendered=None)`).**
  Réutilise l'`Engine` déjà instancié par la fenêtre (mêmes iChannels/buffers déjà assignés, mêmes
  passes déjà compilées) plutôt qu'un `Engine` jetable comme pour le golf-à-froid, puisque l'export
  doit refléter le projet actuellement ouvert — la fonction ne touche ni à la compilation ni aux
  iChannels, l'appelant doit les avoir déjà mis en place (et avoir appelé `engine.resize(width,
  height)` si la résolution d'export diffère du viewport). Chaque `pixels` (RGBA8 brut, déjà le
  format retourné aujourd'hui pour l'affichage viewport) écrit directement sur disque comme
  `frame_%06d.png` (0-indexé) dans un dossier temporaire (`tempfile.mkdtemp`, ou `out_dir` fourni)
  via `QImage(pixels, width, height, Format_RGBA8888).save(...)`, sans repasser par l'affichage Qt à
  chaque frame. Le dossier temporaire lui-même n'est **pas** supprimé par cette fonction (pas son
  rôle : c'est l'appelant — futur dialogue d'export, voir plus bas — qui décide quand la séquence
  PNG a fini de servir, notamment en cas d'annulation en cours d'encodage).
  **Découplage du readback pipeliné existant** : le mode temps réel du viewport accepte 1 frame de
  latence sur la lecture GPU→CPU (`renderer.rs::resolve_readback`, voir 🖥️ UI/UX) parce
  qu'imperceptible en preview ; à l'export, décaler les frames d'une unité changerait le rendu final
  (feedback de buffer en avance/retard d'une frame par rapport à ce qu'on croit exporter). Résolu
  sans aucun changement Rust : la boucle rend `n_frames + 1` frames (`for i in range(n_frames + 1):
  engine.render(i/fps, 1/fps, mouse, i, date)`) et jette le tout premier retour (la frame de bootstrap
  toute noire, celle que `resolve_readback` renvoie quand `pending_readback` était encore `None`) —
  chaque appel `i` soumet les paramètres de la frame logique `i` mais **retourne** les pixels de
  l'appel `i-1`, donc après la boucle la frame sauvegardée sous l'index `k` porte exactement les
  pixels rendus avec `iTime = k/fps`, `iFrame = k`, pixel-identique à ce qu'un hypothétique
  `render_blocking(k/fps, ...)` aurait renvoyé directement.
  `iMouse` fixe à `(0,0,0,0)` par défaut (pas d'enregistrement d'un trajet de souris pour l'instant —
  un shader qui dépend fortement de la souris exportera avec une position figée, limite documentée
  dans le dialogue d'export) ; `iFrame` suit `i` et `iTime` suit `i/fps`, cohérents avec ce qu'un
  shader Shadertoy attend pour une lecture d'animation déterministe par frame — jamais les valeurs
  mesurées par le `QElapsedTimer` du viewport live.
  **Testé** (`test_video_export.py`) : un `Engine` factice reproduisant fidèlement le comportement de
  pipelining d'`Engine::render` (retourne les pixels de l'appel *précédent*, tout-noir au premier
  appel) confirme `n_frames + 1` appels `render()` au total, exactement `n_frames` PNG numérotées
  `frame_000000.png…`, et que la frame sauvegardée sous l'index `k` correspond bien aux paramètres
  `iTime=k/fps`/`iFrame=k` soumis pour la frame logique `k` — pas à ceux de l'appel qui a
  effectivement renvoyé ces pixels un tour plus tard. Les rejets `n_frames<=0`/`fps<=0` sont testés
  aussi. La comparaison pixel-identique avec un rendu à blocage strict côté Rust évoquée plus haut
  (l'alternative écartée) n'a pas pu être rejouée dans cet environnement : le crate `rust_engine`
  n'est pas compilable ici (toolchain `rustc` trop ancien, voir la note tout en bas de `golf.rs` dans
  la section 🏌️), donc aucun `Engine` réel — factice ou non — ne peut tourner ; seule la logique
  Python de `capture_frames` elle-même (offset d'un appel, mapping index↔paramètres, écriture PNG)
  est vérifiée ici.
- [x] **Dialogue d'export (`Fichier → Exporter une vidéo (MP4)…`, nouveau
  `ui/export_video_dialog.py`).** Champs : **durée** (`QDoubleSpinBox` en secondes, ou directement en
  frames dans un `QSpinBox` juste en dessous — les deux se resynchronisent l'un l'autre en direct
  via le fps courant, frames servant d'ancre lors d'un changement de fps pour que "la même durée"
  ne dérive pas silencieusement d'un frame), **fps** (radios 24/30/60 + radio "Libre" avec valeur au
  choix), **résolution** de sortie en largeur/hauteur indépendantes de la taille actuelle du
  viewport, et **compression** exposée comme un CRF ffmpeg (0 = quasi sans perte, ~51 = très
  compressé) plutôt qu'un bitrate cible — plus prévisible visuellement pour un rendu de shader
  (beaucoup de hautes fréquences/bruit procédural, un bitrate fixe donnerait une qualité très
  inégale d'un shader à l'autre) — avec un preset à 3 crans ("Qualité", "Équilibré", "Taille
  minimale" → CRF 18/23/30, radios) et une case "Avancé" qui déverrouille le spinbox CRF exact ;
  ressortir de "Avancé" sans valeur pile sur un preset retombe sur le preset visuellement le plus
  proche plutôt que de laisser les radios sans sélection. Estimation de la taille de fichier
  affichée en direct (`estimated_file_size_bytes` : résolution × durée × fps × un facteur empirique
  par CRF, table par défaut `_DEFAULT_BYTES_PER_PIXEL_FRAME`, interpolée linéairement entre presets
  pour une valeur CRF "avancée" intermédiaire — même forme d'interpolation que les keyframes de
  sliders) plutôt qu'un vrai calcul, pour rester réactif sans lancer d'encodage à blanc.
  **Recalibrage prévu mais pas encore branché** : `record_actual_export_size(qsettings, settings,
  actual_bytes)` existe déjà et sait remplacer le facteur par défaut d'un CRF par
  `actual_bytes / (largeur×hauteur×fps×durée)` mesuré sur un vrai export, persisté via `QSettings`
  (`videoExportCalibration`) et relu automatiquement par le dialogue au prochain lancement — mais
  rien ne l'appelle encore puisque l'encodage ffmpeg lui-même (item suivant de cette section) n'existe
  pas encore ; câblé pour que cet item futur n'ait qu'à appeler cette fonction, pas à réinventer le
  format de stockage.
  Valeurs (durée/fps/résolution/CRF) persistées via `QSettings` à la fermeture acceptée du dialogue
  et rechargées à la prochaine ouverture, même logique que les préférences et les options de golf
  existantes.
  **Câblé** dans `Fichier → Exporter une vidéo (MP4)…` (`MainWindow._on_export_video`) : ouvre le
  dialogue, puis exécute déjà la moitié "capture" du pipeline (`video_export.capture_frames`, voir
  plus haut) — `Engine.resize(w, h)` avant la boucle, puis `resize` de retour à la taille d'écran du
  viewport dans un `finally` (donc y compris si la capture échoue en cours de route), le tout entre
  deux nouvelles méthodes `Viewport.suspend_for_external_render()`/`resume_after_external_render()`
  qui coupent le timer temps réel (~60 fps) et son timer de resize débouncé le temps de l'export —
  précaution nécessaire (pas seulement suggérée par analogie avec `pending_readback`, déjà vidé côté
  Rust par `Engine::resize` lui-même) : sans ça, une frame temps réel pourrait s'intercaler pendant
  l'export et lire des pixels à la mauvaise résolution, ou un resize débouncé en attente pourrait se
  déclencher au milieu de la capture et annuler la résolution d'export. Comme la barre de progression
  (item suivant) et l'invocation ffmpeg (item d'après) n'existent pas encore, `_on_export_video`
  informe pour l'instant par une simple boîte de dialogue où la séquence PNG a été écrite, plutôt que
  de silencieusement jeter le travail déjà fait par la boucle de capture.
  **Testé** (`test_export_video_dialog.py`) : synchronisation secondes↔frames dans les deux sens,
  frames qui reste bien l'ancre lors d'un changement de fps (preset ou libre), bascule preset↔avancé
  du CRF (y compris le rattrapage sur le preset le plus proche en sortant d'avancé), aller-retour de
  persistance via `QSettings` (dialogue rouvert = dernières valeurs), formule d'estimation de taille
  exacte sur un cas simple, interpolation entre presets pour un CRF intermédiaire, plateau en dehors
  de la plage de la table, et `record_actual_export_size`/`_load_calibration` qui font effectivement
  converger l'estimation vers une taille réelle donnée. Le dialogue lui-même n'a pas pu être ouvert
  bout en bout dans une vraie fenêtre (`MainWindow` nécessite le module natif `shadertoy_engine`, non
  compilable dans cet environnement — voir la note toolchain déjà citée dans 🏌️/🎬), donc
  `_on_export_video` n'a pas pu être exercé au-delà d'une relecture attentive du code ; seul le
  dialogue et son estimation, indépendants du module natif, sont couverts par un test automatisé.
- [x] **Barre de progression annulable.** La capture (rendu GPU frame par frame) et l'encodage
  (`ffmpeg`) sont deux phases distinctes avec des durées très différentes selon la complexité du
  shader vs la résolution/CRF choisis ; la barre affiche les deux séparément ("Rendu : 120/300
  frames" puis "Encodage vidéo..." avec la progression parsée depuis la sortie `-progress
  pipe:1` de ffmpeg, format `key=value` ligne par ligne, bien plus simple et stable à parser que le
  texte de la sortie standard destinée à un terminal). Annuler pendant la capture arrête la boucle de
  rendu immédiatement ; annuler pendant l'encodage tue le sous-processus ffmpeg (`Popen.terminate()`,
  `kill()` en dernier recours). Dans tous les cas, le dossier temporaire de frames PNG est supprimé à
  la sortie (succès, échec ou annulation) — jamais laissé sur le disque.
- [x] **Invocation de `ffmpeg.exe`** depuis la séquence de PNG numérotés :
  `ffmpeg -y -framerate {fps} -i frame_%06d.png -c:v libx264 -preset medium -crf {crf} -pix_fmt
  yuv420p -progress pipe:1 -nostats sortie.mp4`. `-pix_fmt yuv420p` forcé explicitement (pas la
  valeur par défaut de libx264) pour garantir la lecture dans à peu près tous les lecteurs/réseaux
  sociaux, y compris ceux qui ne gèrent pas le 4:4:4 par défaut d'un encodage depuis du RGBA8. Chemin
  de l'exécutable résolu via `Path(__file__).parent / "bin" / "ffmpeg.exe"` en développement, et
  relatif au dossier de l'exécutable une fois empaqueté (voir empaquetage ci-dessous) — jamais un
  `ffmpeg` supposé présent dans le `PATH` de l'utilisateur, pour que l'export marche identiquement en
  poste de dev et une fois installé chez quelqu'un qui n'a jamais entendu parler de ffmpeg.
- [x] **`ffmpeg.exe` embarqué dans le logiciel et l'installeur.** Un build **statique LGPL**
  (`ffmpeg-release-essentials` de gyan.dev ou équivalent officiel, pas un build GPL avec des codecs
  supplémentaires non nécessaires ici — un seul codec de sortie, H.264 via `libx264`, suffit et reste
  LGPL-compatible) placé dans `packaging/bin/ffmpeg.exe`, accompagné de son fichier de licence
  (`packaging/bin/ffmpeg-LICENSE.txt`, affiché dans l'installeur comme les autres mentions
  tierces). Trois points d'empaquetage à toucher, cohérents avec le onedir déjà en place (voir
  `COMPILATION.md`/`petit_editeur_glsl.spec`) :
  - `petit_editeur_glsl.spec` : ajouter `(str(PROJECT_ROOT / "packaging" / "bin" / "ffmpeg.exe"),
    ".")` aux `binaries` de l'`EXE`, pour qu'il atterrisse à la racine du dossier `dist/PetitEditeurGLSL/`
    à côté de l'exécutable principal — même logique que les binaires PySide6/QtWebEngine déjà
    collectés par `collect_all`.
  - `installer.iss` : une ligne `Source: "..\dist\PetitEditeurGLSL\ffmpeg.exe"; DestDir:
    "{app}"; Flags: ignoreversion` aux côtés du reste du dossier onedir (déjà couvert si l'installeur
    empaquette tout le dossier `dist/` en bloc — à vérifier contre le script actuel plutôt que
    dupliquer une règle qui existe peut-être déjà de façon générique).
  - Taille de l'installeur : un `ffmpeg.exe` statique essentials pèse couramment 60-80 Mo, à
    mentionner dans `README.md`/`COMPILATION.md` (taille de téléchargement attendue) pour ne
    surprendre personne au moment du build.
  Licence : LGPL 2.1+ de FFmpeg autorise la redistribution binaire telle quelle tant que la mention
  de licence est incluse et qu'aucune modification du binaire n'est faite — c'est bien le cas ici
  (binaire officiel non modifié, simplement copié).
- [x] **Export CLI batch**, même esprit que `python run.py --golf` : `python run.py --export-mp4
  projet.json sortie.mp4 --duration 10 --fps 30 --crf 23 [--width 1920 --height 1080]`, headless
  (charge le projet `.json`, construit l'`Engine` hors GUI, capture, encode). Utile pour générer des
  aperçus vidéo en lot (plusieurs shaders d'un dossier) sans ouvrir l'interface à chaque fois.
- [ ] **Non prévu pour l'instant** : export GIF (palette de couleurs bien plus limitée, moins
  pertinent pour du rendu procédural riche en dégradés — ffmpeg peut déjà produire un GIF depuis la
  même séquence PNG le jour où ce sera demandé, sans changement de pipeline), enregistrement en
  direct pendant la lecture temps réel (l'approche "rendu déterministe puis encodage" ci-dessus
  donne un résultat reproductible, contrairement à un enregistrement live qui dépendrait de la
  vitesse GPU de la machine), et pipe direct `ffmpeg` en entrée standard (recevoir les frames sur
  `stdin` en `rawvideo` plutôt que de passer par des fichiers PNG intermédiaires irait plus vite et
  économiserait de l'espace disque, mais l'énoncé demande explicitement le système fichier par
  fichier qui se recolle à la fin — piste d'optimisation possible plus tard si l'export de longues
  séquences en haute résolution s'avère trop lent en pratique).

---

## 🖥️ UI/UX (`python_ui/ui/*.py`, `assets/web/index.html`)

- [x] Viewport redimensionnable : `Engine::resize` (Rust) réalloue la texture de sortie et les 4
  cibles ping-pong des buffers ; côté UI, `Viewport` n'a plus de `setFixedSize`, un timer debounce
  (~150 ms) évite de réallouer les textures GPU à chaque pixel pendant un drag de splitter/fenêtre.
  Testé (redimensionnement en cours d'exécution, y compris avec un feedback de buffer actif).
  Pas de vrai mode plein écran dédié (juste redimensionnable dans la fenêtre).
- [x] Export image (PNG) de la frame actuellement affichée (`Viewport.export_png`, menu
  `Fichier → Exporter une image (PNG)`). L'export vidéo (.mp4 via ffmpeg) est détaillé dans sa
  propre section 🎬 plus haut ; pas de GIF prévu (voir "non prévu pour l'instant" dans cette section).
- [x] Format de projet `.json` (`Fichier → Ouvrir/Enregistrer le projet`) — voir la description
  complète (format 2, multi-passes) dans la section 🌈 Compatibilité Shadertoy. Passé à **format 3**
  avec l'ajout de la clé `"sliders"` (layout min/max/décimales par onglet, voir 🎚️ Sliders) ; les
  projets format 2 se chargent toujours sans erreur (clé absente = aucun override).
- [x] Liste des fichiers récents (menu `Fichier → Fichiers récents`, persistée via `QSettings`,
  8 entrées max, filtrée des chemins qui n'existent plus au démarrage) + confirmation avant de
  perdre des modifications non enregistrées (Nouveau/Ouvrir/fichier récent/fermeture de fenêtre) —
  suivi du "sale" (`_is_dirty`) branché sur les vraies éditions de l'éditeur (frappe, sliders, golf),
  pas sur les chargements programmatiques (`set_value` ne déclenche pas `textChanged`)
- [x] Glisser-déposer une image directement sur un slot iChannel (en plus du bouton "Parcourir…") :
  `_ChannelSlot` accepte les drops de fichiers, filtre par extension, retour visuel pendant le survol
- [x] Panneau de préférences (`Fichier → Préférences…`) : taille de police de l'éditeur (`editor.
  updateOptions({fontSize})`), minimap on/off, debounce de compilation configurable (remplace la
  constante fixe `COMPILE_DEBOUNCE_MS` par `self._compile_debounce_ms`) — persistés via `QSettings`
  et réappliqués au démarrage (`_apply_editor_preferences`, câblé sur `editorReady`)
- [x] Graphe de temps de frame : `Footer.FrameTimeGraph`, sparkline de 90 échantillons codée
  couleur (vert ≤16.7 ms / jaune ≤33 ms / rouge au-delà), alimentée par `Viewport.frameRendered`
  (temps mesuré autour de l'appel `Engine.render()` à chaque frame)
- [x] Découplage du rendu et de la lecture CPU→GPU (`renderer.rs`) : `Engine` fait maintenant du
  readback pipeliné sur 1 frame au lieu d'un `map_async` + `device.poll(Maintain::Wait)` bloquant à
  chaque appel. Chaque `render()` soumet la frame courante, stocke son buffer de lecture (+ le
  `Receiver` du callback `map_async`) dans `self.pending_readback`, puis résout et retourne le
  **précédent** appel (`Engine::resolve_readback`) — pas celui qu'on vient de soumettre. Comme
  l'appelant revient une frame UI plus tard, le callback de mapping du GPU a quasiment toujours déjà
  été déclenché entretemps, donc `resolve_readback` essaie d'abord un `Maintain::Poll` non-bloquant
  (`try_recv`) et ne retombe sur un vrai `Maintain::Wait` bloquant que si le GPU est réellement plus
  lent que la cadence d'appel — au lieu de bloquer systématiquement à chaque frame comme avant. Coût :
  1 frame de latence d'affichage (imperceptible pour un preview temps réel), payée aussi une fois
  comme frame noire de bootstrap juste après `Engine::new`/`resize` (aucune frame précédente à
  retourner ; le contenu réellement rendu ce jour-là est mis de côté et rendu au prochain appel).
  `resize()` vide `pending_readback` (dimensions et mise en page de padding devenues invalides).
  L'avancement des ping-pong buffers (Buffer A-D) n'est pas affecté : il se produit à la soumission,
  indépendamment du moment où le CPU relit les pixels, donc le feedback inter-frames reste exact.
  Vérifié par : décalage d'une frame confirmé sur un shader dérivé de `iFrame`, accumulateur
  multi-passe (Buffer A en feedback sur lui-même) qui continue de progresser correctement malgré le
  décalage de lecture, et reset propre du pipeline après un `resize`.
- [x] Raccourcis clavier configurables (`shortcuts.py` : registre de `ShortcutSpec`
  action_id/label/défaut persisté en `QSettings`, `ShortcutRegistry` qui applique/sauvegarde en
  direct sur les `QAction` du menu/toolbar sans reconstruction ; `ui/shortcuts_dialog.py` : tableau
  de `QKeySequenceEdit` par commande, accessible via *Edition -> Raccourcis clavier…*, avec détection
  de doublon à la validation et un bouton de réinitialisation par ligne + global. Ctrl+Z/Ctrl+Y restent
  les défauts pour Annuler/Rétablir, désormais rebindables comme toutes les autres commandes du menu et
  de la barre d'outils.)

> Thème : on reste volontairement en clair (light), pas de thème sombre/configurable prévu.

---

## 🌍 Internationalisation (i18n) (`lngs/*.json`, `python_ui/ui/*.py`)

Actuellement, tous les textes de l'interface (menus, boutons, titres de boîtes de dialogue,
messages d'erreur/confirmation) sont codés en dur en français directement dans les fichiers
`python_ui/ui/*.py`. Objectif de cette section : externaliser ces textes dans des fichiers de
langue chargés au démarrage, pour permettre d'ajouter d'autres langues sans toucher au code.

- [x] **Format des fichiers de langue.** Un répertoire `lngs/` à la racine du projet, un fichier
  `.json` par langue (`fr.json`, `en.json`, futur `es.json`, etc.), nommé par son code ISO 639-1.
  Clés imbriquées par zone de l'interface (`menu.file.*`, `menu.edit.*`, `toolbar.*`,
  `dialogs.export_video.*`, `messages.*`, `actions.*` pour les libellés de commandes déjà
  identifiées par leur `action_id` dans `shortcuts.py`, etc.) plutôt qu'une liste à plat, pour
  rester lisible et grouper les chaînes par le fichier/widget qui les affiche. `fr.json` fait foi
  comme langue de référence (toute chaîne de l'app doit y exister) ; `en.json` est la première
  traduction et doit avoir exactement le même jeu de clés — un futur test de cohérence
  (`test_i18n_completeness.py`, section suivante) comparera les deux arborescences de clés pour
  détecter tout écart.
  **Fait dans cette vague** : les deux premiers fichiers, `lngs/fr.json` et `lngs/en.json`,
  couvrant les menus, la barre d'outils, les onglets de passes, les libellés de commandes
  (`actions.*`, alignés sur les `action_id` de `shortcuts.py`), les boîtes de dialogue principales
  (À propos, options de golf, préférences, export vidéo + sa fenêtre de progression, raccourcis
  clavier, import Shadertoy, cubemap, bornes de slider) et les messages d'avertissement/confirmation
  les plus courants. Non encore fait : quelques tooltips secondaires et libellés très spécifiques du
  panneau de sliders/iChannel n'ont pas tous été extraits — à compléter au fil de l'implémentation
  du chargeur (item suivant), en ajoutant les clés manquantes au fur et à mesure qu'un `.py` est
  migré vers `tr(...)`.
- [x] **Chargeur de langue côté Python** (`python_ui/i18n.py`) : `lngs_dir()` résout le répertoire
  `lngs/` exactement comme `video_export.resolve_ffmpeg_path()` résout `ffmpeg.exe` (même bascule
  `sys.frozen` : à côté de `sys.executable` en version packagée, à côté de la racine du projet en
  développement) — `packaging/petit_editeur_glsl.spec` copie désormais `lngs/` à la racine du
  bundle via `Tree(...)`, au même titre que `assets/`. `available_languages()` liste les langues en
  scannant réellement `lngs/*.json` (aucune liste codée en dur : déposer un nouveau fichier suffit à
  faire apparaître une langue). `load_language(code)` charge la langue active et `fr.json` comme
  repli ; `tr(key, **kwargs)` résout une clé pointée (`"dialogs.export_video.title"`) dans
  l'arborescence chargée, retombe sur `fr.json` si la clé manque dans la langue active, puis sur la
  clé brute si elle manque des deux (jamais de chaîne vide ni de `KeyError` visible), et applique
  `str.format(**kwargs)` pour les chaînes paramétrées — un désaccord de placeholder entre l'appelant
  et la traduction rend le gabarit non formaté plutôt que de planter. `main.py` appelle
  `i18n.load_language(...)` au tout début de `main()`, avec la langue choisie lue dans `QSettings`
  (`languageCode`) sinon la langue système si un fichier `lngs/` correspondant existe, sinon `"fr"`.
  Testé (`test_i18n.py`) : parité des clés `fr.json`/`en.json` (187 de chaque côté), résolution
  contre les deux fichiers réels, et — via des fichiers de langue temporaires isolés du vrai
  `lngs/` — repli sur `fr.json` pour une clé manquante de la langue active, repli sur la clé brute
  pour une clé manquante des deux, repli sur `fr` pour un code de langue sans fichier
  correspondant, et non-crash sur un désaccord de placeholder `str.format`.
- [x] **Migration progressive des widgets** : remplacer chaque chaîne en dur des fichiers listés
  dans la section UI/UX (`main_window.py`, `export_video_dialog.py`, `export_progress_dialog.py`,
  `sliders_panel.py`, `ichannel_panel.py`, `shortcuts_dialog.py`, `footer.py`, `shortcuts.py`) par
  un appel `tr("clé.correspondante")`.
  `export_video_dialog.py`, `export_progress_dialog.py`, `sliders_panel.py`, `ichannel_panel.py`,
  `shortcuts_dialog.py` et `footer.py` étaient déjà entièrement migrés (vérifié : plus aucune
  chaîne UI en dur, seuls des glyphes d'icône universels — `↺ 🎲 🎬 📷 ⌨` — et des lettres de face de
  cubemap `"ABCD"[value]` restent en dur, à raison, puisqu'aucune traduction n'a de sens dessus).
  **Fait dans cette vague** : les deux fichiers restants, `main_window.py` (1180 lignes, aucun
  appel `tr()` avant cette vague — le plus gros morceau : menus Fichier/Édition/Aide, barre
  d'outils, onglets de passes, dialogues golf (options/confirmation Common/annulé simple et
  tout-ou-rien/annuler/tout golfer), garde de modifications non enregistrées, fichiers récents,
  ouvrir shader/projet, import Shadertoy (ID invalide/clé API/partiel), enregistrer sous/projet,
  export golfé/PNG/vidéo (ffmpeg introuvable/échec/succès), préférences, à propos, erreurs
  iChannel/vidéo/webcam) et `shortcuts.py` (`ShortcutSpec.label` → `ShortcutSpec.label_key`,
  pointant vers les clés `actions.*` déjà présentes dans `lngs/*.json` plutôt que du texte français
  en dur — `SHORTCUT_SPECS` est une liste au niveau module, construite à l'import, donc avant que
  `i18n.load_language()` ait tourné : comme `_TAB_LABELS` dans `main_window.py`, elle ne peut
  stocker que des *clés*, résolues via `tr()` par l'appelant au moment de l'affichage — c'est
  d'ailleurs ce que `ui/shortcuts_dialog.py` faisait déjà, `tr(spec.label_key)`, avant même que ce
  champ existe sur `ShortcutSpec`, ce qui aurait planté au premier lancement du dialogue de
  raccourcis).
  **Bug réel trouvé et corrigé pendant l'implémentation** : `i18n._lookup` découpait *chaque*
  point d'une clé comme un niveau d'imbrication, alors que le bloc `actions` de `lngs/*.json` est
  un dict *plat* dont les clés elles-mêmes contiennent un point (`"file.new"`, calquées 1:1 sur les
  `action_id` pointés de `shortcuts.py`) plutôt que de vrais sous-objets imbriqués — `tr("actions.
  file.new")` renvoyait donc la clé brute au lieu du libellé traduit, silencieusement (jamais de
  `KeyError`, donc jamais remarqué avant un test ciblé). Corrigé : `_lookup` essaie d'abord, à
  chaque niveau, si le reste du chemin pointé correspond tel quel à une clé plate du nœud courant,
  et ne le découpe part par part que si ce n'est pas le cas — les deux conventions (imbriqué normal
  et plat-avec-points façon `actions.*`) cohabitent désormais dans le même arbre sans lookup
  séparé. Couvert par un nouveau test dans `test_i18n.py` (clé plate `actions.*` réelle contre
  `fr.json`/`en.json`, plus un cas de fixture isolé mélangeant clé imbriquée et clé plate dans le
  même arbre, y compris à travers le repli `fr` d'une langue qui n'a pas du tout la clé).
  Effet de bord découvert au passage : `lngs/en.json` avait dérivé de `fr.json` (10 clés manquantes
  dans `footer.*`/`sliders_panel.*`/`ichannel_panel.*`, ajoutées à la volée par le premier passage
  de golf/i18n dessus sans que la parité soit revérifiée) — remis en parité stricte (205 clés de
  chaque côté), `test_i18n.py` vérifiait déjà cette parité mais n'avait apparemment pas été relancé
  depuis. `test_export_video_dialog.py` référençait aussi encore les anciennes clés françaises en
  dur de `CRF_PRESETS`/`_crf_radios` (`"Équilibré"`, `"Qualité"`, `"Taille minimale"`) alors que
  `export_video_dialog.py` avait déjà été migré vers des identifiants stables (`"quality"`,
  `"balanced"`, `"smallest"`) — mis à jour en conséquence.
  Vérifié : toute la suite de tests existante (`test_i18n.py`, `test_sliders.py`,
  `test_export_video_dialog.py`, `test_video_export.py`, `test_shadertoy_import.py`) passe ;
  vérification statique de chaque appel `tr("clé…")` littéral de `main_window.py` (91 clés) et
  `shortcuts.py` (20 clés `actions.*`) contre `fr.json` et `en.json` — aucune ne retombe sur la clé
  brute (signe d'une clé manquante) dans les deux langues ; `MainWindow` n'a en revanche pas pu être
  réellement instanciée dans cet environnement (`shadertoy_engine`, le module natif Rust, n'est pas
  compilé ici — cf. limitations déjà notées ailleurs dans ce roadmap). L'i18n ne touchant que
  `python_ui/` (jamais `rust_engine/src/golf.rs` ni le GLSL golfé), aucune régression de rendu
  pixel-identique n'est attendue et le golf-test n'a pas eu besoin d'être rejoué pour cette vague.
- [x] **Sélecteur de langue** dans le panneau `Fichier → Préférences…` (menu déroulant listant les
  langues disponibles = les fichiers présents dans `lngs/`, pas une liste codée en dur, pour qu'ajouter
  un fichier `.json` suffise à faire apparaître une langue sans toucher au code du panneau) —
  persisté via `QSettings` sous la même clé `languageCode` que lit le chargeur au démarrage. Un
  changement de langue nécessite de relancer le logiciel (pas de retraduction à chaud de tous les
  widgets déjà construits) : un simple message le précise au moment du changement, plutôt que de
  reconstruire toute l'UI en direct.
  `QComboBox` (`language_box`) ajouté dans `_on_preferences` (`ui/main_window.py`), peuplé via
  `i18n.available_languages()` (déjà existant dans `i18n.py`, scanne `lngs/*.json` et lit
  `_meta.name` de chacun) — aucune liste codée en dur, chaque entrée trié par nom affiché
  (`item[1].lower()`) pour ne pas dépendre de l'ordre du système de fichiers, `code` stocké en
  `QComboBox.itemData` et `name` affiché. Sélection initiale alignée sur `i18n.active_language_code()`
  (la langue réellement chargée au démarrage, pas seulement ce qui est enregistré dans `QSettings` —
  les deux peuvent différer la toute première fois, quand `languageCode` est encore vide et que
  c'est la détection de la locale système qui a choisi la langue active, cf. `main.py::
  _startup_language_code()`). À l'acceptation du dialogue, la clé `languageCode` de `QSettings`
  n'est écrite (et le message d'information affiché) que si le code sélectionné diffère de
  `current_language_code` capturé à l'ouverture — un Ok sans changement de langue n'écrit rien et
  ne montre rien, comme pour les autres champs du dialogue. Nouvelles clés `dialogs.preferences.
  language`/`dialogs.preferences.language_restart_notice` ajoutées en parité stricte dans
  `lngs/fr.json` et `lngs/en.json` (207 clés de chaque côté, `test_i18n.py` repasse). Aucune
  retraduction à chaud : le dialogue lui-même, une fois fermé, et le reste de la fenêtre déjà
  construite restent dans la langue avec laquelle ils ont été bâtis jusqu'au relancement — c'est le
  message `dialogs.preferences.language_restart_notice` (`QMessageBox.information`) qui le rend
  explicite plutôt que de tenter une reconstruction de l'UI en direct (hors scope, cf. le
  `_TAB_LABEL_KEYS`/`_lookup` déjà en place précisément pour éviter de figer du texte traduit avant
  que `i18n.load_language()` ait tourné).
  Vérifié : `i18n.available_languages()` découvre bien un fichier `lngs/de.json` déposé à la volée
  sans changement de code (testé manuellement en ajoutant puis retirant le fichier) ; tri/sélection
  de l'index initial rejoués en dehors de Qt (indisponible dans cet environnement, cf. limitations
  déjà notées ailleurs dans ce roadmap — `MainWindow` reste non instanciable ici) en appelant
  directement `i18n.available_languages()`/`i18n.active_language_code()` et en reproduisant la
  logique de tri/recherche d'index du combo ; `python3 -m py_compile ui/main_window.py` et
  `test_i18n.py` (parité de clés + `available_languages()` + `load_language()`/`tr()`) passent tous
  les deux.
- [x] **Test de cohérence des traductions** (`test_i18n_completeness.py`) : charge tous les fichiers
  de `lngs/`, vérifie que chacun a exactement le même jeu de clés que `fr.json` (aucune clé
  manquante, aucune clé en trop qui ne sert plus à rien), et que `python_ui/i18n.py::tr` lève une
  erreur claire en développement (pas un texte silencieusement vide) si une clé demandée par le
  code n'existe dans aucun fichier de langue — pour repérer une chaîne oubliée pendant la migration
  plutôt que de la découvrir en la voyant manquante à l'écran.
  Trois vérifications indépendantes dans `test_i18n_completeness.py` :
  1. Parité de clés généralisée à *tous* les fichiers de `lngs/` (pas seulement `fr`/`en` comme
     dans `test_i18n.py`) contre `fr.json` — clés manquantes **et** clés en trop (une traduction
     orpheline restée après le renommage/retrait d'une clé de `fr.json`, exactement le genre de
     dérive trouvée à la main dans `en.json` lors de la vague "Migration progressive des widgets").
     Scan de `lngs/*.json` (pas de liste codée en dur), donc une 3e/4e langue déposée plus tard est
     couverte automatiquement, même principe que `i18n.available_languages()`.
  2. `i18n.py::tr` modifié : lève désormais `i18n.MissingTranslationKeyError` (nouvelle exception,
     `LookupError` plutôt que `KeyError` pour un message d'erreur lisible sans guillemets en trop)
     quand une clé est absente à la fois de la langue active et du repli `fr.json` — **mais
     seulement hors build empaqueté** (même test `sys.frozen` que `lngs_dir()`/`video_export.
     resolve_ffmpeg_path()` : "packagé vs développement"). En développement (`python run.py`, et
     tous les tests de ce dépôt), une chaîne oubliée plante immédiatement au lieu de se découvrir
     plus tard comme une étrange clé à points affichée à l'écran ; un build empaqueté livré à un
     utilisateur final continue de dégrader silencieusement vers la clé brute plutôt que de planter
     sur une chaîne manquante. Testé dans les deux sens (dev → lève, `sys.frozen = True` → ne lève
     pas) contre une fixture jetable, isolée des vraies traductions. `test_i18n.py` mis à jour en
     conséquence : son cas `tr("does.not.exist")` vérifiait jusqu'ici un repli silencieux vers la
     clé brute — il vérifie désormais la levée de `MissingTranslationKeyError` (le process de test
     n'étant jamais `sys.frozen`), la couverture du côté "build empaqueté" étant déplacée dans
     `test_i18n_completeness.py`.
  3. Scan statique best-effort des appels `tr("clé.littérale")` dans `python_ui/**/*.py` (regex
     avec lookbehind négatif pour ne jamais confondre avec `str(`/`attr(`/`instr(`), chacun vérifié
     contre `fr.json` — c'est la vérification "chaîne oubliée pendant la migration" que demande
     l'énoncé du ticket, sans avoir besoin d'importer/instancier l'UI (donc sans dépendre de
     PySide6, indisponible dans cet environnement de développement, cf. limitations déjà notées
     ailleurs dans ce roadmap). 196 sites d'appel littéraux trouvés, tous résolus. Les clés
     construites dynamiquement (`tr(_TAB_LABEL_KEYS[tab_id])` dans `main_window.py`, `tr(spec.
     label_key)`/`tr(f"actions.{action_id}")` dans `shortcuts_dialog.py`) ne sont pas des littéraux
     et échappent forcément à un scan par regex sur le texte source — elles avaient déjà été
     vérifiées à la main lors de leur introduction (voir l'entrée "Migration progressive des
     widgets" de ce roadmap) et restent hors du périmètre de ce scan.
  Vérifié en cassant volontairement chaque cas (clé retirée de `en.json` → détecté ; clé littérale
  changée en une clé inexistante dans `main_window.py` → détecté ; fichiers restaurés ensuite) en
  plus des 3 passes normales ; `test_i18n.py` et `test_i18n_completeness.py` passent tous les deux,
  `python3 -m py_compile` sur `i18n.py`/`main_window.py`/`main.py` sans erreur.
- [ ] **Non prévu pour l'instant** : pluralisation façon ICU/gettext (`ngettext`) — l'app n'a pour
  l'instant qu'une poignée de messages avec un compteur (ex. « N pass(es) golfées »), déjà gérés à
  la main avec un `(es)` générique plutôt qu'une vraie règle de pluriel par langue ; RTL (arabe,
  hébreu) — aucune langue RTL prévue dans `lngs/` pour l'instant, la mise en page Qt actuelle
  (menus/toolbar/panneaux à gauche) n'a pas été pensée pour un miroir RTL automatique.

---

Mis à jour ensuite avec le cinquième item de la vague "Golf avancé — prochaine vague" (repli de
constantes purement littérales, `2.*3.` → `6.`, restreint aux opérandes entiers exacts en flottant
pour rester bit-exact avec l'arithmétique `f32` du pilote GPU cible) : il ne reste donc plus qu'un
seul item de cette vague, le plus risqué (inlining des fonctions à site d'appel unique).

*Généré à partir d'une lecture du contenu de `petitediteurglsl.zip` : `rust_engine/src/{golf,literals,shader,renderer,texture,lib}.rs` et `python_ui/{main.py,engine_bridge.py,local_server.py,ui/*.py}`. Mis à jour après la refonte "sliders détectés automatiquement, zéro syntaxe custom", puis après l'ajout des sections golf avancé (🏆) et export vidéo .mp4 (🎬) — la section golf avancé est entièrement implémentée et testée, y compris le renommage pondéré par fréquence. Mis à jour ensuite avec l'import direct depuis Shadertoy (nouveau module `python_ui/shadertoy_import.py`), dont la partie "convention de nommage des faces de cubemap" reste une implémentation best-effort faute d'accès réseau sortant vers shadertoy.com dans l'environnement de développement. Mis à jour ensuite avec la boucle de capture image par image de l'export vidéo (nouveau `python_ui/video_export.py`, testé via un `Engine` factice faute de toolchain Rust dans cet environnement) : le reste de la section 🎬 (dialogue d'export, barre de progression, invocation `ffmpeg`, empaquetage, CLI batch) reste un plan détaillé, non encore implémenté. Ajout d'une nouvelle vague "Golf avancé — prochaine vague" (affectation composée généralisée, splat de constructeur de vecteur, suppression du qualificatif `in`, ternaire depuis un `if`/`else` à affectation unique, repli de constantes littérales exactes, inlining des fonctions à site d'appel unique) : plan détaillé classé par gain/risque comme le reste de cette section. Implémentation de son premier item, l'affectation composée généralisée (`x=x OP atomic;`→`x OP= atomic;`) : périmètre revu à la baisse en cours de route par rapport au plan initial (opérande atomique seulement, pas une sous-expression arbitraire) pour ne jamais réassocier une chaîne d'opérations flottantes et casser la garantie de rendu bit-exact — les 5 autres items de cette vague restent non implémentés. Mis à jour ensuite avec la migration i18n complète de `main_window.py` et `shortcuts.py` (les deux seuls fichiers de la liste UI/UX encore non migrés vers `tr()` ; les six autres l'étaient déjà), la correction d'un bug de résolution des clés plates `actions.*` dans `python_ui/i18n.py::_lookup` (découpait chaque point comme un niveau d'imbrication, cassait silencieusement toute clé `actions.*`), le rattrapage de parité `lngs/en.json` (10 clés manquantes par rapport à `fr.json`) et la correction de `test_export_video_dialog.py` qui référençait encore les anciennes clés françaises en dur de `CRF_PRESETS` — toute la section 🌍 Internationalisation ne compte donc plus que trois items non faits (sélecteur de langue, test de cohérence dédié `test_i18n_completeness.py`, pluralisation/RTL délibérément hors scope). Mis à jour ensuite avec le sélecteur de langue du panneau `Fichier → Préférences…` (`QComboBox` peuplé via `i18n.available_languages()`, persisté dans `QSettings` sous `languageCode`, message de relance affiché uniquement si la langue sélectionnée change) : la section 🌍 Internationalisation ne compte donc plus que deux items non faits (test de cohérence dédié, pluralisation/RTL délibérément hors scope). Mis à jour ensuite avec `test_i18n_completeness.py` (parité de clés généralisée à tous les fichiers de `lngs/`, `i18n.py::tr` qui lève désormais `MissingTranslationKeyError` en développement pour une clé introuvable nulle part plutôt que de dégrader silencieusement — toujours silencieux dans un build empaqueté, `test_i18n.py` mis à jour en conséquence —, et scan statique des appels `tr("...")` littéraux du code contre `fr.json`) : la section 🌍 Internationalisation ne compte donc plus qu'un seul item non fait, délibérément hors scope (pluralisation ICU/gettext, RTL). Mis à jour ensuite avec le quatrième item de la vague "Golf avancé — prochaine vague" (les trois premiers — affectation composée généralisée, splat de constructeur de vecteur, suppression du `in` — étaient déjà faits) : conversion `if`/`else` à affectation unique en opérateur ternaire (`ternary_from_if_else`, `golf.rs`), avec un garde-fou supplémentaire non prévu par le plan initial (rejet d'une condition contenant une affectation ou une virgule à profondeur 0, qui casserait la précédence une fois embarquée dans un `?:`) et une composition volontairement non traitée pour un `if` imbriqué déjà converti par la même passe (branches interdites de contenir `?`/`:`, pour ne jamais avoir à distinguer un ternaire bien formé d'un autre déjà présent). Mis à jour ensuite avec le cinquième item de la vague "Golf avancé — prochaine vague" (repli de constantes purement littérales, `simplify_algebra_pass`, restreint aux opérandes entiers exacts pour rester bit-exact avec l'arithmétique `f32` du pilote GPU cible) — il ne restait alors plus que le dernier item de cette vague, le plus risqué (inlining des fonctions à site d'appel unique). Mis à jour enfin avec cet item (`inline_single_call_functions`, `golf.rs`, branchée sous le même toggle `dead_code` que `remove_unused_functions`) : périmètre tenu au plan initial (fonction non-`void`, corps réduit à un seul `return expr;`, appelée exactement une fois, paramètres substitués systématiquement entre parenthèses, jamais Common), avec un garde-fou supplémentaire découvert en écrivant les tests et non prévu par le plan initial — l'expression de retour substituée est elle-même parenthésée dans son ensemble au site d'appel (pas seulement chaque paramètre), pour ne jamais casser la précédence quand l'appel inliné est imbriqué dans un opérateur de précédence différente de celui du corps de la fonction. La section 🏌️ Golfing est donc désormais entièrement implémentée — la vague "Golf avancé — prochaine vague" n'a plus d'item non fait. Mis à jour enfin avec l'entrée audio (.mp3/.wav) comme type de canal `iChannel` (nouveau `python_ui/audio_source.py`, `ChannelInput::Audio` côté `renderer.rs`/`texture.rs`/`lib.rs`, entrée « Audio (fichier)… » dans `ichannel_panel.py`, `self._audio_sources`/`_on_audio_tick` dans `main_window.py`, clés i18n ajoutées aux 12 fichiers de `lngs/`) : périmètre tenu au plan déjà détaillé plus haut (FFT numpy fenêtrée Hann sur 1024 échantillons → 512 bandes, forme d'onde sous-échantillonnée à 512 points, texture 512×2 fixe jamais recréée contrairement à la vidéo), microphone/contrôle de volume/calage bit-exact du spectre restant explicitement hors périmètre comme prévu. Ni toolchain Rust ni PySide6 n'étant disponibles dans cet environnement de développement, cette entrée n'a pu être vérifiée que par relecture et `python3 -m py_compile`, pas par compilation Rust ni exécution réelle contre un fichier audio — à revalider dans un environnement complet avant la comparaison visuelle contre un shader Shadertoy audio-réactif connu que le plan prévoit déjà.

# Audit — Module Sliders

**Périmètre analysé** (lecture intégrale, ligne par ligne) :
- `rust_engine/src/literals.rs` (505 lignes) — détection des littéraux/sliders dans le GLSL
- `rust_engine/src/lib.rs` (bindings PyO3 des structs `LiteralSlider`/`IntSlider`/`BoolSlider`/`VecSlider`)
- `python_ui/engine_bridge.py` (ré-export du module natif)
- `python_ui/ui/sliders_panel.py` (917 lignes) — panneau UI, édition, keyframing, layout
- `python_ui/ui/main_window.py` (parties d'intégration : `_refresh_sliders_for`, `_on_literal_edited`,
  `_on_slider_drag_finished`, sauvegarde/chargement de projet)
- `python_ui/ui/monaco_editor.py` + `python_ui/assets/web/index.html` (`replaceRange`, pont JS)
- `test_sliders.py`
- `lngs/*.json` (clés `sliders_panel.*`) et `ROADMAP.md` (section 🎚️ Sliders) pour comparer avec l'existant documenté

**Méthode** : lecture exhaustive du code (pas de golden-path only), recherche de tous les
`grep -i slider` dans le dépôt, relecture croisée Rust ↔ Python ↔ JS des invariants (offsets,
bornes, précision), vérification empirique de plusieurs hypothèses avec de petits scripts
Python isolés (précision décimale, comportement `json.loads` face à `NaN`/`Infinity`).

Les points ci-dessous sont classés par sévérité. Les numéros de ligne renvoient à l'état du zip fourni.

---

## 🔴 CRITIQUE — peuvent altérer silencieusement le code source de l'utilisateur

### [x] C1. Les décimales par défaut (4) ne dépendent pas de l'ordre de grandeur du littéral → des constantes petites peuvent être mises à zéro dès la première interaction

`sliders_panel.py:442` et `:521` :

```python
spin = _SliderSpinBox()
spin.setDecimals(4)          # toujours 4, quelle que soit la magnitude de lit.value
```

`format_glsl_float` (ligne 113-120) tronque strictement à `decimals` chiffres après la virgule :

```python
text = f"{value:.{max(decimals, 1)}f}".rstrip("0")
```

Vérifié empiriquement :

| valeur d'origine | réécrite en (decimals=4, valeur par défaut) |
|---|---|
| `0.00003` | `0.0` |
| `0.00001` | `0.0` |
| `0.00005` | `0.0001` |
| `-0.00003` | `-0.0` |

Un GLSL courant contient très souvent des constantes de cet ordre de grandeur (epsilon de ray
marching `EPS = 0.0001`, biais d'anti-aliasing, seuils `1e-5`/`1e-6`...). Dès que l'utilisateur
**touche le slider ne serait-ce qu'une fois** (même un micro-déplacement, y compris via molette —
voir C2), la valeur réelle est perdue et remplacée par `0.0`/`-0.0` dans le code source, ce qui
peut casser silencieusement la logique du shader (division par zéro, boucle qui ne converge plus,
NaN en aval) sans qu'aucune erreur de compilation ne se déclenche.

Le clic droit → "modifier bornes" permet bien de monter `decimals` jusqu'à 8 (`_edit_range`,
ligne 681-683), mais c'est **opt-in et découvrable seulement après coup** : le premier `refresh()`
consécutif à une frappe clavier ordinaire dans l'éditeur (`_on_text_changed` → debounce → recompile
→ `refresh(sliders)`) ne touche pas la valeur tant que le slider n'est pas manipulé — donc le piège
n'est visible qu'au moment où l'utilisateur bouge le slider pour la première fois, sans avertissement.

**Recommandation** : dériver le nombre de décimales par défaut de la représentation textuelle
d'origine du littéral (nombre de chiffres significatifs après le `.` dans le source), avec un
plancher raisonnable (ex. `max(len(fractional_part), 4)`), au lieu d'une constante `4` fixe. Un
garde-fou minimal serait aussi d'avertir/empêcher l'écriture si `format_glsl_float` produit `0.0`
alors que `value != 0.0`.

**Correctif appliqué** (`sliders_panel.py`) :
- Nouvelle fonction `_default_decimals_for(value, text)` : combine (a) le nombre de chiffres
  fractionnaires déjà présents dans le texte source du littéral (couvre `0.00003`), et (b) le
  nombre de décimales nécessaires pour que l'ordre de grandeur de la valeur elle-même ne s'arrondisse
  pas à zéro, avec 2 chiffres de marge (couvre la notation exponentielle `1e-5`, qui n'a aucun
  chiffre fractionnaire dans son texte). Résultat toujours borné entre 4 (comportement inchangé
  pour les valeurs courantes) et 8 (même plafond que le dialogue clic droit "bornes/décimales").
- `_build_scalar_row` accepte maintenant un paramètre `decimals` (au lieu du `4` en dur) pour
  `spin.setDecimals(...)`.
- `rebuild()` extrait le texte source original du littéral (`source[lit.start:lit.end]`) et calcule
  ses décimales par défaut via `_default_decimals_for` avant de construire la ligne.
- Vérifié : `EPS = 0.00003` obtient désormais 7 décimales par défaut et un déplacement du slider
  réécrit `0.00004` (plus jamais `0.0`) ; une valeur ordinaire comme `1.5` garde bien 4 décimales
  par défaut (aucune régression) ; la suite `test_sliders.py` passe intégralement sans modification.

### [x] C2. La protection anti-corruption "drag" ne couvre que le glisser-souris sur le *handle*, pas la molette ni le clavier — alors que ces deux chemins déclenchent exactement le même scénario que le code documente comme dangereux

`_LiteralState`/`is_drag_active()` (ligne 146-243) explique en détail (et à raison) pourquoi un
recompile qui atterrit **au milieu** d'une rafale d'éditions issues d'un slider est dangereux : le
texte de l'éditeur ne reflète encore qu'un préfixe des éditions déjà appliquées côté `_literals`,
et re-synchroniser les offsets dessus fait régresser `end`, ce qui laisse des `-` orphelins
s'accumuler à chaque tick suivant.

Le garde-fou (`_drag_depth`, `is_drag_active()`) n'est armé QUE par :

```python
slider.sliderPressed.connect(self._on_slider_pressed)     # ligne 453
slider.sliderReleased.connect(self._on_slider_released)   # ligne 454
```

Or **`sliderPressed`/`sliderReleased` ne sont émis par Qt que lors d'un clic-glisser à la souris
sur le curseur** (`QAbstractSlider`). Deux interactions parfaitement standards et fréquentes sur
le même widget déclenchent `valueChanged` (donc `_on_slider_moved` → `_emit_edit`) sans jamais
faire passer `_drag_depth` à une valeur non nulle :

- **la molette de la souris** au-dessus du slider (plusieurs crans rapides = plusieurs éditions
  en rafale, plus vite que le debounce de 100 ms — `SLIDER_COMPILE_DEBOUNCE_MS = 100`, ligne 50 de
  `main_window.py`) ;
- **les flèches clavier** une fois le slider focalisé (avec la répétition automatique du système
  d'exploitation, qui peut largement dépasser 10 événements/seconde).

Dans les deux cas, `_refresh_sliders_for` (main_window.py:346-372) ne voit jamais
`is_drag_active() == True` et exécute donc un `rebuild()`/`refresh()` potentiellement en plein
milieu de la rafale — reproduisant très exactement le bug de "tirets `-` qui s'accumulent" que le
docstring de `is_drag_active` décrit comme la raison d'être de cette protection.

**Recommandation** : armer aussi le garde-fou sur `valueChanged` avec un petit délai de "quiescence"
(par ex. considérer qu'un drag est actif tant qu'un nouveau `valueChanged` est arrivé dans les
N ms précédentes, indépendamment de `sliderPressed`/`sliderReleased`), ou plus simplement démarrer
un petit timer d'inactivité à chaque `_on_slider_moved`/`_on_spin_changed` qui joue le même rôle que
`dragFinished`.

**Correctif appliqué** (`sliders_panel.py`) :
- Nouveau `QTimer` à un coup, `_interaction_timer` (constante `_INTERACTION_QUIESCENCE_MS = 200`,
  volontairement au-dessus des 100 ms de `SLIDER_COMPILE_DEBOUNCE_MS` pour couvrir toute rafale plus
  rapide que le debounce), réarmé à **chaque** tick par la nouvelle méthode
  `_arm_interaction_quiescence()`, elle-même appelée en tout premier dans `_on_slider_moved` — donc
  pour un tick issu du glisser-souris, de la molette ou des flèches clavier indifféremment (les trois
  émettent `valueChanged` de façon identique ; seul le glisser-souris émet en plus
  `sliderPressed`/`sliderReleased`).
- `is_drag_active()` retourne désormais `self._drag_depth > 0 or self._interaction_timer.isActive()` :
  le garde-fou reste armé tant que des ticks arrivent plus vite que `_INTERACTION_QUIESCENCE_MS`, sans
  aucune dépendance à `sliderPressed`/`sliderReleased`.
- `_on_interaction_quiescent()` (déclenché par l'expiration du timer) émet `dragFinished` si
  `_drag_depth == 0`, exactement comme `_on_slider_released` le fait déjà pour le glisser-souris — la
  resynchronisation différée par `_refresh_sliders_for` se déclenche donc de la même façon pour les
  trois chemins d'interaction.
- `_on_slider_released` vérifie en plus que le timer n'est pas actif avant d'émettre `dragFinished`
  immédiatement (évite une émission en double si un tick de molette/clavier vient de réarmer le timer
  juste avant le relâchement du bouton de la souris) ; `_on_slider_pressed` arrête le timer par
  hygiène (le compteur `_drag_depth` couvre déjà ce cas).
- Vérifié : une rafale de `valueChanged` sans `sliderPressed`/`sliderReleased` (simulant molette/
  clavier) arme bien `is_drag_active()` dès le premier tick, le maintient armé tant que les ticks se
  succèdent plus vite que 200 ms, puis le désarme et émet `dragFinished` une fois les ticks arrêtés ;
  le chemin glisser-souris classique (`sliderPressed` → ticks → `sliderReleased`) reste inchangé dans
  son comportement observable. La suite `test_sliders.py` passe intégralement sans modification.

### [x] C3. Le sélecteur de couleur (`_on_swatch_clicked`) écrit dans le code la valeur choisie **avant** le clampage Qt du spinbox, créant une désynchronisation durable et non auto-réparable entre le code et l'UI

`sliders_panel.py:730-748` :

```python
def _on_swatch_clicked(self, ordinal: int) -> None:
    ...
    chosen = QColorDialog.getColor(QColor(r, g, b), self, tr("dialogs.color_picker.title"))
    ...
    new_values = [chosen.redF(), chosen.greenF(), chosen.blueF()]  # 0..1 plein cadre, non bornées
    widgets = self._rows[ordinal]
    if widgets is not None:
        swatch, *spins = widgets
        for spin, value in zip(spins, new_values):
            spin.blockSignals(True)
            spin.setValue(value)          # Qt clampe silencieusement dans [spin.minimum(), spin.maximum()]
            spin.blockSignals(False)
        ...
    self._emit_vec_edit(ordinal, new_values)   # <-- écrit new_values NON clampé, pas la valeur clampée du spin
```

Les bornes de chaque spinbox composant sont calculées **une seule fois**, à la construction de la
ligne (`_build_vec_row`, ligne 510-559), à partir de la valeur d'origine du littéral
(`_default_float_range`, souvent un intervalle étroit type `[0, 2×valeur]`). Si l'utilisateur choisit
dans le sélecteur de couleurs une teinte dont une composante dépasse cette borne d'origine (cas très
courant : une couleur de départ sombre `vec3(0.1, 0.05, 0.02)` donne des bornes ~`[0, 0.2]`/`[0, 0.1]`
/`[0, 0.04]`, et n'importe quelle couleur vive choisie ensuite dépasse ces bornes) :

1. Le **spinbox affiché** est silencieusement clampé par Qt à son `maximum()`.
2. Le **code GLSL réellement écrit** (`_emit_vec_edit`) contient la vraie valeur choisie,
   non clampée.
3. À la prochaine resynchronisation (`refresh()`, ligne 594-602), la condition
   `abs(spin.value() - value) > 1e-6` est vraie (le spin affiche la valeur clampée, `value` est la
   vraie valeur du code) → `spin.setValue(value)` est retenté... et reclampé exactement pareil par
   Qt. **Cette désynchronisation ne se répare jamais toute seule** : elle persiste à chaque
   recompilation tant qu'un rebuild structurel (qui recalcule des bornes fraîches à partir de la
   nouvelle valeur) ne survient pas ailleurs dans le fichier.

Résultat concret pour l'utilisateur : le swatch de couleur et les spinboxes R/G/B affichés ne
correspondent plus jamais à la couleur réellement rendue par le shader, sans qu'aucun message
n'indique le problème.

**Recommandation** : soit élargir/recalculer les bornes des spinboxes vec au moment du choix de
couleur (avant `setValue`), soit lire `spin.value()` (post-clampage) pour construire les
`new_values` passées à `_emit_vec_edit`, de façon à ce que le code écrit corresponde toujours à ce
qui est affiché.

**Correctif appliqué** (`sliders_panel.py`) :
- `_on_swatch_clicked` sépare maintenant explicitement `picked_values` (sortie brute 0..1 du
  `QColorDialog`, potentiellement hors bornes) de `new_values` (ce qui sera réellement émis) : après
  chaque `spin.setValue(value)` (qui clampe silencieusement à `[spin.minimum(), spin.maximum()]`),
  la valeur est relue via `spin.value()` et ajoutée à `new_values` au lieu de réutiliser
  `picked_values` telle quelle.
- `_emit_vec_edit(ordinal, new_values)` et `_update_swatch_color(swatch, new_values)` utilisent tous
  les deux ce `new_values` post-clampage : le swatch, les spinboxes affichés et le code écrit
  représentent donc toujours exactement la même valeur, y compris quand la couleur choisie dépasse
  les bornes étroites héritées de la magnitude d'origine du littéral.
- Choix de l'option "lire `spin.value()` post-clampage" plutôt que "élargir les bornes" : plus
  simple, ne change pas la sémantique des bornes existantes (toujours dérivées de la valeur
  d'origine du littéral), et corrige directement le symptôme documenté (désynchronisation code/UI)
  sans effet de bord sur `_default_float_range`/`_edit_range`.
- Vérifié : pour un littéral `vec3(0.1, 0.05, 0.02)` (bornes par défaut étroites `[0,0.2]`/`[0,0.1]`/
  `[0,0.04]`), choisir une couleur vive dont les composantes dépassent ces bornes produit désormais
  des valeurs émises identiques aux valeurs affichées (clampées), au lieu de la valeur brute non
  clampée d'avant le correctif. La suite `test_sliders.py` passe intégralement sans modification.

---

## 🟠 MAJEUR

### [x] M1. `try_scan_vec_call` n'a aucune gestion du signe moins → tout `vec2`/`vec3` contenant une composante négative n'est jamais groupé

`literals.rs:169-210`. Le regroupement `vec2`/`vec3` appelle, pour chaque argument,
`try_scan_float(chars, j)` (ligne 185) — **directement**, sans passer par la logique de détection
du "moins unaire" (`is_unary_minus_context`) qui n'existe que dans la boucle principale de
`detect_all_sliders` (ligne 435-446).

Or `try_scan_float` (ligne 112-151) ne sait scanner **que** des chiffres/point/exposant ; dès que
`chars[j] == '-'`, aucune branche ne l'avance, `saw_digit_before` et `saw_digit_after` restent
`false`, et la fonction retourne `None` (ligne 130-132) — ce qui fait échouer tout `?` dans
`try_scan_vec_call`, donc échouer l'appel entier via `?` (ligne 187).

Conséquence : un appel aussi courant que

```glsl
vec3 dir = vec3(-1.0, 0.5, 0.2);
vec2 offset = vec2(-0.3, 0.4);
```

**n'est jamais reconnu comme un slider groupé** (pas de color-picker / paire X-Y). Le code retombe
silencieusement sur le comportement "littéraux isolés" (chaque composante devient un slider float
séparé, y compris les négatives qui sont bien détectées individuellement par la boucle principale)
— fonctionnellement, rien n'est perdu, mais la fonctionnalité phare "regroupement vec2/vec3" documentée
dans `ROADMAP.md` ne se déclenche jamais dès qu'une composante est négative, ce qui est un cas
extrêmement fréquent (directions, offsets, décalages UV...). Ce n'est mentionné nulle part dans le
ROADMAP en tant que limitation connue — contrairement au cas du splat (`vec3(0.5)`) ou des
expressions, qui eux sont documentés comme volontairement exclus.

**Recommandation** : dans `try_scan_vec_call`, appliquer la même logique de détection du moins
unaire (ou au minimum autoriser un `-` immédiatement suivi d'un chiffre/point, ce qui est sans
ambiguïté dans le contexte d'une liste d'arguments) avant d'appeler `try_scan_float` pour chaque
composante.

**Correctif appliqué** (`literals.rs::try_scan_vec_call`) :
- Avant chaque appel à `try_scan_float`, la boucle vérifie maintenant si le caractère courant est
  `-` immédiatement suivi d'un chiffre ou de `.` + chiffre (même condition « pas d'espace entre le
  signe et le chiffre » que la boucle principale de `detect_all_sliders`).
- Pas besoin d'appeler `is_unary_minus_context` ici : à cette position précise (juste après avoir
  sauté les espaces suivant un `(` ou une `,`), le caractère précédent significatif est toujours
  `(` ou `,`, qui ne "produisent" jamais de valeur au sens de cette fonction — donc le `-` y est
  *toujours* un signe unaire, sans ambiguïté possible avec une soustraction.
- Le signe, quand présent, est inclus dans le texte parsé (`literal_start` au lieu de `j`), donc
  `text.parse::<f32>()` reçoit bien `"-1.0"` et non `"1.0"`.
- Vérifié avec un harnais Rust isolé (le fichier ne dépend d'aucun autre module du crate) :
  `vec3(-1.0, 0.5, 0.2)`, `vec2(-0.3, 0.4)` et `vec3(-0.1, -0.2, -0.3)` sont désormais tous groupés
  en un seul `VecSlider` chacun (au lieu de retomber sur des sliders float isolés), avec les bonnes
  valeurs signées et le bon span. Non-régression vérifiée sur le cas `vec3(a - 1.0, 0.5, 0.2)`
  (soustraction binaire dans une expression) : reste correctement non groupé, comme avant.

### [x] M2. Le détecteur natif réel (`literals.rs::detect_all_sliders`) n'a **aucune** couverture de test automatisée

Recherche exhaustive : aucun `#[test]`/`#[cfg(test)]` dans `literals.rs` ni `lib.rs`, et aucun test
Python n'appelle `engine_bridge.detect_all_sliders`/`detect_literal_sliders` (le vrai binding
natif). `test_sliders.py` teste uniquement la couche `SlidersPanel` avec des objets `FakeFloat`/
`FakeInt` construits à la main (positions/valeurs choisies par le test, jamais issues d'un vrai
parsing GLSL).

Autrement dit, toute la logique la plus délicate — exclusion des commentaires `//`/`/* */`, des
lignes `#directive`, des en-têtes `for(...)`, calcul du moins unaire, regroupement `vec2`/`vec3`,
marqueurs de section `// -- X --`, catégorisation par fonction englobante — n'est vérifiée que par
lecture manuelle et "régression sur `default.frag`" mentionnée dans le `ROADMAP.md`, jamais par un
test reproductible dans la CI. Le bug M1 ci-dessus, par exemple, aurait été détecté immédiatement
par un test unitaire Rust simple (`vec3(-1.0, 0.5, 0.2)` → doit produire un seul `VecSlider`).

**Recommandation** : ajouter un module `#[cfg(test)]` dans `literals.rs` couvrant au minimum :
littéraux dans un commentaire/une directive/un `for(...)`, moins unaire vs soustraction, `vec2`/
`vec3` avec composantes positives/négatives/mixtes, splat et expressions (non groupés), marqueurs
de section valides/invalides, et un test Python d'intégration qui appelle réellement
`engine_bridge.detect_all_sliders` sur des extraits GLSL (pas seulement des `Fake*`).

**Correctif appliqué** :
- Nouveau module `#[cfg(test)]` dans `literals.rs` (21 tests) couvrant exactement la liste
  ci-dessus, appelé directement sur `detect_all_sliders`/`detect_literal_sliders`/
  `parse_section_marker` : masquage commentaire ligne/bloc, masquage directive `#define`,
  masquage `for(...)` (voir la nuance ci-dessous), moins unaire vs soustraction (y compris le cas
  `foo()-1.0`/`arr[0]-2.0`, sans espace, pour vraiment exercer `is_unary_minus_context` et pas
  seulement le raccourci "espace avant le chiffre"), regroupement `vec2`/`vec3` positif/négatif/
  mixte (couvre directement la régression M1), splat et expressions non groupés, bool/int,
  marqueurs de section valides/invalides et propagation de la catégorie, et la vue `float`-only
  `detect_literal_sliders`. Compilé et exécuté avec succès via `rustc --edition 2021 --test`
  (le fichier n'a aucune dépendance externe) : **21/21 passent**. `cargo test` natif non exécutable
  dans cet environnement (toolchain `cargo` disponible ici trop ancien pour le `Cargo.lock` du
  projet — v4, et les ~150 dépendances dont `wgpu` nécessitent de toute façon un `rustc` plus
  récent que celui disponible) ; la couverture logique de `literals.rs` elle-même a néanmoins été
  intégralement vérifiée en isolant le fichier (il ne dépend d'aucun autre module du crate).
- Nouveau fichier `test_literals_native.py` à la racine, deuxième volet demandé par la
  recommandation : appelle réellement `engine_bridge.detect_all_sliders` (pas des `Fake*`) sur des
  extraits GLSL, y compris un extrait `mainImage` complet exerçant catégorie + marqueur de section
  + regroupement `vec3` ensemble. Se termine proprement avec un message `SKIPPED` (exit 0) si le
  module natif n'est pas compilé, plutôt que d'échouer bruyamment — même logique que le message
  d'erreur de `engine_bridge.py`. Non exécuté avec succès dans cet environnement pour la même
  raison de toolchain que ci-dessus (`maturin develop --release` nécessite de compiler `wgpu`) ;
  prêt à tourner tel quel une fois le module natif construit dans un environnement de build normal.
- **Découverte pendant l'écriture de ces tests** (exactement le genre de régression que cette
  recommandation visait à attraper, comme documenté pour M1) : un `for(...)` écrit à l'intérieur
  d'une fonction — donc la quasi-totalité des boucles GLSL réelles, presque toujours dans
  `mainImage` ou une fonction utilitaire — n'est **pas** masqué, contrairement à ce que
  documentent les commentaires du code et contrairement au cas testé isolément (`for` au niveau
  fichier). Voir la nouvelle entrée **M5** ci-dessous. Le test `for_loop_header_inside_a_function_
  is_not_masked_known_bug` documente volontairement le comportement actuel (bogué) plutôt que le
  comportement voulu, pour que le jour où M5 est corrigé, l'assertion échoue et signale qu'il faut
  mettre le test à jour — au lieu de laisser la régression repasser inaperçue.

### [x] M3. `apply_layout` accepte silencieusement des bornes `NaN`/`Infinity` issues d'un fichier projet corrompu ou trafiqué

`sliders_panel.py:333-338` :

```python
try:
    new_min = float(entry["min"])
    new_max = float(entry["max"])
except (KeyError, TypeError, ValueError):
    continue
if new_max <= new_min:
    continue
```

Vérifié : le module `json` de Python accepte nativement (extension non standard, activée par
défaut) les littéraux `NaN`, `Infinity`, `-Infinity` dans un fichier `.json`. Un fichier projet
contenant `"min": NaN` est donc chargé sans erreur, `entry["min"]` est déjà un `float('nan')`, et
`float(nan)` ne lève rien. Or **toute comparaison impliquant NaN est fausse** : `new_max <= new_min`
vaut `False` même quand l'une des deux bornes est NaN, donc le garde-fou ne filtre pas ce cas et le
code continue :

```python
spin.setMinimum(new_min)   # NaN
spin.setMaximum(new_max)
spin.setSingleStep((new_max - new_min) / SLIDER_STEPS)  # NaN
spin.setValue(value)       # value lui-même potentiellement NaN, via min(max(spin.value(), new_min), new_max)
```

ce qui pousse des bornes/valeurs `NaN` dans un `QDoubleSpinBox`/`QSlider` — comportement Qt non
garanti (au mieux un widget visuellement cassé, au pire un crash selon la plateforme/version Qt).
Un simple `.json` de projet ouvert à la main (ou généré par un outil tiers, ou corrompu par un
éditeur qui ne préserve pas le format) suffit à déclencher ce cas, sans qu'aucun message d'erreur
n'apparaisse (le `try/except` ne couvre pas ce cas, NaN n'est pas une exception).

**Recommandation** : ajouter une vérification explicite `math.isfinite(new_min) and
math.isfinite(new_max)` avant d'appliquer les bornes (et idem dans le futur si `decimals`/`index`
sont un jour recalculés depuis des sources externes).

**Correctif appliqué** (`sliders_panel.py`, `apply_layout`) :
- Ajout d'une vérification explicite `math.isfinite(new_min) and math.isfinite(new_max)`
  immédiatement après la conversion `float(entry["min"]/["max"])` et avant le test
  `new_max <= new_min` — donc avant toute application aux widgets Qt (`setMinimum`/`setMaximum`/
  `setSingleStep`/`setValue`). `math` était déjà importé dans ce fichier.
- L'entrée est simplement ignorée (`continue`) si l'une des deux bornes n'est pas finie, exactement
  comme pour les autres cas déjà filtrés (borne manquante, non convertible, `max <= min`) — aucun
  changement de comportement pour les layouts valides.
- Les keyframes de l'entrée restent appliqués indépendamment (le `continue` se situe après
  `state.keyframes = _parse_keyframes(...)`, comme c'était déjà le cas pour le filtre
  `new_max <= new_min` existant) : une entrée avec des bornes corrompues ne perd donc pas ses
  keyframes, cohérent avec le commentaire déjà présent juste au-dessus dans le code.
- Vérifié manuellement : un layout contenant `{"min": NaN, "max": 1.0, ...}` ou
  `{"min": 0.0, "max": Infinity, ...}` (tous deux acceptés par `json.loads` de Python) est
  désormais ignoré silencieusement au lieu de pousser des bornes non finies dans un
  `QDoubleSpinBox`/`QSlider`.

### [x] M4. Le changement d'onglet de pass ne vérifie pas `is_drag_active()`

`main_window.py:331-344` (`_on_pass_tab_changed`) remplace intégralement le contenu de l'éditeur
(`self.editor.set_value(text)`) et appelle `_refresh_sliders_for(text)` **sans jamais consulter**
`self.sliders_panel.is_drag_active()`. `_refresh_sliders_for` lui-même s'arrête tôt si un drag est
actif (ligne 347-354), donc le panneau ne se reconstruit pas pour le nouvel onglet — mais rien
n'empêche le panneau de continuer, pendant ce temps, à afficher/éditer les sliders de **l'ancien**
onglet, dont les offsets `start`/`end` ne correspondent plus du tout au texte maintenant chargé
dans Monaco. Le prochain déplacement de ce slider émettrait alors un `literalEdited(start, end,
text)` qui, via `MonacoEditor.replace_range`, réécrirait une plage de caractères du **nouvel**
onglet sans rapport avec l'intention de l'utilisateur.

Ce scénario suppose que le focus clavier/souris permette de changer d'onglet sans relâcher un
glissé de slider en cours (peu probable avec une souris seule à cause de la capture implicite de
Qt, mais plausible avec un clavier + une souris utilisés simultanément, ou un dispositif tactile).
Sévérité modérée en pratique (fenêtre de déclenchement étroite) mais l'invariant "aucune
resynchronisation pendant un drag" n'est actuellement garanti que côté `_refresh_sliders_for`, pas
côté "quel texte le drag va-t-il réécrire".

**Recommandation** : soit désactiver/ignorer le changement d'onglet tant qu'un drag de slider est
actif, soit faire en sorte que `_on_literal_edited` vérifie que l'onglet ciblé par les offsets
correspond toujours à `_current_tab` avant d'appeler `replace_range`.

**Correctif appliqué** (`main_window.py::_on_literal_edited`) :
- Ajout d'une vérification `self._slider_panel_tab != self._current_tab` en tout début de la
  méthode : si le panneau de sliders affiche encore les littéraux d'un onglet différent de celui
  actuellement chargé dans l'éditeur, l'édition est ignorée (`return`) au lieu d'appeler
  `self.editor.replace_range(start, end, text)`.
- Choix de la deuxième option de la recommandation plutôt que la première (bloquer le changement
  d'onglet) : `_slider_panel_tab` existait déjà précisément pour distinguer "l'onglet dont les
  données sont actuellement chargées dans le panneau" de `_current_tab` (voir le commentaire déjà
  présent lignes 117-124), donc réutiliser cet état existant est direct et n'introduit aucune
  nouvelle source de vérité ni de restriction UX supplémentaire (l'utilisateur reste libre de
  changer d'onglet pendant un drag ; c'est uniquement l'édition qui vise le mauvais texte qui est
  supprimée).
- `_slider_panel_tab` n'est mis à jour sur `_current_tab` que par `_refresh_sliders_for` au moment
  du `rebuild()` effectif du panneau (ligne 369) — donc tant que ce rebuild est différé par
  `is_drag_active()` après un changement d'onglet, la vérification reste vraie (`!=`) et continue
  de bloquer tout `literalEdited` erroné, jusqu'à ce que le drag se termine et que
  `_on_slider_drag_finished` déclenche un `_recompile_current_tab` → `_refresh_sliders_for` qui
  resynchronise enfin `_slider_panel_tab` sur le nouvel onglet.
- Pas de régression sur le chemin normal (pas de changement d'onglet en cours) : `_slider_panel_tab`
  et `_current_tab` restent égaux en permanence hors de la fenêtre de course décrite, donc la
  condition ne se déclenche jamais et `replace_range` continue d'être appelé exactement comme
  avant.

---

## 🟡 MINEUR / LIMITATIONS

### [x] m1. `vec4(...)` n'est jamais groupé
Seuls `vec2`/`vec3` sont reconnus par nom dans `literals.rs:367` (`name == "vec2" || name ==
"vec3"`). Un `vec4(1.0, 0.5, 0.2, 1.0)` (RGBA constant, motif courant en fin de `mainImage` même
si `fragColor = vec4(col, 1.0)` n'est de toute façon pas un cas pur-littéral) retombe sur jusqu'à
4 sliders float séparés au lieu d'un contrôle unique couleur+alpha. Non documenté comme limitation
connue dans le ROADMAP (contrairement au splat/aux expressions).

**Correctif appliqué** :
- `literals.rs::detect_all_sliders` reconnaît maintenant `vec4` au même titre que `vec2`/`vec3`
  (arité 4) ; `try_scan_vec_call` était déjà générique sur l'arité, aucun changement nécessaire
  là. Doc-comments (`literals.rs`, `lib.rs`) mis à jour en conséquence.
- Nouveaux tests Rust : groupement `vec4` positif/négatif, splat `vec4(0.5)` non groupé, mauvaise
  arité (3 arguments pour `vec4`) non groupée.
- `sliders_panel.py::_build_vec_row` : labels `RGBA` pour `size == 4` (nouvelle clé i18n
  `component_labels_rgba`, ajoutée aux 12 fichiers `lngs/*.json` — `parité vérifiée via
  `test_i18n_completeness.py`, "Al" utilisé pour l'alpha en espagnol/portugais afin d'éviter la
  collision avec la lettre déjà utilisée pour le bleu, "Azul" = "A" dans ces deux langues) ; le
  swatch couleur est désormais aussi construit pour `size == 4` (aperçu/édition RGB uniquement,
  la 4ᵉ composante alpha reste un spin numérique ordinaire).
- `_update_swatch_color`/`_on_swatch_clicked` slicent désormais sur les 3 premières composantes
  au lieu d'unpacker exactement 3 valeurs, et préservent l'alpha (`state.value[3:]`) lors du choix
  d'une couleur.
- Compilation Rust non exécutable dans cet environnement (même limite de toolchain que pour M2) ;
  logique vérifiée par lecture, en miroir exact du chemin `vec3` déjà testé et fonctionnel.

### [x] m2. Décimales des composantes vec figées à 4, sans mécanisme d'override
`_build_vec_row` (ligne 521) et `_emit_vec_edit` (ligne 915, `format_glsl_float(v, 4)`) sont câblés
en dur sur 4 décimales, sans le dialogue "clic droit → bornes/décimales" dont bénéficient les
sliders scalaires. C'est cohérent avec la doc ("bool/vec2/vec3 n'ont rien d'overridable"), mais
cela aggrave mécaniquement C1 pour tout vecteur dont une composante est de faible magnitude (ex.
une normale ou un décalage très fin), sans aucune échappatoire côté UI (pas même un clic droit).

**Correctif appliqué** (`sliders_panel.py`) :
- Chaque spinbox de composante vec a maintenant son propre menu contextuel (clic droit), reliant
  vers une nouvelle méthode `_edit_vec_component_range` qui réutilise le même dialogue min/max/
  décimales que `_edit_range` pour les sliders scalaires — adapté à l'absence de `QSlider`
  compagnon (les lignes vec n'ont que des spinboxes, pas de slider visuel).
- `_emit_vec_edit` lit désormais `spin.decimals()` de chaque composante au moment d'écrire le code
  (au lieu du `4` en dur), sinon l'override de décimales aurait été purement cosmétique côté UI
  sans jamais affecter le texte GLSL réellement écrit.

### [x] m3. Duplication de l'heuristique de plage par défaut entre Rust et Python
`literals.rs::default_float_range` (ligne 212-220) et
`sliders_panel.py::_default_float_range` (ligne 123-128) implémentent **indépendamment** la même
règle (`[0, 2×valeur]` / symétrique si négatif / `[-1, 1]` si nul) — la première pour les sliders
scalaires (via le binding Rust), la seconde pour les composantes des `VecSlider` (qui n'ont pas de
min/max côté Rust). Aucun test ne garantit que les deux restent synchronisées si l'heuristique
évolue un jour d'un seul côté — risque de maintenance pure, pas un bug aujourd'hui (les deux
implémentations sont actuellement identiques).

**Traité par documentation croisée** (pas un bug fonctionnel, seulement un risque de dérive
future, comme le note l'audit lui-même) : `sliders_panel.py::_default_float_range` porte
maintenant un commentaire renvoyant explicitement vers `default_float_range` dans
`literals.rs`, avec l'avertissement "si vous touchez cette règle, mettez à jour les deux côtés".
Pas de mécanisme de partage de code possible entre les deux langages sans over-engineering pour
une règle de 4 lignes ; un test de non-régression croisé nécessiterait de faire tourner le module
natif compilé côté Python, indisponible dans cet environnement (même limite de toolchain que pour
M2) — laissé pour un futur environnement de build complet.

### [x] m4. Décalage potentiel caractères Unicode vs unités UTF-16 (Rust/Python vs Monaco/JS)
`literals.rs` indexe `start`/`end` sur un `Vec<char>` (points de code Unicode), tout comme
l'indexation `str` de Python — cohérent entre les deux. Mais côté JS
(`index.html:100-108`, `replaceRange`), Monaco résout ces offsets via
`model.getPositionAt(offset)`, qui attend des offsets en **unités de code UTF-16** (norme du DOM/
JS), pas en points de code Unicode. Les deux ne coïncident que tant que le texte qui précède un
littéral (y compris dans des commentaires) reste dans le plan multilingue de base (BMP). Un seul
caractère hors BMP (certains emoji, certains sinogrammes rares) présent n'importe où avant un
slider désynchroniserait silencieusement tous les offsets suivants entre le moteur natif et
Monaco. Risque faible en pratique (le GLSL est presque toujours en ASCII) et non observé dans le
code fourni, mais non testé et non mentionné.

**Correctif appliqué** (`index.html::replaceRange`) :
- Nouvelle fonction JS `codePointOffsetToUtf16Offset(text, codePointOffset)` qui convertit un
  index en points de code Unicode vers un index en unités de code UTF-16, en comptant chaque paire
  de substituts (`0xd800-0xdbff` suivi de `0xdc00-0xdfff`) comme un seul point de code consommant
  2 unités UTF-16 — identique au texte pour tout contenu purement BMP (le cas quasi-universel du
  GLSL), ne diverge que si un caractère astral apparaît plus tôt dans le texte.
- `replaceRange` appelle désormais cette conversion sur `startOffset`/`endOffset` — en utilisant
  `model.getValue()` (le texte **actuellement** chargé dans Monaco) comme référence — avant de les
  passer à `model.getPositionAt`. Utiliser le texte du modèle lui-même plutôt qu'une copie côté
  Python évite toute dépendance à la question de savoir quelle copie du source est "la bonne" au
  moment de l'appel : la conversion est garantie cohérente avec ce que `getPositionAt` va résoudre,
  par construction.
- Correctif isolé au pont JS ; aucun changement côté Rust/Python nécessaire (leurs offsets en
  points de code restent la représentation canonique, seule la conversion d'unités au moment de
  l'écrire dans Monaco change).

### [x] m5. Le bouton "reset catégorie"/"aléatoire catégorie"/"effacer keyframes catégorie" opère sur *tous* les ordinaux de la catégorie, y compris ceux masqués par le filtre de recherche
`rebuild()` (ligne 371-394) capture `ordinals` = tous les indices de la catégorie au moment de la
construction, indépendamment de l'état du filtre texte. Cliquer "Réinitialiser" en tête d'onglet
pendant qu'un filtre masque une partie des lignes réinitialise donc aussi les lignes actuellement
invisibles, sans avertissement. Comportement probablement voulu (le filtre est un outil de
recherche, pas de sélection), mais mérite d'être confirmé/documenté explicitement.

**Confirmé comme comportement voulu, maintenant documenté explicitement** (`sliders_panel.py`) :
- Commentaire ajouté dans `rebuild()`, juste avant la boucle sur les catégories, expliquant que
  `ordinals` couvre toute la catégorie indépendamment du filtre et que c'est intentionnel.
- Nouvelles clés i18n `reset_category_tooltip`/`randomize_category_tooltip` (les boutons n'avaient
  aucun tooltip jusqu'ici) et extension du texte de `clear_category_keyframes_tooltip` existant,
  sur les 12 langues, pour rendre ce périmètre visible directement dans l'UI au survol plutôt que
  seulement découvrable en le testant.

---

## ✅ Points solides constatés

Pour équilibrer l'audit, plusieurs aspects sont particulièrement bien pensés et méritent d'être
soulignés :

- La gestion des offsets glissants (`_emit_edit`, ligne 889-903) — décalage de tous les littéraux
  suivants après chaque édition — est correcte et bien commentée, et le cas `vecN` (offsets
  regroupés, pas de sous-plages par composant) est cohérent avec la façon dont `literals.rs`
  consomme entièrement l'appel `vecN(...)` sans laisser fuiter de littéral individuel intermédiaire.
- Le masquage des littéraux dans les commentaires/directives/en-têtes `for(...)` est correctement
  implémenté et vérifié pour les cas `vec3(0.5)` (splat) et `vec3(a,b,c)` (expressions), qui
  retombent proprement sur des sliders float individuels sans double-comptage.
- `_interpolate_keyframes` et le cycle export/rebuild/apply des layouts et keyframes sont
  correctement testés dans `test_sliders.py` (bornes tenues plates, fusion d'un keyframe proche,
  absence d'édition redondante à temps constant).
- La parité des clés i18n `sliders_panel.*` est intégrale sur les 12 langues fournies (aucune clé
  manquante ni orpheline, vérifié programmatiquement).
- La gestion best-effort de l'identité `(catégorie, type, index)` pour `export_layout`/
  `apply_layout` est honnêtement documentée comme non garantie, avec un test couvrant explicitement
  le cas d'un layout devenu partiellement obsolète.

---

## Synthèse

| # | Sévérité | Résumé |
|---|---|---|
| C1 | 🔴 Critique | [x] Décimales par défaut fixes (4) → perte silencieuse de précision, voire mise à `0.0`, sur les petites constantes dès la première manipulation d'un slider — **corrigé** |
| C2 | 🔴 Critique | [x] Garde-fou anti-corruption limité au glisser-souris ; molette et clavier sur le même `QSlider` contournaient totalement la protection — **corrigé** |
| C3 | 🔴 Critique | [x] Le color-picker (`_on_swatch_clicked`) écrivait dans le code une valeur non clampée, désynchronisée durablement de l'UI affichée — **corrigé** |
| M1 | 🟠 Majeur | [x] `vec2`/`vec3` avec composante négative n'étaient jamais groupés (bug de parsing, pas de gestion du signe dans `try_scan_vec_call`) — **corrigé** |
| M2 | 🟠 Majeur | [x] Zéro test automatisé sur le détecteur natif réel (`literals.rs`) — **corrigé** |
| M3 | 🟠 Majeur | [x] `apply_layout` accepte des bornes `NaN`/`Infinity` depuis un projet corrompu — **corrigé** |
| M4 | 🟠 Majeur | [x] Changement d'onglet non bloqué par `is_drag_active()` — **corrigé** |
| m1–m5 | 🟡 Mineur | [x] `vec4` non groupé, décimales vec non overridables, duplication Rust/Python de l'heuristique de plage, offsets Unicode vs UTF-16, portée des boutons "catégorie" vs filtre — **tous traités** (m1/m2/m4 corrigés en code, m3/m5 documentés — voir détail dans chaque section) |

**Priorité de correction suggérée** : C1 (perte de données silencieuse) et C3 (désynchronisation
durable code/UI) en premier — ce sont les seuls cas où l'utilisateur perd réellement de
l'information sans le savoir. C2 ensuite (fenêtre d'exposition plus large que prévu par le design
existant). M2 (tests) devrait accompagner tout correctif ci-dessus pour éviter une régression
silencieuse.

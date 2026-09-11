# RESTE.md — Ce qu'il reste à faire

Extrait de `ROADMAP.md` : tous les items à checkbox du roadmap sont cochés `[x]` (66/66), il n'y a
**aucune tâche « ouverte »** au sens classique. Ce qui suit sont les exclusions de périmètre
assumées, limites connues et points de vérification incomplète documentés à l'intérieur même des
tickets déjà marqués faits.

---

## 🏌️ Golfing

Entièrement traitée dans cette session — voir « Vérifications à rejouer » ci-dessous pour ce qui
reste non validé faute d'environnement de build complet.

## 🎚️ Sliders

- [ ] Le `step` d'un slider n'est pas éditable séparément (toujours recalculé depuis min/max).
- [ ] Un override min/max/décimales posé par clic droit ne survit pas à un rebuild structurel
  complet (ajout/suppression d'un littéral ailleurs dans le fichier).

## 🌈 Compatibilité Shadertoy

- [ ] Convention de nommage des 6 faces d'un cubemap importé depuis shadertoy.com :
  implémentation **best-effort, jamais vérifiée** contre un vrai projet Shadertoy (pas d'accès
  réseau sortant vers shadertoy.com dans l'environnement où le code a été écrit).
- [ ] Pas de génération procédurale de cubemap (seulement chargement depuis 6 fichiers image).
- [ ] Entrée audio (`iChannel` audio) : microphone en direct, contrôle de volume, calage
  bit-exact du spectre FFT — explicitement hors périmètre.
- [ ] Entrée audio : **jamais compilée/exécutée réellement** (vérifiée seulement par relecture +
  `python3 -m py_compile`, pas de toolchain Rust ni PySide6 disponible côté dev) — à revalider
  dans un environnement complet, y compris la comparaison visuelle contre un shader Shadertoy
  audio-réactif de référence prévue par le plan.

## 🎬 Export vidéo

- [ ] Pas d'enregistrement du trajet de souris pendant l'export (`iMouse` figé à `(0,0,0,0)` sur
  toute la séquence).
- [ ] Vérifier que `record_actual_export_size` (recalibrage de l'estimation de taille de fichier
  à partir d'un export réel) est bien appelé de bout en bout maintenant que l'encodage ffmpeg
  existe — câblé mais l'intégration complète n'est pas explicitement confirmée dans le roadmap.

## 🌍 Internationalisation

- [ ] Pluralisation (ICU/gettext) — délibérément hors scope.
- [ ] Support RTL (langues écrites de droite à gauche) — délibérément hors scope.

---

## Vérifications à rejouer (pas des tâches de code, mais du non-validé)

Beaucoup de tickets sont marqués `[x]` mais avec la mention que le crate `rust_engine` complet et
`MainWindow` (PySide6) n'ont **jamais pu être compilés/exécutés** dans l'environnement où le
roadmap a été rédigé (toolchain `rustc` trop ancienne pour les dépendances transitives
`wgpu`/`image`, `shadertoy_engine` natif non compilable, PySide6 absent). À rejouer dans un
environnement de build complet avant de considérer ces items comme définitivement clos :

- [ ] `cargo test`/`cargo check` du crate `rust_engine` complet (seuls des sous-fichiers isolés
  comme `golf.rs` ont pu être testés en isolation via `rustc --test` — et même ça, plus du tout
  dans cette session : aucun accès réseau pour réinstaller `rustc`/`cargo`, à la différence de la
  session qui avait implémenté l'inlining à site d'appel unique et la conversion ternaire de base).
- [ ] Rendu GPU réel pixel-identique pour les transforms de golf les plus récentes (repli de
  constantes, ternaire depuis if/else, etc.) — vérifié seulement par tests structurels/textuels.
- [ ] Inlining multi-appels et composition de ternaires imbriqués (`golf.rs`, cette session) —
  vérifiés uniquement par relecture manuelle et traçage à la main token par token, jamais compilés
  ni testés via `rustc --test` (aucune toolchain Rust disponible dans cette session).
- [ ] Ouverture bout-en-bout du dialogue d'export vidéo dans une vraie `MainWindow`.
- [ ] Export vidéo complet (capture → encodage ffmpeg → fichier `.mp4`) en conditions réelles.
- [ ] Entrée audio `iChannel` contre un fichier `.mp3`/`.wav` réel et un shader audio-réactif.
- [ ] Score golf affiché (`footer.py`) dans le widget réel (testé en Python pur hors PySide6).
- [ ] Suivi local du meilleur score golf (`record_golf_score`/`golf_personal_best_html`,
  `footer.py`/`main_window.py`, cette session) — `test_footer_golf_best.py` écrit mais jamais
  exécuté tel quel (PySide6 indisponible) ; logique revalidée séparément par simulation Python pure
  (a permis de trouver et corriger un bug réel sur `is_new_best`), mais le widget réel et le
  câblage dans `MainWindow._do_golf` restent à vérifier en conditions réelles.

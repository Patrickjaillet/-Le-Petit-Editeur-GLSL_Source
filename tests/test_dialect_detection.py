"""Tests dédiés à la détection automatique de dialecte (roadmap1.md,
section "✅ Vérification", complété par RMLG.md section 1.5 pour le
scénario WGSL).

Complète les tests unitaires Rust déjà présents dans
`rust_engine/src/dialect.rs::tests` en exerçant la *vraie* extension
native compilée depuis le côté Python, exactement comme le fait
`main_window.py` (`engine_bridge.detect_dialect` + `Engine.compile_pass`) --
et pas seulement la logique de détection isolée : les shaders GLSL/WGSL
"manuels" testés ici sont réellement soumis au moteur wgpu et leur rendu
est vérifié pixel par pixel, pas seulement "ça ne lève pas d'exception".

Quatre scénarios :

1. Import d'un shader Shadertoy connu du dépôt (`default.frag`, celui-là
   même que `pixel_compare.py` utilise comme référence) -> mode Shadertoy
   affiché.
2. Collage d'un shader GLSL "manuel" classique (`void main(){
   gl_FragColor = ...; }`) -> mode GLSL affiché ET shader qui compile
   *réellement* (pas seulement détecté : rendu et pixel vérifié).
3. Comportement en cours de frappe : partir d'un shader Shadertoy valide,
   retirer `mainImage` progressivement en tapant un `void main()` à la
   place, confirmer que le mode ne bascule qu'une fois `void main()`
   effectivement complet -- jamais avant, jamais de valeur par défaut
   arbitraire entre les deux -- en simulant exactement l'algorithme de
   `main_window.py::_update_dialect_indicator` (passer le dialecte
   précédent à chaque frappe, comme le fait le vrai debounce).
4. (RMLG.md, section 1.5) Collage d'un shader WGSL "manuel"
   (`@fragment fn main() -> @location(0) vec4<f32> { ... }`) -> mode
   WGSL affiché ET shader qui compile *réellement* via
   `wgsl_passthrough_backend`/la réflexion de bind group group0 de
   `renderer.rs`, rendu et pixel vérifié comme pour le scénario GLSL du
   point 2 -- pas seulement "ça ne lève pas".

Nécessite le module natif compilé (`cd rust_engine && maturin develop
--release`, voir le message d'erreur d'`engine_bridge`) et un adaptateur
graphique wgpu utilisable (les scénarios 2 et 4 rendent réellement une
frame) --
absent, ce fichier se SKIP proprement plutôt que d'échouer bruyamment sur
un problème d'environnement, même principe que `test_literals_native.py`.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python_ui"))

try:
    import engine_bridge
except ImportError as exc:
    print(f"SKIPPED: native module not built ({exc}); "
          f"run 'cd rust_engine && maturin develop --release' first.")
    sys.exit(0)

DEFAULT_FRAG_PATH = os.path.join(PROJECT_ROOT, "python_ui", "assets", "shaders", "default.frag")


# ---- 1. Import d'un shader Shadertoy connu -----------------------------

known_shadertoy_source = open(DEFAULT_FRAG_PATH, encoding="utf-8").read()
assert "mainImage" in known_shadertoy_source, (
    "default.frag n'est plus un shader Shadertoy -- ce test doit être "
    "mis à jour en même temps que le shader par défaut."
)

dialect_id, signal_key = engine_bridge.detect_dialect(known_shadertoy_source, "")
assert dialect_id == engine_bridge.DIALECT_SHADERTOY, (
    f"default.frag (shader Shadertoy connu du dépôt) devrait être détecté "
    f"comme '{engine_bridge.DIALECT_SHADERTOY}', obtenu '{dialect_id}'"
)
assert signal_key == "footer.dialect_signal_mainimage"
print("import d'un shader Shadertoy connu -> mode Shadertoy: ok")


# ---- 2. Shader GLSL "manuel" classique : détecté ET compile réellement --

manual_glsl_source = """
void main() {
    gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0);
}
"""

dialect_id2, signal_key2 = engine_bridge.detect_dialect(manual_glsl_source, "")
assert dialect_id2 == engine_bridge.DIALECT_GLSL, (
    f"un void main(){{ gl_FragColor = ...; }} classique devrait être détecté "
    f"comme '{engine_bridge.DIALECT_GLSL}', obtenu '{dialect_id2}'"
)
assert signal_key2 == "footer.dialect_signal_voidmain"

try:
    engine = engine_bridge.Engine(64, 64)
except RuntimeError as exc:
    print(f"SKIPPED (scénario 2 et 3, rendu réel): pas d'adaptateur wgpu "
          f"disponible dans cet environnement ({exc}).")
    sys.exit(0)

engine.compile_pass(engine_bridge.PASS_IMAGE, manual_glsl_source)
# Le tout premier appel à render() sur un Engine fraîchement créé renvoie
# des pixels entièrement à zéro dans ce genre d'environnement (frame de
# "chauffe", voir pixel_compare.py) ; un second appel est nécessaire.
engine.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 8.0, 17.0, 0.0))
pixels = bytes(engine.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 8.0, 17.0, 0.0)))
assert len(pixels) == 64 * 64 * 4
r, g, b, a = pixels[0], pixels[1], pixels[2], pixels[3]
assert (r, g, b, a) == (255, 0, 0, 255), (
    f"le pipeline GLSL standalone a compilé sans erreur mais rend le mauvais "
    f"pixel: attendu rouge opaque (255,0,0,255), obtenu {(r, g, b, a)}"
)
print("collage d'un shader GLSL manuel -> mode GLSL, et le shader compile "
      "et rend réellement le bon pixel: ok")


# ---- 3. Bascule en cours de frappe : mainImage -> void main() ----------

# Simule exactement l'algorithme de
# `main_window.py::_update_dialect_indicator` : le dialecte précédemment
# affiché est repassé au détecteur à chaque étape, comme le fait le vrai
# debounce (jamais recalculé "à froid" sans mémoire du mode précédent).
typing_steps = [
    # Départ : shader Shadertoy valide et complet.
    ("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }",
     engine_bridge.DIALECT_SHADERTOY),
    # Retire "Image(out vec4 fragColor, in vec2 fragCoord)" caractère par
    # caractère jusqu'à ne garder que "void main" -- tant que ni
    # "mainImage" ni "void main()" n'est syntaxiquement complet, aucun
    # signal fort n'est présent: le mode précédent (Shadertoy) doit être
    # conservé, jamais basculer prématurément ni retomber sur une valeur
    # par défaut arbitraire.
    ("void mainImag(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }",
     engine_bridge.DIALECT_SHADERTOY),
    ("void mainIma(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }",
     engine_bridge.DIALECT_SHADERTOY),
    ("void main(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }",
     engine_bridge.DIALECT_SHADERTOY),  # "void main(...)" avec des paramètres n'est ni l'un ni l'autre signal fort
    # L'utilisateur retape maintenant une vraie signature void main() sans
    # paramètre par-dessus -- c'est seulement ici, une fois "void main()"
    # syntaxiquement complet, que la bascule doit se produire.
    ("void main() { gl_FragColor = vec4(1.0); }",
     engine_bridge.DIALECT_GLSL),
]

previous = ""
for step_idx, (source, expected_dialect) in enumerate(typing_steps):
    dialect_id, _ = engine_bridge.detect_dialect(source, previous)
    assert dialect_id == expected_dialect, (
        f"étape {step_idx} ({source!r}): attendu '{expected_dialect}' "
        f"(mode précédent '{previous}'), obtenu '{dialect_id}'"
    )
    previous = dialect_id

print("transition en cours de frappe mainImage -> void main() : bascule "
      "au bon moment, jamais avant: ok")


# ---- 4. Shader WGSL "manuel" : détecté ET compile réellement -----------
# (RMLG.md, section 1.5 -- symétrique du scénario 2 mais pour le dialecte
# WGSL ajouté par RMLG.md section 1.)

manual_wgsl_source = (
    "@fragment\n"
    "fn main() -> @location(0) vec4<f32> {\n"
    "    return vec4<f32>(0.0, 0.0, 1.0, 1.0);\n"
    "}\n"
)

dialect_id4, signal_key4 = engine_bridge.detect_dialect(manual_wgsl_source, "")
assert dialect_id4 == engine_bridge.DIALECT_WGSL, (
    f"un '@fragment fn main() -> @location(0) vec4<f32> {{ ... }}' devrait "
    f"être détecté comme '{engine_bridge.DIALECT_WGSL}', obtenu '{dialect_id4}'"
)
assert signal_key4 == "footer.dialect_signal_wgslentrypoint"

engine.compile_pass(engine_bridge.PASS_IMAGE, manual_wgsl_source)
# Même remarque que pour le scénario 2 : la toute première frame après un
# (re)compile peut rendre des pixels de "chauffe" dans cet environnement,
# un second appel est nécessaire.
engine.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 8.0, 17.0, 0.0))
pixels4 = bytes(engine.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 8.0, 17.0, 0.0)))
assert len(pixels4) == 64 * 64 * 4
r4, g4, b4, a4 = pixels4[0], pixels4[1], pixels4[2], pixels4[3]
assert (r4, g4, b4, a4) == (0, 0, 255, 255), (
    f"le pipeline WGSL passthrough a compilé sans erreur mais rend le "
    f"mauvais pixel: attendu bleu opaque (0,0,255,255), obtenu {(r4, g4, b4, a4)}"
)
print("collage d'un shader WGSL manuel -> mode WGSL, et le shader compile "
      "et rend réellement le bon pixel: ok")


print("\nALL OK")

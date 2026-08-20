"""Vérification manuelle du layout mémoire `Globals` côté WGSL (RMLG.md,
section 1.5, dernier point : "Vérification manuelle du layout mémoire
`Globals` (point dur 1.3) sur un shader qui lit chaque champ un par un et
écrit sa valeur dans la couleur de sortie -- avant tout golfing ou
optimisation, un test qui isole spécifiquement le risque d'alignement
std140 vs WGSL par défaut.").

Contexte (voir RMLG.md, section 1.3) : le buffer `globals_buffer` est
rempli côté Rust selon la règle **std140** (`GlobalsUniform`, `renderer.rs`)
et lu côté shader WGSL selon les règles **par défaut de WGSL**, qui ne sont
pas les mêmes pour un tableau de scalaires -- std140 impose 16 octets par
élément d'un `float[4]`, alors qu'un `array<f32, 4>` WGSL n'a par défaut
qu'un stride de 4 octets. Le repli retenu (RMLG.md, 1.3) est de déclarer
`iChannelTime` comme `array<vec4<f32>, 4>` côté WGSL (seul le premier
composant de chaque élément est utilisé) pour que le stride WGSL (16
octets, un `vec4<f32>` étant toujours aligné sur 16) coïncide avec le
stride std140 déjà utilisé côté Rust (`channel_time: [[f32; 4]; 4]`).

C'est ce point précis -- le seul champ de `Globals` où std140 et WGSL
divergent (tous les autres champs sont des scalaires ou des `vec4`, dont
l'alignement est identique dans les deux conventions, voir RMLG.md 1.3) --
que ce fichier isole et vérifie avec un vrai rendu GPU, pas seulement une
relecture du code : si le repli `array<vec4<f32>, 4>` avait été omis (ou
mal appliqué), ce test est le seul de ce dépôt qui l'aurait détecté --
`shader::wgsl_tests::globals_block_pads_ichanneltime_to_vec4_stride` ne
vérifie que le texte WGSL généré, jamais les octets réellement lus par le
GPU.

Le shader lit un par un tous les champs de `Globals` (pas seulement
`iChannelTime`) et écrit un verdict pass/fail dans la couleur de sortie
plutôt que les valeurs elles-mêmes (comme le fait déjà le scénario 2/4 de
`test_dialect_detection.py`) : plus simple à vérifier pixel par pixel
qu'un encodage RGBA8 à 1/255 près, et ça permet de tout vérifier dans un
seul rendu.

Nécessite le module natif compilé et un adaptateur graphique wgpu
utilisable, même convention de SKIP propre que `test_dialect_detection.py`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python_ui"))

try:
    import engine_bridge
except ImportError as exc:
    print(f"SKIPPED: native module not built ({exc}); "
          f"run 'cd rust_engine && maturin develop --release' first.")
    sys.exit(0)

try:
    engine = engine_bridge.Engine(64, 64)
except RuntimeError as exc:
    print(f"SKIPPED (rendu réel): pas d'adaptateur wgpu disponible dans cet "
          f"environnement ({exc}).")
    sys.exit(0)

# ---- Valeurs attendues, injectées via les arguments de render()/les -----
# ---- setters de channel, un par champ de GlobalsUniform. ----------------

TIME = 12.5
TIME_DELTA = 0.25
FRAME = 7
MOUSE = (10.0, 20.0, 1.0, 0.0)
DATE = (2026.0, 8.0, 18.0, 0.0)
WIDTH = HEIGHT = 64
# Constante fixe côté Rust (`renderer.rs::DEFAULT_SAMPLE_RATE`), jamais
# paramétrable depuis Python -- vérifiée telle quelle.
EXPECTED_SAMPLE_RATE = 44100.0

# iChannel0 : lié à une source vidéo avec une position de lecture connue
# -> `iChannelTime[0]`. C'est le champ qui isole spécifiquement le risque
# std140/WGSL décrit ci-dessus (tous les autres champs lus par ce shader
# sont des scalaires/vec4 dont l'alignement ne diverge pas entre les deux
# conventions, voir RMLG.md 1.3).
CHANNEL0_TIME = 12.5
# iChannel1 : volontairement laissé non lié, pour vérifier que
# `iChannelTime[1]` reste à 0 (comportement Shadertoy : seul un slot
# Video/Webcam/Audio rapporte une position de lecture réelle, voir
# `renderer.rs::write_globals`) -- un contrôle négatif pour ne pas se
# contenter de vérifier qu'"une" valeur non nulle a été lue au bon endroit.

engine.set_ichannel_video(engine_bridge.PASS_IMAGE, 0)
# 1x1 pixel opaque noir : le contenu de la texture n'est pas ce qui est
# vérifié ici (seul `iChannelTime` l'est) -- `set_ichannel_video` seul
# laisse déjà un pixel 1x1 placeholder en place (`ChannelTexture::dynamic`),
# mais on force explicitement `time` via `update_ichannel_video_frame`
# plutôt que de dépendre de la valeur initiale du placeholder.
engine.update_ichannel_video_frame(
    engine_bridge.PASS_IMAGE, 0, 1, 1, bytes([0, 0, 0, 255]), CHANNEL0_TIME
)

wgsl_globals_probe_source = """
@fragment
fn main() -> @location(0) vec4<f32> {
    var ok: bool = true;

    ok = ok && abs(globals.iTime - 12.5) < 0.01;
    ok = ok && abs(globals.iTimeDelta - 0.25) < 0.01;
    ok = ok && (globals.iFrame == 7);

    ok = ok && abs(globals.iMouse.x - 10.0) < 0.01;
    ok = ok && abs(globals.iMouse.y - 20.0) < 0.01;
    ok = ok && abs(globals.iMouse.z - 1.0) < 0.01;
    ok = ok && abs(globals.iMouse.w - 0.0) < 0.01;

    ok = ok && abs(globals.iResolution.x - 64.0) < 0.01;
    ok = ok && abs(globals.iResolution.y - 64.0) < 0.01;

    ok = ok && abs(globals.iDate.x - 2026.0) < 0.01;
    ok = ok && abs(globals.iDate.y - 8.0) < 0.01;
    ok = ok && abs(globals.iDate.z - 18.0) < 0.01;

    ok = ok && abs(globals.iSampleRate - 44100.0) < 1.0;

    // Le point précis que ce fichier isole : sans le repli
    // `array<vec4<f32>, 4>` (RMLG.md 1.3), ce champ lirait les octets du
    // *second* élément std140 (iChannelTime[1], resté à 0) à la place du
    // premier, à cause du décalage de stride -- ce test échouerait alors
    // silencieusement en lisant 0.0 au lieu de 12.5 ici.
    ok = ok && abs(globals.iChannelTime[0].x - 12.5) < 0.01;
    // Contrôle négatif symétrique : le slot non lié doit bien rester à 0,
    // pas hériter par erreur d'une valeur d'un champ voisin (même risque
    // de décalage, dans l'autre sens).
    ok = ok && abs(globals.iChannelTime[1].x - 0.0) < 0.01;

    if (ok) {
        return vec4<f32>(1.0, 0.0, 0.0, 1.0);
    }
    return vec4<f32>(0.0, 1.0, 0.0, 1.0);
}
"""

dialect_id, signal_key = engine_bridge.detect_dialect(wgsl_globals_probe_source, "")
assert dialect_id == engine_bridge.DIALECT_WGSL, (
    f"le shader de sonde du layout Globals devrait être détecté comme "
    f"'{engine_bridge.DIALECT_WGSL}', obtenu '{dialect_id}'"
)

engine.compile_pass(engine_bridge.PASS_IMAGE, wgsl_globals_probe_source)
# Même frame de "chauffe" que les autres scénarios de rendu réel de ce
# dépôt (voir `test_dialect_detection.py`, `pixel_compare.py`).
engine.render(TIME, TIME_DELTA, MOUSE, FRAME, DATE)
pixels = bytes(engine.render(TIME, TIME_DELTA, MOUSE, FRAME, DATE))
assert len(pixels) == WIDTH * HEIGHT * 4
r, g, b, a = pixels[0], pixels[1], pixels[2], pixels[3]
assert (r, g, b, a) == (255, 0, 0, 255), (
    "le layout mémoire de Globals ne correspond pas entre le buffer std140 "
    "écrit côté Rust (renderer.rs::write_globals) et sa lecture côté WGSL "
    "(shader.rs::WGSL_GLOBALS_BLOCK) -- au moins un champ diverge (vert "
    f"(0,255,0,255) = échec, obtenu {(r, g, b, a)}). Voir en particulier le "
    "repli std140 vs WGSL sur iChannelTime documenté dans RMLG.md, section 1.3."
)
print("layout mémoire Globals (std140 Rust <-> WGSL) vérifié champ par "
      "champ sur un rendu réel, y compris le repli iChannelTime "
      "array<vec4<f32>,4> qui isole spécifiquement la divergence "
      "std140/WGSL: ok")

print("\nALL OK")

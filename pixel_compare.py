"""Rejoue `default.frag` original vs golfé (options par défaut de l'UI :
rename=dead_code=algebra=True) à travers le vrai moteur wgpu et compare les
pixels rendus, pour de vrai cette fois (Priorité 0 de RM.md)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "python_ui"))

import engine_bridge

W, H = 800, 450
FRAG_PATH = "python_ui/assets/shaders/default.frag"

source = open(FRAG_PATH, encoding="utf-8").read()
golfed = engine_bridge.golf_shader_ex(source, "", True, True, True)

print(f"Original : {len(source)} caractères, {source.count(chr(10))+1} lignes")
print(f"Golfé    : {len(golfed)} caractères, {golfed.count(chr(10))+1} lignes")

def render(src, label):
    engine = engine_bridge.Engine(W, H)
    engine.compile_pass(engine_bridge.PASS_IMAGE, src)
    # NB : le tout premier appel à render() sur un Engine fraîchement créé
    # renvoie des pixels entièrement à zéro dans cet environnement (frame de
    # "chauffe" — la lecture du buffer semble devancer la fin de soumission
    # GPU). Un second appel est nécessaire pour obtenir une vraie frame.
    # Vrai bug trouvé en conditions réelles, documenté dans RM.md.
    engine.render(0.0, 0.0, (0.0, 0.0, 0.0, 0.0), 0, (2026.0, 8.0, 17.0, 0.0))
    pixels = engine.render(
        12.345,           # time (valeur non triviale pour exercer l'animation)
        1.0 / 60.0,        # time_delta
        (0.0, 0.0, 0.0, 0.0),  # mouse
        42,                 # frame
        (2026.0, 8.0, 17.0, 12345.0),  # date
    )
    data = bytes(pixels)
    assert len(data) == W * H * 4, (label, len(data))
    non_zero = sum(1 for b in data if b)
    print(f"  [{label}] octets non-nuls : {non_zero}/{len(data)}")
    return data

orig_pixels = render(source, "original")
golf_pixels = render(golfed, "golfé")

identical = orig_pixels == golf_pixels
print(f"\nPixel-identique : {identical}")

if not identical:
    diffs = sum(1 for a, b in zip(orig_pixels, golf_pixels) if a != b)
    max_delta = max(abs(a - b) for a, b in zip(orig_pixels, golf_pixels))
    n_px_diff = sum(
        1 for i in range(0, len(orig_pixels), 4)
        if orig_pixels[i:i+4] != golf_pixels[i:i+4]
    )
    print(f"  Octets différents : {diffs} / {len(orig_pixels)}")
    print(f"  Pixels différents : {n_px_diff} / {W*H}")
    print(f"  Delta max (par canal) : {max_delta}")

# Sauvegarde les deux images pour inspection visuelle (RGBA8 -> PNG via Qt,
# déjà disponible dans l'environnement).
from PySide6.QtGui import QImage
QImage(orig_pixels, W, H, QImage.Format_RGBA8888).save("/tmp/render_original.png")
QImage(golf_pixels, W, H, QImage.Format_RGBA8888).save("/tmp/render_golfe.png")
print("\nImages sauvegardées : /tmp/render_original.png, /tmp/render_golfe.png")

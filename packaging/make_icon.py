"""Génère packaging/icon.ico (utilisé par le build PyInstaller et
l'installeur Inno Setup) à partir d'un dessin simple fait en Pillow —
aucun asset externe requis, donc reproductible sur n'importe quel poste.

Usage : .venv\\Scripts\\python.exe packaging\\make_icon.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024
OUT = Path(__file__).resolve().parent / "icon.ico"


def make_base_image() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Fond : carré arrondi bleu nuit, même famille que la fenêtre principale
    # de l'éditeur (thème sombre), pour que l'icône se reconnaisse à côté
    # de l'appli une fois lancée.
    bg = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    margin = SIZE * 0.04
    radius = SIZE * 0.22
    bg_draw.rounded_rectangle(
        [margin, margin, SIZE - margin, SIZE - margin],
        radius=radius,
        fill=(18, 22, 34, 255),
    )

    # Sphère "shader" : dégradé plasma façon aperçu Shadertoy, plus
    # reconnaissable à petite taille qu'un texte ou une icône fine.
    orb = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    px = orb.load()
    cx, cy = SIZE / 2, SIZE / 2
    orb_r = SIZE * 0.30
    for y in range(SIZE):
        dy = y - cy
        if abs(dy) > orb_r:
            continue
        for x in range(SIZE):
            dx = x - cx
            dist = math.hypot(dx, dy)
            if dist > orb_r:
                continue
            angle = math.atan2(dy, dx)
            t = (angle / math.pi + 1.0) / 2.0
            # Dégradé cyan -> violet -> orange, rappel des couleurs
            # d'aperçu shader habituelles.
            r = int(40 + 200 * (0.5 + 0.5 * math.sin(2 * math.pi * (t + 0.0))))
            g = int(40 + 180 * (0.5 + 0.5 * math.sin(2 * math.pi * (t + 0.33))))
            b = int(60 + 200 * (0.5 + 0.5 * math.sin(2 * math.pi * (t + 0.66))))
            shade = 0.55 + 0.45 * (1.0 - dist / orb_r)
            alpha = 255 if dist < orb_r - 2 else int(255 * max(0.0, orb_r - dist))
            px[x, y] = (int(r * shade), int(g * shade), int(b * shade), alpha)

    # Léger halo pour détacher la sphère du fond.
    glow = orb.filter(ImageFilter.GaussianBlur(SIZE * 0.02))
    bg.alpha_composite(glow)
    bg.alpha_composite(orb)

    # Repère "code" : simple chevrons </> en surimpression, assez épais
    # pour rester lisibles à 32px, signalant "éditeur de code" plutôt que
    # juste "image".
    mark = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mdraw = ImageDraw.Draw(mark)
    stroke = int(SIZE * 0.028)
    mid_y = SIZE * 0.78
    span = SIZE * 0.05
    left_x = SIZE * 0.30
    right_x = SIZE * 0.70
    mdraw.line(
        [(left_x + span, mid_y - span), (left_x, mid_y), (left_x + span, mid_y + span)],
        fill=(240, 244, 255, 255), width=stroke, joint="curve",
    )
    mdraw.line(
        [(right_x - span, mid_y - span), (right_x, mid_y), (right_x - span, mid_y + span)],
        fill=(240, 244, 255, 255), width=stroke, joint="curve",
    )
    bg.alpha_composite(mark)

    return bg


def main() -> None:
    base = make_base_image()
    sizes = [256, 128, 64, 48, 32, 24, 16]
    base.resize((sizes[0], sizes[0]), Image.LANCZOS).save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"écrit {OUT} ({OUT.stat().st_size} octets)")


if __name__ == "__main__":
    main()

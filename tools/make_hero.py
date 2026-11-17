"""Erzeugt site/hero.svg: eine Sperrholzplatte mit dem Schriftzug „TiLaser“ als Schnittkontur
mit Haltestegen – berechnet mit TiLasers eigenen Funktionen (Text, Haltestege, Rechteck).

    python tools/make_hero.py <schriftdatei.ttf|otf>
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tilaser.core import geometry as g          # noqa: E402
from tilaser.core import shapes                 # noqa: E402
from tilaser.core.offset import add_tabs        # noqa: E402
from tilaser.core.text_import import text_to_polylines  # noqa: E402

W, H = 240.0, 130.0                             # Plattengröße (mm)


def d_attr(p: g.Polyline) -> str:
    pts = p.loop_pts() if p.closed else p.pts
    y = H - pts[:, 1]                           # SVG: Y nach unten
    return "M" + " L".join(f"{x:.2f},{yy:.2f}" for x, yy in zip(pts[:, 0], y))


def main(font):
    letters = text_to_polylines("TiLaser", font, 46)
    bb = g.bbox(letters)
    dx = W / 2 - (bb[0] + bb[2]) / 2
    dy = H / 2 - (bb[1] + bb[3]) / 2 + 4
    letters = [p.transformed(g.translate(dx, dy)) for p in letters]
    pieces = add_tabs(letters, 2, 1.6, outer_only=True, min_length=10)
    plate = shapes.rectangle(W - 8, H - 8, 7).transformed(g.translate(W / 2, H / 2))
    holes = [shapes.ellipse(5, 5).transformed(g.translate(x, y))
             for x, y in ((14, 14), (W - 14, 14), (W - 14, H - 14), (14, H - 14))]
    cut = holes + pieces + add_tabs([plate], 4, 2.4, outer_only=False)
    paths = "\n".join(f'    <path d="{d_attr(p)}"/>' for p in cut)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-14 -14 {W + 28:.0f} {H + 28:.0f}" role="img"
     aria-label="Sperrholzplatte, aus der der Schriftzug TiLaser mit Haltestegen ausgeschnitten wird">
  <defs>
    <pattern id="raster" width="10" height="10" patternUnits="userSpaceOnUse">
      <path d="M10 0H0V10" fill="none" stroke="#c4c9d0" stroke-width="0.35"/>
    </pattern>
    <filter id="maserung" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.012 0.32" numOctaves="2" seed="7"/>
      <feColorMatrix values="0 0 0 0 0.45  0 0 0 0 0.30  0 0 0 0 0.16  0 0 0 0.16 0"/>
      <feComposite in2="SourceGraphic" operator="in"/>
      <feBlend in2="SourceGraphic" mode="multiply"/>
    </filter>
    <filter id="glut" x="-200%" y="-200%" width="500%" height="500%">
      <feGaussianBlur stdDeviation="2.2"/>
    </filter>
  </defs>
  <rect x="-14" y="-14" width="{W + 28:.0f}" height="{H + 28:.0f}" fill="url(#raster)"/>
  <rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" rx="2" fill="#dcc196" filter="url(#maserung)"/>
  <g id="schnitt" fill="none" stroke="#4a2c17" stroke-width="0.9" stroke-linecap="round" stroke-linejoin="round">
{paths}
  </g>
  <g id="laserkopf" opacity="0">
    <circle r="5" fill="#3d63ff" filter="url(#glut)"/>
    <circle r="1.3" fill="#e9eeff"/>
  </g>
</svg>
'''
    out = os.path.join(ROOT, "site", "hero.svg")
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"{out}: {len(cut)} Schnittpfade")


if __name__ == "__main__":
    main(sys.argv[1])

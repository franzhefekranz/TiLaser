"""Materialtest: Raster aus Feldern mit steigender Leistung (Spalten) und Geschwindigkeit (Zeilen)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .model import Item, Project, VectorPath, new_id
from .text_import import default_font, text_to_polylines


@dataclass
class MaterialTest:
    mode: str = "fill"                 # 'fill' (Gravur) | 'cut'
    power_min: float = 10.0
    power_max: float = 100.0
    cols: int = 5
    speed_min: float = 1000.0
    speed_max: float = 6000.0
    rows: int = 5
    cell: float = 8.0                  # Feldgröße mm
    gap: float = 3.0                   # Abstand mm
    interval: float = 0.1              # Zeilenabstand bei Gravur
    passes: int = 1
    x: float = 10.0                    # linke untere Ecke des Rasters
    y: float = 10.0
    labels: bool = True
    label_power: float = 20.0
    label_speed: float = 3000.0
    label_size: float = 3.0

    def powers(self):
        return list(np.linspace(self.power_min, self.power_max, max(1, self.cols)))

    def speeds(self):
        # Zeile 0 (oben) = schnellste
        return list(np.linspace(self.speed_max, self.speed_min, max(1, self.rows)))


def _rect(x, y, w, h):
    return g.Polyline(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]), True)


def build_project(t: MaterialTest) -> Project:
    p = Project()
    pitch = t.cell + t.gap
    label_w = 14.0 if t.labels else 0.0
    grid_h = t.rows * pitch - t.gap
    top = t.y + grid_h
    font = default_font() if t.labels else None
    for r, speed in enumerate(t.speeds()):
        for c, power in enumerate(t.powers()):
            layer = p.add_layer(f"P{power:.0f}% F{speed:.0f}", mode=t.mode)
            layer.power, layer.speed, layer.passes, layer.interval = float(power), float(speed), t.passes, t.interval
            x0 = t.x + label_w + c * pitch
            y0 = top - (r + 1) * pitch + t.gap
            it = Item(id=new_id("I"), name=layer.name, kind="vector", x=x0 + t.cell / 2, y=y0 + t.cell / 2)
            it.paths = [VectorPath(_rect(-t.cell / 2, -t.cell / 2, t.cell, t.cell), layer.id)]
            p.items.append(it)
    if font:
        lab = p.add_layer("Beschriftung", mode="cut")
        lab.power, lab.speed = t.label_power, t.label_speed
        p.layers.remove(lab)
        p.layers.insert(0, lab)                  # Beschriftung zuerst

        def add_text(txt, cx, cy, rot=0.0):
            polys = text_to_polylines(txt, font, t.label_size)
            if not polys:
                return
            bb = g.bbox(polys)
            cxx, cyy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            it = Item(id=new_id("I"), name=txt, kind="vector", x=cx, y=cy, rotation=rot)
            it.paths = [VectorPath(q.transformed(g.translate(-cxx, -cyy)), lab.id) for q in polys]
            p.items.append(it)

        for c, power in enumerate(t.powers()):
            add_text(f"{power:.0f}", t.x + label_w + c * pitch + t.cell / 2, top + t.label_size)
        for r, speed in enumerate(t.speeds()):
            add_text(f"{speed:.0f}", t.x + label_w / 2, top - (r + 1) * pitch + t.gap + t.cell / 2)
        add_text("Leistung %", t.x + label_w + (t.cols * pitch - t.gap) / 2, top + 2.6 * t.label_size)
        add_text("mm/min", t.x + label_w / 2, top + t.label_size)
    return p

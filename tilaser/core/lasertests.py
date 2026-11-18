"""Einstell-Tests: Fokustest (mit Z-Achse oder als Rampe ohne Z-Achse) und Zeilenabstandstest."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .model import Item, Layer, Project, VectorPath, new_id
from .text_import import default_font, text_to_polylines


def _line(x0, y0, x1, y1):
    return g.Polyline(np.array([[x0, y0], [x1, y1]], float), False)


def _rect(x, y, w, h):
    return g.Polyline(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], float), True)


def _add(p: Project, name: str, polys, layer: Layer):
    bb = g.bbox(polys)
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    it = Item(id=new_id("I"), name=name, kind="vector", x=cx, y=cy)
    it.paths = [VectorPath(q.transformed(g.translate(-cx, -cy)), layer.id) for q in polys]
    p.items.append(it)
    return it


def _label_layer(p: Project, power: float, speed: float) -> Layer:
    lab = p.add_layer("Beschriftung", mode="cut")
    lab.power, lab.speed = power, speed
    p.layers.remove(lab)
    p.layers.insert(0, lab)
    return lab


def _text(p: Project, font, txt, x, y, size, layer, align="left"):
    polys = text_to_polylines(txt, font, size) if font else []
    if not polys:
        return
    bb = g.bbox(polys)
    dx = {"left": x - bb[0], "center": x - (bb[0] + bb[2]) / 2, "right": x - bb[2]}[align]
    dy = y - (bb[1] + bb[3]) / 2
    _add(p, txt, [q.transformed(g.translate(dx, dy)) for q in polys], layer)


# --------------------------------------------------------------------------- Fokustest mit Z-Achse
@dataclass
class FocusTest:
    z_min: float = -2.0          # mm relativ zur aktuellen Höhe (negativ = näher ans Material)
    z_max: float = 2.0
    steps: int = 9
    line_len: float = 25.0
    gap: float = 4.0             # Abstand der Linien
    power: float = 60.0
    speed: float = 1000.0        # mm/min
    x: float = 10.0
    y: float = 10.0
    labels: bool = True
    label_power: float = 20.0
    label_speed: float = 3000.0
    label_size: float = 2.5

    def z_values(self):
        return list(np.linspace(self.z_min, self.z_max, max(1, self.steps)))


def build_focus_test(t: FocusTest) -> Project:
    """Eine Linie je Z-Höhe; jede Linie auf einer eigenen Ebene mit Z-Versatz."""
    p = Project()
    font = default_font() if t.labels else None
    label_w = 12.0 if font else 0.0
    lab = _label_layer(p, t.label_power, t.label_speed) if font else None
    for k, z in enumerate(t.z_values()):
        y = t.y + k * t.gap
        layer = p.add_layer(f"Z {z:+.2f}", mode="cut")
        layer.power, layer.speed, layer.z_offset = t.power, t.speed, float(z)
        _add(p, layer.name, [_line(t.x + label_w, y, t.x + label_w + t.line_len, y)], layer)
        if lab:
            _text(p, font, f"{z:+.1f}", t.x, y, t.label_size, lab)
    return p


# --------------------------------------------------------------------------- Rampentest ohne Z-Achse
@dataclass
class RampTest:
    length: float = 100.0        # Länge der Linie auf dem schräg gelegten Brett
    tick: float = 10.0           # Teilstriche alle … mm
    power: float = 60.0
    speed: float = 1000.0
    x: float = 10.0
    y: float = 20.0
    labels: bool = True
    label_power: float = 20.0
    label_speed: float = 3000.0
    label_size: float = 3.0


def build_ramp_test(t: RampTest) -> Project:
    """Eine lange Linie mit Teilstrichen und Zahlen. Das Brett wird schräg gelegt (ein Ende unterlegt);
    an der dünnsten Stelle der Linie stimmt der Fokus."""
    p = Project()
    line = p.add_layer("Fokuslinie", mode="cut")
    line.power, line.speed = t.power, t.speed
    _add(p, "Fokuslinie", [_line(t.x, t.y, t.x + t.length, t.y)], line)
    font = default_font() if t.labels else None
    marks = _label_layer(p, t.label_power, t.label_speed)
    ticks = []
    n = int(t.length // t.tick)
    for k in range(n + 1):
        xx = t.x + k * t.tick
        long = k % 5 == 0
        ticks.append(_line(xx, t.y - 2.0, xx, t.y - (5.0 if long else 3.5)))
        if font:
            _text(p, font, f"{k * t.tick:g}", xx, t.y - 5.0 - 1.2 * t.label_size, t.label_size, marks, "center")
    _add(p, "Teilstriche", ticks, marks)
    return p


# --------------------------------------------------------------------------- Zeilenabstandstest
@dataclass
class IntervalTest:
    interval_min: float = 0.05
    interval_max: float = 0.30
    count: int = 6
    cell: float = 10.0
    gap: float = 3.0
    power: float = 40.0
    speed: float = 3000.0
    angle: float = 0.0
    x: float = 10.0
    y: float = 10.0
    labels: bool = True
    label_power: float = 20.0
    label_speed: float = 3000.0
    label_size: float = 2.5

    def intervals(self):
        return list(np.linspace(self.interval_min, self.interval_max, max(1, self.count)))


def build_interval_test(t: IntervalTest) -> Project:
    """Gefüllte Quadrate nebeneinander, jedes mit einem anderen Zeilenabstand, darunter der Wert."""
    p = Project()
    font = default_font() if t.labels else None
    lab = _label_layer(p, t.label_power, t.label_speed) if font else None
    pitch = t.cell + t.gap
    label_h = 2.2 * t.label_size if font else 0.0
    for k, iv in enumerate(t.intervals()):
        layer = p.add_layer(f"Abstand {iv:.3f} mm", mode="fill")
        layer.power, layer.speed, layer.interval, layer.angle = t.power, t.speed, float(iv), t.angle
        x0 = t.x + k * pitch
        _add(p, layer.name, [_rect(x0, t.y + label_h, t.cell, t.cell)], layer)
        if lab:
            _text(p, font, f"{iv:.2f}", x0 + t.cell / 2, t.y + t.label_size / 2, t.label_size, lab, "center")
    return p

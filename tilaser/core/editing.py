"""Bearbeitungsfunktionen: Rückgängig/Wiederholen, Kopieren, Ausrichten, Verteilen, Arrays."""
from __future__ import annotations

import copy
import math
import time

from .model import Item, Layer, Project, VectorPath, new_id


# --------------------------------------------------------------------------- Kopien
def clone_item(it: Item, new_ids: bool = True) -> Item:
    """Flache, aber sichere Kopie: Geometrie (Polylinien) und Bilddaten werden geteilt,
    da sie nie an Ort und Stelle verändert werden."""
    c = copy.copy(it)
    c.paths = [VectorPath(vp.poly, vp.layer_id) for vp in it.paths]
    if new_ids:
        c.id = new_id("I")
    return c


def snapshot(project: Project) -> Project:
    p = Project()
    p.layers = [Layer.from_dict(l.to_dict()) for l in project.layers]
    p.items = [clone_item(it, new_ids=False) for it in project.items]
    p._key_to_layer = dict(project._key_to_layer)
    return p


class UndoStack:
    """Speichert Projekt-Schnappschüsse. Aufeinanderfolgende gleichartige Änderungen
    (z. B. Tippen in einem Zahlenfeld) werden über 'key' zusammengefasst."""

    def __init__(self, limit: int = 60, coalesce_s: float = 1.5):
        self.undo: list[tuple[str, Project]] = []
        self.redo: list[tuple[str, Project]] = []
        self.limit = limit
        self.coalesce_s = coalesce_s
        self._last_key = None
        self._last_t = 0.0

    def push(self, project: Project, label: str, key: str | None = None):
        now = time.time()
        if key is not None and key == self._last_key and now - self._last_t < self.coalesce_s:
            self._last_t = now
            return
        self.undo.append((label, snapshot(project)))
        if len(self.undo) > self.limit:
            self.undo.pop(0)
        self.redo.clear()
        self._last_key, self._last_t = key, now

    def can_undo(self):
        return bool(self.undo)

    def can_redo(self):
        return bool(self.redo)

    def do_undo(self, current: Project) -> Project | None:
        if not self.undo:
            return None
        label, snap = self.undo.pop()
        self.redo.append((label, snapshot(current)))
        self._last_key = None
        return snap

    def do_redo(self, current: Project) -> Project | None:
        if not self.redo:
            return None
        label, snap = self.redo.pop()
        self.undo.append((label, snapshot(current)))
        self._last_key = None
        return snap

    def undo_label(self):
        return self.undo[-1][0] if self.undo else ""

    def redo_label(self):
        return self.redo[-1][0] if self.redo else ""


# --------------------------------------------------------------------------- Ausrichten
def selection_bbox(items: list[Item]):
    bbs = [it.world_bbox() for it in items]
    return (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))


def align(items: list[Item], mode: str, ref=None):
    """mode: left, hcenter, right, bottom, vcenter, top. ref = Bezugs-Box (Standard: Auswahl)."""
    if not items:
        return
    rb = ref or selection_bbox(items)
    for it in items:
        b = it.world_bbox()
        if mode == "left":
            it.x += rb[0] - b[0]
        elif mode == "right":
            it.x += rb[2] - b[2]
        elif mode == "hcenter":
            it.x += (rb[0] + rb[2]) / 2 - (b[0] + b[2]) / 2
        elif mode == "bottom":
            it.y += rb[1] - b[1]
        elif mode == "top":
            it.y += rb[3] - b[3]
        elif mode == "vcenter":
            it.y += (rb[1] + rb[3]) / 2 - (b[1] + b[3]) / 2


def distribute(items: list[Item], axis: str, mode: str = "gap"):
    """Verteilt Objekte gleichmäßig zwischen dem ersten und letzten.

    mode 'gap' = gleiche Abstände zwischen den Objekten, 'center' = gleiche Mittelpunktabstände.
    """
    if len(items) < 3:
        return
    k0, k1 = (0, 2) if axis == "x" else (1, 3)
    its = sorted(items, key=lambda it: it.world_bbox()[k0])
    bbs = [it.world_bbox() for it in its]
    if mode == "center":
        c0 = (bbs[0][k0] + bbs[0][k1]) / 2
        c1 = (bbs[-1][k0] + bbs[-1][k1]) / 2
        step = (c1 - c0) / (len(its) - 1)
        for i, (it, b) in enumerate(zip(its, bbs)):
            d = c0 + i * step - (b[k0] + b[k1]) / 2
            if axis == "x":
                it.x += d
            else:
                it.y += d
        return
    total = sum(b[k1] - b[k0] for b in bbs)
    gap = (bbs[-1][k1] - bbs[0][k0] - total) / (len(its) - 1)
    pos = bbs[0][k0]
    for it, b in zip(its, bbs):
        d = pos - b[k0]
        if axis == "x":
            it.x += d
        else:
            it.y += d
        pos += (b[k1] - b[k0]) + gap


# --------------------------------------------------------------------------- Arrays
def grid_array(project: Project, items: list[Item], cols: int, rows: int, dx: float, dy: float,
               spacing_is_gap: bool = True) -> list[Item]:
    """Raster-Array. dx/dy = Abstand zwischen Kopien (gap) oder Rastermaß (pitch)."""
    if not items:
        return []
    b = selection_bbox(items)
    px = dx + (b[2] - b[0]) if spacing_is_gap else dx
    py = dy + (b[3] - b[1]) if spacing_is_gap else dy
    new = []
    for r in range(max(1, rows)):
        for c in range(max(1, cols)):
            if r == 0 and c == 0:
                continue
            for it in items:
                cp = clone_item(it)
                cp.x += c * px
                cp.y -= r * py          # Zeilen nach unten
                project.items.append(cp)
                new.append(cp)
    return new


def circular_array(project: Project, items: list[Item], count: int, center: tuple[float, float],
                   total_angle: float = 360.0, rotate_copies: bool = True) -> list[Item]:
    """Kopien gleichmäßig auf einem Kreisbogen um 'center' (Grad, gegen den Uhrzeigersinn)."""
    if not items or count < 2:
        return []
    full = abs(abs(total_angle) - 360) < 1e-9
    step = total_angle / (count if full else count - 1)
    new = []
    for k in range(1, count):
        a = math.radians(step * k)
        ca, sa = math.cos(a), math.sin(a)
        for it in items:
            cp = clone_item(it)
            rx, ry = it.x - center[0], it.y - center[1]
            cp.x = center[0] + ca * rx - sa * ry
            cp.y = center[1] + sa * rx + ca * ry
            if rotate_copies:
                cp.rotation = it.rotation + step * k
            project.items.append(cp)
            new.append(cp)
    return new


def transform_items(items: list[Item], originals: list[tuple], scale: float = 1.0, rotation: float = 0.0,
                    anchor: tuple[float, float] = (0.0, 0.0)):
    """Skaliert/dreht eine Auswahl um einen Ankerpunkt. originals = [(x, y, scale, rotation), ...]
    aus dem Zustand vor Beginn der Bearbeitung (für stabile, nicht kumulierende Updates)."""
    a = math.radians(rotation)
    ca, sa = math.cos(a), math.sin(a)
    for it, (x, y, s, r) in zip(items, originals):
        dx, dy = (x - anchor[0]) * scale, (y - anchor[1]) * scale
        it.x = anchor[0] + ca * dx - sa * dy
        it.y = anchor[1] + sa * dx + ca * dy
        it.scale = max(1e-4, s * scale)
        it.rotation = (r + rotation) if not it.mirror_x else (r + rotation)


def extract_items(project: Project, ids) -> Project:
    """Neues Projekt nur mit den Objekten 'ids' (gleiche Lage) und den Ebenen, die sie benutzen."""
    ids = set(ids)
    p = Project()
    p.items = [clone_item(it, new_ids=False) for it in project.items if it.id in ids]
    used = set().union(*[it.layer_ids() for it in p.items]) if p.items else set()
    p.layers = [Layer.from_dict(l.to_dict()) for l in project.layers if l.id in used]
    st = getattr(project, "vars", None)
    if st is not None:
        import copy as _copy
        p.vars = _copy.copy(st)
    return p

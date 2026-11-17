"""Boolesche Operationen und Offset für geschlossene Konturen (benötigt pyclipper)."""
from __future__ import annotations

import numpy as np

from . import geometry as g
from .offset import SCALE, offset_closed, pyclipper


class BooleanUnavailable(RuntimeError):
    pass


def _to_clip(polys):
    return [[(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in p.pts] for p in polys
            if p.closed and len(p.pts) >= 3]


def _from_clip(paths):
    return [g.Polyline(np.array(p, float) / SCALE, True) for p in paths if len(p) >= 3]


def boolean(a: list[g.Polyline], b: list[g.Polyline], op: str) -> list[g.Polyline]:
    """op: 'union' | 'difference' (a - b) | 'intersection' | 'xor'. Even-Odd je Operand."""
    if pyclipper is None:
        raise BooleanUnavailable("Für Boolesche Operationen wird pyclipper benötigt (pip install pyclipper).")
    ct = {"union": pyclipper.CT_UNION, "difference": pyclipper.CT_DIFFERENCE,
          "intersection": pyclipper.CT_INTERSECTION, "xor": pyclipper.CT_XOR}[op]
    pc = pyclipper.Pyclipper()
    sa, sb = _to_clip(a), _to_clip(b)
    if sa:
        pc.AddPaths(sa, pyclipper.PT_SUBJECT, True)
    if sb:
        pc.AddPaths(sb, pyclipper.PT_CLIP, True)
    if not sa:
        return []
    res = pc.Execute(ct, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)
    return _from_clip(res)


def boolean_many(groups: list[list[g.Polyline]], op: str) -> list[g.Polyline]:
    """Wendet op nacheinander an: ((g0 op g1) op g2) ..."""
    if not groups:
        return []
    res = [p for p in groups[0] if p.closed]
    for grp in groups[1:]:
        res = boolean(res, grp, op)
    return res


def offset_shapes(polys: list[g.Polyline], d: float, round_joins: bool = False) -> list[g.Polyline]:
    """Versetzt Formen um d (mm): Außenkonturen wachsen, Löcher schrumpfen (bei d > 0).
    Mit pyclipper werden überlappende Ergebnisse zusammengeführt."""
    closed = [p for p in polys if p.closed and len(p.pts) >= 3]
    if pyclipper is not None and closed:
        pco = pyclipper.PyclipperOffset(2.0, 0.02 * SCALE)
        jt = pyclipper.JT_ROUND if round_joins else pyclipper.JT_MITER
        # Even-Odd-Fläche erst bereinigen, damit Löcher korrekt orientiert sind
        pc = pyclipper.Pyclipper()
        pc.AddPaths(_to_clip(closed), pyclipper.PT_SUBJECT, True)
        clean = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD)
        pco.AddPaths(clean, jt, pyclipper.ET_CLOSEDPOLYGON)
        return _from_clip(pco.Execute(d * SCALE))
    depth = g.nesting_depth(closed)
    out = []
    for p, dep in zip(closed, depth):
        for r in offset_closed(p.pts, d if dep % 2 == 0 else -d):
            out.append(g.Polyline(r, True))
    return out

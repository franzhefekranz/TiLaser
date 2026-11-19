"""DXF-Import (ASCII-DXF) ohne externe Abhängigkeiten.

Unterstützt: LINE, LWPOLYLINE (mit Bulges), POLYLINE/VERTEX, CIRCLE, ARC, ELLIPSE, SPLINE
(NURBS inkl. Gewichte), POINT wird ignoriert, INSERT (Blockreferenzen, verschachtelt).
Gruppierung nach DXF-Layer.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g
from .model import ImportedPath, PALETTE

INSUNITS_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 25.4e-6, 9: 0.0254, 10: 914.4,
               13: 0.001, 14: 100.0}

# AutoCAD-Farbindex (Grundfarben)
ACI = {1: "#ff0000", 2: "#ffff00", 3: "#00ff00", 4: "#00ffff", 5: "#0000ff", 6: "#ff00ff", 7: "#000000",
       8: "#808080", 9: "#c0c0c0"}


def _read_pairs(path: str) -> list[tuple[int, str]]:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("Binäre DXF-Dateien werden nicht unterstützt – bitte als ASCII-DXF speichern.")
    text = None
    text = ""
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = text.splitlines()
    pairs = []
    for i in range(0, len(lines) - 1, 2):
        try:
            pairs.append((int(lines[i].strip()), lines[i + 1].strip()))
        except ValueError:
            continue
    return pairs


def _split_entities(pairs, start, end_marker=("ENDSEC",)):
    """Zerlegt eine Paarfolge in Entitäten [(typ, [(code, wert), ...]), ...]."""
    ents = []
    cur = None
    i = start
    while i < len(pairs):
        code, val = pairs[i]
        if code == 0:
            if cur:
                ents.append(cur)
            if val in end_marker:
                return ents, i
            cur = (val, [])
        elif cur is not None:
            cur[1].append((code, val))
        i += 1
    if cur:
        ents.append(cur)
    return ents, i


def _get(data, code, default=None, cast=float):
    for c, v in data:
        if c == code:
            try:
                return cast(v)
            except ValueError:
                return default
    return default


def _nurbs(ctrl: np.ndarray, knots: np.ndarray, degree: int, weights: np.ndarray | None, n_samples: int):
    """Auswertung einer NURBS-Kurve mit dem de-Boor-Algorithmus."""
    n = len(ctrl)
    if weights is None or len(weights) != n:
        weights = np.ones(n)
    P = np.column_stack([ctrl * weights[:, None], weights])
    if len(knots) != n + degree + 1:   # ungültig -> gleichmäßig geklemmt
        inner = max(0, n - degree - 1)
        knots = np.concatenate([np.zeros(degree + 1), np.linspace(0, 1, inner + 2)[1:-1], np.ones(degree + 1)])
    t0, t1 = knots[degree], knots[n]
    out = []
    for t in np.linspace(t0, t1, n_samples):
        k = int(np.searchsorted(knots, t, side="right") - 1)
        k = min(max(k, degree), n - 1)
        d = [P[j + k - degree].copy() for j in range(degree + 1)]
        for r in range(1, degree + 1):
            for j in range(degree, r - 1, -1):
                i = j + k - degree
                den = knots[i + degree - r + 1] - knots[i]
                a = 0.0 if den == 0 else (t - knots[i]) / den
                d[j] = (1 - a) * d[j - 1] + a * d[j]
        out.append(d[degree][:2] / d[degree][2])
    return np.array(out)


MAX_ENTITIES = 1_000_000     # Obergrenze für Elemente inkl. aufgelöster Blöcke


def _finite(*vals) -> bool:
    return all(math.isfinite(v) for v in vals)


class _DxfConverter:
    def __init__(self, blocks, layer_colors, tol, warnings):
        self.blocks = blocks
        self.layer_colors = layer_colors
        self.tol = tol
        self.warnings = warnings
        self.unsupported: dict[str, int] = {}
        self.budget = MAX_ENTITIES             # schützt vor Block-Bomben (verschachtelte INSERT-Arrays)
        self._sizes: dict[str, float] = {}

    def size(self, etype, data, depth=0) -> float:
        """Anzahl Elemente, die ein Element nach dem Auflösen aller Blöcke ergibt (ohne es aufzulösen)."""
        if etype != "INSERT":
            return 1
        if depth > 20:
            return 0
        name = _get(data, 2, "", str)
        if name not in self.blocks:
            return 1
        if name not in self._sizes:
            self._sizes[name] = 0                # gegen zyklische Blöcke
            self._sizes[name] = 1 + sum(self.size(et, d, depth + 1) for et, d in self.blocks[name][1])
        cols, rows = _get(data, 70, 1, int), _get(data, 71, 1, int)
        return max(1, min(cols, 10_000)) * max(1, min(rows, 10_000)) * self._sizes[name]

    def _take(self, n=1):
        self.budget -= n
        if self.budget < 0:
            raise ValueError(f"Die DXF-Datei ist zu komplex (mehr als {MAX_ENTITIES} Elemente nach dem "
                             "Auflösen der Blöcke).")

    def convert(self, etype, data, m, depth=0) -> list[tuple[g.Polyline, str]]:
        layer = _get(data, 8, "0", str)
        res: list[g.Polyline] = []
        if depth == 0:
            self._take(self.size(etype, data))       # vorab prüfen, bevor etwas aufgelöst wird
        sf = g.matrix_scale_factor(m)
        if not math.isfinite(sf) or sf < 1e-9:
            return []                                    # Block mit Skalierung 0 o. Ä.
        tol = self.tol / sf
        if etype == "LINE":
            p = np.array([[_get(data, 10, 0), _get(data, 20, 0)], [_get(data, 11, 0), _get(data, 21, 0)]])
            res.append(g.Polyline.from_bez(g.BezPath.from_points(p, False), tol))
        elif etype == "LWPOLYLINE":
            flags = _get(data, 70, 0, int)
            verts, bulges = [], []
            x = None
            for c, v in data:
                if c == 10:
                    x = float(v)
                elif c == 20 and x is not None:
                    verts.append((x, float(v)))
                    bulges.append(0.0)
                    x = None
                elif c == 42 and bulges:
                    bulges[-1] = float(v)
            res += self._bulge_poly(verts, bulges, bool(flags & 1), tol)
        elif etype == "CIRCLE":
            r = _get(data, 40, 0)
            if r > 0:
                res.append(g.Polyline.from_bez(g.ellipse_bez(_get(data, 10, 0), _get(data, 20, 0), r, r), tol))
        elif etype == "ARC":
            cx, cy, r = _get(data, 10, 0), _get(data, 20, 0), _get(data, 40, 0)
            d0, d1 = _get(data, 50, 0), _get(data, 51, 360)
            if not _finite(cx, cy, r, d0, d1) or r <= 0:
                return []
            a0, a1 = math.radians(d0 % 360), math.radians(d1 % 360)
            if a1 <= a0:
                a1 += 2 * math.pi
            res.append(_arc_bez(cx, cy, r, r, 0.0, a0, a1 - a0, tol))
        elif etype == "ELLIPSE":
            cx, cy = _get(data, 10, 0), _get(data, 20, 0)
            mx, my = _get(data, 11, 1), _get(data, 21, 0)
            ratio = _get(data, 40, 1)
            t0, t1 = _get(data, 41, 0), _get(data, 42, 2 * math.pi)
            if not _finite(cx, cy, mx, my, ratio, t0, t1):
                return []
            full = abs((t1 - t0) - 2 * math.pi) < 1e-6
            t0 %= 2 * math.pi
            t1 = t0 + 2 * math.pi if full else t1 % (2 * math.pi)
            if t1 <= t0:
                t1 += 2 * math.pi
            a = math.hypot(mx, my)
            rot = math.atan2(my, mx)
            if abs((t1 - t0) - 2 * math.pi) < 1e-6:
                res.append(g.Polyline.from_bez(g.ellipse_bez(cx, cy, a, a * ratio, rot), tol))
            else:
                res.append(_arc_bez(cx, cy, a, a * ratio, rot, t0, t1 - t0, tol))
        elif etype == "SPLINE":
            res += self._spline(data, tol)
        elif etype == "POLYLINE":
            pass   # wird in _polyline_entities behandelt
        elif etype == "INSERT":
            if depth > 20:
                return []
            name = _get(data, 2, "", str)
            blk = self.blocks.get(name)
            if blk is None:
                return []
            base, ents = blk
            ins = (_get(data, 10, 0), _get(data, 20, 0))
            sx, sy = _get(data, 41, 1), _get(data, 42, 1)
            rot = _get(data, 50, 0)
            cols, rows = _get(data, 70, 1, int), _get(data, 71, 1, int)
            cols, rows = max(1, min(cols, 10_000)), max(1, min(rows, 10_000))
            cs, rs = _get(data, 44, 0), _get(data, 45, 0)
            out = []
            for ci in range(max(1, cols)):
                for ri in range(max(1, rows)):
                    mm = (m @ g.translate(*ins) @ g.rotate(rot) @ g.translate(ci * cs, ri * rs)
                          @ g.scale(sx, sy) @ g.translate(-base[0], -base[1]))
                    for et, d in _merge_polylines(ents):
                        for poly, lay in self.convert(et, d, mm, depth + 1):
                            out.append((poly, layer if lay == "0" else lay))
            return out
        elif etype in ("POINT", "TEXT", "MTEXT", "DIMENSION", "HATCH", "SOLID", "ATTDEF", "ATTRIB",
                       "VIEWPORT", "SEQEND", "VERTEX", "3DFACE", "LEADER", "MLEADER", "IMAGE", "WIPEOUT"):
            if etype in ("TEXT", "MTEXT", "DIMENSION", "HATCH"):
                self.unsupported[etype] = self.unsupported.get(etype, 0) + 1
        elif etype == "_POLYLINE":   # zusammengeführte POLYLINE
            flags = _get(data, 70, 0, int)
            verts = [(v[0], v[1]) for v in data[-1][1]]
            bulges = [v[2] for v in data[-1][1]]
            res += self._bulge_poly(verts, bulges, bool(flags & 1), tol)
        else:
            self.unsupported[etype] = self.unsupported.get(etype, 0) + 1
        return [(p.transformed(m), layer) for p in res if len(p.pts) >= 2]

    def _bulge_poly(self, verts, bulges, closed, tol):
        """Polylinie mit Bulges -> Bézierpfad (Bögen als kubische Segmente)."""
        if len(verts) < 2:
            return []
        bb = g.BezBuilder(tol)
        bb.move(np.array(verts[0], float))
        n = len(verts)
        last = n if closed else n - 1
        for i in range(last):
            p1, p2 = np.array(verts[i], float), np.array(verts[(i + 1) % n], float)
            b = bulges[i]
            c = p2 - p1
            d = math.hypot(*c)
            if abs(b) < 1e-9 or d < 1e-12:
                bb.line(p2)
                continue
            theta = 4 * math.atan(b)
            nl = np.array([-c[1], c[0]]) / d
            center = (p1 + p2) / 2 + nl * ((d / 2) / math.tan(theta / 2))
            r = math.hypot(*(p1 - center))
            a0 = math.atan2(p1[1] - center[1], p1[0] - center[0])
            for c1, c2, q in g.arc_to_cubics(center[0], center[1], r, r, 0.0, a0, theta):
                bb.cubic(c1, c2, q)
            bb.a[-1] = p2
            bb.o[-1] = p2
        bb.finish(closed)
        return bb.out

    def _spline(self, data, tol):
        degree = _get(data, 71, 3, int)
        flags = _get(data, 70, 0, int)
        knots = np.array([float(v) for c, v in data if c == 40])
        weights = np.array([float(v) for c, v in data if c == 41])
        ctrl, fit = [], []
        x = None
        fx = None
        for c, v in data:
            if c == 10:
                x = float(v)
            elif c == 20 and x is not None:
                ctrl.append((x, float(v)))
                x = None
            elif c == 11:
                fx = float(v)
            elif c == 21 and fx is not None:
                fit.append((fx, float(v)))
                fx = None
        closed = bool(flags & 1)
        if len(ctrl) >= degree + 1:
            ctrl = np.array(ctrl)
            L = float(np.sum(np.hypot(*np.diff(ctrl, axis=0).T)))
            n = int(min(4000, max(16, L / max(tol * 5, 0.05))))
            pts = _nurbs(ctrl, knots, degree, weights if len(weights) else None, n)
            return [g.Polyline(pts, closed)]
        if len(fit) >= 2:
            self.warnings.append("SPLINE nur mit Stützpunkten: als Polylinie angenähert.")
            return [g.Polyline(np.array(fit), closed)]
        return []


def _merge_polylines(ents):
    """Fasst POLYLINE + VERTEX... + SEQEND zu einer Pseudo-Entität '_POLYLINE' zusammen."""
    out = []
    i = 0
    while i < len(ents):
        et, d = ents[i]
        if et == "POLYLINE":
            flags = _get(d, 70, 0, int)
            if flags & (16 | 64):      # Polygonnetz / Polyface -> überspringen
                i += 1
                while i < len(ents) and ents[i][0] in ("VERTEX", "SEQEND"):
                    i += 1
                continue
            verts = []
            i += 1
            while i < len(ents) and ents[i][0] == "VERTEX":
                vd = ents[i][1]
                verts.append((_get(vd, 10, 0), _get(vd, 20, 0), _get(vd, 42, 0)))
                i += 1
            if i < len(ents) and ents[i][0] == "SEQEND":
                i += 1
            out.append(("_POLYLINE", list(d) + [(-1, verts)]))
            continue
        out.append((et, d))
        i += 1
    return out


def import_dxf(path: str, tol: float = g.CURVE_TOL) -> tuple[list[ImportedPath], list[str]]:
    pairs = _read_pairs(path)
    warnings: list[str] = []
    units = 0
    blocks: dict[str, tuple] = {}
    layer_colors: dict[str, str] = {}
    entities = []
    i = 0
    while i < len(pairs):
        code, val = pairs[i]
        if code == 0 and val == "SECTION" and i + 1 < len(pairs):
            name = pairs[i + 1][1]
            if name == "HEADER":
                j = i + 2
                while j < len(pairs) and not (pairs[j][0] == 0 and pairs[j][1] == "ENDSEC"):
                    if pairs[j] == (9, "$INSUNITS") and j + 1 < len(pairs):
                        try:
                            units = int(pairs[j + 1][1])
                        except ValueError:
                            pass
                    j += 1
                i = j
            elif name == "TABLES":
                ents, j = _split_entities(pairs, i + 2)
                for et, d in ents:
                    if et == "LAYER":
                        lname = _get(d, 2, "", str)
                        col = abs(_get(d, 62, 7, int))
                        layer_colors[lname] = ACI.get(col, PALETTE[col % len(PALETTE)])
                i = j
            elif name == "BLOCKS":
                ents, j = _split_entities(pairs, i + 2)
                cur_name, cur_base = None, (0, 0)
                cur: list = []
                for et, d in ents:
                    if et == "BLOCK":
                        cur_name = _get(d, 2, "", str)
                        cur_base = (_get(d, 10, 0), _get(d, 20, 0))
                        cur = []
                    elif et == "ENDBLK":
                        if cur_name is not None:
                            blocks[cur_name] = (cur_base, cur)
                        cur_name = None
                    elif cur_name is not None:
                        cur.append((et, d))
                i = j
            elif name == "ENTITIES":
                entities, j = _split_entities(pairs, i + 2)
                i = j
        i += 1

    scale_mm = INSUNITS_MM.get(units, 1.0)
    if units not in INSUNITS_MM:
        warnings.append(f"Unbekannte DXF-Einheit {units}, verwende mm.")
    m = g.scale(scale_mm)
    conv = _DxfConverter(blocks, layer_colors, tol, warnings)
    result: list[ImportedPath] = []
    for et, d in _merge_polylines(entities):
        for poly, layer in conv.convert(et, d, m):
            color = layer_colors.get(layer) or PALETTE[(list(layer_colors).index(layer) if layer in layer_colors
                                                         else len(layer_colors) + sum(map(ord, layer)) % 7) % len(PALETTE)]
            result.append(ImportedPath(poly, f"DXF:{layer}", color, False))
    if conv.unsupported:
        warnings.append("Nicht unterstützt (ignoriert): " +
                        ", ".join(f"{k}×{v}" for k, v in sorted(conv.unsupported.items())))
    return result, warnings


def _arc_bez(cx, cy, rx, ry, rot, t0, dt, tol):
    """Elliptischer Bogen als offener Bézierpfad."""
    bb = g.BezBuilder(tol)
    c, s_ = math.cos(rot), math.sin(rot)
    start = (cx + rx * math.cos(t0) * c - ry * math.sin(t0) * s_, cy + rx * math.cos(t0) * s_ + ry * math.sin(t0) * c)
    bb.move(start)
    for c1, c2, q in g.arc_to_cubics(cx, cy, rx, ry, rot, t0, dt):
        bb.cubic(c1, c2, q)
    bb.finish(False)
    return bb.out[0]

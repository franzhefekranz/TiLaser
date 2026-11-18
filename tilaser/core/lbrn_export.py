"""TiLaser-Projekt als LightBurn-Datei (.lbrn2) speichern.

* Jede Ebene wird eine LightBurn-Ebene (``CutSetting``): Ebenen-Index nach der nächstliegenden
  LightBurn-Farbe, Modus Schneiden → Cut, Füllen → Scan, Raster → Image, dazu Geschwindigkeit (mm/s),
  Leistung, Durchgänge, Zeilenabstand, Winkel, Kreuzschraffur, bidirektional, Ausgabe, Reihenfolge.
* Vektorobjekte werden Pfade mit Bézierkurven (``VertList``/``PrimList``) in Weltkoordinaten,
  Objekte mit mehreren Konturen eine Gruppe; Bilder werden ``Bitmap`` mit eingebettetem PNG.
"""
from __future__ import annotations

import base64
import io
import math
import xml.etree.ElementTree as ET

import numpy as np

from . import geometry as g
from .lbrn_import import LB_COLORS
from .model import Layer, Project, _lab, nearest_palette

_TYPES = {"cut": "Cut", "fill": "Scan", "offset": "Offset", "raster": "Image"}
_DITHER = {"floyd": "dither", "jarvis": "jarvis", "stucki": "stucki", "atkinson": "atkinson",
           "threshold": "threshold", "grayscale": "grayscale"}


def _n(v: float, dec: int = 6) -> str:
    s = f"{float(v):.{dec}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _assign_indices(layers: list[Layer]) -> tuple[dict[str, int], list[str]]:
    """Ebene -> LightBurn-Index 0..29 (nächste freie LightBurn-Farbe)."""
    free = list(range(30))
    out, warn = {}, []
    for l in layers:
        if not free:
            out[l.id] = 29
            warn.append(f"Mehr als 30 Ebenen: „{l.name}“ liegt in LightBurn auf Ebene 29.")
            continue
        same = [i for i in free if nearest_palette(LB_COLORS[i]) == l.color.lower()]
        lab = _lab(l.color)
        best = same[0] if same else min(free, key=lambda i: sum((u - v) ** 2 for u, v in zip(lab, _lab(LB_COLORS[i]))))
        free.remove(best)
        out[l.id] = best
    return out, warn


def _cut_setting(parent, layer: Layer, idx: int, prio: int):
    tag = "CutSetting_Img" if layer.mode == "raster" else "CutSetting"
    lb_type = _TYPES.get(layer.mode, "Cut")
    if layer.mode == "fill" and any(s.mode == "cut" for s in layer.subs):
        lb_type = "Scan+Cut"
    cs = ET.SubElement(parent, tag, type=lb_type)

    def v(name, val):
        ET.SubElement(cs, name, Value=str(val))
    v("index", idx)
    v("name", layer.name)
    v("minPower", _n(layer.min_power))
    v("maxPower", _n(layer.power))
    v("maxPower2", _n(layer.power))
    v("speed", _n(layer.speed / 60.0))                     # mm/min -> mm/s
    v("priority", prio)
    v("numPasses", layer.passes)
    v("doOutput", 1 if layer.output else 0)
    if layer.mode in ("fill", "offset", "raster"):
        v("interval", _n(layer.interval))
        v("angle", _n(layer.angle))
        v("crossHatch", 1 if layer.crosshatch else 0)
        v("bidir", 1 if layer.bidirectional else 0)
    if layer.z_step:
        v("zPerPass", _n(layer.z_step))
    if layer.kerf:
        v("kerf", _n(layer.kerf))
    if layer.mode == "raster":
        v("ditherMode", _DITHER.get(layer.dither, "dither"))
        v("negativeImage", 1 if layer.invert else 0)


def _vertlist(bez: g.BezPath) -> tuple[str, str]:
    a, hin, hout = bez.anchors, bez.hin, bez.hout
    n = len(a)
    parts = []
    for i in range(n):
        s = f"V{_n(a[i][0])} {_n(a[i][1])}"
        s += f"c0x{_n(hout[i][0])}c0y{_n(hout[i][1])}" if not np.allclose(hout[i], a[i]) else "c0x1"
        s += f"c1x{_n(hin[i][0])}c1y{_n(hin[i][1])}" if not np.allclose(hin[i], a[i]) else "c1x1"
        parts.append(s)
    prims = []
    for k in range(bez.n_segments()):
        j = (k + 1) % n
        prims.append(f"{'L' if bez.is_line(k) else 'B'}{k} {j}")
    return "".join(parts), "".join(prims)


def _path_shape(parent, poly: g.Polyline, idx: int):
    bez = poly.bez if poly.bez is not None else g.BezPath.from_points(poly.pts, poly.closed)
    sh = ET.SubElement(parent, "Shape", Type="Path", CutIndex=str(idx))
    ET.SubElement(sh, "XForm").text = "1 0 0 1 0 0"
    vl, pl = _vertlist(bez)
    ET.SubElement(sh, "VertList").text = vl
    ET.SubElement(sh, "PrimList").text = pl
    return sh


def _bitmap_shape(parent, it, idx: int):
    from PIL import Image
    gray = np.asarray(it.image, np.uint8)
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(gray[::-1])).save(buf, "PNG")   # LightBurn: unterste Zeile zuerst
    m = it.matrix()
    sh = ET.SubElement(parent, "Shape", Type="Bitmap", CutIndex=str(idx), W=_n(it.img_w), H=_n(it.img_h),
                       Gamma="1", Contrast="0", Brightness="0", File=it.name,
                       Data=base64.b64encode(buf.getvalue()).decode("ascii"))
    ET.SubElement(sh, "XForm").text = " ".join(_n(x, 9) for x in (m[0, 0], m[1, 0], m[0, 1], m[1, 1], m[0, 2], m[1, 2]))
    return sh


def export_lbrn(project: Project, path: str) -> list[str]:
    """Schreibt das Projekt als .lbrn2. Rückgabe: Hinweise."""
    used = set().union(*[it.layer_ids() for it in project.items]) if project.items else set()
    layers = [l for l in project.layers if l.id in used]
    # Wie LightBurn: Bild-Ebenen (CutSetting_Img) und Vektor-Ebenen haben getrennte Nummern,
    # ein Bild kann also dieselbe Farbe/Nummer wie eine Vektor-Ebene tragen.
    idx, warn = _assign_indices([l for l in layers if l.mode != "raster"])
    idx_r, warn_r = _assign_indices([l for l in layers if l.mode == "raster"])
    idx.update(idx_r)
    warn += warn_r
    root = ET.Element("LightBurnProject", AppVersion="1.7.00", FormatVersion="1", MaterialHeight="0",
                      MirrorX="False", MirrorY="False")
    for prio, l in enumerate(layers):
        _cut_setting(root, l, idx[l.id], prio)
        if any(s.mode != "cut" or l.mode != "fill" for s in l.subs):
            warn.append(f"Unterebenen von „{l.name}“ werden in LightBurn nicht übernommen.")
    for it in project.items:
        if it.kind == "raster":
            if it.image is not None:
                _bitmap_shape(root, it, idx.get(it.layer_id, 0))
            continue
        m = it.matrix()
        polys = [(vp.poly.transformed(m), idx.get(vp.layer_id, 0)) for vp in it.paths if len(vp.poly.pts) >= 2]
        if not polys:
            continue
        if len(polys) == 1:
            _path_shape(root, *polys[0])
        else:
            grp = ET.SubElement(root, "Shape", Type="Group", CutIndex=str(polys[0][1]))
            ET.SubElement(grp, "XForm").text = "1 0 0 1 0 0"
            ch = ET.SubElement(grp, "Children")
            for p, i in polys:
                _path_shape(ch, p, i)
    ET.indent(root, "    ")
    data = ET.tostring(root, encoding="unicode")
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n' + data + "\n")
    return warn

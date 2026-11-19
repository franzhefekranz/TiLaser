"""Export der Arbeitsfläche als SVG oder DXF (Maße in mm, Lage wie auf dem Bett)."""
from __future__ import annotations

import base64
import io
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image

from .model import Project


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def export_svg(project: Project, path: str, bed_w: float, bed_h: float, only_output: bool = False):
    """SVG mit mm-Einheiten. Jede Ebene wird eine Gruppe (Inkscape-Ebene) in ihrer Farbe."""
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           f'width="{_fmt(bed_w)}mm" height="{_fmt(bed_h)}mm" viewBox="0 0 {_fmt(bed_w)} {_fmt(bed_h)}">']
    for layer in project.layers:
        if only_output and not layer.output:
            continue
        body = []
        for it in project.items:
            if it.kind == "raster" and it.layer_id == layer.id and it.image is not None:
                buf = io.BytesIO()
                Image.fromarray(it.image).save(buf, format="PNG")
                data = base64.b64encode(buf.getvalue()).decode("ascii")
                w, h = it.img_w * it.scale, it.img_h * it.scale
                sx = -1 if it.mirror_x else 1
                body.append(f'<image x="{_fmt(-w / 2)}" y="{_fmt(-h / 2)}" width="{_fmt(w)}" height="{_fmt(h)}" '
                            f'transform="translate({_fmt(it.x)},{_fmt(bed_h - it.y)}) rotate({_fmt(-it.rotation)}) '
                            f'scale({sx},1)" preserveAspectRatio="none" xlink:href="data:image/png;base64,{data}"/>')
            elif it.kind == "vector":
                d = []
                for p in it.world_paths(layer.id):
                    pts = p.pts
                    d.append("M" + " L".join(f"{_fmt(x)},{_fmt(bed_h - y)}" for x, y in pts) + (" Z" if p.closed else ""))
                if d:
                    filled = layer.mode in ("fill", "raster")
                    fill = f'fill="{layer.color}" fill-rule="evenodd"' if filled else 'fill="none"'
                    body.append(f'<path d="{" ".join(d)}" {fill} stroke="{layer.color}" stroke-width="0.1"/>')
        if body:
            out.append(f'<g inkscape:groupmode="layer" inkscape:label="{escape(layer.name)}" id="{layer.id}">')
            out += body
            out.append("</g>")
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


# AutoCAD-Farbindex grob aus RGB
_ACI = {1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0), 4: (0, 255, 255), 5: (0, 0, 255), 6: (255, 0, 255),
        7: (0, 0, 0), 8: (128, 128, 128)}


def _aci(color: str) -> int:
    c = np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)])
    return min(_ACI, key=lambda k: np.sum((np.array(_ACI[k]) - c) ** 2))


def export_dxf(project: Project, path: str, only_output: bool = False):
    """ASCII-DXF (R12-kompatibel bis auf LWPOLYLINE, R2000), Einheit mm, eine DXF-Ebene je Ebene.
    Rasterbilder werden nicht exportiert."""
    L = []

    def pair(code, val):
        L.append(str(code))
        L.append(str(val))

    pair(0, "SECTION"); pair(2, "HEADER")
    pair(9, "$ACADVER"); pair(1, "AC1015")
    pair(9, "$INSUNITS"); pair(70, 4)
    pair(0, "ENDSEC")
    pair(0, "SECTION"); pair(2, "TABLES")
    pair(0, "TABLE"); pair(2, "LAYER"); pair(70, len(project.layers))
    names = {}
    for l in project.layers:
        name = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in l.name) or l.id
        names[l.id] = name
        pair(0, "LAYER"); pair(2, name); pair(70, 0); pair(62, _aci(l.color)); pair(6, "CONTINUOUS")
    pair(0, "ENDTAB")
    pair(0, "ENDSEC")
    pair(0, "SECTION"); pair(2, "ENTITIES")
    for l in project.layers:
        if only_output and not l.output:
            continue
        for it in project.items:
            if it.kind != "vector":
                continue
            for p in it.world_paths(l.id):
                pair(0, "LWPOLYLINE"); pair(8, names[l.id]); pair(90, len(p.pts)); pair(70, 1 if p.closed else 0)
                for x, y in p.pts:
                    pair(10, _fmt(x)); pair(20, _fmt(y))
    pair(0, "ENDSEC")
    pair(0, "EOF")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")

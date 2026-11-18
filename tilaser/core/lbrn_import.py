"""LightBurn-Projekte (.lbrn2 und das ältere .lbrn) lesen.

Beide Formate sind XML. Gelesen werden
* Ebenen (``CutSetting`` / ``CutSetting_Img``): Name, Modus, Geschwindigkeit, Leistung, Durchgänge,
  Zeilenabstand, Winkel, Kreuzschraffur, bidirektional, Ausgabe, Reihenfolge, Z-Schritt, Dithering,
* Formen (``Shape``): Path (Linien und Bézierkurven, auch geteilte Geometrie über VertID/PrimID),
  Rect (mit Eckenradius), Ellipse, Polygon, Group (verschachtelt), Text (über die gespeicherten
  Umrisse ``BackupPath``) und Bitmap (eingebettetes Bild).

Koordinaten: LightBurn rechnet in mm mit y nach oben – wie TiLaser. ``XForm`` enthält die Matrix
``m11 m12 m21 m22 dx dy`` (x' = m11·x + m21·y + dx, y' = m12·x + m22·y + dy).
Das Format ist nicht offiziell dokumentiert; unbekannte Elemente werden mit einem Hinweis übersprungen.
"""
from __future__ import annotations

import base64
import io
import math
import re
import xml.etree.ElementTree as ET

import numpy as np

from . import geometry as g
from .model import MODE_LABELS, Item, Layer, Project, VectorPath, color_name, nearest_palette, new_id

# Standardpalette von LightBurn (Ebenen 00–29, T1, T2)
LB_COLORS = ["#000000", "#0000ff", "#ff0000", "#00e000", "#d0d000", "#ff8000", "#00e0e0", "#ff00ff",
             "#b4b4b4", "#0000a0", "#a00000", "#00a000", "#a0a000", "#c08000", "#00a0ff", "#a000a0",
             "#808080", "#7d87b9", "#bb7784", "#4a6fe3", "#d33f6a", "#8cd78c", "#f0b98d", "#f6c4e1",
             "#fa9ed4", "#500a78", "#b45a00", "#004754", "#86fa88", "#ffdb66", "#f37d0d", "#2a9d8f"]

_DITHER = {"threshold": "threshold", "grayscale": "grayscale", "atkinson": "atkinson", "stucki": "stucki",
           "jarvis": "jarvis", "dither": "floyd", "ordered": "floyd", "newsprint": "floyd", "halftone": "floyd",
           "sketch": "floyd"}

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_VERT = re.compile(rf"V\s*({_NUM})\s+({_NUM})((?:\s*c[01][xy]\s*{_NUM})*)")
_CTRL = re.compile(rf"c([01])([xy])\s*({_NUM})")
_PRIM = re.compile(r"([LB])\s*(\d+)\s+(\d+)")


class LbrnError(Exception):
    pass


# --------------------------------------------------------------------------- Hilfen
def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _xform(el) -> np.ndarray:
    x = el.find("XForm")
    if x is None or not (x.text or "").strip():
        return g.identity()
    v = [_f(t) for t in x.text.split()]
    if len(v) != 6:
        return g.identity()
    a, b, c, d, e, f = v
    return np.array([[a, c, e], [b, d, f], [0, 0, 1.0]])


def _decompose(m) -> tuple[float, float, float, float]:
    """Matrix -> (Skalierung x, Skalierung y mit Vorzeichen, Drehung in Grad, Scherung)."""
    a, b, c, d = m[0, 0], m[1, 0], m[0, 1], m[1, 1]
    sx = math.hypot(a, b) or 1e-12
    rot = math.degrees(math.atan2(b, a))
    det = a * d - b * c
    sy = det / sx
    shear = (a * c + b * d) / (sx * sx)
    return sx, sy, rot, shear


# --------------------------------------------------------------------------- Geometrie
class _Vert:
    __slots__ = ("p", "c0", "c1")

    def __init__(self, p, c0=None, c1=None):
        self.p, self.c0, self.c1 = p, c0, c1


def parse_vertlist(text: str) -> list[_Vert]:
    out = []
    for m in _VERT.finditer(text or ""):
        p = np.array([float(m.group(1)), float(m.group(2))])
        c = {}
        for cm in _CTRL.finditer(m.group(3) or ""):
            c[(cm.group(1), cm.group(2))] = float(cm.group(3))
        c0 = np.array([c[("0", "x")], c[("0", "y")]]) if ("0", "x") in c and ("0", "y") in c else None
        c1 = np.array([c[("1", "x")], c[("1", "y")]]) if ("1", "x") in c and ("1", "y") in c else None
        out.append(_Vert(p, c0, c1))
    return out


def parse_primlist(text: str, n_verts: int) -> list[tuple[str, int, int]]:
    t = (text or "").strip()
    if not t or t.startswith("Line"):                       # LineClosed / LineOpen / leer
        prims = [("L", i, i + 1) for i in range(n_verts - 1)]
        if t == "LineClosed" and n_verts > 2:
            prims.append(("L", n_verts - 1, 0))
        return prims
    return [(m.group(1), int(m.group(2)), int(m.group(3))) for m in _PRIM.finditer(t)]


def build_paths(verts: list[_Vert], prims, m: np.ndarray) -> list[g.Polyline]:
    """Primitive zu zusammenhängenden Pfaden verketten (offene und geschlossene)."""
    bb = g.BezBuilder()
    out: list[g.Polyline] = []
    start = last = None
    n = len(verts)

    def finish():
        if start is not None:
            bb.finish(closed=(last == start))
            out.extend(bb.out)
            bb.out.clear()

    for kind, i, j in prims:
        if not (0 <= i < n and 0 <= j < n):
            continue
        if last is None or i != last:
            finish()
            start = i
            bb.move(verts[i].p)
        if kind == "B":
            c1 = verts[i].c0 if verts[i].c0 is not None else verts[i].p
            c2 = verts[j].c1 if verts[j].c1 is not None else verts[j].p
            bb.cubic(c1, c2, verts[j].p)
        else:
            bb.line(verts[j].p)
        last = j
        if last == start:                                  # Kontur geschlossen
            finish()
            start = last = None
    finish()
    return [p.transformed(m) for p in out]


def _legacy_geometry(el):
    verts = []
    for v in el.findall("V"):
        p = np.array([_f(v.get("vx")), _f(v.get("vy"))])
        c0 = np.array([_f(v.get("c0x")), _f(v.get("c0y"))]) if v.get("c0x") is not None and v.get("c0y") is not None else None
        c1 = np.array([_f(v.get("c1x")), _f(v.get("c1y"))]) if v.get("c1x") is not None and v.get("c1y") is not None else None
        verts.append(_Vert(p, c0, c1))
    prims = []
    for pr in el.findall("P"):
        t = pr.get("T", "L")
        if t.startswith("Line") and pr.get("p0") is None:
            prims += parse_primlist(t, len(verts))
        else:
            prims.append(("B" if t.upper().startswith("B") else "L", int(_f(pr.get("p0"))), int(_f(pr.get("p1")))))
    if verts and not prims:
        prims = parse_primlist("LineOpen", len(verts))
    return verts, prims


# --------------------------------------------------------------------------- Ebenen
def _cut_settings(root) -> dict[tuple[int, str], dict]:
    """Ebeneneinstellungen je (Index, Art). Vektor- (CutSetting) und Bildeinstellungen (CutSetting_Img)
    können denselben Index haben – LightBurn trennt sie nach der Art der Form."""
    out = {}
    for cs in list(root):
        if not cs.tag.startswith("CutSetting"):
            continue
        d = {"type": cs.get("type", "Cut")}
        for ch in cs.iter():
            if ch is not cs and ch.get("Value") is not None:
                d.setdefault(ch.tag, ch.get("Value"))
        idx = int(_f(d.get("index"), len(out)))
        out[(idx, "raster" if cs.tag == "CutSetting_Img" else "vector")] = d
    return out


def _truthy(v, default=False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes")


def _make_layer(idx: int, cs: dict | None, kind: str) -> Layer:
    """kind: 'vector' oder 'raster'."""
    cs = cs or {}
    lb_type = cs.get("type", "Image" if kind == "raster" else "Cut")
    if kind == "raster":
        mode = "raster"
    else:
        mode = {"Cut": "cut", "Scan": "fill", "Scan+Cut": "fill", "Offset": "fill", "Image": "fill"}.get(lb_type, "cut")
    color = nearest_palette(LB_COLORS[idx % len(LB_COLORS)])
    lname = cs.get("name") or f"C{idx:02d}"
    if re.fullmatch(r"C\d\d|T\d", lname):
        lname = f"{MODE_LABELS[mode].split()[0]} {color_name(color)}"
    if kind == "vector" and lb_type == "Image":
        lname += " (Vektor)"
    layer = Layer(id=new_id("L"), name=lname, color=color, mode=mode)
    layer.speed = round(_f(cs.get("speed"), 100.0) * 60.0, 1)           # mm/s -> mm/min
    layer.power = max(0.0, min(100.0, _f(cs.get("maxPower"), 20.0)))
    layer.min_power = max(0.0, min(layer.power, _f(cs.get("minPower"), 10.0 if mode == "raster" else 0.0)))
    layer.passes = max(1, int(_f(cs.get("numPasses"), 1)))
    layer.interval = _f(cs.get("interval"), 0.1) or 0.1
    layer.angle = _f(cs.get("angle"), 0.0)
    layer.crosshatch = _truthy(cs.get("crossHatch"))
    layer.bidirectional = _truthy(cs.get("bidir"), True)
    layer.output = _truthy(cs.get("doOutput"), True) and not lname.startswith("T")
    layer.z_step = abs(_f(cs.get("zPerPass"), 0.0))
    layer.kerf = abs(_f(cs.get("kerf"), 0.0))
    if mode == "raster":
        layer.dither = _DITHER.get(str(cs.get("ditherMode", "threshold")).lower(), "floyd")
        layer.invert = _truthy(cs.get("negativeImage"))
    if lb_type == "Scan+Cut" and kind == "vector":
        sub = layer.add_sub("cut")
        sub.speed, sub.power, sub.passes = layer.speed, layer.power, layer.passes
    return layer


# --------------------------------------------------------------------------- Bilder
def _bitmap(el, m) -> tuple[np.ndarray, float, float, float, float, float] | None:
    data = el.get("Data")
    if data is None:
        d = el.find("Data")
        data = d.text if d is not None else None
    if not data:
        return None
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(data)))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    gray = np.asarray(img.convert("L"), np.uint8)
    # LightBurn legt die unterste Bildzeile zuerst ab (y nach oben) -> umdrehen
    gray = np.ascontiguousarray(gray[::-1])
    w, h = _f(el.get("W"), gray.shape[1]), _f(el.get("H"), gray.shape[0])
    sx, sy, rot, shear = _decompose(m)
    if sy < 0:                                             # gespiegelt: Bild vertikal kippen
        gray = np.ascontiguousarray(gray[::-1])
        sy = -sy
    cx, cy = m[0, 2], m[1, 2]
    return gray, abs(w) * sx, abs(h) * sy, rot, cx, cy


# --------------------------------------------------------------------------- Formen
class _Reader:
    def __init__(self):
        self.verts: dict[str, list] = {}
        self.prims: dict[str, str] = {}
        self.warn: list[str] = []
        self.unknown: dict[str, int] = {}
        self.texts_without_path = 0

    def shape(self, el, parent_m, out_paths, out_bitmaps):
        """Sammelt Konturen als (Polylinie, CutIndex) und Bilder als (Daten, CutIndex)."""
        t = el.get("Type", "")
        m = parent_m @ _xform(el)
        cut = int(_f(el.get("CutIndex"), 0))
        if t == "Group":
            ch = el.find("Children")
            for c in (ch if ch is not None else el).findall("Shape"):
                self.shape(c, m, out_paths, out_bitmaps)
        elif t == "Path" or (t == "Polygon" and (el.find("VertList") is not None or el.find("V") is not None
                                                  or el.get("VertID"))):
            for p in self._path(el, m):
                out_paths.append((p, cut))
        elif t == "Rect":
            from .shapes import rectangle
            w, h, cr = _f(el.get("W")), _f(el.get("H")), _f(el.get("Cr"))
            if w > 0 and h > 0:
                out_paths.append((rectangle(w, h, cr).transformed(m), cut))
        elif t == "Ellipse":
            rx, ry = _f(el.get("Rx")), _f(el.get("Ry"))
            if rx > 0 and ry > 0:
                out_paths.append((g.Polyline.from_bez(g.ellipse_bez(0, 0, rx, ry)).transformed(m), cut))
        elif t == "Polygon":
            from .shapes import polygon
            n = int(_f(el.get("N"), 0))
            rx, ry = _f(el.get("Rx")), _f(el.get("Ry", el.get("Rx")))
            if n >= 3 and rx > 0:
                out_paths.append((polygon(n, 2 * rx, 2 * (ry or rx)).transformed(m), cut))
            else:
                self.unknown["Polygon"] = self.unknown.get("Polygon", 0) + 1
        elif t == "Text":
            bp = el.find("BackupPath")
            if bp is not None:
                # Die Umrisse stehen bereits in Projektkoordinaten (ihre XForm ist die des Textes)
                for p in self._path(bp, parent_m @ _xform(bp)):
                    out_paths.append((p, cut))
            else:
                self.texts_without_path += 1
        elif t == "Bitmap":
            try:
                b = _bitmap(el, m)
            except Exception as e:
                self.warn.append(f"Bild konnte nicht gelesen werden: {e}")
                b = None
            if b is not None:
                name = el.get("File") or "Bild"
                out_bitmaps.append((b, cut, name, el))
        else:
            self.unknown[t or "?"] = self.unknown.get(t or "?", 0) + 1

    def _path(self, el, m):
        vid, pid = el.get("VertID"), el.get("PrimID")
        vl, pl = el.find("VertList"), el.find("PrimList")
        if vl is not None:
            verts = parse_vertlist(vl.text)
        elif el.find("V") is not None:                     # altes .lbrn-Format
            verts, prims = _legacy_geometry(el)
            return build_paths(verts, prims, m)
        else:
            verts = self.verts.get(vid, [])
        if pl is not None:
            prims = parse_primlist(pl.text, len(verts))
        elif pid in self.prims:
            prims = parse_primlist(self.prims[pid], len(verts))
        else:
            prims = parse_primlist("", len(verts))
        return build_paths(verts, prims, m)


# --------------------------------------------------------------------------- Einstieg
def load_lbrn(path: str) -> tuple[Project, list[str]]:
    """LightBurn-Datei als neues TiLaser-Projekt laden. Rückgabe: (Projekt, Hinweise)."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise LbrnError(f"Keine gültige LightBurn-Datei: {e}") from e
    root = tree.getroot()
    if root.tag != "LightBurnProject":
        raise LbrnError("Keine LightBurn-Datei (Wurzelelement LightBurnProject fehlt).")
    settings = _cut_settings(root)
    rd = _Reader()
    for sh in root.iter():                       # geteilte Geometrie vorab sammeln (VertID/PrimID)
        vl, pl = sh.find("VertList"), sh.find("PrimList")
        if vl is not None and sh.get("VertID") is not None:
            rd.verts.setdefault(sh.get("VertID"), parse_vertlist(vl.text))
        if pl is not None and sh.get("PrimID") is not None:
            rd.prims.setdefault(sh.get("PrimID"), pl.text or "")      # Rohtext, je nach Punktzahl auswerten
    for el in list(root.iter("Shape")) + list(root.iter("BackupPath")):   # geteilte Geometrie vorab sammeln
        vl, pl = el.find("VertList"), el.find("PrimList")
        if vl is not None and el.get("VertID") is not None:
            rd.verts.setdefault(el.get("VertID"), parse_vertlist(vl.text))
        if pl is not None and el.get("PrimID") is not None:
            rd.prims.setdefault(el.get("PrimID"), pl.text)
    pr = Project()
    pr.layers = []
    layers: dict[tuple[int, str], Layer] = {}

    def layer_for(idx, kind):
        key = (idx, kind)
        if key not in layers:
            cs = settings.get((idx, kind))
            if cs is None:                                   # nur eine Art vorhanden: die nehmen
                cs = settings.get((idx, "vector" if kind == "raster" else "raster"))
                if cs is not None and kind == "raster":
                    cs = dict(cs, type="Image")
            layers[key] = _make_layer(idx, cs, kind)
        return layers[key]

    for n, el in enumerate(root.findall("Shape")):
        paths, bitmaps = [], []
        rd.shape(el, g.identity(), paths, bitmaps)
        paths = [(p, c) for p, c in paths if len(p.pts) >= 2]
        if paths:
            bb = g.bbox([p for p, _ in paths])
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            t = el.get("Type", "Form")
            name = {"Group": "Gruppe", "Path": "Pfad", "Rect": "Rechteck", "Ellipse": "Ellipse",
                    "Text": f"Text {' '.join(el.get('Str', '').split())}".strip(), "Polygon": "Polygon"}.get(t, t)
            it = Item(id=new_id("I"), name=name, kind="vector", x=cx, y=cy)
            for p, c in paths:
                it.paths.append(VectorPath(p.transformed(g.translate(-cx, -cy)), layer_for(c, "vector").id))
            pr.items.append(it)
        for (gray, w, h, rot, cx, cy), c, name, bel in bitmaps:
            layer = layer_for(c, "raster")
            if bel.get("Gamma") is not None:
                layer.gamma = _f(bel.get("Gamma"), 1.0) or 1.0
            if bel.get("Contrast") is not None:
                layer.contrast = max(-100.0, min(100.0, _f(bel.get("Contrast")) * 100 if abs(_f(bel.get("Contrast"))) <= 1 else _f(bel.get("Contrast"))))
            if bel.get("Brightness") is not None:
                layer.brightness = max(-100.0, min(100.0, _f(bel.get("Brightness")) * 100 if abs(_f(bel.get("Brightness"))) <= 1 else _f(bel.get("Brightness"))))
            it = Item(id=new_id("I"), name=name.replace("\\", "/").split("/")[-1], kind="raster", image=gray,
                      img_w=w, img_h=h, layer_id=layer.id, x=cx, y=cy, rotation=rot)
            pr.items.append(it)

    # Ebenen in LightBurn-Reihenfolge (priority, dann Index)
    def prio(key):
        cs = settings.get(key, settings.get((key[0], "vector"), {}))
        return (_f(cs.get("priority"), key[0]), key[0], key[1])
    pr.layers = [layers[k] for k in sorted(layers, key=prio)]

    warn = rd.warn
    if rd.texts_without_path:
        warn.append(f"{rd.texts_without_path} Text(e) ohne gespeicherte Umrisse übersprungen "
                    "(in LightBurn in einen Pfad umwandeln und neu speichern).")
    for t, n in rd.unknown.items():
        warn.append(f"{n} Form(en) vom Typ „{t}“ werden nicht unterstützt und wurden übersprungen.")
    if any(s.get("type") == "Offset" for s in settings.values()):
        warn.append("Ebenen mit „Offset-Füllung“ wurden als normale Füllung übernommen.")
    if not pr.items:
        warn.append("Die Datei enthält keine verwertbaren Formen.")
    return pr, warn

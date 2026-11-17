"""SVG-Import ohne externe Abhängigkeiten.

Unterstützt: path (alle Befehle inkl. Bögen), rect (inkl. rx/ry), circle, ellipse, line,
polyline, polygon, g, use, Transformationen, viewBox, Einheiten (mm, cm, in, pt, px).
Pfade werden nach Konturfarbe (stroke) bzw. Füllfarbe gruppiert.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

import numpy as np

from . import geometry as g
from .model import ImportedPath

UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6, "px": 25.4 / 96, "": 25.4 / 96}

NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "lime": "#00ff00",
    "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff", "aqua": "#00ffff", "magenta": "#ff00ff",
    "fuchsia": "#ff00ff", "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080",
    "brown": "#a52a2a", "navy": "#000080", "maroon": "#800000", "olive": "#808000", "teal": "#008080",
    "silver": "#c0c0c0", "pink": "#ffc0cb",
}

SKIP_TAGS = {"defs", "clipPath", "mask", "symbol", "marker", "pattern", "style", "script", "metadata",
             "title", "desc", "linearGradient", "radialGradient", "filter", "namedview"}

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_length(s: str | None, default: float | None = None) -> float | None:
    """Länge -> mm. Ohne Einheit: px (96 dpi)."""
    if s is None:
        return default
    m = re.match(rf"\s*({_NUM})\s*([a-z%]*)", s)
    if not m:
        return default
    v, u = float(m.group(1)), m.group(2)
    if u == "%":
        return default
    return v * UNIT_MM.get(u, UNIT_MM["px"])


def parse_color(s: str | None) -> str | None:
    """Gibt '#rrggbb', 'none' oder None (nicht gesetzt) zurück."""
    if s is None:
        return None
    s = s.strip().lower()
    if s in ("none", "transparent"):
        return "none"
    if s.startswith("url("):
        return "#000000"
    if s in NAMED_COLORS:
        return NAMED_COLORS[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) >= 6:
            return "#" + h[:6]
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        vals = []
        for p in parts[:3]:
            vals.append(int(float(p[:-1]) * 2.55) if p.endswith("%") else int(float(p)))
        return "#%02x%02x%02x" % tuple(max(0, min(255, v)) for v in vals)
    return "#000000"


def parse_transform(s: str | None) -> np.ndarray:
    m = g.identity()
    if not s:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        a = [float(x) for x in re.findall(_NUM, args)]
        if name == "matrix" and len(a) == 6:
            t = np.array([[a[0], a[2], a[4]], [a[1], a[3], a[5]], [0, 0, 1.0]])
        elif name == "translate":
            t = g.translate(a[0], a[1] if len(a) > 1 else 0)
        elif name == "scale":
            t = g.scale(a[0], a[1] if len(a) > 1 else a[0])
        elif name == "rotate":
            if len(a) == 3:
                t = g.translate(a[1], a[2]) @ g.rotate(a[0]) @ g.translate(-a[1], -a[2])
            else:
                t = g.rotate(a[0])
        elif name == "skewX":
            t = np.array([[1, math.tan(math.radians(a[0])), 0], [0, 1, 0], [0, 0, 1.0]])
        elif name == "skewY":
            t = np.array([[1, 0, 0], [math.tan(math.radians(a[0])), 1, 0], [0, 0, 1.0]])
        else:
            continue
        m = m @ t
    return m


def parse_style(el) -> dict:
    st = {}
    for k in ("stroke", "fill", "display", "visibility", "opacity", "fill-rule"):
        if el.get(k) is not None:
            st[k] = el.get(k)
    if el.get("style"):
        for part in el.get("style").split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                st[k.strip()] = v.strip()
    return st


# --------------------------------------------------------------------------- Pfad-Parser
class _Scanner:
    def __init__(self, d: str):
        self.d, self.i = d, 0

    def skip(self):
        while self.i < len(self.d) and self.d[self.i] in " \t\r\n,":
            self.i += 1

    def at_number(self) -> bool:
        self.skip()
        return self.i < len(self.d) and (self.d[self.i].isdigit() or self.d[self.i] in "+-.")

    def number(self) -> float:
        self.skip()
        m = re.compile(_NUM).match(self.d, self.i)
        if not m:
            raise ValueError(f"Zahl erwartet bei Position {self.i}")
        self.i = m.end()
        return float(m.group(0))

    def flag(self) -> bool:
        self.skip()
        c = self.d[self.i]
        if c not in "01":
            raise ValueError("Bogen-Flag erwartet")
        self.i += 1
        return c == "1"

    def command(self):
        self.skip()
        if self.i < len(self.d) and self.d[self.i].isalpha():
            c = self.d[self.i]
            self.i += 1
            return c
        return None


def _arc_to_points(p0, rx, ry, phi_deg, large, sweep, p1, m, tol):
    """SVG-Endpunkt-Bogen -> Punkte (in Weltkoordinaten), ohne Startpunkt."""
    x1, y1 = p0
    x2, y2 = p1
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return g.apply_matrix(m, np.array([p1]))
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    c, s = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = c * dx + s * dy, -s * dx + c * dy
    lam = (x1p ** 2) / rx ** 2 + (y1p ** 2) / ry ** 2
    if lam > 1:
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = c * cxp - s * cyp + (x1 + x2) / 2
    cy = s * cxp + c * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    sf = g.matrix_scale_factor(m)
    pts = g.ellipse_arc_points(cx, cy, rx, ry, phi, th1, th1 + dth, tol / sf, include_start=False)
    pts[-1] = p1
    return g.apply_matrix(m, pts)


def parse_path_d(d: str, m: np.ndarray, tol=g.CURVE_TOL) -> list[g.Polyline]:
    """Pfaddaten -> Polylinien in Weltkoordinaten (Matrix m bereits angewendet)."""
    sc = _Scanner(d)
    polys: list[g.Polyline] = []
    cur_pts: list[np.ndarray] = []
    pos = np.zeros(2)
    start = np.zeros(2)
    last_ctrl = None
    last_cmd = ""
    cmd = None

    def T(p):
        return g.apply_matrix(m, np.asarray(p, float)[None, :])

    def finish(closed=False):
        nonlocal cur_pts
        if cur_pts:
            pts = np.vstack(cur_pts)
            if len(pts) >= 2:
                if closed and np.allclose(pts[0], pts[-1]):
                    pts = pts[:-1]
                polys.append(g.Polyline(pts, closed))
        cur_pts = []

    while True:
        c = sc.command()
        if c is None:
            if cmd is None or cmd in "Zz" or not sc.at_number():
                break
            c = cmd
            if c in "Mm":                     # implizite Folgebefehle nach M sind L
                c = "L" if c == "M" else "l"
        cmd = c
        rel = c.islower()
        C = c.upper()
        base = pos.copy() if rel else np.zeros(2)

        if C == "M":
            finish()
            pos = base + [sc.number(), sc.number()]
            start = pos.copy()
            cur_pts = [T(pos)]
            last_ctrl = None
        elif C == "Z":
            if cur_pts:
                finish(closed=True)
            pos = start.copy()
            cur_pts = []
            last_ctrl = None
        elif C in "LHV":
            if C == "L":
                pos = base + [sc.number(), sc.number()]
            elif C == "H":
                v = sc.number()
                pos = np.array([pos[0] + v if rel else v, pos[1]])
            else:
                v = sc.number()
                pos = np.array([pos[0], pos[1] + v if rel else v])
            if not cur_pts:
                cur_pts = [T(start)]
            cur_pts.append(T(pos))
            last_ctrl = None
        elif C in "CS":
            if C == "C":
                c1 = base + [sc.number(), sc.number()]
            else:
                c1 = 2 * pos - last_ctrl if (last_ctrl is not None and last_cmd in "CS") else pos.copy()
            c2 = base + [sc.number(), sc.number()]
            p = base + [sc.number(), sc.number()]
            wp = g.apply_matrix(m, np.array([pos, c1, c2, p]))
            if not cur_pts:
                cur_pts = [T(pos)]
            cur_pts.append(g.flatten_cubic(*wp, tol=tol))
            last_ctrl, pos = c2, p
        elif C in "QT":
            if C == "Q":
                c1 = base + [sc.number(), sc.number()]
            else:
                c1 = 2 * pos - last_ctrl if (last_ctrl is not None and last_cmd in "QT") else pos.copy()
            p = base + [sc.number(), sc.number()]
            wp = g.apply_matrix(m, np.array([pos, c1, p]))
            if not cur_pts:
                cur_pts = [T(pos)]
            cur_pts.append(g.flatten_quad(*wp, tol=tol))
            last_ctrl, pos = c1, p
        elif C == "A":
            rx, ry, phi = sc.number(), sc.number(), sc.number()
            large, sweep = sc.flag(), sc.flag()
            p = base + [sc.number(), sc.number()]
            if not cur_pts:
                cur_pts = [T(pos)]
            cur_pts.append(_arc_to_points(pos, rx, ry, phi, large, sweep, p, m, tol))
            pos = p
            last_ctrl = None
        else:
            raise ValueError(f"Unbekannter Pfadbefehl '{c}'")
        last_cmd = C
    finish()
    return polys


# --------------------------------------------------------------------------- Elemente
def _rect_d(el) -> str:
    x, y = _num(el.get("x")), _num(el.get("y"))
    w, h = _num(el.get("width")), _num(el.get("height"))
    rx, ry = el.get("rx"), el.get("ry")
    rx = _num(rx) if rx is not None else None
    ry = _num(ry) if ry is not None else None
    if rx is None and ry is not None:
        rx = ry
    if ry is None and rx is not None:
        ry = rx
    if not rx:
        return f"M{x},{y} H{x + w} V{y + h} H{x} Z"
    rx, ry = min(rx, w / 2), min(ry, h / 2)
    return (f"M{x + rx},{y} H{x + w - rx} A{rx},{ry} 0 0 1 {x + w},{y + ry} V{y + h - ry} "
            f"A{rx},{ry} 0 0 1 {x + w - rx},{y + h} H{x + rx} A{rx},{ry} 0 0 1 {x},{y + h - ry} "
            f"V{y + ry} A{rx},{ry} 0 0 1 {x + rx},{y} Z")


def _num(s) -> float:
    if s is None:
        return 0.0
    m = re.match(_NUM, str(s).strip())
    return float(m.group(0)) if m else 0.0


def _element_polys(el, tag, m, tol) -> list[g.Polyline]:
    if tag == "path":
        return parse_path_d(el.get("d", ""), m, tol)
    if tag == "rect":
        if _num(el.get("width")) <= 0 or _num(el.get("height")) <= 0:
            return []
        return parse_path_d(_rect_d(el), m, tol)
    if tag in ("circle", "ellipse"):
        cx, cy = _num(el.get("cx")), _num(el.get("cy"))
        if tag == "circle":
            rx = ry = _num(el.get("r"))
        else:
            rx, ry = _num(el.get("rx")), _num(el.get("ry"))
        if rx <= 0 or ry <= 0:
            return []
        sf = g.matrix_scale_factor(m)
        pts = g.ellipse_arc_points(cx, cy, rx, ry, 0, 0, 2 * math.pi, tol / sf)[:-1]
        return [g.Polyline(g.apply_matrix(m, pts), True)]
    if tag == "line":
        pts = np.array([[_num(el.get("x1")), _num(el.get("y1"))], [_num(el.get("x2")), _num(el.get("y2"))]])
        return [g.Polyline(g.apply_matrix(m, pts), False)]
    if tag in ("polyline", "polygon"):
        nums = [float(v) for v in re.findall(_NUM, el.get("points", ""))]
        if len(nums) < 4:
            return []
        pts = np.array(nums[: len(nums) // 2 * 2]).reshape(-1, 2)
        return [g.Polyline(g.apply_matrix(m, pts), tag == "polygon")]
    return []


def import_svg(path: str, tol: float = g.CURVE_TOL) -> tuple[list[ImportedPath], list[str]]:
    """Liest eine SVG-Datei. Rückgabe: (Pfade in mm, Y nach oben), Warnungen."""
    tree = ET.parse(path)
    root = tree.getroot()
    warnings: list[str] = []

    vb = root.get("viewBox")
    w_mm = parse_length(root.get("width"))
    h_mm = parse_length(root.get("height"))
    if vb:
        vx, vy, vw, vh = [float(v) for v in re.findall(_NUM, vb)][:4]
        sx = (w_mm / vw) if w_mm else UNIT_MM["px"]
        sy = (h_mm / vh) if h_mm else (sx if not w_mm else sx)
        if w_mm and not h_mm:
            sy = sx
        if h_mm and not w_mm:
            sx = sy
        root_m = g.scale(sx, sy) @ g.translate(-vx, -vy)
        doc_h = vh * sy
    else:
        root_m = g.scale(UNIT_MM["px"])
        doc_h = h_mm or 0.0
    # SVG: Y nach unten -> Maschine: Y nach oben
    flip = g.translate(0, doc_h) @ g.scale(1, -1)
    root_m = flip @ root_m

    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    result: list[ImportedPath] = []
    skipped_text = [0]

    def walk(el, m, inherited: dict, depth=0):
        tag = _local(el.tag)
        if tag in SKIP_TAGS or depth > 50:
            return
        st = dict(inherited)
        st.update(parse_style(el))
        if st.get("display") == "none" or st.get("visibility") == "hidden":
            return
        m = m @ parse_transform(el.get("transform"))
        if tag in ("svg", "g", "a", "switch") and el is not root:
            if tag == "svg" and el is not root:
                m = m @ g.translate(_num(el.get("x")), _num(el.get("y")))
            for ch in el:
                walk(ch, m, st, depth + 1)
            return
        if el is root:
            for ch in el:
                walk(ch, m, st, depth + 1)
            return
        if tag == "use":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
            ref = ids.get(href.lstrip("#"))
            if ref is not None:
                mm = m @ g.translate(_num(el.get("x")), _num(el.get("y")))
                if _local(ref.tag) == "symbol":
                    for ch in ref:
                        walk(ch, mm, st, depth + 1)
                else:
                    walk(ref, mm, st, depth + 1)
            return
        if tag in ("text", "tspan", "flowRoot"):
            skipped_text[0] += 1
            return
        try:
            polys = _element_polys(el, tag, m, tol)
        except Exception as e:  # defekte Pfade nicht den ganzen Import abbrechen lassen
            warnings.append(f"Element <{tag} id={el.get('id')}> übersprungen: {e}")
            return
        if not polys:
            return
        stroke = parse_color(st.get("stroke"))
        fill = parse_color(st.get("fill"))
        if fill is None:
            fill = "#000000"           # SVG-Standard: schwarz gefüllt
        has_stroke = stroke not in (None, "none")
        has_fill = fill != "none"
        if not has_stroke and not has_fill:
            return
        if has_stroke:
            color, filled = stroke, False
        else:
            color, filled = fill, True
        for p in polys:
            if filled and not p.closed and len(p.pts) > 2:
                p = g.Polyline(p.pts, True)     # Füllungen sind implizit geschlossen
            result.append(ImportedPath(p, color, color, filled))

    walk(root, root_m, {})
    if skipped_text[0]:
        warnings.append(f"{skipped_text[0]} Textelement(e) ignoriert – in Inkscape 'Objekt in Pfad umwandeln' nutzen "
                        f"oder Text direkt in TiLaser einfügen.")
    return result, warnings

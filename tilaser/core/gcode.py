"""Maschinenprofile und G-Code-Erzeugung für GRBL, Marlin, Smoothieware und generische Laser."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from . import geometry as g
from .fill import hatch
from .model import Layer, Project
from .offset import add_tabs, apply_kerf
from .raster import dither, resample

DIALECTS = {
    "grbl": "GRBL 1.1 / FluidNC (M4 dynamisch, S inline)",
    "marlin": "Marlin (M3/M5 je Pfad)",
    "smoothie": "Smoothieware (S 0–1 inline)",
    "generic": "Generisch (M3/M5 je Pfad)",
}

DIALECT_DEFAULTS = {
    "grbl": dict(s_max=1000.0, laser_on="M4", inline_power=True, rapid_with_feed=False),
    "marlin": dict(s_max=255.0, laser_on="M3", inline_power=False, rapid_with_feed=True),
    "smoothie": dict(s_max=1.0, laser_on="", inline_power=True, rapid_with_feed=True),
    "generic": dict(s_max=255.0, laser_on="M3", inline_power=False, rapid_with_feed=True),
}


@dataclass
class MachineProfile:
    name: str = "Mein Laser"
    dialect: str = "grbl"
    s_max: float = 1000.0             # S-Wert bei 100 % (GRBL: $30)
    laser_on: str = "M4"              # M4 = dynamisch (GRBL), M3 = konstant
    inline_power: bool = True         # S-Wert direkt in G1-Zeilen
    rapid_with_feed: bool = False     # G0 mit F (Marlin)
    travel_speed: float = 6000.0      # mm/min
    passes_per_path: bool = True      # Schneiden: alle Durchgänge einer Kontur, dann die nächste
    frame_rubber: bool = False        # Rahmen als Gummiband (konvexe Hülle) statt Rechteck
    remove_overlaps: bool = True      # deckungsgleiche Linien einer Ebene nur einmal schneiden
    frame_rubber_show: bool = False   # Gummiband-Linie dauerhaft auf der Arbeitsfläche zeigen (nur Anzeige)
    bed_w: float = 400.0
    bed_h: float = 400.0
    header: str = "G21\nG90"
    footer: str = "G0 X0 Y0"
    air_on: str = "M8"
    air_off: str = "M9"
    frame_power: float = 0.0          # % – 0 = Laser aus beim Framing
    frame_speed: float = 3000.0
    port: str = ""
    baud: int = 115200
    # Rotationsachse (Rollen-Typ, an Stelle der Y-Achse)
    rotary: bool = False
    rotary_mm_per_rev: float = 40.0   # Y-Weg in mm, den die Steuerung für eine Rollenumdrehung fährt
    rotary_roller_d: float = 20.0     # Rollendurchmesser in mm
    # Netzwerk (TCP/Telnet), z. B. ESP32/FluidNC
    conn_type: str = "serial"         # 'serial' | 'tcp'
    host: str = ""
    tcp_port: int = 23
    # Kamera
    camera_device: str = ""           # Beschreibung des Geräts oder '__file__'
    camera_h: list = field(default_factory=list)   # 3×3-Homographie Kamerapixel -> Bett-mm (9 Werte)
    camera_inset: float = 30.0        # Abstand der Passmarken vom Bettrand
    camera_opacity: float = 0.75
    camera_k1: float = 0.0            # radiale Verzeichnung (0 = keine Korrektur)
    camera_count: int = 4             # Anzahl Passmarken (4 oder 9)
    camera_live_auto: bool = False    # Live-Kamera bei Auftragsstart einschalten
    camera_live_record: bool = False  # jeden Auftrag als Video aufnehmen
    camera_live_bg: bool = False      # entzerrtes Live-Bild als Hintergrund
    camera_live_rot: int = 0          # Drehung der Live-Anzeige (0/90/180/270)
    camera_media_dir: str = ""        # Ordner für Fotos/Videos ('' = Bilder/TiLaser)

    @property
    def y_scale(self) -> float:
        if self.rotary and self.rotary_roller_d > 0:
            return self.rotary_mm_per_rev / (math.pi * self.rotary_roller_d)
        return 1.0

    def apply_dialect(self, dialect: str):
        self.dialect = dialect
        for k, v in DIALECT_DEFAULTS.get(dialect, {}).items():
            setattr(self, k, v)

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class GResult:
    lines: list[str]
    travel: np.ndarray                           # (k, 2, 2)
    burn: list[tuple[str, np.ndarray]]           # [(Farbe, (k,2,2))]
    time_s: float
    warnings: list[str] = field(default_factory=list)
    bbox: tuple | None = None
    moves: np.ndarray | None = None              # (N, 6): x0, y0, x1, y1, Farbindex (-1 = Leerweg), t_ende [s]
    colors: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def fmt(v: float, decimals: int = 3) -> str:
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class GWriter:
    def __init__(self, prof: MachineProfile):
        self.p = prof
        self.lines: list[str] = []
        self.x: float | None = None
        self.y: float | None = None
        self.feed: float | None = None
        self.s: float | None = None
        self.laser_on = False
        self.travel_segs: list = []
        self.burn_segs: dict[str, list] = {}
        self.color = "#000000"
        self.t_min = 0.0
        self.ys = prof.y_scale
        self.moves: list = []
        self.color_ids: dict[str, int] = {}
        self.s_decimals = 3 if prof.s_max <= 1.0 else (1 if prof.s_max < 100 else 0)

    def s_val(self, pct: float) -> float:
        return round(max(0.0, min(100.0, pct)) / 100.0 * self.p.s_max, self.s_decimals)

    def emit(self, line: str):
        self.lines.append(line)

    def comment(self, text: str):
        self.lines.append(f"; {text}")

    def _dist(self, x, y):
        if self.x is None:
            return 0.0
        return math.hypot(x - self.x, y - self.y)

    def laser_off(self):
        if self.laser_on and not self.p.inline_power:
            self.emit("M5")
            self.laser_on = False
            self.s = None

    def travel(self, x: float, y: float):
        if self.x is not None and abs(x - self.x) < 1e-6 and abs(y - self.y) < 1e-6:
            return
        self.laser_off()
        d = self._dist(x, y)
        cmd = f"G0 X{fmt(x)} Y{fmt(y * self.ys)}"
        if self.p.rapid_with_feed:
            cmd += f" F{fmt(self.p.travel_speed, 0)}"
            self.feed = None          # G0 F verändert das modale F – nächstes G1 muss F setzen
        self.emit(cmd)
        if self.x is not None:
            self.travel_segs.append((self.x, self.y, x, y))
        self.t_min += d / max(self.p.travel_speed, 1)
        if self.x is not None:
            self.moves.append((self.x, self.y, x, y, -1, self.t_min * 60))
        self.x, self.y = x, y

    def burn(self, x: float, y: float, speed: float, s: float):
        """Bewegung mit Laserleistung s (S-Einheiten). s == 0 -> Bewegung ohne Brennen."""
        if self.x is not None and abs(x - self.x) < 1e-6 and abs(y - self.y) < 1e-6:
            return
        if s <= 0 and not self.p.inline_power:
            self.travel(x, y)
            return
        d = self._dist(x, y)
        parts = [f"G1 X{fmt(x)} Y{fmt(y * self.ys)}"]
        if self.feed != speed:
            parts.append(f"F{fmt(speed, 0)}")
            self.feed = speed
        if self.p.inline_power:
            if self.s != s:
                parts.append(f"S{fmt(s, self.s_decimals)}")
                self.s = s
        else:
            if not self.laser_on or self.s != s:
                self.emit(f"{self.p.laser_on} S{fmt(s, self.s_decimals)}".strip())
                self.laser_on = True
                self.s = s
        self.emit(" ".join(parts))
        if s > 0 and self.x is not None:
            self.burn_segs.setdefault(self.color, []).append((self.x, self.y, x, y))
        elif self.x is not None:
            self.travel_segs.append((self.x, self.y, x, y))
        self.t_min += d / max(speed, 1)
        if self.x is not None:
            ci = self.color_ids.setdefault(self.color, len(self.color_ids)) if s > 0 else -1
            self.moves.append((self.x, self.y, x, y, ci, self.t_min * 60))
        self.x, self.y = x, y

    def polyline(self, pts: np.ndarray, speed: float, s: float):
        self.travel(*pts[0])
        for x, y in pts[1:]:
            self.burn(x, y, speed, s)

    def start(self):
        self.comment(f"TiLaser – {self.p.name} ({self.p.dialect}) – {time.strftime('%Y-%m-%d %H:%M')}")
        for l in self.p.header.splitlines():
            if l.strip():
                self.emit(l.strip())
        if self.p.inline_power and self.p.laser_on:
            self.emit(f"{self.p.laser_on} S0")
            self.s = 0.0
            self.laser_on = True

    def finish(self):
        if self.p.inline_power and self.p.laser_on or self.laser_on:
            self.emit("M5")
            self.laser_on = False
        for l in self.p.footer.splitlines():
            if l.strip():
                self.emit(l.strip())

    def result(self, warnings, bbox) -> GResult:
        tr = np.array(self.travel_segs, dtype=float).reshape(-1, 2, 2)
        burn = [(c, np.array(s, dtype=float).reshape(-1, 2, 2)) for c, s in self.burn_segs.items()]
        mv = np.array(self.moves, dtype=float).reshape(-1, 6)
        colors = [c for c, _ in sorted(self.color_ids.items(), key=lambda kv: kv[1])]
        return GResult(self.lines, tr, burn, self.t_min * 60.0, warnings, bbox, mv, colors)


# --------------------------------------------------------------------------- Generator
def _z_between(w: GWriter, op: Layer, n: int):
    """Nach Durchgang n (0-basiert) Fokus um z_step absenken (relativ)."""
    if op.z_step > 0 and n < op.passes - 1:
        w.emit("G91")
        w.emit(f"G0 Z{fmt(-op.z_step)}")
        w.emit("G90")


def _z_restore(w: GWriter, op: Layer):
    if op.z_step > 0 and op.passes > 1:
        w.emit("G91")
        w.emit(f"G0 Z{fmt(op.z_step * (op.passes - 1))}")
        w.emit("G90")


def _layer_cut(w: GWriter, layer: Layer, paths: list[g.Polyline]):
    if layer.kerf:
        paths = apply_kerf(paths, layer.kerf)
    if layer.tabs > 0:
        paths = add_tabs(paths, layer.tabs, layer.tab_width, layer.tabs_outer_only)
    if w.p.remove_overlaps and len(paths) > 1:
        # Gemeinsame Kanten nur einmal schneiden – nur in der Ausgabe, das Projekt bleibt unverändert
        from .overlap import remove_overlaps
        paths, saved = remove_overlaps(paths)
        if saved > 0.01:
            w.comment(f"Doppelte Linien ausgelassen: {saved:.1f} mm")
    s = w.s_val(layer.power)
    passes = max(1, layer.passes)
    if passes > 1 and w.p.passes_per_path:
        # Jede Kontur mit allen Durchgängen fertig schneiden, erst dann zur nächsten
        w.comment(f"{passes} Durchgänge je Kontur")
        start = (w.x or 0.0, w.y or 0.0)
        for p in g.order_paths(paths, start, layer.inner_first):
            pts = p.loop_pts()
            for n in range(passes):
                # offene Pfade abwechselnd vor und zurück, damit keine Leerfahrt zum Anfang nötig ist
                w.polyline(pts if p.closed or n % 2 == 0 else pts[::-1], layer.speed, s)
                _z_between(w, layer, n)
            _z_restore(w, layer)
        return
    for n in range(passes):
        if passes > 1:
            w.comment(f"Durchgang {n + 1}/{passes}")
        start = (w.x or 0.0, w.y or 0.0)
        for p in g.order_paths(paths, start, layer.inner_first):
            w.polyline(p.loop_pts(), layer.speed, s)
        _z_between(w, layer, n)
    _z_restore(w, layer)


def _layer_fill(w: GWriter, layer: Layer, paths: list[g.Polyline], warnings: list[str]):
    open_count = sum(1 for p in paths if not p.closed)
    if open_count:
        warnings.append(f"Ebene '{layer.name}': {open_count} offene Pfade können nicht gefüllt werden.")
    segs = hatch(paths, layer.interval, layer.angle, layer.bidirectional)
    if layer.crosshatch:
        segs += hatch(paths, layer.interval, layer.angle + 90, layer.bidirectional)
    s = w.s_val(layer.power)
    for n in range(max(1, layer.passes)):
        for seg in segs:
            w.travel(*seg[0])
            w.burn(*seg[1], layer.speed, s)
        _z_between(w, layer, n)
    _z_restore(w, layer)


def _layer_raster(w: GWriter, layer: Layer, item, warnings: list[str]):
    iv = max(0.01, layer.interval)
    img = resample(item.image, item.img_w * item.scale, item.img_h * item.scale, iv,
                   item.rotation, item.mirror_x)
    inten = dither(img, layer.dither, layer.threshold, layer.invert, layer.brightness, layer.contrast,
                   layer.gamma)
    rows, cols = inten.shape
    W, H = cols * iv, rows * iv
    x0 = item.x - W / 2
    ytop = item.y + H / 2
    pmin, pmax = layer.min_power, layer.power
    if layer.dither == "grayscale":
        pct = np.where(inten > 0, pmin + inten * (pmax - pmin), 0.0)
        pct = np.round(pct * 2.55) / 2.55      # 256 Stufen
    else:
        pct = inten * pmax
    smax = w.p.s_max
    S = np.round(pct / 100.0 * smax, w.s_decimals)
    if not w.p.inline_power:
        warnings.append(f"Ebene '{layer.name}': Rastergravur ohne Inline-Leistung (M3/M5 je Abschnitt) ist langsam.")
    ov = max(0.0, layer.overscan)
    forward = True
    for n_pass in range(max(1, layer.passes)):
        if n_pass:
            _z_between(w, layer, n_pass - 1)
        for r in range(rows):
            row = S[r]
            nz = np.flatnonzero(row > 0)
            if not len(nz):
                continue
            y = ytop - (r + 0.5) * iv
            first, last = int(nz[0]), int(nz[-1])
            seg = row[first:last + 1]
            # Läufe gleicher Leistung
            change = np.flatnonzero(np.diff(seg) != 0) + 1
            starts = np.concatenate([[0], change])
            ends = np.concatenate([change, [len(seg)]])
            runs = [(first + a, first + b, float(seg[a])) for a, b in zip(starts, ends)]
            if forward or not layer.bidirectional:
                xs = x0 + first * iv
                w.travel(xs - ov, y)
                w.burn(xs, y, layer.speed, 0.0)
                for a, b, sv in runs:
                    w.burn(x0 + b * iv, y, layer.speed, sv)
                w.burn(x0 + (last + 1) * iv + ov, y, layer.speed, 0.0)
            else:
                xe = x0 + (last + 1) * iv
                w.travel(xe + ov, y)
                w.burn(xe, y, layer.speed, 0.0)
                for a, b, sv in reversed(runs):
                    w.burn(x0 + a * iv, y, layer.speed, sv)
                w.burn(x0 + first * iv - ov, y, layer.speed, 0.0)
            if layer.bidirectional:
                forward = not forward
    _z_restore(w, layer)


def job_project(project: Project, only_ids=None, offset=(0.0, 0.0)) -> Project:
    """Projektkopie für die Ausgabe: optional nur ausgewählte Objekte, optional verschoben."""
    if not only_ids and not any(offset):
        return project
    from .editing import snapshot
    p = snapshot(project)
    if only_ids:
        p.items = [it for it in p.items if it.id in set(only_ids)]
    for it in p.items:
        it.x += offset[0]
        it.y += offset[1]
    return p


ANCHORS = {"tl": (0, 1), "tc": (0.5, 1), "tr": (1, 1), "ml": (0, 0.5), "mc": (0.5, 0.5), "mr": (1, 0.5),
           "bl": (0, 0), "bc": (0.5, 0), "br": (1, 0)}


def origin_offset(bbox, anchor: str, target) -> tuple[float, float]:
    """Versatz, der den Ankerpunkt der Auftrags-Box auf 'target' (z. B. aktuelle Kopfposition) legt."""
    fx, fy = ANCHORS.get(anchor, (0, 0))
    ax = bbox[0] + fx * (bbox[2] - bbox[0])
    ay = bbox[1] + fy * (bbox[3] - bbox[1])
    return target[0] - ax, target[1] - ay


def generate(project: Project, prof: MachineProfile, only_ids=None, offset=(0.0, 0.0)) -> GResult:
    project = job_project(project, only_ids, offset)
    w = GWriter(prof)
    warnings: list[str] = []
    w.start()
    any_output = False
    for layer in project.layers:
        if not layer.output:
            continue
        paths: list[g.Polyline] = []
        rasters = []
        for it in project.items:
            if it.kind == "raster" and it.layer_id == layer.id:
                rasters.append(it)
            elif it.kind == "vector":
                paths += it.world_paths(layer.id)
        if not paths and not rasters:
            continue
        any_output = True
        w.color = layer.color
        w.comment(f"Ebene: {layer.name} | {layer.mode} | {layer.power:g}% | {layer.speed:g} mm/min | {layer.passes}x")
        if layer.air and prof.air_on:
            w.emit(prof.air_on)
        ops = [layer] + [sub for sub in layer.subs if sub.output]
        for k, op in enumerate(ops):
            if k:
                w.laser_off()
                w.comment(f"Unterebene {k}: {op.mode} | {op.power:g}% | {op.speed:g} mm/min | {op.passes}x")
            if op.mode == "cut":
                _layer_cut(w, op, paths)
            elif op.mode == "fill":
                _layer_fill(w, op, paths, warnings)
            elif op.mode == "raster":
                for it in rasters:
                    _layer_raster(w, op, it, warnings)
                if paths:
                    _layer_fill(w, op, paths, warnings)
            if k == 0 and op.mode != "raster" and rasters:
                for it in rasters:
                    _layer_raster(w, op, it, warnings)
        w.laser_off()
        if layer.air and prof.air_off:
            w.emit(prof.air_off)
    w.finish()
    if prof.rotary:
        warnings.append(f"Rotationsachse aktiv: Y wird mit Faktor {prof.y_scale:.4f} skaliert.")
    if not any_output:
        warnings.append("Keine aktiven Ebenen mit Objekten – G-Code enthält keine Bearbeitung.")
    bb = project.bbox()
    if bb and not prof.rotary and (bb[0] < -1e-6 or bb[1] < -1e-6 or bb[2] > prof.bed_w + 1e-6 or bb[3] > prof.bed_h + 1e-6):
        warnings.append(f"Auftrag liegt teilweise außerhalb des Arbeitsbereichs (0–{prof.bed_w:g} × 0–{prof.bed_h:g} mm).")
    return w.result(warnings, bb)


def convex_hull(points) -> list[tuple[float, float]]:
    """Konvexe Hülle (Monotone Chain), gegen den Uhrzeigersinn, beginnend links unten."""
    pts = sorted({(round(float(x), 4), round(float(y), 4)) for x, y in np.asarray(points, float)})
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def frame_gcode(bbox, prof: MachineProfile, hull=None) -> list[str]:
    """Umriss abfahren – mit Laser aus oder sehr geringer Leistung.

    Standard ist das umschließende Rechteck (``bbox``). Mit ``hull`` (Punkte der konvexen Hülle,
    „Gummiband“) fährt der Kopf die Form eng umschließend ab – wie ein um die Teile gespanntes Gummiband."""
    ys = prof.y_scale
    if hull is not None and len(hull) >= 3:
        corners = [(float(x), float(y) * ys) for x, y in hull]
        corners.append(corners[0])
        x0, y0 = corners[0]
    else:
        x0, y0, x1, y1 = bbox
        y0, y1 = y0 * ys, y1 * ys
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    lines = ["G21", "G90", "M5"]
    lines.append(f"G0 X{fmt(x0)} Y{fmt(y0)}" + (f" F{fmt(prof.travel_speed, 0)}" if prof.rapid_with_feed else ""))
    if prof.frame_power > 0:
        s = round(prof.frame_power / 100 * prof.s_max, 3)
        if prof.dialect == "smoothie":
            for x, y in corners[1:]:
                lines.append(f"G1 X{fmt(x)} Y{fmt(y)} F{fmt(prof.frame_speed, 0)} S{fmt(s)}")
        else:
            lines.append(f"M3 S{fmt(s)}")     # M3 = konstante Leistung, auch im Stillstand sichtbar
            for x, y in corners[1:]:
                lines.append(f"G1 X{fmt(x)} Y{fmt(y)} F{fmt(prof.frame_speed, 0)}")
            lines.append("M5")
    else:
        for x, y in corners[1:]:
            lines.append(f"G1 X{fmt(x)} Y{fmt(y)} F{fmt(prof.frame_speed, 0)}")
    return lines


# --------------------------------------------------------------------------- Fortsetzen
def resume_lines(lines: list[str], start: int, prof: MachineProfile) -> list[str]:
    """G-Code ab Zeile 'start' (Index in der bereinigten Zeilenliste) fortsetzen.

    Stellt den modalen Zustand (Position, Vorschub, Laserleistung, Air Assist) wieder her
    und fährt mit ausgeschaltetem Laser zur letzten Position vor 'start'.
    """
    import re
    clean = []
    for l in lines:
        c = re.sub(r"\([^)]*\)", "", l.split(";", 1)[0]).strip()
        if c:
            clean.append(c)
    start = max(0, min(start, len(clean)))
    x = y = None
    f = s = None
    laser_cmd = None
    air = False
    for c in clean[:start]:
        words = dict(re.findall(r"([A-Z])([-+]?\d*\.?\d+)", c.upper()))
        if "X" in words:
            x = float(words["X"])
        if "Y" in words:
            y = float(words["Y"])
        if "F" in words and (c.startswith("G1") or c.startswith("G0") and prof.rapid_with_feed):
            f = float(words["F"])
        if "S" in words:
            s = float(words["S"])
        if c.startswith(("M3", "M4")):
            laser_cmd = c.split()[0]
        elif c.startswith("M5"):
            laser_cmd = None if not prof.inline_power else laser_cmd
        if c.startswith("M8"):
            air = True
        elif c.startswith("M9"):
            air = False
    out = [f"; Fortsetzen ab Zeile {start + 1} von {len(clean)}", "G21", "G90", "M5"]
    if air and prof.air_on:
        out.append(prof.air_on)
    if x is not None and y is not None:
        out.append(f"G0 X{fmt(x)} Y{fmt(y)}" + (f" F{fmt(prof.travel_speed, 0)}" if prof.rapid_with_feed else ""))
    if f:
        out.append(f"G1 F{fmt(f, 0)}")
    if laser_cmd:
        out.append(f"{laser_cmd} S{fmt(s or 0)}")
    elif prof.inline_power and prof.laser_on:
        out.append(f"{prof.laser_on} S{fmt(s or 0)}")
    return out + clean[start:]

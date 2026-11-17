"""Maschinenprofile und G-Code-Erzeugung für GRBL, Marlin, Smoothieware und generische Laser."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from . import geometry as g
from .fill import hatch
from .model import Layer, Project
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
        cmd = f"G0 X{fmt(x)} Y{fmt(y)}"
        if self.p.rapid_with_feed:
            cmd += f" F{fmt(self.p.travel_speed, 0)}"
            self.feed = None          # G0 F verändert das modale F – nächstes G1 muss F setzen
        self.emit(cmd)
        if self.x is not None:
            self.travel_segs.append((self.x, self.y, x, y))
        self.t_min += d / max(self.p.travel_speed, 1)
        self.x, self.y = x, y

    def burn(self, x: float, y: float, speed: float, s: float):
        """Bewegung mit Laserleistung s (S-Einheiten). s == 0 -> Bewegung ohne Brennen."""
        if self.x is not None and abs(x - self.x) < 1e-6 and abs(y - self.y) < 1e-6:
            return
        if s <= 0 and not self.p.inline_power:
            self.travel(x, y)
            return
        d = self._dist(x, y)
        parts = [f"G1 X{fmt(x)} Y{fmt(y)}"]
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
        return GResult(self.lines, tr, burn, self.t_min * 60.0, warnings, bbox)


# --------------------------------------------------------------------------- Generator
def _layer_cut(w: GWriter, layer: Layer, paths: list[g.Polyline]):
    s = w.s_val(layer.power)
    for n in range(max(1, layer.passes)):
        if layer.passes > 1:
            w.comment(f"Durchgang {n + 1}/{layer.passes}")
        start = (w.x or 0.0, w.y or 0.0)
        for p in g.order_paths(paths, start, layer.inner_first):
            w.polyline(p.loop_pts(), layer.speed, s)


def _layer_fill(w: GWriter, layer: Layer, paths: list[g.Polyline], warnings: list[str]):
    open_count = sum(1 for p in paths if not p.closed)
    if open_count:
        warnings.append(f"Ebene '{layer.name}': {open_count} offene Pfade können nicht gefüllt werden.")
    segs = hatch(paths, layer.interval, layer.angle, layer.bidirectional)
    if layer.crosshatch:
        segs += hatch(paths, layer.interval, layer.angle + 90, layer.bidirectional)
    s = w.s_val(layer.power)
    for _ in range(max(1, layer.passes)):
        for seg in segs:
            w.travel(*seg[0])
            w.burn(*seg[1], layer.speed, s)


def _layer_raster(w: GWriter, layer: Layer, item, warnings: list[str]):
    iv = max(0.01, layer.interval)
    img = resample(item.image, item.img_w * item.scale, item.img_h * item.scale, iv,
                   item.rotation, item.mirror_x)
    inten = dither(img, layer.dither, layer.threshold, layer.invert)
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
    for _ in range(max(1, layer.passes)):
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


def generate(project: Project, prof: MachineProfile) -> GResult:
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
        if layer.mode == "cut":
            _layer_cut(w, layer, paths)
        elif layer.mode == "fill":
            _layer_fill(w, layer, paths, warnings)
        elif layer.mode == "raster":
            for it in rasters:
                _layer_raster(w, layer, it, warnings)
            if paths:
                _layer_fill(w, layer, paths, warnings)
        if layer.mode != "raster" and rasters:
            for it in rasters:
                _layer_raster(w, layer, it, warnings)
        w.laser_off()
        if layer.air and prof.air_off:
            w.emit(prof.air_off)
    w.finish()
    if not any_output:
        warnings.append("Keine aktiven Ebenen mit Objekten – G-Code enthält keine Bearbeitung.")
    bb = project.bbox()
    if bb and (bb[0] < -1e-6 or bb[1] < -1e-6 or bb[2] > prof.bed_w + 1e-6 or bb[3] > prof.bed_h + 1e-6):
        warnings.append(f"Auftrag liegt teilweise außerhalb des Arbeitsbereichs (0–{prof.bed_w:g} × 0–{prof.bed_h:g} mm).")
    return w.result(warnings, bb)


def frame_gcode(bbox, prof: MachineProfile) -> list[str]:
    """Umriss (Bounding Box) abfahren – mit Laser aus oder sehr geringer Leistung."""
    x0, y0, x1, y1 = bbox
    lines = ["G21", "G90"]
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
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

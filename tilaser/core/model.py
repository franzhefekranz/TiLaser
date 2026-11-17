"""Datenmodell: Ebenen (Layer), Objekte (Items) und Projekt."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np

from . import geometry as g

MODES = ("cut", "fill", "raster")
MODE_LABELS = {"cut": "Schneiden / Linie", "fill": "Füllen (Schraffur)", "raster": "Rasterbild"}
DITHER_MODES = ("floyd", "threshold", "grayscale", "atkinson")
DITHER_LABELS = {"floyd": "Floyd-Steinberg", "threshold": "Schwellwert",
                 "grayscale": "Graustufen (Leistung)", "atkinson": "Atkinson"}

PALETTE = ["#000000", "#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa",
           "#00acc1", "#6d4c41", "#d81b60", "#7cb342", "#3949ab", "#fdd835"]

def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@dataclass
class Layer:
    id: str
    name: str
    color: str = "#000000"
    mode: str = "cut"
    power: float = 100.0          # % der Maximalleistung
    min_power: float = 0.0        # % (nur Graustufen-Raster)
    speed: float = 1000.0         # mm/min
    passes: int = 1
    interval: float = 0.1         # mm Zeilenabstand (Füllen/Raster)
    angle: float = 0.0            # Grad (Füllen)
    crosshatch: bool = False      # zweite Füllung um 90° gedreht
    dither: str = "floyd"
    threshold: int = 128
    invert: bool = False
    overscan: float = 2.0         # mm
    bidirectional: bool = True
    inner_first: bool = True
    air: bool = False
    output: bool = True

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class VectorPath:
    poly: g.Polyline
    layer_id: str


@dataclass
class ImportedPath:
    """Ergebnis eines Importers, bevor es einem Projekt zugeordnet wird."""
    poly: g.Polyline
    key: str            # Gruppierungsschlüssel (Farbe oder DXF-Layername)
    color: str          # Anzeigefarbe '#rrggbb'
    filled: bool = False


@dataclass
class Item:
    id: str
    name: str
    kind: str = "vector"             # 'vector' | 'raster'
    paths: list = field(default_factory=list)   # list[VectorPath], lokal um (0,0) zentriert
    image: np.ndarray | None = None  # uint8 Graustufen, 255 = weiß
    img_w: float = 0.0               # mm
    img_h: float = 0.0
    layer_id: str | None = None      # für Rasterbilder
    x: float = 0.0                   # Mittelpunkt in Maschinenkoordinaten (mm)
    y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0            # Grad, gegen Uhrzeigersinn
    mirror_x: bool = False

    def matrix(self) -> np.ndarray:
        m = g.translate(self.x, self.y) @ g.rotate(self.rotation) @ g.scale(self.scale)
        if self.mirror_x:
            m = m @ g.scale(-1, 1)
        return m

    def local_size(self) -> tuple[float, float]:
        if self.kind == "raster":
            return self.img_w, self.img_h
        bb = g.bbox([p.poly for p in self.paths])
        if bb is None:
            return 0.0, 0.0
        return bb[2] - bb[0], bb[3] - bb[1]

    def local_corners(self) -> np.ndarray:
        if self.kind == "raster":
            w, h = self.img_w / 2, self.img_h / 2
            return np.array([[-w, -h], [w, -h], [w, h], [-w, h]])
        bb = g.bbox([p.poly for p in self.paths]) or (0, 0, 0, 0)
        return np.array([[bb[0], bb[1]], [bb[2], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]]])

    def world_bbox(self):
        c = g.apply_matrix(self.matrix(), self.local_corners())
        return float(c[:, 0].min()), float(c[:, 1].min()), float(c[:, 0].max()), float(c[:, 1].max())

    def world_paths(self, layer_id: str | None = None) -> list[g.Polyline]:
        m = self.matrix()
        return [vp.poly.transformed(m) for vp in self.paths if layer_id is None or vp.layer_id == layer_id]

    def layer_ids(self) -> set[str]:
        if self.kind == "raster":
            return {self.layer_id} if self.layer_id else set()
        return {vp.layer_id for vp in self.paths}


class Project:
    def __init__(self):
        self.layers: list[Layer] = []
        self.items: list[Item] = []
        self._key_to_layer: dict[str, str] = {}

    # ---------------------------------------------------------------- Ebenen
    def layer(self, layer_id: str) -> Layer | None:
        for l in self.layers:
            if l.id == layer_id:
                return l
        return None

    def add_layer(self, name: str, color: str | None = None, mode: str = "cut") -> Layer:
        if color is None:
            used = {l.color for l in self.layers}
            color = next((c for c in PALETTE if c not in used), PALETTE[len(self.layers) % len(PALETTE)])
        layer = Layer(id=new_id("L"), name=name, color=color, mode=mode)
        if mode == "fill":
            layer.speed, layer.power, layer.interval = 3000.0, 40.0, 0.1
        elif mode == "raster":
            layer.speed, layer.power, layer.interval = 3000.0, 50.0, 0.1
        if mode in ("fill", "raster"):     # Gravuren vor Schnitten einsortieren
            idx = next((i for i, l in enumerate(self.layers) if l.mode == "cut"), len(self.layers))
            self.layers.insert(idx, layer)
        else:
            self.layers.append(layer)
        return layer

    def layer_for_key(self, key: str, color: str, mode: str) -> Layer:
        lid = self._key_to_layer.get(key)
        if lid and self.layer(lid):
            return self.layer(lid)
        if key.startswith("#"):          # Farbschlüssel (SVG): gleiche Farbe + Modus wiederverwenden
            for l in self.layers:
                if l.color.lower() == color.lower() and l.mode == mode:
                    self._key_to_layer[key] = l.id
                    return l
        elif any(l.color.lower() == color.lower() for l in self.layers):
            color = None                 # benannte Ebene (DXF): eigene Farbe vergeben
        base = key.split("#fill")[0]
        name = base.split(":", 1)[-1] if not base.startswith("#") else f"{MODE_LABELS[mode].split()[0]} {color}"
        l = self.add_layer(name, color, mode)
        self._key_to_layer[key] = l.id
        return l

    def remove_unused_layers(self):
        used = set().union(*[it.layer_ids() for it in self.items]) if self.items else set()
        self.layers = [l for l in self.layers if l.id in used]

    def move_layer(self, layer_id: str, delta: int):
        i = next(i for i, l in enumerate(self.layers) if l.id == layer_id)
        j = max(0, min(len(self.layers) - 1, i + delta))
        self.layers.insert(j, self.layers.pop(i))

    # ---------------------------------------------------------------- Objekte
    def add_vector_import(self, name: str, imported: list[ImportedPath], center_at=None) -> Item | None:
        imported = [ip for ip in imported if len(ip.poly.pts) >= 2]
        if not imported:
            return None
        bb = g.bbox([ip.poly for ip in imported])
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        item = Item(id=new_id("I"), name=name, kind="vector")
        for ip in imported:
            mode = "fill" if ip.filled else "cut"
            layer = self.layer_for_key(ip.key + ("#fill" if ip.filled else ""), ip.color, mode)
            local = g.Polyline(ip.poly.pts - [cx, cy], ip.poly.closed)
            item.paths.append(VectorPath(local, layer.id))
        if center_at is None:
            item.x, item.y = cx, cy
        else:
            item.x, item.y = center_at
        self.items.append(item)
        return item

    def add_raster(self, name: str, gray: np.ndarray, w_mm: float, h_mm: float, center_at=(50, 50)) -> Item:
        layer = next((l for l in self.layers if l.mode == "raster"), None) or self.add_layer("Bild", mode="raster")
        item = Item(id=new_id("I"), name=name, kind="raster", image=gray, img_w=w_mm, img_h=h_mm,
                    layer_id=layer.id, x=center_at[0], y=center_at[1])
        self.items.append(item)
        return item

    def remove_item(self, item_id: str):
        self.items = [it for it in self.items if it.id != item_id]

    def item(self, item_id: str) -> Item | None:
        return next((it for it in self.items if it.id == item_id), None)

    def bbox(self, only_output=True):
        boxes = []
        for it in self.items:
            if only_output and not any((self.layer(l) and self.layer(l).output) for l in it.layer_ids()):
                continue
            boxes.append(it.world_bbox())
        if not boxes:
            return None
        b = np.array(boxes)
        return float(b[:, 0].min()), float(b[:, 1].min()), float(b[:, 2].max()), float(b[:, 3].max())

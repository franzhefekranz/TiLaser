"""Datenmodell: Ebenen (Layer), Objekte (Items) und Projekt."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np

from . import geometry as g

MODES = ("cut", "fill", "raster")
MODE_LABELS = {"cut": "Schneiden / Linie", "fill": "Füllen (Schraffur)", "raster": "Rasterbild"}
DITHER_MODES = ("floyd", "jarvis", "stucki", "atkinson", "threshold", "grayscale")
DITHER_LABELS = {"floyd": "Floyd-Steinberg", "jarvis": "Jarvis", "stucki": "Stucki", "atkinson": "Atkinson",
                 "threshold": "Schwellwert", "grayscale": "Graustufen (Leistung)"}

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
    # Schneiden
    kerf: float = 0.0             # mm Schnittbreite (Ausgleich: außen +k/2, Löcher -k/2)
    tabs: int = 0                 # Haltestege je Kontur
    tab_width: float = 0.5        # mm
    tabs_outer_only: bool = True
    # Bild
    brightness: float = 0.0       # -100..100
    contrast: float = 0.0         # -100..100
    gamma: float = 1.0
    z_step: float = 0.0           # mm Absenkung je Durchgang (0 = aus)
    subs: list = field(default_factory=list)   # Unterebenen: weitere Arbeitsgänge (Layer) auf derselben Geometrie

    def to_dict(self):
        d = dict(self.__dict__)
        d["subs"] = [s.to_dict() for s in self.subs]
        return d

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known["subs"] = [cls.from_dict(x) for x in d.get("subs", [])]
        return cls(**known)

    def add_sub(self, mode: str = "cut") -> "Layer":
        sub = Layer(id=new_id("S"), name=f"{self.name} · {len(self.subs) + 1}", color=self.color, mode=mode)
        if mode == "fill":
            sub.speed, sub.power = 3000.0, 40.0
        self.subs.append(sub)
        return sub


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
            local = ip.poly.transformed(g.translate(-cx, -cy))
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

    # ---------------------------------------------------------------- Gruppieren
    def _item_from_world(self, name: str, world: list[tuple[g.Polyline, str]]) -> Item:
        """Neues Objekt aus Pfaden in Weltkoordinaten (Transformation wird 'eingebacken')."""
        bb = g.bbox([p for p, _ in world])
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        it = Item(id=new_id("I"), name=name, kind="vector", x=cx, y=cy)
        t = g.translate(-cx, -cy)
        it.paths = [VectorPath(p.transformed(t), lid) for p, lid in world]
        return it

    def ungroup(self, item: Item) -> list[Item]:
        """Zerlegt ein Vektorobjekt in Teile (Außenkontur + zugehörige Löcher).

        Besteht es nur noch aus einem solchen Teil, wird es in einzelne Konturen zerlegt.
        """
        if item.kind != "vector" or len(item.paths) < 2:
            return [item]
        m = item.matrix()
        world = [(vp.poly.transformed(m), vp.layer_id) for vp in item.paths]
        parts = g.group_parts([p for p, _ in world])
        if len(parts) < 2:                       # nur noch ein Teil -> in einzelne Konturen zerlegen
            parts = [[i] for i in range(len(world))]
        new = [self._item_from_world(f"{item.name} #{k + 1}", [world[i] for i in idx])
               for k, idx in enumerate(parts)]
        pos = self.items.index(item)
        self.items[pos:pos + 1] = new
        return new

    def group(self, items: list[Item], name: str = "Gruppe") -> Item | None:
        """Fasst Vektorobjekte zu einem Objekt zusammen (Bilder bleiben einzeln)."""
        vec = [it for it in items if it.kind == "vector" and it.paths]
        if len(vec) < 2:
            return None
        world = []
        for it in vec:
            m = it.matrix()
            world += [(vp.poly.transformed(m), vp.layer_id) for vp in it.paths]
        g_item = self._item_from_world(name, world)
        pos = min(self.items.index(it) for it in vec)
        ids = {it.id for it in vec}
        self.items = [it for it in self.items if it.id not in ids]
        self.items.insert(min(pos, len(self.items)), g_item)
        return g_item

    def assign_layer(self, items: list[Item], layer_id: str):
        """Weist Objekte einer Ebene zu. Bilder nur Raster-Ebenen, Vektoren beliebigen."""
        layer = self.layer(layer_id)
        if layer is None:
            return
        for it in items:
            if it.kind == "raster":
                if layer.mode == "raster":
                    it.layer_id = layer_id
            else:
                for vp in it.paths:
                    vp.layer_id = layer_id

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

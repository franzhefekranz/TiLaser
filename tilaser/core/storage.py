"""Speichern/Laden: Material-Presets, Maschinenprofil, Projektdateien (.tlp)."""
from __future__ import annotations

import base64
import io
import json
import os
import sys

import numpy as np
from PIL import Image

from . import geometry as g
from .gcode import MachineProfile
from .model import Item, Layer, Project, VectorPath, new_id

def _config_dir() -> str:
    """Konfigurationsordner.

    Portable Version: liegt neben der EXE eine Datei 'portable.txt', werden Einstellungen und Presets
    im Unterordner 'daten' neben dem Programm gespeichert (nichts in Benutzerprofil oder Registry).
    Mit der Umgebungsvariable TILASER_HOME lässt sich der Ordner frei wählen.
    """
    if os.environ.get("TILASER_HOME"):
        return os.environ["TILASER_HOME"]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(exe_dir, "portable.txt")):
            return os.path.join(exe_dir, "daten")
    return os.path.join(os.path.expanduser("~"), ".tilaser")


CONFIG_DIR = _config_dir()
DEFAULT_PRESETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "presets.json")
USER_PRESETS = os.path.join(CONFIG_DIR, "presets.json")
MACHINE_FILE = os.path.join(CONFIG_DIR, "machine.json")

PRESET_FIELDS = ("mode", "power", "min_power", "speed", "passes", "interval", "angle", "crosshatch",
                 "dither", "threshold", "overscan", "bidirectional", "air", "kerf", "tabs", "tab_width",
                 "brightness", "contrast", "gamma", "z_step")


def _ensure_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


# ------------------------------------------------------------------ Presets
DEFAULT_MACHINES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "machines.json")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def load_presets() -> list[dict]:
    """Mitgelieferte Presets + eigene Presets des Benutzers.

    Die Benutzerdatei enthält nur eigene/geänderte Presets und die Namen ausgeblendeter
    Standard-Presets. So erscheinen neue Standard-Presets nach einem Update automatisch.
    """
    defaults = _read_json(DEFAULT_PRESETS, [])
    user = _read_json(USER_PRESETS, None)
    if user is None:
        return defaults
    if isinstance(user, list):                      # altes Format (vollständige Liste)
        user = {"presets": user, "hidden": []}
    own = {p["name"]: p for p in user.get("presets", [])}
    hidden = set(user.get("hidden", []))
    out = [own.pop(p["name"], p) for p in defaults if p["name"] not in hidden]
    return out + list(own.values())


def save_presets(presets: list[dict]):
    defaults = {p["name"]: p for p in _read_json(DEFAULT_PRESETS, [])}
    names = {p["name"] for p in presets}
    own = [p for p in presets if defaults.get(p["name"]) != p]
    hidden = sorted(n for n in defaults if n not in names)
    _ensure_dir()
    with open(USER_PRESETS, "w", encoding="utf-8") as f:
        json.dump({"presets": own, "hidden": hidden}, f, indent=2, ensure_ascii=False)


def load_machine_templates() -> list[dict]:
    return _read_json(DEFAULT_MACHINES, [])


def preset_from_layer(name: str, material: str, layer: Layer, machine: str = "") -> dict:
    d = {"name": name, "material": material, "machine": machine}
    d.update({k: getattr(layer, k) for k in PRESET_FIELDS})
    return d


def apply_preset(layer: Layer, preset: dict):
    for k in PRESET_FIELDS:
        if k in preset:
            setattr(layer, k, preset[k])


# ------------------------------------------------------------------ Maschine
def load_machine() -> MachineProfile:
    try:
        with open(MACHINE_FILE, encoding="utf-8") as f:
            return MachineProfile.from_dict(json.load(f))
    except (OSError, json.JSONDecodeError):
        return MachineProfile()


def save_machine(prof: MachineProfile):
    _ensure_dir()
    with open(MACHINE_FILE, "w", encoding="utf-8") as f:
        json.dump(prof.to_dict(), f, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------ Projekt
def _img_to_b64(a: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _b64_to_img(s: str) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(s))).convert("L"), dtype=np.uint8).copy()


def save_project(project: Project, path: str):
    data = {"version": 1, "layers": [l.to_dict() for l in project.layers], "items": []}
    for it in project.items:
        d = {k: getattr(it, k) for k in ("id", "name", "kind", "img_w", "img_h", "layer_id", "x", "y",
                                         "scale", "rotation", "mirror_x")}
        if it.kind == "raster":
            d["image"] = _img_to_b64(it.image)
        else:
            d["paths"] = []
            for vp in it.paths:
                pd = {"layer": vp.layer_id, "closed": vp.poly.closed}
                if vp.poly.bez is not None:
                    b = vp.poly.bez
                    pd["bez"] = [np.round(b.anchors, 5).tolist(), np.round(b.hin, 5).tolist(),
                                 np.round(b.hout, 5).tolist()]
                else:
                    pd["pts"] = np.round(vp.poly.pts, 4).tolist()
                d["paths"].append(pd)
        data["items"].append(d)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def load_project(path: str) -> Project:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    p = Project()
    p.layers = [Layer.from_dict(d) for d in data.get("layers", [])]
    for d in data.get("items", []):
        it = Item(id=new_id("I"), name=d.get("name", "Objekt"), kind=d.get("kind", "vector"))
        for k in ("img_w", "img_h", "layer_id", "x", "y", "scale", "rotation", "mirror_x"):
            if k in d:
                setattr(it, k, d[k])
        if it.kind == "raster":
            it.image = _b64_to_img(d["image"])
        else:
            it.paths = []
            for pd in d.get("paths", []):
                if "bez" in pd:
                    poly = g.Polyline.from_bez(g.BezPath(*pd["bez"], closed=pd["closed"]))
                else:
                    poly = g.Polyline(np.array(pd["pts"], float), pd["closed"])
                it.paths.append(VectorPath(poly, pd["layer"]))
        p.items.append(it)
    return p

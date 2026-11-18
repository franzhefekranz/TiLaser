"""Flammenerkennung im Kamerabild (nur numpy).

Idee: Eine Flamme ist hell, gelb-orange und bleibt an derselben Stelle. Der glühende Laserpunkt ist
ebenfalls hell und orange, wandert aber ständig weiter. Deshalb wird für jeden Bildpunkt gemittelt,
wie lange er „heiß“ ist (gleitender Mittelwert mit der Haltezeit als Zeitkonstante). Erst wenn eine
genügend große Fläche dauerhaft heiß ist, schlägt der Wächter an.

„Heiß“ heißt: gelb-orange (Rot > Grün > Blau mit deutlichem Abstand) bzw. fast weiß und deutlich
heller als im Referenzbild. Das Referenzbild wird beim Start aufgenommen und langsam nachgeführt,
so stören feste Lichtquellen (orange LED, Holzfarbe, Kreuzlaser) nicht.

Die Erkennung ersetzt keine Aufsicht – sie ist eine zusätzliche Absicherung.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

WORK_WIDTH = 160            # Bilder werden für die Erkennung auf etwa diese Breite verkleinert


def sensitivity_to_area(sens: int) -> float:
    """Empfindlichkeit 1…10 → Mindestfläche als Anteil am Bild (1 = 0,2 %, 5 ≈ 0,03 %, 10 ≈ 0,003 %)."""
    s = min(10, max(1, int(sens)))
    return 0.002 / (1.6 ** (s - 1))


def downsample(rgb: np.ndarray, width: int = WORK_WIDTH) -> np.ndarray:
    """Auf etwa `width` Pixel Breite verkleinern (Blockmittel, schnell)."""
    h, w = rgb.shape[:2]
    k = max(1, w // width)
    if k == 1:
        return rgb[..., :3].astype(np.float32)
    h2, w2 = h // k * k, w // k * k
    a = rgb[:h2, :w2, :3].astype(np.float32)
    return a.reshape(h2 // k, k, w2 // k, k, 3).mean(axis=(1, 3))


def hot_mask(img: np.ndarray, ref_v: np.ndarray | None, delta: float = 40.0) -> np.ndarray:
    """Bildpunkte, die nach Flamme aussehen (img: float RGB 0…255, bereits verkleinert)."""
    r, gr, b = img[..., 0], img[..., 1], img[..., 2]
    v = np.maximum(np.maximum(r, gr), b)
    orange = (r >= 170) & (r >= gr * 1.05) & (gr >= b * 1.15) & (r - b >= 70)
    white_hot = (v >= 245) & (b <= r * 0.92)            # überstrahlter Kern, nicht bläulich
    hot = orange | white_hot
    if ref_v is not None:
        hot &= v - ref_v >= delta
    return hot


@dataclass
class FlameResult:
    ratio: float            # Fläche / Mindestfläche (≥ 1 = Alarm)
    area: float             # Anteil dauerhaft heißer Bildpunkte
    triggered: bool
    mask: np.ndarray | None = None     # dauerhaft heiße Punkte (verkleinert), zur Anzeige


class FlameDetector:
    def __init__(self, sensitivity: int = 5, hold_s: float = 2.0, level: float = 0.5):
        self.sensitivity = sensitivity
        self.hold_s = hold_s
        self.level = level              # Pegel des Mittelwerts (Flammen flackern, daher 0,5)
        self.reset()

    def reset(self, keep_reference: bool = False):
        if not keep_reference:
            self.ref_v = None
        self.persist = None
        self.t_last = None
        self.triggered = False

    @property
    def min_area(self) -> float:
        return sensitivity_to_area(self.sensitivity)

    def update(self, rgb: np.ndarray, t: float) -> FlameResult:
        """Ein Kamerabild (H×W×3, uint8) zum Zeitpunkt t (Sekunden) auswerten."""
        img = downsample(rgb)
        v = img.max(axis=2)
        if self.ref_v is None or self.ref_v.shape != v.shape:
            self.ref_v = v.copy()               # erstes Bild = Referenz
            self.persist = np.zeros(v.shape, np.float32)
            self.t_last = t
            return FlameResult(0.0, 0.0, False, np.zeros(v.shape, bool))
        hot = hot_mask(img, self.ref_v)
        dt = max(0.0, min(2.0, t - (self.t_last if self.t_last is not None else t)))
        self.t_last = t
        k = -np.log(1.0 - self.level)                      # Pegel erreicht nach hold_s Dauerhitze
        a = 1.0 - np.exp(-dt * k / max(0.1, self.hold_s))
        self.persist += (hot.astype(np.float32) - self.persist) * a
        # Referenz langsam nachführen (≈ 1 min), aber nicht an heißen Stellen
        b = 1.0 - np.exp(-dt / 60.0)
        cool = ~hot & (self.persist < 0.1)
        self.ref_v[cool] += (v[cool] - self.ref_v[cool]) * b
        burning = self.persist >= self.level
        area = float(burning.mean())
        ratio = area / self.min_area
        if ratio >= 1.0:
            self.triggered = True
        return FlameResult(ratio, area, ratio >= 1.0, burning)

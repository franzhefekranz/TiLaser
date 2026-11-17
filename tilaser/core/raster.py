"""Bildverarbeitung für Rastergravur: Laden, Skalieren, Dithering."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps


def load_image_gray(path: str) -> tuple[np.ndarray, float, float]:
    """Lädt ein Bild als Graustufen (255 = weiß). Transparenz wird weiß.

    Rückgabe: (Array, Breite mm, Höhe mm) – Größe aus DPI-Angabe, sonst 254 dpi (0,1 mm/Pixel)
    begrenzt auf 100 mm Breite.
    """
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    dpi = im.info.get("dpi", (0, 0))[0] or 0
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    gray = np.asarray(im.convert("L"), dtype=np.uint8).copy()
    h, w = gray.shape
    if dpi and dpi > 10:
        w_mm, h_mm = w / dpi * 25.4, h / dpi * 25.4
    else:
        w_mm = min(100.0, w * 0.1)
        h_mm = w_mm * h / w
    return gray, float(w_mm), float(h_mm)


def resample(gray: np.ndarray, w_mm: float, h_mm: float, interval: float, rotation: float = 0.0,
             mirror_x: bool = False) -> np.ndarray:
    """Skaliert auf die Zielauflösung (1 Pixel = interval mm), optional gedreht."""
    im = Image.fromarray(gray)
    if mirror_x:
        im = ImageOps.mirror(im)
    cols = max(1, int(round(w_mm / interval)))
    rows = max(1, int(round(h_mm / interval)))
    im = im.resize((cols, rows), Image.Resampling.LANCZOS)
    if abs(rotation) > 1e-6:
        im = im.rotate(rotation, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    return np.asarray(im, dtype=np.uint8)


def adjust(gray: np.ndarray, invert=False, brightness: float = 0.0, contrast: float = 0.0,
           gamma: float = 1.0) -> np.ndarray:
    """Graustufen -> 0..1 (1 = weiß) mit Helligkeit/Kontrast (-100..100) und Gamma."""
    v = gray.astype(np.float32) / 255.0
    if brightness:
        v = v + brightness / 100.0
    if contrast:
        f = (1 + contrast / 100.0) if contrast > 0 else 1 / (1 - contrast / 100.0)
        v = (v - 0.5) * f + 0.5
    v = np.clip(v, 0.0, 1.0)
    if gamma and abs(gamma - 1.0) > 1e-6:
        v = v ** gamma
    if invert:
        v = 1.0 - v
    return v


# Fehlerverteilungs-Kerne: (dy, dx, Gewicht), Summe der Gewichte / Divisor = 1
KERNELS = {
    "jarvis": ([(0, 1, 7), (0, 2, 5), (1, -2, 3), (1, -1, 5), (1, 0, 7), (1, 1, 5), (1, 2, 3),
                (2, -2, 1), (2, -1, 3), (2, 0, 5), (2, 1, 3), (2, 2, 1)], 48),
    "stucki": ([(0, 1, 8), (0, 2, 4), (1, -2, 2), (1, -1, 4), (1, 0, 8), (1, 1, 4), (1, 2, 2),
                (2, -2, 1), (2, -1, 2), (2, 0, 4), (2, 1, 2), (2, 2, 1)], 42),
}


def dither(gray: np.ndarray, mode: str = "floyd", threshold: int = 128, invert: bool = False,
           brightness: float = 0.0, contrast: float = 0.0, gamma: float = 1.0) -> np.ndarray:
    """Liefert Brennintensität 0..1 pro Pixel (1 = volle Leistung).

    'grayscale' gibt kontinuierliche Werte zurück, alle anderen 0/1.
    """
    v = adjust(gray, invert, brightness, contrast, gamma)          # 1 = weiß
    dark = 1.0 - v
    if mode == "grayscale":
        dark[v > 0.995] = 0.0
        return dark
    if mode == "threshold":
        return (v < threshold / 255.0).astype(np.float32)
    if mode == "atkinson":
        return _atkinson(v)
    if mode in KERNELS:
        return _diffuse(v, *KERNELS[mode])
    return _floyd(v)


def _diffuse(v: np.ndarray, kernel, div) -> np.ndarray:
    img = v.astype(np.float64).tolist()
    h, w = len(img), len(img[0])
    out = np.zeros((h, w), dtype=np.float32)
    k = [(dy, dx, wt / div) for dy, dx, wt in kernel]
    for y in range(h):
        res = [0.0] * w
        row = img[y]
        for x in range(w):
            old = row[x]
            new = 1.0 if old >= 0.5 else 0.0
            res[x] = new
            err = old - new
            if err == 0.0:
                continue
            for dy, dx, wt in k:
                yy, xx = y + dy, x + dx
                if yy < h and 0 <= xx < w:
                    img[yy][xx] += err * wt
        out[y] = res
    return 1.0 - out


def _floyd(v: np.ndarray) -> np.ndarray:
    img = v.astype(np.float64).tolist()        # Python-Listen sind hier deutlich schneller
    h, w = len(img), len(img[0])
    out = np.zeros((h, w), dtype=np.float32)
    for y in range(h):
        row = img[y]
        nxt = img[y + 1] if y + 1 < h else None
        orow = out[y]
        res = [0.0] * w
        for x in range(w):
            old = row[x]
            if old >= 0.5:
                new = 1.0
            else:
                new = 0.0
            res[x] = new
            err = old - new
            if err == 0.0:
                continue
            if x + 1 < w:
                row[x + 1] += err * 0.4375
            if nxt is not None:
                if x > 0:
                    nxt[x - 1] += err * 0.1875
                nxt[x] += err * 0.3125
                if x + 1 < w:
                    nxt[x + 1] += err * 0.0625
        orow[:] = res
    return 1.0 - out


def _atkinson(v: np.ndarray) -> np.ndarray:
    img = v.astype(np.float64).tolist()
    h, w = len(img), len(img[0])
    out = np.zeros((h, w), dtype=np.float32)
    offs = ((0, 1), (0, 2), (1, -1), (1, 0), (1, 1), (2, 0))
    for y in range(h):
        res = [0.0] * w
        for x in range(w):
            old = img[y][x]
            new = 1.0 if old >= 0.5 else 0.0
            res[x] = new
            err = (old - new) / 8.0
            if err == 0.0:
                continue
            for dy, dx in offs:
                yy, xx = y + dy, x + dx
                if yy < h and 0 <= xx < w:
                    img[yy][xx] += err
        out[y] = res
    return 1.0 - out


def preview_image(gray: np.ndarray, w_mm: float, h_mm: float, interval: float, mode: str, threshold: int,
                  invert: bool, max_px: int = 600, brightness: float = 0.0, contrast: float = 0.0,
                  gamma: float = 1.0) -> np.ndarray:
    """Schnelle Vorschau des Ditherings (uint8, 255 = weiß), Auflösung begrenzt."""
    iv = max(interval, max(w_mm, h_mm) / max_px)
    small = resample(gray, w_mm, h_mm, iv)
    d = dither(small, mode, threshold, invert, brightness, contrast, gamma)
    return (255 - np.clip(d, 0, 1) * 255).astype(np.uint8)

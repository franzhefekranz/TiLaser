"""Kamera-Ausrichtung: Perspektiv-Entzerrung über 4 Passpunkte (Homographie).

Ablauf: 4 Marken an bekannten Bettkoordinaten lasern, im Kamerabild anklicken,
daraus die Abbildung Kamerapixel -> Bett-mm berechnen. Objektivverzeichnung
wird nicht korrigiert (bei gängigen Weitwinkelkameras an den Rändern ein paar mm).
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """3×3-Matrix H mit dst ~ H·src (DLT, mindestens 4 Punktpaare)."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    A = []
    for (x, y), (u, v) in zip(src, dst):
        A.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        A.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.array(A))
    H = vt[-1].reshape(3, 3)
    return H / H[2, 2]


def apply_h(H: np.ndarray, pts) -> np.ndarray:
    p = np.column_stack([np.asarray(pts, float), np.ones(len(pts))]) @ H.T
    return p[:, :2] / p[:, 2:3]


def marker_positions(bed_w: float, bed_h: float, inset: float = 30.0, count: int = 4) -> list[tuple[float, float]]:
    """Passmarken. 4: links unten, rechts unten, rechts oben, links oben.
    9: 3×3-Raster, zeilenweise von unten links nach oben rechts (für die Linsenkorrektur)."""
    if count == 9:
        xs = [inset, bed_w / 2, bed_w - inset]
        ys = [inset, bed_h / 2, bed_h - inset]
        return [(x, y) for y in ys for x in xs]
    return [(inset, inset), (bed_w - inset, inset), (bed_w - inset, bed_h - inset), (inset, bed_h - inset)]


def marker_labels(count: int) -> list[str]:
    if count == 9:
        rows, cols = ("unten", "Mitte", "oben"), ("links", "Mitte", "rechts")
        return [f"{r} {c}" if not (r == "Mitte" and c == "Mitte") else "Mitte" for r in rows for c in cols]
    return ["links unten", "rechts unten", "rechts oben", "links oben"]


# ------------------------------------------------------------ Linsenkorrektur (radial, k1)
def _undistort(px: np.ndarray, k1: float, size) -> np.ndarray:
    c = np.array(size, float) / 2
    R = float(np.hypot(*c))
    v = (np.asarray(px, float) - c) / R
    r2 = np.sum(v ** 2, axis=1, keepdims=True)
    return c + v * (1 + k1 * r2) * R


def _distort(pu: np.ndarray, k1: float, size) -> np.ndarray:
    c = np.array(size, float) / 2
    R = float(np.hypot(*c))
    vu = (np.asarray(pu, float) - c) / R
    vd = vu.copy()
    for _ in range(8):                                  # Fixpunktiteration
        r2 = np.sum(vd ** 2, axis=-1, keepdims=True)
        vd = vu / (1 + k1 * r2)
    return c + vd * R


def fit_lens(px, mm, size) -> tuple[float, np.ndarray, float]:
    """Bestimmt Verzeichnung k1 und Homographie aus ≥ 6 Passpunkten (z. B. 9).
    Rückgabe: (k1, H, mittlerer Restfehler in mm)."""
    px, mm = np.asarray(px, float), np.asarray(mm, float)

    def resid(k):
        pu = _undistort(px, k, size)
        H = homography(pu, mm)
        return float(np.sqrt(np.mean(np.sum((apply_h(H, pu) - mm) ** 2, axis=1)))), H

    lo, hi = -0.6, 0.6
    gr = (5 ** 0.5 - 1) / 2
    a, b = hi - gr * (hi - lo), lo + gr * (hi - lo)
    fa, fb = resid(a)[0], resid(b)[0]
    for _ in range(60):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - gr * (hi - lo)
            fa = resid(a)[0]
        else:
            lo, a, fa = a, b, fb
            b = lo + gr * (hi - lo)
            fb = resid(b)[0]
    k1 = (lo + hi) / 2
    err, H = resid(k1)
    return k1, H, err


def marker_gcode(points, size: float = 6.0, power_s: float = 100, speed: float = 1500, laser_on="M4") -> list[str]:
    """Kreuze an den Passpunkten lasern (geringe Leistung)."""
    lines = ["G21", "G90", "M5"]
    for x, y in points:
        for (x0, y0), (x1, y1) in (((x - size, y), (x + size, y)), ((x, y - size), (x, y + size))):
            lines.append(f"G0 X{x0:.3f} Y{y0:.3f}")
            lines.append(f"{laser_on} S{power_s:g}".strip())
            lines.append(f"G1 X{x1:.3f} Y{y1:.3f} F{speed:g}")
            lines.append("M5")
    lines.append("G0 X0 Y0")
    return lines


def rectify(image: np.ndarray, H_px_to_mm: np.ndarray, bed_w: float, bed_h: float,
            px_per_mm: float = 2.0, k1: float = 0.0) -> np.ndarray:
    """Entzerrt ein Kamerabild (H×W oder H×W×3) auf das Bett: Ergebnisbild, oben = Y max.
    Mit k1 ≠ 0 wird zusätzlich die Objektivverzeichnung herausgerechnet."""
    if abs(k1) > 1e-9:
        return _rectify_lens(image, H_px_to_mm, bed_w, bed_h, px_per_mm, k1)
    out_w, out_h = int(round(bed_w * px_per_mm)), int(round(bed_h * px_per_mm))
    # Ausgabepixel (u, v) -> Bett-mm (u/px, bed_h - v/px) -> Kamerapixel über H^-1
    to_mm = np.array([[1 / px_per_mm, 0, 0], [0, -1 / px_per_mm, bed_h], [0, 0, 1]])
    M = np.linalg.inv(H_px_to_mm) @ to_mm
    M = M / M[2, 2]
    coeffs = (M[0, 0], M[0, 1], M[0, 2], M[1, 0], M[1, 1], M[1, 2], M[2, 0], M[2, 1])
    im = Image.fromarray(image)
    return np.asarray(im.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coeffs,
                                   Image.Resampling.BILINEAR))


def _rectify_lens(image, H, bed_w, bed_h, px_per_mm, k1):
    out_w, out_h = int(round(bed_w * px_per_mm)), int(round(bed_h * px_per_mm))
    h, w = image.shape[:2]
    u, v = np.meshgrid(np.arange(out_w) + 0.5, np.arange(out_h) + 0.5)
    mm = np.column_stack([u.ravel() / px_per_mm, bed_h - v.ravel() / px_per_mm])
    pu = apply_h(np.linalg.inv(H), mm)
    pd = _distort(pu, k1, (w, h))
    x, y = pd[:, 0] - 0.5, pd[:, 1] - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    valid = (x0 >= 0) & (y0 >= 0) & (x0 < w - 1) & (y0 < h - 1)
    x0c, y0c = np.clip(x0, 0, w - 2), np.clip(y0, 0, h - 2)
    img = image.astype(np.float32)
    if img.ndim == 2:
        img = img[:, :, None]
        fx2, fy2 = fx[:, None], fy[:, None]
    else:
        fx2, fy2 = fx[:, None], fy[:, None]
    val = (img[y0c, x0c] * (1 - fx2) * (1 - fy2) + img[y0c, x0c + 1] * fx2 * (1 - fy2)
           + img[y0c + 1, x0c] * (1 - fx2) * fy2 + img[y0c + 1, x0c + 1] * fx2 * fy2)
    val[~valid] = 0
    out = np.clip(np.rint(val), 0, 255).reshape(out_h, out_w, -1).astype(np.uint8)   # runden statt abschneiden
    return out[:, :, 0] if image.ndim == 2 else out

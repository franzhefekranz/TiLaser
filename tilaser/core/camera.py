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


def marker_positions(bed_w: float, bed_h: float, inset: float = 30.0) -> list[tuple[float, float]]:
    """Passmarken: links unten, rechts unten, rechts oben, links oben."""
    return [(inset, inset), (bed_w - inset, inset), (bed_w - inset, bed_h - inset), (inset, bed_h - inset)]


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
            px_per_mm: float = 2.0) -> np.ndarray:
    """Entzerrt ein Kamerabild (H×W oder H×W×3) auf das Bett: Ergebnisbild, oben = Y max."""
    out_w, out_h = int(round(bed_w * px_per_mm)), int(round(bed_h * px_per_mm))
    # Ausgabepixel (u, v) -> Bett-mm (u/px, bed_h - v/px) -> Kamerapixel über H^-1
    to_mm = np.array([[1 / px_per_mm, 0, 0], [0, -1 / px_per_mm, bed_h], [0, 0, 1]])
    M = np.linalg.inv(H_px_to_mm) @ to_mm
    M = M / M[2, 2]
    coeffs = (M[0, 0], M[0, 1], M[0, 2], M[1, 0], M[1, 1], M[1, 2], M[2, 0], M[2, 1])
    im = Image.fromarray(image)
    return np.asarray(im.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coeffs,
                                   Image.Resampling.BILINEAR))

"""Fingerzinken an den Kanten zweier Teile, optional mit Dogbone-Ecken.

Die beiden Teile werden so hingelegt, dass sich die zu verbindenden Kanten berühren (auf einer
Linie liegen und sich überlappen). Auf dem gemeinsamen Stück werden abwechselnd Zinken und
Aussparungen erzeugt – beim zweiten Teil genau umgekehrt.

Arten:
* ``tabs``   – ineinandergreifend (Standard): Teil 1 bekommt Zinken, die um die Materialstärke
  herausragen, Teil 2 genau passende Aussparungen. Liegen die Teile wie gezeichnet, füllen die
  Zinken die Aussparungen lückenlos – das eine Teil ist mit dem anderen verzinkt.
* ``corner`` – Eckverbindung wie beim Box-Generator: beide Teile behalten ihr Außenmaß und bekommen
  abwechselnd Aussparungen (versetzt); erst um 90° gedreht greifen sie ineinander.

Dogbones: An jeder Innenecke, die durch die Zinken entsteht, wird ein Kreisbogen mit dem
angegebenen Durchmesser freigeschnitten (Fräser- oder Kerf-Ausgleich für eckige Zinken).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .clip import inside


@dataclass
class SharedEdge:
    ra: int          # Ring in Teil A
    ka: int          # Segment (Punkt k -> k+1)
    rb: int
    kb: int
    p0: np.ndarray   # Anfang des gemeinsamen Stücks
    p1: np.ndarray   # Ende
    length: float


def _segments(ring: np.ndarray):
    a = ring
    b = np.roll(ring, -1, axis=0)
    return a, b


def find_shared_edges(rings_a, rings_b, tol: float = 0.5, min_len: float = 3.0,
                      angle_tol_deg: float = 1.0) -> list[SharedEdge]:
    """Alle geraden Kantenstücke von A und B, die (bis tol) auf einer Linie liegen und sich überlappen."""
    out = []
    sin_tol = math.sin(math.radians(angle_tol_deg))
    for ia, ra in enumerate(rings_a):
        a0, a1 = _segments(ra)
        for ib, rb in enumerate(rings_b):
            b0, b1 = _segments(rb)
            for ka in range(len(a0)):
                d = a1[ka] - a0[ka]
                la = float(np.hypot(*d))
                if la < min_len:
                    continue
                u = d / la
                n = np.array([-u[1], u[0]])
                db = b1 - b0
                lb = np.hypot(db[:, 0], db[:, 1])
                ok = lb >= min_len
                cross = np.abs(u[0] * db[:, 1] - u[1] * db[:, 0]) / np.maximum(lb, 1e-12)
                ok &= cross <= sin_tol
                dist0 = np.abs((b0 - a0[ka]) @ n)
                dist1 = np.abs((b1 - a0[ka]) @ n)
                ok &= (dist0 <= tol) & (dist1 <= tol)
                for kb in np.nonzero(ok)[0]:
                    s0, s1 = sorted(((b0[kb] - a0[ka]) @ u, (b1[kb] - a0[ka]) @ u))
                    lo, hi = max(0.0, s0), min(la, s1)
                    if hi - lo >= min_len:
                        out.append(SharedEdge(ia, ka, ib, int(kb), a0[ka] + u * lo, a0[ka] + u * hi, hi - lo))
    out.sort(key=lambda e: -e.length)
    return out


def finger_count(length: float, finger: float) -> int:
    n = max(1, int(round(length / max(finger, 1e-6))))
    return n if n % 2 == 1 else n + 1 if length / (n + 1) >= finger * 0.6 else max(1, n - 1)


def _material_normal(rings, p0, p1) -> np.ndarray:
    """Einheitsnormale der Kante, die ins Material zeigt."""
    u = (p1 - p0) / max(1e-12, float(np.hypot(*(p1 - p0))))
    n = np.array([-u[1], u[0]])
    mid = (p0 + p1) / 2
    eps = min(0.05, 0.01 * float(np.hypot(*(p1 - p0))))
    return n if inside((mid + n * eps)[None], rings)[0] else -n


def _apply_profile(ring: np.ndarray, k: int, p0, p1, depths, n_in) -> np.ndarray:
    """Segment k des Rings: zwischen p0 und p1 durch das Zinkenprofil ersetzen.
    depths: Liste (Tiefe) je Abschnitt; Tiefe > 0 geht ins Material, < 0 heraus."""
    a, b = ring[k], ring[(k + 1) % len(ring)]
    u = (b - a) / max(1e-12, float(np.hypot(*(b - a))))
    if (p1 - p0) @ u < 0:
        p0, p1 = p1, p0
        depths = depths[::-1]
    L = float(np.hypot(*(p1 - p0)))
    w = L / len(depths)
    pts = [p0]
    for i, d in enumerate(depths):
        s0, s1 = i * w, (i + 1) * w
        pts += [p0 + u * s0 + n_in * d, p0 + u * s1 + n_in * d]
    pts.append(p1)
    new = [ring[: k + 1]] + [np.array(pts)] + [ring[k + 1:]]
    out = np.vstack([q for q in new if len(q)])
    keep = [0] + [i for i in range(1, len(out)) if np.hypot(*(out[i] - out[i - 1])) > 1e-9]
    out = out[keep]
    if len(out) > 1 and np.hypot(*(out[0] - out[-1])) <= 1e-9:
        out = out[:-1]
    return _cleanup(out)


def _cleanup(ring: np.ndarray) -> np.ndarray:
    """Doppelte Punkte, Punkte auf einer Geraden und Rückwärts-Spitzen entfernen."""
    pts = [p for p in ring]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, v, b = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            d1, d2 = v - a, b - v
            if np.hypot(*d1) < 1e-9 or np.hypot(*d2) < 1e-9 or abs(d1[0] * d2[1] - d1[1] * d2[0]) < 1e-9:
                pts.pop(i)
                changed = True
                break
    return np.array(pts)


def add_dogbones(rings: list[np.ndarray], diameter: float, only_near=None, near_tol: float = 1e-6) -> list[np.ndarray]:
    """Kreisförmige Freischnitte an Innenecken (rechtwinklig o. ä.). only_near: Liste von Punkten –
    nur Ecken bearbeiten, die (bis near_tol) mit einem dieser Punkte übereinstimmen."""
    if diameter <= 0:
        return rings
    r = diameter / 2
    out = []
    near = np.asarray(only_near) if only_near is not None and len(only_near) else None
    for ring in rings:
        n = len(ring)
        prev = np.roll(ring, 1, axis=0)
        nxt = np.roll(ring, -1, axis=0)
        new = []
        for i in range(n):
            v = ring[i]
            if near is not None and np.min(np.hypot(*(near - v).T)) > near_tol:
                new.append(v)
                continue
            la, lb = np.hypot(*(v - prev[i])), np.hypot(*(nxt[i] - v))
            if la < 1e-9 or lb < 1e-9:
                new.append(v)
                continue
            a = (v - prev[i]) / la
            b = (nxt[i] - v) / lb
            turn = b - a
            tl = float(np.hypot(*turn))
            if tl < 1e-6:                     # gerade weiter
                new.append(v)
                continue
            bis = turn / tl
            eps = 1e-3 * min(1.0, la, lb)
            if inside((v + bis * eps)[None], rings)[0]:     # Außenecke: Material auf der Innenseite der Kurve
                new.append(v)
                continue
            cosang = float(np.clip(-(a @ b), -1, 1))
            half = math.acos(cosang) / 2                 # halber Innenwinkel
            if half < math.radians(20) or half > math.radians(160):
                new.append(v)
                continue
            c = v + bis * r                               # Kreis durch die Ecke
            chord = 2 * r * math.cos(half)               # Abstand Ecke -> zweiter Schnittpunkt je Kante
            if chord > la - 1e-6 or chord > lb - 1e-6:
                new.append(v)
                continue
            q1 = v - a * chord
            q2 = v + b * chord
            new.append(q1)
            new += _arc(c, r, q1, v)
            new += _arc(c, r, v, q2)[1:]
            new.append(q2)
        out.append(np.array(new))
    return out


def _arc(c, r, p, q, step_deg: float = 15.0):
    t0 = math.atan2(p[1] - c[1], p[0] - c[0])
    t1 = math.atan2(q[1] - c[1], q[0] - c[0])
    d = (t1 - t0 + math.pi) % (2 * math.pi) - math.pi          # kürzerer Bogen
    n = max(2, int(abs(math.degrees(d)) / step_deg) + 1)
    ts = t0 + d * np.linspace(0, 1, n)
    return [np.array([c[0] + r * math.cos(t), c[1] + r * math.sin(t)]) for t in ts]


def sides(rings_a, rings_b, tol: float = 0.5, min_len: float = 3.0):
    """Lage der beiden Teile zueinander an der längsten gemeinsamen Kante:
    ('left'|'right'|'bottom'|'top' für A, dasselbe für B) oder None, wenn sich keine Kanten berühren."""
    ra = [np.asarray(r, float) for r in rings_a]
    rb = [np.asarray(r, float) for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=min_len)
    if not edges:
        return None
    e = edges[0]
    to_b = -_material_normal(ra, e.p0, e.p1)            # von A zur Kante hinaus, also Richtung B
    if abs(to_b[0]) >= abs(to_b[1]):
        return ("left", "right") if to_b[0] > 0 else ("right", "left")
    return ("bottom", "top") if to_b[1] > 0 else ("top", "bottom")


def finger_joint(rings_a, rings_b, thickness: float, finger: float = 10.0, kind: str = "tabs",
                 dogbone: float = 0.0, tol: float = 0.5, all_edges: bool = False):
    """Zinken an der längsten gemeinsamen Kante (oder allen). Gibt neue Ringe für A und B und
    die Anzahl der bearbeiteten Kanten zurück."""
    if thickness <= 0:
        raise ValueError("Materialstärke muss größer als 0 sein.")
    ra = [np.asarray(r, float).copy() for r in rings_a]
    rb = [np.asarray(r, float).copy() for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=max(3 * thickness, finger))
    if not edges:
        raise ValueError("Keine gemeinsame Kante gefunden. Die beiden Teile so hinlegen, dass sich die zu "
                         "verbindenden Kanten berühren (z. B. mit Anordnen → Ausrichten), dann erneut versuchen.")
    if not all_edges:
        edges = edges[:1]
    # pro Ring von hinten nach vorn bearbeiten, damit die Segmentnummern stimmen
    corners_a, corners_b = [], []
    for e in sorted(edges, key=lambda e: -e.ka):
        cnt = finger_count(e.length, finger)
        n_a = _material_normal(ra, e.p0, e.p1)
        if kind == "tabs":
            da = [-thickness if i % 2 == 0 else 0.0 for i in range(cnt)]
        else:
            da = [0.0 if i % 2 == 0 else thickness for i in range(cnt)]
        new = _apply_profile(ra[e.ra], e.ka, e.p0, e.p1, da, n_a)
        corners_a += [p for p in new if not any(np.allclose(p, q) for q in ra[e.ra])]
        ra[e.ra] = new
    for e in sorted(edges, key=lambda e: -e.kb):
        cnt = finger_count(e.length, finger)
        n_b = _material_normal(rb, e.p0, e.p1)
        db = [thickness if i % 2 == 0 else 0.0 for i in range(cnt)]
        # Teil B kann die Kante in Gegenrichtung durchlaufen – Profil an p0/p1 ausrichten
        seg0 = rb[e.rb][e.kb]
        seg1 = rb[e.rb][(e.kb + 1) % len(rb[e.rb])]
        u = seg1 - seg0
        p0, p1 = (e.p0, e.p1) if (e.p1 - e.p0) @ u >= 0 else (e.p1, e.p0)
        dd = db if (e.p1 - e.p0) @ u >= 0 else db[::-1]
        new = _apply_profile(rb[e.rb], e.kb, p0, p1, dd, n_b)
        corners_b += [p for p in new if not any(np.allclose(p, q) for q in rb[e.rb])]
        rb[e.rb] = new
    if dogbone > 0:
        ra = add_dogbones(ra, dogbone, corners_a)
        rb = add_dogbones(rb, dogbone, corners_b)
    return ra, rb, len(edges)


# ---------------------------------------------------------------- Puzzle-Verbindung

def puzzle_profile(centers, signs, size: float, delta: float = 0.0, step_deg: float = 8.0) -> list[tuple[float, float]]:
    """Punkte (s, t) der Puzzle-Nasen entlang einer Kante (s entlang, t quer, t > 0 = von Teil A nach B).

    size = Durchmesser des Nasenkopfs. Der Hals ist schmaler als der Kopf, so dass die Teile in der
    Ebene formschlüssig halten. signs: +1 = Nase an Teil A (ragt nach B), -1 = Nase an Teil B.
    delta verschiebt die Kontur senkrecht nach außen (> 0) bzw. innen (< 0) – für das Spiel."""
    r, a0, f = size / 2, 0.3 * size, 0.12 * size
    c = 0.25 * size + math.sqrt(r * r - a0 * a0)          # Mitte des Kopfes über der Kante
    out: list[tuple[float, float]] = []
    for sc, sg in zip(centers, signs):
        a, R = a0 + delta, r + delta
        t1 = c - math.sqrt(max(R * R - a * a, 1e-12))
        pts: list[tuple[float, float]] = []
        for ang in np.radians(np.arange(-90, 0 + 1e-9, step_deg)):          # Ausrundung links
            pts.append((sc - a - f + f * math.cos(ang), f + f * math.sin(ang)))
        pts.append((sc - a, t1))
        th0 = math.atan2(t1 - c, -a) % (2 * math.pi)                         # links unten am Kopf
        th1 = math.atan2(t1 - c, a)                                          # rechts unten (negativ)
        n = max(8, int(math.ceil(math.degrees(th0 - th1) / step_deg)))
        for ang in np.linspace(th0, th1, n + 1)[1:-1]:                       # über den Kopf
            pts.append((sc + R * math.cos(ang), c + R * math.sin(ang)))
        pts.append((sc + a, t1))
        for ang in np.radians(np.arange(180, 270 + 1e-9, step_deg)):         # Ausrundung rechts
            pts.append((sc + a + f + f * math.cos(ang), f + f * math.sin(ang)))
        out += [(s, sg * t) for s, t in pts]
    return out


def puzzle_height(size: float) -> float:
    """Wie weit eine Nase über die Kante ragt."""
    r, a0 = size / 2, 0.3 * size
    return 0.25 * size + math.sqrt(r * r - a0 * a0) + r


def puzzle_count(length: float, size: float) -> int:
    """Anzahl Nasen für eine Kante: etwa alle 2,5 Kopfdurchmesser eine."""
    return max(1, int(length // (2.5 * size)))


def _insert_points(ring: np.ndarray, k: int, pts: np.ndarray) -> np.ndarray:
    """Segment k (Punkt k -> k+1) durch die Weltpunkte pts ersetzen (Richtung wird angepasst)."""
    a, b = ring[k], ring[(k + 1) % len(ring)]
    if len(pts) > 1 and (pts[-1] - pts[0]) @ (b - a) < 0:
        pts = pts[::-1]
    out = np.vstack([q for q in (ring[: k + 1], pts, ring[k + 1:]) if len(q)])
    keep = [0] + [i for i in range(1, len(out)) if np.hypot(*(out[i] - out[i - 1])) > 1e-9]
    out = out[keep]
    if len(out) > 1 and np.hypot(*(out[0] - out[-1])) <= 1e-9:
        out = out[:-1]
    return _cleanup(out)


def puzzle_joint(rings_a, rings_b, size: float = 10.0, count: int = 0, clearance: float = 0.0,
                 alternate: bool = False, tol: float = 0.5, all_edges: bool = False):
    """Puzzle-Nasen an der längsten gemeinsamen Kante (oder allen) zweier Teile, die Kante an Kante liegen.

    Teil A bekommt die Nasen, Teil B die passenden Aussparungen (bei alternate abwechselnd).
    clearance = Spiel (gesamt); jede Seite wird um die Hälfte zurückgenommen. Gibt neue Ringe für A, B
    und die Anzahl der bearbeiteten Kanten zurück."""
    if size <= 0:
        raise ValueError("Die Nasengröße muss größer als 0 sein.")
    if not -0.5 * size < clearance < 0.25 * size:
        raise ValueError("Das Spiel ist für diese Nasengröße zu groß.")
    ra = [np.asarray(r, float).copy() for r in rings_a]
    rb = [np.asarray(r, float).copy() for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=1.2 * size)
    if not edges:
        raise ValueError("Keine gemeinsame Kante gefunden, die lang genug für die Nasen ist. Die beiden Teile Kante "
                         "an Kante legen (z. B. mit Anordnen → Kanten aneinanderlegen) oder die Nasen kleiner wählen.")
    if not all_edges:
        edges = edges[:1]
    height = puzzle_height(size)
    jobs = []
    for e in edges:
        n = count if count > 0 else puzzle_count(e.length, size)
        if n * 1.1 * size > e.length:
            raise ValueError(f"Auf die Kante ({e.length:.1f} mm) passen keine {n} Nasen mit {size:g} mm – "
                             "weniger Nasen oder kleinere Nasen wählen.")
        centers = [e.length * (i + 0.5) / n for i in range(n)]
        signs = [(-1 if (alternate and i % 2) else 1) for i in range(n)]
        u = (e.p1 - e.p0) / e.length
        v = -_material_normal(ra, e.p0, e.p1)               # von A nach B
        # die Aussparungen müssen ganz im jeweils anderen Teil liegen
        for sc, sg in zip(centers, signs):
            probe = e.p0 + u * sc + v * sg * (height + 0.3)
            if not inside(probe[None], rb if sg > 0 else ra)[0]:
                raise ValueError("Ein Teil ist an der Kante zu schmal für die Nasen "
                                 f"(sie ragen {height:.1f} mm hinein) – kleinere Nasen wählen.")
        jobs.append((e, centers, signs, u, v))

    def world(e, centers, signs, u, v, grow):
        # grow < 0: eigene Nasen etwas kleiner, eigene Aussparungen etwas größer (Teil A); grow > 0 für Teil B
        pts = [(0.0, 0.0)]
        for sc, sg in zip(centers, signs):
            pts += puzzle_profile([sc], [sg], size, grow * sg)
        pts.append((e.length, 0.0))
        return np.array([e.p0 + u * s + v * t for s, t in pts])

    half = clearance / 2
    for e, centers, signs, u, v in sorted(jobs, key=lambda j: -j[0].ka):
        ra[e.ra] = _insert_points(ra[e.ra], e.ka, world(e, centers, signs, u, v, -half))
    for e, centers, signs, u, v in sorted(jobs, key=lambda j: -j[0].kb):
        rb[e.rb] = _insert_points(rb[e.rb], e.kb, world(e, centers, signs, u, v, +half))
    return ra, rb, len(jobs)


# ---------------------------------------------------------------- Snap-Lock (Rastlaschen)

@dataclass
class SnapSpec:
    thickness: float = 3.0     # Materialstärke (beide Teile)
    width: float = 8.0         # Breite einer Lasche
    count: int = 0             # 0 = automatisch
    barb: float = 0.5          # wie weit der Haken seitlich übersteht
    slit: float = 1.5          # Breite des Federschlitzes zwischen den beiden Zungen
    clearance: float = 0.1     # Spiel im Schlitz und unter dem Haken
    inset: float = 3.0         # Abstand der Schlitze in Teil B von dessen Kante
    gap: float = 3.0           # Abstand, um den Teil A nach dem Erzeugen von B weggeschoben wird


def snap_tab_profile(sc: float, spec: SnapSpec) -> list[tuple[float, float]]:
    """Kontur einer Rastlasche (s entlang der Kante, t > 0 = aus Teil A heraus).

    Zwei federnde Zungen mit einem Schlitz dazwischen, der in Teil A hineinreicht. Außen sitzt je ein
    Haken knapp über der Materialstärke, darüber eine Schräge zum Einführen."""
    w2, g2, b = spec.width / 2, spec.slit / 2, spec.barb
    y1 = spec.thickness + spec.clearance                 # Unterkante des Hakens: liegt auf der Gegenseite an
    top = y1 + max(1.5, 3 * b)                           # Schräge zum Einführen
    depth = max(2.0, spec.thickness)                     # Schlitz reicht so weit in Teil A
    return [(sc - w2, 0.0), (sc - w2, y1), (sc - w2 - b, y1), (sc - w2, top), (sc - g2, top), (sc - g2, -depth),
            (sc + g2, -depth), (sc + g2, top), (sc + w2, top), (sc + w2 + b, y1), (sc + w2, y1), (sc + w2, 0.0)]


def snap_height(spec: SnapSpec) -> float:
    return spec.thickness + spec.clearance + max(1.5, 3 * spec.barb)


def snap_lock(rings_a, rings_b, spec: SnapSpec, tol: float = 0.5, all_edges: bool = False):
    """Rastlaschen an Teil A und passende Schlitze in Teil B (T- oder L-Verbindung im rechten Winkel).

    Die Teile liegen beim Aufruf Kante an Kante (wie bei den Fingerzinken). Teil A bekommt an der Kante
    federnde Laschen mit Haken, Teil B rechteckige Durchbrüche im Abstand ``inset`` von seiner Kante.
    Danach wird Teil A um Laschenhöhe + ``gap`` von B weggeschoben, damit sich nichts überlappt.
    Gibt (Ringe A, Ringe B inkl. neuer Schlitze, Anzahl Kanten, Verschiebung von A) zurück."""
    t = spec.thickness
    if t <= 0 or spec.width <= 0:
        raise ValueError("Materialstärke und Laschenbreite müssen größer als 0 sein.")
    if spec.slit >= spec.width - 2.0:
        raise ValueError("Der Federschlitz ist zu breit für die Lasche – jede Zunge braucht mindestens 1 mm.")
    if 2 * spec.barb > spec.slit + spec.clearance:
        raise ValueError("Die Haken stehen weiter über, als die Zungen in den Federschlitz ausweichen können "
                         "(2 × Haken ≤ Schlitzbreite + Spiel).")
    ra = [np.asarray(r, float).copy() for r in rings_a]
    rb = [np.asarray(r, float).copy() for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=spec.width + 4)
    if not edges:
        raise ValueError("Keine gemeinsame Kante gefunden, die lang genug für die Laschen ist. Die beiden Teile "
                         "Kante an Kante legen (z. B. mit Anordnen → Kanten aneinanderlegen).")
    if not all_edges:
        edges = edges[:1]
    jobs, slots = [], []
    for e in edges:
        n = spec.count if spec.count > 0 else max(1, int(round(e.length / (4 * spec.width))))
        if n * (spec.width + 2 * spec.barb + 3) > e.length:
            raise ValueError(f"Auf die Kante ({e.length:.1f} mm) passen keine {n} Laschen mit {spec.width:g} mm – "
                             "weniger oder schmalere Laschen wählen.")
        centers = [e.length * (i + 0.5) / n for i in range(n)]
        u = (e.p1 - e.p0) / e.length
        v = -_material_normal(ra, e.p0, e.p1)              # von A nach B
        for sc in centers:
            hw, c = (spec.width + spec.clearance) / 2, spec.clearance / 2
            box = [(sc - hw, spec.inset - c), (sc + hw, spec.inset - c), (sc + hw, spec.inset + t + c),
                   (sc - hw, spec.inset + t + c)]
            ring = np.array([e.p0 + u * s + v * tt for s, tt in box])
            if not inside(ring, rb).all():
                raise ValueError("Die Schlitze passen nicht in Teil B – Abstand zur Kante oder Laschenbreite "
                                 "verkleinern.")
            slots.append(ring)
        jobs.append((e, centers, u, v))
    for e, centers, u, v in sorted(jobs, key=lambda j: -j[0].ka):
        pts = [(0.0, 0.0)]
        for sc in centers:
            pts += snap_tab_profile(sc, spec)
        pts.append((e.length, 0.0))
        ra[e.ra] = _insert_points(ra[e.ra], e.ka, np.array([e.p0 + u * s + v * tt for s, tt in pts]))
    shift = -jobs[0][3] * (snap_height(spec) + spec.gap)
    ra = [r + shift for r in ra]
    return ra, rb + slots, len(jobs), shift


# ---------------------------------------------------------------- Hammerverbindung (Keilzapfen)
#
# Nach der „Hammer Joint“-Zeichnung von Jiskar Schmitz (Lasersheets, frei verwendbar): Der Keil wird direkt
# im Zapfenschlitz mitgelasert und hängt nur an zwei kurzen Stegen. Er steht über den Zapfen hinaus; beim
# Einschlagen brechen die Stege, der Keil rutscht in den sich verjüngenden Schlitz und spreizt die beiden
# Zapfenhälften im Loch. Danach ist er bündig mit dem Zapfen.

@dataclass
class HammerSpec:
    thickness: float = 6.0      # Materialstärke beider Teile
    width: float = 20.0         # Zapfenbreite (Loch); der Zapfen selbst ist um ``press`` breiter
    count: int = 0              # 0 = automatisch
    protrude: float = 0.2       # Zapfen steht so weit über das Gegenstück hinaus (zum Bündigschleifen)
    wedge_out: float = 10.0     # so weit steht der Keil vor dem Einschlagen über den Zapfen hinaus
    spread: float = 1.2         # Keil oben um so viel breiter als der Schlitz an der Zapfenspitze (gesamt)
    slot_bottom: float = 3.3    # Breite des Keilschlitzes an seinem runden Grund
    taper: float = 0.07         # Verjüngung des Schlitzes (Breitenzunahme je mm nach oben)
    depth: float = 20.0         # Tiefe von Keilschlitz und Entlastungsschlitzen unter der Kante
    bridge: float = 1.0         # Länge der Haltestege des Keils
    relief: float = 0.3         # Entlastungsschlitze neben dem Zapfen: Breite an der Kante (keilförmig), 0 = keine
    press: float = 0.15         # Zapfen ist so viel breiter als das Loch (Klemmung quer)
    clearance: float = 0.075    # Loch ist so viel länger als die Materialstärke
    inset: float = 6.0          # Abstand der Zapfenlöcher in Teil B von dessen Kante
    gap: float = 3.0            # Abstand, um den Teil A nach dem Erzeugen weggeschoben wird


_SLIT = 0.01                    # Versatz, damit Hin- und Rückweg eines Einzelschnitts nicht zusammenfallen


def _hammer_dims(spec: HammerSpec):
    H = spec.thickness + spec.protrude                    # Oberkante des Zapfens über der Kante
    bridge_top = H - 5.0                                  # Stege 5 mm unter der Zapfenspitze (wie im Original)
    bridge_bot = bridge_top - spec.bridge
    wedge_bot = -spec.depth + spec.wedge_out + 1.0        # nach dem Einschlagen 1 mm über dem Schlitzgrund

    def half(y):                                          # halbe Schlitzbreite in Höhe y
        return spec.slot_bottom / 2 + spec.taper / 2 * (y + spec.depth)
    return H, bridge_top, bridge_bot, wedge_bot, half


def hammer_tenon_profile(sc: float, spec: HammerSpec, step_deg: float = 15.0):
    """Zapfen samt Keil (Außenkontur) und Schlitz unter dem Keil (eigene Kontur) für einen Zapfen.

    Rückgabe: (Punkte der Kante entlang, Punkte der inneren Kontur) – lokal: s entlang der Kante,
    t > 0 = aus Teil A heraus."""
    H, bt, bb, wb, half = _hammer_dims(spec)
    w2 = spec.width / 2 + spec.press / 2
    r, d, k = spec.relief, spec.depth, _SLIT
    top = half(H) + spec.spread / 2                       # halbe Keilbreite oben
    edge: list[tuple[float, float]] = []
    edge += [(sc - w2 - r, 0.0), (sc - w2, -d)] if r > 0 else [(sc - w2, 0.0)]
    edge += [(sc - w2, H), (sc - half(H), H), (sc - half(bt), bt), (sc - half(H) - k, H),   # Einzelschnitt bis Steg
             (sc - top, H + spec.wedge_out), (sc + top, H + spec.wedge_out),                # Keilkopf
             (sc + half(H) + k, H), (sc + half(bt), bt), (sc + half(H), H), (sc + w2, H)]
    edge += [(sc + w2, -d), (sc + w2 + r, 0.0)] if r > 0 else [(sc + w2, 0.0)]
    rb = spec.slot_bottom / 2
    inner = [(sc - half(bb), bb), (sc - rb, -d + rb)]
    inner += [(sc + rb * math.cos(a), -d + rb + rb * math.sin(a))
              for a in np.radians(np.arange(180 + step_deg, 360 - 1e-9, step_deg))]          # runder Grund
    inner += [(sc + rb, -d + rb), (sc + half(bb), bb), (sc + half(wb) - k, wb), (sc - half(wb) + k, wb)]
    return edge, inner


def hammer_joint(rings_a, rings_b, spec: HammerSpec, tol: float = 0.5, all_edges: bool = False):
    """Keilzapfen an Teil A (Keil mit Stegen im Schlitz), passende Zapfenlöcher in Teil B.

    Die Teile liegen beim Aufruf Kante an Kante. Danach wird Teil A um Zapfen- und Keilhöhe + gap von B
    weggeschoben. Gibt (Ringe A inkl. Schlitzkonturen, Ringe B inkl. Löcher, Anzahl Kanten, Verschiebung
    von A) zurück."""
    t, w = spec.thickness, spec.width
    if t <= 0 or w <= 0:
        raise ValueError("Materialstärke und Zapfenbreite müssen größer als 0 sein.")
    H, bt, bb, wb, half = _hammer_dims(spec)
    if not (wb + 1.0 < bb and bt < H and half(H) * 2 + 4 < w):
        raise ValueError("Die Maße passen nicht zusammen: Keil, Stege und Schlitz brauchen mehr Platz – "
                         "Schlitztiefe vergrößern, Keilüberstand oder Steglänge verkleinern.")
    if half(wb) * 2 >= half(H) * 2 + spec.spread or spec.spread <= 0:
        raise ValueError("Der Keil muss oben breiter sein als der Schlitz (Spreizung größer als 0).")
    ra = [np.asarray(r, float).copy() for r in rings_a]
    rb = [np.asarray(r, float).copy() for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=w + 4)
    if not edges:
        raise ValueError("Keine gemeinsame Kante gefunden, die lang genug für die Zapfen ist. Die beiden Teile "
                         "Kante an Kante legen (z. B. mit Anordnen → Kanten aneinanderlegen).")
    if not all_edges:
        edges = edges[:1]
    jobs, holes, inner_rings = [], [], []
    for e in edges:
        n = spec.count if spec.count > 0 else max(1, int(round(e.length / (3 * w))))
        if n * (w + 2 * spec.relief + 4) > e.length:
            raise ValueError(f"Auf die Kante ({e.length:.1f} mm) passen keine {n} Zapfen mit {w:g} mm – "
                             "weniger oder schmalere Zapfen wählen.")
        centers = [e.length * (i + 0.5) / n for i in range(n)]
        u = (e.p1 - e.p0) / e.length
        v = -_material_normal(ra, e.p0, e.p1)
        for sc in centers:
            probe = e.p0 + u * sc - v * (spec.depth + 1.0)
            if not inside(probe[None], ra)[0]:
                raise ValueError("Teil A ist an der Kante zu schmal für die Schlitztiefe – Schlitztiefe verkleinern.")
            hw, c = w / 2, spec.clearance / 2
            box = [(sc - hw, spec.inset - c), (sc + hw, spec.inset - c), (sc + hw, spec.inset + t + c),
                   (sc - hw, spec.inset + t + c)]
            ring = np.array([e.p0 + u * s + v * tt for s, tt in box])
            if not inside(ring, rb).all():
                raise ValueError("Die Zapfenlöcher passen nicht in Teil B – Abstand zur Kante oder Zapfenbreite "
                                 "verkleinern.")
            holes.append(ring)
        jobs.append((e, centers, u, v))
    for e, centers, u, v in sorted(jobs, key=lambda j: -j[0].ka):
        pts = [(0.0, 0.0)]
        for sc in centers:
            edge, inner = hammer_tenon_profile(sc, spec)
            pts += edge
            inner_rings.append(np.array([e.p0 + u * s + v * tt for s, tt in inner]))
        pts.append((e.length, 0.0))
        ra[e.ra] = _insert_points(ra[e.ra], e.ka, np.array([e.p0 + u * s + v * tt for s, tt in pts]))
    shift = -jobs[0][3] * (H + spec.wedge_out + spec.gap)
    ra = [r + shift for r in ra + inner_rings]
    return ra, rb + holes, len(jobs), shift


# ---------------------------------------------------------------- Knöchelscharnier (Knuckle Hinge)
#
# Scharnier zwischen zwei Teilen einer Box, z. B. Rückwand der Box und Rückwand des Deckels, die Kante an
# Kante liegen (so wie sie bei geschlossener Box übereinanderstehen). Die Knöchel sind Scheiben aus dem
# Material, die senkrecht auf der Außenseite der Wände stehen – abwechselnd an Teil A und Teil B – und je
# mit zwei kleinen Fingerzinken in Schlitzen der Wand stecken. Das Auge mit dem Loch liegt genau über der
# gemeinsamen Kante; zum Ende hin ist der Knöchel abgeschrägt. Durch alle Augen geht der Stift: ein
# mitgelaserter Vierkantstift (dreht sich im runden Loch) oder ein Rundstab/Draht.

@dataclass
class KnuckleSpec:
    thickness: float = 3.0      # Materialstärke (Wände und Knöchel)
    knuckles: int = 5           # Knöchel je Scharnier (ungerade: außen beide an Teil A)
    hinges: int = 2             # Scharniere entlang der Kante (1 = mittig, ab 2: äußere nahe den Enden, Rest dazwischen)
    inset: float = 10.0         # ab 2 Scharnieren: Abstand der äußeren vom Ende der Kante
    pin: float = 0.0            # Stiftdurchmesser; 0 = Vierkantstift mitlasern
    clearance: float = 0.2      # Spiel: Loch um den Stift, Schlitze um die Zinken
    wall: float = 2.0           # Wandstärke um das Loch
    length: float = 25.0        # Knöchellänge ab der Kante (auf der Wand)
    end_height: float = 3.0     # Höhe des Knöchels am abgeschrägten Ende
    tab: float = 4.0            # Länge eines Fingerzinkens


def knuckle_hole(spec: KnuckleSpec) -> float:
    """Lochdurchmesser: Rundstab + Spiel, oder Diagonale des Vierkantstifts + Spiel."""
    d = spec.pin if spec.pin > 0 else spec.thickness * math.sqrt(2)
    return d + spec.clearance


def knuckle_radius(spec: KnuckleSpec) -> float:
    return knuckle_hole(spec) / 2 + spec.wall


def knuckle_tabs(spec: KnuckleSpec) -> list[tuple[float, float]]:
    """Lage der beiden Fingerzinken entlang der Wand (Abstand von der Kante: von, bis)."""
    R, L, tw = knuckle_radius(spec), spec.length, spec.tab
    c1 = R + tw / 2 + 1.0                                  # erster Zinken gleich hinter dem Auge
    c2 = L - tw / 2 - 1.5                                  # zweiter kurz vor dem Ende
    return [(c1 - tw / 2, c1 + tw / 2), (c2 - tw / 2, c2 + tw / 2)]


def knuckle_plate(spec: KnuckleSpec, step_deg: float = 10.0) -> list[np.ndarray]:
    """Ein Knöchel im Querschnitt (lokal: u entlang der Wand weg von der Kante, w von der Wandfläche nach
    außen; Achse bei (0, R)). Unterkante liegt auf der Wand, zwei Zinken reichen um die Materialstärke
    hinein, die Oberkante fällt vom Auge zum Ende hin ab. Rückgabe: [Außenkontur, Loch]."""
    R, L, t, he = knuckle_radius(spec), spec.length, spec.thickness, spec.end_height
    pts = [(0.0, 0.0)]
    for a, b in knuckle_tabs(spec):
        pts += [(a, 0.0), (a, -t), (b, -t), (b, 0.0)]
    pts += [(L, 0.0), (L, he)]
    # Tangente vom Endpunkt (L, he) an das Auge (Mitte (0, R), Radius R), obere Seite
    dist = math.hypot(L, he - R)
    tang = math.atan2(he - R, L) + math.acos(R / dist)      # Winkel des oberen Tangentenpunkts (von der Mitte)
    a0 = tang % (2 * math.pi)
    a1 = 1.5 * math.pi                                      # bis ganz unten (0, 0) gegen den Uhrzeigersinn
    if a1 < a0:
        a1 += 2 * math.pi
    n = max(6, int(math.ceil(math.degrees(a1 - a0) / step_deg)))
    pts += [(R * math.cos(w), R + R * math.sin(w)) for w in np.linspace(a0, a1, n + 1)[:-1]]
    r = knuckle_hole(spec) / 2
    hole = np.array([(r * math.cos(w), R + r * math.sin(w)) for w in np.radians(np.arange(0, 360, step_deg))])
    return [np.array(pts), hole]


def knuckle_hinge(rings_a, rings_b, spec: KnuckleSpec, tol: float = 0.5):
    """Knöchelscharnier an der längsten gemeinsamen Kante von Teil A und B.

    Rückgabe: (Schlitze für A, Schlitze für B, Knöchel-Konturen [Ringe je Knöchel], Stifte, Kantenlänge).
    Knöchel 1, 3, 5 … jedes Scharniers gehören zu A, 2, 4 … zu B."""
    t, n = spec.thickness, spec.knuckles
    if t <= 0 or n < 2:
        raise ValueError("Materialstärke muss größer als 0 sein und es braucht mindestens 2 Knöchel.")
    R = knuckle_radius(spec)
    (a1, b1), (a2, b2) = knuckle_tabs(spec)
    if not (b1 + 1 < a2 and spec.end_height < 2 * R and spec.end_height > 0):
        raise ValueError(f"Der Knöchel ist zu kurz für Auge und zwei Zinken – Knöchellänge mindestens "
                         f"{2 * R + 2 * spec.tab + 5:.0f} mm, Höhe am Ende kleiner als {2 * R:.1f} mm.")
    ra = [np.asarray(r, float) for r in rings_a]
    rb = [np.asarray(r, float) for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=n * t + 2)
    if not edges:
        raise ValueError("Keine gemeinsame Kante gefunden, die lang genug für das Scharnier ist. Die beiden Teile "
                         "Kante an Kante legen (z. B. mit Anordnen → Kanten aneinanderlegen).")
    e = edges[0]
    u = (e.p1 - e.p0) / e.length
    na = _material_normal(ra, e.p0, e.p1)                  # in Teil A hinein
    nb = _material_normal(rb, e.p0, e.p1)                  # in Teil B hinein
    span = n * t
    if spec.hinges >= 2:
        k = spec.hinges
        first, last = spec.inset, e.length - spec.inset - span
        starts = [first + (last - first) * j / (k - 1) for j in range(k)]   # gleichmäßig verteilt
        if last - first < (k - 1) * (span + 2):
            raise ValueError(f"Die Kante ist zu kurz für {k} Scharniere – weniger Scharniere oder Knöchel wählen "
                             "oder den Abstand vom Rand verkleinern.")
    else:
        starts = [(e.length - span) / 2]
    if starts[0] < 0:
        raise ValueError("Das Scharnier ist länger als die Kante – weniger Knöchel wählen.")
    c = spec.clearance
    slots_a: list[np.ndarray] = []
    slots_b: list[np.ndarray] = []
    for x0 in starts:
        for i in range(n):
            xa, xb = x0 + i * t - c / 2, x0 + (i + 1) * t + c / 2
            nrm, out = (na, slots_a) if i % 2 == 0 else (nb, slots_b)
            for a, b in knuckle_tabs(spec):
                ya, yb = a - c / 2, b + c / 2
                out.append(np.array([e.p0 + u * xx + nrm * yy for xx, yy in ((xa, ya), (xb, ya), (xb, yb), (xa, yb))]))
    for s_ in slots_a:
        if not inside(s_, ra).all():
            raise ValueError("Die Schlitze passen nicht in Teil A – Knöchellänge verkleinern.")
    for s_ in slots_b:
        if not inside(s_, rb).all():
            raise ValueError("Die Schlitze passen nicht in Teil B – Knöchellänge verkleinern.")
    plates = [knuckle_plate(spec) for _ in range(n * len(starts))]
    pins = [np.array([[0, 0], [span + 2 * t, 0], [span + 2 * t, t], [0, t]], float)
            for _ in starts] if spec.pin <= 0 else []
    return slots_a, slots_b, plates, pins, e.length

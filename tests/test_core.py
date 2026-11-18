"""Headless-Tests für den Kern (ohne GUI, ohne Laser).  Start:  python -m pytest tests  oder  python tests/test_core.py"""
import base64
import io
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tilaser.core import geometry as g
from tilaser.core import sender as snd
from tilaser.core.dxf_import import import_dxf
from tilaser.core.fill import hatch
from tilaser.core.gcode import MachineProfile, frame_gcode, generate
from tilaser.core.model import Project
from tilaser.core.storage import load_project, save_project
from tilaser.core.svg_import import import_svg, parse_path_d
from tilaser.core.text_import import default_font, text_to_polylines

TMP = tempfile.mkdtemp()

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 200 100">
  <rect x="10" y="10" width="180" height="80" fill="none" stroke="#ff0000"/>
  <circle cx="50" cy="50" r="20" fill="none" stroke="red"/>
  <g transform="translate(100,0)">
    <path d="M10,30 l20,0 a10,10 0 0 1 10,10 c0,10 -10,10 -20,10 z" fill="#0000ff"/>
  </g>
  <rect x="150" y="20" width="20" height="20" rx="5" style="fill:none;stroke:#000"/>
  <text x="0" y="0">ignoriert</text>
</svg>"""

DXF = """0
SECTION
2
HEADER
9
$INSUNITS
70
4
0
ENDSEC
0
SECTION
2
ENTITIES
0
LINE
8
Schnitt
10
0
20
0
11
50
21
0
0
CIRCLE
8
Schnitt
10
25
20
25
40
10
0
LWPOLYLINE
8
Gravur
90
4
70
1
10
0
20
0
10
40
20
0
42
1
10
40
20
20
10
0
20
20
0
ARC
8
Schnitt
10
0
20
0
40
5
50
0
51
90
0
SPLINE
8
Schnitt
71
3
72
8
73
4
40
0
40
0
40
0
40
0
40
1
40
1
40
1
40
1
10
0
20
0
10
10
20
20
10
20
20
-20
10
30
20
0
0
ENDSEC
0
EOF
"""


def test_path_parser():
    polys = parse_path_d("M0,0 L10,0 10,10 H0 Z m20,0 h5 v5", g.identity())
    assert len(polys) == 2
    assert polys[0].closed and len(polys[0].pts) == 4
    assert not polys[1].closed
    assert np.allclose(polys[1].pts[-1], [25, 5])
    # kompakte Bogen-Flags
    polys = parse_path_d("M0 0a5 5 0 1010 0", g.identity())
    assert np.allclose(polys[0].pts[-1], [10, 0], atol=1e-6)


def test_svg():
    p = os.path.join(TMP, "t.svg")
    open(p, "w").write(SVG)
    paths, warn = import_svg(p)
    assert any("Text" in w for w in warn)
    colors = {ip.color for ip in paths}
    assert colors == {"#ff0000", "#0000ff", "#000000"}
    rect = paths[0].poly
    bb = g.bbox([rect])
    assert np.allclose(bb, (5, 5, 95, 45), atol=1e-6), bb       # 0.5 mm pro Einheit, Y gespiegelt
    circ = paths[1].poly
    r = np.hypot(*(circ.pts - [25, 25]).T)
    assert np.allclose(r, 10, atol=0.005)          # Bézier-Näherung des Kreises (< 0,03 %)
    assert any(ip.filled for ip in paths)


def test_dxf():
    p = os.path.join(TMP, "t.dxf")
    open(p, "w").write(DXF)
    paths, warn = import_dxf(p)
    keys = {ip.key for ip in paths}
    assert keys == {"DXF:Schnitt", "DXF:Gravur"}
    lw = [ip.poly for ip in paths if ip.key == "DXF:Gravur"][0]
    assert lw.closed
    bb = g.bbox([lw])
    # Bulge 1 am Segment (40,0)->(40,20): Halbkreis gegen Uhrzeigersinn, wölbt sich nach rechts bis x=50
    assert np.allclose(bb, (0, 0, 50, 20), atol=0.05), bb
    spl = [ip.poly for ip in paths if ip.key == "DXF:Schnitt" and not ip.poly.closed][-1]
    assert np.allclose(spl.pts[0], [0, 0]) and np.allclose(spl.pts[-1], [30, 0])


def test_text():
    f = default_font()
    if not f:
        print("   (übersprungen: keine Systemschrift gefunden)")
        return
    polys = text_to_polylines("Ab\n8", f, 10)
    assert len(polys) >= 5 and all(p.closed for p in polys)
    bb = g.bbox(polys)
    assert bb[3] < 10 and bb[1] < -5


def test_fill_and_nesting():
    outer = g.Polyline(np.array([[0, 0], [10, 0], [10, 10], [0, 10]]), True)
    inner = g.Polyline(np.array([[3, 3], [7, 3], [7, 7], [3, 7]]), True)
    segs = hatch([outer, inner], 1.0, 0)
    assert len(segs) == 10 + 4      # 6 Zeilen voll, 4 Zeilen mit Loch -> 2 Segmente
    total = sum(np.hypot(*(s[1] - s[0])) for s in segs)
    assert abs(total - (100 - 16)) < 1e-6
    order = g.order_paths([outer, inner])
    assert order[0] is not outer and np.allclose(g.bbox([order[0]]), (3, 3, 7, 7))
    segs45 = hatch([outer], 0.5, 45)
    area = sum(np.hypot(*(s[1] - s[0])) for s in segs45) * 0.5
    assert abs(area - 100) < 3


def make_project():
    pr = Project()
    pr.add_vector_import("svg", import_svg(os.path.join(TMP, "t.svg"))[0])
    pr.add_vector_import("dxf", import_dxf(os.path.join(TMP, "t.dxf"))[0], center_at=(150, 100))
    img = np.full((50, 80), 255, np.uint8)
    img[10:40, 10:70] = np.linspace(0, 250, 60, dtype=np.uint8)[None, :]
    pr.add_raster("bild", img, 40, 25, center_at=(60, 120))
    return pr


def test_generate_dialects():
    pr = make_project()
    for d in ("grbl", "marlin", "smoothie", "generic"):
        prof = MachineProfile()
        prof.apply_dialect(d)
        res = generate(pr, prof)
        txt = res.text
        assert "G1" in txt and "G0" in txt
        assert res.time_s > 0
        assert len(res.burn) >= 3
        if d == "grbl":
            assert "M4 S0" in txt and "M3" not in txt
            ss = [float(t[1:]) for l in res.lines if l.startswith("G1") for t in l.split() if t.startswith("S")]
            assert max(ss) <= 1000 and min(ss) >= 0
        if d == "marlin":
            assert "M3 S255" in txt and "M5" in txt
            assert not any(l.startswith("G1") and " S" in l for l in res.lines)
        if d == "smoothie":
            ss = [float(t[1:]) for l in res.lines if l.startswith("G1") for t in l.split() if t.startswith("S")]
            assert max(ss) <= 1.0
        open(os.path.join(TMP, f"out_{d}.gcode"), "w").write(txt)


def test_raster_geometry():
    pr = Project()
    img = np.full((20, 20), 255, np.uint8)
    img[:, :10] = 0                      # linke Hälfte schwarz
    it = pr.add_raster("b", img, 10, 10, center_at=(50, 50))
    layer = pr.layer(it.layer_id)
    layer.dither, layer.interval, layer.overscan, layer.power = "threshold", 0.5, 0, 100
    res = generate(pr, MachineProfile())
    burn = np.vstack([b for _, b in res.burn])
    assert np.isclose(burn[:, :, 0].min(), 45) and np.isclose(burn[:, :, 0].max(), 50)
    assert np.isclose(burn[:, :, 1].min(), 45.25) and np.isclose(burn[:, :, 1].max(), 54.75)
    assert len(burn) == 20


def test_transform_and_save():
    pr = make_project()
    it = pr.items[0]
    w0 = it.world_bbox()
    it.rotation, it.scale = 90, 2
    w1 = it.world_bbox()
    assert abs((w1[3] - w1[1]) - 2 * (w0[2] - w0[0])) < 1e-6
    p = os.path.join(TMP, "p.tlp")
    save_project(pr, p)
    pr2 = load_project(p)
    assert len(pr2.items) == 3 and len(pr2.layers) == len(pr.layers)
    assert np.allclose(pr2.items[0].world_bbox(), w1, atol=1e-3)
    assert generate(pr2, MachineProfile()).lines


def test_frame():
    lines = frame_gcode((10, 10, 50, 30), MachineProfile())
    assert lines.count("G1 X10 Y10 F3000") == 1


def test_group_ungroup():
    pr = Project()
    sq = lambda x0, y0, w: g.Polyline(np.array([[x0, y0], [x0 + w, y0], [x0 + w, y0 + w], [x0, y0 + w]]), True)
    # Platte 0..50 mit zwei Löchern, darin im Loch ein Inselteil, daneben ein zweites Teil und eine offene Linie
    from tilaser.core.model import ImportedPath
    shapes = [sq(0, 0, 50), sq(5, 5, 20), sq(10, 10, 5), sq(30, 30, 10), sq(60, 0, 10),
              g.Polyline(np.array([[0, 60], [50, 60]]), False)]
    it = pr.add_vector_import("t", [ImportedPath(p, "#000000", "#000000") for p in shapes])
    it.x += 100
    it.rotation = 90
    before = sorted(np.round(g.bbox([p]), 6).tolist() for p in it.world_paths())
    parts = pr.ungroup(it)
    sizes = sorted(len(p.paths) for p in parts)
    assert sizes == [1, 1, 1, 3], sizes          # Platte+2 Löcher, Insel, Teil, Linie
    after = sorted(np.round(g.bbox([p]), 6).tolist() for it2 in parts for p in it2.world_paths())
    assert before == after                       # Geometrie unverändert
    plate = next(p for p in parts if len(p.paths) == 3)
    singles = pr.ungroup(plate)                  # zweite Stufe: einzelne Konturen
    assert len(singles) == 3 and all(len(p.paths) == 1 for p in singles)
    parts = pr.items[:]
    grp = pr.group(parts)
    assert len(pr.items) == 1 and len(grp.paths) == 6
    new_layer = pr.add_layer("Rot", "#ff0000")
    pr.assign_layer([grp], new_layer.id)
    assert grp.layer_ids() == {new_layer.id}


def test_presets_merge():
    from tilaser.core import storage
    storage.USER_PRESETS = os.path.join(TMP, "presets.json")
    defaults = storage.load_presets()
    assert any(p.get("machine") == "AlgoLaser DIY KIT MK2 (20 W)" for p in defaults)
    assert all(p["mode"] in ("cut", "fill", "raster") and 0 < p["speed"] and 0 < p["power"] <= 100 for p in defaults)
    first = defaults[0]["name"]
    mine = dict(defaults[1], name="Mein Preset", speed=123)
    storage.save_presets(defaults[1:] + [mine])         # erstes Standard-Preset gelöscht
    again = storage.load_presets()
    names = [p["name"] for p in again]
    assert first not in names and "Mein Preset" in names and len(again) == len(defaults)
    t = storage.load_machine_templates()
    prof = MachineProfile.from_dict(t[0])
    assert prof.dialect == "grbl" and prof.s_max == 1000 and prof.bed_w == 400


def _sq(x0, y0, w):
    return g.Polyline(np.array([[x0, y0], [x0 + w, y0], [x0 + w, y0 + w], [x0, y0 + w]], float), True)


def test_editing():
    from tilaser.core import editing as ed
    from tilaser.core.model import ImportedPath
    pr = Project()
    a = pr.add_vector_import("a", [ImportedPath(_sq(0, 0, 10), "#000000", "#000000")])
    b = pr.add_vector_import("b", [ImportedPath(_sq(30, 5, 20), "#000000", "#000000")])
    c = pr.add_vector_import("c", [ImportedPath(_sq(80, 50, 10), "#000000", "#000000")])
    undo = ed.UndoStack()
    undo.push(pr, "Ausrichten")
    ed.align([a, b, c], "left")
    assert all(abs(it.world_bbox()[0]) < 1e-9 for it in (a, b, c))
    ed.align([a, b, c], "top")
    assert all(abs(it.world_bbox()[3] - 60) < 1e-9 for it in (a, b, c))
    restored = undo.do_undo(pr)
    assert np.allclose(restored.items[1].world_bbox(), (30, 5, 50, 25))
    assert undo.can_redo()
    pr = restored
    a, b, c = pr.items
    # Verteilen: gleiche Abstände
    ed.distribute([a, b, c], "x")
    xs = sorted(it.world_bbox() for it in (a, b, c))
    assert abs((xs[1][0] - xs[0][2]) - (xs[2][0] - xs[1][2])) < 1e-9
    # Arrays
    n0 = len(pr.items)
    new = ed.grid_array(pr, [a], 3, 2, 5, 5)
    assert len(new) == 5 and len(pr.items) == n0 + 5
    assert abs(max(it.world_bbox()[2] for it in [a] + new) - (a.world_bbox()[0] + 3 * 10 + 2 * 5)) < 1e-9
    circ = ed.circular_array(pr, [a], 4, (a.x + 20, a.y))
    assert len(circ) == 3 and abs(circ[1].x - (a.x + 40)) < 1e-9 and circ[0].rotation == 90
    # Anfasser-Transformation: um 2 skalieren, um 90° drehen
    orig = [(a.x, a.y, a.scale, a.rotation)]
    ed.transform_items([a], orig, 2.0, 90, (a.x - 5, a.y - 5))
    assert abs(a.scale - 2) < 1e-9 and abs(a.rotation - 90) < 1e-9
    # Kopie teilt Geometrie, hat aber eigene ID
    k = ed.clone_item(a)
    assert k.id != a.id and k.paths[0].poly is a.paths[0].poly


def test_kerf_and_tabs():
    from tilaser.core.offset import add_tabs, apply_kerf
    plate, hole = _sq(0, 0, 40), _sq(10, 10, 10)
    out = apply_kerf([plate, hole], 0.2)
    bbs = sorted((g.bbox([p]) for p in out), key=lambda b: b[2] - b[0])
    assert np.allclose(bbs[1], (-0.1, -0.1, 40.1, 40.1), atol=1e-3)   # außen größer
    assert np.allclose(bbs[0], (10.1, 10.1, 19.9, 19.9), atol=1e-3)   # Loch kleiner
    tabbed = add_tabs([plate, hole], 4, 1.0)
    pieces = [p for p in tabbed if not p.closed]
    assert len(pieces) == 4 and any(p.closed for p in tabbed)           # Loch ohne Stege
    assert abs(sum(p.length() for p in pieces) - (160 - 4)) < 1e-6
    pr = Project()
    from tilaser.core.model import ImportedPath
    pr.add_vector_import("p", [ImportedPath(plate, "#ff0000", "#ff0000"), ImportedPath(hole, "#ff0000", "#ff0000")])
    pr.layers[0].kerf, pr.layers[0].tabs = 0.15, 3
    res = generate(pr, MachineProfile())
    assert sum(1 for l in res.lines if l.startswith("G0")) >= 4


def test_image_and_trace():
    from tilaser.core.raster import dither
    from tilaser.core.trace import trace_image
    grad = np.tile(np.linspace(0, 255, 64, dtype=np.uint8), (32, 1))
    for m in ("floyd", "jarvis", "stucki", "atkinson", "threshold", "grayscale"):
        d = dither(grad, m)
        assert d.shape == grad.shape and 0.35 < d.mean() < 0.65, (m, d.mean())
    assert dither(grad, "threshold", brightness=50).mean() < dither(grad, "threshold").mean()
    img = np.full((100, 100), 255, np.uint8)
    img[20:80, 20:80] = 0
    img[40:60, 40:60] = 255                       # Ring
    img[5:8, 90:93] = 0                           # Fleck < min_area
    polys = trace_image(img, 50, 50, min_area_px=20)
    assert len(polys) == 2 and all(p.closed for p in polys)
    bbs = sorted((g.bbox([p]) for p in polys), key=lambda b: b[2] - b[0])
    assert np.allclose(bbs[1], (-15, -15, 15, 15), atol=0.01) and np.allclose(bbs[0], (-5, -5, 5, 5), atol=0.01)
    assert all(len(p.pts) == 4 for p in polys)    # Quadrate auf 4 Ecken vereinfacht


def test_material_test_and_origin():
    from tilaser.core.gcode import origin_offset
    from tilaser.core.materialtest import MaterialTest, build_project
    t = MaterialTest(cols=4, rows=3)
    pr = build_project(t)
    cells = [l for l in pr.layers if l.name.startswith("P")]
    assert len(cells) == 12
    assert {round(l.power) for l in cells} == {10, 40, 70, 100}
    res = generate(pr, MachineProfile())
    assert res.time_s > 0 and res.moves is not None and len(res.moves) > 0
    assert np.all(np.diff(res.moves[:, 5]) >= -1e-9)          # Zeitachse monoton
    bb = pr.bbox()
    dx, dy = origin_offset(bb, "bl", (100, 100))
    res2 = generate(pr, MachineProfile(), offset=(dx, dy))
    burn = np.vstack([b for _, b in res2.burn])
    assert abs(burn[:, :, 0].min() - 100) < 0.5 and abs(burn[:, :, 1].min() - 100) < 0.5
    only = generate(pr, MachineProfile(), only_ids=[pr.items[0].id])
    assert len(only.lines) < len(res.lines)


def test_rotary_and_resume():
    from tilaser.core.gcode import resume_lines
    pr = Project()
    from tilaser.core.model import ImportedPath
    pr.add_vector_import("p", [ImportedPath(_sq(0, 0, 10), "#000000", "#000000")])
    prof = MachineProfile(rotary=True, rotary_mm_per_rev=40, rotary_roller_d=40 / np.pi)
    assert abs(prof.y_scale - 1.0) < 1e-9
    prof.rotary_roller_d = 20 / np.pi
    lines = generate(pr, prof).lines
    ys = [float(t[1:]) for l in lines if l.startswith("G1") for t in l.split() if t.startswith("Y")]
    assert abs(max(ys) - 20) < 1e-6                           # 10 mm Höhe -> 20 mm Y-Weg
    full = generate(pr, MachineProfile()).lines
    res = resume_lines(full, 8, MachineProfile())
    assert res[0].startswith("; Fortsetzen") and "M4 S1000" in res and res[-1] == full[-1]


def test_tcp_transport():
    import socket, threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def server():
        conn, _ = srv.accept()
        conn.sendall(b"\xff\xfb\x01Grbl 1.1h ['$' for help]\r\n")
        buf = b""
        while True:
            d = conn.recv(1024)
            if not d:
                break
            buf += d.replace(b"?", b"")
            if b"?" in d:
                conn.sendall(b"<Idle|WPos:0.000,0.000,0.000>\r\n")
            while b"\n" in buf:
                _, buf = buf.split(b"\n", 1)
                conn.sendall(b"ok\r\n")
        conn.close()

    threading.Thread(target=server, daemon=True).start()
    states, fws = [], []
    c = snd.LaserConnection(on_state=states.append, on_firmware=lambda fw, t: fws.append(fw))
    c.connect_tcp("127.0.0.1", port)
    t0 = time.time()
    while "idle" not in states and time.time() - t0 < 5:
        time.sleep(0.01)
    c.start_job([f"G1 X{i}" for i in range(50)])
    t0 = time.time()
    while "done" not in states and time.time() - t0 < 5:
        time.sleep(0.01)
    c.disconnect()
    assert fws == ["grbl"] and "done" in states


def test_shapes_and_join():
    from tilaser.core import shapes
    r = shapes.rectangle(20, 10, 3)
    assert r.closed and np.allclose(g.bbox([r]), (-10, -5, 10, 5), atol=1e-6)
    e = shapes.ellipse(30, 10)
    assert np.allclose(g.bbox([e]), (-15, -5, 15, 5), atol=0.05)
    st = shapes.polygon(5, 40, 40, star=0.5)
    assert len(st.pts) == 10 and np.allclose(g.bbox([st]), (-20, -20, 20, 20), atol=1e-6)
    a = g.Polyline(np.array([[0, 0], [10, 0]]), False)
    b = g.Polyline(np.array([[10, 10], [10, 0.01]]), False)       # umgekehrt, kleine Lücke
    c = g.Polyline(np.array([[10, 10], [0, 10], [0, 0]]), False)
    joined = g.join_paths([a, b, c], tol=0.05)
    assert len(joined) == 1 and joined[0].closed and len(joined[0].pts) == 4


def test_boolean_offset():
    from tilaser.core import boolean as bo
    from tilaser.core.offset import pyclipper
    big, small = _sq(0, 0, 20), _sq(10, 10, 20)
    off = bo.offset_shapes([big], 2)
    assert np.allclose(g.bbox(off), (-2, -2, 22, 22), atol=0.01)
    if pyclipper is None:
        try:
            bo.boolean([big], [small], "union")
            assert False
        except bo.BooleanUnavailable:
            print("   (Boolesche Operationen übersprungen: pyclipper fehlt)")
        return
    area = lambda ps: sum(abs(0.5 * np.sum(p.pts[:, 0] * np.roll(p.pts[:, 1], -1) - np.roll(p.pts[:, 0], -1) * p.pts[:, 1])) for p in ps)
    assert abs(area(bo.boolean([big], [small], "union")) - 700) < 1e-3
    assert abs(area(bo.boolean([big], [small], "difference")) - 300) < 1e-3
    assert abs(area(bo.boolean([big], [small], "intersection")) - 100) < 1e-3
    ring = bo.boolean([big], [_sq(5, 5, 10)], "difference")
    assert len(ring) == 2


def test_sublayers_and_z():
    from tilaser.core.model import ImportedPath, Layer
    pr = Project()
    pr.add_vector_import("p", [ImportedPath(_sq(0, 0, 10), "#000000", "#000000", True)])
    layer = pr.layers[0]
    assert layer.mode == "fill"
    sub = layer.add_sub("cut")
    sub.passes, sub.z_step, sub.power = 3, 0.5, 90
    res = generate(pr, MachineProfile())
    txt = res.text
    assert "Unterebene 1" in txt and txt.count("G0 Z-0.5") == 2 and "G0 Z1" in txt
    assert any(" S900" in l for l in res.lines)
    d = layer.to_dict()
    back = Layer.from_dict(d)
    assert len(back.subs) == 1 and back.subs[0].z_step == 0.5


def test_passes_per_path():
    from tilaser.core.model import ImportedPath
    pr = Project()
    pr.add_vector_import("p", [ImportedPath(_sq(0, 0, 10), "#000000", "#000000"),
                               ImportedPath(_sq(50, 0, 10), "#000000", "#000000"),
                               ImportedPath(g.Polyline(np.array([[100.0, 0], [120, 0]]), False), "#000000", "#000000")])
    layer = pr.layers[0]
    layer.passes, layer.z_step = 3, 0.5

    def order(lines):
        """X-Bereich (0, 50, 100) jeder Brennfahrt, zusammengefasst je Kontur."""
        seq = []
        for l in lines:
            if l.startswith("G1") and " X" in l:
                x = float(next(t[1:] for t in l.split() if t.startswith("X")))
                k = 0 if x < 30 else 50 if x < 80 else 100
                if not seq or seq[-1][0] != k:
                    seq.append([k, 0])
            elif l.startswith("G0") and " X" in l and seq:
                seq[-1][1] += 1                       # Leerfahrt innerhalb derselben Kontur zählt mit
        return [k for k, _ in seq]

    res = generate(pr, MachineProfile())
    assert order(res.lines) == [0, 50, 100] and res.text.count("G0 Z-0.5") == 3 * 2   # je Kontur fertig
    assert res.text.count("G0 Z1") == 3                                              # Z je Kontur zurück
    open_burns = [l for l in res.lines if l.startswith("G1") and " X1" in l and ("X100" in l or "X120" in l)]
    assert [("X120" in l) for l in open_burns] == [True, False, True]                # offen: hin, zurück, hin
    seq = order(generate(pr, MachineProfile(passes_per_path=False)).lines)                                # Durchgang für Durchgang
    assert len(seq) > 3                                 # Konturen wechseln zwischen den Durchgängen
    assert MachineProfile.from_dict({"name": "alt"}).passes_per_path                 # alte Profile: an


def test_export_roundtrip():
    from tilaser.core.export import export_dxf, export_svg
    pr = make_project()
    p_svg, p_dxf = os.path.join(TMP, "ex.svg"), os.path.join(TMP, "ex.dxf")
    export_svg(pr, p_svg, 400, 400)
    export_dxf(pr, p_dxf)
    vec = [p for it in pr.items if it.kind == "vector" for p in it.world_paths()]
    ref = g.bbox(vec)
    back_svg, _ = import_svg(p_svg)
    back_dxf, _ = import_dxf(p_dxf)
    assert len(back_dxf) == len(vec)
    assert np.allclose(g.bbox([ip.poly for ip in back_dxf]), ref, atol=1e-3)
    vb = g.bbox([ip.poly for ip in back_svg])
    assert np.allclose(vb, ref, atol=1e-3), (vb, ref)       # Bild-Element wird beim SVG-Import ignoriert


def test_camera():
    from tilaser.core.camera import apply_h, homography, rectify
    mm = np.array([[30, 30], [370, 30], [370, 370], [30, 370]], float)
    H_true = np.array([[0.8, 0.05, 20], [-0.02, -0.9, 400], [0.0001, 0.00005, 1]])
    px = apply_h(np.linalg.inv(H_true), mm)                 # simulierte Kamerapixel
    H = homography(px, mm)
    assert np.allclose(apply_h(H, px), mm, atol=1e-6)
    cam = np.zeros((500, 500), np.uint8)
    out = rectify(cam, H, 400, 400, 1.0)
    assert out.shape == (400, 400)


def test_bezier():
    from tilaser.core import shapes
    polys = parse_path_d("M0,0 C10,20 30,20 40,0 L40,-10 Q20,-30 0,-10 Z", g.identity())
    p = polys[0]
    assert p.bez is not None and len(p.bez.anchors) == 4 and p.closed
    assert np.allclose(p.pts, p.bez.flatten())
    assert not p.bez.is_line(0) and p.bez.is_line(1) and not p.bez.is_line(2)
    # Bogen -> Kubiken, Endpunkt exakt
    arc = parse_path_d("M0,0 A10,10 0 0 1 20,0", g.identity())[0]
    assert np.allclose(arc.bez.anchors[-1], [20, 0]) and len(arc.bez.anchors) == 3
    mid = arc.pts[len(arc.pts) // 2]
    assert abs(np.hypot(mid[0] - 10, mid[1]) - 10) < 0.01
    # Teilen erhält die Form
    b = p.bez.copy()
    before = b.flatten(0.001)
    b.split(0, 0.5)
    after = b.flatten(0.001)
    assert len(b.anchors) == 5 and abs(g.Polyline(before, True).length() - g.Polyline(after, True).length()) < 0.01
    i, t, d = b.nearest((20, 15))
    assert i in (0, 1) and d < 0.5
    # Transformation, Gruppieren, Speichern erhalten die Kurven
    from tilaser.core.model import ImportedPath
    pr = Project()
    it = pr.add_vector_import("k", [ImportedPath(p, "#000000", "#000000")])
    it.rotation, it.scale = 30, 2
    assert it.world_paths()[0].bez is not None
    parts = pr.ungroup(pr.group([it, pr.add_vector_import("r", [ImportedPath(shapes.rectangle(10, 5, 1), "#ff0000", "#ff0000")])]))
    assert all(vp.poly.bez is not None for q in parts for vp in q.paths)
    fpath = os.path.join(TMP, "bez.tlp")
    save_project(pr, fpath)
    back = load_project(fpath)
    for a_it, b_it in zip(pr.items, back.items):
        for va, vb in zip(a_it.paths, b_it.paths):
            assert vb.poly.bez is not None and np.allclose(va.poly.bez.anchors, vb.poly.bez.anchors, atol=1e-3)
            assert abs(va.poly.length() - vb.poly.length()) < 0.01, (va.poly.length(), vb.poly.length())
    # Text: Buchstabe O mit wenigen Ankern
    f = default_font()
    if f:
        o = text_to_polylines("O", f, 20)
        assert all(q.bez is not None for q in o) and max(len(q.bez.anchors) for q in o) < 20


def _mini_pdf(content: bytes, compress=False) -> bytes:
    import zlib
    stream = zlib.compress(content) if compress else content
    filt = b"/Filter /FlateDecode " if compress else b""
    form = b"0 0 1 RG 0 0 72 72 re S"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 288 288] >>",
            b"<< /Type /Page /Parent 2 0 R /Resources << /XObject << /Fm1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< " + filt + b"/Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] /Matrix [1 0 0 1 144 0] /Length "
            + str(len(form)).encode() + b" >>\nstream\n" + form + b"\nendstream"]
    out = b"%PDF-1.4\n"
    for i, o in enumerate(objs, 1):
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    return out + b"trailer << /Root 1 0 R >>\n%%EOF\n"


def test_pdf_import():
    from tilaser.core.pdf_import import import_pdf
    content = (b"q 1 0 0 RG 2 w 72 72 m 144 72 l 144 144 l h S Q\n"
               b"0 g 0 0 m 72 0 72 72 0 72 c f\n"
               b"BT /F1 12 Tf (Hallo) Tj ET\n/Fm1 Do\n")
    for comp in (False, True):
        p = os.path.join(TMP, f"t{comp}.pdf")
        open(p, "wb").write(_mini_pdf(content, comp))
        paths, warn = import_pdf(p)
        reds = [ip for ip in paths if ip.color == "#ff0000"]
        assert len(reds) == 1 and reds[0].poly.closed
        assert np.allclose(g.bbox([reds[0].poly]), (25.4, 25.4, 50.8, 50.8), atol=1e-6)
        fills = [ip for ip in paths if ip.filled]
        assert len(fills) == 1 and not fills[0].poly.bez.is_line(0)
        blue = [ip for ip in paths if ip.color == "#0000ff"]            # aus dem Formular-XObject
        assert len(blue) == 1 and np.allclose(g.bbox([blue[0].poly]), (50.8, 0, 76.2, 25.4), atol=1e-6)
    # PDF von matplotlib (Objektströme, Komprimierung) – nur wenn matplotlib installiert ist
    try:
        import matplotlib
    except ImportError:
        print("   (PDF-Test mit matplotlib übersprungen: matplotlib fehlt)")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(2, 2))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.add_patch(plt.Circle((0.5, 0.5), 0.3, fill=False, color="red"))
    ax.plot([0.1, 0.9], [0.1, 0.1], color="blue")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    pm = os.path.join(TMP, "mpl.pdf")
    fig.savefig(pm)
    paths, _ = import_pdf(pm)
    circ = [ip for ip in paths if ip.color == "#ff0000"]
    assert circ and abs((g.bbox([circ[0].poly])[2] - g.bbox([circ[0].poly])[0]) - 0.6 * 50.8) < 0.5


def _pdf(objs: list[bytes]) -> bytes:
    out = b"%PDF-1.5\n"
    for i, o in enumerate(objs, 1):
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    return out + b"trailer << /Root 1 0 R >>\n%%EOF\n"


def _stream(data: bytes, extra=b"") -> bytes:
    import zlib
    z = zlib.compress(data)
    return b"<< /Filter /FlateDecode /Length " + str(len(z)).encode() + b" " + extra + b">>\nstream\n" + z + b"\nendstream"


def _text_pdf(font_obj: bytes, extra_objs: list[bytes], content: bytes) -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 400 200] >>",
            b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            _stream(content), font_obj] + extra_objs
    return _pdf(objs)


def test_pdf_text():
    from tilaser.core.pdf_import import import_pdf
    font = default_font()
    if not font:
        return
    from fontTools.ttLib import TTFont
    ttf = open(font, "rb").read()
    tt = TTFont(font)
    cmap = tt.getBestCmap()
    order = {n: i for i, n in enumerate(tt.getGlyphOrder())}
    content = b"BT 0 0 1 rg /F1 20 Tf 10 20 Td (Hallo) Tj 0 3 Tr 0 40 Td (Unsichtbar) Tj ET"
    # 1) eingebettete TrueType-Schrift (einfach, WinAnsi)
    pdf = _text_pdf(b"<< /Type /Font /Subtype /TrueType /BaseFont /Test /Encoding /WinAnsiEncoding "
                    b"/FontDescriptor 6 0 R >>",
                    [b"<< /Type /FontDescriptor /FontName /Test /FontFile2 7 0 R >>", _stream(ttf)], content)
    p = os.path.join(TMP, "text1.pdf")
    open(p, "wb").write(pdf)
    paths, warn = import_pdf(p)
    assert len(paths) == 7 and all(ip.filled and ip.color == "#0000ff" for ip in paths), (len(paths), warn)
    assert all(ip.poly.bez is not None for ip in paths)
    bb = g.bbox([ip.poly for ip in paths])
    assert abs(bb[0] - 10 * 25.4 / 72) < 1.0 and 4 < bb[3] - bb[1] < 7.5, bb     # 20 pt ≈ 7 mm Schriftgröße
    # 2) nicht eingebettete Standardschrift -> Ersatz
    p2 = os.path.join(TMP, "text2.pdf")
    open(p2, "wb").write(_text_pdf(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", [], content))
    paths2, warn2 = import_pdf(p2)
    assert len(paths2) == 7 and any("Ersatzschrift" in w for w in warn2)
    # 3) Type0 / Identity-H mit Glyphen-IDs
    gids = [order[cmap[ord(c)]] for c in "Hi"]
    hexs = "".join(f"{x:04X}" for x in gids).encode()
    c3 = b"BT /F1 20 Tf 10 20 Td <" + hexs + b"> Tj ET"
    p3 = os.path.join(TMP, "text3.pdf")
    open(p3, "wb").write(_text_pdf(
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Test /Encoding /Identity-H /DescendantFonts [6 0 R] >>",
        [b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Test /CIDToGIDMap /Identity /DW 600 "
         b"/W [0 [600]] /FontDescriptor 7 0 R >>",
         b"<< /Type /FontDescriptor /FontName /Test /FontFile2 8 0 R >>", _stream(ttf)], c3))
    paths3, _ = import_pdf(p3)
    assert len(paths3) == 3                                   # H (1) + i (2)
    # 4) CFF-Schrift (Type1C), mit fontTools erzeugt
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    pen = T2CharStringPen(600, None)
    pen.moveTo((100, 0)); pen.lineTo((500, 0)); pen.lineTo((300, 700)); pen.closePath()
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({65: "A"})
    nd = T2CharStringPen(500, None).getCharString()
    fb.setupCFF("TestCFF", {"FullName": "TestCFF"}, {".notdef": nd, "A": pen.getCharString()}, {})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (600, 100)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestCFF", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    cff_bytes = fb.font["CFF "].compile(fb.font)
    p4 = os.path.join(TMP, "text4.pdf")
    open(p4, "wb").write(_text_pdf(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /TestCFF /FirstChar 65 /LastChar 65 /Widths [600] "
        b"/FontDescriptor 6 0 R >>",
        [b"<< /Type /FontDescriptor /FontName /TestCFF /FontFile3 7 0 R >>",
         _stream(cff_bytes, b"/Subtype /Type1C ")],
        b"BT /F1 72 Tf 0 0 Td (AA) Tj ET"))
    paths4, w4 = import_pdf(p4)
    assert len(paths4) == 2, w4
    b4 = sorted((g.bbox([ip.poly]) for ip in paths4), key=lambda b: b[0])
    assert np.allclose(b4[0], (100 / 1000 * 25.4, 0, 500 / 1000 * 25.4, 700 / 1000 * 25.4), atol=0.01)
    assert abs(b4[1][0] - b4[0][0] - 600 / 1000 * 25.4) < 0.01         # Vorschub aus /Widths


def test_eps_via_converter():
    if os.name == "nt":
        return
    from tilaser.core import eps_import
    d = tempfile.mkdtemp()
    pdf = os.path.join(d, "vorlage.pdf")
    open(pdf, "wb").write(_mini_pdf(b"1 0 0 RG 0 0 72 72 re S\n"))
    fake = os.path.join(d, "gs")
    open(fake, "w").write("#!" + sys.executable + "\nimport sys, shutil\n"
                          "out = [a for a in sys.argv if a.startswith('-sOutputFile=')][0].split('=', 1)[1]\n"
                          f"shutil.copy({pdf!r}, out)\n")
    os.chmod(fake, 0o755)
    old = os.environ["PATH"]
    os.environ["PATH"] = d + os.pathsep + old
    try:
        eps = os.path.join(d, "x.eps")
        open(eps, "w").write("%!PS-Adobe-3.0 EPSF-3.0\n")
        paths, warn = eps_import.import_eps(eps)
    finally:
        os.environ["PATH"] = old
    assert any("Ghostscript" in w for w in warn)
    assert any(ip.color == "#ff0000" for ip in paths)


def test_text_on_path_and_lens():
    from tilaser.core.camera import _distort, fit_lens, marker_positions, rectify
    from tilaser.core.text_import import text_on_path
    f = default_font()
    if f:
        circle = g.ellipse_arc_points(0, 0, 50, 50, 0, 0, 2 * np.pi)
        polys = text_on_path("RUNDHERUM", f, 8, circle, closed=True, align="center", distance=2)
        assert len(polys) >= 9
        r = [np.hypot(*p.pts.mean(axis=0)) for p in polys]
        assert all(50 < v < 60 for v in r), r
        c = np.vstack([p.pts for p in polys]).mean(axis=0)
        assert abs(c[0]) < 5 and c[1] > 35, c                    # oben mittig
        line = np.array([[0, 0], [100, 0]], float)
        flat = text_on_path("AB", f, 10, line)
        bb = g.bbox(flat)
        assert bb[1] > -1 and bb[0] >= -0.5 and bb[2] < 20
    # Linsenkorrektur: simulierte Tonnenverzeichnung wird erkannt
    mm = np.array(marker_positions(400, 400, 30, 9), float)
    size = (1000, 1000)
    H_true = np.array([[2.0, 0.1, 100], [0.05, -2.0, 900], [0.0001, 0.0, 1.0]])
    from tilaser.core.camera import apply_h
    pu = apply_h(np.linalg.inv(np.linalg.inv(H_true)), mm)       # mm -> unverzerrte Pixel
    pu = apply_h(H_true, mm)
    k_true = -0.12
    pd = _distort(pu, k_true, size)
    k1, H, err = fit_lens(pd, mm, size)
    assert abs(k1 - k_true) < 0.01 and err < 0.05, (k1, err)
    out = rectify(np.full((1000, 1000, 3), 128, np.uint8), H, 400, 400, 1.0, k1)
    assert out.shape == (400, 400, 3) and abs(int(out[200, 200, 0]) - 128) <= 1


def test_dxf_curves():
    p = os.path.join(TMP, "t.dxf")
    open(p, "w").write(DXF)
    paths, _ = import_dxf(p)
    circ = [ip.poly for ip in paths if ip.poly.closed and ip.key == "DXF:Schnitt"][0]
    assert circ.bez is not None and len(circ.bez.anchors) == 4
    r = np.hypot(*(circ.pts - [25, 25]).T)
    assert np.allclose(r, 10, atol=0.005)
    lw = [ip.poly for ip in paths if ip.key == "DXF:Gravur"][0]
    assert lw.bez is not None and len(lw.bez.anchors) == 5 and not lw.bez.is_line(1)   # Halbkreis = 2 Kubiken
    assert np.allclose(g.bbox([lw]), (0, 0, 50, 20), atol=0.01)
    arc = [ip.poly for ip in paths if not ip.poly.closed and ip.poly.bez is not None
           and len(ip.poly.bez.anchors) == 2 and not ip.poly.bez.is_line(0)][0]
    assert np.allclose(arc.pts[0], [5, 0]) and np.allclose(arc.pts[-1], [0, 5], atol=1e-9)


def _min_dist(pa, pb):
    """Kleinster Abstand zwischen zwei Konturmengen (dicht abgetastet)."""
    def sample(polys):
        out = []
        for p in polys:
            lp = p.loop_pts()
            for a, b in zip(lp[:-1], lp[1:]):
                n = max(2, int(np.hypot(*(b - a)) / 0.2) + 1)
                out.append(a + (b - a) * np.linspace(0, 1, n)[:, None])
        return np.vstack(out)
    A, B = sample(pa), sample(pb)
    best = np.inf
    for k in range(0, len(A), 400):
        d = np.sqrt(((A[k:k + 400, None, :] - B[None, :, :]) ** 2).sum(-1)).min()
        best = min(best, d)
    return best


def test_nesting():
    from tilaser.core import shapes
    from tilaser.core.model import ImportedPath
    from tilaser.core.nesting import NestSettings, nest
    pr = Project()
    items = []
    for i in range(6):
        items.append(pr.add_vector_import(f"r{i}", [ImportedPath(shapes.rectangle(30 + 5 * i, 18), "#000000", "#000000")],
                                          center_at=(300 + i * 10, 300)))
    for i in range(4):
        items.append(pr.add_vector_import(f"c{i}", [ImportedPath(shapes.ellipse(20, 20), "#ff0000", "#ff0000")],
                                          center_at=(350, 350)))
    # Rahmen mit großem Loch: kleine Teile dürfen hinein
    ring = pr.add_vector_import("ring", [ImportedPath(shapes.rectangle(70, 70), "#00ff00", "#00ff00"),
                                         ImportedPath(shapes.rectangle(50, 50), "#00ff00", "#00ff00")],
                                center_at=(300, 300))
    items.append(ring)
    st = NestSettings(x=10, y=20, width=120, height=100, spacing=3.0, margin=2.0)
    res = nest(pr, items, st)
    assert len(res.placed) >= 10, (res.placed, res.unplaced)
    placed = [pr.item(i) for i in res.placed]
    for it in placed:                                   # innerhalb der Grundfläche inkl. Rand
        b = it.world_bbox()
        assert b[0] >= 12 - 1e-6 and b[1] >= 22 - 1e-6 and b[2] <= 128 + 1e-6 and b[3] <= 118 + 1e-6, b
    for i in range(len(placed)):                        # Mindestabstand
        for j in range(i + 1, len(placed)):
            d = _min_dist(placed[i].world_paths(), placed[j].world_paths())
            assert d >= 3.0 - 0.05, (placed[i].name, placed[j].name, d)
    inside_ring = [it for it in placed if it is not ring and
                   all(ring.world_bbox()[0] < v < ring.world_bbox()[2] for v in (it.world_bbox()[0], it.world_bbox()[2]))
                   and all(ring.world_bbox()[1] < v < ring.world_bbox()[3] for v in (it.world_bbox()[1], it.world_bbox()[3]))]
    assert inside_ring, "kein Teil im Loch des Rahmens"
    # Kopien: so viele Kreise wie möglich auf 60 × 60
    pr2 = Project()
    c = pr2.add_vector_import("k", [ImportedPath(shapes.ellipse(10, 10), "#000000", "#000000")])
    r2 = nest(pr2, [c], NestSettings(0, 0, 60, 60, spacing=2, margin=0, copies=30, rotations=(0,)))
    # theoretisch 25 (5 × 5); das konservative Raster (≈ 0,25 mm) kostet etwas Platz
    assert 20 <= len(r2.placed) <= 25 and len(pr2.items) == len(r2.placed), len(r2.placed)
    its = pr2.items
    for i in range(len(its)):
        for j in range(i + 1, len(its)):
            a, b = its[i], its[j]
            assert np.hypot(a.x - b.x, a.y - b.y) >= 12 - 0.05


class FakeGrbl:
    def __init__(self, *a, **k):
        self.out = bytearray(b"\r\nGrbl 1.1h ['$' for help]\r\n")
        self.rx = bytearray()
        self.received = []
        self.buffer_max = 0

    def read(self, n):
        time.sleep(0.001)
        d = bytes(self.out[:n])
        del self.out[:n]
        return d

    def write(self, data):
        for b in data:
            c = bytes([b])
            if c == b"?":
                self.out += b"<Idle|MPos:1.000,2.000,0.000|FS:0,0|WCO:1.000,0.000,0.000>\r\n"
            elif c in (b"!", b"~", b"\x18"):
                pass
            else:
                self.rx += c
                self.buffer_max = max(self.buffer_max, len(self.rx))
                if c == b"\n":
                    line = self.rx.decode().strip()
                    self.rx.clear()
                    self.received.append(line)
                    self.out += b"ok\r\n"
        return len(data)

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def test_sender_stream():
    fake = FakeGrbl()

    class FakeSerialModule:
        @staticmethod
        def Serial(*a, **k):
            return fake

    snd.serial = FakeSerialModule
    states, fws, stats = [], [], []
    c = snd.LaserConnection(on_state=states.append, on_firmware=lambda fw, t: fws.append(fw),
                            on_status=stats.append)
    c.connect("FAKE", 115200)
    t0 = time.time()
    while "idle" not in states and time.time() - t0 < 5:
        time.sleep(0.01)
    assert fws == ["grbl"]
    job = [f"G1 X{i} Y{i} F1000 ; Kommentar" for i in range(500)]
    c.start_job(job)
    t0 = time.time()
    while "done" not in states and time.time() - t0 < 10:
        time.sleep(0.01)
    c.disconnect()
    assert "done" in states
    assert fake.received[-1] == "G1 X499 Y499 F1000"
    assert stats and stats[-1]["WPos"][0] == 0.0


def test_layer_palette():
    from tilaser.core.model import PALETTE, ImportedPath, nearest_palette
    assert len(PALETTE) == 32 and len(set(PALETTE)) == 32
    assert nearest_palette("#ff0000") == "#e53935" and nearest_palette("#000") == "#000000"
    assert all(nearest_palette(c) == c for c in PALETTE)
    pr = Project()
    sq = g.Polyline(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float), True)
    pr.add_vector_import("a", [ImportedPath(sq, "#ff0000", "#ff0000"), ImportedPath(sq, "#e83a36", "#e83a36"),
                               ImportedPath(sq, "#123456", "#123456")])
    assert all(l.color in PALETTE for l in pr.layers)
    assert len(pr.layers) == 2 and pr.layers[0].name == "Schneiden Rot"      # Rottöne landen auf einer Ebene
    assert pr.add_layer("x", "#abcdef").color in PALETTE
    for i in range(40):                                                     # mehr Ebenen als Farben
        assert pr.add_layer(f"L{i}").color in PALETTE


def _overlap_area(a, b, cell=0.25):
    """Grobe Überlappung zweier Teile (gefüllte Außenkonturen) über ein Punktraster."""
    from tilaser.core.geometry import points_in_polygon
    ba, bb = a.world_bbox(), b.world_bbox()
    x0, y0, x1, y1 = max(ba[0], bb[0]), max(ba[1], bb[1]), min(ba[2], bb[2]), min(ba[3], bb[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    xs, ys = np.meshgrid(np.arange(x0, x1, cell), np.arange(y0, y1, cell))
    P = np.c_[xs.ravel(), ys.ravel()]
    def inside(it):
        r = np.zeros(len(P), bool)
        for p in it.world_paths():
            if p.closed:
                r ^= points_in_polygon(P, p.pts)
        return r
    return float((inside(a) & inside(b)).sum()) * cell * cell


def test_nesting_no_parts_inside_material():
    from tilaser.core import shapes
    from tilaser.core.geometry import Polyline
    from tilaser.core.model import ImportedPath, VectorPath
    from tilaser.core.nesting import NestSettings, nest, rasterize
    # Quadrat aus vier offenen Linien (wie DXF-LINEs): Inneres ist Material
    sq = [np.array(v, float) for v in ([[0, 0], [60, 0]], [[60, 0], [60, 60]], [[60, 60], [0, 60]], [[0, 60], [0, 0]])]
    m, _, _ = rasterize([Polyline(v, False) for v in sq], 1.0)
    assert m[30, 30] and m.mean() > 0.95
    # doppelte Kontur: kein Loch durch Even-Odd
    r = shapes.rectangle(40, 40)
    m, _, _ = rasterize([r, Polyline(r.pts.copy(), True)], 1.0)
    assert m[20, 20]
    # echte Aussparung bleibt frei, Gravur (nicht geschnitten) im Teil nicht
    m, _, _ = rasterize([(shapes.rectangle(60, 60), True), (shapes.rectangle(40, 40), True)], 1.0)
    assert not m[30, 30]
    m, _, _ = rasterize([(shapes.rectangle(60, 60), True), (shapes.rectangle(40, 40), False)], 1.0)
    assert m[30, 30]

    pr = Project()
    big = pr.add_vector_import("platte", [ImportedPath(Polyline(v, False), "#000000", "#000000") for v in sq],
                               center_at=(200, 200))
    plate = pr.add_vector_import("gravur", [ImportedPath(shapes.rectangle(60, 60), "#000000", "#000000")],
                                 center_at=(300, 200))
    eng = pr.add_layer("Gravur", "#0000ff", "fill")
    plate.paths.append(VectorPath(shapes.rectangle(40, 40), eng.id))
    small = [pr.add_vector_import(f"k{i}", [ImportedPath(shapes.rectangle(12, 12), "#000000", "#000000")],
                                  center_at=(250, 300)) for i in range(6)]
    res = nest(pr, [big, plate] + small, NestSettings(0, 0, 200, 70, spacing=2, margin=1, rotations=(0,)), thorough=False)
    placed = [pr.item(i) for i in res.placed]
    for a in (big, plate):
        for b in small:
            if b in placed and a in placed:
                assert _overlap_area(a, b) == 0.0, (a.name, b.name)

    # Rahmen mit echter Aussparung: kleines Teil darf hinein
    pr2 = Project()
    ring = pr2.add_vector_import("ring", [ImportedPath(shapes.rectangle(60, 60), "#000000", "#000000"),
                                          ImportedPath(shapes.rectangle(44, 44), "#000000", "#000000")])
    k = pr2.add_vector_import("k", [ImportedPath(shapes.rectangle(20, 20), "#000000", "#000000")])
    res2 = nest(pr2, [ring, k], NestSettings(0, 0, 66, 66, spacing=2, margin=1, rotations=(0,)), thorough=False)
    assert len(res2.placed) == 2


def test_profile_live_camera_fields():
    from tilaser.core.gcode import MachineProfile
    p = MachineProfile()
    assert not p.camera_live_auto and not p.camera_live_record and not p.camera_live_bg
    p.camera_live_auto, p.camera_live_rot, p.camera_media_dir = True, 90, "/tmp/x"
    q = MachineProfile.from_dict(p.to_dict())
    assert q.camera_live_auto and q.camera_live_rot == 90 and q.camera_media_dir == "/tmp/x"
    # alte Profile ohne die neuen Felder laden weiterhin
    d = p.to_dict()
    for k in ("camera_live_auto", "camera_live_record", "camera_live_bg", "camera_live_rot", "camera_media_dir"):
        d.pop(k)
    assert MachineProfile.from_dict(d).camera_live_rot == 0


LBRN = """<?xml version="1.0" encoding="UTF-8"?>
<LightBurnProject AppVersion="1.4.03" FormatVersion="1" MaterialHeight="0" MirrorX="False" MirrorY="False">
    <CutSetting type="Cut">
        <index Value="0"/>
        <name Value="C00"/>
        <maxPower Value="80"/>
        <speed Value="10"/>
        <numPasses Value="2"/>
        <priority Value="1"/>
    </CutSetting>
    <CutSetting type="Scan">
        <index Value="2"/>
        <name Value="Gravur"/>
        <maxPower Value="30"/>
        <speed Value="200"/>
        <interval Value="0.08"/>
        <priority Value="0"/>
    </CutSetting>
    <Shape Type="Path" CutIndex="0" VertID="0" PrimID="0">
        <XForm>1 0 0 1 0 0</XForm>
        <VertList>V2 3c0x2c0y2.4477153c1x1V3 2c0x1c1x2.4477153c1y2V22.504 2c0x1c1x1V22.504 0c0x1c1x1</VertList>
        <PrimList>B0 1L1 2L2 3</PrimList>
    </Shape>
    <Shape Type="Path" CutIndex="2" VertID="1" PrimID="1">
        <XForm>1 0 0 1 0 0</XForm>
        <VertList>V10 730c0x1c1x1V64 730c0x1c1x1V64 750c0x1c1x1V10 750c0x1c1x1</VertList>
        <PrimList>LineClosed</PrimList>
    </Shape>
    <Shape Type="Path" CutIndex="2" VertID="1" PrimID="1">
        <XForm>1 0 0 1 100 0</XForm>
    </Shape>
    <Shape Type="Rect" CutIndex="0" W="10" H="20" Cr="0">
        <XForm>1 0 0 1 353.926 134.554</XForm>
    </Shape>
    <Shape Type="Group" CutIndex="0">
        <XForm>-1 0 0 -1 320 0</XForm>
        <Children>
            <Shape Type="Ellipse" CutIndex="0" Rx="6" Ry="6">
                <XForm>1 0 0 1 20 -50</XForm>
            </Shape>
            <Shape Type="Rect" CutIndex="2" W="4" H="4" Cr="1">
                <XForm>1 0 0 1 20 -50</XForm>
            </Shape>
        </Children>
    </Shape>
    <Shape Type="Text" CutIndex="0" Str="Hi" HasBackupPath="1">
        <XForm>1 0 0 1 50 50</XForm>
        <BackupPath Type="Path" CutIndex="0">
            <XForm>1 0 0 1 50 50</XForm>
            <VertList>V0 0c0x1c1x1V5 0c0x1c1x1V5 5c0x1c1x1</VertList>
            <PrimList>L0 1L1 2L2 0</PrimList>
        </BackupPath>
    </Shape>
    <Shape Type="Spiral" CutIndex="0"/>
</LightBurnProject>"""


def test_lightburn_import_export():
    from tilaser.core.lbrn_export import export_lbrn
    from tilaser.core.lbrn_import import load_lbrn, parse_vertlist
    v = parse_vertlist("V2 3c0x2c0y2.4477153c1x1V3 2c0x1c1x2.4477153c1y2")
    assert v[0].c0 is not None and v[0].c1 is None and v[1].c0 is None and np.allclose(v[1].c1, [2.4477153, 2])
    p = os.path.join(TMP, "t.lbrn2")
    open(p, "w").write(LBRN)
    pr, warn = load_lbrn(p)
    assert any("Spiral" in w for w in warn)
    names = [l.name for l in pr.layers]
    assert names == ["Gravur", "Schneiden Schwarz"]                         # nach priority
    grav, cut = pr.layers
    assert grav.mode == "fill" and grav.speed == 12000 and grav.interval == 0.08 and grav.color == "#e53935"
    assert cut.mode == "cut" and cut.speed == 600 and cut.power == 80 and cut.passes == 2
    boxes = {it.name: np.round(it.world_bbox(), 3).tolist() for it in pr.items}
    assert boxes["Rechteck"] == [348.926, 124.554, 358.926, 144.554]
    assert np.allclose(boxes["Gruppe"], [294, 44, 306, 56])                 # verschachtelte XForms
    assert boxes["Text Hi"] == [50, 50, 55, 55]                             # Umrisse aus BackupPath
    shared = [it for it in pr.items if it.name == "Pfad"]
    assert len(shared) == 3 and np.allclose(shared[2].world_bbox(), [110, 730, 164, 750])   # VertID/PrimID geteilt
    assert shared[0].world_paths()[0].bez is not None and not shared[0].world_paths()[0].bez.is_line(0)
    # Bild dazu, speichern und wieder laden
    img = np.full((20, 30), 255, np.uint8)
    img[:5] = 0                                                             # oberer Rand schwarz
    it = pr.add_raster("bild.png", img, 30, 20, center_at=(200, 100))
    it.rotation = 30
    out = os.path.join(TMP, "rt.lbrn2")
    assert export_lbrn(pr, out) == []
    pr2, warn2 = load_lbrn(out)
    assert warn2 == [] and len(pr2.items) == len(pr.items)
    for a, b in zip(pr.items, pr2.items):
        assert np.allclose(a.world_bbox(), b.world_bbox(), atol=1e-4), (a.name, b.name)
    b = pr2.items[-1]
    assert abs(b.rotation - 30) < 1e-6 and (b.image[:5] == 0).all() and (b.image[5:] == 255).all()
    assert [(l.name, l.mode, l.speed, l.power) for l in pr2.layers if l.mode != "raster"] == \
           [(l.name, l.mode, l.speed, l.power) for l in pr.layers if l.mode != "raster"]
    assert 'index Value="2"' in open(out).read()                            # Rot bleibt LightBurn-Ebene 02
    # Vektor- und Bildeinstellungen mit demselben Index (wie LightBurn 1.7 speichert), Bild mit Transparenz
    from PIL import Image
    pic = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
    pic.putpixel((0, 1), (0, 0, 0, 255))                                    # gespeicherte Zeile 1 = obere Bildzeile
    buf = io.BytesIO()
    pic.save(buf, "PNG")
    data = base64.b64encode(buf.getvalue()).decode()
    xml = ('<LightBurnProject AppVersion="1.7.08" FormatVersion="1">'
           '<CutSetting type="Scan"><index Value="0"/><name Value="C00"/><speed Value="100"/></CutSetting>'
           '<CutSetting_Img type="Image"><index Value="0"/><name Value="C00"/><speed Value="250"/>'
           '<ditherMode Value="stucki"/></CutSetting_Img>'
           f'<Shape Type="Bitmap" CutIndex="0" W="40" H="20" Data="{data}"><XForm>1 0 0 1 70 100</XForm></Shape>'
           '<Shape Type="Rect" CutIndex="0" W="10" H="10"><XForm>1 0 0 1 0 0</XForm></Shape>'
           '</LightBurnProject>')
    p3 = os.path.join(TMP, "img.lbrn2")
    open(p3, "w").write(xml)
    pr3, _ = load_lbrn(p3)
    modes = {l.mode: l for l in pr3.layers}
    assert modes["fill"].speed == 6000 and modes["raster"].speed == 15000 and modes["raster"].dither == "stucki"
    bmp = next(it for it in pr3.items if it.kind == "raster")
    assert np.allclose(bmp.world_bbox(), (50, 90, 90, 110))
    assert bmp.image[0, 0] == 0 and bmp.image[1, 0] == 255 and bmp.image[0, 1] == 255   # aufrecht, Transparenz weiß


def test_rubber_band_frame():
    from tilaser.core import shapes
    from tilaser.core.gcode import convex_hull
    from tilaser.core.model import ImportedPath
    hull = convex_hull([(0, 0), (10, 0), (5, 5), (10, 10), (0, 10), (5, 2)])
    assert hull == [(0, 0), (10, 0), (10, 10), (0, 10)]                       # Innenpunkte fallen weg
    pr = Project()
    pr.add_vector_import("k", [ImportedPath(shapes.ellipse(40, 40), "#000000", "#000000")], center_at=(100, 100))
    pr.add_vector_import("d", [ImportedPath(g.Polyline(np.array([[0.0, 0], [10, 0], [5, 8]]), True),
                                            "#000000", "#000000")], center_at=(160, 100))
    h = convex_hull(pr.outline_points())
    assert len(h) > 8                                                         # Kreisbogen + Dreieck
    lines = frame_gcode(pr.bbox(), MachineProfile(), h)
    moves = [l for l in lines if l.startswith("G1")]
    assert len(moves) == len(h) and moves[-1].startswith(f"G1 X{h[0][0]:g} Y{h[0][1]:g}")   # geschlossen
    xs = [float(l.split()[1][1:]) for l in moves]
    ys = [float(l.split()[2][1:]) for l in moves]
    bb = pr.bbox()
    assert min(xs) >= bb[0] - 1e-6 and max(xs) <= bb[2] + 1e-6 and min(ys) >= bb[1] - 1e-6
    # Ecken des Rechtecks liegen außerhalb der Hülle: die Fahrt berührt sie nicht
    assert not any(abs(x - bb[0]) < 0.5 and abs(y - bb[1]) < 0.5 for x, y in zip(xs, ys))
    assert len([l for l in frame_gcode(pr.bbox(), MachineProfile()) if l.startswith("G1")]) == 4


def test_lightburn_legacy_lbrn():
    from tilaser.core.lbrn_import import load_lbrn
    # Altes Format (LightBurn 1.0, FormatVersion 0): V-/P-Elemente statt VertList/PrimList, skalierte XForm
    xml = ('<LightBurnProject AppVersion="1.0.06" FormatVersion="0">'
           '<CutSetting type="Cut"><index Value="27"/><name Value="C27"/><maxPower Value="100"/>'
           '<speed Value="5.83333"/><overscan Value="1"/><overscanPercent Value="2.5"/></CutSetting>'
           '<Shape Type="Path" CutIndex="27"><XForm>0.5 0 0 0.5 100 100</XForm>'
           '<V vx="0" vy="0"/><V vx="20" vy="0"/><V vx="20" vy="20" c0x="20" c0y="30" c1x="20" c1y="10"/>'
           '<V vx="0" vy="20"/><P T="L" p0="0" p1="1"/><P T="B" p0="1" p1="2"/><P T="L" p0="2" p1="3"/>'
           '<P T="L" p0="3" p1="0"/></Shape></LightBurnProject>')
    p = os.path.join(TMP, "alt.lbrn")
    open(p, "w").write(xml)
    pr, warn = load_lbrn(p)
    assert warn == [] and len(pr.items) == 1 and pr.layers[0].speed == 350 and pr.layers[0].power == 100
    poly = pr.items[0].world_paths()[0]
    assert poly.closed and poly.bez is not None and np.allclose(g.bbox([poly]), (100, 100, 110, 110), atol=1e-6)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)
    print("Ausgaben in", TMP)

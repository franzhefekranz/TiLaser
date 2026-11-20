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


def test_line_times():
    from tilaser.core.gcode import line_times
    from tilaser.core.sender import clean_line
    prof = MachineProfile()
    cum = line_times(["G21", "G90", "G0 X60 Y80", "G1 X60 Y0 F600", "G4 P2", "G91", "G1 X30"], prof)
    t_rapid = 100 / prof.travel_speed * 60
    assert abs(cum[2] - t_rapid) < 1e-9 and abs(cum[3] - (t_rapid + 8)) < 1e-9          # 80 mm bei 600 mm/min
    assert abs(cum[4] - (t_rapid + 10)) < 1e-9 and abs(cum[6] - (t_rapid + 13)) < 1e-9   # Pause 2 s, relativ 30 mm
    pr = make_project()
    res = generate(pr, prof)
    clean = [c for c in (clean_line(l) for l in res.lines) if c]
    assert abs(line_times(clean, prof)[-1] - res.time_s) / res.time_s < 0.05             # passt zur Gesamtschätzung


def test_lightburn_round_features():
    """Offset-Füllung, Startpunkt, Schnittreihenfolge, Z-Versatz, Einstell-Tests, Makros."""
    from tilaser.core.gcode import offset_rings, ordered_paths
    from tilaser.core.lasertests import (FocusTest, IntervalTest, RampTest, build_focus_test, build_interval_test,
                                         build_ramp_test)
    from tilaser.core.model import ImportedPath, VectorPath
    # Offset-Füllung: Ringe im Abstand, auch mit Loch
    rings = offset_rings([_sq(0, 0, 10)], 1.0)
    assert len(rings) == 5 and np.allclose(g.bbox([rings[0]]), (0.5, 0.5, 9.5, 9.5), atol=1e-6)
    assert len(offset_rings([_sq(0, 0, 20), _sq(8, 8, 4)], 1.0)) >= 8
    pr = Project()
    pr.add_vector_import("o", [ImportedPath(_sq(0, 0, 10), "#000000", "#000000")])
    pr.layers[0].mode, pr.layers[0].interval = "offset", 1.0
    res = generate(pr, MachineProfile())
    assert sum(1 for l in res.lines if l.startswith("G0")) >= 5
    # Startpunkt: festgelegte Kontur wird nicht gedreht
    a = _sq(10, 0, 10)
    a.start_fixed = True
    out = g.order_paths([a], (20, 10), False)
    assert np.allclose(out[0].pts[0], [10, 0])
    free = g.order_paths([_sq(10, 0, 10)], (20, 10), False)
    assert np.allclose(free[0].pts[0], [20, 10])
    pr2 = Project()
    it = pr2.add_vector_import("s", [ImportedPath(_sq(0, 0, 10), "#000000", "#000000")])
    it.paths[0].start_fixed = True
    fp = os.path.join(TMP, "start.tlp")
    save_project(pr2, fp)
    assert load_project(fp).items[0].paths[0].start_fixed
    # Schnittreihenfolge: Objekt für Objekt folgt der Projektreihenfolge
    pr3 = Project()
    for x in (100, 0, 50):
        pr3.add_vector_import(f"q{x}", [ImportedPath(_sq(x, 0, 5), "#000000", "#000000")])
    xs = lambda lines: [round(float(next(t[1:] for t in l.split() if t.startswith("X")))) for l in lines
                        if l.startswith("G0") and " X" in l]
    by_obj = generate(pr3, MachineProfile(cut_order="objects"))
    opt = generate(pr3, MachineProfile())
    assert [x // 50 * 50 for x in xs(by_obj.lines)[:3]] == [100, 0, 50]
    assert [x // 50 * 50 for x in xs(opt.lines)[:3]] == [0, 50, 100]
    # Z-Versatz je Ebene
    pr3.layers[0].z_offset = -1.5
    zl = [l for l in generate(pr3, MachineProfile()).lines if l.startswith("G0 Z")]
    assert zl == ["G0 Z-1.5", "G0 Z1.5"]
    # Einstell-Tests
    ft = build_focus_test(FocusTest(steps=5, labels=False))
    assert sorted(round(l.z_offset, 2) for l in ft.layers) == [-2, -1, 0, 1, 2]
    assert len(build_ramp_test(RampTest(labels=False)).items) == 2
    it_ = build_interval_test(IntervalTest(count=4, labels=False))
    assert [round(l.interval, 3) for l in it_.layers] == [0.05, 0.133, 0.217, 0.3]
    for tp in (ft, it_):
        assert generate(tp, MachineProfile()).time_s > 0
    # Makros im Profil
    prof = MachineProfile()
    assert prof.macros and prof.macros[0]["gcode"] == "M8"
    prof.macros.append({"name": "Fokus", "gcode": "G0 Z5", "key": "Ctrl+1"})
    assert MachineProfile.from_dict(prof.to_dict()).macros[-1]["key"] == "Ctrl+1"


def test_variable_text_and_codes():
    import datetime
    from tilaser.core import vartext as vt
    from tilaser.core.barcode import code128_widths, qr_matrix
    st = vt.VarState(nr=7)
    now = datetime.datetime(2026, 11, 18, 9, 5)
    assert vt.render("Nr {nr:03} am {datum} {zeit} {Name} {x}", st, now, {"Name": "Anna"}) == \
        "Nr 007 am 18.11.2026 09:05 Anna {x}"
    csvp = os.path.join(TMP, "liste.csv")
    open(csvp, "w", encoding="utf-8").write("Name;Ort\nAnna;Berlin\nBen;Köln\n")
    head, rows = vt.read_csv(csvp)
    assert head == ["Name", "Ort"] and rows[1]["Ort"] == "Köln"
    pr = Project()
    layer = pr.add_layer("G", mode="fill")
    st = vt.project_state(pr)
    st.csv_path = csvp
    qr = vt.make_item({"kind": "qr", "template": "{Name}-{nr}", "size": 20}, layer.id, 50, 50, st, vt.current_row(st))
    pr.items.append(qr)
    assert qr.name == "QR: Anna-1" and np.allclose(qr.world_bbox()[2] - qr.world_bbox()[0], 20, atol=1.0)
    vt.advance(pr)
    assert vt.refresh_project(pr) == 1 and qr.name == "QR: Ben-2"
    fp = os.path.join(TMP, "var.tlp")
    save_project(pr, fp)
    back = load_project(fp)
    assert back.vars.nr == 2 and back.items[0].var["template"] == "{Name}-{nr}"
    # Code 128: Start B, Prüfzeichen, Stopp; jede Zeichenbreite 11 Module
    w = code128_widths("AB")
    assert sum(w) == 11 * 4 + 13 and w[:6] == [2, 1, 1, 2, 1, 4]
    # QR-Code: mit OpenCV lesbar (falls vorhanden)
    m = qr_matrix("TiLaser {nr} Prüfung", "M")
    assert len(m) in (21, 25, 29)
    try:
        import cv2
    except ImportError:
        return
    a = np.pad(np.where(np.array(m), 0, 255).astype(np.uint8), 4, constant_values=255)
    img = np.kron(a, np.ones((8, 8), np.uint8))
    assert cv2.QRCodeDetector().detectAndDecode(img)[0] == "TiLaser {nr} Prüfung"


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


def test_profile_auto_connect():
    p = MachineProfile()
    assert p.auto_connect
    p.conn_type, p.port, p.baud, p.auto_connect = "serial", "COM5", 250000, False
    q = MachineProfile.from_dict(p.to_dict())
    assert (q.conn_type, q.port, q.baud, q.auto_connect) == ("serial", "COM5", 250000, False)
    d = p.to_dict()
    d.pop("auto_connect")
    assert MachineProfile.from_dict(d).auto_connect                      # alte Profile: an


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


def test_extract_items_for_sheet():
    from tilaser.core import shapes
    from tilaser.core.editing import extract_items
    from tilaser.core.model import ImportedPath
    from tilaser.core.nesting import NestSettings, nest
    pr = Project()
    its = [pr.add_vector_import(f"q{i}", [ImportedPath(shapes.rectangle(40, 40), c, c)], center_at=(300, 300))
           for i, c in enumerate(["#000000", "#ff0000"] * 3)]
    res = nest(pr, its, NestSettings(0, 0, 90, 90, spacing=2, margin=0, rotations=(0,)), thorough=False)
    assert len(res.placed) == 4 and len(res.unplaced) == 2
    sheet = extract_items(pr, res.placed)
    assert [it.id for it in sheet.items] == [i for i in (it.id for it in pr.items) if i in res.placed]
    assert {l.id for l in sheet.layers} == set().union(*[it.layer_ids() for it in sheet.items])
    fp = os.path.join(TMP, "platte1.tlp")
    save_project(sheet, fp)
    back = load_project(fp)
    assert len(back.items) == 4 and all(0 <= it.world_bbox()[0] and it.world_bbox()[2] <= 90 for it in back.items)
    for i in res.placed:                                   # Rest bleibt im Projekt und lässt sich neu nesten
        pr.remove_item(i)
    rest = [pr.item(i) for i in res.unplaced]
    res2 = nest(pr, rest, NestSettings(0, 0, 90, 90, spacing=2, margin=0, rotations=(0,)), thorough=False)
    assert len(res2.placed) == 2 and not res2.unplaced
    sheet.items[0].x += 5                                  # Kopie ist unabhängig vom Original
    assert pr.item(rest[0].id) is not sheet.items[0]


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


def test_shared_edges():
    from tilaser.core import shapes
    from tilaser.core.model import ImportedPath
    from tilaser.core.nesting import NestSettings, nest
    from tilaser.core.overlap import remove_overlaps, slide_distance

    def burn_len(res):
        return sum(float(np.hypot(*(b[:, 1] - b[:, 0]).T).sum()) for _, b in res.burn)

    # zwei Quadrate mit gemeinsamer Kante, ein drittes überlappt nur halb
    a, b, c = _sq(0, 0, 20), _sq(20, 0, 20), _sq(40, 10, 20)
    out, saved = remove_overlaps([a, b, c])
    assert abs(saved - 30) < 1e-6 and out[0] is a                       # 20 mm + 10 mm doppelt
    assert abs(sum(p.length() for p in out) - (80 + 60 + 70)) < 1e-6
    # Gleiten: an der Ecke vorbei blockiert nicht, frontal schon
    assert slide_distance([_sq(30, 20, 10)], [_sq(0, 0, 20)], (-1, 0)) == np.inf
    assert abs(slide_distance([_sq(30, 0, 10)], [_sq(0, 0, 20)], (-1, 0)) - 10) < 1e-9

    pr = Project()
    its = [pr.add_vector_import(f"q{i}", [ImportedPath(shapes.rectangle(20, 20), "#000000", "#000000")],
                                center_at=(300, 300)) for i in range(6)]
    its.append(pr.add_vector_import("k", [ImportedPath(shapes.ellipse(16, 16), "#000000", "#000000")],
                                    center_at=(300, 300)))
    nest(pr, its, NestSettings(0, 0, 100, 60, spacing=0, margin=0, rotations=(0,)), thorough=False)
    boxes = sorted(tuple(np.round(it.world_bbox()[:2], 6)) for it in its[:6])
    assert all(x % 20 == 0 and y % 20 == 0 for x, y in boxes), boxes   # Quadrate exakt bündig
    for i in range(len(its)):
        for j in range(i + 1, len(its)):
            assert _overlap_area(its[i], its[j], 0.1) == 0.0
    full = 6 * 80 + its[6].world_paths()[0].length()
    with_dupes = generate(pr, MachineProfile(remove_overlaps=False))
    without = generate(pr, MachineProfile())
    assert abs(burn_len(with_dupes) - full) < 0.5 and burn_len(without) < full - 50
    assert "Doppelte Linien ausgelassen" in without.text
    assert all(len(it.paths[0].poly.pts) == 4 for it in its[:6])        # Projekt unverändert
    # größerer Abstand: alle Linien wieder da
    nest(pr, its, NestSettings(0, 0, 200, 100, spacing=3, margin=0, rotations=(0,)), thorough=False)
    again = generate(pr, MachineProfile())
    assert abs(burn_len(again) - full) < 0.5 and "Doppelte" not in again.text


def test_box_generator_fingers_fit():
    """Jedes Stück Wand genau einmal vorhanden (Voxel-Probe), Hohlraum frei, Volumen stimmt."""
    from tilaser.core.boxgen import BoxSpec, build_box, panel_outline, panels, polygon_area

    def inside(poly, x, y):
        c = np.zeros(x.shape, bool)
        for i in range(len(poly)):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
            m = (y1 > y) != (y2 > y)
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1 if y2 != y1 else 1)
            c ^= m & (x < xi)
        return c

    for s in (BoxSpec(width=40, depth=30, height=20, thickness=3, finger=6),
              BoxSpec(width=40, depth=30, height=20, thickness=3, finger=6, closed=False),
              BoxSpec(width=25, depth=47, height=33, thickness=4, finger=5, inner=False)):
        W, D, H = s.outer()
        t = s.thickness
        X, Y, Z = np.meshgrid(np.arange(0.2371, W, 0.7), np.arange(0.2371, D, 0.7), np.arange(0.2371, H, 0.7),
                              indexing="ij")
        cnt = np.zeros(X.shape, int)
        vol = 0.0
        for n, a, b, nb in panels(s):
            P = panel_outline(n, a, b, nb, t, s.finger)
            vol += polygon_area(P) * t
            cnt += {"Boden": lambda: (Z < t) & inside(P, X, Y),
                    "Deckel": lambda: (Z > H - t) & inside(P, X, Y),
                    "Vorne": lambda: (Y < t) & inside(P, X, Z),
                    "Hinten": lambda: (Y > D - t) & inside(P, W - X, Z),
                    "Links": lambda: (X < t) & inside(P, D - Y, Z),
                    "Rechts": lambda: (X > W - t) & inside(P, Y, Z)}[n]()
        top = H - t if s.closed else H + 1
        cav = (X > t) & (X < W - t) & (Y > t) & (Y < D - t) & (Z > t) & (Z < top)
        assert (cnt[~cav] == 1).all() and (cnt[cav] == 0).all()
        inner = (W - 2 * t) * (D - 2 * t) * (top - t if s.closed else H - t)
        assert abs(vol - (W * D * H - inner)) < 1e-6
    p = build_box(BoxSpec(width=100, depth=80, height=50, kerf=0.15, labels=False))
    assert len(p.items) == 6 and p.layers[-1].kerf == 0.15
    assert abs(BoxSpec(width=100, depth=80, height=50).outer()[2] - 56) < 1e-9
    try:
        build_box(BoxSpec(width=5, depth=5, height=5, inner=False))
        assert False
    except ValueError:
        pass


def test_flame_detector():
    """Wandernder Laserpunkt und feste Lichter lösen nicht aus, eine stehende Flamme schon."""
    from tilaser.core.flame import FlameDetector, sensitivity_to_area

    def frame(dot=None, blob=None):
        f = np.zeros((480, 640, 3), np.uint8)
        f[:] = (150, 110, 70)                       # Holz
        f[10:20, 600:610] = (255, 140, 20)          # orange LED, schon im Referenzbild
        if dot:
            f[dot[1]:dot[1] + 5, dot[0]:dot[0] + 5] = (255, 200, 120)
        if blob:
            x, y, s = blob
            f[y:y + s, x:x + s] = (255, 170, 40)
        return f

    assert sensitivity_to_area(1) > sensitivity_to_area(5) > sensitivity_to_area(10)
    d = FlameDetector(10, 2.0)
    d.update(frame(), 0.0)
    for i in range(150):                            # 30 s Laserpunkt, auch mit höchster Empfindlichkeit
        r = d.update(frame(dot=(100 + (i * 6) % 400, 200 + (i // 60) * 20)), 0.2 * (i + 1))
        assert not r.triggered
    d = FlameDetector(5, 2.0)
    d.update(frame(), 0.0)
    hit = None
    for i in range(1, 60):
        if d.update(frame(blob=(300, 300, 20)), 0.2 * i).triggered:
            hit = 0.2 * i
            break
    assert hit is not None and 1.5 <= hit <= 3.0
    d = FlameDetector(5, 2.0)                       # schon beim Start vorhanden -> Referenz, kein Alarm
    d.update(frame(blob=(300, 300, 20)), 0.0)
    assert not any(d.update(frame(blob=(300, 300, 20)), 0.2 * i).triggered for i in range(1, 30))
    d = FlameDetector(1, 2.0)                       # unempfindlich: kleine Flamme reicht nicht
    d.update(frame(), 0.0)
    assert not any(d.update(frame(blob=(300, 300, 12)), 0.2 * i).triggered for i in range(1, 30))


def test_webserver():
    """Anmeldung per PIN, Status, Befehle in die Warteschlange, Kamerabild, Schutz vor fremden Formularen."""
    import json as _json
    import urllib.error
    import urllib.request
    from tilaser.core.webserver import WebServer

    srv = WebServer(port=0, pin="123456", host="127.0.0.1")
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    cookie = {}

    def req(path, body=None, header=True):
        r = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                   data=_json.dumps(body).encode() if body is not None else None)
        if header:
            r.add_header("X-TiLaser", "1")
        if body is not None:
            r.add_header("Content-Type", "application/json")
        if cookie:
            r.add_header("Cookie", "tilaser_session=" + cookie["v"])
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers
    try:
        code, body, _ = req("/")
        assert code == 200 and b"TiLaser" in body
        assert req("/api/status")[0] == 401
        assert req("/api/cmd", {"cmd": "stop"})[0] == 401
        assert req("/api/login", {"pin": "000000"})[0] == 403
        assert req("/api/login", {"pin": "123456"}, header=False)[0] == 403       # fremdes Formular
        code, _, hdr = req("/api/login", {"pin": "123456"})
        assert code == 200
        cookie["v"] = hdr["Set-Cookie"].split(";")[0].split("=", 1)[1]
        srv.publish({"state": "Läuft", "progress": 42})
        code, body, _ = req("/api/status")
        st = _json.loads(body)
        assert code == 200 and st["state"] == "Läuft" and st["allow_start"] is False
        assert req("/api/cmd", {"cmd": "pause"})[0] == 200 and srv.commands.get_nowait() == "pause"
        assert req("/api/cmd", {"cmd": "start"})[0] == 403                        # nicht erlaubt
        assert req("/api/cmd", {"cmd": "rm -rf"})[0] == 400
        assert req("/cam.jpg")[0] == 204 and srv.camera_wanted()
        srv.publish_jpeg(b"\xff\xd8jpeg")
        code, body, _ = req("/cam.jpg")
        assert code == 200 and body.startswith(b"\xff\xd8")
        srv.allow_start = True
        assert req("/api/cmd", {"cmd": "start"})[0] == 200 and srv.commands.get_nowait() == "start"
    finally:
        srv.stop()


def _ring_area(rings):
    tot = 0.0
    for r in rings:
        p = r.pts if hasattr(r, "pts") else r
        tot += 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1)))
    return tot


def test_clip_boolean_without_pyclipper():
    from tilaser.core.clip import boolean, clip_lines
    sq = lambda x, y, w, h: g.Polyline(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], float), True)
    a, b = [sq(0, 0, 10, 10)], [sq(5.001, 5.002, 10, 10)]
    assert abs(abs(_ring_area(boolean(a, b, "intersection"))) - 4.999 * 4.998) < 1e-6
    assert abs(abs(_ring_area(boolean(a, b, "difference"))) - (100 - 4.999 * 4.998)) < 1e-6
    ring = [g.Polyline(g.circle_points(0, 0, 10), True), g.Polyline(g.circle_points(0, 0, 5), True)]
    pieces = clip_lines([g.Polyline(np.array([[-20, 0.3], [20, 0.3]]))], ring)
    assert len(pieces) == 2 and all(abs(p.pts[:, 0]).min() >= 4.9 for p in pieces)


def test_patterns_in_region():
    from tilaser.core import patterns as pt
    from tilaser.core.clip import distance_to_edges, inside
    rect = [g.Polyline(np.array([[0, 0], [120, 0], [120, 50], [0, 50]], float), True)]
    hinge = pt.living_hinge(rect, 20, 3, 1.5, 3)
    rows = {}
    for p in hinge:
        rows.setdefault(round(float(p.pts[0, 1]), 4), []).append(sorted(p.pts[:, 0]))
    ys = sorted(rows)
    assert len(ys) % 2 == 1 and abs(ys[0] - 3 - (50 - 3 - ys[-1])) < 1e-3        # mittig, ungerade Zahl
    for i, y in enumerate(ys):
        xs = np.array(sorted(rows[y]))
        assert np.allclose(xs[:, 0] + xs[::-1, 1], 120, atol=1e-3)               # spiegelsymmetrisch
        if i % 2 == 0:                                                           # geschlossen: Randabstand
            assert xs.min() >= 3 - 1e-6 and xs.max() <= 117 + 1e-6
        else:                                                                    # offen: bis zum Rand
            assert xs.min() < 1e-3 and xs.max() > 120 - 1e-3
    closed_mids = [(u + v) / 2 for u, v in sorted(rows[ys[0]])]
    o = sorted(rows[ys[1]])
    open_bridges = [(o[i][1] + o[i + 1][0]) / 2 for i in range(len(o) - 1)]
    assert np.allclose(closed_mids, open_bridges, atol=1e-3)                     # Stege mittig versetzt
    for style in ("triple", "wave"):                                             # punktsymmetrisch zur Mitte
        hs = pt.living_hinge(rect, 20, 3, 1.5, 3, 0, style)
        pts = np.vstack([p.pts for p in hs])
        assert inside(pts * 0.9999 + np.array([60, 25]) * 0.0001, rect).all()
        ends = np.round(np.vstack([np.r_[p.pts[0], p.pts[-1]] for p in hs]), 2)
        flip = np.round(np.vstack([[120 - p.pts[-1][0], 50 - p.pts[-1][1], 120 - p.pts[0][0], 50 - p.pts[0][1]]
                                   for p in hs]), 2)
        assert {tuple(e) for e in ends} == {tuple(f) for f in flip}, style
        far = [p for p in hs if np.ptp(p.pts[:, 0]) > 0 and p.pts[:, 0].min() > 1e-3 and p.pts[:, 0].max() < 120 - 1e-3]
        assert far and (distance_to_edges(np.vstack([p.pts for p in far]), rect) >= 0.5).all()
    holes = pt.hole_pattern(rect, "hex", 6, 2, "offset", 3)
    pts = np.vstack([p.pts for p in holes])
    assert len(holes) > 40 and inside(pts, rect).all() and (distance_to_edges(pts, rect) >= 3 - 1e-6).all()
    donut = [g.Polyline(g.circle_points(60, 40, 38), True),
             g.Polyline(np.array([[45, 30], [75, 30], [75, 50], [45, 50]], float), True)]
    region_area = abs(_ring_area(donut[:1])) - 600
    for kind in pt.ENGRAVE_PATTERNS:
        res = pt.engrave_pattern(donut, kind, 8, 1)
        cov = sum(abs(_ring_area([p])) for p in res)
        assert res and 0.15 * region_area < cov < 0.95 * region_area, kind
        c = np.vstack([p.pts for p in res])
        assert inside(c[:: max(1, len(c) // 200)] * 0.999 + np.array([60, 40]) * 0.001, donut[:1]).all()
    hs = pt.engrave_pattern(rect, "checker", 10, 0)
    assert abs(sum(abs(_ring_area([p])) for p in hs) - 3000) < 5


def test_finger_joint_edges():
    from tilaser.core.clip import boolean
    from tilaser.core.joints import finger_joint
    A = [np.array([[0, 0], [100, 0], [100, 50], [0, 50.]])]
    B = [np.array([[100, 80], [0, 80], [0, 50], [100, 50.]])]
    w = 100 / 11
    ra, rb, n = finger_joint(A, B, 3, 10, "corner")
    assert n == 1 and abs(_ring_area(ra) - (5000 - 5 * w * 3)) < 1e-6 and abs(_ring_area(rb) - (3000 - 6 * w * 3)) < 1e-6
    ra, rb, _ = finger_joint(A, B, 3, 10, "tabs")
    P = lambda rs: [g.Polyline(r, True) for r in rs]
    assert abs(_ring_area(ra) - (5000 + 6 * w * 3)) < 1e-6
    assert sum(abs(_ring_area([p])) for p in boolean(P(ra), P(rb), "intersection")) < 1e-6   # greifen ineinander
    from tilaser.core.joints import sides
    assert sides(A, B) == ("bottom", "top") and sides(B, A) == ("top", "bottom")
    assert sides(A, [np.array([[100, 0], [150, 0], [150, 50], [100, 50.]])]) == ("left", "right")
    assert sides(A, [r + [0, 20] for r in B]) is None
    ra3, rb3, _ = finger_joint(A, B, 3, 10)                                   # Standard: ineinandergreifend
    assert sum(abs(_ring_area([p])) for p in boolean(P(ra3), P(rb3), "intersection")) < 1e-6
    assert abs(_ring_area(ra3) + _ring_area(rb3) - 8000) < 1e-6              # lückenlos, nichts doppelt
    ra2, rb2, _ = finger_joint(A, B, 3, 10, "corner", dogbone=1.0)
    assert len(rb2[0]) > len(rb[0]) and _ring_area(rb2) < 3000 - 6 * w * 3
    try:
        finger_joint(A, [r + [0, 20] for r in B], 3, 10)
        assert False
    except ValueError:
        pass


def test_gear_puzzle_stencil():
    from tilaser.core.shapes_gen import gear_pair, jigsaw, spur_gear, stencil_bridges
    gear = spur_gear(2, 20, bore=5)
    r = np.hypot(*gear[0].pts.T)
    assert abs(r.max() - 22) < 0.05 and abs(r.min() - 17.5) < 0.05 and len(gear) == 2
    a, b, dist = gear_pair(2, 15, 24)
    assert dist == 39
    frame, lines = jigsaw(200, 150, 6, 4, seed=7)
    assert frame.closed and len(lines) == 3 + 5
    pts = np.vstack([p.pts for p in lines])
    assert pts[:, 0].min() >= -0.01 and pts[:, 0].max() <= 200.01
    assert len(jigsaw(200, 150, 6, 4, seed=7)[1][0].pts) == len(lines[0].pts)    # gleiche Zahl, gleiches Puzzle
    o = [g.Polyline(g.circle_points(0, 0, 10), True), g.Polyline(g.circle_points(0, 0, 6), True)]
    res, n = stencil_bridges(o, 2.0)
    assert n == 1 and len(res) == 2
    assert abs(sum(abs(_ring_area([p])) for p in res) - (np.pi * 64 - 2 * 2 * 4)) < 0.5


def test_job_log(tmp_path=None):
    import tempfile
    from tilaser.core import joblog
    d = tempfile.mkdtemp()
    f = os.path.join(d, "auftraege.csv")
    e = joblog.JobEntry(start="2026-11-18 10:00:00", duration_s=1800, result="fertig", project="test.tlp",
                        layers="Schneiden (80 %, 300 mm/min, 1×)", cost=joblog.job_cost(1800, 10, 200, 0.4))
    joblog.append(e, f)
    joblog.append(joblog.JobEntry(result="gestoppt", duration_s=60), f)
    rows = joblog.read(f)
    assert len(rows) == 2 and rows[0].project == "test.tlp" and abs(rows[0].cost - 5.04) < 1e-9
    assert rows[0].duration_s == 1800 and joblog.totals(rows)["done"] == 1
    assert open(f, "rb").read(3) == b"\xef\xbb\xbf"                    # BOM für Excel


def test_translations():
    """Sprachdatei vollständig, Platzhalter stimmen, Muster und zusammengesetzte Texte werden übersetzt."""
    import importlib.util
    import json as _json
    import re as _re
    from tilaser.core import i18n
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("i18n_extract", os.path.join(ROOT, "tools", "i18n_extract.py"))
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    keys = ex.collect()
    en = _json.load(open(os.path.join(ROOT, "tilaser", "data", "lang", "en.json"), encoding="utf-8"))
    missing = [k for k in keys if not en.get(k)]
    assert not missing, f"{len(missing)} Texte ohne englische Übersetzung, z. B. {missing[:3]} – " \
                        "python tools/i18n_extract.py --missing en"
    for k, v in en.items():
        if k.startswith("_") or not v:
            continue
        assert sorted(_re.findall(r"\{\d+\}", k)) == sorted(_re.findall(r"\{\d+\}", v)), k
    t = i18n.Translator("en")
    assert t.tr("&Datei") == "&File"
    assert t.tr("Projekt öffnen (Ctrl+O)") == "Open project (Ctrl+O)"
    assert t.tr("-- Auftrag fertig in 3:20 (geschätzt 3:00)") == "-- Job finished in 3:20 (estimated 3:00)"
    assert t.tr("12 Objekte") == "12 objects"
    assert t.tr("⏸ Pause") == "⏸ Pause" and t.tr("Läuft") == "Running"
    assert i18n.Translator("de").tr("Läuft") == "Läuft"
    d = tempfile.mkdtemp()
    old = i18n.USER_DIR
    try:                                            # eigene Sprache: fehlende Texte fallen auf Englisch zurück
        i18n.USER_DIR = d
        with open(os.path.join(d, "fr.json"), "w", encoding="utf-8") as f:
            _json.dump({"_name": "Français", "Läuft": "En cours"}, f)
        fr = i18n.Translator("fr")
        assert fr.tr("Läuft") == "En cours" and fr.tr("Bereit") == "Ready"
        assert i18n.available()["fr"] == "Français"
        path = i18n.make_template("nl", "Nederlands", keys[:5])
        tpl = _json.load(open(path, encoding="utf-8"))
        assert tpl["_name"] == "Nederlands" and all(k in tpl for k in keys[:5])
    finally:
        i18n.USER_DIR = old


def test_manual_complete_and_help_links():
    """Jedes Bedienelement steht in beiden Anleitungen, alle internen Links und Hilfe-Kennungen stimmen."""
    import ast as _ast
    import importlib.util
    import re as _re
    from tilaser.core import manual
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("doc_audit", os.path.join(ROOT, "tools", "doc_audit.py"))
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    for lang in ("de", "en"):
        miss = audit.missing(lang)
        assert not miss, f"{lang}: {len(miss)} Bedienelemente nicht beschrieben, z. B. {miss[:3]} – python tools/doc_audit.py {lang}"
    # Hilfe-Kennungen aus gui/help.py
    tree = _ast.parse(open(os.path.join(ROOT, "tilaser", "gui", "help.py"), encoding="utf-8").read())
    used = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Dict):
            used |= {v.value for v in node.values if isinstance(v, _ast.Constant) and isinstance(v.value, str)}
        if isinstance(node, _ast.Tuple) and len(node.elts) == 2 and all(isinstance(e, _ast.Constant) for e in node.elts):
            v = node.elts[1].value
            if isinstance(v, str) and _re.fullmatch(r"[a-z]+(-[a-z]+)*", v):
                used.add(v)
    used |= {"referenz", "oberflaeche", "zeichenwerkzeuge", "fenster", "dlg-generator", "hilfe"}
    used = {u for u in used if _re.fullmatch(r"[a-z]+(-[a-z0-9]+)*", u) and u not in ("de", "en")}
    for doc in ("Bedienungsanleitung.md", "Manual_en.md"):
        md = open(os.path.join(ROOT, "docs", doc), encoding="utf-8").read()
        ids = manual.help_ids(md)
        assert used <= ids, f"{doc}: Kennungen fehlen: {sorted(used - ids)}"
        text, heads = manual.prepare(md, os.path.join(ROOT, "docs"))
        slugs = {h[2] for h in heads}
        links = set(_re.findall(r"\]\(#([^)]+)\)", md))
        assert not links - slugs, f"{doc}: tote Links {sorted(links - slugs)[:5]}"
        assert "<!--" not in text and all(h[3] for h in heads if h[1].startswith("22."))
    de = manual.help_ids(open(os.path.join(ROOT, "docs", "Bedienungsanleitung.md"), encoding="utf-8").read())
    en = manual.help_ids(open(os.path.join(ROOT, "docs", "Manual_en.md"), encoding="utf-8").read())
    assert de == en                                  # beide Sprachen springen an dieselben Stellen
    assert manual.slug("8. Nesting – Teile automatisch anordnen") == "8-nesting--teile-automatisch-anordnen"


def test_edge_align():
    """Kante an Kante: Teil wird gedreht und bündig ohne Überlappung angelegt."""
    from tilaser.core.clip import boolean
    from tilaser.core.edgealign import align_matrix, nearest_edge, rotation_deg
    A = [g.Polyline(np.array([[0, 0], [100, 0], [100, 50], [0, 50.]]), True)]
    m = g.translate(200, 100) @ g.rotate(30)
    B = [g.Polyline(g.apply_matrix(m, np.array([[0, 0], [60, 0], [60, 30], [0, 30.]])), True)]
    ea = nearest_edge([("A", A)], 50, 50.2, 1)
    eb = nearest_edge([("B", B)], *g.apply_matrix(m, np.array([[30, 0.2]]))[0], 1)
    assert ea.item_id == "A" and abs(ea.length - 100) < 1e-9 and np.allclose(ea.outward, [0, 1])
    for mode, x0 in (("center", 20), ("start", 0), ("end", 40)):
        M = align_matrix(eb, ea, mode)
        moved = [g.Polyline(g.apply_matrix(M, B[0].pts), True)]
        assert np.allclose(g.bbox(moved), [x0, 50, x0 + 60, 80])
        assert sum(abs(_ring_area([p])) for p in boolean(A, moved, "intersection")) < 1e-6
    assert abs(abs(rotation_deg(align_matrix(eb, ea))) - 30) < 1e-6 or abs(abs(rotation_deg(align_matrix(eb, ea))) - 150) < 1e-6
    # gerade Kante aus mehreren Punkten zählt ganz
    P = [g.Polyline(np.array([[0, 0], [40, 0], [70, 0], [100, 0], [100, 20], [0, 20.]]), True)]
    assert abs(nearest_edge([("P", P)], 10, 0.1, 1).length - 100) < 1e-9


def test_ortho_point():
    from tilaser.core.geometry import ortho_point
    assert ortho_point((0, 0), (10, 3)) == (10, 0)
    assert ortho_point((0, 0), (-2, -9)) == (0, -9)
    assert ortho_point((5, 5), (8, 8)) == (8, 5)


class FakeMarlin:
    """Marlin-Attrappe: quittiert jede Zeile erst, wenn release() aufgerufen wird (Planner beschäftigt)."""

    def __init__(self):
        self.out, self.rx, self.received, self.owed = bytearray(), bytearray(), [], 0
        self.auto = False

    def read(self, n):
        time.sleep(0.001)
        d = bytes(self.out[:n])
        del self.out[:n]
        return d

    def write(self, data):
        for b in data:
            self.rx.append(b)
            if b == 10:
                self.received.append(self.rx.decode().strip())
                self.rx.clear()
                if self.auto:
                    self.out += b"ok\r\n"
                else:
                    self.owed += 1
        return len(data)

    def release(self):
        self.out += b"ok\r\n" * self.owed
        self.owed = 0

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def _wait(cond, timeout=3.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        time.sleep(0.005)
    return cond()


def test_marlin_pause_stop_immediate(monkeypatch):
    fake = FakeMarlin()
    monkeypatch.setattr(snd, "serial", type("S", (), {"Serial": staticmethod(lambda *a, **k: fake)}))
    c = snd.LaserConnection()
    monkeypatch.setattr(c, "_detect", lambda: None)
    c.connect("FAKE", 115200, "ping")
    try:
        c.start_job(["M3 S200", "G1 X1 F100", "G1 X2", "G1 X3", "M5"])
        assert _wait(lambda: fake.received == ["M3 S200"])
        c.pause()                                         # M3 noch nicht quittiert – M5 trotzdem sofort
        assert _wait(lambda: "M5" in fake.received, 1.0)
        fake.auto = True
        fake.release()
        time.sleep(0.1)
        assert fake.received == ["M3 S200", "M5"]          # in der Pause wird nichts nachgeschoben
        c.resume()                                        # Laser vor der nächsten Bewegung wieder an
        assert _wait(lambda: len(fake.received) >= 4)
        assert fake.received[2:4] == ["M3 S200", "G1 X1 F100"]
        assert _wait(lambda: not c.job_active)
        fake.auto = False
        c.start_job(["M3 S100", "G1 X5 F100", "M5"])
        assert _wait(lambda: fake.received[-1] == "M3 S100")  # hängt im Planner, noch kein ok
        c.stop()
        assert _wait(lambda: fake.received[-3:] == ["M410", "M5", "M9"], 1.0)
    finally:
        c.disconnect()


def test_grbl_error_pauses_job(monkeypatch):
    class ErrGrbl(FakeGrbl):
        def write(self, data):
            if b"X13 " in data or data.endswith(b"X13\n"):
                self.received.append(data.decode().strip())
                self.out += b"error:20\r\n"
                return len(data)
            return super().write(data)
    fake = ErrGrbl()
    monkeypatch.setattr(snd, "serial", type("S", (), {"Serial": staticmethod(lambda *a, **k: fake)}))
    states = []
    c = snd.LaserConnection(on_state=states.append)
    monkeypatch.setattr(c, "_detect", lambda: None)
    c.connect("FAKE", 115200, "grbl")
    try:
        c.start_job([f"G1 X{i}" for i in range(400)])
        assert _wait(lambda: "paused" in states)
        assert c.job_active and c.errors == 1 and "done" not in states
    finally:
        c.stop()
        c.disconnect()


def test_gcode_safety_invariants():
    import re as _re
    from tilaser.core.gcode import DIALECTS, fmt
    from tilaser.core.sender import clean_line
    pr = Project()
    sq = g.Polyline(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float), True)
    from tilaser.core.model import ImportedPath
    pr.add_vector_import("a", [ImportedPath(sq, "#ff0000", "#ff0000")])
    pr.add_vector_import("b", [ImportedPath(sq.transformed(g.translate(30, 0)), "#ff0000", "#ff0000")])
    pr.layers[0].mode = "fill"
    for dialect in DIALECTS:
        prof = MachineProfile()
        prof.apply_dialect(dialect)
        on, s = False, 0.0
        for line in (clean_line(x) for x in generate(pr, prof).lines):
            u = line.upper()
            if u.startswith(("M3", "M4")):
                on = True
            if u.startswith("M5"):
                on = False
            m = _re.search(r"S([\d.]+)", u)
            if m:
                s = float(m.group(1))
            if u.startswith("G0"):
                assert (not on) or (prof.inline_power and s == 0), (dialect, line)
            assert "NAN" not in u and "INF" not in u
    for bad in (float("nan"), float("inf"), 1e20):
        try:
            fmt(bad)
            raise AssertionError("fmt() muss ungültige Werte ablehnen")
        except ValueError:
            pass
    pr2 = Project()
    pr2.add_vector_import("n", [ImportedPath(sq, "#ff0000", "#ff0000")])
    pr2.items[0].paths[0].poly.pts[1, 0] = float("nan")
    res = generate(pr2, MachineProfile())
    assert any("ungültigen Koordinaten" in w for w in res.warnings)


def test_kerf_collapse_reported():
    from tilaser.core import offset as off
    outer = g.Polyline(np.array([[0, 0], [50, 0], [50, 50], [0, 50]], float), True)
    hole = g.Polyline(g.circle_points(25, 25, 0.1), True)
    for clipper in (off.pyclipper, None):
        saved = off.pyclipper
        off.pyclipper = clipper
        try:
            lost = []
            res = off.apply_kerf([outer, hole], 0.3, lost)
            assert len(res) == 1 and lost == [hole]
        finally:
            off.pyclipper = saved
    from tilaser.core.model import ImportedPath
    pr = Project()
    pr.add_vector_import("a", [ImportedPath(outer, "#ff0000", "#ff0000"), ImportedPath(hole, "#ff0000", "#ff0000")])
    pr.layers[0].kerf = 0.3
    assert any("Schnittbreite" in w for w in generate(pr, MachineProfile()).warnings)


def test_import_bombs_terminate():
    d = tempfile.mkdtemp()
    arc = os.path.join(d, "arc.dxf")
    with open(arc, "w") as f:
        f.write("0\nSECTION\n2\nENTITIES\n0\nARC\n8\n0\n10\n0\n20\n0\n40\n5\n50\n1e20\n51\n0\n"
                "0\nARC\n8\n0\n10\n0\n20\n0\n40\n5\n50\ninf\n51\n0\n0\nENDSEC\n0\nEOF\n")
    t0 = time.time()
    paths, _ = import_dxf(arc)
    assert len(paths) == 1 and time.time() - t0 < 2
    blk = ["0", "SECTION", "2", "BLOCKS", "0", "BLOCK", "2", "B0", "10", "0", "20", "0",
           "0", "LINE", "8", "0", "10", "0", "20", "0", "11", "1", "21", "1", "0", "ENDBLK"]
    for i in range(1, 8):
        blk += ["0", "BLOCK", "2", f"B{i}", "10", "0", "20", "0",
                "0", "INSERT", "8", "0", "2", f"B{i - 1}", "10", "0", "20", "0", "70", "100", "71", "100", "0", "ENDBLK"]
    blk += ["0", "ENDSEC", "0", "SECTION", "2", "ENTITIES", "0", "INSERT", "8", "0", "2", "B7", "10", "0", "20", "0",
            "0", "ENDSEC", "0", "EOF"]
    bomb = os.path.join(d, "bomb.dxf")
    with open(bomb, "w") as f:
        f.write("\n".join(blk) + "\n")
    t0 = time.time()
    try:
        import_dxf(bomb)
        raise AssertionError("Block-Bombe muss abgelehnt werden")
    except ValueError as e:
        assert "zu komplex" in str(e) and time.time() - t0 < 10
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><defs>'
             '<path id="g0" d="M0 0L1 1"/>']
    for i in range(1, 26):
        parts.append(f'<g id="g{i}"><use xlink:href="#g{i - 1}"/><use xlink:href="#g{i - 1}"/></g>')
    parts.append('</defs><use xlink:href="#g25"/></svg>')
    svg = os.path.join(d, "bomb.svg")
    with open(svg, "w") as f:
        f.write("".join(parts))
    t0 = time.time()
    try:
        import_svg(svg)
        raise AssertionError("use-Bombe muss abgelehnt werden")
    except ValueError as e:
        assert "zu komplex" in str(e) and time.time() - t0 < 10


def test_webserver_bad_content_length():
    import socket
    from tilaser.core.webserver import WebServer
    srv = WebServer(port=0, pin="123456", host="127.0.0.1")
    srv.start()
    try:
        port = srv._httpd.server_address[1]
        for cl in (b"-1", b"abc", b"999999"):
            c = socket.create_connection(("127.0.0.1", port))
            c.settimeout(3)
            c.sendall(b"POST /api/login HTTP/1.1\r\nHost: 127.0.0.1\r\nX-TiLaser: 1\r\nContent-Length: " + cl + b"\r\n\r\n{}")
            assert c.recv(20).startswith(b"HTTP/1.0 403")
            c.close()
    finally:
        srv.stop()


def test_webserver_pin_and_host():
    import socket
    from tilaser.core import webserver as ws
    assert ws.host_allowed("192.168.1.20:8080") and ws.host_allowed("[fe80::1]:8080")
    assert ws.host_allowed("localhost:8080") and ws.host_allowed(socket.gethostname() + ":8080")
    assert not ws.host_allowed("evil.example.com:8080") and not ws.host_allowed("") and not ws.host_allowed(None)
    for pin in ("", "12"):
        try:
            ws.WebServer(port=0, pin=pin, host="0.0.0.0").start()
            raise AssertionError("ohne PIN darf der Server nicht im Netz starten")
        except ValueError:
            pass
    srv = ws.WebServer(port=0, pin="123456", host="127.0.0.1")
    srv.start()
    try:
        c = socket.create_connection(("127.0.0.1", srv.port))
        c.settimeout(3)
        c.sendall(b"GET /api/status HTTP/1.1\r\nHost: rebind.attacker.net:8080\r\n\r\n")
        assert c.recv(20).startswith(b"HTTP/1.0 421")
        c.close()
        tok = srv.check_pin("123456")
        assert srv.authorized(tok)
        srv._sessions[tok] -= ws.SESSION_S + 1                 # Sitzung abgelaufen
        assert not srv.authorized(tok)
    finally:
        srv.stop()


def test_done_only_after_idle(monkeypatch):
    class BusyGrbl(FakeGrbl):
        busy_until = None

        def write(self, data):
            if data == b"?":
                run = self.busy_until is None or time.time() < self.busy_until
                self.out += (b"<Run|MPos:0,0,0|FS:100,0>\r\n" if run else b"<Idle|MPos:0,0,0|FS:0,0>\r\n")
                return 1
            n = super().write(data)
            if b"G1 X49" in data:
                self.busy_until = time.time() + 0.8          # Maschine fährt nach dem letzten ok noch
            return n
    fake = BusyGrbl()
    monkeypatch.setattr(snd, "serial", type("S", (), {"Serial": staticmethod(lambda *a, **k: fake)}))
    states = []
    c = snd.LaserConnection(on_state=states.append)
    monkeypatch.setattr(c, "_detect", lambda: None)
    c.connect("FAKE", 115200, "grbl")
    try:
        c.start_job([f"G1 X{i}" for i in range(50)])
        assert _wait(lambda: fake.received and fake.received[-1] == "G1 X49")
        t_last = time.time()
        assert _wait(lambda: "done" in states)
        assert time.time() - t_last > 0.5 and not c.job_active
    finally:
        c.disconnect()


def test_order_paths_matches_bruteforce():
    """Die beschleunigte Pfadoptimierung muss genau das Ergebnis des vollständigen Vergleichs liefern."""
    def brute(polys, cur):
        rem, out, cur = list(polys), [], np.asarray(cur, float)
        while rem:
            best = None
            for i, p in enumerate(rem):
                if p.closed:
                    d2 = np.sum((p.pts - cur) ** 2, axis=1)
                    k = int(np.argmin(d2))
                    cands = [(d2[k], i, "rot", k)]
                else:
                    cands = [(np.sum((p.pts[0] - cur) ** 2), i, "start", 0), (np.sum((p.pts[-1] - cur) ** 2), i, "rev", 0)]
                for c in cands:
                    if best is None or c[:2] < best[:2]:
                        best = c
            p = rem.pop(best[1])
            if best[2] == "rot":
                pts = p.pts[:-1] if np.allclose(p.pts[0], p.pts[-1]) else p.pts
                p = g.Polyline(np.roll(pts, -best[3], axis=0), True)
            elif best[2] == "rev":
                p = g.Polyline(p.pts[::-1].copy(), False)
            out.append(p)
            cur = p.loop_pts()[-1]
        return out
    rng = np.random.default_rng(7)
    for _ in range(15):
        ps = []
        for _ in range(int(rng.integers(1, 90))):
            c = np.round(rng.uniform(0, 100, 2))
            ps.append(g.Polyline(g.circle_points(*c, float(rng.choice([1, 2, 5]))), True) if rng.random() < 0.5
                      else g.Polyline(np.round(rng.uniform(0, 100, (int(rng.integers(2, 5)), 2))), False))
        ps += ps[:2]                                                       # Gleichstände
        a, b = g.order_paths(ps, (0, 0), False), brute(ps, (0, 0))
        assert all(np.array_equal(x.pts, y.pts) for x, y in zip(a, b)) and len(a) == len(b)
    many = [g.Polyline(g.circle_points(*rng.uniform(0, 300, 2), 2), True) for _ in range(3000)]
    t0 = time.time()
    assert len(g.order_paths(many, (0, 0), True)) == 3000
    assert time.time() - t0 < 8


def test_fill_union_and_resume_z():
    from tilaser.core.fill import hatch
    from tilaser.core.gcode import resume_lines, z_offset_at
    a = g.Polyline(np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float), True)
    b = g.Polyline(np.array([[5, 0], [15, 0], [15, 10], [5, 10]], float), True)
    hole = g.Polyline(np.array([[1, 1], [3, 1], [3, 9], [1, 9]], float), True)

    def length(segs):
        return sum(float(np.hypot(*(s[1] - s[0]))) for s in segs)
    eo = length(hatch([a, hole, b], 1.0, 0.0, True))                 # Even-Odd: Überlappung 5..10 frei
    un = length(hatch([a, hole, b], 1.0, 0.0, True, [0, 0, 1]))      # vereinigt, Loch in a bleibt
    assert abs(eo - (8 * 8 + 2 * 10)) < 1e-6 and abs(un - (8 * 13 + 2 * 15)) < 1e-6
    job = ["G21", "G90", "M4 S0", "G91", "G0 Z2", "G90", "G1 X1 S100", "G91", "G0 Z-0.5", "G90", "G1 X2", "G1 X3"]
    assert z_offset_at(job, 6) == 2 and z_offset_at(job, 11) == 1.5
    out = resume_lines(job, 10, MachineProfile())                   # Kopf neu auf Ausgangshöhe
    assert "G0 Z1.5" in out and out.index("G0 Z1.5") < out.index("G1 X3")
    out = resume_lines(job, 10, MachineProfile(), z_now=1.5)       # Kopf noch auf Abbruchhöhe
    assert not any(l.startswith("G0 Z") for l in out[:6])
    out = resume_lines(job, 10, MachineProfile(), z_now=2.0)
    assert "G0 Z-0.5" in out[:8]


class _MiniMonkeyPatch:
    """Ersatz für die pytest-Fixture ``monkeypatch``, wenn die Tests ohne pytest laufen (Build-Skripte)."""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value):
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        for target, name, value in reversed(self._undo):
            setattr(target, name, value)
        self._undo.clear()


def test_puzzle_joint():
    from tilaser.core.joints import puzzle_height, puzzle_joint

    def area(r):
        return abs(0.5 * float(np.sum(r[:, 0] * np.roll(r[:, 1], -1) - np.roll(r[:, 0], -1) * r[:, 1])))
    a = [np.array([[0, 0], [60, 0], [60, 30], [0, 30]], float)]
    b = [np.array([[60, 30], [0, 30], [0, 60], [60, 60]], float)]               # Kante gegenläufig
    for alt in (False, True):
        ra, rb, n = puzzle_joint(a, b, 10, 0, 0.0, alt)
        assert n == 1 and abs(area(ra[0]) + area(rb[0]) - 3600) < 1e-6          # lückenlos ineinander
        top, bottom = ra[0][:, 1].max(), ra[0][:, 1].min()
        assert top > 30 + 0.95 * puzzle_height(10)                              # Nase von A ragt nach B
        if alt:
            assert rb[0][:, 1].min() < 30 - 0.95 * puzzle_height(10)            # und eine von B nach A
        else:
            assert abs(area(ra[0]) - 1800) > 50 and bottom == 0
            knob = ra[0][(ra[0][:, 1] > 30.01) & (ra[0][:, 0] < 30)]                # erste Nase
            head = knob[knob[:, 1] > 30 + 0.6 * puzzle_height(10)]
            neck = knob[(knob[:, 1] > 31.19) & (knob[:, 1] < 32.51)]
            assert np.ptp(head[:, 0]) > np.ptp(neck[:, 0]) + 2                  # Kopf breiter als der Hals
    ra, rb, _ = puzzle_joint(a, b, 10, 2, 0.2, False)                           # Spiel: Fuge bleibt frei
    assert 3600 - (area(ra[0]) + area(rb[0])) > 0.5
    for bad in (dict(size=40), dict(size=10, count=8)):
        try:
            puzzle_joint(a, b, bad["size"], bad.get("count", 0))
            raise AssertionError("muss abgelehnt werden")
        except ValueError:
            pass


def test_snap_lock():
    from tilaser.core.joints import SnapSpec, snap_height, snap_lock
    a = [np.array([[0, 0], [60, 0], [60, 30], [0, 30]], float)]
    b = [np.array([[0, 30], [60, 30], [60, 60], [0, 60]], float)]
    spec = SnapSpec()
    ra, rb, n, shift = snap_lock(a, b, spec)
    assert n == 1 and len(rb) == 1 + 2 and np.allclose(rb[0], b[0])                    # B unverändert + 2 Schlitze
    assert np.allclose(shift, [0, -(snap_height(spec) + spec.gap)])
    slot = rb[1]
    assert abs(np.ptp(slot[:, 0]) - (spec.width + spec.clearance)) < 1e-9
    assert abs(np.ptp(slot[:, 1]) - (spec.thickness + spec.clearance)) < 1e-9
    assert abs(slot[:, 1].min() - (30 + spec.inset - spec.clearance / 2)) < 1e-9
    top = ra[0][:, 1].max() - shift[1]                                                  # Laschen vor dem Verschieben
    assert abs(top - (30 + snap_height(spec))) < 1e-9 and ra[0][:, 1].max() < 30 - spec.gap + 1e-9
    xs = ra[0][:, 0]
    tab = ra[0][ra[0][:, 1] > 30 + shift[1] + 1e-6]
    assert np.ptp(tab[:, 0]) > spec.width                                              # Haken stehen über
    assert abs(np.ptp(slot[:, 0]) - spec.width) < 2 * spec.barb and xs.min() == 0
    for bad in (SnapSpec(barb=1.0), SnapSpec(slit=7.5), SnapSpec(inset=40)):
        try:
            snap_lock(a, b, bad)
            raise AssertionError("muss abgelehnt werden")
        except ValueError:
            pass


def test_hammer_joint():
    from tilaser.core.clip import inside
    from tilaser.core.joints import HammerSpec, hammer_joint, hammer_tenon_profile
    a = [np.array([[0, 0], [80, 0], [80, 40], [0, 40]], float)]
    b = [np.array([[80, 40], [0, 40], [0, 80], [80, 80]], float)]
    spec = HammerSpec(thickness=10, protrude=0.074)                         # wie die 10-mm-Originalzeichnung
    ra, rb, n, shift = hammer_joint(a, b, spec)
    H = 10.074
    assert n == 1 and len(ra) == 2 and len(rb) == 2
    assert np.allclose(shift, [0, -(H + spec.wedge_out + spec.gap)])
    up = ra[0] - shift
    assert abs(up[:, 1].max() - (40 + H + spec.wedge_out)) < 1e-9              # Keil steht über den Zapfen
    hole = rb[1]
    assert abs(np.ptp(hole[:, 0]) - 20.0) < 1e-9 and abs(np.ptp(hole[:, 1]) - 10.075) < 1e-9
    edge, inner = hammer_tenon_profile(0.0, spec)
    E, I = np.array(edge), np.array(inner)
    tip = E[np.isclose(E[:, 1], H)]
    assert abs(np.ptp(tip[:, 0]) - 20.15) < 1e-6                              # Zapfen 20,15 mm (Übermaß)
    head = E[E[:, 1] > H + 1e-9]
    assert abs(head[:, 0].max() - 3.3) < 0.05                                  # Keilkopf wie Original (±3,27)
    assert abs(I[:, 1].min() + 20) < 1e-9 and abs(I[:, 1].max() - (H - 6)) < 1e-9   # Schlitz bis zum Steg
    # Stege: zwischen Unterkante des Einzelschnitts und Oberkante der Schlitzkontur 1 mm Material
    cut_bottom = E[E[:, 1] < H - 1e-9][:, 1]
    assert abs(cut_bottom[(cut_bottom > 0)].min() - (H - 5)) < 1e-9
    # der Keil (innerhalb der Kontur von A) ist mit A verbunden: Punkt im Keil liegt im Material von A
    probe = np.array([[40.0, 40 + H - 1]]) + shift
    assert inside(probe, ra[:1])[0] and not inside(probe, ra[1:])[0]
    for bad in (HammerSpec(wedge_out=30), HammerSpec(depth=45), HammerSpec(inset=60), HammerSpec(spread=0)):
        try:
            hammer_joint(a, b, bad)
            raise AssertionError("muss abgelehnt werden")
        except ValueError:
            pass


def test_knuckle_hinge():
    from tilaser.core.joints import KnuckleSpec, knuckle_hinge, knuckle_hole, knuckle_plate, knuckle_radius
    spec = KnuckleSpec(thickness=3, knuckles=5, hinges=2, inset=10)
    A = [np.array([[0, 0], [100, 0], [100, 50], [0, 50]], float)]               # Rückwand Box
    B = [np.array([[100, 50], [0, 50], [0, 80], [100, 80]], float)]             # Rückwand Deckel (gegenläufig)
    sa, sb, plates, pins, L = knuckle_hinge(A, B, spec)
    assert L == 100 and len(plates) == 10 and len(pins) == 2
    # Vierkantstift: nur die beiden äußeren Knöchel (1 und 5, beide an A) mit Vierkantloch, sonst rund
    assert [p for p, _ in plates].count("A") == 6
    assert [len(rr[1]) for _, rr in plates[:5]] == [4, 36, 36, 36, 4]
    assert abs(np.ptp(plates[0][1][1][:, 0]) - (3 + 0.1)) < 1e-9
    even = knuckle_hinge(A, B, KnuckleSpec(knuckles=4, hinges=1))[2]             # gerade: erster und letzter von A
    assert [len(rr[1]) for _, rr in even] == [4, 36, 4, 36]
    two = knuckle_hinge(A, B, KnuckleSpec(knuckles=2, hinges=1))[2]
    assert [len(rr[1]) for _, rr in two] == [4, 36]
    assert len(sa) == 2 * 3 * 2 and len(sb) == 2 * 2 * 2                         # 2 Zinken je Knöchel
    assert all(r[:, 1].max() <= 50 + 1e-9 for r in sa) and all(r[:, 1].min() >= 50 - 1e-9 for r in sb)
    xs = sorted({round(float(r[:, 0].min()), 3) for r in sa + sb})
    assert abs(xs[0] - (10 - 0.1)) < 1e-9 and abs(xs[-1] - (100 - 10 - 3 - 0.1)) < 1e-9   # Abstand vom Ende
    assert all(abs(np.ptp(r[:, 0]) - 3.2) < 1e-9 for r in sa + sb)
    outer, hole = knuckle_plate(spec)
    R = knuckle_radius(spec)
    assert abs(outer[:, 1].min() + 3) < 1e-9                                     # Zinken so tief wie die Wand
    assert abs(outer[:, 1].max() - 2 * R) < 0.05 and abs(outer[:, 0].max() - spec.length) < 1e-9
    end = outer[np.isclose(outer[:, 0], spec.length)]
    assert abs(end[:, 1].max() - spec.end_height) < 1e-9                         # abgeschrägt
    assert abs(np.ptp(hole[:, 0]) - knuckle_hole(spec)) < 0.01
    one = knuckle_hinge(A, B, KnuckleSpec(hinges=1, pin=2.0))
    assert len(one[2]) == 5 and one[3] == [] and all(len(rr[1]) == 36 for _, rr in one[2])   # Rundstab: rund
    three = knuckle_hinge(A, B, KnuckleSpec(hinges=3))                          # breite Teile: mehrere Scharniere
    starts = sorted({round(float(r[:, 0].min()) + 0.1, 3) for r in three[0]})
    assert len(three[2]) == 15 and len(three[3]) == 3
    assert abs(starts[0] - 10) < 1e-6 and abs(starts[-1] - (100 - 10 - 15 + 4 * 3)) < 1e-6         # letzter Knöchel
    for bad in (KnuckleSpec(length=10), KnuckleSpec(knuckles=40), KnuckleSpec(inset=45), KnuckleSpec(length=60),
                KnuckleSpec(hinges=6)):
        try:
            knuckle_hinge(A, B, bad)
            raise AssertionError("muss abgelehnt werden")
        except ValueError:
            pass


def test_bend_between_edges():
    from tilaser.core.clip import inside
    from tilaser.core.edgealign import Edge, bend_length
    from tilaser.core.patterns import bend_between
    A = [np.array([[0, 0], [100, 0], [100, 60], [0, 60]], float),
         np.array([[10, 10], [20, 10], [20, 20], [10, 20]], float)]                # mit Loch
    B = [np.array([[150, 0], [230, 0], [230, 40], [150, 40]], float)]
    target = Edge("a", np.array([100., 60.]), np.array([0., 60.]), np.array([0., 1.]))
    moving = Edge("b", np.array([150., 40.]), np.array([150., 0.]), np.array([-1., 0.]))
    m, merged, slits, L, width = bend_between(A, B, moving, target, 10, 90, 3)
    assert abs(L - np.pi / 2 * 11.5) < 1e-9 and abs(width - 40) < 0.02
    outer = max(merged, key=lambda p: np.ptp(p.pts[:, 1]))
    assert abs(outer.pts[:, 1].max() - (60 + L + 80)) < 1e-6                      # B um 90° gedreht dahinter
    assert len(merged) == 2                                                       # Loch von A bleibt
    pts = np.vstack([q.pts for q in slits])
    assert len(slits) > 20 and pts[:, 1].min() >= 60 - 1e-6 and pts[:, 1].max() <= 60 + L + 1e-6
    assert inside(pts * 0.999 + np.array([50, 69]) * 0.001, [p.pts for p in merged]).all()
    tiny = [np.array([[150, 0], [153, 0], [153, 2], [150, 2]], float)]
    tiny_edge = Edge("b", np.array([150., 2.]), np.array([150., 0.]), np.array([-1., 0.]))
    for bb, edge, r, ang in ((B, moving, 0.5, 5), (tiny, tiny_edge, 10, 90)):  # zu kurz / zu schmal
        try:
            bend_between(A, bb, edge, target, r, ang, 3)
            raise AssertionError("muss abgelehnt werden")
        except ValueError:
            pass
    assert abs(bend_length(0, 180, 4) - np.pi * 2) < 1e-9


def test_numbering():
    from tilaser.core.clip import distance_to_edges, inside
    from tilaser.core.numbering import group_parts, label_spot, number_paths, reading_order
    from tilaser.core.text_import import default_font

    def tf(rings, ang, dx, dy, flip=False):
        m = g.translate(dx, dy) @ g.rotate(ang) @ (g.scale(-1, 1) if flip else np.eye(3))
        return [g.apply_matrix(m, r) for r in rings]
    L = [np.array([[0, 0], [40, 0], [40, 10], [10, 10], [10, 30], [0, 30]], float)]
    sq = [np.array([[0, 0], [20, 0], [20, 20], [0, 20]], float), g.circle_points(10, 10, 4)]
    sq2 = [np.array([[0, 0], [20, 0], [20, 20], [0, 20]], float), g.circle_points(6, 6, 4)]   # Loch woanders
    rect = [np.array([[0, 0], [40, 0], [40, 10], [0, 10]], float)]
    parts = [L, tf(L, 37, 100, 50), tf(L, 200, -30, 80, True), sq, tf(sq, 33, 60, -40), sq2, rect, tf(rect, 90, 0, 0)]
    assert group_parts(parts) == [0, 0, 1, 2, 2, 3, 4, 4]                      # gespiegelt = anders
    assert group_parts(parts, mirror=True) == [0, 0, 0, 1, 1, 2, 3, 3]
    grid = [tf(rect, 0, x, y) for y in (0, 50) for x in (100, 0, 50)]
    assert reading_order(grid) == [4, 5, 3, 1, 2, 0]                            # oben links zuerst
    spot, room = label_spot(rect)
    assert abs(room - 5) < 0.1 and abs(spot[0] - 20) < 1.5                      # mittig im Rechteck
    font = default_font()
    if font:
        polys, h = number_paths("12", sq, font, 5.0)
        pts = np.vstack([p.pts for p in polys])
        assert polys and 1.5 <= h <= 5.0 and inside(pts, sq).all()
        assert distance_to_edges(pts, sq).min() >= 0.8 - 1e-6                     # Randabstand
        assert number_paths("12345", [np.array([[0, 0], [4, 0], [4, 2], [0, 2]], float)], font)[0] == []



def test_group_parts_nested_and_fast():
    import time
    from tilaser.core import geometry as g

    def sq(x, y, w):
        return g.Polyline(np.array([[x, y], [x + w, y], [x + w, y + w], [x, y + w]], float), True)
    # Platte mit Loch, darin eine Insel mit eigenem Loch, dazu eine offene Linie
    polys = [sq(0, 0, 100), sq(10, 10, 50), sq(20, 20, 30), sq(30, 30, 10), sq(70, 70, 10),
             g.Polyline(np.array([[200, 0], [210, 0]], float), False)]
    assert g.group_parts(polys) == [[0, 1, 4], [2, 3], [5]]
    # viele Teile mit je zwei Löchern: früher quadratisch in Python, jetzt vorgefiltert
    many = []
    for i in range(30):
        for j in range(30):
            many += [sq(i * 12, j * 12, 10), sq(i * 12 + 1, j * 12 + 1, 3), sq(i * 12 + 6, j * 12 + 6, 3)]
    t0 = time.time()
    parts = g.group_parts(many)
    assert len(parts) == 900 and all(len(p) == 3 and p[0] % 3 == 0 for p in parts)
    assert time.time() - t0 < 5


def test_spring_latch():
    from tilaser.core.clip import inside
    from tilaser.core.joints import LatchSpec, latch_spring_line, spring_latch
    A = [np.array([[0, 0], [100, 0], [100, 60], [0, 60]], float)]              # Vorderwand Deckel (Feder)
    B = [np.array([[100, 0], [0, 0], [0, -40], [100, -40]], float)]            # Vorderwand Box
    spec = LatchSpec()
    ra, rb, n, d = spring_latch(A, B, spec)
    assert n == 1 and len(ra) == 1 and len(rb) == 1
    area = lambda r: 0.5 * abs(np.dot(r[:, 0], np.roll(r[:, 1], -1)) - np.dot(r[:, 1], np.roll(r[:, 0], -1)))
    assert area(ra[0]) < 6000 and area(rb[0]) < 4000                           # Fenster bzw. Aussparung
    # Zunge mit Haken ragt in B, Aussparung in B ist größer als die Zunge (Spiel), keine Überlappung
    assert ra[0][:, 1].min() < -d["depth"] + 1e-6 and rb[0][:, 1].max() <= 1e-9
    low = ra[0][ra[0][:, 1] < -0.5]
    probe = np.array([[(low[:, 0].min() + low[:, 0].max()) / 2, -d["depth"] / 2]])
    assert inside(probe, ra)[0] and not inside(probe, rb)[0]
    # Feder: 3 Schenkel, runde Wendungen; Mittellinie endet an der Drucktaste
    line = latch_spring_line(spec)
    assert abs(line[0, 1] - d["top"]) < 1e-9 and abs(line[-1, 1] - d["yt"]) < 1e-9
    assert len(line) > 20
    # Drucktaste breiter als der Durchlass (liegt beim Ziehen auf dem Steg auf)
    assert d["wb2"] > d["w2"] + spec.catch + spec.clearance
    # zwei Verschlüsse, gerade Schenkelzahl abgelehnt, zu kurze Kante abgelehnt
    assert spring_latch(A, B, LatchSpec(count=2, inset=5))[2] == 2
    for bad in (LatchSpec(legs=4), LatchSpec(count=4, inset=20)):
        try:
            spring_latch(A, B, bad)
            raise AssertionError("ValueError erwartet")
        except ValueError:
            pass


def test_slider_latch():
    from tilaser.core.clip import inside
    from tilaser.core.joints import SliderLatchSpec, slider_latch, slider_plate, slider_spring_line
    L = [np.array([[0, 0], [120, 0], [120, 70], [0, 70]], float)]               # Deckel
    W = [np.array([[120, 0], [0, 0], [0, -40], [120, -40]], float)]             # Vorderwand
    spec = SliderLatchSpec()
    lid_slots, wall_slots, plate, keys, d = slider_latch(L, W, spec)
    assert len(lid_slots) == 2 and len(wall_slots) == 2 and len(plate) == 4 and len(keys) == 2
    # Hakenschlitze eine Wandstärke hinter der Deckelkante, Halterschlitze in der Wand auf Höhe der Öse
    assert all(abs(r[:, 1].min() - (spec.wall - spec.clearance / 2)) < 1e-9 for r in lid_slots)
    assert all(abs(r[:, 1].mean() - d["key_y"]) < 1e-9 for r in wall_slots)
    # Riegel: Haken oben, Fenster, Öse und Langloch liegen im Riegel; Halter sitzt in der Öse
    pl, hx = slider_plate(spec)
    assert pl[0][:, 1].max() > spec.lid + spec.clearance and len(hx) == 2
    for r in pl[1:]:
        assert inside(r, [pl[0]]).all()
    assert inside(np.array([[d["key_a"], d["key_y"]]]), [pl[2]])[0]
    line = slider_spring_line(spec)
    assert abs(line[-1, 1] - d["win_bot"]) < 1e-9 and abs(line[0, 0] - d["ring_r"]) < 1e-9
    # Langloch erlaubt genau den Federweg
    assert abs(np.ptp(pl[3][:, 0]) - (d["hole_w"] + d["travel"])) < 1e-9
    for bad in (SliderLatchSpec(legs=4), SliderLatchSpec(length=40)):
        try:
            slider_latch(L, W, bad)
            raise AssertionError("ValueError erwartet")
        except ValueError:
            pass

if __name__ == "__main__":
    import inspect
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            params = inspect.signature(fn).parameters
            mp = _MiniMonkeyPatch()
            try:
                fn(**({"monkeypatch": mp} if "monkeypatch" in params else {}))
            finally:
                mp.undo()
            print("OK ", name)
    print("Ausgaben in", TMP)

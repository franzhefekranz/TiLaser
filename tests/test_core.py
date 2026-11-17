"""Headless-Tests für den Kern (ohne GUI, ohne Laser).  Start:  python -m pytest tests  oder  python tests/test_core.py"""
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
    assert np.allclose(r, 10, atol=1e-6)
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


# ------------------------------------------------------------ Sender mit simuliertem GRBL
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)
    print("Ausgaben in", TMP)

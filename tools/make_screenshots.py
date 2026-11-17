"""Erzeugt die Screenshots für die Bedienungsanleitung (docs/screenshots).

Läuft ohne Bildschirm (Qt-Plattform 'offscreen'), z. B. im GitHub-Workflow 'Screenshots':
    QT_QPA_PLATFORM=offscreen python tools/make_screenshots.py docs/screenshots
"""
from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["TILASER_HOME"] = tempfile.mkdtemp()          # sauberes Profil, keine eigenen Einstellungen
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np                                            # noqa: E402
from PIL import Image, ImageDraw, ImageFont                   # noqa: E402
from PyQt6.QtCore import QPoint, Qt                           # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QToolBar  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "screenshots")
os.makedirs(OUT, exist_ok=True)

app = QApplication(sys.argv)
app.setStyle("Fusion")

# Meldungsfenster würden ohne Bildschirm blockieren
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)

from tilaser.core import camera as cam                       # noqa: E402
from tilaser.core import geometry as g                       # noqa: E402
from tilaser.core.gcode import MachineProfile                # noqa: E402
from tilaser.core.model import Item, VectorPath, new_id      # noqa: E402
from tilaser.core.nesting import NestSettings, nest          # noqa: E402
from tilaser.core.storage import load_machine_templates      # noqa: E402
from tilaser.core.text_import import default_font, text_to_polylines  # noqa: E402
from tilaser.gui import dialogs as dl                        # noqa: E402
from tilaser.gui.main_window import MainWindow               # noqa: E402

SHOTS = []


def pump(n=8):
    for _ in range(n):
        app.processEvents()


def shot(widget, name, title=""):
    pump()
    path = os.path.join(OUT, f"{name}.png")
    widget.grab().save(path)
    SHOTS.append((name, title))
    print("gespeichert:", path)


def dialog_shot(dlg, name, title="", size=None):
    if size:
        dlg.resize(*size)
    dlg.show()
    pump()
    shot(dlg, name, title)
    dlg.close()
    pump()


def demo_image(path):
    """Synthetisches Graustufenbild (Verlauf + Kreis + Schrift) für die Rastergravur."""
    w, h = 600, 400
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - w * 0.35, yy - h * 0.5) / (h * 0.6)
    img = np.clip(255 * (0.15 + 0.85 * r), 0, 255).astype(np.uint8)
    im = Image.fromarray(img).convert("L")
    d = ImageDraw.Draw(im)
    d.ellipse((380, 90, 560, 270), fill=40)
    try:
        f = ImageFont.truetype(default_font(), 54) if default_font() else None
    except Exception:
        f = None
    d.text((40, 300), "TiLaser", fill=20, font=f)
    im.save(path)


# ------------------------------------------------------------------ Hauptfenster vorbereiten
w = MainWindow()
w.resize(1440, 900)
w.show()
pump()

tmpl = load_machine_templates()[0]
for k, v in MachineProfile.from_dict(tmpl).__dict__.items():
    setattr(w.profile, k, v)
w.canvas.set_bed(w.profile.bed_w, w.profile.bed_h)
w.layers.set_machine(w.profile.name)
w._update_title()

tmp = tempfile.mkdtemp()
img_path = os.path.join(tmp, "foto.png")
demo_image(img_path)
w.import_files([os.path.join(ROOT, "beispiele", "testplatte.svg"), img_path])
plate, photo = w.project.items[0], w.project.items[1]
plate.x, plate.y = 80, 320
photo.x, photo.y, photo.scale = 300, 300, 1.4

font = default_font()
if font:
    polys = text_to_polylines("TiLaser", font, 22)
    bb = g.bbox(polys)
    layer = w.project.add_layer("Text Gravur", "#1e88e5", "fill")
    t = Item(id=new_id("I"), name="Text: TiLaser", kind="vector", x=90, y=190)
    t.paths = [VectorPath(p.transformed(g.translate(-(bb[0] + bb[2]) / 2, -(bb[1] + bb[3]) / 2)), layer.id)
               for p in polys]
    w.project.items.append(t)
w._on_shape_drawn("rect", {"p0": (40, 40), "p1": (140, 110), "radius": 8})
w._on_shape_drawn("ellipse", {"p0": (170, 50), "p1": (240, 110)})
w._on_shape_drawn("polygon", {"p0": (270, 40), "p1": (350, 120), "sides": 5, "star": 0.45})
w.canvas.rebuild_all()
w.layers.refresh()
w._invalidate()
w.canvas.zoom_fit()
w.canvas.select_only([plate.id])
pump()

# ------------------------------------------------------------------ Übersicht
shot(w, "01_hauptfenster", "Hauptfenster")

for tb in w.findChildren(QToolBar):
    if tb.windowTitle() == "Werkzeuge":
        shot(tb, "02_werkzeugleiste", "Werkzeugleiste")
    elif tb.windowTitle() == "Objekt":
        shot(tb, "03_objektleiste", "Objektleiste")
    elif tb.windowTitle() == "Zeichnen":
        shot(tb, "04_zeichenwerkzeuge", "Zeichenwerkzeuge")

side = w.sidebar.widget()
w.tabs.setCurrentIndex(0)
w.layers.list.setCurrentRow(0)
shot(side, "05_ebenen", "Ebenen")

# Ebenen-Formular für eine Raster-Ebene
for i, l in enumerate(w.project.layers):
    if l.mode == "raster":
        w.layers.list.setCurrentRow(i)
shot(side, "06_ebene_raster", "Raster-Ebene")

# Auswahl mit Anfassern (Ausschnitt der Arbeitsfläche)
star = w.project.items[-1]
w.canvas.select_only([star.id])
shot(w.canvas, "07_auswahl_anfasser", "Auswahl mit Anfassern")

# Knotenbearbeitung
w.canvas.set_tool("node")
w.canvas._node["gi"] = w.canvas.graphics[star.id]
w.canvas._node["sel"] = (0, 0)
w.canvas.node_command("curve")
w.canvas._update_nodes()
shot(w.canvas, "08_knoten", "Knotenbearbeitung")
w.canvas.set_tool("select")

# Kontextmenü
w.canvas.select_only([plate.id])
menu = QMenu(w)
items = w._selected_items()
menu.addAction(w.a_group)
menu.addAction(w.a_ungroup)
menu.addSeparator()
menu.addAction(w.a_cut)
menu.addAction(w.a_copy)
menu.addAction(w.a_paste)
lm = menu.addMenu("Ebene zuweisen")
w._fill_layer_menu(lm)
menu.addSeparator()
for a in (w.a_center, w.a_origin, w.a_dup, w.a_array, w.a_nest):
    menu.addAction(a)
menu.addSeparator()
menu.addAction(w.a_del)
menu.ensurePolished()
menu.adjustSize()
shot(menu, "09_kontextmenue", "Kontextmenü")

# Werkzeugweg-Vorschau und Simulation
w.a_preview.setChecked(True)
w._refresh_preview()
shot(w, "10_vorschau", "Werkzeugweg-Vorschau")
w._sim_slider(420)
shot(w.canvas, "11_simulation", "Simulation")
w.a_preview.setChecked(False)
w._refresh_preview()

# Seitenleisten-Tabs
console = w.machine.console
for line in (">> $$", "<< $30=1000", "<< $32=1", "<< $130=400.000", "<< $131=400.000", "<< ok",
             "<< Grbl 1.1h ['$' for help]", ">> $J=G91 G21 X10 Y0 F3000", "<< ok"):
    console.appendPlainText(line)
w.machine.lbl_fw.setText("Firmware: GRBL / FluidNC")
w.machine.lbl_state.setText("Bereit")
w.machine.lbl_pos.setText("X 0.00  Y 0.00")
for idx, name, title in ((1, "12_verbindung", "Verbindung"), (2, "13_bewegen", "Bewegen"),
                         (3, "14_konsole", "Konsole")):
    w.tabs.setCurrentIndex(idx)
    shot(side, name, title)

# Tab „Kamera“ mit synthetischem Live-Bild (Holzplatte, halb gravierter Kreis, Laserkopf)
from PyQt6.QtGui import QImage                                # noqa: E402
lv = Image.new("RGB", (640, 480), (196, 160, 112))
d = ImageDraw.Draw(lv)
for yy in range(0, 480, 9):
    d.line((0, yy, 640, yy + 14), fill=(184, 148, 100), width=2)
d.rectangle((150, 110, 490, 380), outline=(120, 90, 60), width=3)
d.arc((220, 140, 420, 340), 200, 470, fill=(60, 40, 25), width=7)
d.rectangle((395, 60, 475, 150), fill=(40, 40, 44))
d.ellipse((425, 160, 445, 180), fill=(255, 230, 255))
d.line((435, 150, 435, 170), fill=(160, 80, 255), width=3)
qlv = QImage(np.ascontiguousarray(np.asarray(lv)).data, 640, 480, 640 * 3, QImage.Format.Format_RGB888).copy()
cp = w.camera_panel
cp.dev.clear()
cp.dev.addItem("USB-Kamera")
cp.dev.setEnabled(True)
cp.view.set_image(qlv)
cp.view.overlay = "● REC 03:42"
cp.btn_on.setChecked(True)
cp.btn_on.setText("■ Ausschalten")
cp.btn_rec.setChecked(True)
cp.btn_rec.setText("■ Aufnahme beenden")
cp.cb_auto.setChecked(True)
cp.cb_rec.setChecked(True)
cp.lbl_status.setText("Live: USB-Kamera")
w.tabs.setCurrentWidget(cp)
shot(side, "30_kamera_live", "Live-Kamera")
cp.view.clear()
w.tabs.setCurrentIndex(0)

# ------------------------------------------------------------------ Dialoge
dialog_shot(dl.MachineDialog(w.profile, w), "20_maschine", "Maschineneinstellungen", (560, 760))
dialog_shot(dl.TextDialog(w.project, w), "21_text", "Text einfügen")
dialog_shot(dl.TextPathDialog(w.project, w), "22_text_auf_pfad", "Text auf Pfad")
dialog_shot(dl.ArrayDialog((40, 40, 140, 110), w), "23_array", "Array")
dialog_shot(dl.OffsetDialog(w), "24_offset", "Offset")
dialog_shot(dl.TraceDialog(w.project, w), "25_nachzeichnen", "Bild nachzeichnen")
dialog_shot(dl.MaterialTestDialog(w.profile, w), "26_materialtest", "Materialtest", (480, 640))
dialog_shot(dl.NestDialog(w.profile.bed_w, w.profile.bed_h, None, w), "27_nesting_dialog", "Nesting")

# Kamera: Passmarken anklicken (synthetisches Kamerabild)
cimg = Image.new("RGB", (800, 600), (150, 120, 90))
d = ImageDraw.Draw(cimg)
pts = [(100 + 300 * (i % 3), 500 - 200 * (i // 3)) for i in range(9)]
for x, y in pts:
    d.line((x - 12, y, x + 12, y), fill=(30, 20, 10), width=3)
    d.line((x, y - 12, x, y + 12), fill=(30, 20, 10), width=3)
pick = dl.PointPickDialog(np.asarray(cimg), w, cam.marker_labels(9))
pick.resize(900, 700)
pick.show()
pump()
for x, y in pts[:4]:
    pick._add(x, y)
shot(pick, "28_kamera_marken", "Kamera ausrichten")
pick.close()

# ------------------------------------------------------------------ Nesting-Ergebnis
w._checkpoint("Nesting")
parts = [it for it in w.project.items if it.kind == "vector"]
nest(w.project, parts, NestSettings(x=0, y=0, width=250, height=180, spacing=3, margin=3, copies=2), thorough=False)
fl = w.project.add_layer("Nesting-Grundfläche", "#757575", "cut")
fl.output = False
fr = Item(id=new_id("I"), name="Grundfläche", kind="vector", x=125, y=90)
from tilaser.core import shapes                               # noqa: E402
fr.paths = [VectorPath(shapes.rectangle(250, 180), fl.id)]
w.project.items.append(fr)
photo.x, photo.y = 330, 300
w.canvas.rebuild_all()
w.layers.refresh()
w._invalidate()
w.canvas.zoom_fit()
shot(w.canvas, "29_nesting_ergebnis", "Nesting-Ergebnis")

# Symbole der Werkzeugleisten für die Anleitung (weiße Kachel, 40 × 40 px)
from PyQt6.QtCore import QByteArray, QRectF                     # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter, QPen         # noqa: E402
from PyQt6.QtSvg import QSvgRenderer                           # noqa: E402
from tilaser.gui import icons as ic                            # noqa: E402

DOC_ICONS = ["new", "open", "save", "import", "text", "fit", "toolpath", "play", "laser", "group", "ungroup",
             "center", "origin", "duplicate", "delete", "select", "rect", "ellipse", "polygon", "line", "node"]
for n in DOC_ICONS:
    img = QImage(40, 40, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#c9ced3"), 0.8))
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(0.5, 0.5, 39, 39), 6.5, 6.5)
    QSvgRenderer(QByteArray(ic.svg(n, "#2b2f33").encode())).render(p, QRectF(4, 4, 32, 32))
    p.end()
    img.save(os.path.join(OUT, f"icon_{n}.png"))

print(f"{len(SHOTS)} Screenshots in {OUT}")
w.close()

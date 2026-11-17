"""Hauptfenster von TiLaser."""
from __future__ import annotations

import copy
import os

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QColor, QKeySequence
from PyQt6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QDockWidget, QFileDialog, QFrame, QInputDialog,
                             QComboBox, QLabel, QMainWindow, QMenu, QMessageBox, QScrollArea, QSlider, QTabWidget,
                             QDoubleSpinBox, QSpinBox, QToolBar, QToolButton,
                             QVBoxLayout, QWidget)

from .. import __version__
from ..core import geometry as g
from ..core import storage
from ..core.dxf_import import import_dxf
from ..core import editing as ed
from ..core.gcode import DIALECTS, frame_gcode, generate, job_project, origin_offset, resume_lines
from ..core.materialtest import build_project
from ..core.trace import trace_image
from ..core.model import MODE_LABELS, Item, Project, VectorPath, new_id
from ..core.raster import load_image_gray
from ..core.svg_import import import_svg
from ..core.text_import import text_to_polylines
from .canvas import LaserCanvas
from .dialogs import (ArrayDialog, CameraGrabber, GcodeDialog, MachineDialog, MaterialTestDialog, OffsetDialog,
                      PointPickDialog, TextDialog, TextPathDialog, TraceDialog, camera_devices)
from ..core.pdf_import import import_pdf
from ..core.text_import import text_on_path
from ..core import boolean as bo
from ..core import camera as cam
from ..core import shapes
from ..core.export import export_dxf, export_svg
from .machine_panel import MachinePanel
from .panels import LayerPanel, ObjectPanel, color_icon

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")
MAX_IMAGE_PX = 4000


def fmt_time(s: float) -> str:
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d} h" if h else f"{m}:{sec:02d} min"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TiLaser")
        self.resize(1400, 900)
        self.project = Project()
        self.profile = storage.load_machine()
        self.project_path: str | None = None
        self.result = None
        self._result_key = None
        self.undo = ed.UndoStack()
        self._clipboard: list[Item] = []
        self.last_dir = os.path.expanduser("~")

        self.canvas = LaserCanvas()
        self.setCentralWidget(self.canvas)
        self.canvas.set_bed(self.profile.bed_w, self.profile.bed_h)
        self.canvas.set_project(self.project)

        self.layers = LayerPanel()
        self.objects = ObjectPanel()
        self.machine = MachinePanel(self.profile, self)
        self.layers.set_project(self.project)
        self.objects.set_project(self.project)

        # Rechte Seitenleiste: Tabs + immer sichtbare Auftragsleiste (Start/Pause/Stopp)
        self.tabs = QTabWidget()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(self.layers)
        self.tabs.addTab(sa, "Ebenen")
        self.tabs.addTab(self.machine.w_connection, "Verbindung")
        self.tabs.addTab(self.machine.w_jog, "Bewegen")
        self.tabs.addTab(self.machine.w_console, "Konsole")
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(self.tabs, 1)
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.machine.w_jobbar)
        sv.addWidget(frame)
        dock = QDockWidget("Werkzeuge", self)
        dock.setObjectName("sidebar")
        dock.setWidget(side)
        dock.setMinimumWidth(340)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.sidebar = dock

        self.lbl_pos = QLabel("")
        self.lbl_info = QLabel("")
        self.statusBar().addWidget(self.lbl_pos)
        self.statusBar().addPermanentWidget(self.lbl_info)

        self._build_actions()

        self.canvas.selectionChanged.connect(self._on_selection)
        self.canvas.itemMoved.connect(self._on_item_moved)
        self.canvas.mouseMoved.connect(lambda x, y: self.lbl_pos.setText(f"X {x:7.2f}   Y {y:7.2f} mm"))
        self.canvas.filesDropped.connect(self.import_files)
        self.canvas.deleteRequested.connect(self.delete_selected)
        self.canvas.contextMenuRequested.connect(self._context_menu)
        self.canvas.aboutToModify.connect(lambda label: self._checkpoint(label))
        self.canvas.itemsTransformed.connect(self._on_items_transformed)
        self.layers.aboutToChange.connect(lambda label, key: self._checkpoint(label, key or None))
        self.objects.aboutToChange.connect(lambda label, key: self._checkpoint(label, key or None))
        self.machine.resumeRequested.connect(self.resume_job)
        self.canvas.shapeDrawn.connect(self._on_shape_drawn)
        self.canvas.nodesEdited.connect(self._on_nodes_edited)
        self.canvas.toolChanged.connect(self._on_tool_changed)
        self._grabber = None
        self._camera_img = None
        self.layers.layersChanged.connect(self._on_layers_changed)
        self.objects.changed.connect(self._on_object_changed)
        self.objects.action.connect(self._on_object_action)
        self.machine.startRequested.connect(self.start_job)
        self.machine.frameRequested.connect(self.run_frame)
        self.machine.firmwareDetected.connect(self._on_firmware)
        self.machine.profileChanged.connect(lambda: storage.save_machine(self.profile))
        self.layers.set_machine(self.profile.name)
        self._update_title()
        self.canvas.zoom_fit()

    # ------------------------------------------------------------ Menüs
    def _act(self, text, slot, shortcut=None, tip=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        return a

    def _build_actions(self):
        mb = self.menuBar()
        m = mb.addMenu("&Datei")
        self.a_new = self._act("Neu", self.new_project, "Ctrl+N")
        self.a_open = self._act("Projekt öffnen…", self.open_project, "Ctrl+O")
        self.a_save = self._act("Projekt speichern", self.save_project, "Ctrl+S")
        self.a_save_as = self._act("Projekt speichern unter…", lambda: self.save_project(True), "Ctrl+Shift+S")
        self.a_import = self._act("Importieren…", self.import_dialog, "Ctrl+I", "SVG, DXF oder Bild importieren")
        self.a_text = self._act("Text einfügen…", self.insert_text, "Ctrl+T")
        self.a_export = self._act("G-Code exportieren…", self.export_gcode, "Ctrl+E")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_import)
        m.addAction(self.a_text)
        m.addAction(self.a_export)
        m.addAction(self._act("Als SVG exportieren…", lambda: self.export_vector("svg")))
        m.addAction(self._act("Als DXF exportieren…", lambda: self.export_vector("dxf")))
        m.addSeparator()
        m.addAction(self._act("Beenden", self.close, "Ctrl+Q"))

        e = mb.addMenu("&Bearbeiten")
        self.a_undo = self._act("Rückgängig", self.do_undo, "Ctrl+Z")
        self.a_redo = self._act("Wiederholen", self.do_redo, "Ctrl+Y")
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.a_cut = self._act("Ausschneiden", self.cut_selected, "Ctrl+X")
        self.a_copy = self._act("Kopieren", self.copy_selected, "Ctrl+C")
        self.a_paste = self._act("Einfügen", lambda: self.paste(False), "Ctrl+V")
        self.a_paste_inplace = self._act("An Originalposition einfügen", lambda: self.paste(True), "Ctrl+Shift+V")
        for a in (self.a_undo, self.a_redo):
            e.addAction(a)
        e.addSeparator()
        for a in (self.a_cut, self.a_copy, self.a_paste, self.a_paste_inplace):
            e.addAction(a)
        e.addSeparator()
        self.a_dup = self._act("Duplizieren", lambda: self._on_object_action("duplicate"), "Ctrl+D")
        self.a_del = self._act("Löschen", self.delete_selected, None, "Ausgewählte Objekte löschen (Entf)")
        self.a_all = self._act("Alles auswählen", lambda: self.canvas.select_only([i.id for i in self.project.items]),
                               "Ctrl+A")
        self.a_group = self._act("Gruppieren", self.group_selected, "Ctrl+G",
                                 "Ausgewählte Vektorobjekte zu einem Objekt zusammenfassen")
        self.a_ungroup = self._act("Gruppierung aufheben", self.ungroup_selected, "Ctrl+Shift+G",
                                   "In Einzelteile zerlegen (Außenkontur bleibt mit ihren Löchern zusammen)")
        self.a_center = self._act("Zentrieren", lambda: self._on_object_action("center"), None,
                                  "Auswahl in die Mitte des Arbeitsbereichs setzen")
        self.a_origin = self._act("An 0/0", lambda: self._on_object_action("origin"), None,
                                  "Auswahl an den Nullpunkt (links unten) schieben")
        for a in (self.a_dup, self.a_del, self.a_all):
            e.addAction(a)
        e.addSeparator()
        for a in (self.a_group, self.a_ungroup):
            e.addAction(a)
        self.layer_menu = e.addMenu("Ebene zuweisen")
        self.layer_menu.aboutToShow.connect(lambda: self._fill_layer_menu(self.layer_menu))
        e.addSeparator()
        for a in (self.a_center, self.a_origin):
            e.addAction(a)

        ar = mb.addMenu("&Anordnen")
        self.align_actions = []
        for key, text, sc in (("left", "Links ausrichten", "Ctrl+Shift+Left"),
                              ("hcenter", "Horizontal mittig ausrichten", None),
                              ("right", "Rechts ausrichten", "Ctrl+Shift+Right"),
                              ("top", "Oben ausrichten", "Ctrl+Shift+Up"),
                              ("vcenter", "Vertikal mittig ausrichten", None),
                              ("bottom", "Unten ausrichten", "Ctrl+Shift+Down")):
            a = self._act(text, lambda _=False, k=key: self.align_selected(k), sc)
            ar.addAction(a)
            self.align_actions.append(a)
        ar.addSeparator()
        ar.addAction(self._act("Horizontal verteilen (gleiche Abstände)", lambda: self.distribute_selected("x")))
        ar.addAction(self._act("Vertikal verteilen (gleiche Abstände)", lambda: self.distribute_selected("y")))
        ar.addSeparator()
        ar.addAction(self._act("Horizontal spiegeln", lambda: self.mirror_selected("h")))
        ar.addAction(self._act("Vertikal spiegeln", lambda: self.mirror_selected("v")))
        ar.addAction(self._act("90° nach links drehen", lambda: self.rotate_selected(90), "Ctrl+L"))
        ar.addAction(self._act("90° nach rechts drehen", lambda: self.rotate_selected(-90), "Ctrl+R"))
        ar.addSeparator()
        self.a_array = self._act("Array erstellen…", self.array_dialog, "Ctrl+Shift+A",
                                 "Auswahl als Raster oder im Kreis vervielfältigen")
        ar.addAction(self.a_array)
        ar.addAction(self.a_center)
        ar.addAction(self.a_origin)

        fm = mb.addMenu("&Formen")
        self.a_union = self._act("Vereinigen", lambda: self.boolean_op("union"), "Ctrl+Shift+U",
                                 "Ausgewählte Formen zu einer Fläche verschmelzen")
        self.a_diff = self._act("Abziehen (größte Form minus andere)", lambda: self.boolean_op("difference"),
                                "Ctrl+Shift+D")
        self.a_inter = self._act("Schnittmenge", lambda: self.boolean_op("intersection"), "Ctrl+Shift+I")
        self.a_xor = self._act("Ausschluss (XOR)", lambda: self.boolean_op("xor"))
        self.a_offset = self._act("Offset…", self.offset_dialog, "Alt+O", "Konturen nach außen oder innen versetzen")
        self.a_join = self._act("Pfade verbinden / schließen…", self.join_selected, "Alt+J",
                                "Offene Pfade mit gemeinsamen Endpunkten verbinden und schließen")
        for a in (self.a_union, self.a_diff, self.a_inter, self.a_xor):
            fm.addAction(a)
        fm.addSeparator()
        fm.addAction(self.a_offset)
        fm.addAction(self.a_join)

        wz = mb.addMenu("&Werkzeuge")
        self.a_trace = self._act("Bild nachzeichnen…", self.trace_selected, "Alt+T",
                                 "Ausgewähltes Bild in Vektorkonturen umwandeln")
        self.a_mattest = self._act("Materialtest…", self.material_test, None,
                                   "Testraster mit verschiedenen Leistungen und Geschwindigkeiten")
        wz.addAction(self.a_trace)
        self.a_textpath = self._act("Text auf Pfad…", self.text_on_path_dialog, None,
                                    "Text entlang der ausgewählten Kontur setzen")
        wz.addAction(self.a_textpath)
        wz.addAction(self.a_mattest)

        v = mb.addMenu("&Ansicht")
        self.a_fit = self._act("Alles zeigen", self.canvas.zoom_fit, "Ctrl+0")
        self.a_preview = self._act("Werkzeugweg-Vorschau", self.toggle_preview, "Ctrl+P",
                                   "Erzeugt den G-Code und zeigt Brenn- und Leerwege")
        self.a_preview.setCheckable(True)
        self.a_show_gcode = self._act("G-Code anzeigen…", self.show_gcode)
        for a in (self.a_fit, self.a_preview, self.a_show_gcode):
            v.addAction(a)
        v.addSeparator()
        self.a_snap = self._act("Am Raster fangen", self._toggle_snap, "Ctrl+Shift+F",
                                "Linke untere Objektecke beim Ziehen auf das Raster legen")
        self.a_snap.setCheckable(True)
        v.addAction(self.a_snap)
        sm = v.addMenu("Rastermaß")
        for st in (0.5, 1.0, 5.0, 10.0):
            a = sm.addAction(f"{st:g} mm")
            a.setCheckable(True)
            a.setChecked(st == 1.0)
            a.triggered.connect(lambda _=False, x=st, m=sm: self._set_snap_step(x, m))

        mm = mb.addMenu("&Maschine")
        mm.addAction(self._act("Maschineneinstellungen…", self.machine_settings))
        self.a_rotary = self._act("Rotationsachse aktiv", self._toggle_rotary)
        self.a_rotary.setCheckable(True)
        self.a_rotary.setChecked(self.profile.rotary)
        mm.addAction(self.a_rotary)
        mm.addSeparator()
        cm = mm.addMenu("Kamera")
        cm.addAction(self._act("Kamera wählen…", self.camera_select))
        cm.addAction(self._act("Passmarken lasern", self.camera_markers, None,
                               "4 Kreuze mit geringer Leistung an bekannten Positionen lasern"))
        cm.addAction(self._act("Kamera ausrichten…", self.camera_align, None,
                               "Bild aufnehmen und die 4 Passmarken anklicken"))
        self.a_cam_update = self._act("Kamerabild aktualisieren", self.camera_update, "F5")
        cm.addAction(self.a_cam_update)
        self.a_cam_show = self._act("Kamerabild anzeigen", self._camera_toggle)
        self.a_cam_show.setCheckable(True)
        cm.addAction(self.a_cam_show)

        h = mb.addMenu("&Hilfe")
        h.addAction(self._act("Über TiLaser", self.about))

        tb = QToolBar("Werkzeuge")
        tb.setMovable(False)
        self.addToolBar(tb)
        for a in (self.a_new, self.a_open, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_import, self.a_text):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_fit)
        tb.addAction(self.a_preview)
        self.chk_travel = QCheckBox("Leerwege")
        self.chk_travel.setChecked(True)
        self.chk_travel.toggled.connect(lambda _: self._refresh_preview())
        tb.addWidget(self.chk_travel)
        self.btn_play = QToolButton()
        self.btn_play.setText("▶")
        self.btn_play.setToolTip("Simulation abspielen")
        self.btn_play.clicked.connect(self._sim_play)
        self.sim_slider = QSlider(Qt.Orientation.Horizontal)
        self.sim_slider.setRange(0, 1000)
        self.sim_slider.setValue(1000)
        self.sim_slider.setFixedWidth(160)
        self.sim_slider.setToolTip("Simulation: Zeitpunkt im Auftrag")
        self.sim_slider.valueChanged.connect(self._sim_slider)
        self.sim_speed = QComboBox()
        for f in (1, 5, 20, 100, 500):
            self.sim_speed.addItem(f"{f}×", f)
        self.sim_speed.setCurrentIndex(2)
        self.lbl_sim = QLabel("")
        for w in (self.btn_play, self.sim_slider, self.sim_speed, self.lbl_sim):
            tb.addWidget(w)
        self.sim_timer = QTimer(self)
        self.sim_timer.setInterval(40)
        self.sim_timer.timeout.connect(self._sim_tick)
        self._sim_t = None
        self._set_sim_enabled(False)
        tb.addSeparator()
        tb.addAction(self.a_export)
        tb.addSeparator()
        for a in (self.a_group, self.a_ungroup, self.a_center, self.a_origin, self.a_dup, self.a_del):
            tb.addAction(a)

        tt = QToolBar("Zeichnen")
        tt.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, tt)
        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for key, text, sc, tip in (("select", "⬚", "V", "Auswählen und verschieben (V)"),
                                   ("rect", "▭", "R", "Rechteck zeichnen (R) – Shift: Quadrat, Strg: von der Mitte"),
                                   ("ellipse", "◯", "E", "Ellipse zeichnen (E) – Shift: Kreis, Strg: von der Mitte"),
                                   ("polygon", "⬡", "P", "Polygon/Stern zeichnen (P)"),
                                   ("line", "╱", "L", "Linie/Polylinie (L): klicken, Doppelklick oder Enter beendet, "
                                                      "Klick auf Startpunkt schließt"),
                                   ("node", "◆", "N", "Knoten bearbeiten (N): ziehen = verschieben, Doppelklick auf "
                                                      "Linie = einfügen, Entf = löschen")):
            a = QAction(text, self)
            a.setToolTip(tip)
            a.setShortcut(QKeySequence(sc))
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.canvas.set_tool(k))
            self.tool_group.addAction(a)
            tt.addAction(a)
            self.tool_actions[key] = a
        self.tool_actions["select"].setChecked(True)
        tt.addSeparator()
        self.poly_sides = QSpinBox()
        self.poly_sides.setRange(3, 60)
        self.poly_sides.setValue(6)
        self.poly_sides.setToolTip("Ecken für Polygon/Stern")
        self.poly_star = QCheckBox("★")
        self.poly_star.setToolTip("Stern statt Polygon")
        self.rect_radius = QDoubleSpinBox()
        self.rect_radius.setRange(0, 500)
        self.rect_radius.setDecimals(1)
        self.rect_radius.setSuffix(" r")
        self.rect_radius.setToolTip("Eckenradius für Rechtecke (mm)")
        for w in (self.poly_sides, self.poly_star, self.rect_radius):
            tt.addWidget(w)
        self.poly_sides.valueChanged.connect(lambda v: setattr(self.canvas, "poly_sides", v))
        self.poly_star.toggled.connect(lambda on: setattr(self.canvas, "poly_star", 0.5 if on else 0.0))
        self.rect_radius.valueChanged.connect(lambda v: setattr(self.canvas, "rect_radius", v))

        self.addToolBarBreak()
        ob = QToolBar("Objekt")
        ob.setMovable(False)
        ob.addWidget(self.objects)
        self.addToolBar(ob)

    # ------------------------------------------------------------ Projekt
    def _update_title(self):
        name = os.path.basename(self.project_path) if self.project_path else "Unbenannt"
        self.setWindowTitle(f"TiLaser – {name} – {self.profile.name} ({self.profile.dialect})")

    def _set_project(self, project: Project):
        self.project = project
        self.canvas.set_project(project)
        self.layers.set_project(project)
        self.objects.set_project(project)
        self._invalidate()
        self.canvas.zoom_fit()

    def new_project(self):
        if self.project.items and QMessageBox.question(self, "Neu", "Aktuelles Projekt verwerfen?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.project_path = None
        self._set_project(Project())
        self._update_title()

    def open_project(self):
        p, _ = QFileDialog.getOpenFileName(self, "Projekt öffnen", self.last_dir, "TiLaser-Projekt (*.tlp)")
        if not p:
            return
        try:
            self._set_project(storage.load_project(p))
        except Exception as e:
            QMessageBox.critical(self, "Öffnen", f"Projekt konnte nicht geladen werden:\n{e}")
            return
        self.project_path = p
        self.last_dir = os.path.dirname(p)
        self._update_title()

    def save_project(self, save_as=False):
        p = self.project_path
        if save_as or not p:
            p, _ = QFileDialog.getSaveFileName(self, "Projekt speichern", os.path.join(self.last_dir, "projekt.tlp"),
                                               "TiLaser-Projekt (*.tlp)")
            if not p:
                return
            if not p.lower().endswith(".tlp"):
                p += ".tlp"
        try:
            storage.save_project(self.project, p)
        except Exception as e:
            QMessageBox.critical(self, "Speichern", str(e))
            return
        self.project_path = p
        self._update_title()
        self.statusBar().showMessage(f"Gespeichert: {p}", 4000)

    # ------------------------------------------------------------ Import
    def import_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Importieren", self.last_dir,
            "Alle unterstützten (*.svg *.dxf *.pdf *.ai *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;"
            "SVG (*.svg);;DXF (*.dxf);;PDF / Illustrator (*.pdf *.ai);;"
            "Bilder (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)")
        if files:
            self.import_files(files)

    def import_files(self, files):
        new_ids, msgs = [], []
        if any(not f.lower().endswith(".tlp") for f in files):
            self._checkpoint("Importieren")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for f in files:
                self.last_dir = os.path.dirname(f)
                ext = os.path.splitext(f)[1].lower()
                name = os.path.basename(f)
                try:
                    if ext == ".tlp":
                        self.project_path = f
                        self._set_project(storage.load_project(f))
                        self._update_title()
                        continue
                    if ext in (".svg", ".dxf", ".pdf", ".ai"):
                        reader = {".svg": import_svg, ".dxf": import_dxf}.get(ext, import_pdf)
                        paths, warn = reader(f)
                        msgs += [f"{name}: {w}" for w in warn]
                        if not paths:
                            msgs.append(f"{name}: keine verwertbaren Konturen gefunden.")
                            continue
                        it = self.project.add_vector_import(name, paths)
                        self._place_on_bed(it)
                    elif ext in IMAGE_EXT:
                        gray, w, h = load_image_gray(f)
                        if max(gray.shape) > MAX_IMAGE_PX:
                            from PIL import Image
                            k = MAX_IMAGE_PX / max(gray.shape)
                            gray = np.asarray(Image.fromarray(gray).resize(
                                (int(gray.shape[1] * k), int(gray.shape[0] * k)), Image.Resampling.LANCZOS))
                        it = self.project.add_raster(name, gray, w, h,
                                                     center_at=(self.profile.bed_w / 2, self.profile.bed_h / 2))
                        self._place_on_bed(it)
                    else:
                        msgs.append(f"{name}: Dateityp nicht unterstützt.")
                        continue
                    self.canvas.add_item(it)
                    new_ids.append(it.id)
                except Exception as e:
                    msgs.append(f"{name}: Fehler beim Import – {e}")
        finally:
            QApplication.restoreOverrideCursor()
        self.layers.refresh()
        self.canvas.refresh_styles()
        self.canvas.select_only(new_ids)
        self._invalidate()
        if new_ids:
            self.canvas.zoom_fit()
        if msgs:
            QMessageBox.information(self, "Import", "\n".join(msgs))

    def _place_on_bed(self, it: Item):
        """Liegt das Objekt außerhalb des Arbeitsbereichs, wird es in die Mitte gesetzt."""
        bb = it.world_bbox()
        if bb[0] < 0 or bb[1] < 0 or bb[2] > self.profile.bed_w or bb[3] > self.profile.bed_h:
            it.x, it.y = self.profile.bed_w / 2, self.profile.bed_h / 2

    def insert_text(self):
        dlg = TextDialog(self.project, self)
        if dlg.exec() != TextDialog.DialogCode.Accepted:
            return
        text = dlg.text.toPlainText()
        font = dlg.font_path()
        if not text.strip() or not font:
            return
        try:
            polys = text_to_polylines(text, font, dlg.size.value(), dlg.spacing.value(), dlg.letter.value(),
                                      dlg.align.currentData())
        except Exception as e:
            QMessageBox.critical(self, "Text", f"Text konnte nicht erzeugt werden:\n{e}")
            return
        if not polys:
            QMessageBox.information(self, "Text", "Die Schrift enthält keine Konturen für diesen Text.")
            return
        self._checkpoint("Text einfügen")
        target = dlg.layer.currentData()
        if target == TextDialog.NEW_FILL:
            layer = self.project.add_layer("Text Gravur", mode="fill")
        elif target == TextDialog.NEW_CUT:
            layer = self.project.add_layer("Text Kontur", mode="cut")
        else:
            layer = self.project.layer(target)
        bb = g.bbox(polys)
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        it = Item(id=new_id("I"), name=f"Text: {text.splitlines()[0][:20]}", kind="vector",
                  x=self.profile.bed_w / 2, y=self.profile.bed_h / 2)
        it.paths = [VectorPath(p.transformed(g.translate(-cx, -cy)), layer.id) for p in polys]
        self.project.items.append(it)
        self.canvas.add_item(it)
        self.layers.refresh(layer.id)
        self.canvas.select_only([it.id])
        self._invalidate()

    # ------------------------------------------------------------ Auswahl / Änderungen
    def _selected_items(self) -> list[Item]:
        return [self.project.item(i) for i in self.canvas.selected_ids() if self.project.item(i)]

    def _on_selection(self, ids):
        items = [self.project.item(i) for i in ids]
        self.objects.set_item(items[0] if len(items) == 1 else None)
        if len(items) > 1:
            self.objects.title.setText(f"{len(items)} Objekte ausgewählt")
        if len(items) == 1 and items[0].layer_ids():
            ids_ = items[0].layer_ids()
            if len(ids_) == 1:
                self.layers.select_layer(next(iter(ids_)))

    def _on_item_moved(self, item_id):
        if self.objects.item and self.objects.item.id == item_id:
            self.objects.refresh()
        self._invalidate()

    def _on_object_changed(self, item_id, geom):
        if geom:
            self.canvas.refresh_styles()
            self.layers.refresh()
        self.canvas.refresh_item(item_id)
        self._invalidate()

    def _on_layers_changed(self, visual):
        self.canvas.refresh_styles()
        if self.objects.item:
            self.objects.refresh()
        self._invalidate()

    def _on_object_action(self, key):
        items = self._selected_items()
        if not items:
            return
        if key == "delete":
            self.delete_selected()
            return
        self._checkpoint({"duplicate": "Duplizieren", "center": "Zentrieren", "origin": "An 0/0"}.get(key, key))
        if key == "duplicate":
            new_ids = []
            for it in items:
                c = copy.deepcopy(it)
                c.id = new_id("I")
                c.x += 10
                c.y -= 10
                self.project.items.append(c)
                self.canvas.add_item(c)
                new_ids.append(c.id)
            self.canvas.select_only(new_ids)
        for it in items if key in ("center", "origin") else []:
            bb = it.world_bbox()
            if key == "center":
                it.x += self.profile.bed_w / 2 - (bb[0] + bb[2]) / 2
                it.y += self.profile.bed_h / 2 - (bb[1] + bb[3]) / 2
            else:
                it.x -= bb[0]
                it.y -= bb[1]
            self.canvas.refresh_item(it.id)
        self.objects.refresh()
        self._invalidate()

    # ------------------------------------------------------------ Gruppieren / Ebenen zuweisen
    def group_selected(self):
        items = self._selected_items()
        if sum(1 for it in items if it.kind == "vector") < 2:
            self.statusBar().showMessage("Zum Gruppieren mindestens zwei Vektorobjekte auswählen.", 4000)
            return
        self._checkpoint("Gruppieren")
        for it in items:
            if it.kind == "vector":
                self.canvas.remove_item(it.id)
        g_item = self.project.group(items)
        self.canvas.add_item(g_item)
        self.canvas.select_only([g_item.id] + [it.id for it in items if it.kind == "raster"])
        self._invalidate()

    def ungroup_selected(self):
        items = [it for it in self._selected_items() if it.kind == "vector"]
        if not items:
            return
        self._checkpoint("Gruppierung aufheben")
        new_ids = []
        for it in items:
            n = len(g.group_parts([vp.poly for vp in it.paths]))
            if n > 300 and QMessageBox.question(
                    self, "Gruppierung aufheben", f"„{it.name}“ zerfällt in {n} Teile. Fortfahren?") \
                    != QMessageBox.StandardButton.Yes:
                new_ids.append(it.id)
                continue
            parts = self.project.ungroup(it)
            if len(parts) == 1 and parts[0] is it:
                new_ids.append(it.id)
                continue
            self.canvas.remove_item(it.id)
            for p in parts:
                self.canvas.add_item(p)
                new_ids.append(p.id)
        self.canvas.select_only(new_ids)
        self.statusBar().showMessage(f"{len(new_ids)} Objekte", 3000)
        self._invalidate()

    def _fill_layer_menu(self, menu: QMenu):
        menu.clear()
        items = self._selected_items()
        if not items:
            a = menu.addAction("(keine Auswahl)")
            a.setEnabled(False)
            return
        only_raster = all(it.kind == "raster" for it in items)
        current = set().union(*[it.layer_ids() for it in items])
        for l in self.project.layers:
            if only_raster and l.mode != "raster":
                continue
            a = menu.addAction(color_icon(l.color), f"{l.name}   ({MODE_LABELS[l.mode]})")
            a.setCheckable(True)
            a.setChecked(current == {l.id})
            a.triggered.connect(lambda _=False, lid=l.id: self.assign_layer(lid))
        menu.addSeparator()
        if only_raster:
            modes = (("raster", "Neue Ebene: Rasterbild…"),)
        else:
            modes = (("cut", "Neue Ebene: Schneiden / Linie…"), ("fill", "Neue Ebene: Füllen…"))
        for mode, text in modes:
            a = menu.addAction(text)
            a.triggered.connect(lambda _=False, m=mode: self.assign_new_layer(m))

    def assign_layer(self, layer_id: str):
        items = self._selected_items()
        if not items:
            return
        self._checkpoint("Ebene zuweisen")
        layer = self.project.layer(layer_id)
        skipped = [it for it in items if it.kind == "raster" and layer.mode != "raster"]
        self.project.assign_layer(items, layer_id)
        self.canvas.refresh_styles()
        self.layers.refresh(layer_id)
        self.objects.refresh()
        self._invalidate()
        if skipped:
            self.statusBar().showMessage(f"{len(skipped)} Bild(er) übersprungen – Bilder nur Raster-Ebenen zuweisen.",
                                         5000)

    def assign_new_layer(self, mode: str):
        if not self._selected_items():
            return
        c = QColorDialog.getColor(QColor("#e53935"), self, "Farbe der neuen Ebene")
        if not c.isValid():
            return
        name, ok = QInputDialog.getText(self, "Neue Ebene", "Name:", text=f"{MODE_LABELS[mode].split()[0]} {c.name()}")
        if not ok:
            return
        layer = self.project.add_layer(name.strip() or c.name(), c.name(), mode)
        self.assign_layer(layer.id)

    def _context_menu(self, global_pos):
        items = self._selected_items()
        menu = QMenu(self)
        if items:
            menu.addAction(self.a_group)
            menu.addAction(self.a_ungroup)
            menu.addSeparator()
            menu.addAction(self.a_cut)
            menu.addAction(self.a_copy)
            menu.addAction(self.a_paste)
            lm = menu.addMenu("Ebene zuweisen")
            self._fill_layer_menu(lm)
            menu.addSeparator()
            menu.addAction(self.a_center)
            menu.addAction(self.a_origin)
            menu.addAction(self.a_dup)
            menu.addAction(self.a_array)
            if len(items) == 1 and items[0].kind == "vector":
                menu.addAction(self.a_textpath)
            if any(it.kind == "raster" for it in items):
                menu.addAction(self.a_trace)
            menu.addSeparator()
            menu.addAction(self.a_del)
        else:
            menu.addAction(self.a_paste)
            menu.addAction(self.a_import)
            menu.addAction(self.a_text)
            menu.addAction(self.a_all)
            menu.addAction(self.a_fit)
        menu.exec(global_pos)

    def delete_selected(self):
        ids = self.canvas.selected_ids()
        if ids:
            self._checkpoint("Löschen")
        for i in ids:
            self.project.remove_item(i)
            self.canvas.remove_item(i)
        self.objects.set_item(None)
        self._invalidate()

    # ------------------------------------------------------------ G-Code
    def _invalidate(self):
        self.result = None
        bb = self.project.bbox()
        self._info = f"Auftrag: {bb[2] - bb[0]:.1f} × {bb[3] - bb[1]:.1f} mm" if bb else ""
        self.lbl_info.setText(self._info)
        if self.a_preview.isChecked():
            self._refresh_preview()
        else:
            self.canvas.clear_simulation()
            self.canvas.clear_toolpath()

    def ensure_result(self, only_ids=None, offset=(0.0, 0.0)):
        key = (tuple(only_ids) if only_ids else None, round(offset[0], 4), round(offset[1], 4))
        if self.result is None or self._result_key != key:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.result = generate(self.project, self.profile, only_ids, offset)
                self._result_key = key
            finally:
                QApplication.restoreOverrideCursor()
            self.lbl_info.setText(getattr(self, "_info", "") +
                                  f"  |  geschätzt {fmt_time(self.result.time_s)}  |  {len(self.result.lines)} Zeilen")
        return self.result

    def _job_params(self, need_position=True):
        """(only_ids, offset) laut Auftragsleiste. Löst ValueError mit Meldung aus."""
        only = None
        if self.machine.only_sel.isChecked():
            only = self.canvas.selected_ids()
            if not only:
                raise ValueError("„Nur Auswahl“ ist aktiv, aber nichts ausgewählt.")
        offset = (0.0, 0.0)
        if self.machine.origin.currentData() == "current" and need_position:
            pos = self.machine.last_pos
            if pos is None:
                raise ValueError("Ursprung „Aktuelle Position“: Die Kopfposition ist unbekannt – Laser verbinden.")
            bb = job_project(self.project, only).bbox()
            if bb is None:
                raise ValueError("Keine aktiven Objekte.")
            offset = origin_offset(bb, self.machine.anchor.currentData(), pos[:2])
        return only, offset

    def toggle_preview(self):
        self._refresh_preview()

    def _refresh_preview(self):
        if not self.a_preview.isChecked():
            self.sim_timer.stop()
            self.canvas.clear_simulation()
            self.canvas.clear_toolpath()
            self._set_sim_enabled(False)
            return
        try:
            only = self.canvas.selected_ids() if self.machine.only_sel.isChecked() else None
            res = self.ensure_result(only or None)
        except Exception as e:
            QMessageBox.critical(self, "Vorschau", f"Fehler bei der G-Code-Erzeugung:\n{e}")
            self.a_preview.setChecked(False)
            self._set_sim_enabled(False)
            return
        self.sim_timer.stop()
        self._sim_t = None
        self.canvas.clear_simulation()
        self.canvas.show_toolpath(res, self.chk_travel.isChecked())
        self._set_sim_enabled(True)
        self.sim_slider.blockSignals(True)
        self.sim_slider.setValue(1000)
        self.sim_slider.blockSignals(False)
        self.lbl_sim.setText(fmt_time(res.time_s))

    def show_gcode(self):
        res = self.ensure_result()
        GcodeDialog(res.text, self).exec()

    def export_gcode(self):
        if not self.project.items:
            QMessageBox.information(self, "Export", "Das Projekt ist leer.")
            return
        try:
            only, offset = self._job_params(need_position=self.machine.conn.connected)
        except ValueError as e:
            QMessageBox.information(self, "Export", str(e))
            return
        res = self.ensure_result(only, offset)
        base = os.path.splitext(os.path.basename(self.project_path))[0] if self.project_path else "auftrag"
        p, _ = QFileDialog.getSaveFileName(self, "G-Code exportieren", os.path.join(self.last_dir, base + ".gcode"),
                                           "G-Code (*.gcode *.nc *.gc *.ngc)")
        if not p:
            return
        with open(p, "w", encoding="ascii", errors="replace", newline="\n") as f:
            f.write(res.text)
        msg = f"Exportiert: {os.path.basename(p)}\n{len(res.lines)} Zeilen, geschätzte Dauer {fmt_time(res.time_s)}"
        if res.warnings:
            msg += "\n\nHinweise:\n• " + "\n• ".join(res.warnings)
        QMessageBox.information(self, "Export", msg)

    # ------------------------------------------------------------ Maschine
    def start_job(self):
        if not self.project.items:
            QMessageBox.information(self, "Start", "Das Projekt ist leer.")
            return
        if not self.machine.is_ready():
            QMessageBox.information(self, "Start", "Laser nicht verbunden oder beschäftigt.")
            return
        try:
            only, offset = self._job_params()
        except ValueError as e:
            QMessageBox.information(self, "Start", str(e))
            return
        res = self.ensure_result(only, offset)
        text = (f"Auftrag starten?\n\nGeschätzte Dauer: {fmt_time(res.time_s)}\n"
                f"Zeilen: {len(res.lines)}\nDialekt: {DIALECTS.get(self.profile.dialect)}\n\n"
                "⚠ Schutzbrille tragen, Absaugung an, Laser nie unbeaufsichtigt lassen!")
        if res.warnings:
            text += "\n\nHinweise:\n• " + "\n• ".join(res.warnings)
        if QMessageBox.question(self, "Auftrag starten", text) != QMessageBox.StandardButton.Yes:
            return
        self.machine.run_job(res.lines)

    def run_frame(self):
        try:
            only, offset = self._job_params()
        except ValueError as e:
            QMessageBox.information(self, "Rahmen", str(e))
            return
        bb = job_project(self.project, only, offset).bbox()
        if not bb:
            QMessageBox.information(self, "Rahmen", "Keine aktiven Objekte.")
            return
        if self.profile.frame_power > 0 and QMessageBox.question(
                self, "Rahmen", f"Rahmen mit {self.profile.frame_power:g} % Laserleistung fahren?\n"
                                "Schutzbrille tragen!") != QMessageBox.StandardButton.Yes:
            return
        self.machine.run_job(frame_gcode(bb, self.profile))

    def _on_firmware(self, fw):
        if fw == self.profile.dialect:
            return
        names = {"grbl": "GRBL / FluidNC", "marlin": "Marlin", "smoothie": "Smoothieware"}
        if QMessageBox.question(self, "Firmware erkannt",
                                f"Erkannte Firmware: {names.get(fw, fw)}\n"
                                f"Maschinenprofil ist auf „{self.profile.dialect}“ eingestellt.\n\n"
                                "Profil auf die erkannte Firmware umstellen (empfohlen)?") \
                == QMessageBox.StandardButton.Yes:
            self.profile.apply_dialect(fw)
            storage.save_machine(self.profile)
            self._update_title()
            self._invalidate()

    def machine_settings(self):
        dlg = MachineDialog(self.profile, self)
        if dlg.exec() == MachineDialog.DialogCode.Accepted:
            dlg.apply()
            storage.save_machine(self.profile)
            self.layers.set_machine(self.profile.name)
            self.machine.baud.setCurrentText(str(self.profile.baud))
            self.canvas.set_bed(self.profile.bed_w, self.profile.bed_h)
            self._update_title()
            self._invalidate()

    def about(self):
        QMessageBox.about(self, "Über TiLaser",
                          f"<b>TiLaser {__version__}</b><br>Schneiden und Gravieren mit G-Code-Lasern "
                          "(GRBL, Marlin, Smoothieware).<br><br>Import: SVG, DXF, Bilder, Text<br>"
                          "Bedienung: Mausrad = Zoom, rechte/mittlere Maustaste = Verschieben, "
                          "Pfeiltasten = Objekt um 1 mm (Shift: 10 mm) bewegen.")

    # ------------------------------------------------------------ Rückgängig / Zwischenablage
    def _checkpoint(self, label: str, key: str | None = None):
        self.undo.push(self.project, label, key)
        self._update_undo_actions()

    def _update_undo_actions(self):
        self.a_undo.setEnabled(self.undo.can_undo())
        self.a_redo.setEnabled(self.undo.can_redo())
        self.a_undo.setText(f"Rückgängig: {self.undo.undo_label()}" if self.undo.can_undo() else "Rückgängig")
        self.a_redo.setText(f"Wiederholen: {self.undo.redo_label()}" if self.undo.can_redo() else "Wiederholen")

    def _restore(self, project: Project):
        sel = self.canvas.selected_ids()
        self.project = project
        self.canvas.set_project(project)
        self.layers.set_project(project)
        self.objects.set_project(project)
        self.canvas.select_only([i for i in sel if project.item(i)])
        self._update_undo_actions()
        self._invalidate()

    def do_undo(self):
        p = self.undo.do_undo(self.project)
        if p is not None:
            self._restore(p)

    def do_redo(self):
        p = self.undo.do_redo(self.project)
        if p is not None:
            self._restore(p)

    def copy_selected(self):
        self._clipboard = [ed.clone_item(it) for it in self._selected_items()]
        self.statusBar().showMessage(f"{len(self._clipboard)} Objekt(e) kopiert", 2000)

    def cut_selected(self):
        self.copy_selected()
        self.delete_selected()

    def paste(self, in_place=False):
        if not self._clipboard:
            return
        self._checkpoint("Einfügen")
        new_ids = []
        for it in self._clipboard:
            c = ed.clone_item(it)
            if not in_place:
                c.x += 10
                c.y -= 10
            for lid in c.layer_ids():                  # Ebene existiert nicht mehr -> erste passende
                if self.project.layer(lid) is None:
                    fallback = next((l for l in self.project.layers if (l.mode == "raster") == (c.kind == "raster")),
                                    None) or self.project.add_layer("Eingefügt", mode="raster" if c.kind == "raster"
                                                                    else "cut")
                    self.project.assign_layer([c], fallback.id)
            self.project.items.append(c)
            self.canvas.add_item(c)
            new_ids.append(c.id)
        if not in_place:                               # nächstes Einfügen wieder versetzt
            self._clipboard = [ed.clone_item(self.project.item(i)) for i in new_ids]
        self.layers.refresh()
        self.canvas.select_only(new_ids)
        self._invalidate()

    # ------------------------------------------------------------ Anordnen
    def _refresh_items(self, items):
        for it in items:
            self.canvas.refresh_item(it.id)
        self.objects.refresh()
        self._invalidate()

    def align_selected(self, mode):
        items = self._selected_items()
        if len(items) < 2:
            self.statusBar().showMessage("Zum Ausrichten mindestens zwei Objekte auswählen.", 3000)
            return
        self._checkpoint("Ausrichten")
        ed.align(items, mode)
        self._refresh_items(items)

    def distribute_selected(self, axis):
        items = self._selected_items()
        if len(items) < 3:
            self.statusBar().showMessage("Zum Verteilen mindestens drei Objekte auswählen.", 3000)
            return
        self._checkpoint("Verteilen")
        ed.distribute(items, axis)
        self._refresh_items(items)

    def mirror_selected(self, direction):
        items = self._selected_items()
        if not items:
            return
        self._checkpoint("Spiegeln")
        bb = ed.selection_bbox(items)
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        for it in items:
            it.mirror_x = not it.mirror_x
            it.rotation = -it.rotation
            if direction == "h":
                it.x = 2 * cx - it.x
            else:                                       # vertikal = horizontal + 180°
                it.rotation += 180
                it.y = 2 * cy - it.y
        self._refresh_items(items)

    def rotate_selected(self, angle):
        items = self._selected_items()
        if not items:
            return
        self._checkpoint("Drehen")
        bb = ed.selection_bbox(items)
        ed.transform_items(items, [(it.x, it.y, it.scale, it.rotation) for it in items], 1.0, angle,
                           ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2))
        self._refresh_items(items)

    def array_dialog(self):
        items = self._selected_items()
        if not items:
            self.statusBar().showMessage("Zuerst Objekte auswählen.", 3000)
            return
        dlg = ArrayDialog(ed.selection_bbox(items), self)
        if dlg.exec() != ArrayDialog.DialogCode.Accepted:
            return
        self._checkpoint("Array")
        if dlg.kind.currentData() == "grid":
            new = ed.grid_array(self.project, items, dlg.cols.value(), dlg.rows.value(), dlg.dx.value(),
                                dlg.dy.value(), dlg.gap.currentData())
        else:
            new = ed.circular_array(self.project, items, dlg.count.value(), (dlg.cx.value(), dlg.cy.value()),
                                    dlg.angle.value(), dlg.rotate.isChecked())
        for it in new:
            self.canvas.add_item(it)
        self.canvas.select_only([it.id for it in items + new])
        self._invalidate()

    def _on_items_transformed(self, ids):
        if self.objects.item and self.objects.item.id in ids:
            self.objects.refresh()
        self._invalidate()

    def _toggle_snap(self):
        self.canvas.snap_enabled = self.a_snap.isChecked()

    def _set_snap_step(self, step, menu):
        self.canvas.snap_step = step
        for a in menu.actions():
            a.setChecked(a.text() == f"{step:g} mm")
        self.a_snap.setChecked(True)
        self.canvas.snap_enabled = True

    # ------------------------------------------------------------ Werkzeuge
    def trace_selected(self):
        rasters = [it for it in self._selected_items() if it.kind == "raster"]
        if len(rasters) != 1:
            QMessageBox.information(self, "Nachzeichnen", "Bitte genau ein Bild auswählen.")
            return
        it = rasters[0]
        dlg = TraceDialog(self.project, self)
        if dlg.exec() != TraceDialog.DialogCode.Accepted:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            polys = trace_image(it.image, it.img_w, it.img_h, dlg.threshold.value(), dlg.invert.isChecked(),
                                dlg.smooth.value(), dlg.min_area.value(), brightness=dlg.bright.value(),
                                contrast=dlg.contrast.value())
        finally:
            QApplication.restoreOverrideCursor()
        if not polys:
            QMessageBox.information(self, "Nachzeichnen", "Keine Konturen gefunden – Schwellwert anpassen.")
            return
        self._checkpoint("Bild nachzeichnen")
        target = dlg.layer.currentData()
        if target in ("__cut__", "__fill__"):
            layer = self.project.add_layer("Nachgezeichnet", mode="cut" if target == "__cut__" else "fill")
        else:
            layer = self.project.layer(target)
        v = Item(id=new_id("I"), name=f"Kontur: {it.name}", kind="vector", x=it.x, y=it.y, scale=it.scale,
                 rotation=it.rotation, mirror_x=it.mirror_x)
        v.paths = [VectorPath(p, layer.id) for p in polys]
        self.project.items.append(v)
        if not dlg.keep.isChecked():
            self.project.remove_item(it.id)
            self.canvas.remove_item(it.id)
        self.canvas.add_item(v)
        self.layers.refresh(layer.id)
        self.canvas.select_only([v.id])
        self._invalidate()
        self.statusBar().showMessage(f"{len(polys)} Konturen, {sum(len(p.pts) for p in polys)} Punkte", 5000)

    def material_test(self):
        dlg = MaterialTestDialog(self.profile, self)
        if dlg.exec() != MaterialTestDialog.DialogCode.Accepted or not dlg.action:
            return
        try:
            res = generate(build_project(dlg.test()), self.profile)
        except Exception as e:
            QMessageBox.critical(self, "Materialtest", str(e))
            return
        if dlg.action == "preview":
            self.a_preview.setChecked(False)
            self._set_sim_enabled(False)
            self.canvas.clear_simulation()
            self.canvas.show_toolpath(res, True)
            self.statusBar().showMessage(f"Vorschau Materialtest – {fmt_time(res.time_s)}. "
                                         "Mit Strg+P (Vorschau) wieder zur normalen Ansicht.", 10000)
        elif dlg.action == "export":
            p, _ = QFileDialog.getSaveFileName(self, "Materialtest exportieren",
                                               os.path.join(self.last_dir, "materialtest.gcode"), "G-Code (*.gcode *.nc)")
            if p:
                with open(p, "w", encoding="ascii", errors="replace", newline="\n") as f:
                    f.write(res.text)
        elif dlg.action == "send":
            if not self.machine.is_ready():
                QMessageBox.information(self, "Materialtest", "Laser nicht verbunden oder beschäftigt.")
                return
            if QMessageBox.question(self, "Materialtest", f"Materialtest starten? Dauer ca. {fmt_time(res.time_s)}\n\n"
                                    "⚠ Schutzbrille tragen, Laser nie unbeaufsichtigt lassen!") \
                    == QMessageBox.StandardButton.Yes:
                self.machine.run_job(res.lines)

    def resume_job(self):
        conn = self.machine.conn
        if not conn.last_job or not self.machine.is_ready():
            return
        total = len(conn.last_job)
        default = max(0, conn.last_acked - 15)
        n, ok = QInputDialog.getInt(
            self, "Auftrag fortsetzen",
            f"Abgebrochen nach Zeile {conn.last_acked} von {total}.\n"
            "Die Steuerung puffert einige Befehle; deshalb wird standardmäßig etwas früher\n"
            "neu begonnen (kurze Stücke werden doppelt gelasert).\n\nAb Zeile fortsetzen:",
            default + 1, 1, total, 1)
        if not ok:
            return
        lines = resume_lines(conn.last_job, n - 1, self.profile)
        if QMessageBox.question(self, "Fortsetzen", f"Ab Zeile {n} fortsetzen ({total - n + 1} Zeilen)?\n"
                                "Werkstück darf nicht verschoben worden sein!") == QMessageBox.StandardButton.Yes:
            self.machine.run_job(lines)

    def _toggle_rotary(self):
        self.profile.rotary = self.a_rotary.isChecked()
        storage.save_machine(self.profile)
        self._invalidate()
        if self.profile.rotary:
            self.statusBar().showMessage(f"Rotationsachse aktiv: Y-Faktor {self.profile.y_scale:.4f}", 5000)

    # ------------------------------------------------------------ Simulation
    def _set_sim_enabled(self, on):
        for w in (self.btn_play, self.sim_slider, self.sim_speed, self.lbl_sim):
            w.setEnabled(on)
        if not on:
            self.sim_timer.stop()
            self.btn_play.setText("▶")
            self.lbl_sim.setText("")

    def _sim_ensure(self):
        if self._sim_t is None and self.result is not None:
            self.canvas.start_simulation(self.result)
            self._sim_t = 0.0

    def _sim_slider(self, v):
        if self.result is None:
            return
        self._sim_ensure()
        self._sim_t = self.result.time_s * v / 1000
        self.canvas.set_simulation_time(self._sim_t)
        self.lbl_sim.setText(f"{fmt_time(self._sim_t)} / {fmt_time(self.result.time_s)}")

    def _sim_play(self):
        if self.result is None:
            return
        if self.sim_timer.isActive():
            self.sim_timer.stop()
            self.btn_play.setText("▶")
            return
        self._sim_ensure()
        if self.sim_slider.value() >= 1000:
            self.sim_slider.setValue(0)
        self.btn_play.setText("⏸")
        self.sim_timer.start()

    def _sim_tick(self):
        if self.result is None or self.result.time_s <= 0:
            self.sim_timer.stop()
            return
        t = (self._sim_t or 0.0) + 0.04 * self.sim_speed.currentData()
        if t >= self.result.time_s:
            t = self.result.time_s
            self.sim_timer.stop()
            self.btn_play.setText("▶")
        self.sim_slider.blockSignals(True)
        self.sim_slider.setValue(int(t / self.result.time_s * 1000))
        self.sim_slider.blockSignals(False)
        self._sim_t = t
        self.canvas.set_simulation_time(t)
        self.lbl_sim.setText(f"{fmt_time(t)} / {fmt_time(self.result.time_s)}")

    # ------------------------------------------------------------ Zeichnen / Knoten
    def _on_tool_changed(self, tool):
        a = self.tool_actions.get(tool)
        if a and not a.isChecked():
            a.setChecked(True)
        names = {"select": "", "rect": "Rechteck: ziehen", "ellipse": "Ellipse: ziehen",
                 "polygon": "Polygon: ziehen", "line": "Linie: klicken, Doppelklick/Enter = fertig, Esc = abbrechen",
                 "node": "Knoten: ziehen = verschieben, Anfasser ziehen = Kurve formen (Alt: einzeln), "
                         "Doppelklick = einfügen, Entf = löschen, C = Kurve, L = Linie, S = glätten, "
                         "B = auftrennen"}
        if names.get(tool):
            self.statusBar().showMessage(names[tool])
        else:
            self.statusBar().clearMessage()

    def _drawing_layer(self):
        cur = self.layers.current_layer()
        if cur is not None and cur.mode != "raster":
            return cur
        l = next((l for l in self.project.layers if l.mode == "cut"), None)
        return l or self.project.add_layer("Zeichnung", mode="cut")

    def _on_shape_drawn(self, kind, d):
        if kind == "line":
            pts = np.array(d["pts"], float)
            bb = pts.min(axis=0), pts.max(axis=0)
            c = (bb[0] + bb[1]) / 2
            poly = shapes.polyline(pts - c, d["closed"])
            name = "Polylinie" if len(pts) > 2 else "Linie"
        else:
            (x0, y0), (x1, y1) = d["p0"], d["p1"]
            w, h = abs(x1 - x0), abs(y1 - y0)
            c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            if kind == "rect":
                poly, name = shapes.rectangle(w, h, d.get("radius", 0.0)), "Rechteck"
            elif kind == "ellipse":
                poly, name = shapes.ellipse(w, h), "Ellipse"
            else:
                poly, name = shapes.polygon(d.get("sides", 6), w, h, d.get("star", 0.0)), (
                    "Stern" if d.get("star") else "Polygon")
        self._checkpoint(f"{name} zeichnen")
        layer = self._drawing_layer()
        it = Item(id=new_id("I"), name=name, kind="vector", x=float(c[0]), y=float(c[1]))
        it.paths = [VectorPath(poly, layer.id)]
        self.project.items.append(it)
        self.canvas.add_item(it)
        self.layers.refresh(layer.id)
        self.canvas.set_tool("select")
        self.canvas.select_only([it.id])
        self._invalidate()

    def _on_nodes_edited(self, item_id):
        it = self.project.item(item_id)
        if it is not None and not it.paths:                    # letzte Kontur gelöscht
            self.project.remove_item(item_id)
            self.canvas.remove_item(item_id)
        self.objects.refresh()
        self._invalidate()

    # ------------------------------------------------------------ Formen
    def _vector_selection(self):
        return [it for it in self._selected_items() if it.kind == "vector"]

    def boolean_op(self, op):
        items = self._vector_selection()
        if len(items) < 2:
            self.statusBar().showMessage("Mindestens zwei Vektorobjekte auswählen.", 4000)
            return
        if op == "difference":
            items.sort(key=lambda it: -((it.world_bbox()[2] - it.world_bbox()[0]) *
                                        (it.world_bbox()[3] - it.world_bbox()[1])))
        groups = [[p for p in it.world_paths() if p.closed] for it in items]
        if any(not grp for grp in groups):
            QMessageBox.information(self, "Formen", "Boolesche Operationen funktionieren nur mit geschlossenen "
                                                    "Konturen. Ggf. zuerst „Pfade verbinden / schließen“.")
            return
        try:
            res = bo.boolean_many(groups, op)
        except bo.BooleanUnavailable as e:
            QMessageBox.warning(self, "Formen", str(e))
            return
        self._checkpoint({"union": "Vereinigen", "difference": "Abziehen", "intersection": "Schnittmenge",
                          "xor": "Ausschluss"}[op])
        layer_id = next(iter(items[0].layer_ids()))
        for it in items:
            self.project.remove_item(it.id)
            self.canvas.remove_item(it.id)
        if not res:
            self.statusBar().showMessage("Ergebnis ist leer.", 4000)
            self._invalidate()
            return
        new = self.project._item_from_world(items[0].name, [(p, layer_id) for p in res])
        self.project.items.append(new)
        self.canvas.add_item(new)
        self.canvas.select_only([new.id])
        self._invalidate()

    def offset_dialog(self):
        items = self._vector_selection()
        if not items:
            return
        dlg = OffsetDialog(self)
        if dlg.exec() != OffsetDialog.DialogCode.Accepted:
            return
        self._checkpoint("Offset")
        new_ids = []
        for it in items:
            res = bo.offset_shapes(it.world_paths(), dlg.dist.value(), dlg.round.isChecked())
            if not res:
                continue
            layer_id = next(iter(it.layer_ids()))
            n = self.project._item_from_world(f"{it.name} (Offset)", [(p, layer_id) for p in res])
            self.project.items.append(n)
            self.canvas.add_item(n)
            new_ids.append(n.id)
            if not dlg.keep.isChecked():
                self.project.remove_item(it.id)
                self.canvas.remove_item(it.id)
        self.canvas.select_only(new_ids)
        self._invalidate()

    def join_selected(self):
        items = self._vector_selection()
        if not items:
            return
        tol, ok = QInputDialog.getDouble(self, "Pfade verbinden", "Maximaler Abstand der Endpunkte (mm):",
                                         0.1, 0.0, 10.0, 3)
        if not ok:
            return
        self._checkpoint("Pfade verbinden")
        before = after = 0
        for it in items:
            by_layer = {}
            for vp in it.paths:
                by_layer.setdefault(vp.layer_id, []).append(vp.poly)
            before += len(it.paths)
            it.paths = [VectorPath(p, lid) for lid, polys in by_layer.items()
                        for p in g.join_paths(polys, tol / max(it.scale, 1e-9))]
            after += len(it.paths)
            self.canvas.refresh_item(it.id, geometry=True)
        self.statusBar().showMessage(f"{before} Pfade → {after} Pfade", 4000)
        self._invalidate()

    def text_on_path_dialog(self):
        items = self._vector_selection()
        if len(items) != 1:
            QMessageBox.information(self, "Text auf Pfad", "Bitte genau ein Vektorobjekt (den Pfad) auswählen.")
            return
        it = items[0]
        paths = it.world_paths()
        path = max(paths, key=lambda p: p.length())
        dlg = TextPathDialog(self.project, self)
        if dlg.exec() != TextPathDialog.DialogCode.Accepted:
            return
        text = dlg.text.toPlainText().replace("\n", " ").strip()
        font = dlg.font_path()
        if not text or not font:
            return
        try:
            polys = text_on_path(text, font, dlg.size.value(), path.pts, path.closed, dlg.offset.value(),
                                 dlg.distance.value(), dlg.align.currentData(), dlg.letter.value(),
                                 dlg.flip.isChecked())
        except Exception as e:
            QMessageBox.critical(self, "Text auf Pfad", str(e))
            return
        if not polys:
            QMessageBox.information(self, "Text auf Pfad", "Der Text passt nicht auf den Pfad.")
            return
        self._checkpoint("Text auf Pfad")
        target = dlg.layer.currentData()
        if target == TextDialog.NEW_FILL:
            layer = self.project.add_layer("Text Gravur", mode="fill")
        elif target == TextDialog.NEW_CUT:
            layer = self.project.add_layer("Text Kontur", mode="cut")
        else:
            layer = self.project.layer(target)
        n = self.project._item_from_world(f"Text: {text[:20]}", [(p, layer.id) for p in polys])
        self.project.items.append(n)
        self.canvas.add_item(n)
        self.layers.refresh(layer.id)
        self.canvas.select_only([n.id])
        self._invalidate()

    # ------------------------------------------------------------ Export
    def export_vector(self, fmt):
        if not self.project.items:
            return
        base = os.path.splitext(os.path.basename(self.project_path))[0] if self.project_path else "zeichnung"
        filt = "SVG (*.svg)" if fmt == "svg" else "DXF (*.dxf)"
        p, _ = QFileDialog.getSaveFileName(self, f"Als {fmt.upper()} exportieren",
                                           os.path.join(self.last_dir, f"{base}.{fmt}"), filt)
        if not p:
            return
        try:
            if fmt == "svg":
                export_svg(self.project, p, self.profile.bed_w, self.profile.bed_h)
            else:
                export_dxf(self.project, p)
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        msg = f"Gespeichert: {p}"
        if fmt == "dxf" and any(it.kind == "raster" for it in self.project.items):
            msg += "  (Bilder sind in DXF nicht enthalten)"
        self.statusBar().showMessage(msg, 6000)

    # ------------------------------------------------------------ Kamera
    def camera_select(self):
        devs = camera_devices()
        choices = devs + ["Bild aus Datei (z. B. Handyfoto)"]
        cur = self.profile.camera_device
        idx = devs.index(cur) if cur in devs else len(devs)
        item, ok = QInputDialog.getItem(self, "Kamera", "Kamera wählen:", choices, idx, False)
        if not ok:
            return
        self.profile.camera_device = "__file__" if item not in devs else item
        storage.save_machine(self.profile)

    def _grab(self, callback):
        dev = self.profile.camera_device
        if dev == "__file__" or not camera_devices():
            p, _ = QFileDialog.getOpenFileName(self, "Kamerabild", self.last_dir,
                                               "Bilder (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
            if not p:
                return
            from PIL import Image, ImageOps
            im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            callback(np.asarray(im).copy())
            return
        self.statusBar().showMessage("Kamerabild wird aufgenommen …")
        self._grabber = CameraGrabber(dev, callback,
                                      lambda msg: QMessageBox.warning(self, "Kamera", f"Aufnahme fehlgeschlagen:\n{msg}"))

    def camera_markers(self):
        if not self.machine.is_ready():
            QMessageBox.information(self, "Kamera", "Laser verbinden, dann Passmarken lasern.")
            return
        choices = ["9 Marken (mit Linsenkorrektur, empfohlen)", "4 Marken (ohne Linsenkorrektur)"]
        item, ok = QInputDialog.getItem(self, "Passmarken", "Anzahl Passmarken:", choices,
                                        0 if self.profile.camera_count == 9 else 1, False)
        if not ok:
            return
        self.profile.camera_count = 9 if item.startswith("9") else 4
        storage.save_machine(self.profile)
        pts = cam.marker_positions(self.profile.bed_w, self.profile.bed_h, self.profile.camera_inset,
                                   self.profile.camera_count)
        txt = ", ".join(f"({x:g}/{y:g})" for x, y in pts)
        if QMessageBox.question(self, "Passmarken", f"{len(pts)} Kreuze mit 10 % Leistung lasern bei {txt} mm?\n"
                                "Ein Stück Pappe o. Ä. unterlegen. Schutzbrille tragen!") \
                != QMessageBox.StandardButton.Yes:
            return
        s = round(0.10 * self.profile.s_max, 3)
        self.machine.run_job(cam.marker_gcode(pts, power_s=s, laser_on=self.profile.laser_on or "M3"))

    def camera_align(self):
        def got(img):
            n = self.profile.camera_count
            dlg = PointPickDialog(img, self, cam.marker_labels(n))
            if dlg.exec() != PointPickDialog.DialogCode.Accepted or len(dlg.points) != n:
                return
            mm = np.array(cam.marker_positions(self.profile.bed_w, self.profile.bed_h, self.profile.camera_inset, n))
            px = np.array(dlg.points)
            if n >= 6:
                k1, H, err = cam.fit_lens(px, mm, (img.shape[1], img.shape[0]))
                msg = f"Kamera ausgerichtet: Verzeichnung k1 = {k1:+.3f}, Restfehler {err:.2f} mm."
            else:
                k1, H = 0.0, cam.homography(px, mm)
                msg = "Kamera ausgerichtet (ohne Linsenkorrektur)."
            self.profile.camera_h = [float(v) for v in H.ravel()]
            self.profile.camera_k1 = float(k1)
            storage.save_machine(self.profile)
            self._show_camera(img)
            self.statusBar().showMessage(msg, 8000)
        self._grab(got)

    def camera_update(self):
        if len(self.profile.camera_h) != 9:
            QMessageBox.information(self, "Kamera", "Kamera zuerst ausrichten (Maschine → Kamera → Kamera ausrichten).")
            return
        self._grab(self._show_camera)

    def _show_camera(self, img):
        H = np.array(self.profile.camera_h, float).reshape(3, 3)
        try:
            rect = cam.rectify(img, H, self.profile.bed_w, self.profile.bed_h, 2.0, self.profile.camera_k1)
        except Exception as e:
            QMessageBox.warning(self, "Kamera", f"Entzerrung fehlgeschlagen: {e}")
            return
        self._camera_img = rect
        self.canvas.set_background(rect, self.profile.bed_w, self.profile.bed_h, self.profile.camera_opacity)
        self.a_cam_show.setChecked(True)
        self.statusBar().clearMessage()

    def _camera_toggle(self):
        if self.a_cam_show.isChecked():
            if self._camera_img is not None:
                self.canvas.set_background(self._camera_img, self.profile.bed_w, self.profile.bed_h,
                                           self.profile.camera_opacity)
            else:
                self.camera_update()
        else:
            self.canvas.clear_background()

    def closeEvent(self, e):
        if self.machine.conn.job_active and QMessageBox.question(
                self, "Beenden", "Ein Auftrag läuft noch. Trotzdem beenden (Laser wird gestoppt)?") \
                != QMessageBox.StandardButton.Yes:
            e.ignore()
            return
        if self.machine.conn.job_active:
            self.machine.conn.stop()
        self.machine.shutdown()
        storage.save_machine(self.profile)
        e.accept()

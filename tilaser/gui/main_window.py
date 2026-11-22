"""Hauptfenster von TiLaser."""
from __future__ import annotations

import copy
import os

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QColor, QKeySequence
from PyQt6.QtWidgets import (QApplication, QCheckBox, QDockWidget, QFileDialog, QFrame, QInputDialog,
                             QComboBox, QLabel, QMainWindow, QMenu, QMessageBox, QScrollArea, QSlider, QTabWidget,
                             QDoubleSpinBox, QSpinBox, QToolBar, QToolButton,
                             QVBoxLayout, QWidget)

from .. import __version__
from ..core import geometry as g
from ..core import storage
from ..core.dxf_import import import_dxf
from ..core import editing as ed
from ..core.gcode import (DIALECTS, convex_hull, frame_gcode, generate, job_project, origin_offset, resume_lines,
                          z_offset_at)
from ..core.materialtest import build_project
from ..core.trace import trace_image
from .busy import active as busy_active
from .busy import run_busy
from ..core.model import MODE_LABELS, PALETTE, Item, Project, VectorPath, color_name, new_id
from ..core.raster import load_image_gray
from ..core.svg_import import import_svg
from ..core.text_import import text_to_polylines
from .canvas import LaserCanvas
from . import icons
from .dialogs import (ArrayDialog, BarcodeDialog, BoxDialog, ParamDialog, CameraGrabber, VarDialog, GcodeDialog, MachineDialog, MaterialTestDialog, NestDialog, FocusTestDialog, IntervalTestDialog,
                      OffsetDialog,
                      PointPickDialog, TextDialog, TextPathDialog, TraceDialog, camera_devices)
from .camera_live import CameraPanel, LiveCamera
from ..core import vartext as vt
from ..core.i18n import tr as i18n_tr
from . import help as help_mod
from ..core.eps_import import import_eps
from ..core.lbrn_export import export_lbrn
from ..core.lbrn_import import load_lbrn
from ..core.pdf_import import PdfError, import_pdf
from ..core.text_import import text_on_path
from ..core import boolean as bo
from ..core import camera as cam
from ..core import shapes
from ..core.export import export_dxf, export_svg
from ..core.nesting import NestSettings, nest
from .machine_panel import MachinePanel
from .panels import LayerPanel, ObjectPanel, color_icon, pick_layer_color

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
        self.canvas.nudge_steps = (self.profile.nudge_small, self.profile.nudge, self.profile.nudge_large)
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
        self.live_cam = LiveCamera(self)
        self.camera_panel = CameraPanel(self.live_cam, self.profile)
        self.tabs.addTab(self.camera_panel, "Kamera")
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
        self.canvas.edgesPicked.connect(self._edges_picked)
        self.canvas.edgeStep.connect(self._edge_step)
        self._grabber = None
        self._camera_img = None
        self._live_bg_timer = QTimer(self)
        self._live_bg_timer.setInterval(2000)
        self._live_bg_timer.timeout.connect(self._live_background)
        self._live_bg_timer.start()
        self.camera_panel.profileChanged.connect(lambda: storage.save_machine(self.profile))
        self.camera_panel.backgroundToggled.connect(self._live_bg_toggled)
        self.machine.jobStarted.connect(self._on_job_started)
        self.camera_panel.flameDetected.connect(self._on_flame)
        self.machine.jobEnded.connect(lambda _s: self.camera_panel.job_finished())
        self.machine.jobEnded.connect(self._on_job_ended)
        self.machine.jobStarted.connect(self._job_log_started)
        self.machine.jobEnded.connect(self._job_log_ended)
        self.machine.macrosEdited.connect(self._bind_macro_keys)
        self._macro_shortcuts = []
        self._bind_macro_keys()
        self.layers.layersChanged.connect(self._on_layers_changed)
        self.objects.changed.connect(self._on_object_changed)
        self.objects.action.connect(self._on_object_action)
        self.machine.startRequested.connect(self.start_job)
        self.machine.frameRequested.connect(self.run_frame)
        self.machine.firmwareDetected.connect(self._on_firmware)
        self.machine.profileChanged.connect(lambda: storage.save_machine(self.profile))
        QTimer.singleShot(800, self.machine.auto_connect)       # letzte Verbindung wiederherstellen
        self.web = None
        self._flame_alarm = 0.0
        self._flame_did = ""
        self._web_jpeg_t = 0.0
        self._web_timer = QTimer(self)
        self._web_timer.setInterval(300)
        self._web_timer.timeout.connect(self._web_tick)
        QTimer.singleShot(1000, self._web_apply)
        help_mod.install(self)                      # F1-Kontexthilfe (vor der Übersetzung der Menütexte)
        self.machine.rubber_show.toggled.connect(self._set_rubber_show)
        self.machine.only_sel.toggled.connect(lambda _: self._update_rubber())
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
        self.a_save_sel = self._act("Auswahl als Projekt speichern…", self.save_selection, None,
                                    "Nur die ausgewählten Objekte (mit ihren Ebenen) als eigene .tlp-Datei speichern")
        self.a_import = self._act("Importieren…", self.import_dialog, "Ctrl+I", "SVG, DXF oder Bild importieren")
        self.a_text = self._act("Text einfügen…", self.insert_text, "Ctrl+T")
        self.a_export = self._act("G-Code exportieren…", self.export_gcode, "Ctrl+E")
        for a in (self.a_new, self.a_open, self.a_save, self.a_save_as, self.a_save_sel):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_import)
        m.addAction(self.a_text)
        m.addAction(self.a_export)
        m.addAction(self._act("Als SVG exportieren…", lambda: self.export_vector("svg")))
        m.addAction(self._act("Als DXF exportieren…", lambda: self.export_vector("dxf")))
        m.addAction(self._act("Als LightBurn-Datei speichern…", lambda: self.export_vector("lbrn2"),
                              None, "Projekt als .lbrn2 für LightBurn speichern (Ebenen, Formen, Bilder)"))
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
        self.a_edge_align = self._act("Kanten aneinanderlegen", self._edge_align_start, "Alt+K",
                                      "Erst eine Kante des Teils anklicken, das bewegt wird, dann die Zielkante am "
                                      "anderen Teil: das erste Teil wird gedreht und bündig angelegt")
        ar.addAction(self.a_edge_align)
        ar.addSeparator()
        ar.addAction(self._act("Horizontal spiegeln", lambda: self.mirror_selected("h")))
        ar.addAction(self._act("Vertikal spiegeln", lambda: self.mirror_selected("v")))
        ar.addAction(self._act("90° nach links drehen", lambda: self.rotate_selected(90), "Ctrl+L"))
        ar.addAction(self._act("90° nach rechts drehen", lambda: self.rotate_selected(-90), "Ctrl+R"))
        ar.addSeparator()
        self.a_array = self._act("Array erstellen…", self.array_dialog, "Ctrl+Shift+A",
                                 "Auswahl als Raster oder im Kreis vervielfältigen")
        ar.addAction(self.a_array)
        self.a_nest = self._act("Nesting (Teile anordnen)…", self.nest_dialog, "Ctrl+Shift+N",
                                "Ausgewählte Teile platzsparend auf einer Grundfläche anordnen")
        ar.addAction(self.a_nest)
        ar.addAction(self.a_center)
        ar.addAction(self.a_origin)
        ar.addSeparator()
        om = ar.addMenu("Schnittreihenfolge")
        for text, where, sc in (("Zuerst schneiden", "first", "Ctrl+Shift+PgUp"), ("Früher schneiden", "up", "Ctrl+PgUp"),
                                ("Später schneiden", "down", "Ctrl+PgDown"), ("Zuletzt schneiden", "last", "Ctrl+Shift+PgDown")):
            om.addAction(self._act(text, lambda _=False, w_=where: self.reorder_selected(w_), sc))
        om.addSeparator()
        self.a_show_order = self._act("Reihenfolge anzeigen", lambda: self._update_order_labels(), None,
                                      "Nummer jedes Objekts in der Schnittreihenfolge auf der Arbeitsfläche zeigen")
        self.a_show_order.setCheckable(True)
        om.addAction(self.a_show_order)

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
        tests = wz.addMenu("Lasertests")                       # alle Testmuster an einer Stelle
        tests.addAction(self.a_mattest)
        tests.addAction(self._act("Fokustest…", self.focus_test, None,
                                  "Besten Fokusabstand finden: Rampe auf schrägem Brett oder Linien mit Z-Versatz"))
        tests.addAction(self._act("Zeilenabstandstest…", self.interval_test, None,
                                  "Felder mit unterschiedlichem Zeilenabstand gravieren"))
        wz.addSeparator()
        wz.addAction(self._act("QR-Code / Barcode einfügen…", self.insert_barcode, "Ctrl+Shift+Q",
                               "QR-Code oder Code-128-Barcode als Gravur einfügen (Platzhalter erlaubt)"))
        wz.addAction(self._act("Variable Texte…", self.var_settings, None,
                               "Fortlaufende Nummer und CSV-Datei für Platzhalter wie {nr} oder {Name}"))
        gen = wz.addMenu("Generatoren")
        gen.addAction(self._act("Box-Generator…", self.box_generator, "Ctrl+Shift+B",
                                "Kasten mit Fingerzinken aus Plattenmaterial erzeugen"))
        gen.addAction(self._act("Zahnrad…", self.gear_generator, None,
                                "Stirnrad mit Evolventenverzahnung, optional mit passendem Gegenrad"))
        gen.addAction(self._act("Puzzle…", self.puzzle_generator, None, "Puzzle mit zufälligen Nasen"))
        mu = wz.addMenu("Muster in Fläche")
        mu.addAction(self._act("Biegescharnier…", lambda: self.pattern_tool("hinge"), None,
                               "Schnittmuster, mit dem sich Holz in der ausgewählten Fläche biegen lässt"))
        wz.addAction(self._act("Biegescharnier zwischen zwei Kanten…", self.bend_between_edges, None,
                               "Zwei Kanten anklicken, Radius und Winkel eingeben: die Teile werden mit einer "
                               "passend langen Biegezone zu einem Teil verbunden"))
        mu.addAction(self._act("Lochmuster / Lüftungsgitter…", lambda: self.pattern_tool("holes"), None,
                               "Löcher oder Langlöcher in der ausgewählten Fläche verteilen"))
        mu.addAction(self._act("Gravurmuster…", lambda: self.pattern_tool("engrave"), None,
                               "Hahnentritt, Fischgrät, Waben … als Gravur in der ausgewählten Fläche"))
        wz.addAction(self._act("Fingerzinken an Kante…", self.finger_joint_tool, None,
                               "Zinken an der gemeinsamen Kante zweier Teile, optional mit Dogbone-Ecken"))
        wz.addAction(self._act("Puzzle-Verbindung an Kante…", self.puzzle_joint_tool, None,
                               "Puzzle-Nasen an der gemeinsamen Kante zweier Teile, die Kante an Kante liegen"))
        wz.addAction(self._act("Snap-Lock an Kante…", self.snap_lock_tool, None,
                               "Federnde Rastlaschen an einem Teil, passende Schlitze im anderen (Steckverbindung "
                               "im rechten Winkel ohne Leim)"))
        wz.addAction(self._act("Hammerverbindung an Kante…", self.hammer_joint_tool, None,
                               "Zapfen mit mitgelasertem Keil an einem Teil, Zapfenlöcher im anderen; der "
                               "eingeschlagene Keil klemmt die Verbindung (ohne Schrauben und Leim)"))
        wz.addAction(self._act("Knöchelscharnier an Kante…", self.knuckle_hinge_tool, None,
                               "Scharnier zwischen zwei Boxteilen (z. B. Rückwand Box und Deckel): Knöchel mit je zwei "
                               "Fingerzinken, Schlitze in den Wänden, Stift"))
        wz.addAction(self._act("Federverschluss an Kante…", self.spring_latch_tool, None,
                               "Verschluss für einen Deckel: Zunge mit Haken an einer Mäanderfeder (S-Form) im einen "
                               "Teil, Aussparung mit Schulter im anderen"))
        wz.addAction(self._act("Federriegel mit Haken…", self.slider_latch_tool, None,
                               "Riegel hinter der Vorderwand, dessen Haken durch Schlitze im Deckel greifen; eine lange "
                               "Mäanderfeder schiebt ihn zurück"))
        wz.addAction(self._act("Teile nummerieren…", self.number_parts_tool, None,
                               "Teile durchnummerieren und die Nummer eingravieren; gleiche Teile bekommen dieselbe "
                               "Nummer"))
        wz.addAction(self._act("Schablonen-Stege…", self.stencil_tool, None,
                               "Stege einfügen, damit Innenteile (O, A, B …) beim Ausschneiden nicht herausfallen"))

        v = mb.addMenu("&Ansicht")
        self._build_language_menu(v)
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
        self.a_node_snap = self._act("An Knoten fangen", self._toggle_node_snap, None,
                                     "Beim Ziehen rastet der Knoten, an dem man das Teil greift, an Knoten anderer "
                                     "Teile ein (Alt gedrückt halten = ohne Fangen)")
        self.a_node_snap.setCheckable(True)
        self.a_node_snap.setChecked(True)
        v.addAction(self.a_node_snap)
        self.a_rubber_show = self._act("Gummiband-Rahmen anzeigen", lambda: self._set_rubber_show(
            self.a_rubber_show.isChecked()), None, "Linie, die der Gummiband-Rahmen abfährt, auf der Arbeitsfläche "
                                                     "zeigen – nur zur Ansicht, wird nicht gelasert")
        self.a_rubber_show.setCheckable(True)
        self.a_rubber_show.setChecked(self.profile.frame_rubber_show)
        v.addAction(self.a_rubber_show)
        v.addAction(self._act("Schrittweiten der Pfeiltasten…", self.nudge_steps_dialog, None,
                              "Wie weit die Pfeiltasten ausgewählte Teile verschieben: klein (Strg), mittel, groß "
                              "(Shift)"))
        sm = v.addMenu("Rastermaß")
        for st in (0.5, 1.0, 5.0, 10.0):
            a = sm.addAction(f"{st:g} mm")
            a.setCheckable(True)
            a.setChecked(st == 1.0)
            a.triggered.connect(lambda _=False, x=st, m=sm: self._set_snap_step(x, m))

        mm = mb.addMenu("&Maschine")
        mm.addAction(self._act("Maschineneinstellungen…", self.machine_settings))
        mm.addAction(self._act("GRBL-Einstellungen ($$)…", self.machine.edit_grbl_settings, None,
                               "Einstellungen der GRBL-Steuerung anzeigen und ändern"))
        mm.addAction(self._act("Makros bearbeiten…", self.machine.edit_macros, None,
                               "Eigene G-Code-Knöpfe mit Tastenkürzel"))
        mm.addAction(self._act("Auftragsprotokoll…", self.job_log_dialog, None,
                               "Alle Aufträge mit Dauer, Ergebnis und Kosten"))
        mm.addAction(self._act("Weboberfläche…", self.web_settings, None,
                               "Laser vom Handy oder einem anderen Rechner im Netz beobachten und bedienen"))
        self.a_rotary = self._act("Rotationsachse aktiv", self._toggle_rotary)
        self.a_rotary.setCheckable(True)
        self.a_rotary.setChecked(self.profile.rotary)
        mm.addAction(self.a_rotary)
        mm.addSeparator()
        lm = mm.addMenu("Laser zur Auswahl fahren")
        self._fill_move_menu(lm)
        mm.addAction(self._act("Startpunkt auf gewählten Knoten setzen", lambda: self.canvas.node_command("start"),
                               None, "Im Knoten-Werkzeug (N) einen Knoten anklicken: dort beginnt der Schnitt "
                                     "dieser Kontur (Taste A)"))
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
        cm.addSeparator()
        cm.addAction(self._act("Live-Kamera", self._camera_tab, "Ctrl+Shift+K",
                               "Webcam-Live-Bild im Tab „Kamera“ zeigen und einschalten"))
        cm.addAction(self._act("Großes Kamerafenster", self.camera_panel.open_window, None,
                               "Live-Bild in einem eigenen Fenster, z. B. auf dem zweiten Bildschirm"))

        h = mb.addMenu("&Hilfe")
        self.a_manual = self._act("Bedienungsanleitung", lambda: help_mod.show_help(None), "F1",
                                  "Bedienungsanleitung öffnen – F1 zeigt überall den passenden Abschnitt")
        self.a_manual.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)    # F1 übernimmt die Kontexthilfe
        h.addAction(self.a_manual)
        h.addAction(self._act("Bedienungsanleitung als PDF", help_mod.open_pdf))
        h.addSeparator()
        h.addAction(self._act("Lizenzen…", self.show_licenses, None, "Lizenzen von TiLaser und der mitgelieferten Bibliotheken"))
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
        for key, text, sc, tip in (("select", "⬚", "V", "Auswählen und verschieben (V). Rahmen nach rechts = nur ganz umschlossene, nach links = berührte Objekte"),
                                   ("rect", "▭", "R", "Rechteck zeichnen (R) – Shift: Quadrat, Strg: von der Mitte"),
                                   ("ellipse", "◯", "E", "Ellipse zeichnen (E) – Shift: Kreis, Strg: von der Mitte"),
                                   ("polygon", "⬡", "P", "Polygon/Stern zeichnen (P)"),
                                   ("line", "╱", "L", "Linie/Polylinie (L): klicken, Doppelklick oder Enter beendet, "
                                                      "Klick auf Startpunkt schließt, Shift = waagrecht/senkrecht"),
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
        self._apply_icons(tb, tt)

    def _apply_icons(self, tb, tt):
        """Symbole für Werkzeugleisten und Menüs; Tooltips zeigen Text und Tastenkürzel."""
        from PyQt6.QtCore import QSize
        for a in self.findChildren(QAction):
            ic = icons.icon_for_text(a.text())
            if ic is not None and not ic.isNull():
                a.setIcon(ic)
        tool_icons = {"select": "select", "rect": "rect", "ellipse": "ellipse", "polygon": "polygon",
                      "line": "line", "node": "node"}
        for key, a in self.tool_actions.items():
            ic = icons.icon(tool_icons[key])
            if not ic.isNull():
                a.setIcon(ic)
        for bar in (tb, tt):
            bar.setIconSize(QSize(22, 22))
            bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            for a in bar.actions():
                if a.isSeparator() or not a.text():
                    continue
                tip = a.toolTip() if a.toolTip() and a.toolTip() != a.text().replace("&", "") \
                    else a.text().replace("&", "").rstrip("…")
                sc = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                if sc and sc not in tip:
                    tip = f"{tip} ({sc})"
                a.setToolTip(tip)
        self._play_icons = (icons.icon("play"), icons.icon("pause"))
        self.btn_play.setIconSize(QSize(22, 22))
        self._set_play_icon(False)

    def _set_play_icon(self, playing: bool):
        ics = self.__dict__.get("_play_icons")
        ic = ics[1 if playing else 0] if ics else None
        if ic is not None and not ic.isNull():
            self.btn_play.setIcon(ic)
            self.btn_play.setText("")
        else:
            self.btn_play.setText("⏸" if playing else "▶")

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
        p, _ = QFileDialog.getOpenFileName(self, "Projekt öffnen", self.last_dir,
                                           "Projekte (*.tlp *.lbrn2 *.lbrn);;TiLaser-Projekt (*.tlp);;"
                                           "LightBurn-Projekt (*.lbrn2 *.lbrn)")
        if not p:
            return
        warn = []
        try:
            if p.lower().endswith((".lbrn2", ".lbrn")):
                self._checkpoint("LightBurn-Projekt öffnen")
                project, warn = load_lbrn(p)
            else:
                project = storage.load_project(p)
            self._set_project(project)
        except Exception as e:
            QMessageBox.critical(self, "Öffnen", f"Projekt konnte nicht geladen werden:\n{e}")
            return
        is_tlp = p.lower().endswith(".tlp")
        self.project_path = p if is_tlp else None          # LightBurn-Datei: beim Speichern als .tlp fragen
        self.last_dir = os.path.dirname(p)
        self._update_title()
        if not is_tlp:
            self.canvas.zoom_fit()
            warn = [f"{os.path.basename(p)}: {w}" for w in warn]
            if warn:
                QMessageBox.information(self, "LightBurn-Projekt", "\n".join(warn))

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

    def save_selection(self):
        ids = self.canvas.selected_ids()
        if not ids:
            QMessageBox.information(self, "Auswahl speichern", "Bitte zuerst Objekte auswählen.")
            return
        self.save_items_as_project(ids, "auswahl.tlp")

    def save_items_as_project(self, ids, default_name: str) -> str | None:
        """Objekte 'ids' samt Ebenen als eigenes Projekt speichern; Rückgabe: Pfad oder None."""
        base = os.path.splitext(os.path.basename(self.project_path))[0] + "_" if self.project_path else ""
        p, _ = QFileDialog.getSaveFileName(self, "Als Projekt speichern",
                                           os.path.join(self.last_dir, base + default_name),
                                           "TiLaser-Projekt (*.tlp)")
        if not p:
            return None
        if not p.lower().endswith(".tlp"):
            p += ".tlp"
        try:
            storage.save_project(ed.extract_items(self.project, ids), p)
        except Exception as e:
            QMessageBox.critical(self, "Speichern", str(e))
            return None
        self.last_dir = os.path.dirname(p)
        self.statusBar().showMessage(f"{len(ids)} Objekt(e) gespeichert: {p}", 6000)
        return p

    def _remove_items(self, ids, title):
        self._checkpoint(title)
        for i in ids:
            self.project.remove_item(i)
            self.canvas.remove_item(i)
        self.objects.set_item(None)
        self.layers.refresh()
        self._invalidate()

    # ------------------------------------------------------------ Import
    def import_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Importieren", self.last_dir,
            "Alle unterstützten (*.svg *.dxf *.pdf *.ai *.eps *.ps *.lbrn2 *.lbrn *.png *.jpg *.jpeg *.bmp *.gif *.tif "
            "*.tiff *.webp);;"
            "SVG (*.svg);;DXF (*.dxf);;PDF / Illustrator (*.pdf *.ai);;EPS / PostScript (*.eps *.ps);;"
            "LightBurn (*.lbrn2 *.lbrn);;"
            "Bilder (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)")
        if files:
            self.import_files(files)

    def import_files(self, files):
        new_ids, msgs = [], []
        if any(not f.lower().endswith(".tlp") for f in files):
            self._checkpoint("Importieren")
        for f in files:
            self.last_dir = os.path.dirname(f)
            ext = os.path.splitext(f)[1].lower()
            name = os.path.basename(f)
            try:
                if ext == ".tlp":
                    self.project_path = f
                    self._set_project(run_busy(self, f"{name} wird geladen …", storage.load_project, f))
                    self._update_title()
                    continue
                if ext in (".lbrn2", ".lbrn"):                 # Ebenen und Objekte dazuladen
                    lp, warn = run_busy(self, f"{name} wird importiert …", load_lbrn, f)
                    msgs += [f"{name}: {w}" for w in warn]
                    self.project.layers += lp.layers
                    for it in lp.items:
                        self.project.items.append(it)
                        self.canvas.add_item(it)
                        new_ids.append(it.id)
                    continue
                if ext in (".svg", ".dxf", ".pdf", ".ai", ".eps", ".ps"):
                    reader = {".svg": import_svg, ".dxf": import_dxf, ".eps": import_eps,
                              ".ps": import_eps}.get(ext, import_pdf)
                    def read(path=f, reader=reader, ext=ext):
                        try:
                            return reader(path)
                        except PdfError:
                            if ext != ".ai":
                                raise
                            return import_eps(path)          # altes Illustrator-Format (PostScript)
                    paths, warn = run_busy(self, f"{name} wird importiert …", read)
                    msgs += [f"{name}: {w}" for w in warn]
                    if not paths:
                        msgs.append(f"{name}: keine verwertbaren Konturen gefunden.")
                        continue
                    it = self.project.add_vector_import(name, paths)
                    self._place_on_bed(it)
                elif ext in IMAGE_EXT:
                    gray, w, h = run_busy(self, f"{name} wird geladen …", load_image_gray, f)
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
        spec = {"kind": "text", "template": text, "font": font, "size": dlg.size.value(),
                "spacing": dlg.spacing.value(), "letter": dlg.letter.value(), "align": dlg.align.currentData()}
        target = dlg.layer.currentData()
        self._insert_var_item(spec, target, "Text einfügen",
                              {TextDialog.NEW_FILL: ("Text Gravur", "fill"), TextDialog.NEW_CUT: ("Text Kontur", "cut")})

    def insert_barcode(self):
        dlg = BarcodeDialog(self.project, self)
        if dlg.exec() != BarcodeDialog.DialogCode.Accepted or not dlg.content.text().strip():
            return
        self._insert_var_item(dlg.spec(), dlg.layer.currentData(), "QR-Code/Barcode einfügen",
                              {BarcodeDialog.NEW_FILL: ("Code Gravur", "fill")})

    def _insert_var_item(self, spec, target, title, new_layers):
        st = vt.project_state(self.project)
        try:
            probe = vt.make_item(spec, "", 0, 0, st, vt.current_row(st))
        except Exception as e:
            QMessageBox.critical(self, title, f"Konnte nicht erzeugt werden:\n{e}")
            return
        if not probe.paths:
            QMessageBox.information(self, title, "Kein Inhalt – für diesen Text gibt es keine Konturen.")
            return
        self._checkpoint(title)
        if target in new_layers:
            name, mode = new_layers[target]
            layer = self.project.add_layer(name, mode=mode)
        else:
            layer = self.project.layer(target)
        it = vt.make_item(spec, layer.id, self.profile.bed_w / 2, self.profile.bed_h / 2, st, vt.current_row(st))
        self.project.items.append(it)
        self.canvas.add_item(it)
        self.layers.refresh(layer.id)
        self.canvas.select_only([it.id])
        self._invalidate()

    # ------------------------------------------------------------ Auswahl / Änderungen
    def _selected_items(self) -> list[Item]:
        return [self.project.item(i) for i in self.canvas.selected_ids() if self.project.item(i)]

    def _on_selection(self, ids):
        if self.machine.only_sel.isChecked():
            self._update_rubber()
        items = [it for it in (self.project.item(i) for i in ids) if it is not None]   # nur Objekte des Projekts
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
        with self.canvas.batch():
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
        with self.canvas.batch():
            for it in items:
                # Teile vorab nur zählen, wenn es überhaupt mehr als 300 werden können
                n = len(g.group_parts([vp.poly for vp in it.paths])) if len(it.paths) > 300 else 0
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
        used = {l.color.lower() for l in self.project.layers}
        free = next((c for c in PALETTE if c not in used), "#e53935")
        c = pick_layer_color(self, free, self.project, "Farbe der neuen Ebene")
        if not c:
            return
        name, ok = QInputDialog.getText(self, "Neue Ebene", "Name:", text=f"{MODE_LABELS[mode].split()[0]} {color_name(c)}")
        if not ok:
            return
        layer = self.project.add_layer(name.strip() or color_name(c), c, mode)
        self.assign_layer(layer.id)

    def _context_menu(self, global_pos):
        items = self._selected_items()
        menu = QMenu(self)
        if len(items) == 1 and items[0].var:
            a = menu.addAction("Inhalt ändern…")
            a.triggered.connect(lambda: self.edit_var_item(items[0]))
            menu.addSeparator()
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
            menu.addAction(self.a_nest)
            menu.addAction(self.a_save_sel)
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
        pos = self.canvas.__dict__.get("last_context_pos")
        menu.addSeparator()
        if pos is not None:
            a = menu.addAction(f"Laser hierher fahren (X{pos[0]:.1f} Y{pos[1]:.1f})")
            a.triggered.connect(lambda: self.machine.move_to(*pos))
        if items:
            sub = menu.addMenu("Laser zur Auswahl fahren")
            self._fill_move_menu(sub)
        menu.exec(global_pos)

    def _fill_move_menu(self, menu):
        for key, label in (("c", "Mitte"), ("bl", "Links unten"), ("tl", "Links oben"), ("tr", "Rechts oben"),
                           ("br", "Rechts unten")):
            a = menu.addAction(label)
            a.triggered.connect(lambda _=False, k=key: self.move_laser_to_selection(k))

    def move_laser_to_selection(self, anchor: str = "c"):
        items = self._selected_items()
        if not items:
            QMessageBox.information(self, "Laser zur Auswahl", "Bitte zuerst Objekte auswählen.")
            return
        bbs = np.array([it.world_bbox() for it in items])
        x0, y0, x1, y1 = bbs[:, 0].min(), bbs[:, 1].min(), bbs[:, 2].max(), bbs[:, 3].max()
        x = {"c": (x0 + x1) / 2, "bl": x0, "tl": x0, "tr": x1, "br": x1}[anchor]
        y = {"c": (y0 + y1) / 2, "bl": y0, "tl": y1, "tr": y1, "br": y0}[anchor]
        self.machine.move_to(float(x), float(y))

    def edit_var_item(self, it):
        """Text bzw. Inhalt eines Text-, QR- oder Barcode-Objekts ändern (Platzhalter erlaubt)."""
        old = it.var.get("template", "")
        if it.var.get("kind") == "text":
            txt, ok = QInputDialog.getMultiLineText(self, "Inhalt ändern", "Text (Platzhalter erlaubt):", old)
        else:
            txt, ok = QInputDialog.getText(self, "Inhalt ändern", "Inhalt (Platzhalter erlaubt):", text=old)
        if not ok or txt == old or not txt.strip():
            return
        self._checkpoint("Inhalt ändern")
        it.var["template"] = txt
        st = vt.project_state(self.project)
        try:
            vt.refresh_item(it, st, row=vt.current_row(st))
        except Exception as e:
            QMessageBox.critical(self, "Inhalt ändern", str(e))
            it.var["template"] = old
            vt.refresh_item(it, st, row=vt.current_row(st))
        self.canvas.refresh_item(it.id)
        self.objects.refresh()
        self._invalidate()

    def var_settings(self):
        st = vt.project_state(self.project)
        dlg = VarDialog(st, self)
        if dlg.exec() == VarDialog.DialogCode.Accepted:
            dlg.apply()
            self._refresh_var_items()

    def _refresh_var_items(self) -> int:
        """Objekte mit Platzhaltern neu erzeugen (vor Vorschau, Auftrag und Export)."""
        try:
            n = vt.refresh_project(self.project)
        except Exception as e:
            self.statusBar().showMessage(f"Variable Texte: {e}", 8000)
            return 0
        if n:
            for it in self.project.items:
                if it.var and vt.has_placeholders(it.var.get("template", "")):
                    self.canvas.refresh_item(it.id)
            self.objects.refresh()
            self.result = None
        return n

    def _advance_vars(self):
        st = vt.project_state(self.project)
        if st.auto_advance and any(it.var and vt.has_placeholders(it.var.get("template", ""))
                                   for it in self.project.items):
            vt.advance(self.project)
            self._refresh_var_items()
            self._invalidate()
            self.statusBar().showMessage(f"Variable Texte: nächste Nummer {st.nr}"
                                         + (f", CSV-Zeile {st.csv_row + 1}" if st.csv_path else ""), 6000)

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
    def _set_rubber_show(self, on: bool):
        self.profile.frame_rubber_show = on
        for w in (self.a_rubber_show, self.machine.rubber_show):
            if w.isChecked() != on:
                w.blockSignals(True)
                w.setChecked(on)
                w.blockSignals(False)
        storage.save_machine(self.profile)
        self._update_rubber()

    def _rubber_hull(self):
        """Punkte der Gummiband-Linie für das, was der Rahmen abfahren würde (ohne Ursprungs-Versatz)."""
        only = self.canvas.selected_ids() if self.machine.only_sel.isChecked() else None
        if self.machine.only_sel.isChecked() and not only:
            return None
        pts = job_project(self.project, only).outline_points()
        return convex_hull(pts) if pts is not None else None

    def _update_rubber(self):
        if "machine" not in self.__dict__:                   # während des Aufbaus
            return
        self.canvas.set_rubber_outline(self._rubber_hull() if self.profile.frame_rubber_show else None)

    def reorder_selected(self, where: str):
        """Objekte in der Projektliste verschieben (bestimmt 'Schnittreihenfolge: Objekt für Objekt')."""
        sel = set(self.canvas.selected_ids())
        if not sel:
            return
        self._checkpoint("Schnittreihenfolge")
        items = self.project.items
        chosen = [it for it in items if it.id in sel]
        rest = [it for it in items if it.id not in sel]
        if where == "first":
            items[:] = chosen + rest
        elif where == "last":
            items[:] = rest + chosen
        else:
            idx = [i for i, it in enumerate(items) if it.id in sel]
            rng = idx if where == "up" else idx[::-1]
            for i in rng:
                j = i - 1 if where == "up" else i + 1
                if 0 <= j < len(items) and items[j].id not in sel:
                    items[i], items[j] = items[j], items[i]
        if self.profile.cut_order != "objects":
            self.statusBar().showMessage("Hinweis: Die Reihenfolge wirkt, wenn in den Maschineneinstellungen "
                                         "„Schnittreihenfolge: Objekt für Objekt“ gewählt ist.", 6000)
        if not self.a_show_order.isChecked():
            self.a_show_order.setChecked(True)
        self._invalidate()

    def _update_order_labels(self):
        if "a_show_order" not in self.__dict__:
            return
        labels = []
        if self.a_show_order.isChecked():
            n = 0
            for it in self.project.items:
                if not any((l := self.project.layer(lid)) and l.output for lid in it.layer_ids()):
                    continue
                n += 1
                b = it.world_bbox()
                labels.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, n))
        self.canvas.set_order_labels(labels)

    def _invalidate(self):
        self._update_order_labels()
        self._update_rubber()
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
        if self.result is None:
            self._refresh_var_items()
        if self.result is None or self._result_key != key:
            self.result = run_busy(self, "G-Code wird erzeugt …", generate, self.project, self.profile,
                                   only_ids, offset)
            self._result_key = key
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
        self._advance_vars()

    # ------------------------------------------------------------ Maschine
    def start_job(self):
        if busy_active():                      # z. B. Start aus der Weboberfläche während einer Berechnung
            return
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
        self._var_job = True                     # nach dem Auftrag Platzhalter weiterzählen
        self._prepare_job_log("Auftrag", res, "TiLaser")
        self.machine.run_job(res.lines)

    def run_frame(self):
        try:
            only, offset = self._job_params()
        except ValueError as e:
            QMessageBox.information(self, "Rahmen", str(e))
            return
        jp = job_project(self.project, only, offset)
        bb = jp.bbox()
        if not bb:
            QMessageBox.information(self, "Rahmen", "Keine aktiven Objekte.")
            return
        hull = None
        if self.profile.frame_rubber:
            pts = jp.outline_points()
            hull = convex_hull(pts) if pts is not None else None
            if hull is not None and len(hull) >= 3 and not self.profile.frame_rubber_show:
                self.canvas.show_frame_outline(hull)
        if self.profile.frame_power > 0 and QMessageBox.question(
                self, "Rahmen", f"Rahmen mit {self.profile.frame_power:g} % Laserleistung fahren?\n"
                                "Schutzbrille tragen!") != QMessageBox.StandardButton.Yes:
            return
        self.machine.run_job(frame_gcode(bb, self.profile, hull))

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
        box = QMessageBox(self)
        box.setWindowTitle("Über TiLaser")
        box.setIconPixmap(self.windowIcon().pixmap(64, 64))
        box.setText(f"<b>TiLaser {__version__}</b><br>Schneiden und Gravieren mit G-Code-Lasern "
                    "(GRBL, Marlin, Smoothieware).<br><br>"
                    "Freie Software unter der GNU GPL Version 3.<br>"
                    "Quellcode: <a href='https://github.com/franzhefekranz/TiLaser'>"
                    "github.com/franzhefekranz/TiLaser</a><br>"
                    "Enthält Python, PyQt6 (GPL-3.0), Qt 6 (LGPL-3.0), FFmpeg (LGPL), NumPy, Pillow, fontTools, "
                    "pyserial und pyclipper – Einzelheiten unter <i>Lizenzen…</i>.<br><br>"
                    "<small>LightBurn ist eine Marke der LightBurn Software LLC. TiLaser ist ein "
                    "unabhängiges Projekt und steht in keiner Verbindung zu LightBurn Software.</small>")
        b_lic = box.addButton("Lizenzen…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is b_lic:
            self.show_licenses()

    def show_licenses(self):
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout
        path = os.path.join(os.path.dirname(storage.DEFAULT_PRESETS), "THIRD_PARTY_LICENSES.txt")
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            text = "Lizenzübersicht nicht gefunden."
        import sys
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(sys.argv[0]))
        lic = os.path.join(base, "licenses")
        if os.path.isdir(lic):
            text += f"\n\nVollständige Lizenztexte: {lic}"
        dlg = QDialog(self)
        dlg.setWindowTitle("Lizenzen")
        dlg.resize(760, 560)
        lay = QVBoxLayout(dlg)
        ed_ = QPlainTextEdit(text)
        ed_.setReadOnly(True)
        ed_.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace;")
        lay.addWidget(ed_)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

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
        self.layers.set_project(project)            # Bedienfelder zuerst, dann die Zeichenfläche
        self.objects.set_project(project)
        self.canvas.set_project(project)
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

    def nest_dialog(self):
        from PyQt6.QtWidgets import QProgressDialog
        items = self._selected_items()
        if not items:
            QMessageBox.information(self, "Nesting", "Bitte die anzuordnenden Teile auswählen "
                                                     "(Strg+A für alle).")
            return
        largest = max(items, key=lambda it: (it.world_bbox()[2] - it.world_bbox()[0]) *
                                            (it.world_bbox()[3] - it.world_bbox()[1])) if len(items) > 1 else None
        dlg = NestDialog(self.profile.bed_w, self.profile.bed_h, largest.world_bbox() if largest else None, self)
        if dlg.exec() != NestDialog.DialogCode.Accepted:
            return
        parts = [it for it in items if not (dlg.source.currentData() == "object" and it is largest)]
        if not parts:
            return
        st = NestSettings(x=dlg.x.value(), y=dlg.y.value(), width=dlg.w.value(), height=dlg.h.value(),
                          spacing=dlg.spacing.value(), margin=dlg.margin.value(), rotations=dlg.rot.currentData(),
                          copies=dlg.copies.value(), use_holes=dlg.holes.isChecked(),
                          shared_edges=dlg.shared.isChecked())
        self._checkpoint("Nesting")
        prog = QProgressDialog("Teile werden angeordnet …", "Abbrechen", 0, 1000, self)
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(300)
        cancelled = []

        def cb(n, total):
            prog.setValue(int(n / max(1, total) * 1000))
            QApplication.processEvents()
            if prog.wasCanceled():
                cancelled.append(True)
                raise KeyboardInterrupt

        try:
            res = nest(self.project, parts, st, cb, dlg.thorough.isChecked())
        except KeyboardInterrupt:
            prog.close()
            p = self.undo.do_undo(self.project)              # unverändert zurück
            if p is not None:
                self._restore(p)
            return
        prog.close()
        fr = None
        if dlg.frame.isChecked():
            layer = self.project.add_layer("Nesting-Grundfläche", "#757575", "cut")
            layer.output = False
            fr = Item(id=new_id("I"), name="Grundfläche", kind="vector", x=st.x + st.width / 2,
                      y=st.y + st.height / 2)
            fr.paths = [VectorPath(shapes.rectangle(st.width, st.height), layer.id)]
            self.project.items.append(fr)
        self.canvas.rebuild_all()
        self.layers.refresh()
        self.canvas.select_only(res.unplaced or res.placed)
        self._invalidate()
        msg = (f"{len(res.placed)} Teile platziert"
               + (f" (davon {len(res.new_items)} Kopien)" if res.new_items else "")
               + f", Flächennutzung {res.utilization * 100:.0f} %, Raster {res.cell:.2f} mm.")
        if not res.unplaced:
            QMessageBox.information(self, "Nesting", msg)
            return
        msg += (f"\n\n{len(res.unplaced)} Teil(e) passen nicht auf die Grundfläche – sie sind jetzt ausgewählt."
                "\n\nDie platzierten Teile können als eigene Datei (eine Platte) gespeichert werden. Mit "
                "„Speichern und entfernen“ verschwinden sie danach aus diesem Projekt, und der Rest lässt "
                "sich gleich erneut anordnen (Strg+Shift+N).")
        box = QMessageBox(QMessageBox.Icon.Information, "Nesting", msg, parent=self)
        b_save = box.addButton("Platzierte speichern…", QMessageBox.ButtonRole.ActionRole)
        b_move = box.addButton("Speichern und entfernen…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Schließen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_move)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_save, b_move):
            return
        sheet = list(res.placed) + ([fr.id] if fr is not None else [])
        n = self.__dict__.get("_sheet_no", 0) + 1
        path = self.save_items_as_project(sheet, f"platte{n}.tlp")
        if path is None:
            return
        self._sheet_no = n
        if clicked is b_move:
            self._remove_items(sheet, "Platzierte Teile entfernen")
            self.canvas.select_only(res.unplaced)
            self.statusBar().showMessage(f"Platte gespeichert ({os.path.basename(path)}). "
                                         f"{len(res.unplaced)} Teil(e) übrig – jetzt erneut nesten (Strg+Shift+N).",
                                         10000)

    def _on_items_transformed(self, ids):
        if self.objects.item and self.objects.item.id in ids:
            self.objects.refresh()
        self._invalidate()

    def _toggle_snap(self):
        self.canvas.snap_enabled = self.a_snap.isChecked()

    def nudge_steps_dialog(self):
        p = self.profile
        fields = [("small", "Klein (Strg + Pfeiltaste)", "f", p.nudge_small, (0.001, 100, 3, 0.01, " mm")),
                  ("mid", "Mittel (Pfeiltaste)", "f", p.nudge, (0.001, 1000, 3, 0.1, " mm")),
                  ("large", "Groß (Shift + Pfeiltaste)", "f", p.nudge_large, (0.001, 1000, 3, 1, " mm"))]
        dlg = ParamDialog("Schrittweiten der Pfeiltasten", fields, self.project,
                          "Wie weit die Pfeiltasten die ausgewählten Teile verschieben. Wird gespeichert.", self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        p.nudge_small, p.nudge, p.nudge_large = v["small"], v["mid"], v["large"]
        self.canvas.nudge_steps = (p.nudge_small, p.nudge, p.nudge_large)
        storage.save_machine(p)
        self.statusBar().showMessage(f"Pfeiltasten: Strg {p.nudge_small:g} mm, ohne {p.nudge:g} mm, Shift "
                                     f"{p.nudge_large:g} mm", 5000)

    def _toggle_node_snap(self):
        self.canvas.node_snap = self.a_node_snap.isChecked()

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
        self._run_test(build_project(dlg.test()), "Materialtest", dlg.action)

    def focus_test(self):
        dlg = FocusTestDialog(self.profile, self)
        if dlg.exec() == FocusTestDialog.DialogCode.Accepted and dlg.action:
            self._run_test(dlg.project(), "Fokustest", dlg.action)

    # ------------------------------------------------------------ Generatoren und Muster
    def _target_layer(self, choice, mode_new: str, name_new: str):
        if choice == ParamDialog.NEW_FILL:
            return self.project.add_layer(name_new, mode="fill")
        if choice == ParamDialog.NEW_CUT:
            return self.project.add_layer(name_new, mode="cut")
        return self.project.layer(choice) or self.project.add_layer(name_new, mode=mode_new)

    def _add_generated(self, name: str, world, select=True):
        """world: Liste (Polylinie, Ebenen-ID) in Weltkoordinaten -> neues Objekt."""
        n = self.project._item_from_world(name, world)
        self.project.items.append(n)
        self.canvas.add_item(n)
        self.layers.set_project(self.project)
        if select:
            self.canvas.select_only([n.id])
        self._invalidate()
        return n

    def _place_at(self, polys, x=10.0, y=10.0):
        bb = g.bbox(polys)
        m = g.translate(x - bb[0], y - bb[1])
        return [p.transformed(m) for p in polys]

    def _region_selection(self, title):
        items = self._vector_selection()
        rings = [p for it in items for p in it.world_paths() if p.closed and len(p.pts) >= 3]
        if not rings:
            QMessageBox.information(self, title, "Bitte zuerst eine geschlossene Kontur auswählen "
                                                 "(z. B. ein Rechteck oder die Umrisslinie eines Teils).")
            return None, None
        return items, rings

    def pattern_tool(self, kind: str):
        from ..core import patterns as pt
        titles = {"hinge": "Biegescharnier", "holes": "Lochmuster", "engrave": "Gravurmuster"}
        title = titles[kind]
        items, region = self._region_selection(title)
        if not region:
            return
        cut_pref = next(iter(items[0].layer_ids()), ParamDialog.NEW_CUT)
        if kind == "hinge":
            fields = [("style", "Art", "c", "straight", pt.HINGE_STYLES),
                      ("slit", "Schlitzlänge", "f", 20.0, (2, 500, 1, 1, " mm")),
                      ("bridge", "Steg zwischen Schlitzen", "f", 3.0, (0.5, 50, 1, 0.5, " mm")),
                      ("spacing", "Abstand der Reihen", "f", 1.5, (0.3, 20, 2, 0.1, " mm")),
                      ("margin", "Abstand zum Rand", "f", 3.0, (0, 100, 1, 0.5, " mm")),
                      ("relief", "Stress Relief an den Schlitzenden (Ø, 0 = aus)", "f", 0.0, (0, 10, 2, 0.1, " mm")),
                      ("angle", "Richtung der Schlitze", "f", 0.0, (-180, 180, 0, 15, " °")),
                      ("layer", "Ebene", "layer", None, (("cut",), cut_pref))]
            info = ("Die Fläche biegt sich um eine Achse parallel zu den Schlitzen. Kleinerer Reihenabstand "
                    "und längere Schlitze = weicher. Für 3-mm-Sperrholz sind 1,5 mm Abstand und 20 mm Schlitze "
                    "ein guter Anfang. Die Fläche selbst wird nicht verändert.")
        elif kind == "holes":
            fields = [("shape", "Form", "c", "circle", pt.HOLE_SHAPES),
                      ("size", "Größe (Ø / Schlüsselweite)", "f", 5.0, (0.5, 200, 1, 0.5, " mm")),
                      ("slot_len", "Länge (nur Langloch)", "f", 15.0, (1, 500, 1, 1, " mm")),
                      ("web", "Steg zwischen Löchern", "f", 2.0, (0.2, 100, 1, 0.5, " mm")),
                      ("layout", "Anordnung", "c", "offset", pt.HOLE_LAYOUTS),
                      ("margin", "Abstand zum Rand", "f", 3.0, (0, 100, 1, 0.5, " mm")),
                      ("angle", "Drehung", "f", 0.0, (-180, 180, 0, 15, " °")),
                      ("layer", "Ebene", "layer", None, (("cut",), cut_pref))]
            info = "Es werden nur ganze Löcher gesetzt, die mit dem Randabstand vollständig in der Fläche liegen."
        else:
            fields = [("pattern", "Muster", "c", "houndstooth", pt.ENGRAVE_PATTERNS),
                      ("size", "Größe (Rapport)", "f", 8.0, (1, 200, 1, 0.5, " mm")),
                      ("gap", "Fuge / Abstand", "f", 1.0, (0, 50, 1, 0.2, " mm")),
                      ("angle", "Drehung", "f", 0.0, (-180, 180, 0, 15, " °")),
                      ("layer", "Ebene", "layer", None, (("fill",), ParamDialog.NEW_FILL))]
            info = ("Das Muster wird als Fläche auf eine Füll-Ebene gelegt und dort graviert. "
                    "Die Fuge gilt für Rauten, Punkte, Waben, Ziegel und Wellen.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        try:
            if kind == "hinge":
                res = pt.living_hinge(region, v["slit"], v["bridge"], v["spacing"], v["margin"], v["angle"], v["style"],
                                      v["relief"])
            elif kind == "holes":
                res = pt.hole_pattern(region, v["shape"], v["size"], v["web"], v["layout"], v["margin"],
                                      v["slot_len"], v["angle"])
            else:
                res = pt.engrave_pattern(region, v["pattern"], v["size"], v["gap"], v["angle"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        if not res:
            QMessageBox.information(self, title, "Mit diesen Einstellungen passt nichts in die Fläche.")
            return
        self._checkpoint(title)
        layer = self._target_layer(v["layer"], "fill" if kind == "engrave" else "cut", title)
        self._add_generated(title, [(p, layer.id) for p in res])
        self.statusBar().showMessage(f"{title}: {len(res)} Konturen erzeugt.", 6000)

    def finger_joint_tool(self):
        from ..core.joints import finger_holes, finger_joint, sides
        title = "Fingerzinken an Kante"
        items = self._vector_selection()
        if len(items) != 2:
            QMessageBox.information(self, title, "Bitte genau zwei Teile auswählen, deren Kanten sich berühren.")
            return
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        rings = [[p.pts for p, _ in w if p.closed] for w in worlds]
        pos = sides(rings[0], rings[1], tol=2.0)
        if pos is None:
            QMessageBox.information(self, title, "Keine gemeinsame Kante gefunden. Die beiden Teile so hinlegen, dass "
                                                 "sich die zu verbindenden Kanten berühren (z. B. mit Anordnen → "
                                                 "Ausrichten), dann erneut versuchen.")
            return
        where = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}
        who = {str(k): f"{it.name} ({i18n_tr(where[pos[k]])})" for k, it in enumerate(items)}
        fields = [("kind", "Art", "c", "tabs",
                   {"tabs": "Ineinandergreifend: ein Teil mit Zinken, das andere mit Aussparungen",
                    "corner": "Eckverbindung: beide Teile mit versetzten Aussparungen, Außenmaße bleiben",
                    "surface": "Kante an Fläche: Zinken am einen Teil, passende Löcher in der Fläche des anderen"}),
                  ("fingers_on", "Zinken an", "c", "0", who),
                  ("thickness", "Materialstärke", "f", 3.0, (0.5, 50, 2, 0.1, " mm")),
                  ("finger", "Zinkenbreite", "f", 10.0, (1, 200, 1, 1, " mm")),
                  ("dogbone", "Dogbone-Durchmesser (0 = aus)", "f", 1.0, (0, 20, 2, 0.1, " mm")),
                  ("inset", "Kante an Fläche: Abstand der Löcher von der Kante", "f", 3.0, (0.5, 2000, 1, 1, " mm")),
                  ("length", "Kante an Fläche: Zinkenlänge (0 = Materialstärke)", "f", 0.0, (0, 50, 2, 0.5, " mm")),
                  ("clearance", "Kante an Fläche: Spiel in den Löchern", "f", 0.1, (0, 2, 2, 0.05, " mm")),
                  ("tol", "Toleranz für „berühren“", "f", 0.5, (0.01, 5, 2, 0.1, " mm")),
                  ("all", "Alle gemeinsamen Kanten (sonst nur die längste)", "b", False, None)]
        info = ("Ineinandergreifend: Das gewählte Teil bekommt Zinken, die um die Materialstärke über die Kante "
                "hinausragen, das andere genau passende Aussparungen – so wie die Teile liegen, füllen die Zinken "
                "die Aussparungen lückenlos. Eckverbindung: wie beim Box-Generator, beide Teile behalten ihr "
                "Außenmaß. Kante an Fläche (T-Verbindung, z. B. Zwischenwand oder Regalboden): das gewählte Teil "
                "bekommt Zinken, das andere keine Zinken, sondern passende Löcher in der Fläche, im eingestellten "
                "Abstand von seiner Kante; das Teil mit den Zinken wird danach etwas weggeschoben. Kurven der Teile "
                "werden in feine Linienzüge umgewandelt.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        first = 1 if str(v["fingers_on"]) == "1" else 0
        order = (first, 1 - first)
        a, b = items[order[0]], items[order[1]]
        wa, wb = worlds[order[0]], worlds[order[1]]
        ia = [i for i, (p, _) in enumerate(wa) if p.closed]
        ib = [i for i, (p, _) in enumerate(wb) if p.closed]
        if v["kind"] == "surface":
            try:
                ra, rb, n, shift = finger_holes([wa[i][0].pts for i in ia], [wb[i][0].pts for i in ib],
                                                v["thickness"], v["finger"], v["inset"], v["clearance"], 3.0,
                                                v["dogbone"], v["tol"], v["length"])
            except ValueError as e:
                QMessageBox.information(self, title, str(e))
                return
            self._checkpoint(title)
            moved = [(p.transformed(g.translate(float(shift[0]), float(shift[1]))), lid) for p, lid in wa]
            for k, i in enumerate(ia):
                moved[i] = (g.Polyline(ra[k], True), wa[i][1])
            lid_b = wb[ib[0]][1] if ib else next(iter(b.layer_ids()))
            new_b = list(wb) + [(g.Polyline(r, True), lid_b) for r in rb[len(ib):]]
            self.canvas.select_only([self._replace_item(a, moved).id, self._replace_item(b, new_b).id])
            self.statusBar().showMessage(f"Fingerzinken Kante an Fläche: {n} Löcher in {b.name}; {a.name} wurde "
                                         f"um {float(np.hypot(*shift)):.1f} mm weggeschoben.", 8000)
            return
        try:
            ra, rb, n = finger_joint([wa[i][0].pts for i in ia], [wb[i][0].pts for i in ib], v["thickness"],
                                     v["finger"], v["kind"], v["dogbone"], v["tol"], v["all"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        new_ids = []
        for it, world, idx, rr in ((a, wa, ia, ra), (b, wb, ib, rb)):
            new_world = list(world)
            for k, i in enumerate(idx):
                if len(rr[k]) != len(world[i][0].pts) or not np.allclose(rr[k], world[i][0].pts):
                    new_world[i] = (g.Polyline(rr[k], True), world[i][1])
            new_ids.append(self._replace_item(it, new_world).id)
        self.canvas.select_only(new_ids)
        self.statusBar().showMessage(f"Fingerzinken an {n} Kante(n) erzeugt.", 6000)

    def puzzle_joint_tool(self):
        from ..core.joints import puzzle_height, puzzle_joint, sides
        title = "Puzzle-Verbindung an Kante"
        items = self._vector_selection()
        if len(items) != 2:
            QMessageBox.information(self, title, "Bitte genau zwei Teile auswählen, deren Kanten sich berühren.")
            return
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        rings = [[p.pts for p, _ in w if p.closed] for w in worlds]
        pos = sides(rings[0], rings[1], tol=2.0)
        if pos is None:
            QMessageBox.information(self, title, "Keine gemeinsame Kante gefunden. Die beiden Teile Kante an Kante "
                                                 "legen (z. B. mit Anordnen → Kanten aneinanderlegen), dann erneut "
                                                 "versuchen.")
            return
        where = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}
        who = {str(k): f"{it.name} ({i18n_tr(where[pos[k]])})" for k, it in enumerate(items)}
        fields = [("knobs_on", "Nasen an", "c", "0", who),
                  ("size", "Nasengröße (Kopfdurchmesser)", "f", 10.0, (2, 200, 1, 1, " mm")),
                  ("count", "Anzahl je Kante (0 = automatisch)", "i", 0, (0, 100)),
                  ("alternate", "Nasen abwechselnd an beiden Teilen", "b", False, None),
                  ("clearance", "Spiel", "f", 0.0, (-1, 2, 2, 0.05, " mm")),
                  ("tol", "Toleranz für „berühren“", "f", 0.5, (0.01, 5, 2, 0.1, " mm")),
                  ("all", "Alle gemeinsamen Kanten (sonst nur die längste)", "b", False, None)]
        info = ("Das gewählte Teil bekommt runde Nasen mit schmalerem Hals, das andere genau passende Aussparungen – "
                "die Teile halten in der Ebene zusammen wie Puzzleteile. Automatisch etwa alle 2,5 Kopfdurchmesser "
                "eine Nase. Spiel: positiv = lockerer (z. B. 0,1 mm), negativ = strammer; der Laser nimmt selbst "
                "schon etwas Material weg. Kurven der Teile werden in feine Linienzüge umgewandelt.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        first = 1 if str(v["knobs_on"]) == "1" else 0
        a, b = items[first], items[1 - first]
        wa, wb = worlds[first], worlds[1 - first]
        ia = [i for i, (p, _) in enumerate(wa) if p.closed]
        ib = [i for i, (p, _) in enumerate(wb) if p.closed]
        try:
            ra, rb, n = puzzle_joint([wa[i][0].pts for i in ia], [wb[i][0].pts for i in ib], v["size"], v["count"],
                                     v["clearance"], v["alternate"], v["tol"], v["all"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        new_ids = []
        for it, world, idx, rr in ((a, wa, ia, ra), (b, wb, ib, rb)):
            new_world = list(world)
            for k, i in enumerate(idx):
                if len(rr[k]) != len(world[i][0].pts) or not np.allclose(rr[k], world[i][0].pts):
                    new_world[i] = (g.Polyline(rr[k], True), world[i][1])
            new_ids.append(self._replace_item(it, new_world).id)
        self.canvas.select_only(new_ids)
        self.statusBar().showMessage(f"Puzzle-Verbindung an {n} Kante(n) erzeugt (Nasen ragen "
                                     f"{puzzle_height(v['size']):.1f} mm über die Kante).", 6000)

    def snap_lock_tool(self):
        from ..core.joints import SnapSpec, sides, snap_height, snap_lock
        title = "Snap-Lock an Kante"
        items = self._vector_selection()
        if len(items) != 2:
            QMessageBox.information(self, title, "Bitte genau zwei Teile auswählen, deren Kanten sich berühren.")
            return
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        rings = [[p.pts for p, _ in w if p.closed] for w in worlds]
        pos = sides(rings[0], rings[1], tol=2.0)
        if pos is None:
            QMessageBox.information(self, title, "Keine gemeinsame Kante gefunden. Die beiden Teile Kante an Kante "
                                                 "legen (z. B. mit Anordnen → Kanten aneinanderlegen), dann erneut "
                                                 "versuchen.")
            return
        where = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}
        who = {str(k): f"{it.name} ({i18n_tr(where[pos[k]])})" for k, it in enumerate(items)}
        d = SnapSpec()
        fields = [("tabs_on", "Laschen an", "c", "0", who),
                  ("thickness", "Materialstärke", "f", d.thickness, (0.5, 20, 2, 0.1, " mm")),
                  ("width", "Laschenbreite", "f", d.width, (3, 100, 1, 0.5, " mm")),
                  ("count", "Anzahl je Kante (0 = automatisch)", "i", 0, (0, 100)),
                  ("barb", "Haken (seitlicher Überstand)", "f", d.barb, (0.1, 3, 2, 0.05, " mm")),
                  ("slit", "Federschlitz", "f", d.slit, (0.3, 10, 2, 0.1, " mm")),
                  ("clearance", "Spiel", "f", d.clearance, (0, 1, 2, 0.05, " mm")),
                  ("inset", "Abstand der Schlitze zur Kante", "f", d.inset, (0.5, 100, 1, 0.5, " mm")),
                  ("tol", "Toleranz für „berühren“", "f", 0.5, (0.01, 5, 2, 0.1, " mm")),
                  ("all", "Alle gemeinsamen Kanten (sonst nur die längste)", "b", False, None)]
        info = ("Das gewählte Teil bekommt Laschen aus zwei federnden Zungen mit Haken, das andere passende "
                "rechteckige Schlitze. Zusammengesteckt stehen die Teile im rechten Winkel; die Zungen werden "
                "beim Durchstecken zusammengedrückt und die Haken rasten auf der Rückseite ein. Danach wird das "
                "Teil mit den Laschen etwas weggeschoben, damit sich nichts überlappt. Lösen: Zungen "
                "zusammendrücken.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        spec = SnapSpec(v["thickness"], v["width"], v["count"], v["barb"], v["slit"], v["clearance"], v["inset"])
        first = 1 if str(v["tabs_on"]) == "1" else 0
        a, b = items[first], items[1 - first]
        wa, wb = worlds[first], worlds[1 - first]
        ia = [i for i, (p, _) in enumerate(wa) if p.closed]
        ib = [i for i, (p, _) in enumerate(wb) if p.closed]
        try:
            ra, rb, n, shift = snap_lock([wa[i][0].pts for i in ia], [wb[i][0].pts for i in ib], spec,
                                         v["tol"], v["all"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        moved = [(p.transformed(g.translate(float(shift[0]), float(shift[1]))), lid) for p, lid in wa]
        for k, i in enumerate(ia):
            moved[i] = (g.Polyline(ra[k], True), wa[i][1])
        new_b = list(wb)
        for k, i in enumerate(ib):
            if len(rb[k]) != len(wb[i][0].pts) or not np.allclose(rb[k], wb[i][0].pts):
                new_b[i] = (g.Polyline(rb[k], True), wb[i][1])
        lid_b = wb[ib[0]][1] if ib else next(iter(b.layer_ids()))
        new_b += [(g.Polyline(r, True), lid_b) for r in rb[len(ib):]]           # neue Schlitze
        new_ids = [self._replace_item(a, moved).id, self._replace_item(b, new_b).id]
        self.canvas.select_only(new_ids)
        self.statusBar().showMessage(f"Snap-Lock an {n} Kante(n) erzeugt; {a.name} wurde um "
                                     f"{snap_height(spec) + spec.gap:.1f} mm weggeschoben.", 8000)

    def hammer_joint_tool(self):
        from ..core.joints import HammerSpec, hammer_joint, sides
        title = "Hammerverbindung an Kante"
        items = self._vector_selection()
        if len(items) != 2:
            QMessageBox.information(self, title, "Bitte genau zwei Teile auswählen, deren Kanten sich berühren.")
            return
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        rings = [[p.pts for p, _ in w if p.closed] for w in worlds]
        pos = sides(rings[0], rings[1], tol=2.0)
        if pos is None:
            QMessageBox.information(self, title, "Keine gemeinsame Kante gefunden. Die beiden Teile Kante an Kante "
                                                 "legen (z. B. mit Anordnen → Kanten aneinanderlegen), dann erneut "
                                                 "versuchen.")
            return
        where = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}
        who = {str(k): f"{it.name} ({i18n_tr(where[pos[k]])})" for k, it in enumerate(items)}
        d = HammerSpec()
        fields = [("tenons_on", "Zapfen an", "c", "0", who),
                  ("thickness", "Materialstärke", "f", d.thickness, (2, 30, 2, 0.5, " mm")),
                  ("width", "Zapfenbreite", "f", d.width, (8, 100, 1, 1, " mm")),
                  ("count", "Anzahl je Kante (0 = automatisch)", "i", 0, (0, 100)),
                  ("protrude", "Überstand des Zapfens", "f", d.protrude, (0, 3, 2, 0.05, " mm")),
                  ("wedge_out", "Keilüberstand vor dem Einschlagen", "f", d.wedge_out, (2, 40, 1, 0.5, " mm")),
                  ("spread", "Spreizung durch den Keil", "f", d.spread, (0.1, 4, 2, 0.05, " mm")),
                  ("bridge", "Länge der Haltestege", "f", d.bridge, (0.2, 3, 2, 0.1, " mm")),
                  ("depth", "Schlitztiefe in das Teil", "f", d.depth, (5, 100, 1, 0.5, " mm")),
                  ("relief", "Entlastungsschlitze (0 = keine)", "f", d.relief, (0, 3, 2, 0.05, " mm")),
                  ("press", "Übermaß des Zapfens (Breite)", "f", d.press, (-0.5, 1, 3, 0.05, " mm")),
                  ("clearance", "Spiel im Zapfenloch", "f", d.clearance, (0, 1, 3, 0.025, " mm")),
                  ("inset", "Abstand der Zapfenlöcher zur Kante", "f", d.inset, (0.5, 100, 1, 0.5, " mm")),
                  ("tol", "Toleranz für „berühren“", "f", 0.5, (0.01, 5, 2, 0.1, " mm")),
                  ("all", "Alle gemeinsamen Kanten (sonst nur die längste)", "b", False, None)]
        info = ("Nach der Hammerverbindung von Lasersheets: Das gewählte Teil bekommt Zapfen mit einem sich "
                "verjüngenden Schlitz, das andere passende Zapfenlöcher. Der Keil wird mit zwei kurzen Stegen direkt "
                "im Schlitz mitgelasert und steht über den Zapfen hinaus. Zapfen durch das Loch stecken und den Keil "
                "mit dem Hammer bündig einschlagen – die Stege brechen, der Keil spreizt die Zapfenhälften im Loch. "
                "Ohne Schrauben und Leim, nicht wieder lösbar. Am besten ab 6 mm Sperrholz oder Massivholz; vorher an "
                "einem Probestück testen.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        spec = HammerSpec(thickness=v["thickness"], width=v["width"], count=v["count"], protrude=v["protrude"],
                          wedge_out=v["wedge_out"], spread=v["spread"], bridge=v["bridge"], depth=v["depth"],
                          relief=v["relief"], press=v["press"], clearance=v["clearance"], inset=v["inset"])
        first = 1 if str(v["tenons_on"]) == "1" else 0
        a, b = items[first], items[1 - first]
        wa, wb = worlds[first], worlds[1 - first]
        ia = [i for i, (p, _) in enumerate(wa) if p.closed]
        ib = [i for i, (p, _) in enumerate(wb) if p.closed]
        try:
            ra, rb, n, shift = hammer_joint([wa[i][0].pts for i in ia], [wb[i][0].pts for i in ib], spec,
                                            v["tol"], v["all"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        moved = [(p.transformed(g.translate(float(shift[0]), float(shift[1]))), lid) for p, lid in wa]
        for k, i in enumerate(ia):
            moved[i] = (g.Polyline(ra[k], True), wa[i][1])
        lid_a = wa[ia[0]][1] if ia else next(iter(a.layer_ids()))
        moved += [(g.Polyline(r, True), lid_a) for r in ra[len(ia):]]          # Schlitze unter den Keilen
        new_b = list(wb)
        lid_b = wb[ib[0]][1] if ib else next(iter(b.layer_ids()))
        new_b += [(g.Polyline(r, True), lid_b) for r in rb[len(ib):]]           # Zapfenlöcher
        new_ids = [self._replace_item(a, moved).id, self._replace_item(b, new_b).id]
        self.canvas.select_only(new_ids)
        self.statusBar().showMessage(f"Hammerverbindung an {n} Kante(n) erzeugt; {a.name} wurde um "
                                     f"{abs(float(np.hypot(*shift))):.1f} mm weggeschoben.", 8000)

    def spring_latch_tool(self):
        from ..core.joints import LatchSpec, sides, spring_latch
        title = "Federverschluss an Kante"
        items = self._vector_selection()
        if len(items) != 2:
            QMessageBox.information(self, title, "Bitte genau zwei Teile auswählen, deren Kanten sich berühren – z. B. "
                                                 "die Vorderwand der Box und die Vorderwand des Deckels.")
            return
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        rings = [[p.pts for p, _ in w if p.closed] for w in worlds]
        pos = sides(rings[0], rings[1], tol=2.0)
        if pos is None:
            QMessageBox.information(self, title, "Keine gemeinsame Kante gefunden. Die beiden Teile so hinlegen, wie sie "
                                                 "bei geschlossenem Deckel aneinanderstoßen (z. B. mit Anordnen → "
                                                 "Kanten aneinanderlegen), dann erneut versuchen.")
            return
        where = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}
        who = {str(k): f"{it.name} ({i18n_tr(where[pos[k]])})" for k, it in enumerate(items)}
        d = LatchSpec()
        fields = [("spring_on", "Feder und Zunge an", "c", "0", who),
                  ("count", "Anzahl Verschlüsse (1 = mittig)", "i", d.count, (1, 20)),
                  ("inset", "Abstand der äußeren Verschlüsse vom Ende der Kante", "f", d.inset, (0, 500, 1, 1, " mm")),
                  ("legs", "Schenkel der Feder (ungerade, 3 = S-Form)", "i", d.legs, (1, 41)),
                  ("leg_length", "Schenkellänge", "f", d.leg_length, (3, 200, 1, 1, " mm")),
                  ("bar", "Stegbreite der Feder", "f", d.bar, (0.5, 10, 2, 0.1, " mm")),
                  ("gap", "Schlitz zwischen den Schenkeln", "f", d.gap, (0.3, 10, 2, 0.1, " mm")),
                  ("width", "Zungenbreite", "f", d.width, (2, 50, 1, 0.5, " mm")),
                  ("catch", "Hakenüberstand (Federweg)", "f", d.catch, (0.3, 10, 2, 0.1, " mm")),
                  ("hold", "Schulter über dem Haken", "f", d.hold, (1, 30, 1, 0.5, " mm")),
                  ("button", "Länge der Drucktaste", "f", d.button, (2, 50, 1, 0.5, " mm")),
                  ("grip", "Platz zum Drücken neben der Taste", "f", d.grip, (1, 50, 1, 0.5, " mm")),
                  ("frame", "Steg zwischen Fenster und Kante", "f", d.frame, (1, 30, 1, 0.5, " mm")),
                  ("clearance", "Spiel", "f", d.clearance, (0, 1, 2, 0.05, " mm")),
                  ("tol", "Toleranz für „berühren“", "f", 0.5, (0.01, 5, 2, 0.1, " mm"))]
        info = ("Für zwei Teile, die bei geschlossenem Deckel in einer Ebene aneinanderstoßen – z. B. die Vorderwand "
                "der Box und die Vorderwand des Deckels (gegenüber vom Scharnier). Das gewählte Teil bekommt ein "
                "Fenster mit einer Mäanderfeder, an der eine Zunge mit Haken hängt; sie ragt über die Kante in eine "
                "Aussparung des anderen Teils. Beim Schließen drückt die Schräge des Hakens die Zunge zur Seite, die "
                "Feder schnappt zurück und der Haken hält hinter der Schulter. Zum Öffnen die Drucktaste im Fenster "
                "zur Seite schieben. Mehr Schenkel oder längere Schenkel = weichere Feder mit mehr Federweg.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        spec = LatchSpec(width=v["width"], catch=v["catch"], hold=v["hold"], legs=int(v["legs"]),
                         leg_length=v["leg_length"], bar=v["bar"], gap=v["gap"], button=v["button"], grip=v["grip"],
                         frame=v["frame"], clearance=v["clearance"], count=int(v["count"]), inset=v["inset"])
        first = 1 if str(v["spring_on"]) == "1" else 0
        a, b = items[first], items[1 - first]
        wa, wb = worlds[first], worlds[1 - first]
        ia = [i for i, (p, _) in enumerate(wa) if p.closed]
        ib = [i for i, (p, _) in enumerate(wb) if p.closed]
        try:
            ra, rb, n, dims = spring_latch([wa[i][0].pts for i in ia], [wb[i][0].pts for i in ib], spec, v["tol"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        new_ids = []
        for it, world, idx, rr in ((a, wa, ia, ra), (b, wb, ib, rb)):
            new_world = list(world)
            for k, i in enumerate(idx):
                if len(rr[k]) != len(world[i][0].pts) or not np.allclose(rr[k], world[i][0].pts):
                    new_world[i] = (g.Polyline(rr[k], True), world[i][1])
            new_ids.append(self._replace_item(it, new_world).id)
        self.canvas.select_only(new_ids)
        self.statusBar().showMessage(
            f"Federverschluss: {n} × an {a.name}. Fenster {dims['xr'] - dims['xl']:.1f} × {dims['top']:.1f} mm, die "
            f"Zunge ragt {dims['depth']:.1f} mm in {b.name}.", 8000)

    def slider_latch_tool(self):
        from ..core.joints import SliderLatchSpec, sides, slider_latch
        title = "Federriegel mit Haken"
        sel = self._vector_selection()
        if len(sel) not in (2, 3):
            QMessageBox.information(self, title, "Bitte den Deckel und die Vorderwand der Box auswählen, Kante an Kante "
                                                 "gelegt – optional dazu die innere Lage (zweiter Boden, so groß wie "
                                                 "die Wand), zwischen der und der Wand der Riegel liegt.")
            return
        inner_item = None
        items, pos = sel[:2], None
        for i, j in ((0, 1), (0, 2), (1, 2)) if len(sel) == 3 else ((0, 1),):
            ri = [p.pts for p in sel[i].world_paths() if p.closed]
            rj = [p.pts for p in sel[j].world_paths() if p.closed]
            pos = sides(ri, rj, tol=2.0)
            if pos is not None:
                items = [sel[i], sel[j]]
                inner_item = next((it for it in sel if it not in items), None)
                break
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        if pos is None:
            QMessageBox.information(self, title, "Keine gemeinsame Kante gefunden. Deckel und Vorderwand so hinlegen, "
                                                 "dass die Vorderkante des Deckels an der Oberkante der Wand liegt "
                                                 "(z. B. mit Anordnen → Kanten aneinanderlegen), dann erneut versuchen.")
            return
        where = {"left": "links", "right": "rechts", "top": "oben", "bottom": "unten"}
        who = {str(k): f"{it.name} ({i18n_tr(where[pos[k]])})" for k, it in enumerate(items)}
        d = SliderLatchSpec()
        fields = [("lid_is", "Deckel ist", "c", "0", who),
                  ("thickness", "Materialstärke (Riegel, Halter)", "f", d.thickness, (1, 20, 2, 0.5, " mm")),
                  ("lid", "Deckelstärke", "f", d.lid, (1, 30, 2, 0.5, " mm")),
                  ("wall", "Wandstärke (Abstand des Riegels von der Deckelkante)", "f", d.wall,
                   (0, 50, 2, 0.5, " mm")),
                  ("length", "Länge des Riegels", "f", d.length, (30, 1000, 1, 5, " mm")),
                  ("hooks", "Anzahl Haken", "i", d.hooks, (1, 10)),
                  ("hook_width", "Breite des Hakenschafts", "f", d.hook_width, (2, 30, 1, 0.5, " mm")),
                  ("catch", "Hakenüberstand (Federweg)", "f", d.catch, (0.5, 10, 2, 0.1, " mm")),
                  ("head", "Hakenkopf über dem Deckel (mindestens)", "f", d.head, (1, 20, 1, 0.5, " mm")),
                  ("ramp_angle", "Neigung der Schräge an der Nase", "f", d.ramp_angle, (20, 80, 0, 5, "°")),
                  ("legs", "Schenkel der Feder (ungerade)", "i", d.legs, (1, 61)),
                  ("bar", "Stegbreite der Feder", "f", d.bar, (0.5, 10, 2, 0.1, " mm")),
                  ("gap", "Schlitz zwischen den Schenkeln", "f", d.gap, (0.3, 10, 2, 0.1, " mm")),
                  ("key", "Höhe der Halter", "f", d.key, (3, 40, 1, 0.5, " mm")),
                  ("inner_copy", "Innere Lage als Kopie der Wand erzeugen" if inner_item is None else
                   "Drittes ausgewähltes Teil als innere Lage verwenden", "b", inner_item is not None, None),
                  ("inner", "Stärke der inneren Lage", "f", d.wall, (0.5, 50, 2, 0.5, " mm")),
                  ("margin", "Rand des Riegels", "f", d.margin, (1.5, 20, 1, 0.5, " mm")),
                  ("clearance", "Spiel", "f", d.clearance, (0, 1, 2, 0.05, " mm")),
                  ("tol", "Toleranz für „berühren“", "f", 0.5, (0.01, 5, 2, 0.1, " mm"))]
        info = ("Für einen Deckel, der oben auf der Vorderwand aufliegt. Innen hinter der Vorderwand sitzt ein Riegel, "
                "dessen Haken durch Schlitze im Deckel greifen. Zwei T-förmige Halter stecken durch den Riegel in "
                "Schlitzen der Wand; einer sitzt in der Öse am Ende einer langen Mäanderfeder, der andere in einem "
                "Langloch. Beim Schließen drücken die Schlitzkanten die Schrägen der Haken zur Seite, die Feder schiebt "
                "den Riegel zurück und die Haken liegen auf dem Deckel. Zum Öffnen die Hakenköpfe zur Seite schieben. "
                "Die Schlitze kommen in Deckel und Wand, Riegel und Halter entstehen neben den Teilen.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        spec = SliderLatchSpec(thickness=v["thickness"], lid=v["lid"], wall=v["wall"], length=v["length"],
                               hooks=int(v["hooks"]), hook_width=v["hook_width"], catch=v["catch"], head=v["head"],
                               ramp_angle=v["ramp_angle"],
                               legs=int(v["legs"]), bar=v["bar"], gap=v["gap"], key=v["key"], margin=v["margin"],
                               clearance=v["clearance"], inner=v["inner"] if v["inner_copy"] else 0.0)
        first = 1 if str(v["lid_is"]) == "1" else 0
        lid, wall = items[first], items[1 - first]
        wl, ww = worlds[first], worlds[1 - first]
        try:
            lid_slots, wall_slots, plate, keys, dims = slider_latch([p.pts for p, _ in wl if p.closed],
                                                                    [p.pts for p, _ in ww if p.closed], spec, v["tol"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        lid_l = next((l for p, l in wl if p.closed), next(iter(lid.layer_ids())))
        lid_w = next((l for p, l in ww if p.closed), next(iter(wall.layer_ids())))
        bb = g.bbox_of([p for p, _ in wl + ww])
        ids = [self._replace_item(lid, list(wl) + [(g.Polyline(r, True), lid_l) for r in lid_slots]).id,
               self._replace_item(wall, list(ww) + [(g.Polyline(r, True), lid_w) for r in wall_slots]).id]
        pts = np.vstack(plate)
        shift = np.array([bb[2] + 10.0 - pts[:, 0].min(), bb[1] - pts[:, 1].min()])
        ids.append(self._add_generated(f"Riegel {wall.name}", [(g.Polyline(r + shift, True), lid_w) for r in plate],
                                       False).id)
        kx, ky = bb[2] + 10.0, bb[1] - 10.0 - spec.key - 4
        key_world = []
        for r in keys:
            key_world.append((g.Polyline(r + [kx, ky], True), lid_w))
            kx += float(np.ptp(r[:, 0])) + 4
        ids.append(self._add_generated(f"Halter ({len(keys)})", key_world, False).id)
        if spec.inner > 0:
            bw = g.bbox_of([p for p, _ in ww])
            if inner_item is not None:
                wi = [(p, vp.layer_id) for p, vp in zip(inner_item.world_paths(), inner_item.paths)]
                bi = g.bbox_of([p for p, _ in wi])
                if abs((bi[2] - bi[0]) - (bw[2] - bw[0])) > 0.5 or abs((bi[3] - bi[1]) - (bw[3] - bw[1])) > 0.5:
                    QMessageBox.information(self, title, "Die innere Lage ist nicht so groß wie die Vorderwand – die "
                                                         "Schlitze für die Köpfe der Halter wurden trotzdem an "
                                                         "derselben Stelle (von links oben gemessen) geschnitten.")
                off = np.array([bi[0] - bw[0], bi[3] - bw[3]])
                lid_i = next((l for p, l in wi if p.closed), lid_w)
                ids.append(self._replace_item(inner_item, wi + [(g.Polyline(r + off, True), lid_i)
                                                                for r in dims["head_slots"]]).id)
            else:                                     # Kopie der Wand ohne deren Halterschlitze, unter alles gelegt
                bb_all = g.bbox_of([p for p, _ in wl + ww])
                off = np.array([0.0, bb_all[1] - 10.0 - bw[3]])
                copy = [(g.Polyline(p.pts + off, p.closed), l) for p, l in ww]
                copy += [(g.Polyline(r + off, True), lid_w) for r in dims["head_slots"]]
                ids.append(self._add_generated(f"{wall.name} innen", copy, False).id)
        self.canvas.select_only(ids)
        self.statusBar().showMessage(
            f"Federriegel: {spec.hooks} Haken durch {lid.name}, Halter in {wall.name}. Riegel {spec.length:g} × "
            f"{dims['height']:.1f} mm, Federweg {dims['travel']:.2f} mm.", 10000)

    def knuckle_hinge_tool(self):
        from ..core.joints import KnuckleSpec, knuckle_hinge, knuckle_hole, knuckle_square, sides
        title = "Knöchelscharnier an Kante"
        items = self._vector_selection()
        if len(items) != 2:
            QMessageBox.information(self, title, "Bitte genau zwei Teile auswählen, deren Kanten sich berühren – z. B. "
                                                 "die Rückwand der Box und die Rückwand des Deckels.")
            return
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        rings = [[p.pts for p, _ in w if p.closed] for w in worlds]
        if sides(rings[0], rings[1], tol=2.0) is None:
            QMessageBox.information(self, title, "Keine gemeinsame Kante gefunden. Die beiden Teile so hinlegen, wie sie "
                                                 "bei geschlossener Box aneinanderstoßen (z. B. mit Anordnen → Kanten "
                                                 "aneinanderlegen), dann erneut versuchen.")
            return
        d = KnuckleSpec()
        fields = [("thickness", "Materialstärke", "f", d.thickness, (1, 20, 2, 0.5, " mm")),
                  ("hinges", "Anzahl Scharniere (1 = mittig)", "i", 2, (1, 50)),
                  ("inset", "Abstand der äußeren Scharniere vom Ende der Kante", "f", d.inset, (0, 500, 1, 1, " mm")),
                  ("knuckles", "Knöchel je Scharnier", "i", d.knuckles, (2, 99)),
                  ("length", "Knöchellänge", "f", d.length, (8, 200, 1, 1, " mm")),
                  ("end_height", "Höhe am abgeschrägten Ende", "f", d.end_height, (0.5, 50, 1, 0.5, " mm")),
                  ("tab", "Länge der Fingerzinken", "f", d.tab, (1, 50, 1, 0.5, " mm")),
                  ("pin", "Stiftdurchmesser (0 = Vierkantstift mitlasern)", "f", d.pin, (0, 20, 2, 0.5, " mm")),
                  ("wall", "Wandstärke um das Loch", "f", d.wall, (0.8, 20, 1, 0.5, " mm")),
                  ("clearance", "Spiel", "f", d.clearance, (0, 1, 2, 0.05, " mm")),
                  ("tol", "Toleranz für „berühren“", "f", 0.5, (0.01, 5, 2, 0.1, " mm"))]
        info = ("Für zwei Boxteile, die bei geschlossener Box aneinanderstoßen – z. B. Rückwand der Box und Rückwand "
                "des Deckels. Die Knöchel stehen senkrecht außen auf den Wänden, abwechselnd an beiden Teilen, und "
                "stecken mit je zwei kleinen Fingerzinken in Schlitzen der Wand. Das Auge sitzt genau über der "
                "gemeinsamen Kante, zum Ende hin ist der Knöchel abgeschrägt. Durch alle Augen geht der Stift – ein "
                "mitgelaserter Vierkantstift oder ein Rundstab/Draht. Die Schlitze werden in die beiden Teile "
                "geschnitten, Knöchel und Stifte entstehen neben den Teilen.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        spec = KnuckleSpec(thickness=v["thickness"], knuckles=v["knuckles"], hinges=int(v["hinges"]),
                           inset=v["inset"], pin=v["pin"], clearance=v["clearance"], wall=v["wall"],
                           length=v["length"], end_height=v["end_height"], tab=v["tab"])
        a, b = items
        wa, wb = worlds
        try:
            slots_a, slots_b, plates, pins, _ = knuckle_hinge(rings[0], rings[1], spec, v["tol"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        ia = [i for i, (p, _) in enumerate(wa) if p.closed]
        ib = [i for i, (p, _) in enumerate(wb) if p.closed]
        lid_a = wa[ia[0]][1] if ia else next(iter(a.layer_ids()))
        lid_b = wb[ib[0]][1] if ib else next(iter(b.layer_ids()))
        new_a = list(wa) + [(g.Polyline(r, True), lid_a) for r in slots_a]
        new_b = list(wb) + [(g.Polyline(r, True), lid_b) for r in slots_b]
        bb = g.bbox_of([p for p, _ in wa + wb])
        x = bb[2] + 10.0
        ids = [self._replace_item(a, new_a).id, self._replace_item(b, new_b).id]
        for part, owner in (("A", a), ("B", b)):
            group = [rr for p_, rr in plates if p_ == part]
            if not group:
                continue
            world_k = []
            for rr in group:
                w_ = float(max(r[:, 0].max() for r in rr) - min(r[:, 0].min() for r in rr))
                dx = x - float(min(r[:, 0].min() for r in rr))
                dy = bb[1] - float(min(q[:, 1].min() for q in rr))
                world_k += [(g.Polyline(r + [dx, dy], True), lid_a) for r in rr]
                x += w_ + 3.0
            x += 7.0
            ids.append(self._add_generated(f"Knöchel {owner.name} ({len(group)})", world_k, False).id)
        if pins:
            y = bb[1] - 10.0
            pin_world = []
            for r in pins:
                pin_world.append((g.Polyline(r + [bb[2] + 10.0, y], True), lid_a))
                y -= spec.thickness + 3
            ids.append(self._add_generated(f"Stift ({len(pins)})", pin_world, False).id)
        self.canvas.select_only(ids)
        msg = (f"Knöchelscharnier: {spec.hinges} × {spec.knuckles} Knöchel. Ungerade Knöchel gehören zu {a.name}, "
               f"gerade zu {b.name}.")
        if spec.pin <= 0:
            msg += (f" Der Vierkantstift sitzt im ersten und letzten Knöchel von {a.name} fest (Vierkant "
                    f"{knuckle_square(spec):.2f} mm) und dreht sich in den übrigen (Ø {knuckle_hole(spec):.2f} mm).")
        else:
            msg += f" Loch Ø {knuckle_hole(spec):.2f} mm."
        self.statusBar().showMessage(msg, 12000)

    def _replace_item(self, it, world):
        idx = self.project.items.index(it)
        n = self.project._item_from_world(it.name, world)
        self.project.remove_item(it.id)
        self.canvas.remove_item(it.id)
        self.project.items.insert(idx, n)
        self.canvas.add_item(n)
        self._invalidate()
        return n

    def number_parts_tool(self):
        from ..core.numbering import group_parts, number_paths, reading_order
        from ..core.text_import import default_font
        title = "Teile nummerieren"
        items = [it for it in self._vector_selection() if any(p.closed for p in it.world_paths())]
        if not items:
            items = [it for it in self.project.items
                     if it.kind == "vector" and any(p.closed for p in it.world_paths())]
        if not items:
            QMessageBox.information(self, title, "Es gibt keine Teile mit geschlossenen Konturen.")
            return
        font = default_font()
        if not font:
            QMessageBox.information(self, title, "Keine Schrift gefunden – die Nummern können nicht erzeugt werden.")
            return
        pref = next((l.id for l in self.project.layers if l.mode == "fill" and l.name == i18n_tr("Nummern")),
                    ParamDialog.NEW_FILL)
        fields = [("prefix", "Vorsatz (z. B. „T“ oder leer)", "s", "", 8),
                  ("start", "Erste Nummer", "i", 1, (0, 99999)),
                  ("same", "Gleiche Teile bekommen dieselbe Nummer", "b", True, None),
                  ("mirror", "Gespiegelte Teile gelten als gleich", "b", False, None),
                  ("tol", "Toleranz beim Vergleich", "f", 0.2, (0.01, 5, 2, 0.05, " mm")),
                  ("height", "Schrifthöhe (höchstens)", "f", 5.0, (0.5, 100, 1, 0.5, " mm")),
                  ("min_height", "Schrifthöhe (mindestens)", "f", 1.5, (0.3, 50, 1, 0.1, " mm")),
                  ("margin", "Abstand zum Rand", "f", 0.8, (0, 20, 1, 0.1, " mm")),
                  ("replace", "Vorhandene Nummern auf dieser Ebene ersetzen", "b", True, None),
                  ("layer", "Ebene", "layer", None, (("fill",), pref))]
        scope = i18n_tr("(Auswahl)") if self._vector_selection() else i18n_tr("(alle Teile)")
        info = (f"{len(items)} Teil(e) {scope}. Nummeriert "
                "wird in Lesereihenfolge – Zeilen von oben nach unten, darin von links nach rechts. Gleiche Teile "
                "werden auch gedreht erkannt. Die Nummer kommt an die Stelle mit dem meisten Platz und wird so "
                "verkleinert, dass sie mit dem Randabstand ins Teil passt; ist selbst die Mindesthöhe zu groß, "
                "bleibt das Teil ohne Nummer. Mit „ersetzen“ werden alte Nummern auf der gewählten Ebene in diesen "
                "Teilen vorher entfernt.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        worlds = [[(p, vp.layer_id) for p, vp in zip(it.world_paths(), it.paths)] for it in items]
        rings = [[p.pts for p, _ in w if p.closed] for w in worlds]
        order = reading_order(rings)
        groups = group_parts([rings[i] for i in order], v["tol"], v["mirror"]) if v["same"] else list(
            range(len(order)))
        self._checkpoint(title)
        layer = self._target_layer(v["layer"], "fill", i18n_tr("Nummern"))
        prefix = v["prefix"].strip()
        too_small, ids = [], []
        for k, i in enumerate(order):
            label = f"{prefix}{v['start'] + groups[k]}"
            polys, _ = number_paths(label, rings[i], font, v["height"], v["min_height"], v["margin"])
            world = [(p, lid) for p, lid in worlds[i] if not (v["replace"] and lid == layer.id)]
            if not polys:
                too_small.append(items[i].name)
            world += [(p, layer.id) for p in polys]
            ids.append(self._replace_item(items[i], world).id)
        self.layers.set_project(self.project)
        self.canvas.select_only(ids)
        n_numbers = len(set(groups))
        msg = f"{len(order)} Teil(e) nummeriert, {n_numbers} verschiedene Nummer(n)."
        if too_small:
            msg += f" Zu klein für eine Nummer: {', '.join(too_small[:5])}{' …' if len(too_small) > 5 else ''}."
        self.statusBar().showMessage(msg, 12000)

    def stencil_tool(self):
        from ..core.shapes_gen import stencil_bridges
        title = "Schablonen-Stege"
        items = self._vector_selection()
        if not items:
            QMessageBox.information(self, title, "Bitte zuerst einen Text oder eine Form auswählen.")
            return
        fields = [("width", "Stegbreite", "f", 1.5, (0.2, 20, 2, 0.1, " mm")),
                  ("dir", "Richtung", "c", "vertical", {"vertical": "Senkrecht", "horizontal": "Waagerecht"})]
        dlg = ParamDialog(title, fields, self.project, "Für jedes Innenteil (z. B. im O, A, B, 8) wird ein Steg "
                          "durch die Form gelegt, damit es beim Ausschneiden der Schablone hängen bleibt.", self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        done = 0
        self._checkpoint(title)
        new_ids = []
        for it in items:
            paths = it.world_paths()
            lid = next(iter(it.layer_ids()))
            try:
                res, n = stencil_bridges([p for p in paths if p.closed], v["width"], v["dir"])
            except ValueError:
                continue
            if n:
                new = self._replace_item(it, [(p, lid) for p in res] + [(p, lid) for p in paths if not p.closed])
                new_ids.append(new.id)
                done += n
            else:
                new_ids.append(it.id)
        self.canvas.select_only(new_ids)
        self.statusBar().showMessage(f"{done} Steg(e) eingefügt." if done else "Keine Innenteile gefunden.", 6000)

    def gear_generator(self):
        from ..core.shapes_gen import gear_info, gear_pair, spur_gear
        title = "Zahnrad"
        fields = [("module", "Modul", "f", 2.0, (0.3, 20, 2, 0.25, " mm")),
                  ("teeth", "Zähne", "i", 20, (6, 400)),
                  ("pressure", "Eingriffswinkel", "c", 20.0, {20.0: "20° (Standard)", 14.5: "14,5°", 25.0: "25°"}),
                  ("bore", "Bohrung (0 = keine)", "f", 5.0, (0, 500, 1, 0.5, " mm")),
                  ("backlash", "Flankenspiel", "f", 0.1, (0, 2, 2, 0.05, " mm")),
                  ("pair", "Gegenrad erzeugen", "b", False, None),
                  ("teeth2", "Zähne Gegenrad", "i", 40, (6, 400)),
                  ("bore2", "Bohrung Gegenrad", "f", 5.0, (0, 500, 1, 0.5, " mm")),
                  ("layer", "Ebene", "layer", None, (("cut",), ParamDialog.NEW_CUT))]
        dlg = ParamDialog(title, fields, self.project, "Teilkreis = Modul × Zähne. Räder mit gleichem Modul passen "
                          "zusammen; der Achsabstand ist Modul × (Zähne 1 + Zähne 2) / 2. Ab etwa 17 Zähnen "
                          "entstehen keine unterschnittenen Zähne.", self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        try:
            if v["pair"]:
                a, b, dist = gear_pair(v["module"], v["teeth"], v["teeth2"], v["pressure"], v["bore"], v["bore2"],
                                       v["backlash"])
            else:
                a, b, dist = spur_gear(v["module"], v["teeth"], v["pressure"], v["bore"], v["backlash"]), [], 0
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        layer = self._target_layer(v["layer"], "cut", "Zahnrad")
        inf = gear_info(v["module"], v["teeth"])
        shift = self._place_at(a + b)
        na = len(a)
        ids = [self._add_generated(f"Zahnrad z={v['teeth']}", [(p, layer.id) for p in shift[:na]], False).id]
        if b:
            ids.append(self._add_generated(f"Zahnrad z={v['teeth2']}", [(p, layer.id) for p in shift[na:]], False).id)
        self.canvas.select_only(ids)
        self.statusBar().showMessage(f"Teilkreis Ø {inf['pitch_d']:g} mm, Kopfkreis Ø {inf['outer_d']:g} mm"
                                     + (f", Achsabstand {dist:g} mm" if b else ""), 10000)

    def puzzle_generator(self):
        import random
        from ..core.shapes_gen import jigsaw
        title = "Puzzle"
        fields = [("width", "Breite", "f", 200.0, (10, 2000, 1, 5, " mm")),
                  ("height", "Höhe", "f", 150.0, (10, 2000, 1, 5, " mm")),
                  ("cols", "Spalten", "i", 6, (1, 100)),
                  ("rows", "Reihen", "i", 4, (1, 100)),
                  ("tab", "Nasengröße", "f", 20.0, (10, 30, 0, 1, " %")),
                  ("jitter", "Unregelmäßigkeit", "f", 4.0, (0, 13, 0, 1, " %")),
                  ("seed", "Zufallszahl (0 = neu würfeln)", "i", 0, (0, 999999)),
                  ("radius", "Eckenradius", "f", 0.0, (0, 100, 1, 1, " mm")),
                  ("layer", "Ebene", "layer", None, (("cut",), ParamDialog.NEW_CUT))]
        dlg = ParamDialog(title, fields, self.project, "Tipp: Ein Foto auf derselben Fläche vorher gravieren, "
                          "dann ausschneiden. Gleiche Zufallszahl = gleiches Puzzle.", self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        seed = v["seed"] or random.randint(1, 999999)
        try:
            frame, lines = jigsaw(v["width"], v["height"], v["cols"], v["rows"], v["tab"], v["jitter"], seed,
                                  v["radius"], 10.0, 10.0)
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        self._checkpoint(title)
        layer = self._target_layer(v["layer"], "cut", "Puzzle")
        self._add_generated(f"Puzzle {v['cols']}×{v['rows']}", [(frame, layer.id)] + [(p, layer.id) for p in lines])
        self.statusBar().showMessage(f"Puzzle mit {v['cols'] * v['rows']} Teilen, Zufallszahl {seed}.", 10000)

    def box_generator(self):
        dlg = BoxDialog(self.profile, self)
        if dlg.exec() == BoxDialog.DialogCode.Accepted and dlg.action:
            self._run_test(dlg.project(), "Box", dlg.action)

    def interval_test(self):
        dlg = IntervalTestDialog(self.profile, self)
        if dlg.exec() == IntervalTestDialog.DialogCode.Accepted and dlg.action:
            self._run_test(dlg.project(), "Zeilenabstandstest", dlg.action)

    def _run_test(self, tp: Project, title: str, action: str):
        """Test-Projekt anzeigen, ins Projekt übernehmen, exportieren oder an den Laser senden."""
        if action == "add":
            self._checkpoint(title)
            self.project.layers = tp.layers + self.project.layers
            for it in tp.items:
                self.project.items.append(it)
                self.canvas.add_item(it)
            self.layers.set_project(self.project)
            self.canvas.select_only([it.id for it in tp.items])
            self._invalidate()
            return
        try:
            res = generate(tp, self.profile)
        except Exception as e:
            QMessageBox.critical(self, title, str(e))
            return
        if action == "preview":
            self.a_preview.setChecked(False)
            self._set_sim_enabled(False)
            self.canvas.clear_simulation()
            self.canvas.show_toolpath(res, True)
            self.statusBar().showMessage(f"Vorschau {title} – {fmt_time(res.time_s)}. "
                                         "Mit Strg+P (Vorschau) wieder zur normalen Ansicht.", 10000)
        elif action == "export":
            name = title.lower().replace(" ", "_")
            p, _ = QFileDialog.getSaveFileName(self, f"{title} exportieren",
                                               os.path.join(self.last_dir, f"{name}.gcode"), "G-Code (*.gcode *.nc)")
            if p:
                with open(p, "w", encoding="ascii", errors="replace", newline="\n") as f:
                    f.write(res.text)
        elif action == "send":
            if not self.machine.is_ready():
                QMessageBox.information(self, title, "Laser nicht verbunden oder beschäftigt.")
                return
            if QMessageBox.question(self, title, f"{title} starten? Dauer ca. {fmt_time(res.time_s)}\n\n"
                                    "⚠ Schutzbrille tragen, Laser nie unbeaufsichtigt lassen!") \
                    == QMessageBox.StandardButton.Yes:
                self._prepare_job_log(title, res, "TiLaser", tp)
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
        z_now = 0.0
        z_start, z_stop = z_offset_at(conn.last_job, n - 1), z_offset_at(conn.last_job, conn.last_acked)
        if abs(z_start) > 1e-6 or abs(z_stop) > 1e-6:
            ans = QMessageBox.question(
                self, "Auftrag fortsetzen",
                f"Der Auftrag verstellt die Z-Achse (Fokus). Beim Abbruch stand Z um {z_stop:+.2f} mm gegenüber "
                f"dem Auftragsbeginn, an der gewählten Zeile sind es {z_start:+.2f} mm.\n\n"
                "Steht der Laserkopf noch auf der Höhe beim Abbruch?\n\n"
                "Ja = Kopf seit dem Abbruch nicht bewegt\n"
                "Nein = Fokus wurde neu auf die Ausgangshöhe eingestellt",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            z_now = z_stop if ans == QMessageBox.StandardButton.Yes else 0.0
        lines = resume_lines(conn.last_job, n - 1, self.profile, z_now)
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
            self._set_play_icon(False)
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
            self._set_play_icon(False)
            return
        self._sim_ensure()
        if self.sim_slider.value() >= 1000:
            self.sim_slider.setValue(0)
        self._set_play_icon(True)
        self.sim_timer.start()

    def _sim_tick(self):
        if self.result is None or self.result.time_s <= 0:
            self.sim_timer.stop()
            return
        t = (self._sim_t or 0.0) + 0.04 * self.sim_speed.currentData()
        if t >= self.result.time_s:
            t = self.result.time_s
            self.sim_timer.stop()
            self._set_play_icon(False)
        self.sim_slider.blockSignals(True)
        self.sim_slider.setValue(int(t / self.result.time_s * 1000))
        self.sim_slider.blockSignals(False)
        self._sim_t = t
        self.canvas.set_simulation_time(t)
        self.lbl_sim.setText(f"{fmt_time(t)} / {fmt_time(self.result.time_s)}")

    # ------------------------------------------------------------ Kanten aneinanderlegen
    def _edge_align_start(self):
        self._edge_purpose = "align"
        self.canvas.set_tool("edge")

    def bend_between_edges(self):
        self._edge_purpose = "bend"
        self.canvas.set_tool("edge")

    def _edge_step(self, step):
        if getattr(self, "_edge_purpose", "align") == "bend":
            if step == 0:
                self.statusBar().showMessage("Biegescharnier: Kante des ersten Teils anklicken (dieses Teil wird "
                                             "gedreht und verschoben; Esc = abbrechen)")
            else:
                self.statusBar().showMessage("Jetzt die Kante des zweiten Teils anklicken, mit der es über die "
                                             "Biegezone verbunden werden soll")
            return
        if step == 0:
            self.statusBar().showMessage("Kanten aneinanderlegen: Kante des Teils anklicken, das bewegt werden soll "
                                         "(Esc = abbrechen)")
        else:
            self.statusBar().showMessage("Jetzt die Zielkante am anderen Teil anklicken – Mitten bündig; mit "
                                         "Umschalt: links bzw. unten bündig, mit Strg: rechts bzw. oben bündig")

    def _edges_picked(self, moving, target, mods):
        if getattr(self, "_edge_purpose", "align") == "bend":
            self._edge_purpose = "align"
            self.canvas.set_tool("select")
            self._bend_connect(moving, target)
            return
        from ..core.edgealign import align_matrix, rotation_deg
        it = self.project.item(moving.item_id)
        if it is None:
            return
        mode = "center"
        if mods & Qt.KeyboardModifier.ShiftModifier:
            mode = "start"
        elif mods & Qt.KeyboardModifier.ControlModifier:
            mode = "end"
        m = align_matrix(moving, target, mode)
        self._checkpoint("Kanten aneinanderlegen")
        nx, ny = g.apply_matrix(m, np.array([[it.x, it.y]]))[0]
        it.x, it.y = float(nx), float(ny)
        it.rotation = (it.rotation + rotation_deg(m) + 180.0) % 360.0 - 180.0
        self._refresh_items([it])
        self.canvas.set_tool("select")
        self.canvas.select_only([it.id])
        self.statusBar().showMessage("Kanten bündig angelegt. Tipp: beide Teile auswählen und Werkzeuge → "
                                     "Fingerzinken an Kante… oder Puzzle-Verbindung an Kante… verbindet sie.", 8000)

    def _bend_connect(self, moving, target):
        """Biegezone zwischen zwei Kanten: Teil B so drehen/verschieben, dass seine Kante im Abstand der
        Biegelänge gegenüber der Kante von Teil A liegt, beide mit einem Streifen zu einem Teil vereinigen und
        den Streifen mit einem Biegescharnier-Muster versehen."""
        from ..core import patterns as pt
        title = "Biegescharnier zwischen zwei Kanten"
        b, a = self.project.item(moving.item_id), self.project.item(target.item_id)
        if a is None or b is None or a is b:
            QMessageBox.information(self, title, "Bitte zwei Kanten an zwei verschiedenen Teilen anklicken.")
            return
        fields = [("radius", "Biegeradius (innen)", "f", 10.0, (0, 1000, 1, 1, " mm")),
                  ("angle", "Biegewinkel", "f", 90.0, (1, 360, 0, 5, " °")),
                  ("thickness", "Materialstärke", "f", 3.0, (0.5, 30, 2, 0.5, " mm")),
                  ("style", "Art", "c", "straight", pt.HINGE_STYLES),
                  ("slit", "Schlitzlänge", "f", 20.0, (2, 500, 1, 1, " mm")),
                  ("bridge", "Steg zwischen Schlitzen", "f", 3.0, (0.5, 50, 1, 0.5, " mm")),
                  ("spacing", "Abstand der Reihen", "f", 1.5, (0.3, 20, 2, 0.1, " mm")),
                  ("margin", "Abstand zum Rand", "f", 3.0, (0, 100, 1, 0.5, " mm")),
                  ("relief", "Stress Relief an den Schlitzenden (Ø, 0 = aus)", "f", 0.0, (0, 10, 2, 0.1, " mm"))]
        info = (f"„{b.name}“ wird so gedreht und verschoben, dass seine Kante der Kante von „{a.name}“ im Abstand der "
                "Biegelänge gegenüberliegt. Beide werden mit der Biegezone zu einem Teil verbunden; die Biegezone "
                "bekommt das Schnittmuster. Biegelänge = Winkel × (Radius + halbe Materialstärke), z. B. 90° bei "
                "10 mm Radius und 3 mm Material: 18,1 mm. Der Abstand zum Rand gilt für die Enden der Schlitze.")
        dlg = ParamDialog(title, fields, self.project, info, self)
        if dlg.exec() != ParamDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        wa = [(p, vp.layer_id) for p, vp in zip(a.world_paths(), a.paths)]
        try:
            m, merged, slits, L, width = pt.bend_between(
                [p for p, _ in wa if p.closed], [p for p in b.world_paths() if p.closed], moving, target,
                v["radius"], v["angle"], v["thickness"], v["slit"], v["bridge"], v["spacing"], v["margin"],
                v["style"], v["relief"])
        except ValueError as e:
            QMessageBox.information(self, title, str(e))
            return
        wb = [(p.transformed(m), vp.layer_id) for p, vp in zip(b.world_paths(), b.paths)]
        self._checkpoint(title)
        lid = next((lid for p, lid in wa if p.closed), wa[0][1] if wa else self._drawing_layer().id)
        world = [(p, lid) for p in merged]
        world += [(p, l_) for p, l_ in wa + wb if not p.closed]            # Gravuren u. Ä. bleiben erhalten
        world += [(p, lid) for p in slits]
        name = f"{a.name} + {b.name}"
        self.project.remove_item(b.id)
        self.canvas.remove_item(b.id)
        n = self._replace_item(a, world)
        n.name = name
        self.layers.set_project(self.project)
        self.canvas.select_only([n.id])
        self.statusBar().showMessage(f"Biegezone {L:.1f} mm lang ({v['angle']:g}° bei {v['radius']:g} mm Radius), "
                                     f"{width:.1f} mm breit, {sum(not s.closed for s in slits)} Schlitze.", 10000)

    # ------------------------------------------------------------ Zeichnen / Knoten
    def _on_tool_changed(self, tool):
        a = self.tool_actions.get(tool)
        if a and not a.isChecked():
            a.setChecked(True)
        if tool == "edge":                       # Hinweis kommt über edgeStep
            return
        self._edge_purpose = "align"             # Werkzeug gewechselt: Biegescharnier-Auswahl beenden
        names = {"select": "", "edge": "", "rect": "Rechteck: ziehen", "ellipse": "Ellipse: ziehen",
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
            if op == "xor":
                QMessageBox.warning(self, "Formen", str(e))
                return
            from ..core import clip           # eigene Berechnung ohne pyclipper
            res = groups[0]
            for grp in groups[1:]:
                res = clip.boolean(res, grp, op)
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
        filt = {"svg": "SVG (*.svg)", "dxf": "DXF (*.dxf)", "lbrn2": "LightBurn-Projekt (*.lbrn2)"}[fmt]
        title = "Als LightBurn-Datei speichern" if fmt == "lbrn2" else f"Als {fmt.upper()} exportieren"
        p, _ = QFileDialog.getSaveFileName(self, title,
                                           os.path.join(self.last_dir, f"{base}.{fmt}"), filt)
        if not p:
            return
        if not p.lower().endswith("." + fmt):
            p += "." + fmt
        warn = []
        try:
            if fmt == "svg":
                export_svg(self.project, p, self.profile.bed_w, self.profile.bed_h)
            elif fmt == "lbrn2":
                warn = export_lbrn(self.project, p)
            else:
                export_dxf(self.project, p)
        except Exception as e:
            QMessageBox.critical(self, "Export", str(e))
            return
        self.last_dir = os.path.dirname(p)
        if warn:
            QMessageBox.information(self, "LightBurn-Datei", "\n".join(warn))
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
        if self.live_cam.running:                       # Live-Kamera läuft: aktuelles Bild verwenden
            img = self.live_cam.latest_array()
            if img is not None:
                callback(img)
                return
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

    def _show_camera(self, img, quiet=False):
        H = np.array(self.profile.camera_h, float).reshape(3, 3)
        try:
            rect = cam.rectify(img, H, self.profile.bed_w, self.profile.bed_h, 2.0, self.profile.camera_k1)
        except Exception as e:
            if not quiet:
                QMessageBox.warning(self, "Kamera", f"Entzerrung fehlgeschlagen: {e}")
            return
        self._camera_img = rect
        self.canvas.set_background(rect, self.profile.bed_w, self.profile.bed_h, self.profile.camera_opacity)
        self.a_cam_show.setChecked(True)
        if not quiet:
            self.statusBar().clearMessage()

    # ------------------------------------------------------------ Live-Kamera
    def _camera_tab(self):
        self.sidebar.show()
        self.tabs.setCurrentWidget(self.camera_panel)
        if self.live_cam.cam is None:
            self.camera_panel.start()

    def _bind_macro_keys(self):
        """Tastenkürzel der Makros (fensterweit)."""
        from PyQt6.QtGui import QKeySequence, QShortcut
        for sc in self._macro_shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._macro_shortcuts = []
        for k, mac in enumerate(self.profile.macros):
            if mac.get("key"):
                sc = QShortcut(QKeySequence(mac["key"]), self)
                sc.activated.connect(lambda i=k: self.machine.run_macro(i))
                self._macro_shortcuts.append(sc)

    # ------------------------------------------------------------ Auftragsprotokoll
    def _prepare_job_log(self, kind, res, source, project=None):
        from ..core import joblog
        if not getattr(self.profile, "job_log", True):
            self._job_info = None
            return
        name = os.path.basename(self.project_path) if self.project_path and project is None else \
            ("" if project is not None else "(ungespeichert)")
        self._job_info = {"kind": kind, "project": name,
                          "layers": joblog.layer_summary(project if project is not None else self.project),
                          "lines": len(res.lines), "estimate_s": float(res.time_s), "source": source,
                          "t0": None, "start": "", "flame": ""}

    def _job_log_started(self):
        info = self.__dict__.get("_job_info")
        if info and info["t0"] is None:
            import time as _t
            from ..core import joblog
            info["t0"], info["start"] = _t.time(), joblog.now()

    def _job_log_ended(self, state):
        info = self.__dict__.get("_job_info")
        self._job_info = None
        if not info or info["t0"] is None:
            return
        from ..core import joblog
        dur = self.machine._active_time() if hasattr(self.machine, "_active_time") else 0.0
        if not dur:
            import time as _t
            dur = _t.time() - info["t0"]
        p = self.profile
        e = joblog.JobEntry(start=info["start"], end=joblog.now(), duration_s=float(dur),
                            estimate_s=info["estimate_s"], result=joblog.RESULTS.get(state, state),
                            kind=info["kind"], project=info["project"], layers=info["layers"],
                            lines=info["lines"], source=info["source"], flame=info["flame"],
                            cost=joblog.job_cost(dur, p.job_rate, p.job_power_w, p.job_kwh_price))
        try:
            joblog.append(e)
        except OSError as ex:
            self.machine._log(f"!! Auftragsprotokoll konnte nicht geschrieben werden: {ex}")

    def job_log_dialog(self):
        from .dialogs import JobLogDialog
        dlg = JobLogDialog(self.profile, self)
        if dlg.exec() == JobLogDialog.DialogCode.Accepted:
            dlg.apply(self.profile)
            storage.save_machine(self.profile)

    def _on_job_ended(self, state):
        was = self.__dict__.pop("_var_job", False)
        if was and state == "done":
            self._advance_vars()

    def _on_job_started(self):
        if self.camera_panel.job_started():
            self.tabs.setCurrentWidget(self.camera_panel)
        if getattr(self.profile, "flame_detect", False) and not self.camera_panel.flame_watching():
            self.machine._log("!! Flammenwächter ist eingeschaltet, aber keine Kamera aktiv – keine Überwachung!")

    def _on_flame(self, photo: str):
        """Flammenwächter hat ausgelöst: je nach Einstellung warnen, pausieren oder stoppen."""
        cp = self.camera_panel
        test = cp._flame_test
        action = "warn" if test else getattr(self.profile, "flame_action", "pause")
        if action == "pause" and self.machine.conn.protocol != "grbl":
            action = "stop"       # Marlin/Smoothie: Pause hält den Planner nicht sofort an – sicherer stoppen
        did = ""
        if action == "pause" and self.machine.pause_job():
            did = "Der Auftrag wurde pausiert."
        elif action == "stop" and self.machine.stop_job():
            did = "Der Auftrag wurde gestoppt."
        self.machine._log(f"!! Flammenwächter: mögliche Flamme erkannt. {did}".rstrip())
        if not test:
            import time as _t
            self._flame_alarm, self._flame_did = _t.time(), did
            if self.__dict__.get("_job_info"):
                self._job_info["flame"] = _t.strftime("%H:%M:%S") + (f" ({did})" if did else "")
            self._web_tick()
        self.tabs.setCurrentWidget(cp)
        self.raise_()
        self.activateWindow()
        beep = QTimer(self)
        beep.setInterval(700)
        beep.timeout.connect(QApplication.beep)
        if not test:
            QApplication.beep()
            beep.start()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Flammenwächter")
        if test:
            box.setText("Test: Der Flammenwächter hätte jetzt ausgelöst.")
        else:
            box.setText("<b>🔥 Mögliche Flamme erkannt!</b><br><br>"
                        + (did + " " if did else "") + "Sofort nach dem Laser sehen.")
        info = []
        if action == "pause" and did:
            info.append("Ist alles in Ordnung, mit „Fortsetzen“ (Pause-Knopf) weiterlasern.")
        if photo:
            info.append(f"Foto: {photo}")
        box.setInformativeText("\n".join(info))
        b_stop = None
        if not test and self.machine.conn.job_active:
            b_stop = box.addButton("Auftrag stoppen", QMessageBox.ButtonRole.DestructiveRole)
        b_watch = box.addButton("Weiter beobachten", QMessageBox.ButtonRole.AcceptRole)
        b_off = box.addButton("Wächter für diesen Auftrag aus", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        beep.stop()
        clicked = box.clickedButton()
        if b_stop is not None and clicked is b_stop:
            self.machine.stop_job()
        elif clicked is b_watch:
            cp.rearm_flame()
        elif clicked is b_off:
            if test:
                cp.btn_ftest.setChecked(False)
            else:
                cp._flame_stop()
                self.machine._log("-- Flammenwächter für diesen Auftrag ausgeschaltet.")

    # ------------------------------------------------------------ Sprache
    def _build_language_menu(self, parent_menu):
        from ..core import i18n
        lm = parent_menu.addMenu("Sprache / Language")
        grp = QActionGroup(self)
        cur = i18n.current().code
        for code, name in i18n.available().items():
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(code == cur)
            a.triggered.connect(lambda _c=False, c=code: self._set_language(c))
            grp.addAction(a)
            lm.addAction(a)
        lm.addSeparator()
        lm.addAction(self._act("Eigene Übersetzung anlegen / bearbeiten…", self._edit_translation, None,
                               "Sprachdatei mit allen Texten anlegen, die man in einem Texteditor übersetzen kann"))
        parent_menu.addSeparator()

    def _set_language(self, code):
        from ..core import i18n
        if code == i18n.current().code:
            return
        self.profile.language = code
        storage.save_machine(self.profile)
        QMessageBox.information(self, "Sprache / Language",
                                "Die neue Sprache gilt nach einem Neustart von TiLaser.\n"
                                "The new language takes effect after restarting TiLaser.")

    def _edit_translation(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from ..core import i18n
        code, ok = QInputDialog.getText(self, "Eigene Übersetzung",
                                        "Sprachcode (zwei Buchstaben, z. B. fr, nl, it, pl):",
                                        text=i18n.current().code if i18n.current().active else "")
        code = (code or "").strip().lower()
        if not ok or not code:
            return
        if code == "de":
            QMessageBox.information(self, "Eigene Übersetzung", "Deutsch ist die Ausgangssprache und braucht keine Datei.")
            return
        names = {"en": "English", "fr": "Français", "nl": "Nederlands", "it": "Italiano", "es": "Español",
                 "pl": "Polski", "cs": "Čeština", "da": "Dansk", "sv": "Svenska", "pt": "Português"}
        path = i18n.make_template(code, names.get(code, code), i18n.all_keys())
        QMessageBox.information(self, "Eigene Übersetzung",
                                f"Die Sprachdatei liegt hier:\n{path}\n\n"
                                "Mit einem Texteditor öffnen und jeweils den rechten Text übersetzen "
                                "(links steht der deutsche Text, vorbelegt ist Englisch). Platzhalter in "
                                "geschweiften Klammern beibehalten. Danach unter Ansicht → Sprache die Sprache wählen und TiLaser neu starten.\n\n"
                                "Texte, die noch fehlen, sammelt TiLaser beim Beenden in fehlend_<code>.json im selben Ordner.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ------------------------------------------------------------ Weboberfläche
    def web_settings(self):
        from .dialogs import WebDialog
        dlg = WebDialog(self.profile, self.web, self)
        if dlg.exec() == WebDialog.DialogCode.Accepted:
            dlg.apply(self.profile)
            storage.save_machine(self.profile)
            self._web_apply(restart=True)

    def _web_apply(self, restart: bool = False):
        from ..core.webserver import WebServer
        p = self.profile
        if self.web is not None and (restart or not p.web_enabled):
            self.web.stop()
            self.web = None
        if p.web_enabled and self.web is None:
            if len(p.web_pin or "") < 4:
                from ..core.webserver import new_pin
                p.web_pin = new_pin()                  # ohne PIN keine Weboberfläche
                self.machine._log(f"-- Weboberfläche: neue PIN {p.web_pin} (Maschine → Weboberfläche)")
            try:
                self.web = WebServer(port=p.web_port, pin=p.web_pin, allow_start=p.web_allow_start)
                self.web.start()
            except (OSError, ValueError) as e:
                self.web = None
                self.machine._log(f"!! Weboberfläche konnte nicht starten (Port {p.web_port}): {e}")
                self.statusBar().showMessage(f"Weboberfläche: Port {p.web_port} ist belegt oder gesperrt.", 10000)
                return
            self.machine._log("-- Weboberfläche: " + ", ".join(self.web.urls()))
        if self.web is not None:
            self._web_timer.start()
        else:
            self._web_timer.stop()

    def _web_status(self) -> dict:
        m = self.machine
        conn = m.conn
        pos = m.__dict__.get("last_pos")
        st = {"state": m.lbl_state.text() or "–", "machine": m.machine_state, "connected": bool(conn.connected),
              "job_active": bool(conn.job_active), "pos": f"X {pos[0]:.1f}  Y {pos[1]:.1f}" if pos else "",
              "progress": 0.0, "time": "", "remaining": "", "project": os.path.basename(self.__dict__.get("project_path") or ""),
              "flame_alarm": self._flame_alarm, "flame_did": i18n_tr(self._flame_did),
              "flame_watch": self.camera_panel.flame_watching(),
              "can_start": bool(self.profile.web_allow_start and m.is_ready() and self.project.items)}
        try:
            mx = max(1, m.progress.maximum())
            st["progress"] = 100.0 * m.progress.value() / mx
        except Exception:
            pass
        est = m.time_estimate() if conn.job_active else None
        if est:
            st["time"] = f"{m._fmt(est[0])} / ~{m._fmt(est[1])}"
            st["remaining"] = m._fmt(est[2])
        elif m.lbl_time.text():
            st["time"] = m.lbl_time.text()
        st["log"] = m.console.toPlainText().splitlines()[-20:]
        return st

    def _web_tick(self):
        w = self.web
        if w is None:
            return
        while not w.commands.empty():
            self._web_command(w.commands.get_nowait())
        try:
            w.publish(self._web_status())
        except Exception as e:                    # Anzeige darf den Laser nie stören
            w.publish({"state": f"Fehler: {e}"})
        import time as _t
        if w.camera_wanted() and self.live_cam.image is not None and _t.monotonic() - self._web_jpeg_t > 0.8:
            self._web_jpeg_t = _t.monotonic()
            data = self._camera_jpeg()
            if data:
                w.publish_jpeg(data)

    def _camera_jpeg(self) -> bytes:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt6.QtGui import QTransform
        img = self.live_cam.image
        if img is None or img.isNull():
            return b""
        if img.width() > 800:
            img = img.scaledToWidth(800, Qt.TransformationMode.SmoothTransformation)
        rot = int(getattr(self.profile, "camera_live_rot", 0)) % 360
        if rot:
            img = img.transformed(QTransform().rotate(rot))
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPG", 70)
        buf.close()
        return bytes(ba.data())

    def _web_command(self, cmd: str):
        m = self.machine
        if cmd == "pause" and m.pause_job():
            m._log("-- Über die Weboberfläche pausiert.")
        elif cmd == "resume" and m.resume_job():
            m._log("-- Über die Weboberfläche fortgesetzt.")
        elif cmd == "stop":
            m.stop_job()
            m._log("-- Über die Weboberfläche gestoppt.")
        elif cmd == "ack":
            self._flame_alarm, self._flame_did = 0.0, ""
        elif cmd == "start" and self.profile.web_allow_start:
            self._start_job_remote()

    def _start_job_remote(self):
        m = self.machine
        if not self.project.items or not m.is_ready():
            m._log("!! Start über die Weboberfläche abgelehnt: Projekt leer oder Laser nicht bereit.")
            return
        try:
            only, offset = self._job_params()
            res = self.ensure_result(only, offset)
        except Exception as e:
            m._log(f"!! Start über die Weboberfläche fehlgeschlagen: {e}")
            return
        m._log(f"-- Auftrag über die Weboberfläche gestartet (ca. {fmt_time(res.time_s)}).")
        self._var_job = True
        self._prepare_job_log("Auftrag", res, "Weboberfläche")
        m.run_job(res.lines)

    def _live_bg_toggled(self, on):
        if on and len(self.profile.camera_h) != 9:
            self.statusBar().showMessage("Für das Live-Hintergrundbild die Kamera zuerst ausrichten "
                                         "(Maschine → Kamera → Kamera ausrichten).", 8000)
        elif on:
            self._live_background()

    def _live_background(self):
        if not (self.profile.camera_live_bg and self.live_cam.running and len(self.profile.camera_h) == 9):
            return
        img = self.live_cam.latest_array()
        if img is not None:
            self._show_camera(img, quiet=True)

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
        self.camera_panel.shutdown()
        if self.web is not None:
            self.web.stop()
        try:
            from ..core import i18n
            i18n.current().save_missing()
        except OSError:
            pass
        storage.save_machine(self.profile)
        e.accept()

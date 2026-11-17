"""Hauptfenster von TiLaser."""
from __future__ import annotations

import copy
import os

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QKeySequence
from PyQt6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QDockWidget, QFileDialog, QFrame, QInputDialog,
                             QLabel, QMainWindow, QMenu, QMessageBox, QScrollArea, QTabWidget, QToolBar,
                             QVBoxLayout, QWidget)

from .. import __version__
from ..core import geometry as g
from ..core import storage
from ..core.dxf_import import import_dxf
from ..core.gcode import DIALECTS, frame_gcode, generate
from ..core.model import MODE_LABELS, Item, Project, VectorPath, new_id
from ..core.raster import load_image_gray
from ..core.svg_import import import_svg
from ..core.text_import import text_to_polylines
from .canvas import LaserCanvas
from .dialogs import GcodeDialog, MachineDialog, TextDialog
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
        m.addSeparator()
        m.addAction(self._act("Beenden", self.close, "Ctrl+Q"))

        e = mb.addMenu("&Bearbeiten")
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

        v = mb.addMenu("&Ansicht")
        self.a_fit = self._act("Alles zeigen", self.canvas.zoom_fit, "Ctrl+0")
        self.a_preview = self._act("Werkzeugweg-Vorschau", self.toggle_preview, "Ctrl+P",
                                   "Erzeugt den G-Code und zeigt Brenn- und Leerwege")
        self.a_preview.setCheckable(True)
        self.a_show_gcode = self._act("G-Code anzeigen…", self.show_gcode)
        for a in (self.a_fit, self.a_preview, self.a_show_gcode):
            v.addAction(a)

        mm = mb.addMenu("&Maschine")
        mm.addAction(self._act("Maschineneinstellungen…", self.machine_settings))

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
        tb.addSeparator()
        tb.addAction(self.a_export)
        tb.addSeparator()
        for a in (self.a_group, self.a_ungroup, self.a_center, self.a_origin, self.a_dup, self.a_del):
            tb.addAction(a)

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
            "Alle unterstützten (*.svg *.dxf *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;"
            "SVG (*.svg);;DXF (*.dxf);;Bilder (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)")
        if files:
            self.import_files(files)

    def import_files(self, files):
        new_ids, msgs = [], []
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
                    if ext in (".svg", ".dxf"):
                        paths, warn = (import_svg if ext == ".svg" else import_dxf)(f)
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
        it.paths = [VectorPath(g.Polyline(p.pts - [cx, cy], True), layer.id) for p in polys]
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
            lm = menu.addMenu("Ebene zuweisen")
            self._fill_layer_menu(lm)
            menu.addSeparator()
            menu.addAction(self.a_center)
            menu.addAction(self.a_origin)
            menu.addAction(self.a_dup)
            menu.addSeparator()
            menu.addAction(self.a_del)
        else:
            menu.addAction(self.a_import)
            menu.addAction(self.a_text)
            menu.addAction(self.a_all)
            menu.addAction(self.a_fit)
        menu.exec(global_pos)

    def delete_selected(self):
        ids = self.canvas.selected_ids()
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
            self.canvas.clear_toolpath()

    def ensure_result(self):
        if self.result is None:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.result = generate(self.project, self.profile)
            finally:
                QApplication.restoreOverrideCursor()
            self.lbl_info.setText(getattr(self, "_info", "") +
                                  f"  |  geschätzt {fmt_time(self.result.time_s)}  |  {len(self.result.lines)} Zeilen")
        return self.result

    def toggle_preview(self):
        self._refresh_preview()

    def _refresh_preview(self):
        if not self.a_preview.isChecked():
            self.canvas.clear_toolpath()
            return
        try:
            res = self.ensure_result()
        except Exception as e:
            QMessageBox.critical(self, "Vorschau", f"Fehler bei der G-Code-Erzeugung:\n{e}")
            self.a_preview.setChecked(False)
            return
        self.canvas.show_toolpath(res, self.chk_travel.isChecked())

    def show_gcode(self):
        res = self.ensure_result()
        GcodeDialog(res.text, self).exec()

    def export_gcode(self):
        if not self.project.items:
            QMessageBox.information(self, "Export", "Das Projekt ist leer.")
            return
        res = self.ensure_result()
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
        res = self.ensure_result()
        text = (f"Auftrag starten?\n\nGeschätzte Dauer: {fmt_time(res.time_s)}\n"
                f"Zeilen: {len(res.lines)}\nDialekt: {DIALECTS.get(self.profile.dialect)}\n\n"
                "⚠ Schutzbrille tragen, Absaugung an, Laser nie unbeaufsichtigt lassen!")
        if res.warnings:
            text += "\n\nHinweise:\n• " + "\n• ".join(res.warnings)
        if QMessageBox.question(self, "Auftrag starten", text) != QMessageBox.StandardButton.Yes:
            return
        self.machine.run_job(res.lines)

    def run_frame(self):
        bb = self.project.bbox()
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

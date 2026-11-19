"""Dialoge: Maschineneinstellungen, Text einfügen, G-Code-Anzeige."""
from __future__ import annotations

import os
import re

import numpy as np

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QGridLayout,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core.gcode import DIALECT_DEFAULTS, DIALECTS, MachineProfile
from ..core.model import Project
from ..core.storage import load_machine_templates
from ..core.text_import import default_font, system_fonts
from .panels import color_icon, dspin


class MachineDialog(QDialog):
    def __init__(self, prof: MachineProfile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Maschineneinstellungen")
        self.prof = prof
        lay = QVBoxLayout(self)
        f = QFormLayout()
        trow = QHBoxLayout()
        self.template = QComboBox()
        self.templates = load_machine_templates()
        self.template.addItem("– Vorlage wählen –", None)
        for i, t in enumerate(self.templates):
            self.template.addItem(t["name"], i)
        self.btn_template = QPushButton("Übernehmen")
        trow.addWidget(self.template, 1)
        trow.addWidget(self.btn_template)
        f.addRow("Maschinenvorlage", trow)
        self.lbl_hint = QLabel("")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color: #8a6d3b")
        f.addRow("", self.lbl_hint)
        self.name = QLineEdit(prof.name)
        self.dialect = QComboBox()
        for k, v in DIALECTS.items():
            self.dialect.addItem(v, k)
        self.dialect.setCurrentIndex(list(DIALECTS).index(prof.dialect) if prof.dialect in DIALECTS else 0)
        self.btn_defaults = QPushButton("Standardwerte für Firmware übernehmen")
        self.s_max = dspin(0.001, 100000, 3, 1)
        self.s_max.setValue(prof.s_max)
        self.s_max.setToolTip("S-Wert für 100 % Leistung. GRBL: Wert von $30 (meist 1000), Marlin: 255, Smoothie: 1")
        self.laser_on = QComboBox()
        self.laser_on.setEditable(True)
        self.laser_on.addItems(["M4", "M3", ""])
        self.laser_on.setCurrentText(prof.laser_on)
        self.laser_on.setToolTip("M4 = dynamische Leistung (GRBL, empfohlen), M3 = konstant")
        self.inline = QCheckBox("S-Wert in jeder G1-Zeile (Inline-Leistung)")
        self.inline.setChecked(prof.inline_power)
        self.rapid_f = QCheckBox("G0 mit Vorschub F (Marlin)")
        self.rapid_f.setChecked(prof.rapid_with_feed)
        self.travel = dspin(10, 100000, 0, 100, " mm/min")
        self.travel.setValue(prof.travel_speed)
        self.bed_w = dspin(10, 5000, 1, 10, " mm")
        self.bed_w.setValue(prof.bed_w)
        self.bed_h = dspin(10, 5000, 1, 10, " mm")
        self.bed_h.setValue(prof.bed_h)
        self.frame_power = dspin(0, 20, 1, 0.5, " %")
        self.frame_power.setValue(prof.frame_power)
        self.frame_power.setToolTip("0 % = Laser beim Rahmenfahren aus. Max. 20 % – nur zum Zielen!")
        self.frame_speed = dspin(10, 30000, 0, 100, " mm/min")
        self.frame_speed.setValue(prof.frame_speed)
        self.air_on = QLineEdit(prof.air_on)
        self.air_off = QLineEdit(prof.air_off)
        self.header = QPlainTextEdit(prof.header)
        self.header.setMaximumHeight(70)
        self.footer = QPlainTextEdit(prof.footer)
        self.footer.setMaximumHeight(70)
        f.addRow("Name", self.name)
        f.addRow("Firmware / Dialekt", self.dialect)
        f.addRow("", self.btn_defaults)
        f.addRow("S-Wert bei 100 %", self.s_max)
        f.addRow("Laser-Ein-Befehl", self.laser_on)
        f.addRow("", self.inline)
        f.addRow("", self.rapid_f)
        f.addRow("Eilgang", self.travel)
        f.addRow("Arbeitsbereich Breite", self.bed_w)
        f.addRow("Arbeitsbereich Tiefe", self.bed_h)
        f.addRow("Rahmen: Leistung", self.frame_power)
        f.addRow("Rahmen: Geschwindigkeit", self.frame_speed)
        f.addRow("Air Assist an", self.air_on)
        f.addRow("Air Assist aus", self.air_off)
        self.per_path = QCheckBox("Bei mehreren Durchgängen jede Kontur fertig schneiden")
        self.per_path.setToolTip("An: jede Kontur bekommt alle Durchgänge, erst dann geht es zur nächsten.\n"
                                 "Aus: Durchgang 1 über alle Konturen, dann Durchgang 2 usw.\n"
                                 "(Material kühlt zwischen den Durchgängen ab, dafür mehr Leerfahrten)")
        self.per_path.setChecked(prof.passes_per_path)
        f.addRow("Durchgänge", self.per_path)
        self.no_dupes = QCheckBox("Übereinanderliegende Linien nur einmal schneiden")
        self.no_dupes.setToolTip("Gemeinsame Kanten bündig liegender Teile (gleiche Ebene) werden nur einmal\n"
                                 "geschnitten. Gilt nur für den G-Code – die Teile im Projekt bleiben vollständig.")
        self.no_dupes.setChecked(prof.remove_overlaps)
        f.addRow("Doppelte Linien", self.no_dupes)
        self.cut_order = QComboBox()
        self.cut_order.addItem("Optimiert (kürzeste Wege)", "optimized")
        self.cut_order.addItem("Objekt für Objekt (Reihenfolge im Projekt)", "objects")
        self.cut_order.setToolTip("Optimiert: TiLaser sucht je Ebene den kürzesten Weg über alle Konturen.\n"
                                  "Objekt für Objekt: in der Reihenfolge aus Anordnen → Schnittreihenfolge;\n"
                                  "innerhalb eines Objekts weiterhin innen vor außen und kürzeste Wege.")
        self.cut_order.setCurrentIndex(1 if prof.cut_order == "objects" else 0)
        f.addRow("Schnittreihenfolge", self.cut_order)
        self.rotary = QCheckBox("Rotationsachse (Rolle) statt Y-Achse")
        self.rotary.setChecked(prof.rotary)
        self.rot_mm = dspin(0.1, 10000, 3, 1, " mm")
        self.rot_mm.setValue(prof.rotary_mm_per_rev)
        self.rot_mm.setToolTip("Y-Weg, den die Steuerung für genau eine Umdrehung der Antriebsrolle fährt.\n"
                               "Ermitteln: Markierung auf die Rolle, z. B. 'G91 G0 Y40' senden und Umdrehung prüfen.")
        self.rot_d = dspin(1, 500, 2, 0.5, " mm")
        self.rot_d.setValue(prof.rotary_roller_d)
        f.addRow("", self.rotary)
        f.addRow("Y-Weg je Rollenumdrehung", self.rot_mm)
        f.addRow("Rollendurchmesser", self.rot_d)
        f.addRow("G-Code Anfang", self.header)
        f.addRow("G-Code Ende", self.footer)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.btn_defaults.clicked.connect(self._defaults)
        self.btn_template.clicked.connect(self._apply_template)

    def _apply_template(self):
        i = self.template.currentData()
        if i is None:
            return
        t = self.templates[i]
        self.name.setText(t.get("name", self.name.text()))
        if t.get("dialect") in DIALECTS:
            self.dialect.setCurrentIndex(list(DIALECTS).index(t["dialect"]))
        for key, w in (("s_max", self.s_max), ("travel_speed", self.travel), ("bed_w", self.bed_w),
                       ("bed_h", self.bed_h), ("frame_power", self.frame_power), ("frame_speed", self.frame_speed)):
            if key in t:
                w.setValue(float(t[key]))
        if "laser_on" in t:
            self.laser_on.setCurrentText(t["laser_on"])
        if "inline_power" in t:
            self.inline.setChecked(bool(t["inline_power"]))
        if "rapid_with_feed" in t:
            self.rapid_f.setChecked(bool(t["rapid_with_feed"]))
        for key, w in (("air_on", self.air_on), ("air_off", self.air_off)):
            if key in t:
                w.setText(t[key])
        if "header" in t:
            self.header.setPlainText(t["header"])
        if "footer" in t:
            self.footer.setPlainText(t["footer"])
        self.baud_from_template = t.get("baud")
        self.lbl_hint.setText(t.get("hinweis", ""))

    def _defaults(self):
        d = DIALECT_DEFAULTS[self.dialect.currentData()]
        self.s_max.setValue(d["s_max"])
        self.laser_on.setCurrentText(d["laser_on"])
        self.inline.setChecked(d["inline_power"])
        self.rapid_f.setChecked(d["rapid_with_feed"])

    def apply(self):
        p = self.prof
        p.name = self.name.text().strip() or p.name
        p.dialect = self.dialect.currentData()
        p.s_max = self.s_max.value()
        p.laser_on = self.laser_on.currentText().strip()
        p.inline_power = self.inline.isChecked()
        p.rapid_with_feed = self.rapid_f.isChecked()
        p.travel_speed = self.travel.value()
        p.passes_per_path = self.per_path.isChecked()
        p.remove_overlaps = self.no_dupes.isChecked()
        p.cut_order = self.cut_order.currentData()
        p.bed_w, p.bed_h = self.bed_w.value(), self.bed_h.value()
        p.frame_power = self.frame_power.value()
        p.frame_speed = self.frame_speed.value()
        p.air_on, p.air_off = self.air_on.text().strip(), self.air_off.text().strip()
        p.header, p.footer = self.header.toPlainText(), self.footer.toPlainText()
        p.rotary = self.rotary.isChecked()
        p.rotary_mm_per_rev = self.rot_mm.value()
        p.rotary_roller_d = self.rot_d.value()
        if getattr(self, "baud_from_template", None):
            p.baud = int(self.baud_from_template)


class TextDialog(QDialog):
    NEW_FILL = "__new_fill__"
    NEW_CUT = "__new_cut__"

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text einfügen")
        self.resize(460, 360)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit("TiLaser")
        lay.addWidget(self.text)
        f = QFormLayout()
        frow = QHBoxLayout()
        self.font = QComboBox()
        self.font.setEditable(True)
        fonts = system_fonts()
        for p in fonts:
            self.font.addItem(os.path.splitext(os.path.basename(p))[0], p)
        df = default_font()
        if df:
            i = self.font.findData(df)
            if i >= 0:
                self.font.setCurrentIndex(i)
        btn = QPushButton("…")
        btn.setFixedWidth(30)
        btn.clicked.connect(self._browse)
        frow.addWidget(self.font, 1)
        frow.addWidget(btn)
        self.size = dspin(0.5, 1000, 1, 1, " mm")
        self.size.setValue(10)
        self.size.setToolTip("Schriftgröße (Em-Höhe); Großbuchstaben sind ca. 70 % davon")
        self.spacing = dspin(0.5, 5, 2, 0.1)
        self.spacing.setValue(1.2)
        self.letter = dspin(-10, 50, 2, 0.1, " mm")
        self.align = QComboBox()
        for k, v in (("left", "Links"), ("center", "Zentriert"), ("right", "Rechts")):
            self.align.addItem(v, k)
        self.layer = QComboBox()
        for l in project.layers:
            if l.mode != "raster":
                self.layer.addItem(color_icon(l.color), l.name, l.id)
        self.layer.addItem("Neue Ebene: Füllen (gravieren)", self.NEW_FILL)
        self.layer.addItem("Neue Ebene: Schneiden / Kontur", self.NEW_CUT)
        self.layer.setCurrentIndex(self.layer.count() - 2)
        f.addRow("Schrift", frow)
        f.addRow("Größe", self.size)
        f.addRow("Zeilenabstand", self.spacing)
        f.addRow("Zeichenabstand", self.letter)
        f.addRow("Ausrichtung", self.align)
        f.addRow("Ebene", self.layer)
        lay.addLayout(f)
        hint = QLabel("Platzhalter: {nr} fortlaufende Nummer ({nr:03} = 3-stellig), {datum}, {zeit}, "
                      "{Spaltenname} aus einer CSV-Datei – siehe Werkzeuge → Variable Texte.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        lay.addWidget(hint)
        if not fonts:
            lay.addWidget(QLabel("Keine Systemschriften gefunden – bitte TTF/OTF-Datei wählen."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Schriftdatei", "", "Schriften (*.ttf *.otf *.ttc)")
        if p:
            self.font.addItem(os.path.splitext(os.path.basename(p))[0], p)
            self.font.setCurrentIndex(self.font.count() - 1)

    def font_path(self) -> str | None:
        d = self.font.currentData()
        if d and self.font.currentText() == self.font.itemText(self.font.currentIndex()):
            return d
        t = self.font.currentText()
        return t if os.path.isfile(t) else d


class GcodeDialog(QDialog):
    def __init__(self, text: str, parent=None, max_lines=30000):
        super().__init__(parent)
        self.setWindowTitle("G-Code")
        self.resize(640, 700)
        lay = QVBoxLayout(self)
        lines = text.splitlines()
        ed = QPlainTextEdit()
        ed.setReadOnly(True)
        ed.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace")
        shown = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            shown += f"\n; … {len(lines) - max_lines} weitere Zeilen (vollständig im Export)"
        ed.setPlainText(shown)
        lay.addWidget(QLabel(f"{len(lines)} Zeilen"))
        lay.addWidget(ed)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


def _ok_cancel(dlg, lay):
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    return bb


class ArrayDialog(QDialog):
    """Raster- oder Kreis-Array der Auswahl."""

    def __init__(self, sel_bbox, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Array erstellen")
        lay = QVBoxLayout(self)
        self.kind = QComboBox()
        self.kind.addItem("Raster (Zeilen × Spalten)", "grid")
        self.kind.addItem("Kreis", "circle")
        lay.addWidget(self.kind)
        self.grid_box = QWidget()
        f = QFormLayout(self.grid_box)
        self.cols = QSpinBox()
        self.cols.setRange(1, 500)
        self.cols.setValue(3)
        self.rows = QSpinBox()
        self.rows.setRange(1, 500)
        self.rows.setValue(2)
        self.dx = dspin(-1000, 1000, 2, 0.5, " mm")
        self.dx.setValue(2)
        self.dy = dspin(-1000, 1000, 2, 0.5, " mm")
        self.dy.setValue(2)
        self.gap = QComboBox()
        self.gap.addItem("Abstand zwischen den Objekten", True)
        self.gap.addItem("Rastermaß (Mitte zu Mitte)", False)
        f.addRow("Spalten", self.cols)
        f.addRow("Zeilen", self.rows)
        f.addRow("Abstand X", self.dx)
        f.addRow("Abstand Y", self.dy)
        f.addRow("Bedeutung", self.gap)
        lay.addWidget(self.grid_box)
        self.circ_box = QWidget()
        c = QFormLayout(self.circ_box)
        self.count = QSpinBox()
        self.count.setRange(2, 500)
        self.count.setValue(6)
        cx, cy = (sel_bbox[0] + sel_bbox[2]) / 2, (sel_bbox[1] + sel_bbox[3]) / 2
        self.cx = dspin(-5000, 5000, 2, 1, " mm")
        self.cx.setValue(cx)
        self.cy = dspin(-5000, 5000, 2, 1, " mm")
        self.cy.setValue(cy - max(20.0, (sel_bbox[3] - sel_bbox[1]) * 1.5))
        self.angle = dspin(-360, 360, 1, 15, " °")
        self.angle.setValue(360)
        self.rotate = QCheckBox("Kopien mitdrehen")
        self.rotate.setChecked(True)
        c.addRow("Anzahl (inkl. Original)", self.count)
        c.addRow("Mittelpunkt X", self.cx)
        c.addRow("Mittelpunkt Y", self.cy)
        c.addRow("Gesamtwinkel", self.angle)
        c.addRow("", self.rotate)
        lay.addWidget(self.circ_box)
        _ok_cancel(self, lay)
        self.kind.currentIndexChanged.connect(self._vis)
        self._vis()

    def _vis(self):
        grid = self.kind.currentData() == "grid"
        self.grid_box.setVisible(grid)
        self.circ_box.setVisible(not grid)


class TraceDialog(QDialog):
    """Bild nachzeichnen: Einstellungen für Schwellwert und Glättung."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bild nachzeichnen")
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.threshold = QSpinBox()
        self.threshold.setRange(1, 254)
        self.threshold.setValue(128)
        self.threshold.setToolTip("Pixel dunkler als dieser Wert werden zur Fläche")
        self.invert = QCheckBox("Invertieren (helle Bereiche nachzeichnen)")
        self.smooth = dspin(0, 10, 1, 0.1, " px")
        self.smooth.setValue(0.8)
        self.smooth.setToolTip("Vereinfachung: größer = weniger Punkte, glattere Kanten")
        self.min_area = dspin(0, 10000, 0, 5, " px²")
        self.min_area.setValue(10)
        self.min_area.setToolTip("Kleinere Flecken werden ignoriert")
        self.bright = dspin(-100, 100, 0, 5, "")
        self.contrast = dspin(-100, 100, 0, 5, "")
        self.layer = QComboBox()
        for l in project.layers:
            if l.mode != "raster":
                self.layer.addItem(color_icon(l.color), l.name, l.id)
        self.layer.addItem("Neue Ebene: Schneiden / Kontur", "__cut__")
        self.layer.addItem("Neue Ebene: Füllen (gravieren)", "__fill__")
        self.layer.setCurrentIndex(self.layer.count() - 2)
        self.keep = QCheckBox("Bild behalten")
        self.keep.setChecked(False)
        f.addRow("Schwellwert", self.threshold)
        f.addRow("", self.invert)
        f.addRow("Glättung", self.smooth)
        f.addRow("Mindestgröße", self.min_area)
        f.addRow("Helligkeit", self.bright)
        f.addRow("Kontrast", self.contrast)
        f.addRow("Ebene", self.layer)
        f.addRow("", self.keep)
        lay.addLayout(f)
        _ok_cancel(self, lay)


class MaterialTestDialog(QDialog):
    """Materialtest: Leistung × Geschwindigkeit."""

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        from ..core.materialtest import MaterialTest
        self.setWindowTitle("Materialtest")
        t = MaterialTest()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Jedes Feld wird mit eigener Leistung (Spalten) und Geschwindigkeit (Zeilen) gelasert.\n"
                             "Position = linke untere Ecke des Testrasters (inkl. Beschriftung)."))
        f = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("Gravieren (Fläche füllen)", "fill")
        self.mode.addItem("Schneiden (Kontur)", "cut")
        self.p_min = dspin(0, 100, 1, 5, " %")
        self.p_min.setValue(t.power_min)
        self.p_max = dspin(0, 100, 1, 5, " %")
        self.p_max.setValue(t.power_max)
        self.cols = QSpinBox()
        self.cols.setRange(1, 20)
        self.cols.setValue(t.cols)
        self.s_min = dspin(1, 100000, 0, 100, " mm/min")
        self.s_min.setValue(t.speed_min)
        self.s_max = dspin(1, 100000, 0, 100, " mm/min")
        self.s_max.setValue(t.speed_max)
        self.rows = QSpinBox()
        self.rows.setRange(1, 20)
        self.rows.setValue(t.rows)
        self.cell = dspin(1, 100, 1, 1, " mm")
        self.cell.setValue(t.cell)
        self.gap = dspin(0, 50, 1, 0.5, " mm")
        self.gap.setValue(t.gap)
        self.interval = dspin(0.01, 2, 3, 0.01, " mm")
        self.interval.setValue(t.interval)
        self.passes = QSpinBox()
        self.passes.setRange(1, 50)
        self.x = dspin(-1000, 5000, 1, 5, " mm")
        self.x.setValue(t.x)
        self.y = dspin(-1000, 5000, 1, 5, " mm")
        self.y.setValue(t.y)
        self.labels = QCheckBox("Beschriften")
        self.labels.setChecked(True)
        self.l_power = dspin(0, 100, 1, 5, " %")
        self.l_power.setValue(t.label_power)
        self.l_speed = dspin(1, 100000, 0, 100, " mm/min")
        self.l_speed.setValue(t.label_speed)
        for label, w in (("Modus", self.mode), ("Leistung von", self.p_min), ("Leistung bis", self.p_max),
                         ("Spalten", self.cols), ("Geschwindigkeit von", self.s_min),
                         ("Geschwindigkeit bis", self.s_max), ("Zeilen", self.rows), ("Feldgröße", self.cell),
                         ("Feldabstand", self.gap), ("Zeilenabstand (Gravur)", self.interval),
                         ("Durchgänge", self.passes), ("Position X", self.x), ("Position Y", self.y),
                         ("", self.labels), ("Beschriftung Leistung", self.l_power),
                         ("Beschriftung Geschw.", self.l_speed)):
            f.addRow(label, w)
        lay.addLayout(f)
        self.info = QLabel("")
        lay.addWidget(self.info)
        bb = QDialogButtonBox()
        self.btn_preview = bb.addButton("Vorschau", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_export = bb.addButton("Exportieren…", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_send = bb.addButton("An Laser senden", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.action = None
        self.btn_preview.clicked.connect(lambda: self._done("preview"))
        self.btn_export.clicked.connect(lambda: self._done("export"))
        self.btn_send.clicked.connect(lambda: self._done("send"))

    def _done(self, action):
        self.action = action
        self.accept()

    def test(self):
        from ..core.materialtest import MaterialTest
        return MaterialTest(mode=self.mode.currentData(), power_min=self.p_min.value(), power_max=self.p_max.value(),
                            cols=self.cols.value(), speed_min=self.s_min.value(), speed_max=self.s_max.value(),
                            rows=self.rows.value(), cell=self.cell.value(), gap=self.gap.value(),
                            interval=self.interval.value(), passes=self.passes.value(), x=self.x.value(),
                            y=self.y.value(), labels=self.labels.isChecked(), label_power=self.l_power.value(),
                            label_speed=self.l_speed.value())


class OffsetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Offset (Kontur versetzen)")
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.dist = dspin(-100, 100, 3, 0.1, " mm")
        self.dist.setValue(1.0)
        self.dist.setToolTip("Positiv = nach außen (Formen werden größer), negativ = nach innen")
        self.round = QCheckBox("Runde Ecken")
        self.round.setChecked(True)
        self.keep = QCheckBox("Original behalten")
        self.keep.setChecked(True)
        f.addRow("Abstand", self.dist)
        f.addRow("", self.round)
        f.addRow("", self.keep)
        lay.addLayout(f)
        _ok_cancel(self, lay)


# --------------------------------------------------------------------------- Kamera
class CameraGrabber:
    """Einzelbild von einer Kamera über QtMultimedia aufnehmen (asynchron, Rückruf mit numpy-Bild)."""

    def __init__(self, device_desc: str, on_image, on_error, timeout_ms: int = 10000):
        from PyQt6.QtCore import QTimer
        from PyQt6.QtMultimedia import QCamera, QImageCapture, QMediaCaptureSession, QMediaDevices
        self.on_image, self.on_error = on_image, on_error
        devs = QMediaDevices.videoInputs()
        dev = next((d for d in devs if d.description() == device_desc), devs[0] if devs else None)
        if dev is None:
            on_error("Keine Kamera gefunden.")
            self.cam = None
            return
        self.cam = QCamera(dev)
        self.session = QMediaCaptureSession()
        self.session.setCamera(self.cam)
        self.cap = QImageCapture()
        self.session.setImageCapture(self.cap)
        self.cap.readyForCaptureChanged.connect(self._ready)
        self.cap.imageCaptured.connect(self._captured)
        self.cap.errorOccurred.connect(lambda _id, _err, msg: self._fail(msg))
        self.cam.errorOccurred.connect(lambda _err, msg: self._fail(msg))
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._fail("Zeitüberschreitung beim Aufnehmen."))
        self.timer.start(timeout_ms)
        self.done = False
        self.cam.start()

    def _ready(self, ok):
        if ok and not self.done:
            self.cap.capture()

    def _captured(self, _id, qimg):
        if self.done:
            return
        from PyQt6.QtGui import QImage
        img = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h, bpl = img.width(), img.height(), img.bytesPerLine()
        ptr = img.bits()
        ptr.setsize(img.sizeInBytes())
        arr = np.frombuffer(ptr, np.uint8).reshape(h, bpl)[:, : w * 3].reshape(h, w, 3).copy()
        self._finish()
        self.on_image(arr)

    def _fail(self, msg):
        if self.done:
            return
        self._finish()
        self.on_error(msg or "Kamerafehler")

    def _finish(self):
        self.done = True
        self.timer.stop()
        if self.cam is not None:
            self.cam.stop()


def camera_devices() -> list[str]:
    try:
        from PyQt6.QtMultimedia import QMediaDevices
        return [d.description() for d in QMediaDevices.videoInputs()]
    except Exception:
        return []


class PointPickDialog(QDialog):
    """Kamerabild anzeigen und die Passmarken der Reihe nach anklicken."""
    LABELS = ("links unten", "rechts unten", "rechts oben", "links oben")

    def __init__(self, image: np.ndarray, parent=None, labels=None):
        super().__init__(parent)
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage, QPainter, QPen, QColor, QPixmap
        from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView
        self.setWindowTitle("Passmarken anklicken")
        self.resize(1000, 760)
        self.points: list[tuple[float, float]] = []
        self.labels = list(labels or self.LABELS)
        lay = QVBoxLayout(self)
        self.info = QLabel()
        lay.addWidget(self.info)
        a = np.ascontiguousarray(image)
        h, w = a.shape[:2]
        qi = (QImage(a.data, w, h, w * 3, QImage.Format.Format_RGB888) if a.ndim == 3
              else QImage(a.data, w, h, w, QImage.Format.Format_Grayscale8)).copy()
        self.scene = QGraphicsScene(self)
        self.scene.addPixmap(QPixmap.fromImage(qi))
        dlg = self

        class View(QGraphicsView):
            def mousePressEvent(self, e):
                if e.button() == Qt.MouseButton.LeftButton and len(dlg.points) < len(dlg.labels):
                    p = self.mapToScene(e.position().toPoint())
                    dlg._add(p.x(), p.y())

            def wheelEvent(self, e):
                f = 1.15 ** (e.angleDelta().y() / 120)
                self.scale(f, f)

        self.view = View(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.btn_reset = QPushButton("Zurücksetzen")
        self.btn_reset.clicked.connect(self._reset)
        row.addWidget(self.btn_reset)
        row.addStretch(1)
        lay.addLayout(row)
        self.bb = _ok_cancel(self, lay)
        self._pen = QPen(QColor("#ff1744"), 0)
        self._marks = []
        self._update()
        self.view.fitInView(self.scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _add(self, x, y):
        self.points.append((x, y))
        r = 6 / max(self.view.transform().m11(), 1e-6)
        self._marks.append(self.scene.addLine(x - r, y, x + r, y, self._pen))
        self._marks.append(self.scene.addLine(x, y - r, x, y + r, self._pen))
        self._update()

    def _reset(self):
        for m in self._marks:
            self.scene.removeItem(m)
        self._marks, self.points = [], []
        self._update()

    def _update(self):
        n, total = len(self.points), len(self.labels)
        self.info.setText(f"Klicke die Mitte der Marke {n + 1} von {total}: {self.labels[n]}  (Mausrad = Zoom)"
                          if n < total else f"Alle {total} Marken gesetzt. OK zum Übernehmen.")
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(n == total)


class TextPathDialog(TextDialog):
    """Text-Dialog mit zusätzlichen Einstellungen für Text auf Pfad."""

    def __init__(self, project, parent=None):
        super().__init__(project, parent)
        self.setWindowTitle("Text auf Pfad")
        self.align.setItemText(0, "Am Anfang (bei Konturen: ab oben)")
        self.align.setItemText(1, "Mittig (bei Konturen: oben mittig)")
        self.align.setCurrentIndex(1)
        f = QFormLayout()
        self.distance = dspin(-100, 100, 2, 0.5, " mm")
        self.distance.setValue(1.0)
        self.distance.setToolTip("Abstand der Grundlinie von der Kontur")
        self.offset = dspin(-2000, 2000, 1, 1, " mm")
        self.offset.setToolTip("Text entlang des Pfades verschieben")
        self.flip = QCheckBox("Umdrehen (bei Konturen: innen, unten lesbar)")
        f.addRow("Abstand", self.distance)
        f.addRow("Versatz", self.offset)
        f.addRow("", self.flip)
        self.layout().insertLayout(self.layout().count() - 1, f)
        self.spacing.setEnabled(False)


class NestDialog(QDialog):
    """Nesting: Grundfläche, Abstände, Drehungen, Kopien."""
    ROTATIONS = (("Keine Drehung", (0,)), ("0° / 180°", (0, 180)), ("90°-Schritte", (0, 90, 180, 270)),
                 ("45°-Schritte", tuple(range(0, 360, 45))), ("15°-Schritte (langsam)", tuple(range(0, 360, 15))))

    def __init__(self, bed_w, bed_h, sel_bbox=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nesting – Teile anordnen")
        self.bed = (bed_w, bed_h)
        self.sel_bbox = sel_bbox
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.source = QComboBox()
        self.source.addItem("Arbeitsbereich", "bed")
        self.source.addItem("Eigene Maße", "custom")
        if sel_bbox is not None:
            self.source.addItem("Größtes ausgewähltes Objekt (dessen Umriss)", "object")
        self.x = dspin(-5000, 5000, 1, 1, " mm")
        self.y = dspin(-5000, 5000, 1, 1, " mm")
        self.w = dspin(1, 5000, 1, 1, " mm")
        self.h = dspin(1, 5000, 1, 1, " mm")
        self.spacing = dspin(0, 100, 2, 0.5, " mm")
        self.spacing.setValue(2.0)
        self.spacing.setToolTip("Kleinster Abstand zwischen zwei Teilen")
        self.margin = dspin(0, 100, 2, 0.5, " mm")
        self.margin.setValue(2.0)
        self.margin.setToolTip("Abstand der Teile zum Rand der Grundfläche")
        self.rot = QComboBox()
        for label, rots in self.ROTATIONS:
            self.rot.addItem(label, rots)
        self.rot.setCurrentIndex(2)
        self.rot.setToolTip("Erlaubte Drehungen. Bei Holz mit Maserung ggf. „0° / 180°“ wählen.")
        self.copies = QSpinBox()
        self.copies.setRange(1, 1000)
        self.copies.setToolTip("Anzahl je ausgewähltem Objekt. Was nicht passt, wird nicht angelegt.")
        self.holes = QCheckBox("Kleine Teile auch in Löcher größerer Teile setzen")
        self.holes.setChecked(True)
        self.shared = QCheckBox("Bei Abstand 0 bündig anlegen (gemeinsame Kanten)")
        self.shared.setChecked(True)
        self.shared.setToolTip("Teile werden nach links und unten zusammengeschoben, bis sie sich berühren.\n"
                               "Gemeinsame Kanten schneidet der Laser dann nur einmal (Maschineneinstellung\n"
                               "„Doppelte Linien“). Die Teile selbst bleiben vollständig – bei größerem Abstand\n"
                               "sind alle Linien wieder da.")
        self.thorough = QCheckBox("Gründlich (mehrere Strategien, langsamer, meist dichter)")
        self.thorough.setChecked(True)
        self.frame = QCheckBox("Grundfläche als Rahmen einzeichnen (Ebene ohne Ausgabe)")
        f.addRow("Grundfläche", self.source)
        f.addRow("X (links)", self.x)
        f.addRow("Y (unten)", self.y)
        f.addRow("Breite", self.w)
        f.addRow("Höhe", self.h)
        f.addRow("Mindestabstand", self.spacing)
        f.addRow("Randabstand", self.margin)
        f.addRow("Drehungen", self.rot)
        f.addRow("Kopien je Objekt", self.copies)
        f.addRow("", self.holes)
        f.addRow("", self.shared)
        f.addRow("", self.thorough)
        f.addRow("", self.frame)
        lay.addLayout(f)
        _ok_cancel(self, lay)
        self.source.currentIndexChanged.connect(self._src)
        self.spacing.valueChanged.connect(lambda v: self.shared.setEnabled(v <= 0))
        self.shared.setEnabled(self.spacing.value() <= 0)
        self._src()

    def _src(self):
        src = self.source.currentData()
        if src == "bed":
            vals = (0, 0, self.bed[0], self.bed[1])
        elif src == "object" and self.sel_bbox is not None:
            b = self.sel_bbox
            vals = (b[0], b[1], b[2] - b[0], b[3] - b[1])
        else:
            vals = None
        for w in (self.x, self.y, self.w, self.h):
            w.setEnabled(src == "custom")
        if vals:
            for w, v in zip((self.x, self.y, self.w, self.h), vals):
                w.setValue(v)


class _TestDialogBase(QDialog):
    """Gemeinsame Schaltflächen der Einstell-Tests: Vorschau, Exportieren, An Laser senden."""

    def _finish_layout(self, lay):
        bb = QDialogButtonBox()
        bb.addButton("Vorschau", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(lambda: self._done("preview"))
        bb.addButton("Ins Projekt", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(lambda: self._done("add"))
        bb.addButton("Exportieren…", QDialogButtonBox.ButtonRole.ActionRole).clicked.connect(lambda: self._done("export"))
        bb.addButton("An Laser senden", QDialogButtonBox.ButtonRole.AcceptRole).clicked.connect(lambda: self._done("send"))
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.action = None

    def _done(self, action):
        self.action = action
        self.accept()


class FocusTestDialog(_TestDialogBase):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Fokustest")
        from ..core.lasertests import FocusTest, RampTest
        ft, rt = FocusTest(), RampTest()
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("Ohne Z-Achse: Rampe (Brett schräg legen)", "ramp")
        self.kind.addItem("Mit Z-Achse: Linien in verschiedenen Höhen", "z")
        self.z_min = dspin(-20, 20, 2, 0.25, " mm")
        self.z_min.setValue(ft.z_min)
        self.z_max = dspin(-20, 20, 2, 0.25, " mm")
        self.z_max.setValue(ft.z_max)
        self.steps = QSpinBox()
        self.steps.setRange(2, 41)
        self.steps.setValue(ft.steps)
        self.line_len = dspin(5, 200, 1, 5, " mm")
        self.line_len.setValue(ft.line_len)
        self.length = dspin(20, 1000, 0, 10, " mm")
        self.length.setValue(rt.length)
        self.tick = dspin(1, 100, 1, 1, " mm")
        self.tick.setValue(rt.tick)
        self.power = dspin(0, 100, 1, 5, " %")
        self.power.setValue(ft.power)
        self.speed = dspin(1, 100000, 0, 100, " mm/min")
        self.speed.setValue(ft.speed)
        self.x = dspin(-1000, 5000, 1, 5, " mm")
        self.x.setValue(ft.x)
        self.y = dspin(-1000, 5000, 1, 5, " mm")
        self.y.setValue(rt.y)
        self.labels = QCheckBox("Beschriftung")
        self.labels.setChecked(True)
        self._rows = {}
        for key, label, w in (("kind", "Art", self.kind), ("z", "Z von", self.z_min), ("z", "Z bis", self.z_max),
                              ("z", "Linien", self.steps), ("z", "Linienlänge", self.line_len),
                              ("ramp", "Länge", self.length), ("ramp", "Teilstriche alle", self.tick),
                              ("", "Leistung", self.power), ("", "Geschwindigkeit", self.speed),
                              ("", "Position X", self.x), ("", "Position Y", self.y), ("", "", self.labels)):
            f.addRow(label, w)
            self._rows.setdefault(key, []).append(w)
        lay.addLayout(f)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.form = f
        self.kind.currentIndexChanged.connect(self._vis)
        self._vis()
        self._finish_layout(lay)

    def _vis(self):
        k = self.kind.currentData()
        for key in ("z", "ramp"):
            for w in self._rows[key]:
                self.form.setRowVisible(w, key == k)
        if k == "ramp":
            self.info.setText("Ein Ende des Bretts etwa 10 mm unterlegen, sodass die Linie quer über die Schräge "
                              "läuft, und den Laser auf die Mitte fokussieren. Nach dem Lasern die dünnste Stelle "
                              "suchen und an der Skala ablesen – dort stimmt der Abstand. Danach den Fokus auf "
                              "diese Höhe einstellen.")
        else:
            self.info.setText("Startet in der aktuellen Höhe; jede Linie wird um ihren Z-Wert versetzt gelasert "
                              "(negativ = näher am Material). Die schärfste Linie zeigt den besten Versatz.")

    def project(self):
        from ..core.lasertests import FocusTest, RampTest, build_focus_test, build_ramp_test
        if self.kind.currentData() == "ramp":
            return build_ramp_test(RampTest(length=self.length.value(), tick=self.tick.value(),
                                            power=self.power.value(), speed=self.speed.value(), x=self.x.value(),
                                            y=self.y.value(), labels=self.labels.isChecked()))
        return build_focus_test(FocusTest(z_min=self.z_min.value(), z_max=self.z_max.value(),
                                          steps=self.steps.value(), line_len=self.line_len.value(),
                                          power=self.power.value(), speed=self.speed.value(), x=self.x.value(),
                                          y=self.y.value(), labels=self.labels.isChecked()))


class BoxDialog(_TestDialogBase):
    """Box mit Fingerzinken erzeugen."""
    _last = None

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Box-Generator")
        from ..core.boxgen import BoxSpec
        b = BoxDialog._last or BoxSpec()
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.measure = QComboBox()
        self.measure.addItem("Innenmaße", True)
        self.measure.addItem("Außenmaße", False)
        self.measure.setCurrentIndex(0 if b.inner else 1)
        self.width = dspin(10, 2000, 1, 1, " mm")
        self.width.setValue(b.width)
        self.depth = dspin(10, 2000, 1, 1, " mm")
        self.depth.setValue(b.depth)
        self.height = dspin(5, 2000, 1, 1, " mm")
        self.height.setValue(b.height)
        self.thick = dspin(0.5, 30, 2, 0.1, " mm")
        self.thick.setValue(b.thickness)
        self.finger = dspin(1, 200, 1, 1, " mm")
        self.finger.setValue(b.finger)
        self.kerf = dspin(-1, 2, 3, 0.01, " mm")
        self.kerf.setValue(b.kerf)
        self.kerf.setToolTip("Schnittbreite des Lasers. Wird als Kerf der Ebene „Box“ gesetzt. "
                             "Größerer Wert = strammere Passung.")
        self.closed = QCheckBox("Mit Deckel")
        self.closed.setChecked(b.closed)
        self.power = dspin(0, 100, 1, 5, " %")
        self.power.setValue(b.power)
        self.speed = dspin(1, 100000, 0, 50, " mm/min")
        self.speed.setValue(b.speed)
        self.passes = QSpinBox()
        self.passes.setRange(1, 50)
        self.passes.setValue(b.passes)
        self.gap = dspin(0, 100, 1, 1, " mm")
        self.gap.setValue(b.gap)
        self.x = dspin(-1000, 5000, 1, 5, " mm")
        self.x.setValue(b.x)
        self.y = dspin(-1000, 5000, 1, 5, " mm")
        self.y.setValue(b.y)
        self.labels = QCheckBox("Teile beschriften (Boden, Vorne, …)")
        self.labels.setChecked(b.labels)
        for label, w in (("Maße", self.measure), ("Breite (X)", self.width), ("Tiefe (Y)", self.depth),
                         ("Höhe (Z)", self.height), ("Materialstärke", self.thick), ("Zinkenbreite", self.finger),
                         ("Schnittbreite (Kerf)", self.kerf), ("", self.closed), ("Leistung", self.power),
                         ("Geschwindigkeit", self.speed), ("Durchgänge", self.passes),
                         ("Abstand der Teile", self.gap), ("Position X", self.x), ("Position Y", self.y),
                         ("", self.labels)):
            f.addRow(label, w)
        lay.addLayout(f)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        for w in (self.width, self.depth, self.height, self.thick, self.finger):
            w.valueChanged.connect(self._update_info)
        self.measure.currentIndexChanged.connect(self._update_info)
        self.closed.toggled.connect(self._update_info)
        self._update_info()
        self._finish_layout(lay)

    def spec(self):
        from ..core.boxgen import BoxSpec
        return BoxSpec(width=self.width.value(), depth=self.depth.value(), height=self.height.value(),
                       inner=bool(self.measure.currentData()), thickness=self.thick.value(),
                       finger=self.finger.value(), closed=self.closed.isChecked(), kerf=self.kerf.value(),
                       gap=self.gap.value(), x=self.x.value(), y=self.y.value(), labels=self.labels.isChecked(),
                       power=self.power.value(), speed=self.speed.value(), passes=self.passes.value())

    def _update_info(self):
        s = self.spec()
        W, D, H = s.outer()
        try:
            s.check()
            msg = (f"Außenmaß {W:g} × {D:g} × {H:g} mm, {6 if s.closed else 5} Teile. "
                   "Material erst messen: Die Materialstärke muss genau stimmen, sonst passen die Zinken nicht. "
                   "Für eine Presspassung den Kerf etwas größer als die echte Schnittbreite wählen.")
        except ValueError as e:
            msg = f"⚠ {e}"
        self.info.setText(msg)

    def _done(self, action):
        try:
            self.spec().check()
        except ValueError as e:
            QMessageBox.warning(self, "Box-Generator", str(e))
            return
        BoxDialog._last = self.spec()
        super()._done(action)

    def project(self):
        from ..core.boxgen import build_box
        return build_box(self.spec())


class IntervalTestDialog(_TestDialogBase):
    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Zeilenabstandstest")
        from ..core.lasertests import IntervalTest
        t = IntervalTest()
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.iv_min = dspin(0.01, 2, 3, 0.01, " mm")
        self.iv_min.setValue(t.interval_min)
        self.iv_max = dspin(0.01, 2, 3, 0.01, " mm")
        self.iv_max.setValue(t.interval_max)
        self.count = QSpinBox()
        self.count.setRange(2, 20)
        self.count.setValue(t.count)
        self.cell = dspin(2, 100, 1, 1, " mm")
        self.cell.setValue(t.cell)
        self.gap = dspin(0, 50, 1, 0.5, " mm")
        self.gap.setValue(t.gap)
        self.power = dspin(0, 100, 1, 5, " %")
        self.power.setValue(t.power)
        self.speed = dspin(1, 100000, 0, 100, " mm/min")
        self.speed.setValue(t.speed)
        self.angle = dspin(-180, 180, 0, 15, " °")
        self.x = dspin(-1000, 5000, 1, 5, " mm")
        self.x.setValue(t.x)
        self.y = dspin(-1000, 5000, 1, 5, " mm")
        self.y.setValue(t.y)
        self.labels = QCheckBox("Beschriftung")
        self.labels.setChecked(True)
        for label, w in (("Zeilenabstand von", self.iv_min), ("Zeilenabstand bis", self.iv_max),
                         ("Felder", self.count), ("Feldgröße", self.cell), ("Feldabstand", self.gap),
                         ("Leistung", self.power), ("Geschwindigkeit", self.speed), ("Winkel", self.angle),
                         ("Position X", self.x), ("Position Y", self.y), ("", self.labels)):
            f.addRow(label, w)
        lay.addLayout(f)
        info = QLabel("Gefüllte Quadrate mit steigendem Zeilenabstand. Das Feld, in dem die Linien gerade "
                      "zu einer gleichmäßigen Fläche verschmelzen, zeigt den passenden Zeilenabstand für "
                      "dieses Material und diesen Fokus.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self._finish_layout(lay)

    def project(self):
        from ..core.lasertests import IntervalTest, build_interval_test
        return build_interval_test(IntervalTest(
            interval_min=self.iv_min.value(), interval_max=self.iv_max.value(), count=self.count.value(),
            cell=self.cell.value(), gap=self.gap.value(), power=self.power.value(), speed=self.speed.value(),
            angle=self.angle.value(), x=self.x.value(), y=self.y.value(), labels=self.labels.isChecked()))



class BarcodeDialog(QDialog):
    """QR-Code oder Barcode (Code 128) einfügen; Inhalt darf Platzhalter enthalten."""
    NEW_FILL = TextDialog.NEW_FILL

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QR-Code / Barcode einfügen")
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("QR-Code", "qr")
        self.kind.addItem("Barcode (Code 128)", "code128")
        self.content = QLineEdit("https://franzhefekranz.github.io/TiLaser/")
        self.content.setToolTip("Text oder Adresse. Platzhalter wie {nr}, {datum} oder {Spalte} sind erlaubt.")
        self.size = dspin(5, 500, 1, 1, " mm")
        self.size.setValue(25)
        self.ecl = QComboBox()
        for k, t in (("L", "L – 7 % (kleinster Code)"), ("M", "M – 15 % (Standard)"), ("Q", "Q – 25 %"),
                     ("H", "H – 30 % (robust)")):
            self.ecl.addItem(t, k)
        self.ecl.setCurrentIndex(1)
        self.width = dspin(10, 1000, 1, 1, " mm")
        self.width.setValue(50)
        self.height = dspin(3, 300, 1, 1, " mm")
        self.height.setValue(15)
        self.layer = QComboBox()
        for l in project.layers:
            if l.mode in ("fill", "offset"):
                self.layer.addItem(color_icon(l.color), l.name, l.id)
        self.layer.addItem("Neue Ebene: Füllen (gravieren)", self.NEW_FILL)
        self.layer.setCurrentIndex(self.layer.count() - 1)
        self.form = f
        for label, w in (("Art", self.kind), ("Inhalt", self.content), ("Kantenlänge", self.size),
                         ("Fehlerkorrektur", self.ecl), ("Breite", self.width), ("Höhe", self.height),
                         ("Ebene", self.layer)):
            f.addRow(label, w)
        lay.addLayout(f)
        info = QLabel("Tipp: Für zuverlässiges Scannen einen Zeilenabstand von höchstens einem Viertel der "
                      "Modulbreite wählen und um den Code einen hellen Rand lassen. Vorher mit dem Handy testen.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.kind.currentIndexChanged.connect(self._vis)
        self._vis()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _vis(self):
        qr = self.kind.currentData() == "qr"
        for w, v in ((self.size, qr), (self.ecl, qr), (self.width, not qr), (self.height, not qr)):
            self.form.setRowVisible(w, v)

    def spec(self) -> dict:
        if self.kind.currentData() == "qr":
            return {"kind": "qr", "template": self.content.text(), "size": self.size.value(),
                    "ecl": self.ecl.currentData()}
        return {"kind": "code128", "template": self.content.text(), "width": self.width.value(),
                "height": self.height.value()}


class VarDialog(QDialog):
    """Werte für die Platzhalter: fortlaufende Nummer und CSV-Datei."""

    def __init__(self, st, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Variable Texte")
        self.st = st
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.nr = QSpinBox()
        self.nr.setRange(-999999, 99999999)
        self.nr.setValue(st.nr)
        self.step = QSpinBox()
        self.step.setRange(-1000, 1000)
        self.step.setValue(st.step)
        crow = QHBoxLayout()
        self.csv = QLineEdit(st.csv_path)
        self.csv.setPlaceholderText("keine CSV-Datei")
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(self._browse)
        b2 = QPushButton("✕")
        b2.setFixedWidth(30)
        b2.setToolTip("CSV-Datei entfernen")
        b2.clicked.connect(lambda: (self.csv.setText(""), self._load()))
        crow.addWidget(self.csv, 1)
        crow.addWidget(b)
        crow.addWidget(b2)
        self.row = QSpinBox()
        self.row.setRange(1, 1)
        self.auto = QCheckBox("Nach jedem Auftrag bzw. G-Code-Export weiterzählen")
        self.auto.setChecked(st.auto_advance)
        self.cols = QLabel()
        self.cols.setWordWrap(True)
        for label, w in (("Nummer {nr}", self.nr), ("Schrittweite", self.step), ("CSV-Datei", crow),
                         ("Aktuelle Zeile", self.row), ("Spalten", self.cols), ("", self.auto)):
            f.addRow(label, w)
        lay.addLayout(f)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)
        self.row.valueChanged.connect(self._update_preview)
        self.nr.valueChanged.connect(self._update_preview)
        self.csv.editingFinished.connect(self._load)
        self._rows = []
        self._load()
        self.row.setValue(st.csv_row + 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "CSV-Datei", "", "CSV (*.csv *.txt);;Alle Dateien (*)")
        if p:
            self.csv.setText(p)
            self._load()

    def _load(self):
        from ..core.vartext import read_csv
        self._rows, head = [], []
        p = self.csv.text().strip()
        if p:
            try:
                head, self._rows = read_csv(p)
            except Exception as e:
                self.cols.setText(f"Datei nicht lesbar: {e}")
        self.row.setRange(1, max(1, len(self._rows)))
        self.row.setEnabled(bool(self._rows))
        if head:
            self.cols.setText(", ".join("{" + h + "}" for h in head) + f"   ({len(self._rows)} Zeilen)")
        elif not p:
            self.cols.setText("–")
        self._update_preview()

    def _update_preview(self, *_):
        r = self._rows[self.row.value() - 1] if self._rows else {}
        vals = ", ".join(f"{k} = {v}" for k, v in list(r.items())[:4])
        self.preview.setText(f"Nächster Auftrag: {{nr}} = {self.nr.value()}" + (f"; {vals}" if vals else ""))

    def apply(self):
        self.st.nr, self.st.step = self.nr.value(), self.step.value()
        self.st.csv_path = self.csv.text().strip()
        self.st.csv_row = self.row.value() - 1 if self._rows else 0
        self.st.auto_advance = self.auto.isChecked()


GRBL_SETTINGS = {
    0: "Schrittimpuls-Dauer (µs)", 1: "Schritt-Leerlaufverzögerung (ms, 255 = Motoren immer an)",
    2: "Schrittsignal invertieren (Bitmaske)", 3: "Drehrichtung invertieren (Bitmaske X=1, Y=2, Z=4)",
    4: "Enable-Signal invertieren", 5: "Endschalter invertieren", 6: "Sonde invertieren",
    10: "Statusbericht (Bitmaske)", 11: "Kurvenabweichung (mm)", 12: "Bogentoleranz (mm)",
    13: "Berichte in Zoll", 20: "Software-Endschalter (Arbeitsbereich)", 21: "Hardware-Endschalter",
    22: "Referenzfahrt aktiv", 23: "Referenzfahrt-Richtung invertieren (Bitmaske)",
    24: "Referenzfahrt langsam (mm/min)", 25: "Referenzfahrt schnell (mm/min)", 26: "Endschalter-Entprellung (ms)",
    27: "Rückzug nach Referenzfahrt (mm)", 30: "Maximaler S-Wert (= 100 % Leistung)", 31: "Minimaler S-Wert",
    32: "Lasermodus (1 = an, für Laser nötig)",
    100: "Schritte/mm X", 101: "Schritte/mm Y", 102: "Schritte/mm Z",
    110: "Max. Geschwindigkeit X (mm/min)", 111: "Max. Geschwindigkeit Y (mm/min)",
    112: "Max. Geschwindigkeit Z (mm/min)", 120: "Beschleunigung X (mm/s²)", 121: "Beschleunigung Y (mm/s²)",
    122: "Beschleunigung Z (mm/s²)", 130: "Max. Verfahrweg X (mm)", 131: "Max. Verfahrweg Y (mm)",
    132: "Max. Verfahrweg Z (mm)",
}


class GrblSettingsDialog(QDialog):
    """GRBL-Einstellungen ($$) anzeigen und geänderte Werte zurückschreiben."""

    def __init__(self, panel, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem
        self._Item = QTableWidgetItem
        self.panel = panel
        self.setWindowTitle("GRBL-Einstellungen")
        self.resize(620, 560)
        lay = QVBoxLayout(self)
        warn = QLabel("⚠ Falsche Werte können die Maschine falsch fahren lassen. Vorher die aktuellen Werte "
                      "sichern (Exportieren), nur bekannte Werte ändern.")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Nr.", "Bedeutung", "Wert"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        self.status = QLabel("Lese Einstellungen …")
        lay.addWidget(self.status)
        bb = QDialogButtonBox()
        b_read = bb.addButton("Neu lesen", QDialogButtonBox.ButtonRole.ActionRole)
        b_save = bb.addButton("Exportieren…", QDialogButtonBox.ButtonRole.ActionRole)
        b_write = bb.addButton("Geänderte schreiben", QDialogButtonBox.ButtonRole.ApplyRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        b_read.clicked.connect(self._read)
        b_save.clicked.connect(self._export)
        b_write.clicked.connect(self._write)
        self._orig: dict[int, str] = {}
        panel.grblSettingsUpdated.connect(self._fill)
        self._fill()

    def _read(self):
        self.panel.grbl_settings.clear()
        self.table.setRowCount(0)
        self.status.setText("Lese Einstellungen …")
        self.panel.conn.send("$$")

    def _fill(self):
        cur = dict(self.panel.grbl_settings)
        if not cur:
            return
        self.table.blockSignals(True)
        self.table.setRowCount(len(cur))
        for r, k in enumerate(sorted(cur)):
            for c, txt in enumerate((f"${k}", GRBL_SETTINGS.get(k, ""), cur[k])):
                it = self._Item(txt)
                if c < 2:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        self._orig = cur
        self.status.setText(f"{len(cur)} Einstellungen gelesen. Werte in der Spalte „Wert“ ändern und "
                            "„Geänderte schreiben“ drücken.")

    def changes(self) -> list[tuple[int, str]]:
        out = []
        for r in range(self.table.rowCount()):
            k = int(self.table.item(r, 0).text()[1:])
            v = self.table.item(r, 2).text().strip()
            if v and v != self._orig.get(k):
                out.append((k, v))
        return out

    def _write(self):
        ch = self.changes()
        if not ch:
            self.status.setText("Keine Änderungen.")
            return
        bad = [f"${k}={v}" for k, v in ch if not re.fullmatch(r"-?\d+(\.\d+)?", v)]
        if bad:
            QMessageBox.warning(self, "GRBL-Einstellungen", "Ungültige Werte (nur Zahlen):\n" + "\n".join(bad))
            return
        txt = "\n".join(f"${k}={v}   ({GRBL_SETTINGS.get(k, '')})" for k, v in ch)
        if QMessageBox.question(self, "GRBL-Einstellungen", f"Diese Werte in die Steuerung schreiben?\n\n{txt}") \
                != QMessageBox.StandardButton.Yes:
            return
        for k, v in ch:
            self.panel.conn.send(f"${k}={v}")
            self.panel._log(f">> ${k}={v}")
        self.status.setText(f"{len(ch)} Wert(e) geschrieben – lese zur Kontrolle neu …")
        self._read()

    def _export(self):
        p, _ = QFileDialog.getSaveFileName(self, "GRBL-Einstellungen sichern", "grbl-einstellungen.txt",
                                           "Text (*.txt)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                for k in sorted(self._orig):
                    f.write(f"${k}={self._orig[k]}\n")


class MacroDialog(QDialog):
    """Eigene G-Code-Knöpfe: Name, G-Code (mehrzeilig), Tastenkürzel."""

    def __init__(self, macros: list, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QKeySequenceEdit, QListWidget
        self.setWindowTitle("Makros bearbeiten")
        self.resize(560, 420)
        self._macros = [dict(m) for m in macros]
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in (("+", self._add), ("−", self._del), ("↑", lambda: self._move(-1)), ("↓", lambda: self._move(1))):
            b = QPushButton(text)
            b.setFixedWidth(34)
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        f = QFormLayout()
        self.name = QLineEdit()
        self.code = QPlainTextEdit()
        self.code.setPlaceholderText("z. B.\nM8\nG0 X0 Y0")
        self.key = QKeySequenceEdit()
        f.addRow("Name", self.name)
        f.addRow("G-Code", self.code)
        f.addRow("Tastenkürzel", self.key)
        right.addLayout(f)
        hint = QLabel("Jede Zeile wird an die Steuerung gesendet. Makros sind gesperrt, solange ein Auftrag läuft.")
        hint.setWordWrap(True)
        right.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay.addLayout(right, 2)
        self._cur = -1
        self.list.currentRowChanged.connect(self._select)
        self.name.textEdited.connect(self._store)
        self.code.textChanged.connect(self._store)
        self.key.keySequenceChanged.connect(self._store)
        self._refresh()
        if self._macros:
            self.list.setCurrentRow(0)

    def _refresh(self):
        self.list.blockSignals(True)
        self.list.clear()
        for m in self._macros:
            self.list.addItem(m.get("name") or "(ohne Namen)")
        self.list.blockSignals(False)

    def _select(self, i):
        self._cur = i
        ok = 0 <= i < len(self._macros)
        for w in (self.name, self.code, self.key):
            w.setEnabled(ok)
            w.blockSignals(True)
        m = self._macros[i] if ok else {}
        self.name.setText(m.get("name", ""))
        self.code.setPlainText(m.get("gcode", ""))
        from PyQt6.QtGui import QKeySequence
        self.key.setKeySequence(QKeySequence(m.get("key", "")))
        for w in (self.name, self.code, self.key):
            w.blockSignals(False)

    def _store(self, *_):
        if not (0 <= self._cur < len(self._macros)):
            return
        m = self._macros[self._cur]
        m["name"] = self.name.text()
        m["gcode"] = self.code.toPlainText()
        m["key"] = self.key.keySequence().toString()
        it = self.list.item(self._cur)
        if it is not None:
            it.setText(m["name"] or "(ohne Namen)")

    def _add(self):
        self._macros.append({"name": f"Makro {len(self._macros) + 1}", "gcode": "", "key": ""})
        self._refresh()
        self.list.setCurrentRow(len(self._macros) - 1)

    def _del(self):
        if 0 <= self._cur < len(self._macros):
            del self._macros[self._cur]
            self._refresh()
            self.list.setCurrentRow(min(self._cur, len(self._macros) - 1))

    def _move(self, d):
        i, j = self._cur, self._cur + d
        if 0 <= i < len(self._macros) and 0 <= j < len(self._macros):
            self._macros[i], self._macros[j] = self._macros[j], self._macros[i]
            self._refresh()
            self.list.setCurrentRow(j)

    def macros(self) -> list:
        return [m for m in self._macros if m.get("gcode", "").strip() or m.get("name", "").strip()]


class WebDialog(QDialog):
    """Einstellungen der Weboberfläche (Fernbedienung im lokalen Netz)."""

    def __init__(self, profile, server=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Weboberfläche")
        from ..core.webserver import local_addresses, new_pin
        self._new_pin = new_pin
        lay = QVBoxLayout(self)
        intro = QLabel("Den Laser vom Handy, Tablet oder einem anderen Rechner im selben Netz beobachten: "
                       "Zustand, Fortschritt, Restzeit, Kamerabild und Konsole, dazu Pause, Fortsetzen und "
                       "Stopp. TiLaser muss dafür laufen.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        f = QFormLayout()
        self.enabled = QCheckBox("Weboberfläche einschalten")
        self.enabled.setChecked(bool(profile.web_enabled))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(profile.web_port))
        pin_row = QHBoxLayout()
        self.pin = QLineEdit(profile.web_pin or new_pin())
        self.pin.setMaxLength(12)
        self.pin.setToolTip("Wird im Browser einmal abgefragt. Mindestens 4 Zeichen; "
                            "ist sie kürzer oder leer, wird eine neue PIN erzeugt.")
        b_new = QPushButton("Neue PIN")
        b_new.clicked.connect(lambda: self.pin.setText(new_pin()))
        pin_row.addWidget(self.pin, 1)
        pin_row.addWidget(b_new)
        self.allow_start = QCheckBox("Aufträge aus der Ferne starten erlauben")
        self.allow_start.setChecked(bool(profile.web_allow_start))
        self.allow_start.setToolTip("Startet das aktuell geöffnete Projekt mit den Einstellungen der Auftragsleiste. "
                                    "Nur einschalten, wenn beim Start immer jemand am Laser steht.")
        f.addRow("", self.enabled)
        f.addRow("Port", self.port)
        f.addRow("PIN", pin_row)
        f.addRow("", self.allow_start)
        lay.addLayout(f)
        port = server.port if server is not None else int(profile.web_port)
        urls = [f"http://{ip}:{port}/" for ip in local_addresses()]
        state = "läuft" if server is not None and server.running else "aus"
        self.lbl = QLabel(f"Status: {state}<br>Adresse im Browser: <b>{urls[0]}</b>"
                          + ("<br>weitere: " + ", ".join(urls[1:]) if len(urls) > 1 else ""))
        self.lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        qr = QLabel()
        qr.setPixmap(_qr_pixmap(urls[0], 4))
        qr.setToolTip("Mit der Handykamera scannen")
        lay.addWidget(qr, 0, Qt.AlignmentFlag.AlignHCenter)
        warn = QLabel("Nur im eigenen Netz verwenden und keine Portfreigabe im Router einrichten. "
                      "Die Weboberfläche ersetzt nicht die Aufsicht am Laser. Unter Windows fragt die Firewall "
                      "beim ersten Start, ob TiLaser im privaten Netz erreichbar sein darf.")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply(self, profile):
        profile.web_enabled = self.enabled.isChecked()
        profile.web_port = int(self.port.value())
        pin = re.sub(r"\s+", "", self.pin.text())
        profile.web_pin = pin if len(pin) >= 4 else self._new_pin()
        profile.web_allow_start = self.allow_start.isChecked()


def _qr_pixmap(text: str, scale: int = 4):
    from PyQt6.QtGui import QColor, QImage, QPixmap
    from ..core.barcode import qr_matrix
    m = qr_matrix(text, "M")
    n = len(m) + 8                              # Ruhezone 4 Module
    img = QImage(n, n, QImage.Format.Format_RGB32)
    img.fill(QColor(255, 255, 255))
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                img.setPixel(x + 4, y + 4, 0xFF000000)
    return QPixmap.fromImage(img.scaled(n * scale, n * scale))


class ParamDialog(QDialog):
    """Einfacher Einstellungsdialog für Generatoren.

    fields: Liste (Schlüssel, Beschriftung, Art, Vorgabe, Zusatz)
      Art "f": Zusatz = (min, max, Nachkommastellen, Schritt, Einheit)
      Art "i": Zusatz = (min, max)
      Art "c": Zusatz = {Wert: Text}
      Art "b": Häkchen (Beschriftung = Text am Häkchen)
      Art "layer": Zusatz = (erlaubte Modi, Vorgabe "new_fill" | "new_cut" | Ebenen-ID)
    Die zuletzt verwendeten Werte werden je Dialogtitel für die Sitzung gemerkt.
    """
    _memory: dict = {}
    NEW_FILL = TextDialog.NEW_FILL
    NEW_CUT = TextDialog.NEW_CUT

    def __init__(self, title: str, fields, project=None, info: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._title = title
        mem = ParamDialog._memory.get(title, {})
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.w = {}
        self._kinds = {}
        for key, label, kind, default, extra in fields:
            val = mem.get(key, default) if kind != "layer" else default
            if kind == "f":
                lo, hi, dec, step, suf = extra
                w = dspin(lo, hi, dec, step, suf)
                w.setValue(float(val))
            elif kind == "i":
                w = QSpinBox()
                w.setRange(*extra)
                w.setValue(int(val))
            elif kind == "c":
                w = QComboBox()
                for k, txt in extra.items():
                    w.addItem(txt, k)
                w.setCurrentIndex(max(0, w.findData(val)))
            elif kind == "b":
                w = QCheckBox(label)
                w.setChecked(bool(val))
                label = ""
            elif kind == "layer":
                modes, pref = extra
                w = QComboBox()
                for l in (project.layers if project is not None else []):
                    if l.mode in modes:
                        w.addItem(color_icon(l.color), l.name, l.id)
                w.addItem("Neue Ebene: Füllen (gravieren)", self.NEW_FILL)
                w.addItem("Neue Ebene: Schneiden / Kontur", self.NEW_CUT)
                i = w.findData(pref)
                w.setCurrentIndex(i if i >= 0 else w.count() - 1)
            else:
                raise ValueError(kind)
            self.w[key] = w
            self._kinds[key] = kind
            f.addRow(label, w)
        lay.addLayout(f)
        if info:
            lb = QLabel(info)
            lb.setWordWrap(True)
            lay.addWidget(lb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        out = {}
        for k, w in self.w.items():
            kind = self._kinds[k]
            if kind == "f":
                out[k] = float(w.value())
            elif kind == "i":
                out[k] = int(w.value())
            elif kind in ("c", "layer"):
                out[k] = w.currentData()
            elif kind == "b":
                out[k] = bool(w.isChecked())
        return out

    def _ok(self):
        ParamDialog._memory[self._title] = {k: v for k, v in self.values().items() if self._kinds[k] != "layer"}
        self.accept()


class JobLogDialog(QDialog):
    """Auftragsprotokoll anzeigen, Kostensätze einstellen, exportieren oder leeren."""

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem
        from ..core import joblog
        self.setWindowTitle("Auftragsprotokoll")
        self.resize(980, 560)
        self.profile = profile
        lay = QVBoxLayout(self)
        entries = joblog.read()
        cols = ["start", "duration_s", "result", "kind", "project", "layers", "source", "flame", "cost"]
        tab = QTableWidget(len(entries), len(cols))
        tab.setHorizontalHeaderLabels([joblog.HEADERS[c] for c in cols])
        tab.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        cur = getattr(profile, "job_currency", "€")
        for r, e in enumerate(reversed(entries)):
            for c, key in enumerate(cols):
                v = getattr(e, key)
                if key == "duration_s":
                    s = int(round(v))
                    v = f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"
                elif key == "cost":
                    v = f"{v:.2f} {cur}".replace(".", ",") if v else ""
                tab.setItem(r, c, QTableWidgetItem(str(v)))
        tab.resizeColumnsToContents()
        lay.addWidget(tab, 1)
        t = joblog.totals(entries)
        s = int(t["duration_s"])
        lay.addWidget(QLabel(f"{t['count']} Aufträge ({t['done']} fertig), Laufzeit gesamt "
                             f"{s // 3600}:{s // 60 % 60:02d} h, Kosten gesamt "
                             f"{t['cost']:.2f} {cur}".replace(".", ",")))
        g = QGridLayout()
        self.rate = dspin(0, 10000, 2, 1, f" {cur}/h")
        self.rate.setValue(float(getattr(profile, "job_rate", 0.0)))
        self.rate.setToolTip("Maschinenstundensatz (Abnutzung, Laserdiode, Arbeitszeit …). 0 = nicht berechnen")
        self.watt = dspin(0, 5000, 0, 10, " W")
        self.watt.setValue(float(getattr(profile, "job_power_w", 0.0)))
        self.watt.setToolTip("Leistungsaufnahme von Laser, Absaugung und Luftpumpe zusammen")
        self.kwh = dspin(0, 10, 3, 0.01, f" {cur}/kWh")
        self.kwh.setValue(float(getattr(profile, "job_kwh_price", 0.0)))
        g.addWidget(QLabel("Stundensatz"), 0, 0)
        g.addWidget(self.rate, 0, 1)
        g.addWidget(QLabel("Leistungsaufnahme"), 0, 2)
        g.addWidget(self.watt, 0, 3)
        g.addWidget(QLabel("Strompreis"), 0, 4)
        g.addWidget(self.kwh, 0, 5)
        lay.addLayout(g)
        self.enabled = QCheckBox("Aufträge protokollieren")
        self.enabled.setChecked(bool(getattr(profile, "job_log", True)))
        lay.addWidget(self.enabled)
        lay.addWidget(QLabel(f"Datei: {joblog.LOG_FILE}"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        b_exp = bb.addButton("Als CSV speichern…", QDialogButtonBox.ButtonRole.ActionRole)
        b_clr = bb.addButton("Protokoll leeren…", QDialogButtonBox.ButtonRole.ActionRole)
        b_exp.clicked.connect(self._export)
        b_clr.clicked.connect(self._clear)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply(self, profile):
        profile.job_rate = float(self.rate.value())
        profile.job_power_w = float(self.watt.value())
        profile.job_kwh_price = float(self.kwh.value())
        profile.job_log = self.enabled.isChecked()

    def _export(self):
        import shutil
        from ..core import joblog
        if not os.path.exists(joblog.LOG_FILE):
            QMessageBox.information(self, "Auftragsprotokoll", "Noch keine Aufträge protokolliert.")
            return
        p, _ = QFileDialog.getSaveFileName(self, "Protokoll speichern", os.path.expanduser("~/auftraege.csv"),
                                           "CSV (*.csv)")
        if p:
            shutil.copyfile(joblog.LOG_FILE, p)

    def _clear(self):
        from ..core import joblog
        if QMessageBox.question(self, "Auftragsprotokoll", "Alle protokollierten Aufträge löschen?") \
                == QMessageBox.StandardButton.Yes:
            joblog.clear()
            self.accept()

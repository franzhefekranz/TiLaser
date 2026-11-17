"""Dialoge: Maschineneinstellungen, Text einfügen, G-Code-Anzeige."""
from __future__ import annotations

import os

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                             QSpinBox, QVBoxLayout)

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
        p.bed_w, p.bed_h = self.bed_w.value(), self.bed_h.value()
        p.frame_power = self.frame_power.value()
        p.frame_speed = self.frame_speed.value()
        p.air_on, p.air_off = self.air_on.text().strip(), self.air_off.text().strip()
        p.header, p.footer = self.header.toPlainText(), self.footer.toPlainText()
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

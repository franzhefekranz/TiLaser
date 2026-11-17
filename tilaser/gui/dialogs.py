"""Dialoge: Maschineneinstellungen, Text einfügen, G-Code-Anzeige."""
from __future__ import annotations

import os

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                             QSpinBox, QVBoxLayout, QWidget)

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

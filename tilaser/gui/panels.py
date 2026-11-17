"""Seitenleisten: Ebenen (inkl. Material-Presets) und Objekteigenschaften."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                             QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import storage
from ..core.model import DITHER_LABELS, DITHER_MODES, MODE_LABELS, MODES, Project


def color_icon(color: str, size=14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def dspin(lo, hi, dec=1, step=1.0, suffix="") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class LayerPanel(QWidget):
    layersChanged = pyqtSignal(bool)       # True = Darstellung neu aufbauen

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.presets: list[dict] = storage.load_presets()
        self._loading = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.list = QListWidget()
        self.list.setMinimumHeight(120)
        self.list.currentRowChanged.connect(self._on_select)
        self.list.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.list)

        row = QHBoxLayout()
        self.btn_up = QPushButton("▲")
        self.btn_down = QPushButton("▼")
        self.btn_color = QPushButton("Farbe…")
        self.btn_new = QPushButton("Neu")
        self.btn_clean = QPushButton("Leere entfernen")
        for b in (self.btn_up, self.btn_down):
            b.setFixedWidth(32)
        self.btn_up.setToolTip("Ebene früher ausführen")
        self.btn_down.setToolTip("Ebene später ausführen (Schneiden sollte zuletzt kommen)")
        for b in (self.btn_up, self.btn_down, self.btn_color, self.btn_new, self.btn_clean):
            row.addWidget(b)
        lay.addLayout(row)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        self.btn_color.clicked.connect(self._pick_color)
        self.btn_new.clicked.connect(self._new_layer)
        self.btn_clean.clicked.connect(self._clean)

        # Presets
        pg = QGroupBox("Material-Presets")
        pl = QVBoxLayout(pg)
        self.preset_combo = QComboBox()
        pl.addWidget(self.preset_combo)
        prow = QHBoxLayout()
        self.btn_apply = QPushButton("Anwenden")
        self.btn_save_preset = QPushButton("Speichern…")
        self.btn_del_preset = QPushButton("Löschen")
        for b in (self.btn_apply, self.btn_save_preset, self.btn_del_preset):
            prow.addWidget(b)
        pl.addLayout(prow)
        lay.addWidget(pg)
        self.btn_apply.clicked.connect(self._apply_preset)
        self.btn_save_preset.clicked.connect(self._save_preset)
        self.btn_del_preset.clicked.connect(self._delete_preset)
        self._fill_presets()

        # Einstellungen
        self.box = QGroupBox("Ebeneneinstellungen")
        self.form = QFormLayout(self.box)
        self.f_name = QLineEdit()
        self.f_mode = QComboBox()
        for m in MODES:
            self.f_mode.addItem(MODE_LABELS[m], m)
        self.f_power = dspin(0, 100, 1, 1, " %")
        self.f_min = dspin(0, 100, 1, 1, " %")
        self.f_speed = dspin(1, 100000, 0, 100, " mm/min")
        self.f_passes = QSpinBox()
        self.f_passes.setRange(1, 100)
        self.f_interval = dspin(0.01, 5, 3, 0.01, " mm")
        self.f_angle = dspin(-180, 180, 1, 15, " °")
        self.f_cross = QCheckBox("Kreuzschraffur")
        self.f_dither = QComboBox()
        for d in DITHER_MODES:
            self.f_dither.addItem(DITHER_LABELS[d], d)
        self.f_thresh = QSpinBox()
        self.f_thresh.setRange(1, 254)
        self.f_invert = QCheckBox("Invertieren")
        self.f_overscan = dspin(0, 50, 1, 0.5, " mm")
        self.f_bidir = QCheckBox("Bidirektional")
        self.f_inner = QCheckBox("Innen vor außen")
        self.f_air = QCheckBox("Air Assist (M8/M9)")
        self.f_output = QCheckBox("Ausgeben")

        self.rows = {}
        for key, label, w in (("name", "Name", self.f_name), ("mode", "Modus", self.f_mode),
                              ("power", "Leistung max", self.f_power), ("min", "Leistung min", self.f_min),
                              ("speed", "Geschwindigkeit", self.f_speed), ("passes", "Durchgänge", self.f_passes),
                              ("interval", "Zeilenabstand", self.f_interval), ("angle", "Winkel", self.f_angle),
                              ("cross", "", self.f_cross), ("dither", "Bildmodus", self.f_dither),
                              ("thresh", "Schwellwert", self.f_thresh), ("invert", "", self.f_invert),
                              ("overscan", "Overscan", self.f_overscan), ("bidir", "", self.f_bidir),
                              ("inner", "", self.f_inner), ("air", "", self.f_air), ("output", "", self.f_output)):
            self.form.addRow(label, w)
            self.rows[key] = w
        self.f_interval.setToolTip("Abstand der Schraffur-/Bildzeilen. 0,1 mm ≈ 254 DPI")
        self.f_overscan.setToolTip("Anlaufweg vor/nach jeder Bildzeile – vermeidet dunkle Ränder")
        lay.addWidget(self.box)
        lay.addStretch(1)

        for w in (self.f_power, self.f_min, self.f_speed, self.f_interval, self.f_angle, self.f_overscan):
            w.valueChanged.connect(self._commit)
        self.f_passes.valueChanged.connect(self._commit)
        self.f_thresh.valueChanged.connect(self._commit)
        for w in (self.f_mode, self.f_dither):
            w.currentIndexChanged.connect(self._commit)
        for w in (self.f_cross, self.f_invert, self.f_bidir, self.f_inner, self.f_air, self.f_output):
            w.toggled.connect(self._commit)
        self.f_name.editingFinished.connect(self._commit)
        self.box.setEnabled(False)

    # ------------------------------------------------------------ Liste
    def set_project(self, project: Project):
        self.project = project
        self.refresh()

    def current_layer(self):
        row = self.list.currentRow()
        if self.project and 0 <= row < len(self.project.layers):
            return self.project.layers[row]
        return None

    def refresh(self, keep_id=None):
        cur = keep_id or (self.current_layer().id if self.current_layer() else None)
        self._loading = True
        self.list.clear()
        for l in self.project.layers:
            txt = f"{l.name}  –  {MODE_LABELS[l.mode].split(' ')[0]}  {l.power:g}% · {l.speed:g} mm/min"
            if l.passes > 1:
                txt += f" · {l.passes}×"
            li = QListWidgetItem(color_icon(l.color), txt)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked if l.output else Qt.CheckState.Unchecked)
            li.setData(Qt.ItemDataRole.UserRole, l.id)
            self.list.addItem(li)
        self._loading = False
        ids = [l.id for l in self.project.layers]
        if cur in ids:
            self.list.setCurrentRow(ids.index(cur))
        elif ids:
            self.list.setCurrentRow(0)
        else:
            self.box.setEnabled(False)

    def select_layer(self, layer_id):
        ids = [l.id for l in self.project.layers]
        if layer_id in ids:
            self.list.setCurrentRow(ids.index(layer_id))

    def _on_item_changed(self, li: QListWidgetItem):
        if self._loading:
            return
        l = self.project.layer(li.data(Qt.ItemDataRole.UserRole))
        if l:
            l.output = li.checkState() == Qt.CheckState.Checked
            self._load_form(l)
            self.layersChanged.emit(True)

    def _on_select(self, row):
        l = self.current_layer()
        self.box.setEnabled(l is not None)
        if l:
            self._load_form(l)

    def _load_form(self, l):
        self._loading = True
        self.f_name.setText(l.name)
        self.f_mode.setCurrentIndex(MODES.index(l.mode))
        self.f_power.setValue(l.power)
        self.f_min.setValue(l.min_power)
        self.f_speed.setValue(l.speed)
        self.f_passes.setValue(l.passes)
        self.f_interval.setValue(l.interval)
        self.f_angle.setValue(l.angle)
        self.f_cross.setChecked(l.crosshatch)
        self.f_dither.setCurrentIndex(DITHER_MODES.index(l.dither) if l.dither in DITHER_MODES else 0)
        self.f_thresh.setValue(l.threshold)
        self.f_invert.setChecked(l.invert)
        self.f_overscan.setValue(l.overscan)
        self.f_bidir.setChecked(l.bidirectional)
        self.f_inner.setChecked(l.inner_first)
        self.f_air.setChecked(l.air)
        self.f_output.setChecked(l.output)
        self._loading = False
        self._update_visibility(l.mode, l.dither)

    def _update_visibility(self, mode, dither):
        vis = {
            "min": mode == "raster" and dither == "grayscale",
            "interval": mode in ("fill", "raster"),
            "angle": mode == "fill", "cross": mode == "fill",
            "dither": mode == "raster", "thresh": mode == "raster" and dither == "threshold",
            "invert": mode == "raster", "overscan": mode == "raster",
            "bidir": mode in ("fill", "raster"), "inner": mode == "cut",
        }
        for key, v in vis.items():
            self.form.setRowVisible(self.rows[key], v)

    def _commit(self, *_):
        if self._loading:
            return
        l = self.current_layer()
        if not l:
            return
        old_mode, old_dither, old_iv, old_th, old_inv = l.mode, l.dither, l.interval, l.threshold, l.invert
        l.name = self.f_name.text().strip() or l.name
        l.mode = self.f_mode.currentData()
        l.power = self.f_power.value()
        l.min_power = min(self.f_min.value(), l.power)
        l.speed = self.f_speed.value()
        l.passes = self.f_passes.value()
        l.interval = self.f_interval.value()
        l.angle = self.f_angle.value()
        l.crosshatch = self.f_cross.isChecked()
        l.dither = self.f_dither.currentData()
        l.threshold = self.f_thresh.value()
        l.invert = self.f_invert.isChecked()
        l.overscan = self.f_overscan.value()
        l.bidirectional = self.f_bidir.isChecked()
        l.inner_first = self.f_inner.isChecked()
        l.air = self.f_air.isChecked()
        l.output = self.f_output.isChecked()
        self._update_visibility(l.mode, l.dither)
        visual = (old_mode, old_dither, old_iv, old_th, old_inv) != (l.mode, l.dither, l.interval, l.threshold, l.invert)
        self.refresh(l.id)
        self.layersChanged.emit(visual or True)

    def _move(self, d):
        l = self.current_layer()
        if l:
            self.project.move_layer(l.id, d)
            self.refresh(l.id)
            self.layersChanged.emit(False)

    def _pick_color(self):
        l = self.current_layer()
        if not l:
            return
        c = QColorDialog.getColor(QColor(l.color), self, "Ebenenfarbe")
        if c.isValid():
            l.color = c.name()
            self.refresh(l.id)
            self.layersChanged.emit(True)

    def _new_layer(self):
        l = self.project.add_layer(f"Ebene {len(self.project.layers) + 1}")
        self.refresh(l.id)
        self.layersChanged.emit(False)

    def _clean(self):
        self.project.remove_unused_layers()
        self.refresh()
        self.layersChanged.emit(True)

    # ------------------------------------------------------------ Presets
    def _fill_presets(self):
        self.preset_combo.clear()
        for p in self.presets:
            self.preset_combo.addItem(f"{p['name']}  [{MODE_LABELS.get(p.get('mode', 'cut'), '')}]")
            self.preset_combo.setItemData(self.preset_combo.count() - 1, p.get("material", ""),
                                          Qt.ItemDataRole.ToolTipRole)

    def _apply_preset(self):
        l = self.current_layer()
        i = self.preset_combo.currentIndex()
        if not l or i < 0:
            return
        storage.apply_preset(l, self.presets[i])
        self._load_form(l)
        self.refresh(l.id)
        self.layersChanged.emit(True)

    def _save_preset(self):
        l = self.current_layer()
        if not l:
            return
        name, ok = QInputDialog.getText(self, "Preset speichern", "Name des Presets:", text=l.name)
        if not ok or not name.strip():
            return
        material, ok = QInputDialog.getText(self, "Preset speichern", "Material / Bemerkung:")
        if not ok:
            return
        new = storage.preset_from_layer(name.strip(), material.strip(), l)
        self.presets = [p for p in self.presets if p["name"] != new["name"]] + [new]
        storage.save_presets(self.presets)
        self._fill_presets()
        self.preset_combo.setCurrentIndex(len(self.presets) - 1)

    def _delete_preset(self):
        i = self.preset_combo.currentIndex()
        if i < 0:
            return
        if QMessageBox.question(self, "Preset löschen", f"Preset „{self.presets[i]['name']}“ löschen?") \
                == QMessageBox.StandardButton.Yes:
            del self.presets[i]
            storage.save_presets(self.presets)
            self._fill_presets()


class ObjectPanel(QWidget):
    changed = pyqtSignal(str, bool)          # Item-ID, Geometrie/Ebene geändert
    action = pyqtSignal(str)                 # 'center', 'origin', 'duplicate', 'delete'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.item = None
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.title = QLabel("Kein Objekt ausgewählt")
        self.title.setStyleSheet("font-weight: bold")
        lay.addWidget(self.title)
        form = QFormLayout()
        self.f_x = dspin(-5000, 5000, 2, 1, " mm")
        self.f_y = dspin(-5000, 5000, 2, 1, " mm")
        self.f_w = dspin(0.01, 5000, 2, 1, " mm")
        self.f_h = dspin(0.01, 5000, 2, 1, " mm")
        self.f_scale = dspin(0.1, 10000, 2, 1, " %")
        self.f_rot = dspin(-360, 360, 1, 15, " °")
        self.f_mirror = QCheckBox("Horizontal spiegeln")
        self.f_layer = QComboBox()
        form.addRow("X (Mitte)", self.f_x)
        form.addRow("Y (Mitte)", self.f_y)
        form.addRow("Breite", self.f_w)
        form.addRow("Höhe", self.f_h)
        form.addRow("Skalierung", self.f_scale)
        form.addRow("Drehung", self.f_rot)
        form.addRow("", self.f_mirror)
        form.addRow("Ebene", self.f_layer)
        lay.addLayout(form)
        row = QHBoxLayout()
        for key, text in (("center", "Zentrieren"), ("origin", "An 0/0"), ("duplicate", "Duplizieren"),
                          ("delete", "Löschen")):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            row.addWidget(b)
        lay.addLayout(row)
        lay.addStretch(1)
        self.f_x.valueChanged.connect(self._pos)
        self.f_y.valueChanged.connect(self._pos)
        self.f_w.valueChanged.connect(lambda v: self._size(v, None))
        self.f_h.valueChanged.connect(lambda v: self._size(None, v))
        self.f_scale.valueChanged.connect(self._scale)
        self.f_rot.valueChanged.connect(self._rot)
        self.f_mirror.toggled.connect(self._mirror)
        self.f_layer.activated.connect(self._layer)
        self.setEnabled(False)

    def set_project(self, project):
        self.project = project
        self.set_item(None)

    def set_item(self, item):
        self.item = item
        self.setEnabled(item is not None)
        self.refresh()

    def refresh(self):
        it = self.item
        self._loading = True
        if it is None:
            self.title.setText("Kein Objekt ausgewählt")
        else:
            self.title.setText(f"{it.name}  ({'Bild' if it.kind == 'raster' else 'Vektor'})")
            w, h = it.local_size()
            self.f_x.setValue(it.x)
            self.f_y.setValue(it.y)
            self.f_w.setValue(w * it.scale)
            self.f_h.setValue(h * it.scale)
            self.f_scale.setValue(it.scale * 100)
            self.f_rot.setValue(it.rotation)
            self.f_mirror.setChecked(it.mirror_x)
            self.f_layer.clear()
            ids = it.layer_ids()
            for l in self.project.layers:
                if it.kind == "raster" and l.mode != "raster":
                    continue
                self.f_layer.addItem(color_icon(l.color), l.name, l.id)
            if len(ids) == 1:
                idx = self.f_layer.findData(next(iter(ids)))
                self.f_layer.setCurrentIndex(idx)
            else:
                self.f_layer.insertItem(0, "— mehrere Ebenen —", None)
                self.f_layer.setCurrentIndex(0)
        self._loading = False

    def _emit(self, geom=False):
        self.changed.emit(self.item.id, geom)

    def _pos(self, *_):
        if self._loading or not self.item:
            return
        self.item.x, self.item.y = self.f_x.value(), self.f_y.value()
        self._emit()

    def _size(self, w, h):
        if self._loading or not self.item:
            return
        lw, lh = self.item.local_size()
        if w is not None and lw > 0:
            self.item.scale = w / lw
        elif h is not None and lh > 0:
            self.item.scale = h / lh
        self.refresh()
        self._emit()

    def _scale(self, v):
        if self._loading or not self.item:
            return
        self.item.scale = v / 100.0
        self.refresh()
        self._emit()

    def _rot(self, v):
        if self._loading or not self.item:
            return
        self.item.rotation = v
        self._emit()

    def _mirror(self, on):
        if self._loading or not self.item:
            return
        self.item.mirror_x = on
        self._emit()

    def _layer(self, idx):
        if self._loading or not self.item:
            return
        lid = self.f_layer.itemData(idx)
        if lid is None:
            return
        if self.item.kind == "raster":
            self.item.layer_id = lid
        else:
            for vp in self.item.paths:
                vp.layer_id = lid
        self.refresh()
        self._emit(True)

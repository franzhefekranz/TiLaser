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
    aboutToChange = pyqtSignal(str, str)   # Beschreibung, Zusammenfassungs-Schlüssel (Rückgängig)

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
        self.machine_filter = QComboBox()
        self.machine_filter.setToolTip("Presets nach Maschine filtern")
        self.preset_combo = QComboBox()
        self.preset_combo.setMaxVisibleItems(25)
        pl.addWidget(self.machine_filter)
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
        self._fill_machine_filter()
        self.machine_filter.currentIndexChanged.connect(lambda _: self._fill_presets())
        self._fill_presets()

        # Einstellungen
        self.box = QGroupBox("Ebeneneinstellungen")
        self.form = QFormLayout(self.box)
        oprow = QHBoxLayout()
        self.op_combo = QComboBox()
        self.op_combo.setToolTip("Hauptarbeitsgang oder Unterebene (weiterer Arbeitsgang auf derselben Geometrie)")
        self.btn_add_sub = QPushButton("+")
        self.btn_add_sub.setFixedWidth(28)
        self.btn_add_sub.setToolTip("Unterebene hinzufügen, z. B. erst gravieren, dann schneiden")
        self.btn_del_sub = QPushButton("−")
        self.btn_del_sub.setFixedWidth(28)
        self.btn_del_sub.setToolTip("Gewählte Unterebene entfernen")
        oprow.addWidget(self.op_combo, 1)
        oprow.addWidget(self.btn_add_sub)
        oprow.addWidget(self.btn_del_sub)
        self.form.addRow("Arbeitsgang", oprow)
        self._op_idx = 0
        self._op_layer = None
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
        self.f_kerf = dspin(-2, 2, 3, 0.01, " mm")
        self.f_kerf.setToolTip("Schnittbreite. Außenkonturen werden um die Hälfte nach außen, Löcher nach innen versetzt,\n"
                               "damit Teile maßhaltig werden (z. B. Presspassungen). 0 = aus")
        self.f_tabs = QSpinBox()
        self.f_tabs.setRange(0, 50)
        self.f_tabs.setToolTip("Haltestege je Kontur: kleine Stege bleiben stehen, damit Teile nicht herausfallen. 0 = aus")
        self.f_tab_w = dspin(0.05, 10, 2, 0.1, " mm")
        self.f_tabs_outer = QCheckBox("Stege nur an Außenkonturen")
        self.f_bright = dspin(-100, 100, 0, 5, "")
        self.f_contrast = dspin(-100, 100, 0, 5, "")
        self.f_gamma = dspin(0.1, 5, 2, 0.1, "")
        self.f_z = dspin(0, 20, 2, 0.1, " mm")
        self.f_z.setToolTip("Fokus nach jedem Durchgang um diesen Wert absenken (nur mit motorisierter Z-Achse). 0 = aus")
        self.f_output = QCheckBox("Ausgeben")

        self.rows = {}
        for key, label, w in (("name", "Name", self.f_name), ("mode", "Modus", self.f_mode),
                              ("power", "Leistung max", self.f_power), ("min", "Leistung min", self.f_min),
                              ("speed", "Geschwindigkeit", self.f_speed), ("passes", "Durchgänge", self.f_passes),
                              ("interval", "Zeilenabstand", self.f_interval), ("angle", "Winkel", self.f_angle),
                              ("cross", "", self.f_cross), ("dither", "Bildmodus", self.f_dither),
                              ("thresh", "Schwellwert", self.f_thresh), ("invert", "", self.f_invert),
                              ("overscan", "Overscan", self.f_overscan), ("bidir", "", self.f_bidir),
                              ("inner", "", self.f_inner), ("kerf", "Kerf-Ausgleich", self.f_kerf),
                              ("tabs", "Haltestege", self.f_tabs), ("tab_w", "Stegbreite", self.f_tab_w),
                              ("tabs_outer", "", self.f_tabs_outer), ("bright", "Helligkeit", self.f_bright),
                              ("contrast", "Kontrast", self.f_contrast), ("gamma", "Gamma", self.f_gamma),
                              ("z", "Z-Schritt je Durchgang", self.f_z),
                              ("air", "", self.f_air), ("output", "", self.f_output)):
            self.form.addRow(label, w)
            self.rows[key] = w
        self.f_interval.setToolTip("Abstand der Schraffur-/Bildzeilen. 0,1 mm ≈ 254 DPI")
        self.f_overscan.setToolTip("Anlaufweg vor/nach jeder Bildzeile – vermeidet dunkle Ränder")
        lay.addWidget(self.box)
        lay.addStretch(1)

        for w in (self.f_power, self.f_min, self.f_speed, self.f_interval, self.f_angle, self.f_overscan,
                  self.f_kerf, self.f_tab_w, self.f_bright, self.f_contrast, self.f_gamma, self.f_z):
            w.valueChanged.connect(self._commit)
        self.f_passes.valueChanged.connect(self._commit)
        self.f_tabs.valueChanged.connect(self._commit)
        self.f_tabs_outer.toggled.connect(self._commit)
        self.f_thresh.valueChanged.connect(self._commit)
        for w in (self.f_mode, self.f_dither):
            w.currentIndexChanged.connect(self._commit)
        for w in (self.f_cross, self.f_invert, self.f_bidir, self.f_inner, self.f_air, self.f_output):
            w.toggled.connect(self._commit)
        self.f_name.editingFinished.connect(self._commit)
        self.op_combo.currentIndexChanged.connect(self._on_op)
        self.btn_add_sub.clicked.connect(self._add_sub)
        self.btn_del_sub.clicked.connect(self._del_sub)
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

    def current_op(self):
        l = self.current_layer()
        if l is None:
            return None
        i = self._op_idx
        return l if i == 0 or i > len(l.subs) else l.subs[i - 1]

    def _fill_ops(self, l):
        self._loading = True
        if self._op_layer is not l:
            self._op_idx = 0
            self._op_layer = l
        self._op_idx = min(self._op_idx, len(l.subs))
        self.op_combo.clear()
        self.op_combo.addItem(f"Haupt: {MODE_LABELS[l.mode]}")
        for k, sub in enumerate(l.subs):
            self.op_combo.addItem(f"Unterebene {k + 1}: {MODE_LABELS[sub.mode]}"
                                  + ("" if sub.output else " (aus)"))
        self.op_combo.setCurrentIndex(self._op_idx)
        self.btn_del_sub.setEnabled(self._op_idx > 0)
        self._loading = False

    def _on_op(self, idx):
        if self._loading or idx < 0:
            return
        self._op_idx = idx
        self.btn_del_sub.setEnabled(idx > 0)
        op = self.current_op()
        if op is not None:
            self._load_form(op)

    def _add_sub(self):
        l = self.current_layer()
        if not l:
            return
        self.aboutToChange.emit("Unterebene hinzufügen", "")
        l.add_sub("cut" if l.mode != "cut" else "fill")
        self._op_idx = len(l.subs)
        self.refresh(l.id)
        self.layersChanged.emit(False)

    def _del_sub(self):
        l = self.current_layer()
        if not l or self._op_idx == 0:
            return
        self.aboutToChange.emit("Unterebene entfernen", "")
        del l.subs[self._op_idx - 1]
        self._op_idx = 0
        self.refresh(l.id)
        self.layersChanged.emit(False)

    def refresh(self, keep_id=None):
        cur = keep_id or (self.current_layer().id if self.current_layer() else None)
        self._loading = True
        self.list.clear()
        for l in self.project.layers:
            txt = f"{l.name}  –  {MODE_LABELS[l.mode].split(' ')[0]}  {l.power:g}% · {l.speed:g} mm/min"
            if l.passes > 1:
                txt += f" · {l.passes}×"
            if l.subs:
                txt += f"  +{len(l.subs)} Unterebene{'n' if len(l.subs) > 1 else ''}"
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
            self.aboutToChange.emit("Ebene ein/aus", "")
            l.output = li.checkState() == Qt.CheckState.Checked
            self._load_form(l)
            self.layersChanged.emit(True)

    def _on_select(self, row):
        l = self.current_layer()
        self.box.setEnabled(l is not None)
        if l:
            self._fill_ops(l)
            self._load_form(self.current_op())

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
        self.f_kerf.setValue(l.kerf)
        self.f_tabs.setValue(l.tabs)
        self.f_tab_w.setValue(l.tab_width)
        self.f_tabs_outer.setChecked(l.tabs_outer_only)
        self.f_bright.setValue(l.brightness)
        self.f_contrast.setValue(l.contrast)
        self.f_gamma.setValue(l.gamma)
        self.f_z.setValue(l.z_step)
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
            "kerf": mode == "cut", "tabs": mode == "cut", "tab_w": mode == "cut", "tabs_outer": mode == "cut",
            "bright": mode == "raster", "contrast": mode == "raster", "gamma": mode == "raster",
        }
        for key, v in vis.items():
            self.form.setRowVisible(self.rows[key], v)

    def _commit(self, *_):
        if self._loading:
            return
        layer = self.current_layer()
        l = self.current_op()
        if not l:
            return
        self.aboutToChange.emit("Ebene ändern", f"layer:{l.id}")
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
        l.kerf = self.f_kerf.value()
        l.tabs = self.f_tabs.value()
        l.tab_width = self.f_tab_w.value()
        l.tabs_outer_only = self.f_tabs_outer.isChecked()
        l.brightness = self.f_bright.value()
        l.contrast = self.f_contrast.value()
        l.gamma = self.f_gamma.value()
        l.z_step = self.f_z.value()
        self._update_visibility(l.mode, l.dither)
        visual = (old_mode, old_dither, old_iv, old_th, old_inv) != (l.mode, l.dither, l.interval, l.threshold, l.invert)
        self.refresh(layer.id)
        self.layersChanged.emit(visual or True)

    def _move(self, d):
        l = self.current_layer()
        if l:
            self.aboutToChange.emit("Ebenenreihenfolge", "")
            self.project.move_layer(l.id, d)
            self.refresh(l.id)
            self.layersChanged.emit(False)

    def _pick_color(self):
        l = self.current_layer()
        if not l:
            return
        c = QColorDialog.getColor(QColor(l.color), self, "Ebenenfarbe")
        if c.isValid():
            self.aboutToChange.emit("Ebenenfarbe", "")
            l.color = c.name()
            self.refresh(l.id)
            self.layersChanged.emit(True)

    def _new_layer(self):
        self.aboutToChange.emit("Neue Ebene", "")
        l = self.project.add_layer(f"Ebene {len(self.project.layers) + 1}")
        self.refresh(l.id)
        self.layersChanged.emit(False)

    def _clean(self):
        self.aboutToChange.emit("Leere Ebenen entfernen", "")
        self.project.remove_unused_layers()
        self.refresh()
        self.layersChanged.emit(True)

    # ------------------------------------------------------------ Presets
    def _fill_machine_filter(self, select: str | None = None):
        cur = select or self.machine_filter.currentData()
        self.machine_filter.blockSignals(True)
        self.machine_filter.clear()
        self.machine_filter.addItem("Alle Maschinen", "")
        for m in sorted({p.get("machine", "") for p in self.presets if p.get("machine")}):
            self.machine_filter.addItem(m, m)
        i = self.machine_filter.findData(cur) if cur else 0
        self.machine_filter.setCurrentIndex(max(0, i))
        self.machine_filter.blockSignals(False)

    def set_machine(self, name: str):
        """Filter auf die Maschine des aktuellen Profils setzen (falls Presets dafür existieren)."""
        if self.machine_filter.findData(name) >= 0:
            self._fill_machine_filter(name)
            self._fill_presets()

    def _fill_presets(self):
        flt = self.machine_filter.currentData() or ""
        self.preset_combo.clear()
        for i, p in enumerate(self.presets):
            if flt and p.get("machine", "") not in (flt, ""):
                continue
            self.preset_combo.addItem(f"{p['name']}  [{MODE_LABELS.get(p.get('mode', 'cut'), '')}]", i)
            self.preset_combo.setItemData(self.preset_combo.count() - 1, p.get("material", ""),
                                          Qt.ItemDataRole.ToolTipRole)

    def _preset_index(self):
        d = self.preset_combo.currentData()
        return d if isinstance(d, int) and 0 <= d < len(self.presets) else None

    def _apply_preset(self):
        l = self.current_layer()
        i = self._preset_index()
        if not l or i is None:
            return
        self.aboutToChange.emit("Preset anwenden", "")
        op = self.current_op()
        storage.apply_preset(op, self.presets[i])
        self._load_form(op)
        self.refresh(l.id)
        self.layersChanged.emit(True)

    def _save_preset(self):
        l = self.current_op()
        if not l:
            return
        name, ok = QInputDialog.getText(self, "Preset speichern", "Name des Presets:", text=l.name)
        if not ok or not name.strip():
            return
        material, ok = QInputDialog.getText(self, "Preset speichern", "Material / Bemerkung:")
        if not ok:
            return
        new = storage.preset_from_layer(name.strip(), material.strip(), l, self.machine_filter.currentData() or "")
        self.presets = [p for p in self.presets if p["name"] != new["name"]] + [new]
        storage.save_presets(self.presets)
        self._fill_machine_filter()
        self._fill_presets()
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(len(self.presets) - 1))

    def _delete_preset(self):
        i = self._preset_index()
        if i is None:
            return
        if QMessageBox.question(self, "Preset löschen", f"Preset „{self.presets[i]['name']}“ löschen?") \
                == QMessageBox.StandardButton.Yes:
            del self.presets[i]
            storage.save_presets(self.presets)
            self._fill_machine_filter()
            self._fill_presets()


class ObjectPanel(QWidget):
    changed = pyqtSignal(str, bool)          # Item-ID, Geometrie/Ebene geändert
    aboutToChange = pyqtSignal(str, str)
    action = pyqtSignal(str)                 # 'center', 'origin', 'duplicate', 'delete'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.item = None
        self._loading = False
        # Horizontale Leiste über der Arbeitsfläche
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)
        self.title = QLabel("Kein Objekt ausgewählt")
        self.title.setStyleSheet("font-weight: bold")
        self.title.setMinimumWidth(150)
        self.title.setMaximumWidth(220)
        self.f_x = dspin(-5000, 5000, 2, 1, " mm")
        self.f_y = dspin(-5000, 5000, 2, 1, " mm")
        self.f_w = dspin(0.01, 5000, 2, 1, " mm")
        self.f_h = dspin(0.01, 5000, 2, 1, " mm")
        self.f_scale = dspin(0.1, 10000, 1, 1, " %")
        self.f_rot = dspin(-360, 360, 1, 15, " °")
        self.f_mirror = QCheckBox("Spiegeln")
        self.f_layer = QComboBox()
        self.f_layer.setMinimumWidth(150)
        self.f_x.setToolTip("Mittelpunkt X")
        self.f_y.setToolTip("Mittelpunkt Y")
        lay.addWidget(self.title)
        for label, w in (("X", self.f_x), ("Y", self.f_y), ("B", self.f_w), ("H", self.f_h),
                         ("Skal.", self.f_scale), ("∠", self.f_rot)):
            lab = QLabel(label)
            lab.setContentsMargins(6, 0, 0, 0)
            lay.addWidget(lab)
            lay.addWidget(w)
        lay.addWidget(self.f_mirror)
        lab = QLabel("Ebene")
        lab.setContentsMargins(6, 0, 0, 0)
        lay.addWidget(lab)
        lay.addWidget(self.f_layer)
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
            self.f_layer.clear()
        else:
            self.title.setText(it.name)
            self.title.setToolTip(f"{it.name} ({'Bild' if it.kind == 'raster' else f'Vektor, {len(it.paths)} Pfade'})")
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

    def _before(self, what):
        self.aboutToChange.emit(what, f"obj:{self.item.id}:{what}")

    def _emit(self, geom=False):
        self.changed.emit(self.item.id, geom)

    def _pos(self, *_):
        if self._loading or not self.item:
            return
        self._before("Position")
        self.item.x, self.item.y = self.f_x.value(), self.f_y.value()
        self._emit()

    def _size(self, w, h):
        if self._loading or not self.item:
            return
        self._before("Größe")
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
        self._before("Skalierung")
        self.item.scale = v / 100.0
        self.refresh()
        self._emit()

    def _rot(self, v):
        if self._loading or not self.item:
            return
        self._before("Drehung")
        self.item.rotation = v
        self._emit()

    def _mirror(self, on):
        if self._loading or not self.item:
            return
        self._before("Spiegeln")
        self.item.mirror_x = on
        self._emit()

    def _layer(self, idx):
        if self._loading or not self.item:
            return
        self._before("Ebene")
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

"""Live-Kamera zum Beobachten des Lasers.

* ``LiveCamera``  – besitzt Kamera, Aufnahme-Session, Video-Sink und Recorder (QtMultimedia)
* ``CameraView``  – zeichnet das aktuelle Bild seitenrichtig skaliert (optional gedreht)
* ``CameraWindow`` – großes, frei verschiebbares Fenster (z. B. auf einem zweiten Bildschirm)
* ``CameraPanel`` – Tab „Kamera“ in der Seitenleiste, mit Flammenwächter (``core.flame``)

QtMultimedia wird erst beim Einschalten importiert, damit TiLaser auch ohne Kameraunterstützung startet.
"""
from __future__ import annotations

import os
import time

import numpy as np
from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from ..core.flame import FlameDetector

FILE_DEVICE = "__file__"


def qimage_to_array(img) -> np.ndarray | None:
    """QImage -> numpy RGB (h, w, 3)."""
    if img is None or img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    ptr = img.bits()
    ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(h, bpl)[:, : w * 3].reshape(h, w, 3).copy()


def default_media_dir() -> str:
    try:
        from PyQt6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    except Exception:
        base = ""
    if not isinstance(base, str) or not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "TiLaser")


def video_devices() -> list[str]:
    try:
        from PyQt6.QtMultimedia import QMediaDevices
        return [d.description() for d in QMediaDevices.videoInputs()]
    except Exception:
        return []


# --------------------------------------------------------------------------- Kamera
class LiveCamera(QObject):
    frameReady = pyqtSignal(object)        # QImage (gedrosselt auf max_fps)
    activeChanged = pyqtSignal(bool)
    recordingChanged = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(self, parent=None, max_fps: float = 15.0):
        super().__init__(parent)
        self.cam = self.session = self.sink = self.rec = None
        self.device = ""
        self.image = None                  # letztes QImage
        self.max_fps = max_fps
        self._last = 0.0
        self._rec_on = False
        self._stop_after_rec = False
        self.record_file = ""

    # -- Zustand
    @property
    def running(self) -> bool:
        try:
            return self.cam is not None and bool(self.cam.isActive())
        except Exception:
            return False

    @property
    def recording(self) -> bool:
        return self._rec_on

    # -- Steuerung
    def start(self, device_desc: str) -> bool:
        self.stop()
        try:
            from PyQt6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink
        except Exception as e:                       # pragma: no cover - abhängig von der Installation
            self.error.emit(f"QtMultimedia ist nicht verfügbar: {e}")
            return False
        devs = QMediaDevices.videoInputs()
        dev = next((d for d in devs if d.description() == device_desc), devs[0] if devs else None)
        if dev is None:
            self.error.emit("Keine Kamera gefunden.")
            return False
        self.device = dev.description()
        self.cam = QCamera(dev)
        self.session = QMediaCaptureSession()
        self.session.setCamera(self.cam)
        self.sink = QVideoSink()
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)
        self.cam.errorOccurred.connect(lambda _e, msg: self.error.emit(msg or "Kamerafehler"))
        self.cam.activeChanged.connect(self.activeChanged.emit)
        self.cam.start()
        return True

    def stop(self, force: bool = False):
        if self.cam is None:
            return
        if self._rec_on and not force:          # Video erst sauber abschließen, dann Kamera aus
            self._stop_after_rec = True
            self.rec.stop()
            return
        self._stop_after_rec = False
        self.stop_recording()
        try:
            self.cam.stop()
        except Exception:
            pass
        self.cam = self.session = self.sink = self.rec = None
        self.image = None
        self.activeChanged.emit(False)

    def _frame(self, frame):
        now = time.monotonic()
        if now - self._last < 1.0 / max(1.0, self.max_fps):
            return
        self._last = now
        try:
            img = frame.toImage()
        except Exception:
            return
        if img is None or img.isNull():
            return
        self.image = img
        self.frameReady.emit(img)

    # -- Foto / Video
    def latest_array(self) -> np.ndarray | None:
        return qimage_to_array(self.image)

    def save_photo(self, path: str) -> bool:
        return self.image is not None and not self.image.isNull() and bool(self.image.save(path))

    def start_recording(self, path: str) -> bool:
        if self.session is None:
            self.error.emit("Kamera ist nicht eingeschaltet.")
            return False
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QMediaFormat, QMediaRecorder
        if self.rec is None:
            self.rec = QMediaRecorder()
            self.session.setRecorder(self.rec)
            self.rec.errorOccurred.connect(lambda _e, msg: self._rec_error(msg))
            self.rec.recorderStateChanged.connect(self._rec_state)
        fmt = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
        if QMediaFormat.VideoCodec.H264 in fmt.supportedVideoCodecs(QMediaFormat.ConversionMode.Encode):
            fmt.setVideoCodec(QMediaFormat.VideoCodec.H264)
            self.rec.setMediaFormat(fmt)
        self.rec.setQuality(QMediaRecorder.Quality.NormalQuality)
        self.rec.setOutputLocation(QUrl.fromLocalFile(path))
        self.record_file = path
        self.rec.record()
        return True

    def stop_recording(self):
        if self.rec is not None and self._rec_on:
            self.rec.stop()

    def actual_record_file(self) -> str:
        try:
            loc = self.rec.actualLocation().toLocalFile() if self.rec is not None else ""
        except Exception:
            loc = ""
        return loc or self.record_file

    def _rec_state(self, st):
        from PyQt6.QtMultimedia import QMediaRecorder
        on = st == QMediaRecorder.RecorderState.RecordingState
        if on != self._rec_on:
            self._rec_on = on
            self.recordingChanged.emit(on)
        if not on and self._stop_after_rec:
            self.stop(force=True)

    def _rec_error(self, msg):
        self.error.emit(f"Videoaufnahme: {msg or 'unbekannter Fehler'}")


# --------------------------------------------------------------------------- Anzeige
class CameraView(QWidget):
    doubleClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None
        self.rotation = 0
        self.overlay = ""
        self.mask = None                   # QImage (rot, halbtransparent) über dem Bild
        self.placeholder = "Kamera aus"
        self.setMinimumSize(200, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, img):
        self.image = img
        self.update()

    def clear(self, text: str = "Kamera aus"):
        self.image = None
        self.placeholder = text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(24, 24, 24))
        img = self.image
        if img is not None and not img.isNull():
            iw, ih = img.width(), img.height()
            rw, rh = (ih, iw) if self.rotation in (90, 270) else (iw, ih)
            s = min(W / rw, H / rh)
            p.save()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.translate(W / 2, H / 2)
            p.rotate(self.rotation)
            p.scale(s, s)
            p.drawImage(QRectF(-iw / 2, -ih / 2, iw, ih), img)
            if self.mask is not None:
                p.drawImage(QRectF(-iw / 2, -ih / 2, iw, ih), self.mask)
            p.restore()
        else:
            p.setPen(QColor(170, 170, 170))
            p.drawText(QRectF(0, 0, W, H), int(Qt.AlignmentFlag.AlignCenter), self.placeholder)
        if self.overlay:
            f = QFont(self.font())
            f.setBold(True)
            p.setFont(f)
            p.fillRect(QRectF(6, 6, 9 * len(self.overlay) + 16, 24), QColor(0, 0, 0, 150))
            p.setPen(QColor(255, 80, 80))
            p.drawText(QRectF(14, 6, 9 * len(self.overlay) + 8, 24),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.overlay)
        p.end()

    def mouseDoubleClickEvent(self, _e):
        self.doubleClicked.emit()


class CameraWindow(QWidget):
    """Großes Kamerafenster; F11 = Vollbild, Esc = Vollbild verlassen."""
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("TiLaser – Kamera")
        self.resize(960, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = CameraView(self)
        self.view.doubleClicked.connect(self._toggle_full)
        lay.addWidget(self.view)

    def _toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_F11:
            self._toggle_full()
        elif e.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self.closed.emit()
        e.accept()


# --------------------------------------------------------------------------- Tab
class CameraPanel(QWidget):
    """Tab „Kamera“: Live-Bild, Kamerawahl, Foto/Video, Automatik."""
    profileChanged = pyqtSignal()
    backgroundToggled = pyqtSignal(bool)
    flameDetected = pyqtSignal(str)        # Pfad des Beweisfotos ('' wenn nicht gespeichert)

    def __init__(self, live: LiveCamera, profile, parent=None):
        super().__init__(parent)
        self.live, self.profile = live, profile
        self.window_ = None
        self._job_rec = False              # Aufnahme wurde vom Auftrag gestartet
        self._pending_rec = ""             # Aufnahme starten, sobald die Kamera läuft
        self._rec_t0 = 0.0
        self._auto_started = False
        self.flame = FlameDetector()
        self._flame_on = False             # Wächter beobachtet gerade (Auftrag oder Test)
        self._flame_test = False
        self._flame_t = 0.0
        self.flame_ratio = 0.0

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.dev = QComboBox()
        self.dev.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.dev.setMinimumContentsLength(12)
        self.dev.activated.connect(self._dev_changed)
        b_ref = QPushButton("⟳")
        b_ref.setToolTip("Kameraliste aktualisieren")
        b_ref.setFixedWidth(32)
        b_ref.clicked.connect(self.refresh_devices)
        self.btn_on = QPushButton("▶ Einschalten")
        self.btn_on.setCheckable(True)
        self.btn_on.clicked.connect(self._toggle)
        top.addWidget(self.dev, 1)
        top.addWidget(b_ref)
        top.addWidget(self.btn_on)
        lay.addLayout(top)

        self.view = CameraView()
        self.view.setMinimumHeight(220)
        self.view.doubleClicked.connect(self.open_window)
        lay.addWidget(self.view, 1)

        g = QGridLayout()
        self.btn_photo = QPushButton("Foto speichern")
        self.btn_photo.clicked.connect(self.save_photo)
        self.btn_rec = QPushButton("● Video aufnehmen")
        self.btn_rec.setCheckable(True)
        self.btn_rec.clicked.connect(self._rec_clicked)
        self.btn_win = QPushButton("Großes Fenster")
        self.btn_win.setToolTip("Live-Bild in einem eigenen Fenster (auch per Doppelklick aufs Bild)")
        self.btn_win.clicked.connect(self.open_window)
        self.btn_rot = QPushButton("Drehen 90°")
        self.btn_rot.setToolTip("Nur die Anzeige drehen (z. B. bei hochkant montierter Kamera)")
        self.btn_rot.clicked.connect(self._rotate)
        g.addWidget(self.btn_photo, 0, 0)
        g.addWidget(self.btn_rec, 0, 1)
        g.addWidget(self.btn_win, 1, 0)
        g.addWidget(self.btn_rot, 1, 1)
        lay.addLayout(g)

        self.cb_auto = QCheckBox("Bei Auftragsstart automatisch einschalten")
        self.cb_rec = QCheckBox("Jeden Auftrag als Video aufnehmen")
        self.cb_bg = QCheckBox("Live-Bild als Hintergrund (entzerrt, alle 2 s)")
        self.cb_bg.setToolTip("Nur wenn die Kamera ausgerichtet ist (Maschine → Kamera → Kamera ausrichten)")
        for cb in (self.cb_auto, self.cb_rec, self.cb_bg):
            lay.addWidget(cb)
        self.cb_auto.toggled.connect(lambda v: self._set("camera_live_auto", v))
        self.cb_rec.toggled.connect(lambda v: self._set("camera_live_record", v))
        self.cb_bg.toggled.connect(self._bg_toggled)

        d = QHBoxLayout()
        self.lbl_dir = QLabel()
        self.lbl_dir.setWordWrap(True)
        b_dir = QPushButton("Ordner…")
        b_dir.clicked.connect(self._choose_dir)
        d.addWidget(QLabel("Speicherort:"))
        d.addWidget(self.lbl_dir, 1)
        d.addWidget(b_dir)
        lay.addLayout(d)
        lay.addWidget(self._build_flame_box())
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        lay.addWidget(self.lbl_status)

        self.live.frameReady.connect(self._on_frame)
        self.live.activeChanged.connect(self._on_active)
        self.live.recordingChanged.connect(self._on_recording)
        self.live.error.connect(self._on_error)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._update_overlay)
        self.load_profile()
        self.refresh_devices()
        self._update_buttons()

    # -- Profil
    def load_profile(self):
        p = self.profile
        for cb, key in ((self.cb_auto, "camera_live_auto"), (self.cb_rec, "camera_live_record"),
                        (self.cb_bg, "camera_live_bg")):
            cb.blockSignals(True)
            cb.setChecked(bool(getattr(p, key, False)))
            cb.blockSignals(False)
        self.view.rotation = int(getattr(p, "camera_live_rot", 0)) % 360
        self.lbl_dir.setText(self.media_dir())
        for w in (self.cb_flame, self.cmb_flame, self.sl_flame, self.sp_hold, self.cb_fshow):
            w.blockSignals(True)
        self.cb_flame.setChecked(bool(getattr(p, "flame_detect", False)))
        i = self.cmb_flame.findData(getattr(p, "flame_action", "pause"))
        self.cmb_flame.setCurrentIndex(max(0, i))
        self.sl_flame.setValue(int(getattr(p, "flame_sensitivity", 5)))
        self.sp_hold.setValue(float(getattr(p, "flame_hold", 2.0)))
        self.cb_fshow.setChecked(bool(getattr(p, "flame_show", True)))
        for w in (self.cb_flame, self.cmb_flame, self.sl_flame, self.sp_hold, self.cb_fshow):
            w.blockSignals(False)
        self._flame_params()

    def _set(self, key, val):
        setattr(self.profile, key, val)
        self.profileChanged.emit()

    def _bg_toggled(self, v):
        self._set("camera_live_bg", v)
        self.backgroundToggled.emit(v)

    def media_dir(self) -> str:
        return getattr(self.profile, "camera_media_dir", "") or default_media_dir()

    def _choose_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Speicherort für Fotos und Videos", self.media_dir())
        if p:
            self._set("camera_media_dir", p)
            self.lbl_dir.setText(p)

    def _new_path(self, prefix: str, ext: str) -> str:
        d = self.media_dir()
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{prefix}_{time.strftime('%Y-%m-%d_%H-%M-%S')}.{ext}")

    # -- Geräte
    def refresh_devices(self):
        devs = video_devices()
        self.dev.blockSignals(True)
        self.dev.clear()
        if devs:
            self.dev.addItems(devs)
            cur = self.profile.camera_device
            self.dev.setCurrentIndex(devs.index(cur) if cur in devs else 0)
        else:
            self.dev.addItem("Keine Kamera gefunden")
        self.dev.setEnabled(bool(devs))
        self.dev.blockSignals(False)
        self._update_buttons()

    def current_device(self) -> str:
        devs = video_devices()
        txt = self.dev.currentText()
        return txt if txt in devs else (devs[0] if devs else "")

    def _dev_changed(self, _i):
        dev = self.current_device()
        if not dev:
            return
        self._set("camera_device", dev)
        if self.live.running and dev != self.live.device:
            self.start()

    # -- Ein/Aus
    def start(self) -> bool:
        dev = self.current_device()
        if not dev:
            self._on_error("Keine Kamera gefunden. Webcam anschließen und ⟳ drücken.")
            self._update_buttons()
            return False
        if self.profile.camera_device in ("", FILE_DEVICE):
            self._set("camera_device", dev)
        self.lbl_status.setText("Kamera wird eingeschaltet …")
        self.view.clear("Kamera wird eingeschaltet …")
        ok = self.live.start(dev)
        self._update_buttons()
        return ok

    def stop(self):
        self._pending_rec = ""
        self.live.stop()
        self._update_buttons()

    def _toggle(self):
        if self.live.cam is not None:
            self.stop()
        else:
            self.start()

    # -- Auftrag
    def job_started(self) -> bool:
        """Vom Hauptfenster bei Auftragsstart aufgerufen. True, wenn die Kamera dadurch eingeschaltet wurde."""
        started = False
        want = self.profile.camera_live_auto or getattr(self.profile, "flame_detect", False)
        if want and self.live.cam is None and self.current_device():
            started = self.start()
            self._auto_started = started
        if self.profile.camera_live_record and self.live.cam is not None and not self.live.recording:
            self._job_rec = True
            self._begin_recording(self._new_path("Auftrag", "mp4"))
        if getattr(self.profile, "flame_detect", False):
            self._flame_start()
        return started

    def job_finished(self):
        if not self._flame_test:
            self._flame_stop()
        if self._job_rec:
            self._job_rec = False
            self._pending_rec = ""
            QTimer.singleShot(3000, self.live.stop_recording)   # Ende des Auftrags noch mitnehmen

    # -- Foto / Video
    def save_photo(self):
        if self.live.image is None:
            self._on_error("Noch kein Kamerabild vorhanden.")
            return
        path = self._new_path("Foto", "jpg")
        if self.live.save_photo(path):
            self.lbl_status.setText(f"Foto gespeichert: {path}")
        else:
            self._on_error(f"Foto konnte nicht gespeichert werden: {path}")

    def _rec_clicked(self):
        if self.live.recording or self._pending_rec:
            self._pending_rec = ""
            self._job_rec = False
            self.live.stop_recording()
        else:
            if self.live.cam is None and not self.start():
                self._update_buttons()
                return
            self._begin_recording(self._new_path("Video", "mp4"))
        self._update_buttons()

    def _begin_recording(self, path):
        if self.live.running:
            self.live.start_recording(path)
        else:
            self._pending_rec = path         # sobald die Kamera aktiv ist
        self._update_buttons()

    # -- Rückmeldungen
    def _on_frame(self, img):
        if self._flame_on:
            self._flame_frame(img)
        self.view.set_image(img)
        if self.window_ is not None:
            self.window_.view.set_image(img)

    def _on_active(self, on):
        if on:
            self.lbl_status.setText(f"Live: {self.live.device}")
            if self._pending_rec:
                path, self._pending_rec = self._pending_rec, ""
                self.live.start_recording(path)
        else:
            self.view.clear("Kamera aus")
            if self.window_ is not None:
                self.window_.view.clear("Kamera aus")
            if not self.lbl_status.text().startswith("Fehler"):
                self.lbl_status.setText("")
        self._update_buttons()

    def _on_recording(self, on):
        if on:
            self._rec_t0 = time.monotonic()
            self._tick.start()
        else:
            self._tick.stop()
            f = self.live.actual_record_file()
            if f:
                self.lbl_status.setText(f"Video gespeichert: {f}")
        self._update_overlay()
        self._update_buttons()

    def _on_error(self, msg):
        self.lbl_status.setText(f"Fehler: {msg}")

    def _update_overlay(self):
        txt = ""
        if self.live.recording:
            s = int(time.monotonic() - self._rec_t0)
            txt = f"● REC {s // 60:02d}:{s % 60:02d}"
        for v in (self.view, self.window_.view if self.window_ is not None else None):
            if v is not None:
                v.overlay = txt
                v.update()

    def _update_buttons(self):
        on = self.live.cam is not None
        self.btn_on.setChecked(on)
        self.btn_on.setText("■ Ausschalten" if on else "▶ Einschalten")
        self.btn_photo.setEnabled(on)
        rec = self.live.recording or bool(self._pending_rec)
        self.btn_rec.setChecked(rec)
        self.btn_rec.setText("■ Aufnahme beenden" if rec else "● Video aufnehmen")

    # -- Fenster / Drehen
    def open_window(self):
        if self.window_ is None:
            self.window_ = CameraWindow(self)
            self.window_.view.rotation = self.view.rotation
            self.window_.closed.connect(self._window_closed)
            if self.live.image is not None:
                self.window_.view.set_image(self.live.image)
            else:
                self.window_.view.clear(self.view.placeholder)
        self.window_.show()
        self.window_.raise_()
        self.window_.activateWindow()
        if self.live.cam is None:
            self.start()

    def _window_closed(self):
        w, self.window_ = self.window_, None
        if w is not None:
            w.deleteLater()

    def _rotate(self):
        rot = (self.view.rotation + 90) % 360
        self.view.rotation = rot
        self.view.update()
        if self.window_ is not None:
            self.window_.view.rotation = rot
            self.window_.view.update()
        self._set("camera_live_rot", rot)

    # -- Flammenwächter
    def _build_flame_box(self):
        box = QGroupBox("Flammenwächter")
        v = QVBoxLayout(box)
        self.cb_flame = QCheckBox("Flammen während des Auftrags erkennen")
        self.cb_flame.setToolTip("Schaltet bei Auftragsstart die Kamera ein und sucht im Bild nach Flammen, "
                                 "die an einer Stelle stehen bleiben. Ersetzt keine Aufsicht!")
        v.addWidget(self.cb_flame)
        g = QGridLayout()
        self.cmb_flame = QComboBox()
        self.cmb_flame.addItem("Nur warnen", "warn")
        self.cmb_flame.addItem("Auftrag pausieren", "pause")
        self.cmb_flame.addItem("Auftrag stoppen", "stop")
        self.sl_flame = QSlider(Qt.Orientation.Horizontal)
        self.sl_flame.setRange(1, 10)
        self.sl_flame.setToolTip("Höher = kleinere Flammen lösen aus (aber auch eher Fehlalarme)")
        self.lbl_sens = QLabel("5")
        self.sp_hold = QDoubleSpinBox()
        self.sp_hold.setRange(0.5, 10.0)
        self.sp_hold.setDecimals(1)
        self.sp_hold.setSingleStep(0.5)
        self.sp_hold.setSuffix(" s")
        self.sp_hold.setToolTip("So lange muss eine Stelle brennen, bevor der Wächter auslöst")
        g.addWidget(QLabel("Bei Flamme:"), 0, 0)
        g.addWidget(self.cmb_flame, 0, 1, 1, 2)
        g.addWidget(QLabel("Empfindlichkeit:"), 1, 0)
        g.addWidget(self.sl_flame, 1, 1)
        g.addWidget(self.lbl_sens, 1, 2)
        g.addWidget(QLabel("Auslösen nach:"), 2, 0)
        g.addWidget(self.sp_hold, 2, 1, 1, 2)
        v.addLayout(g)
        h = QHBoxLayout()
        self.cb_fshow = QCheckBox("Erkannte Stellen rot zeigen")
        self.btn_ftest = QPushButton("Testen")
        self.btn_ftest.setCheckable(True)
        self.btn_ftest.setToolTip("Wächter ohne Auftrag laufen lassen, z. B. mit einem Feuerzeug vor der Kamera. "
                                  "Löst nur eine Meldung aus.")
        h.addWidget(self.cb_fshow, 1)
        h.addWidget(self.btn_ftest)
        v.addLayout(h)
        self.lbl_flame = QLabel("")
        self.lbl_flame.setWordWrap(True)
        v.addWidget(self.lbl_flame)
        self.cb_flame.toggled.connect(self._flame_enabled)
        self.cmb_flame.currentIndexChanged.connect(lambda _i: self._set("flame_action", self.cmb_flame.currentData()))
        self.sl_flame.valueChanged.connect(lambda val: (self._set("flame_sensitivity", int(val)), self._flame_params()))
        self.sp_hold.valueChanged.connect(lambda val: (self._set("flame_hold", float(val)), self._flame_params()))
        self.cb_fshow.toggled.connect(lambda val: (self._set("flame_show", bool(val)), self._clear_mask()))
        self.btn_ftest.toggled.connect(self._flame_test_toggled)
        return box

    def _flame_params(self):
        self.flame.sensitivity = int(getattr(self.profile, "flame_sensitivity", 5))
        self.flame.hold_s = float(getattr(self.profile, "flame_hold", 2.0))
        self.lbl_sens.setText(str(self.flame.sensitivity))
        for w in (self.cmb_flame, self.sl_flame, self.sp_hold, self.cb_fshow):
            w.setEnabled(self.cb_flame.isChecked() or self._flame_test)

    def _flame_enabled(self, on):
        self._set("flame_detect", bool(on))
        self._flame_params()
        if not on and not self._flame_test:
            self._flame_stop()

    def _flame_test_toggled(self, on):
        self._flame_test = bool(on)
        self._flame_params()
        if on:
            if self.live.cam is None and not self.start():
                self.btn_ftest.setChecked(False)
                return
            self._flame_start()
        else:
            self._flame_stop()

    def _flame_start(self):
        self.flame.reset()
        self._flame_params()
        self._flame_on = True
        self._flame_t = 0.0
        if self.live.cam is None:
            self.lbl_flame.setText("⚠ Flammenwächter: keine Kamera – Überwachung nicht möglich!")
        else:
            self.lbl_flame.setText("Flammenwächter: startet …")

    def _flame_stop(self):
        self._flame_on = False
        self.flame_ratio = 0.0
        self.lbl_flame.setText("")
        self._clear_mask()

    def flame_watching(self) -> bool:
        return self._flame_on and self.live.cam is not None

    def rearm_flame(self):
        """Nach einer Meldung weiter beobachten (Referenzbild bleibt erhalten)."""
        self.flame.reset(keep_reference=True)
        self._flame_on = True

    def _clear_mask(self):
        for v in (self.view, self.window_.view if self.window_ is not None else None):
            if v is not None:
                v.mask = None
                v.update()

    def _flame_frame(self, img):
        now = time.monotonic()
        if now - self._flame_t < 0.2:          # 5 Bilder pro Sekunde reichen
            return
        self._flame_t = now
        arr = qimage_to_array(img)
        if arr is None:
            return
        if self.flame.triggered:
            return
        res = self.flame.update(arr, now)
        self.flame_ratio = res.ratio
        pct = min(999, int(round(res.ratio * 100)))
        self.lbl_flame.setText(f"Flammenwächter: beobachtet – {pct} % der Auslöseschwelle"
                               + (" (Test)" if self._flame_test else ""))
        if getattr(self.profile, "flame_show", True) and res.mask is not None and res.mask.any():
            rgba = np.zeros(res.mask.shape + (4,), np.uint8)
            rgba[res.mask] = (255, 0, 0, 150)
            h, w = res.mask.shape
            self._mask_buf = np.ascontiguousarray(rgba)
            q = QImage(self._mask_buf.data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
            for v in (self.view, self.window_.view if self.window_ is not None else None):
                if v is not None:
                    v.mask = q
        elif self.view.mask is not None:
            self._clear_mask()
        if res.triggered:
            self._flame_on = False
            path = ""
            try:
                path = self._new_path("Flamme", "jpg")
                if not self.live.save_photo(path):
                    path = ""
            except OSError:
                path = ""
            self.lbl_flame.setText("🔥 Flammenwächter hat ausgelöst!" + (f" Foto: {path}" if path else ""))
            self.flameDetected.emit(path)

    def shutdown(self):
        if self.window_ is not None:
            self.window_.close()
        self.live.stop(force=True)

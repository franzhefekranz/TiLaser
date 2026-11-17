"""Maschinensteuerung: Verbindung, Jog, Home, Framing, Auftrag starten/pausieren/stoppen, Konsole."""
from __future__ import annotations

import re
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core import sender
from ..core.gcode import MachineProfile


class _Bridge(QObject):
    """Leitet Callbacks aus dem Sender-Thread threadsicher in den GUI-Thread."""
    log = pyqtSignal(str)
    status = pyqtSignal(dict)
    progress = pyqtSignal(int, int)
    state = pyqtSignal(str)
    firmware = pyqtSignal(object, str)


ANCHOR_LABELS = (("bl", "↙ links unten"), ("bc", "↓ unten Mitte"), ("br", "↘ rechts unten"),
                 ("ml", "← links Mitte"), ("mc", "· Mitte"), ("mr", "→ rechts Mitte"),
                 ("tl", "↖ links oben"), ("tc", "↑ oben Mitte"), ("tr", "↗ rechts oben"))


class MachinePanel(QWidget):
    startRequested = pyqtSignal()
    resumeRequested = pyqtSignal()
    frameRequested = pyqtSignal()
    firmwareDetected = pyqtSignal(str)
    profileChanged = pyqtSignal()

    def __init__(self, profile: MachineProfile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.bridge = _Bridge()
        self.conn = sender.LaserConnection(
            on_log=self.bridge.log.emit, on_status=self.bridge.status.emit,
            on_progress=self.bridge.progress.emit, on_state=self.bridge.state.emit,
            on_firmware=self.bridge.firmware.emit)
        self.bridge.log.connect(self._log)
        self.bridge.status.connect(self._status)
        self.bridge.progress.connect(self._progress)
        self.bridge.state.connect(self._state)
        self.bridge.firmware.connect(self._firmware)
        self.job_start = 0.0
        self.machine_state = "disconnected"

        # Das Panel selbst wird nicht angezeigt; das Hauptfenster verteilt die Bereiche auf Tabs:
        #   w_connection (Verbindung), w_jog (Bewegen), w_console (Konsole), w_jobbar (immer sichtbar)
        self.hide()

        # --- Verbindung
        self.w_connection = QWidget()
        cv = QVBoxLayout(self.w_connection)
        cg = QGroupBox("Verbindung")
        cl = QGridLayout(cg)
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(140)
        self.btn_refresh = QPushButton("↻")
        self.btn_refresh.setFixedWidth(30)
        self.btn_refresh.setToolTip("Portliste aktualisieren")
        self.baud = QComboBox()
        self.baud.addItems(["115200", "250000", "230400", "57600", "9600"])
        self.btn_connect = QPushButton("Verbinden")
        self.lbl_fw = QLabel("Firmware: –")
        self.conn_type = QComboBox()
        self.conn_type.addItem("USB / Seriell", "serial")
        self.conn_type.addItem("Netzwerk (TCP/Telnet)", "tcp")
        self.host = QLineEdit(profile.host)
        self.host.setPlaceholderText("IP-Adresse, z. B. 192.168.1.50")
        self.tcp_port = QSpinBox()
        self.tcp_port.setRange(1, 65535)
        self.tcp_port.setValue(profile.tcp_port)
        self.lbl_port, self.lbl_baud = QLabel("Port"), QLabel("Baud")
        self.lbl_host, self.lbl_tcp = QLabel("Adresse"), QLabel("TCP-Port")
        cl.addWidget(QLabel("Art"), 0, 0)
        cl.addWidget(self.conn_type, 0, 1, 1, 2)
        cl.addWidget(self.lbl_port, 1, 0)
        cl.addWidget(self.port, 1, 1)
        cl.addWidget(self.btn_refresh, 1, 2)
        cl.addWidget(self.lbl_baud, 2, 0)
        cl.addWidget(self.baud, 2, 1, 1, 2)
        cl.addWidget(self.lbl_host, 3, 0)
        cl.addWidget(self.host, 3, 1, 1, 2)
        cl.addWidget(self.lbl_tcp, 4, 0)
        cl.addWidget(self.tcp_port, 4, 1, 1, 2)
        cl.addWidget(self.btn_connect, 5, 0, 1, 3)
        cl.addWidget(self.lbl_fw, 6, 0, 1, 3)
        self.conn_type.setCurrentIndex(1 if profile.conn_type == "tcp" else 0)
        self.conn_type.currentIndexChanged.connect(self._conn_vis)
        self._conn_vis()
        cv.addWidget(cg)
        mg = QGroupBox("Maschine")
        ml = QGridLayout(mg)
        self.btn_home = QPushButton("⌂ Referenzfahrt")
        self.btn_home.setToolTip("Referenzfahrt (Home, $H bzw. G28)")
        self.btn_unlock = QPushButton("Entsperren")
        self.btn_unlock.setToolTip("Alarm aufheben ($X bzw. M999)")
        self.btn_zero = QPushButton("Nullpunkt hier")
        self.btn_zero.setToolTip("Aktuelle Kopfposition als Werkstück-Nullpunkt (X0 Y0) setzen")
        self.btn_goto0 = QPushButton("Zu 0/0 fahren")
        ml.addWidget(self.btn_home, 0, 0)
        ml.addWidget(self.btn_unlock, 0, 1)
        ml.addWidget(self.btn_zero, 1, 0)
        ml.addWidget(self.btn_goto0, 1, 1)
        cv.addWidget(mg)
        cv.addStretch(1)

        # --- Bewegen
        self.w_jog = QWidget()
        jv = QVBoxLayout(self.w_jog)
        jog = QGroupBox("Relativ")
        gl = QGridLayout(jog)
        self.jog_btns = {}
        for text, dx, dy, r, c in (("↖", -1, 1, 0, 0), ("Y+", 0, 1, 0, 1), ("↗", 1, 1, 0, 2),
                                   ("X−", -1, 0, 1, 0), ("X+", 1, 0, 1, 2),
                                   ("↙", -1, -1, 2, 0), ("Y−", 0, -1, 2, 1), ("↘", 1, -1, 2, 2)):
            b = QPushButton(text)
            b.setFixedSize(52, 40)
            b.clicked.connect(lambda _=False, a=dx, bb=dy: self._jog(a, bb))
            gl.addWidget(b, r, c)
            self.jog_btns[text] = b
        self.btn_home2 = QPushButton("⌂")
        self.btn_home2.setToolTip("Referenzfahrt (Home)")
        self.btn_home2.setFixedSize(52, 40)
        gl.addWidget(self.btn_home2, 1, 1)
        self.step = QComboBox()
        self.step.addItems(["0.1", "1", "5", "10", "50", "100"])
        self.step.setCurrentText("10")
        self.feed = QSpinBox()
        self.feed.setRange(10, 30000)
        self.feed.setSingleStep(500)
        self.feed.setValue(3000)
        self.feed.setSuffix(" mm/min")
        gl.addWidget(QLabel("Schritt (mm)"), 3, 0, 1, 1)
        gl.addWidget(self.step, 3, 1, 1, 2)
        gl.addWidget(QLabel("Vorschub"), 4, 0, 1, 1)
        gl.addWidget(self.feed, 4, 1, 1, 2)
        jv.addWidget(jog)

        ab = QGroupBox("Absolut (Werkstückkoordinaten)")
        al = QGridLayout(ab)
        self.abs_x = QDoubleSpinBox()
        self.abs_y = QDoubleSpinBox()
        for sp in (self.abs_x, self.abs_y):
            sp.setRange(-10000, 10000)
            sp.setDecimals(2)
            sp.setSuffix(" mm")
            sp.setKeyboardTracking(False)
        self.btn_take = QPushButton("Aktuelle Position übernehmen")
        self.btn_goto = QPushButton("Fahren")
        self.btn_goto.setStyleSheet("font-weight: bold")
        self.btn_goto.setToolTip("Laserkopf mit ausgeschaltetem Laser zur eingegebenen Position fahren")
        al.addWidget(QLabel("X"), 0, 0)
        al.addWidget(self.abs_x, 0, 1)
        al.addWidget(QLabel("Y"), 1, 0)
        al.addWidget(self.abs_y, 1, 1)
        al.addWidget(self.btn_take, 2, 0, 1, 2)
        al.addWidget(self.btn_goto, 3, 0, 1, 2)
        self.abs_x.lineEdit().returnPressed.connect(self._goto_abs)
        self.abs_y.lineEdit().returnPressed.connect(self._goto_abs)
        jv.addWidget(ab)
        jv.addStretch(1)

        # --- Konsole
        self.w_console = QWidget()
        vl = QVBoxLayout(self.w_console)
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(3000)
        self.console.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 9pt")
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("G-Code oder $-Befehl, Enter zum Senden")
        vl.addWidget(self.console)
        vl.addWidget(self.cmd)

        # --- Auftragsleiste (immer sichtbar)
        self.w_jobbar = QWidget()
        jb = QGridLayout(self.w_jobbar)
        jb.setContentsMargins(4, 4, 4, 4)
        self.lbl_state = QLabel("Getrennt")
        self.lbl_state.setStyleSheet("font-weight: bold")
        self.lbl_pos = QLabel("X –  Y –")
        self.btn_frame = QPushButton("Rahmen")
        self.btn_frame.setToolTip("Umriss des Auftrags abfahren")
        self.btn_start = QPushButton("▶ Start")
        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_stop = QPushButton("■ Stopp")
        self.btn_start.setStyleSheet("font-weight: bold")
        self.btn_stop.setStyleSheet("color: #c62828; font-weight: bold")
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(16)
        self.lbl_time = QLabel("")
        jb.addWidget(self.lbl_state, 0, 0, 1, 2)
        jb.addWidget(self.lbl_pos, 0, 2, 1, 2)
        jb.addWidget(self.btn_frame, 1, 0)
        jb.addWidget(self.btn_start, 1, 1)
        jb.addWidget(self.btn_pause, 1, 2)
        jb.addWidget(self.btn_stop, 1, 3)
        jb.addWidget(self.progress, 2, 0, 1, 3)
        jb.addWidget(self.lbl_time, 2, 3)
        self.origin = QComboBox()
        self.origin.addItem("Absolut", "abs")
        self.origin.addItem("Aktuelle Position", "current")
        self.origin.setToolTip("Absolut: Koordinaten wie auf der Arbeitsfläche.\n"
                               "Aktuelle Position: der gewählte Ankerpunkt des Auftrags liegt an der Kopfposition.")
        self.anchor = QComboBox()
        for key, label in ANCHOR_LABELS:
            self.anchor.addItem(label, key)
        self.anchor.setToolTip("Ankerpunkt des Auftrags bei 'Aktuelle Position'")
        self.only_sel = QCheckBox("Nur Auswahl")
        self.only_sel.setToolTip("Nur die ausgewählten Objekte lasern bzw. umrahmen")
        self.btn_resume = QPushButton("Fortsetzen…")
        self.btn_resume.setToolTip("Abgebrochenen Auftrag ab einer Zeile fortsetzen")
        jb.addWidget(self.origin, 3, 0, 1, 2)
        jb.addWidget(self.anchor, 3, 2, 1, 2)
        jb.addWidget(self.only_sel, 4, 0, 1, 2)
        jb.addWidget(self.btn_resume, 4, 2, 1, 2)
        self.origin.currentIndexChanged.connect(lambda _: self.anchor.setEnabled(self.origin.currentData() == "current"))
        self.anchor.setEnabled(False)
        self.last_pos: list | None = None

        self.btn_refresh.clicked.connect(self.refresh_ports)
        self.btn_connect.clicked.connect(self.toggle_connect)
        self.btn_home.clicked.connect(self._home)
        self.btn_home2.clicked.connect(self._home)
        self.btn_take.clicked.connect(self._take_pos)
        self.btn_goto.clicked.connect(self._goto_abs)
        self.btn_unlock.clicked.connect(self._unlock)
        self.btn_zero.clicked.connect(self._set_zero)
        self.btn_goto0.clicked.connect(self._goto0)
        self.btn_frame.clicked.connect(self.frameRequested.emit)
        self.btn_start.clicked.connect(self.startRequested.emit)
        self.btn_resume.clicked.connect(self.resumeRequested.emit)
        self.btn_pause.clicked.connect(self._pause)
        self.btn_stop.clicked.connect(self._stop)
        self.cmd.returnPressed.connect(self._send_cmd)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.refresh_ports()
        if profile.port:
            self.port.setCurrentText(profile.port)
        self.baud.setCurrentText(str(profile.baud))
        self._update_buttons()
        if sender.serial is None:
            self._log("!! pyserial fehlt – Laser-Steuerung deaktiviert. Installation: pip install pyserial")

    # ------------------------------------------------------------ Verbindung
    def refresh_ports(self):
        cur = self.port.currentText()
        self.port.clear()
        for dev, desc in sender.list_ports():
            self.port.addItem(dev)
            self.port.setItemData(self.port.count() - 1, desc, Qt.ItemDataRole.ToolTipRole)
        if cur:
            self.port.setCurrentText(cur)

    def _conn_vis(self):
        tcp = self.conn_type.currentData() == "tcp"
        for w in (self.lbl_port, self.port, self.btn_refresh, self.lbl_baud, self.baud):
            w.setVisible(not tcp)
        for w in (self.lbl_host, self.host, self.lbl_tcp, self.tcp_port):
            w.setVisible(tcp)

    def toggle_connect(self):
        if self.conn.connected:
            if self.conn.job_active and QMessageBox.question(
                    self, "Trennen", "Ein Auftrag läuft. Wirklich trennen?") != QMessageBox.StandardButton.Yes:
                return
            self.conn.disconnect()
            return
        hint = "grbl" if self.profile.dialect == "grbl" else "ping"
        tcp = self.conn_type.currentData() == "tcp"
        try:
            if tcp:
                host = self.host.text().strip()
                if not host:
                    QMessageBox.warning(self, "Verbinden", "Bitte die IP-Adresse des Lasers eingeben.")
                    return
                self.conn.connect_tcp(host, self.tcp_port.value(), hint)
                self.profile.host, self.profile.tcp_port = host, self.tcp_port.value()
            else:
                port = self.port.currentText().strip()
                if not port:
                    QMessageBox.warning(self, "Verbinden", "Bitte einen seriellen Port wählen.")
                    return
                self.conn.connect(port, int(self.baud.currentText()), hint)
                self.profile.port, self.profile.baud = port, int(self.baud.currentText())
        except Exception as e:
            QMessageBox.critical(self, "Verbinden", f"Verbindung fehlgeschlagen:\n{e}")
            return
        self.profile.conn_type = "tcp" if tcp else "serial"
        self.profileChanged.emit()
        self.lbl_state.setText("Verbinde … (Firmware-Erkennung)")
        self.btn_connect.setText("Trennen")

    def shutdown(self):
        if self.conn.connected:
            self.conn.disconnect()

    def is_ready(self) -> bool:
        return self.conn.connected and not self.conn.job_active

    # ------------------------------------------------------------ Befehle
    def _grbl(self) -> bool:
        return self.conn.protocol == "grbl"

    def send(self, line: str):
        if not self.conn.connected:
            QMessageBox.information(self, "Laser", "Nicht verbunden.")
            return False
        self.conn.send(line)
        return True

    def _jog(self, dx, dy):
        if self.conn.job_active:
            return
        s = float(self.step.currentText())
        f = self.feed.value()
        x, y = dx * s, dy * s
        if self._grbl():
            self.send(f"$J=G91 G21 X{x:g} Y{y:g} F{f}")
        else:
            self.send(f"G91\nG0 X{x:g} Y{y:g} F{f}\nG90")

    def _home(self):
        self.send("$H" if self._grbl() else "G28 X Y")

    def _unlock(self):
        self.send("$X" if self._grbl() else "M999")

    def _set_zero(self):
        self.send("G10 L20 P1 X0 Y0" if self._grbl() else "G92 X0 Y0")

    def _goto0(self):
        self.send("G90\nG0 X0 Y0" + ("" if self._grbl() else f" F{self.profile.travel_speed:g}"))

    def _take_pos(self):
        if self.last_pos:
            self.abs_x.setValue(self.last_pos[0])
            self.abs_y.setValue(self.last_pos[1])

    def _goto_abs(self):
        if not self.conn.connected or self.conn.job_active:
            return
        x, y, f = self.abs_x.value(), self.abs_y.value(), self.feed.value()
        if not (0 <= x <= self.profile.bed_w and 0 <= y <= self.profile.bed_h):
            if QMessageBox.question(self, "Fahren",
                                    f"X{x:g} Y{y:g} liegt außerhalb des Arbeitsbereichs "
                                    f"(0–{self.profile.bed_w:g} × 0–{self.profile.bed_h:g} mm).\nTrotzdem fahren?") \
                    != QMessageBox.StandardButton.Yes:
                return
        if self._grbl():
            self.send(f"$J=G90 G21 X{x:.3f} Y{y:.3f} F{f}")     # Jog: jederzeit mit Stopp abbrechbar
        else:
            self.send(f"M5\nG90\nG0 X{x:.3f} Y{y:.3f} F{f}")

    def _send_cmd(self):
        t = self.cmd.text().strip()
        if not t:
            return
        if t in ("?", "!", "~"):
            self.conn.realtime(t.encode())
        elif self.send(t):
            self.cmd.clear()

    def run_job(self, lines: list[str]):
        self.conn.start_job(lines)
        self.job_start = time.time()

    def _pause(self):
        if not self.conn.job_active:
            return
        if self.machine_state == "paused" or self.conn._paused:
            self.conn.resume()
            self.btn_pause.setText("⏸ Pause")
        else:
            self.conn.pause()
            self.btn_pause.setText("▶ Fortsetzen")

    def _stop(self):
        if self.conn.connected:
            self.conn.stop()
            self.btn_pause.setText("⏸ Pause")
            if self._grbl():
                self._log("-- Soft-Reset gesendet. Ggf. 'Entsperren' ($X) drücken.")

    # ------------------------------------------------------------ Rückmeldungen
    def _log(self, s: str):
        self.console.appendPlainText(s)
        m = re.match(r"<< \$(\d+)=([\d.]+)", s)
        if m:
            key, val = int(m.group(1)), float(m.group(2))
            if key == 30 and self.profile.dialect == "grbl" and abs(val - self.profile.s_max) > 1e-6:
                self.profile.s_max = val
                self.profileChanged.emit()
                self.console.appendPlainText(f"-- Maximaler S-Wert aus $30 übernommen: S{val:g}")
            if key == 32 and val == 0:
                self.console.appendPlainText("!! $32=0: Lasermodus ist AUS. Mit '$32=1' aktivieren, "
                                             "sonst brennt der Laser bei Stillstand/G0 weiter!")

    def _status(self, st: dict):
        state = st.get("state", "")
        pos = st.get("WPos") or st.get("MPos")
        txt = state
        if self.conn.job_active and state.startswith("Idle"):
            txt = "Läuft"
        self.lbl_state.setText(txt)
        if pos:
            self.last_pos = pos
            self.lbl_pos.setText(f"X {pos[0]:.2f}  Y {pos[1]:.2f}")

    def _progress(self, done, total):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)

    def _state(self, s: str):
        self.machine_state = s
        labels = {"idle": "Bereit", "running": "Läuft", "paused": "Pausiert", "done": "Fertig",
                  "stopped": "Gestoppt", "alarm": "ALARM", "error": "Verbindungsfehler", "disconnected": "Getrennt"}
        self.lbl_state.setText(labels.get(s, s))
        if s == "done":
            dur = time.time() - self.job_start
            self._log(f"-- Auftrag fertig in {int(dur // 60)}:{int(dur % 60):02d} min"
                      + (f", {self.conn.errors} Fehlermeldung(en)" if self.conn.errors else ""))
        if s in ("disconnected", "error"):
            self.btn_connect.setText("Verbinden")
            self.lbl_fw.setText("Firmware: –")
            self.lbl_pos.setText("X –  Y –")
            self.last_pos = None
            if s == "error" and self.conn.ser is not None:
                try:
                    self.conn.ser.close()
                except Exception:
                    pass
                self.conn.ser = None
        if s in ("done", "stopped", "alarm"):
            self.btn_pause.setText("⏸ Pause")
        self._update_buttons()

    def _firmware(self, fw, text):
        names = {"grbl": "GRBL / FluidNC", "marlin": "Marlin", "smoothie": "Smoothieware"}
        self.lbl_fw.setText(f"Firmware: {names.get(fw, 'nicht erkannt')}")
        if fw:
            self.firmwareDetected.emit(fw)
        else:
            self._log("-- Firmware nicht erkannt. Protokoll laut Maschinenprofil: "
                      f"{self.profile.dialect}. Ggf. Baudrate prüfen.")
        if fw == "grbl":
            self.conn.send("$$")          # Einstellungen lesen ($30, $32)

    def _tick(self):
        if self.conn.job_active:
            dur = time.time() - self.job_start
            self.lbl_time.setText(f"Laufzeit {int(dur // 60)}:{int(dur % 60):02d}")
        self._update_buttons()

    def _update_buttons(self):
        con = self.conn.connected
        job = self.conn.job_active
        for b in list(self.jog_btns.values()) + [self.btn_home, self.btn_home2, self.btn_zero, self.btn_goto0,
                                                 self.btn_frame, self.btn_start, self.btn_goto]:
            b.setEnabled(con and not job)
        self.btn_unlock.setEnabled(con)
        self.btn_take.setEnabled(con and self.last_pos is not None)
        self.btn_pause.setEnabled(con and job)
        self.btn_stop.setEnabled(con)
        self.btn_resume.setEnabled(con and not job and bool(self.conn.last_job))
        self.cmd.setEnabled(con and not job)
        self.btn_connect.setText("Trennen" if con else "Verbinden")

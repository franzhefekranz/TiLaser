"""Serielle Verbindung zum Laser: Firmware-Erkennung, G-Code-Streaming, Echtzeitbefehle.

GRBL: Zeichenzähl-Protokoll (Puffer 127 Byte) + Statusabfrage '?'.
Marlin/Smoothie/generisch: Zeile senden, auf 'ok' warten.
Läuft in einem eigenen Thread; Rückmeldungen über Callbacks.
"""
from __future__ import annotations

import queue
import re
import threading
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:          # pyserial optional – ohne kann nur exportiert werden
    serial = None

GRBL_RX_BUFFER = 127


class TcpTransport:
    """TCP/Telnet-Verbindung mit derselben Schnittstelle wie serial.Serial (read/write/close)."""

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        import socket
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(0.02)

    def read(self, n: int) -> bytes:
        import socket
        try:
            data = self.sock.recv(n)
        except (socket.timeout, BlockingIOError):
            return b""
        if data == b"":
            raise ConnectionError("Verbindung vom Gerät geschlossen")
        # Telnet-Steuersequenzen (IAC) herausfiltern
        if b"\xff" in data:
            out, i = bytearray(), 0
            while i < len(data):
                if data[i] == 0xFF and i + 2 < len(data):
                    i += 3
                    continue
                out.append(data[i])
                i += 1
            data = bytes(out)
        return data

    def write(self, data: bytes):
        self.sock.sendall(data)
        return len(data)

    def reset_input_buffer(self):
        while self.read(4096):
            pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def list_ports() -> list[tuple[str, str]]:
    if serial is None:
        return []
    return [(p.device, p.description or p.device) for p in serial.tools.list_ports.comports()]


def clean_line(line: str) -> str:
    """Kommentare (; und ( )) und Leerzeichen am Rand entfernen."""
    line = line.split(";", 1)[0]
    line = re.sub(r"\([^)]*\)", "", line)
    return line.strip()


def parse_grbl_status(s: str, wco_cache: list) -> dict:
    body = s.strip().strip("<>")
    parts = body.split("|")
    st: dict = {"state": parts[0]}
    for p in parts[1:]:
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        nums = []
        for x in v.split(","):
            try:
                nums.append(float(x))
            except ValueError:
                pass
        if k in ("MPos", "WPos", "WCO", "FS", "F", "Ov"):
            st[k] = nums
    if "WCO" in st:
        wco_cache[:] = st["WCO"]
    if "MPos" in st and "WPos" not in st and wco_cache:
        st["WPos"] = [a - b for a, b in zip(st["MPos"], wco_cache)]
    if "WPos" in st and "MPos" not in st and wco_cache:
        st["MPos"] = [a + b for a, b in zip(st["WPos"], wco_cache)]
    return st


class LaserConnection:
    def __init__(self, on_log=None, on_status=None, on_progress=None, on_state=None, on_firmware=None):
        self.on_log = on_log or (lambda s: None)
        self.on_status = on_status or (lambda d: None)
        self.on_progress = on_progress or (lambda done, total: None)
        self.on_state = on_state or (lambda s: None)
        self.on_firmware = on_firmware or (lambda fw, text: None)
        self.ser = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.firmware: str | None = None       # 'grbl' | 'marlin' | 'smoothie' | None
        self.protocol = "grbl"                  # Streaming-Verfahren
        self._cmd_q: queue.Queue[str] = queue.Queue()
        self._rt_q: queue.Queue[bytes] = queue.Queue()
        self._job: list[str] = []
        self._job_idx = 0
        self._job_acked = 0
        self._job_active = False
        self._paused = False
        self._pending: list[tuple[int, bool]] = []   # (Länge, gehört zum Job)
        self._wco: list = []
        self._lock = threading.Lock()
        self.errors = 0
        self.last_job: list[str] = []          # für 'Fortsetzen'
        self.last_acked = 0
        self.pause_on_error = True             # error:/Fehler im Auftrag -> anhalten statt weiterlaufen
        self.air_off = "M9"                    # beim Stopp (nicht GRBL) zusätzlich senden; "" = nicht
        self._extra_ok = 0                     # erwartete 'ok' auf direkt geschriebene Zeilen (Marlin)
        self._finishing = False                # alle Zeilen quittiert, Maschine fährt noch (GRBL)

    # ------------------------------------------------------------ Verbindung
    @property
    def connected(self) -> bool:
        return self.ser is not None and self.running

    def connect(self, port: str, baud: int = 115200, protocol_hint: str | None = None):
        if serial is None:
            raise RuntimeError("pyserial ist nicht installiert (pip install pyserial).")
        self.ser = serial.Serial(port, baud, timeout=0.02, write_timeout=2)
        self._start(protocol_hint)

    def connect_tcp(self, host: str, port: int = 23, protocol_hint: str | None = None):
        """Netzwerkverbindung (z. B. ESP32 mit FluidNC/grblHAL: Telnet-Port 23)."""
        self.ser = TcpTransport(host, port)
        self._start(protocol_hint)

    def _start(self, protocol_hint):
        self.running = True
        self.protocol = protocol_hint or "grbl"
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def disconnect(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        try:
            if self.ser:
                self.ser.close()
        finally:
            self.ser = None
            self.thread = None
            self._job_active = False
            self.on_state("disconnected")

    # ------------------------------------------------------------ Öffentliche Befehle
    def send(self, line: str):
        """Einzelbefehl (Konsole, Jog, Home ...)."""
        for l in line.splitlines():
            l = clean_line(l) if not l.strip().startswith("$") else l.strip()
            if l:
                self._cmd_q.put(l)

    def realtime(self, b: bytes):
        self._rt_q.put(b)

    def _urgent(self, line: str):
        """Zeile sofort schreiben, an der Warteschlange vorbei (Marlin/Smoothie: M410, M5 …).

        Die Maschine quittiert sie mit einem eigenen 'ok', das nicht zu einer wartenden Zeile gehört."""
        self._rt_q.put((line.strip() + "\n").encode())

    @staticmethod
    def _laser_state(lines: list[str]) -> str | None:
        """Letzter Laser-Einschaltbefehl (M3/M4 …) in lines, None wenn danach M5 kam."""
        for l in reversed(lines):
            u = l.upper()
            if u.startswith("M5"):
                return None
            if u.startswith(("M3", "M4")):
                return l
        return None

    def start_job(self, lines: list[str]):
        if self._job_active:
            raise RuntimeError("Es läuft bereits ein Auftrag.")
        with self._lock:
            self._job = [c for c in (clean_line(l) for l in lines) if c]
            if self.protocol != "grbl" and self._job:
                self._job.append("M400")       # 'ok' erst, wenn alle Bewegungen ausgeführt sind
            self._finishing = False
            self._job_idx = 0
            self._job_acked = 0
            self._paused = False
            self.errors = 0
            self._job_active = True
        self.on_state("running")
        self.on_progress(0, len(self._job))

    @property
    def job_active(self) -> bool:
        return self._job_active

    def pause(self):
        self._paused = True
        if self.protocol == "grbl":
            self.realtime(b"!")              # Vorschub-Halt; M4 (dynamisch) = kein Strahl im Stillstand
        else:
            # Marlin/Smoothie: der Planner fährt seine Bewegungen noch zu Ende. Mit M3 (konstante
            # Leistung) würde der Laser danach im Stillstand weiterbrennen – daher sofort M5 senden.
            self._urgent("M5")
        self.on_state("paused")

    def resume(self):
        if self.protocol != "grbl" and self._job_active:
            with self._lock:
                on = self._laser_state(self._job[:self._job_idx])
            if on:
                self._cmd_q.put(on)          # Laser wie vor der Pause wieder einschalten (vor der nächsten Zeile)
        self._paused = False
        if self.protocol == "grbl":
            self.realtime(b"~")
        self.on_state("running" if self._job_active else "idle")

    def stop(self):
        with self._lock:
            if self._job:
                self.last_job, self.last_acked = list(self._job), self._job_acked
            self._job_active = False
            self._finishing = False
            self._paused = False
            self._job = []
        if self.protocol == "grbl":
            self.realtime(b"\x18")           # Soft-Reset: stoppt sofort, Laser und Luft aus
        else:
            # Sofort, an der Warteschlange vorbei: M410 (Quickstop, Marlins Emergency Parser) vor M5
            self._urgent("M410")
            self._urgent("M5")
            if self.air_off:
                self._urgent(self.air_off)
        self.on_state("stopped")

    # ------------------------------------------------------------ Thread
    def _write(self, data: bytes):
        self.ser.write(data)

    def _readlines(self, duration: float) -> list[str]:
        out, buf = [], b""
        t_end = time.time() + duration
        while time.time() < t_end:
            buf += self.ser.read(256)
            while b"\n" in buf:
                l, buf = buf.split(b"\n", 1)
                l = l.decode(errors="replace").strip()
                if l:
                    out.append(l)
                    self.on_log("<< " + l)
        return out

    def _detect(self):
        text = "\n".join(self._readlines(2.0))       # Arduino-Reset abwarten
        fw = None
        if re.search(r"grbl|fluidnc", text, re.I):
            fw = "grbl"
        if fw is None:
            self._write(b"\r\n\r\n")
            text += "\n" + "\n".join(self._readlines(0.5))
            self._write(b"$I\n")
            t = "\n".join(self._readlines(1.0))
            text += "\n" + t
            if re.search(r"grbl|fluidnc|\[VER:", t, re.I):
                fw = "grbl"
        if fw is None:
            self._write(b"M115\n")
            t = "\n".join(self._readlines(1.5))
            text += "\n" + t
            if re.search(r"marlin", t, re.I):
                fw = "marlin"
            elif re.search(r"smoothie", t, re.I):
                fw = "smoothie"
        if fw is None:
            self._write(b"version\n")
            t = "\n".join(self._readlines(1.0))
            text += "\n" + t
            if re.search(r"smoothie", t, re.I):
                fw = "smoothie"
        self.ser.reset_input_buffer()
        self.firmware = fw
        self.protocol = "grbl" if fw == "grbl" else ("ping" if fw else self.protocol)
        if self.protocol not in ("grbl", "ping"):
            self.protocol = "ping"
        self.on_firmware(fw, text)

    def _run(self):
        try:
            self._detect()
        except Exception as e:
            self.on_log(f"!! Erkennung fehlgeschlagen: {e}")
        self.on_state("idle")
        buf = b""
        last_poll = 0.0
        while self.running:
            try:
                # Echtzeitbefehle
                while not self._rt_q.empty():
                    b = self._rt_q.get_nowait()
                    self._write(b)
                    if b == b"\x18":
                        self._pending.clear()
                        self._extra_ok = 0
                    elif b.endswith(b"\n"):
                        self._extra_ok += 1
                        self.on_log(">> " + b.decode(errors="replace").strip())
                if self.protocol == "grbl" and time.time() - last_poll > 0.25:
                    self._write(b"?")
                    last_poll = time.time()
                # Empfangen
                data = self.ser.read(512)
                if data:
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._handle(line.decode(errors="replace").strip())
                # Senden
                self._fill_buffer()
            except Exception as e:     # Kabel gezogen o. Ä.
                if self._job_active:
                    self.last_job, self.last_acked = list(self._job), self._job_acked
                self.on_log(f"!! Verbindungsfehler: {e}")
                self.running = False
                self._job_active = False
                self.on_state("error")
                break

    def _capacity_ok(self, length: int) -> bool:
        if self.protocol == "grbl":
            return sum(p[0] for p in self._pending) + length <= GRBL_RX_BUFFER
        return not self._pending

    def _fill_buffer(self):
        while True:
            if not self._cmd_q.empty():
                line = self._cmd_q.queue[0]
                if not self._capacity_ok(len(line) + 1):
                    return
                self._cmd_q.get_nowait()
                self._write((line + "\n").encode())
                self._pending.append((len(line) + 1, False))
                self.on_log(">> " + line)
                continue
            with self._lock:
                if not self._job_active or self._paused or self._job_idx >= len(self._job):
                    return
                line = self._job[self._job_idx]
                if not self._capacity_ok(len(line) + 1):
                    return
                self._job_idx += 1
            self._write((line + "\n").encode())
            self._pending.append((len(line) + 1, True))

    def _handle(self, line: str):
        if not line:
            return
        if line.startswith("<") and line.endswith(">"):
            st = parse_grbl_status(line, self._wco)
            self.on_status(st)
            if self._finishing and self._job_active and st["state"].startswith("Idle"):
                self._finishing = False        # 'ok' heißt nur „im Planner“ – fertig ist erst Idle
                self._job_active = False
                self.on_state("done")
            if st["state"].startswith("Alarm") and self._job_active:
                self.last_job, self.last_acked = list(self._job), self._job_acked
                self.on_log("!! Alarm – Auftrag abgebrochen")
                self._job_active = False
                self.on_state("alarm")
            return
        is_ok = line == "ok" or line.startswith("ok ")
        is_err = line.lower().startswith("error")
        if is_ok or is_err:
            was_job = False
            if not self._pending and self._extra_ok:
                self._extra_ok -= 1              # Quittung einer sofort geschriebenen Zeile
            elif self._pending:
                _, was_job = self._pending.pop(0)
                if was_job:
                    self._job_acked += 1
                    total = len(self._job)
                    if self._job_acked % 20 == 0 or self._job_acked >= total:
                        self.on_progress(self._job_acked, total)
                    if self._job_active and self._job_acked >= total and not self._finishing:
                        if self.protocol == "grbl":
                            self._finishing = True     # auf <Idle> warten
                        else:
                            self._job_active = False   # letzte Zeile ist M400: Bewegungen sind fertig
                            self.on_state("done")
            if is_err:
                self.errors += 1
                self.on_log("<< " + line)
                if was_job and self._job_active and self.pause_on_error and not self._paused:
                    self.on_log(f"!! Fehler bei Zeile {self._job_acked} des Auftrags – Auftrag angehalten. "
                                "Prüfen, dann fortsetzen oder stoppen.")
                    self.pause()
            return
        if line.startswith("ALARM"):
            self._pending.clear()
            if self._job_active:
                self.last_job, self.last_acked = list(self._job), self._job_acked
                self._job_active = False
                self.on_state("alarm")
        if line.lower().startswith("grbl") and self._pending:
            self._pending.clear()          # nach Reset ist der Puffer leer
        self.on_log("<< " + line)

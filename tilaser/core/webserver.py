"""Kleine Weboberfläche zur Fernbedienung (nur Standardbibliothek, ohne Qt).

Der Server läuft in einem eigenen Thread. Er greift nie direkt auf die Oberfläche zu:
* Das Hauptfenster legt regelmäßig einen Zustands-Schnappschuss und das letzte Kamerabild (JPEG) ab
  (``WebServer.publish`` / ``publish_jpeg``).
* Befehle aus dem Browser landen in einer Warteschlange (``WebServer.commands``), die das Hauptfenster
  im eigenen Takt abarbeitet.

Zugang: PIN (Pflicht, mindestens 4 Zeichen). Anfragen mit fremdem Host-Namen werden abgewiesen
(Schutz vor DNS-Rebinding). Nach richtiger Eingabe bekommt der Browser ein zufälliges Sitzungs-Cookie
(HttpOnly, SameSite=Strict). Nach mehreren Fehlversuchen wird die Anmeldung kurz gesperrt.
"""
from __future__ import annotations

import hmac
import json
import queue
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

COMMANDS = ("pause", "resume", "stop", "start", "ack")
COOKIE = "tilaser_session"
SESSION_S = 86400                    # Sitzung gilt 24 Stunden
MIN_PIN = 4
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def host_allowed(host_header: str | None) -> bool:
    """Schutz vor DNS-Rebinding: nur IP-Adressen, localhost und der eigene Rechnername als Host.

    Eine fremde Webseite kann den Browser zwar unter ihrem eigenen Namen auf TiLaser umleiten,
    der Host-Kopf trägt dann aber ihren Namen – solche Anfragen werden abgewiesen."""
    import ipaddress
    h = (host_header or "").strip().lower()
    if h.startswith("["):                                   # [IPv6]:port
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    h = h.rstrip(".")
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    me = socket.gethostname().lower().rstrip(".")
    short = me.split(".", 1)[0]
    return h == "localhost" or h in (me, short) or h.startswith(short + ".")


def local_addresses() -> list[str]:
    """IPv4-Adressen dieses Rechners im lokalen Netz (beste zuerst)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))          # sendet nichts, wählt nur die Schnittstelle
            out.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out or ["127.0.0.1"]


def new_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


class WebServer:
    def __init__(self, port: int = 8080, pin: str = "", allow_start: bool = False, host: str = "0.0.0.0"):
        self.host, self.port, self.pin, self.allow_start = host, int(port), str(pin or ""), bool(allow_start)
        self.commands: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._status: dict = {"state": "–"}
        self._jpeg: bytes = b""
        self._jpeg_t = 0.0
        self.camera_wanted_until = 0.0          # Browser hat kürzlich ein Kamerabild angefordert
        self._sessions: dict[str, float] = {}
        self._fails: list[float] = []
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- Lebenszyklus
    def start(self):
        if len(self.pin) < MIN_PIN and self.host not in LOOPBACK:
            raise ValueError(f"Die Weboberfläche braucht eine PIN mit mindestens {MIN_PIN} Zeichen.")
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]        # bei Port 0 den tatsächlichen Port merken
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="TiLaser-Web", daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        self._httpd = self._thread = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}/" for ip in local_addresses()]

    # ---------------------------------------------------------------- Daten vom Hauptfenster
    def publish(self, status: dict):
        with self._lock:
            self._status = dict(status)

    def publish_jpeg(self, data: bytes):
        with self._lock:
            self._jpeg, self._jpeg_t = bytes(data), time.time()

    def camera_wanted(self) -> bool:
        return time.monotonic() < self.camera_wanted_until

    def status(self) -> dict:
        with self._lock:
            st = dict(self._status)
        st["allow_start"] = self.allow_start
        st["camera"] = bool(self._jpeg) and time.time() - self._jpeg_t < 10
        return st

    def jpeg(self) -> bytes:
        with self._lock:
            return self._jpeg if time.time() - self._jpeg_t < 10 else b""

    # ---------------------------------------------------------------- Anmeldung
    def check_pin(self, pin: str) -> str | None:
        """Neues Sitzungs-Token bei richtiger PIN, sonst None (mit Sperre nach Fehlversuchen)."""
        now = time.monotonic()
        self._fails = [t for t in self._fails if now - t < 300]
        if len(self._fails) >= 10:
            return None
        if not hmac.compare_digest(str(pin).strip().encode(), self.pin.encode()):
            self._fails.append(now)
            return None
        tok = secrets.token_urlsafe(24)
        self._sessions[tok] = now
        return tok

    def locked(self) -> bool:
        now = time.monotonic()
        return len([t for t in self._fails if now - t < 300]) >= 10

    def authorized(self, token: str | None) -> bool:
        if not self.pin and self.host in LOOPBACK:
            return True
        now = time.monotonic()
        for t in [t for t, t0 in self._sessions.items() if now - t0 > SESSION_S]:
            self._sessions.pop(t, None)                     # abgelaufene Sitzungen entfernen
        return bool(token) and token in self._sessions

    def logout_all(self):
        self._sessions.clear()


def _make_handler(srv: WebServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TiLaser"
        timeout = 10                         # langsame oder hängende Verbindungen nicht ewig offen halten

        def log_message(self, *_a):          # keine Ausgabe auf der Konsole
            pass

        # -- Hilfen
        def _token(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v
            return None

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj, extra=None):
            if isinstance(obj.get("error"), str):
                obj = dict(obj, error=localized(obj["error"]))
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), extra=extra)

        def _body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if not 0 < n <= 4096:            # negativ hieße "bis zum Verbindungsende lesen"
                return {}
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return {}

        def _host_ok(self) -> bool:
            if host_allowed(self.headers.get("Host")):
                return True
            self._send(421, b"", "text/plain")
            return False

        # -- Routen
        def do_GET(self):
            if not self._host_ok():
                return
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, localized(PAGE).encode("utf-8"), "text/html; charset=utf-8",
                           {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                       "script-src 'self' 'unsafe-inline'; img-src 'self'"})
                return
            if path == "/api/status":
                if not srv.authorized(self._token()):
                    self._json(401, {"login": True, "locked": srv.locked()})
                    return
                self._json(200, srv.status())
                return
            if path == "/cam.jpg":
                if not srv.authorized(self._token()):
                    self._send(401, b"", "text/plain")
                    return
                srv.camera_wanted_until = time.monotonic() + 5.0
                data = srv.jpeg()
                if not data:
                    self._send(204, b"", "text/plain")
                    return
                self._send(200, data, "image/jpeg")
                return
            self._send(404, localized("Nicht gefunden").encode("utf-8"), "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._host_ok():
                return
            path = urlparse(self.path).path
            # Nur Anfragen aus der eigenen Seite (fetch mit eigenem Kopf), kein einfaches Formular von außen
            if self.headers.get("X-TiLaser") != "1":
                self._json(403, {"error": "Ungültige Anfrage"})
                return
            if path == "/api/login":
                tok = srv.check_pin(self._body().get("pin", ""))
                if tok is None:
                    time.sleep(0.8)
                    self._json(403, {"error": "Anmeldung gesperrt – bitte 5 Minuten warten." if srv.locked()
                                     else "Falsche PIN."})
                    return
                self._json(200, {"ok": True},
                           {"Set-Cookie": f"{COOKIE}={tok}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400"})
                return
            if path == "/api/cmd":
                if not srv.authorized(self._token()):
                    self._json(401, {"login": True})
                    return
                cmd = str(self._body().get("cmd", ""))
                if cmd not in COMMANDS:
                    self._json(400, {"error": "Unbekannter Befehl"})
                    return
                if cmd == "start" and not srv.allow_start:
                    self._json(403, {"error": "Starten aus der Ferne ist in TiLaser nicht erlaubt."})
                    return
                srv.commands.put(cmd)
                self._json(200, {"ok": True})
                return
            self._json(404, {"error": "Nicht gefunden"})

        def do_HEAD(self):
            self.do_GET()

    return Handler


# --------------------------------------------------------------------------- Seite
# Deutsche Texte der Seite, die beim Ausliefern übersetzt werden (siehe core/i18n.py)
WEB_PHRASES = ["PIN aus TiLaser", "Maschine → Weboberfläche", "Anmelden", "🔥 Mögliche Flamme erkannt!",
               "Gesehen", "Kein Kamerabild", "Pause", "Stopp", "Auftrag starten", "Konsole",
               "Laser nie unbeaufsichtigt lassen. Diese Seite ersetzt nicht den Blick auf die Maschine.",
               "Tastatur: Leertaste = Pause/Fortsetzen, Esc = Stopp.", "Anmeldung gesperrt – bitte 5 Minuten warten.",
               "Fehler", "aktualisiert ", "keine Verbindung zu TiLaser", "Fortsetzen", "noch ",
               "Auftrag wirklich stoppen?", "Auftrag jetzt starten? Nur wenn jemand am Laser ist!", "Kamerabild",
               "Falsche PIN.", "Ungültige Anfrage", "Unbekannter Befehl", "Nicht gefunden",
               "Starten aus der Ferne ist in TiLaser nicht erlaubt."]


def localized(text: str) -> str:
    """Seite bzw. Meldung in der Sprache der Oberfläche."""
    from .i18n import current
    t = current()
    if not t.active:
        return text
    for ph in sorted(WEB_PHRASES, key=len, reverse=True):
        if ph in text:
            text = text.replace(ph, t.tr(ph.strip()) + (" " if ph.endswith(" ") else ""))
    return (text.replace('lang="de"', f'lang="{t.code}"')
            .replace('toLocaleTimeString("de-DE")', "toLocaleTimeString()"))


PAGE = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>TiLaser</title>
<style>
:root{--alu:#e4e7eb;--alu-hell:#f2f4f6;--alu-dunkel:#c4c9d0;--holz:#dcc196;--brand:#4a2c17;
--laser:#3d63ff;--schiefer:#232a33;--text:#2e3742;--leise:#5a6573;--alarm:#c4221b;
--schrift:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
@media (prefers-color-scheme:dark){:root{--alu:#1b2027;--alu-hell:#232a33;--alu-dunkel:#39424e;--text:#e4e7eb;--leise:#a3adba}}
*{box-sizing:border-box}
body{margin:0;font-family:var(--schrift);background:var(--alu);color:var(--text);line-height:1.45}
main{max-width:46rem;margin:0 auto;padding:1rem 1rem 2.5rem}
header{display:flex;align-items:baseline;justify-content:space-between;gap:1rem}
.marke{font-weight:700;letter-spacing:.01em}
.marke span{color:var(--laser)}
#verbindung{font-size:.85rem;color:var(--leise)}
#zustand{font-size:clamp(2.2rem,9vw,3.4rem);font-weight:800;line-height:1.05;margin:.6rem 0 .2rem}
#pos{font-variant-numeric:tabular-nums;color:var(--leise)}
#kamera{margin:1rem 0;background:var(--schiefer);border-radius:6px;overflow:hidden;aspect-ratio:4/3;
display:grid;place-items:center;color:#a3adba}
#kamera img{width:100%;height:100%;object-fit:contain;display:block}
/* Fortschritt als Sperrholzstreifen, der vom Laser abgebrannt wird */
.streifen{position:relative;height:1.6rem;background:var(--holz);border-radius:3px;overflow:hidden;
box-shadow:inset 0 -2px 0 rgba(0,0,0,.08)}
.streifen .brand{position:absolute;inset:0 auto 0 0;width:0;background:var(--brand);transition:width .6s linear}
.streifen .punkt{position:absolute;top:50%;width:.7rem;height:.7rem;margin:-.35rem 0 0 -.35rem;left:0;
border-radius:50%;background:#fff;box-shadow:0 0 .5rem .25rem var(--laser);transition:left .6s linear;opacity:0}
.laeuft .streifen .punkt{opacity:1}
.zeiten{display:flex;justify-content:space-between;font-variant-numeric:tabular-nums;margin:.35rem 0 1.2rem;color:var(--leise)}
.knoepfe{display:grid;grid-template-columns:1fr 1fr;gap:.7rem}
button{font:inherit;font-weight:700;font-size:1.15rem;padding:1rem;border-radius:6px;border:0;cursor:pointer;
background:var(--alu-hell);color:var(--text);box-shadow:inset 0 0 0 2px var(--alu-dunkel)}
button:focus-visible,input:focus-visible{outline:3px solid var(--laser);outline-offset:2px}
button:disabled{opacity:.4;cursor:default}
#stopp{background:var(--alarm);color:#fff;box-shadow:none}
#start{grid-column:1/-1;background:var(--laser);color:#fff;box-shadow:none}
#alarm{display:none;background:var(--alarm);color:#fff;padding:1rem;border-radius:6px;margin:1rem 0;font-weight:700}
#alarm.an{display:block}
#alarm button{margin-top:.7rem;width:100%;background:#fff;color:var(--alarm);box-shadow:none}
details{margin-top:1.5rem}
summary{cursor:pointer;color:var(--leise)}
pre{white-space:pre-wrap;font-size:.8rem;background:var(--alu-hell);padding:.7rem;border-radius:6px;max-height:16rem;overflow:auto}
#anmelden{display:none;max-width:20rem;margin:4rem auto}
#anmelden input{font:inherit;font-size:1.6rem;letter-spacing:.3em;width:100%;padding:.6rem;text-align:center;
border-radius:6px;border:2px solid var(--alu-dunkel);background:var(--alu-hell);color:var(--text)}
#anmelden button{width:100%;margin-top:.8rem;background:var(--laser);color:#fff;box-shadow:none}
#meldung{min-height:1.4rem;color:var(--alarm);margin-top:.6rem}
.hinweis{font-size:.85rem;color:var(--leise);margin-top:1.5rem}
.pc{display:none}
main{display:grid;grid-template-columns:minmax(0,1fr);grid-template-areas:"titel" "kopf" "kamera" "bedienung"}
header{grid-area:titel}.kopf{grid-area:kopf}#kamera{grid-area:kamera}.bedienung{grid-area:bedienung}
/* PC und Tablet quer: Kamera groß links, Bedienung rechts */
@media (min-width:900px){
main{max-width:84rem;padding:1.5rem 2rem 3rem;column-gap:2rem;grid-template-columns:minmax(0,1.7fr) minmax(19rem,1fr);
grid-template-rows:auto auto 1fr;grid-template-areas:"titel titel" "kamera kopf" "kamera bedienung"}
#kamera{margin:.6rem 0 0;align-self:start;position:sticky;top:1rem}
#zustand{margin-top:0}.bedienung{margin-top:1.2rem}
.pc{display:inline}
pre{max-height:22rem}}
</style></head><body>
<form id="anmelden"><p class="marke">Ti<span>Laser</span></p>
<label for="pin">PIN aus TiLaser (<i>Maschine → Weboberfläche</i>)</label>
<input id="pin" inputmode="numeric" autocomplete="one-time-code" autofocus>
<button>Anmelden</button><div id="meldung" role="alert"></div></form>
<main id="haupt" hidden>
<header><span class="marke">Ti<span>Laser</span></span><span id="verbindung"></span></header>
<div class="kopf">
<div id="alarm" role="alert"><div id="alarmtext">🔥 Mögliche Flamme erkannt!</div>
<button id="quittieren">Gesehen</button></div>
<div id="zustand">…</div><div id="pos"></div></div>
<div id="kamera"><span>Kein Kamerabild</span></div>
<div class="bedienung">
<div class="streifen" aria-hidden="true"><div class="brand"></div><div class="punkt"></div></div>
<div class="zeiten"><span id="zeit"></span><span id="rest"></span></div>
<div class="knoepfe">
<button id="pause" title="Leertaste">Pause</button><button id="stopp" title="Esc">Stopp</button>
<button id="start" hidden>Auftrag starten</button></div>
<details id="konsole"><summary>Konsole</summary><pre id="log"></pre></details>
<p class="hinweis">Laser nie unbeaufsichtigt lassen. Diese Seite ersetzt nicht den Blick auf die Maschine.
<span class="pc">Tastatur: Leertaste = Pause/Fortsetzen, Esc = Stopp.</span></p></div>
</main>
<script>
const $=id=>document.getElementById(id);let st={},alarmSeen=0,camTimer=null,lastOk=0;
async function api(path,body){const o={credentials:"same-origin",headers:{"X-TiLaser":"1"}};
if(body!==undefined){o.method="POST";o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}
const r=await fetch(path,o);let j={};try{j=await r.json()}catch(e){}return [r.status,j]}
function zeigeLogin(t){$("haupt").hidden=true;$("anmelden").style.display="block";$("meldung").textContent=t||""}
$("anmelden").onsubmit=async e=>{e.preventDefault();const [c,j]=await api("/api/login",{pin:$("pin").value});
if(c===200){$("anmelden").style.display="none";$("pin").value="";tick()}else $("meldung").textContent=j.error||"Fehler"};
function beep(){try{const a=new (window.AudioContext||window.webkitAudioContext)(),o=a.createOscillator();
o.frequency.value=880;o.connect(a.destination);o.start();setTimeout(()=>{o.stop();a.close()},600)}catch(e){}
if(navigator.vibrate)navigator.vibrate([300,150,300,150,600])}
function render(){const s=st;$("haupt").hidden=false;
$("zustand").textContent=s.state||"–";$("pos").textContent=s.pos||"";
document.body.classList.toggle("laeuft",s.machine==="running");
const p=Math.max(0,Math.min(100,s.progress||0));document.querySelector(".brand").style.width=p+"%";
document.querySelector(".punkt").style.left=p+"%";
$("zeit").textContent=s.time||"";$("rest").textContent=s.remaining?("noch "+s.remaining):"";
const job=s.job_active;$("pause").disabled=!job;$("stopp").disabled=!s.connected;
$("pause").textContent=s.machine==="paused"?"Fortsetzen":"Pause";
$("start").hidden=!s.allow_start;$("start").disabled=!s.can_start;
$("log").textContent=(s.log||[]).join("\n");
const a=s.flame_alarm||0;$("alarm").classList.toggle("an",a>0&&a!==alarmSeen);
if(a>0&&a!==alarmSeen&&!$("alarm").dataset.piep){$("alarm").dataset.piep=a;beep()}
$("alarmtext").textContent="🔥 Mögliche Flamme erkannt!"+(s.flame_did?" "+s.flame_did:"");
$("verbindung").textContent="aktualisiert "+new Date().toLocaleTimeString("de-DE");
if(s.camera&&!camTimer)startCam();}
function startCam(){const k=$("kamera");let img=k.querySelector("img");if(!img){img=new Image();img.alt="Kamerabild";k.textContent="";k.appendChild(img)}
camTimer=setInterval(()=>{if(!document.hidden)img.src="/cam.jpg?t="+Date.now()},1000)}
async function tick(){try{const [c,j]=await api("/api/status");
if(c===401){zeigeLogin(j.locked?"Anmeldung gesperrt – bitte 5 Minuten warten.":"");return}
st=j;lastOk=Date.now();render()}catch(e){$("verbindung").textContent="keine Verbindung zu TiLaser"}
setTimeout(tick,document.hidden?5000:1000)}
async function cmd(c){if(c==="stop"&&!confirm("Auftrag wirklich stoppen?"))return;
if(c==="start"&&!confirm("Auftrag jetzt starten? Nur wenn jemand am Laser ist!"))return;
const [code,j]=await api("/api/cmd",{cmd:c});if(code!==200)alert(j.error||"Fehler")}
$("pause").onclick=()=>cmd(st.machine==="paused"?"resume":"pause");
$("stopp").onclick=()=>cmd("stop");$("start").onclick=()=>cmd("start");
$("quittieren").onclick=()=>{alarmSeen=st.flame_alarm;cmd("ack");render()};
document.addEventListener("keydown",e=>{if($("haupt").hidden||e.target.tagName==="INPUT")return;
if(e.code==="Space"&&st.job_active){e.preventDefault();cmd(st.machine==="paused"?"resume":"pause")}
else if(e.key==="Escape"&&st.connected){e.preventDefault();cmd("stop")}});
if(matchMedia("(min-width:900px)").matches)$("konsole").open=true;
tick();
</script></body></html>
"""

"""Auftragsprotokoll: jeder Laserauftrag als Zeile in einer CSV-Datei.

Die Datei ``auftraege.csv`` liegt im Einstellungsordner (portabel: ``daten``). Sie ist mit
Semikolon getrennt und UTF-8 mit BOM, damit Excel und LibreOffice sie direkt richtig öffnen.
"""
from __future__ import annotations

import csv
import os
import time
from dataclasses import asdict, dataclass, fields

from . import storage

LOG_FILE = os.path.join(storage.CONFIG_DIR, "auftraege.csv")
RESULTS = {"done": "fertig", "stopped": "gestoppt", "alarm": "Alarm", "error": "Verbindungsfehler",
           "disconnected": "getrennt"}
HEADERS = {"start": "Start", "end": "Ende", "duration_s": "Dauer (s)", "estimate_s": "Geschätzt (s)",
           "result": "Ergebnis", "kind": "Art", "project": "Projekt", "layers": "Ebenen", "lines": "Zeilen",
           "source": "Gestartet über", "flame": "Flammenalarm", "cost": "Kosten", "note": "Notiz"}


@dataclass
class JobEntry:
    start: str = ""
    end: str = ""
    duration_s: float = 0.0
    estimate_s: float = 0.0
    result: str = ""
    kind: str = "Auftrag"
    project: str = ""
    layers: str = ""
    lines: int = 0
    source: str = "TiLaser"
    flame: str = ""
    cost: float = 0.0
    note: str = ""


def job_cost(duration_s: float, rate_per_hour: float, power_w: float, kwh_price: float) -> float:
    """Maschinenstundensatz plus Strom."""
    h = max(0.0, duration_s) / 3600.0
    return round(h * rate_per_hour + h * power_w / 1000.0 * kwh_price, 2)


def layer_summary(project, only_selected_ids=None) -> str:
    parts = []
    used = {vp.layer_id for it in project.items for vp in getattr(it, "paths", [])}
    used |= {getattr(it, "layer_id", None) for it in project.items}
    for l in project.layers:
        if l.id in used and getattr(l, "output", True):
            parts.append(f"{l.name} ({l.power:g} %, {l.speed:g} mm/min, {l.passes}×)")
    return ", ".join(parts)


def append(entry: JobEntry, path: str = LOG_FILE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow([HEADERS[k.name] for k in fields(JobEntry)])
        row = asdict(entry)
        w.writerow([_fmt(row[k.name]) for k in fields(JobEntry)])


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}".replace(".", ",")
    return v


def _num(s: str) -> float:
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return 0.0


def read(path: str = LOG_FILE) -> list[JobEntry]:
    if not os.path.exists(path):
        return []
    names = {v: k for k, v in HEADERS.items()}
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f, delimiter=";")
        head = next(r, None)
        if not head:
            return []
        keys = [names.get(h, h) for h in head]
        for row in r:
            if not row:
                continue
            d = dict(zip(keys, row))
            e = JobEntry()
            for fl in fields(JobEntry):
                if fl.name in d:
                    val: object = d[fl.name]
                    if fl.type in ("float", float):
                        val = _num(str(val))
                    elif fl.type in ("int", int):
                        val = int(_num(str(val)))
                    setattr(e, fl.name, val)
            out.append(e)
    return out


def clear(path: str = LOG_FILE):
    if os.path.exists(path):
        os.remove(path)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def totals(entries: list[JobEntry]) -> dict:
    done = [e for e in entries if e.result == RESULTS["done"]]
    return {"count": len(entries), "done": len(done), "duration_s": sum(e.duration_s for e in entries),
            "cost": round(sum(e.cost for e in entries), 2)}

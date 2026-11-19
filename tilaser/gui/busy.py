"""Längere Berechnungen im Hintergrund, ohne dass die Oberfläche einfriert.

``run_busy(parent, text, fn, *args)`` führt ``fn`` in einem eigenen Thread aus und gibt das Ergebnis zurück
(oder löst dessen Ausnahme aus) – für den Aufrufer also wie ein normaler Funktionsaufruf. Solange es dauert,
läuft die Ereignisschleife weiter: Fenster zeichnen sich neu, Laser-Status, Kamera, Flammenwächter und die
Weboberfläche arbeiten weiter. Ab etwa 0,15 s erscheint ein modaler Hinweis, der weitere Eingaben sperrt.

``fn`` darf nur Daten lesen und berechnen, aber keine Qt-Objekte anfassen.
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import QEventLoop, QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QProgressDialog

_active = [0]


def active() -> bool:
    """Läuft gerade eine Hintergrundberechnung (z. B. um verschachtelte Starts abzulehnen)?"""
    return _active[0] > 0


class _Done(QObject):
    finished = pyqtSignal()


def run_busy(parent, text: str, fn, *args, **kwargs):
    result: dict = {}
    sig = _Done()
    loop = QEventLoop()
    sig.finished.connect(loop.quit)

    def work():
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as e:            # noqa: BLE001 – wird im Hauptthread erneut ausgelöst
            result["error"] = e
        finally:
            sig.finished.emit()               # über Thread-Grenzen: wird in der Schleife zugestellt

    _active[0] += 1
    th = threading.Thread(target=work, name="tilaser-worker", daemon=True)
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        th.start()
        th.join(0.15)                         # kurze Aufgaben ohne aufblitzenden Dialog
        if th.is_alive():
            dlg = QProgressDialog(text, "", 0, 0, parent)
            dlg.setCancelButton(None)
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            dlg.setMinimumDuration(0)
            dlg.setWindowTitle("TiLaser")
            dlg.show()
            if th.is_alive():
                loop.exec()
            dlg.close()
        th.join()
    finally:
        _active[0] -= 1
        QApplication.restoreOverrideCursor()
    if "error" in result:
        raise result["error"]
    return result.get("value")

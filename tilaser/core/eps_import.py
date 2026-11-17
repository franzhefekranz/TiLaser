"""EPS/PS-Import (und alte, PostScript-basierte Illustrator-Dateien).

PostScript ist eine Programmiersprache; die Datei wird deshalb mit Ghostscript (bevorzugt)
oder Inkscape in eine PDF umgewandelt und dann mit dem PDF-Importer gelesen.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile

from .pdf_import import import_pdf


class EpsError(RuntimeError):
    pass


def find_converter() -> tuple[str, str] | None:
    """('gs' | 'inkscape', Pfad) oder None."""
    for name in ("gswin64c", "gswin32c", "gs"):
        p = shutil.which(name)
        if p:
            return "gs", p
    if sys.platform.startswith("win"):
        for pat in (r"C:\Program Files\gs\gs*\bin\gswin64c.exe", r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe"):
            hits = sorted(glob.glob(pat))
            if hits:
                return "gs", hits[-1]
    p = shutil.which("inkscape")
    if not p and sys.platform.startswith("win"):
        for cand in (r"C:\Program Files\Inkscape\bin\inkscape.exe", r"C:\Program Files\Inkscape\inkscape.exe"):
            if os.path.exists(cand):
                p = cand
                break
    if not p and sys.platform == "darwin" and os.path.exists("/Applications/Inkscape.app/Contents/MacOS/inkscape"):
        p = "/Applications/Inkscape.app/Contents/MacOS/inkscape"
    return ("inkscape", p) if p else None


def import_eps(path: str):
    conv = find_converter()
    if conv is None:
        raise EpsError("Für EPS/PostScript wird Ghostscript oder Inkscape benötigt.\n"
                       "Ghostscript: https://ghostscript.com/releases/gsdnld.html\n"
                       "Alternativ die Datei im Grafikprogramm als PDF oder SVG speichern.")
    kind, exe = conv
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "konvertiert.pdf")
        if kind == "gs":
            cmd = [exe, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dQUIET", "-sDEVICE=pdfwrite", "-dEPSCrop",
                   "-dNoOutputFonts", f"-sOutputFile={out}", path]
        else:
            cmd = [exe, path, "--export-type=pdf", f"--export-filename={out}", "--export-text-to-path"]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise EpsError("Umwandlung dauerte zu lange (Zeitüberschreitung).")
        if not os.path.exists(out):
            msg = (r.stderr or r.stdout or b"").decode(errors="replace").strip()[-400:]
            raise EpsError(f"Umwandlung mit {os.path.basename(exe)} fehlgeschlagen.\n{msg}")
        paths, warnings = import_pdf(out)
    return paths, warnings + [f"Über {'Ghostscript' if kind == 'gs' else 'Inkscape'} in PDF umgewandelt."]

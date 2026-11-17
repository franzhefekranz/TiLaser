"""Eigene Symbole für Werkzeugleisten und Menüs.

Alle Symbole sind 24 × 24-SVGs mit Linien in der Textfarbe der Oberfläche (``C``), damit sie in hellen
und dunklen Farbschemata passen. Gerendert wird über das SVG-Bildformat von Qt; fehlt es, bleibt das
Symbol leer und Qt zeigt stattdessen den Text der Aktion.
"""
from __future__ import annotations

_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="C" '
         'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">')
_FILL = 'fill="C" fill-opacity="0.3"'

# Kreise der Booleschen Symbole: Mittelpunkte (9|12) und (15|12), Radius 5,5 → Schnittpunkte (12|7,39), (12|16,61)
ICONS: dict[str, str] = {
    "new": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M12 11v6M9 14h6"/>',
    "open": '<path d="M3 7a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>'
            '<path d="M3 11h18"/>',
    "save": '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4"/><path d="M8 20v-6h8v6"/>',
    "import": '<path d="M12 3v11"/><path d="M8 10l4 4 4-4"/><path d="M4 15v4h16v-4"/>',
    "export_file": '<path d="M12 14V3"/><path d="M8 7l4-4 4 4"/><path d="M4 15v4h16v-4"/>',
    "text": '<path d="M5 7V4h14v3"/><path d="M12 4v16"/><path d="M9 20h6"/>',
    "fit": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/><rect x="9" y="9" width="6" height="6"/>',
    "toolpath": '<path d="M4 19l5-11 5 8 6-11"/><path d="M4 19h16" stroke-dasharray="1.5 2.5"/>'
                '<circle cx="4" cy="19" r="1.3" fill="C"/><circle cx="20" cy="5" r="1.3" fill="C"/>',
    "play": '<path d="M8 5v14l11-7z" fill="C"/>',
    "pause": '<path d="M8 5v14M16 5v14" stroke-width="3"/>',
    "laser": '<rect x="7" y="3" width="10" height="7" rx="1"/><path d="M10 10l2 3 2-3"/>'
             '<path d="M12 14v4" stroke-dasharray="1.2 1.6"/><path d="M4 21h16"/>',
    "gcode": '<path d="M8 6l-5 6 5 6M16 6l5 6-5 6M13.5 5l-3 14"/>',
    "group": '<rect x="3" y="3" width="18" height="18" rx="1" stroke-dasharray="2 2.2"/>'
             '<rect x="7" y="7" width="6" height="6"/><rect x="11" y="11" width="6" height="6"/>',
    "ungroup": '<rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/>'
               '<path d="M15 5h4v4M9 19H5v-4" stroke-dasharray="1.5 1.8"/>',
    "center": '<rect x="3" y="3" width="18" height="18" rx="1"/><circle cx="12" cy="12" r="3"/>'
              '<path d="M12 6v3M12 15v3M6 12h3M15 12h3"/>',
    "origin": '<path d="M4 3v17h17"/><rect x="7" y="11" width="6" height="6" ' + _FILL + '/>'
              '<circle cx="4" cy="20" r="1.6" fill="C"/>',
    "duplicate": '<rect x="9" y="9" width="12" height="12" rx="1"/>'
                 '<path d="M15 9V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h5"/>',
    "delete": '<path d="M4 7h16M10 7V4h4v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    "select": '<path d="M5 3l5.5 17 2.6-7.3L20.5 10z"/>',
    "rect": '<rect x="4" y="6" width="16" height="12" rx="1"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8.5" ry="6.5"/>',
    "polygon": '<path d="M12 3l7.8 4.5v9L12 21l-7.8-4.5v-9z"/>',
    "line": '<path d="M4 18l6-10 5 7 5-9"/><rect x="2.8" y="16.8" width="2.4" height="2.4" fill="C"/>'
            '<rect x="8.8" y="6.8" width="2.4" height="2.4" fill="C"/><rect x="13.8" y="13.8" width="2.4" '
            'height="2.4" fill="C"/><rect x="18.8" y="4.8" width="2.4" height="2.4" fill="C"/>',
    "node": '<path d="M3 20C5 11 8 8 12 8s7 3 9 12"/><path d="M5 8h14" stroke-width="1.2"/>'
            '<rect x="10.5" y="6.5" width="3" height="3" fill="C"/><circle cx="5" cy="8" r="1.5"/>'
            '<circle cx="19" cy="8" r="1.5"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
    "cut": '<circle cx="6" cy="17" r="3"/><circle cx="18" cy="17" r="3"/><path d="M8.2 15L19 3M15.8 15L5 3"/>',
    "paste": '<rect x="5" y="4" width="14" height="17" rx="1"/><rect x="9" y="2" width="6" height="4" rx="1"/>'
             '<path d="M9 11h6M9 15h4"/>',
    "select_all": '<rect x="3" y="3" width="18" height="18" rx="1" stroke-dasharray="2 2.2"/>'
                  '<rect x="7" y="7" width="4" height="4" ' + _FILL + '/><rect x="13" y="7" width="4" height="4" '
                  + _FILL + '/><rect x="7" y="13" width="10" height="4" ' + _FILL + '/>',
    "align_left": '<path d="M4 3v18"/><rect x="7" y="6" width="12" height="4"/><rect x="7" y="14" width="7" height="4"/>',
    "align_hcenter": '<path d="M12 3v18"/><rect x="5" y="6" width="14" height="4"/><rect x="8" y="14" width="8" '
                     'height="4"/>',
    "align_right": '<path d="M20 3v18"/><rect x="5" y="6" width="12" height="4"/><rect x="10" y="14" width="7" '
                   'height="4"/>',
    "align_top": '<path d="M3 4h18"/><rect x="6" y="7" width="4" height="12"/><rect x="14" y="7" width="4" height="7"/>',
    "align_vcenter": '<path d="M3 12h18"/><rect x="6" y="5" width="4" height="14"/><rect x="14" y="8" width="4" '
                     'height="8"/>',
    "align_bottom": '<path d="M3 20h18"/><rect x="6" y="5" width="4" height="12"/><rect x="14" y="10" width="4" '
                    'height="7"/>',
    "distribute_h": '<path d="M3 3v18M21 3v18"/><rect x="7" y="7" width="3" height="10"/><rect x="14" y="7" '
                    'width="3" height="10"/>',
    "distribute_v": '<path d="M3 3h18M3 21h18"/><rect x="7" y="7" width="10" height="3"/><rect x="7" y="14" '
                    'width="10" height="3"/>',
    "mirror_h": '<path d="M12 3v18" stroke-dasharray="2 2"/><path d="M9 6L3 18h6z"/><path d="M15 6l6 12h-6z" '
                + _FILL + '/>',
    "mirror_v": '<path d="M3 12h18" stroke-dasharray="2 2"/><path d="M6 9L18 3v6z"/><path d="M6 15l12 6v-6z" '
                + _FILL + '/>',
    "rotate_left": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
    "rotate_right": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "array": ''.join(f'<rect x="{x}" y="{y}" width="4.5" height="4.5" rx="0.5"/>'
                     for y in (3.5, 9.75, 16) for x in (3.5, 9.75, 16)),
    "nest": '<rect x="3" y="3" width="18" height="18" rx="1"/><rect x="5.5" y="5.5" width="7" height="6" '
            + _FILL + '/><circle cx="16.5" cy="8.5" r="3" ' + _FILL + '/><path d="M5.5 18.5l3.5-5 3.5 5z" '
            + _FILL + '/><rect x="14.5" y="14" width="4.5" height="4.5" ' + _FILL + '/>',
    "union": '<path d="M12 7.39A5.5 5.5 0 1 0 12 16.61A5.5 5.5 0 1 0 12 7.39z" ' + _FILL + '/>',
    "difference": '<path d="M12 7.39A5.5 5.5 0 1 0 12 16.61A5.5 5.5 0 0 1 12 7.39z" ' + _FILL + '/>'
                  '<path d="M12 7.39A5.5 5.5 0 1 1 12 16.61" stroke-dasharray="1.6 1.8"/>',
    "intersection": '<path d="M12 7.39A5.5 5.5 0 0 1 12 16.61A5.5 5.5 0 0 1 12 7.39z" ' + _FILL + '/>'
                    '<path d="M12 7.39A5.5 5.5 0 1 0 12 16.61M12 7.39A5.5 5.5 0 1 1 12 16.61" '
                    'stroke-dasharray="1.6 1.8"/>',
    "xor": '<path fill-rule="evenodd" d="M3.5 12a5.5 5.5 0 1 0 11 0a5.5 5.5 0 1 0-11 0zM9.5 12a5.5 5.5 0 1 0 11 0'
           'a5.5 5.5 0 1 0-11 0z" ' + _FILL + '/>',
    "offset": '<rect x="8" y="8" width="8" height="8"/><rect x="3.5" y="3.5" width="17" height="17" rx="4.5" '
              'stroke-dasharray="2 2"/>',
    "join": '<path d="M3 18c4 0 5-10 9-10"/><path d="M12 8c4 0 5 10 9 10"/><circle cx="12" cy="8" r="2" fill="C"/>',
    "trace": '<rect x="3" y="5" width="18" height="14" rx="1"/><path d="M3 16l5-5 4 4 3-3 6 6"/>'
             '<circle cx="16" cy="9" r="1.5"/>',
    "textpath": '<path d="M3 19c4-7 14-7 18 0" stroke-dasharray="1.6 1.8"/><path d="M8 4h8M12 4v8"/>',
    "mattest": ''.join(f'<rect x="{3 + 6.2 * c}" y="{3 + 6.2 * r}" width="5" height="5" fill="C" '
                       f'fill-opacity="{0.12 + 0.11 * (r * 3 + c):.2f}"/>' for r in range(3) for c in range(3)),
    "camera": '<rect x="3" y="7" width="18" height="13" rx="2"/><circle cx="12" cy="13.5" r="3.5"/>'
              '<path d="M8 7l1.5-3h5L16 7"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/>'
                '<circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "snap": ''.join(f'<circle cx="{x}" cy="{y}" r="0.9" fill="C" stroke="none"/>'
                    for y in (4, 9, 14, 19) for x in (4, 9, 14, 19))
            + '<rect x="9" y="9" width="10" height="10" ' + _FILL + '/>',
    "quit": '<path d="M14 4h5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-5"/><path d="M10 8l-4 4 4 4M6 12h10"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.5" r="0.6" fill="C"/>',
}

# Aktionstext (ohne „…“) -> Symbol, für alle Menüeinträge und Werkzeugleisten
BY_TEXT = {
    "Neu": "new", "Projekt öffnen": "open", "Projekt speichern": "save", "Projekt speichern unter": "save",
    "Importieren": "import", "Text einfügen": "text", "G-Code exportieren": "laser",
    "Als SVG exportieren": "export_file", "Als DXF exportieren": "export_file", "Beenden": "quit",
    "Rückgängig": "undo", "Wiederholen": "redo", "Ausschneiden": "cut", "Kopieren": "duplicate",
    "Einfügen": "paste", "An Originalposition einfügen": "paste", "Duplizieren": "duplicate",
    "Löschen": "delete", "Alles auswählen": "select_all", "Gruppieren": "group",
    "Gruppierung aufheben": "ungroup", "Zentrieren": "center", "An 0/0": "origin",
    "Links ausrichten": "align_left", "Horizontal mittig ausrichten": "align_hcenter",
    "Rechts ausrichten": "align_right", "Oben ausrichten": "align_top",
    "Vertikal mittig ausrichten": "align_vcenter", "Unten ausrichten": "align_bottom",
    "Horizontal verteilen (gleiche Abstände)": "distribute_h", "Vertikal verteilen (gleiche Abstände)": "distribute_v",
    "Horizontal spiegeln": "mirror_h", "Vertikal spiegeln": "mirror_v",
    "90° nach links drehen": "rotate_left", "90° nach rechts drehen": "rotate_right",
    "Array erstellen": "array", "Nesting (Teile anordnen)": "nest",
    "Vereinigen": "union", "Abziehen (größte Form minus andere)": "difference", "Schnittmenge": "intersection",
    "Ausschluss (XOR)": "xor", "Offset": "offset", "Pfade verbinden / schließen": "join",
    "Bild nachzeichnen": "trace", "Text auf Pfad": "textpath", "Materialtest": "mattest",
    "Alles zeigen": "fit", "Werkzeugweg-Vorschau": "toolpath", "G-Code anzeigen": "gcode",
    "Am Raster fangen": "snap", "Maschineneinstellungen": "settings", "Kamera wählen": "camera",
    "Kamera ausrichten": "camera", "Kamerabild aktualisieren": "camera", "Über TiLaser": "info",
}

_cache: dict = {}


def svg(name: str, color: str = "#000000") -> str:
    """Vollständiges SVG eines Symbols in der angegebenen Farbe."""
    return (_HEAD + ICONS[name] + "</svg>").replace('"C"', f'"{color}"')


def _text_color() -> str:
    try:
        from PyQt6.QtGui import QPalette
        from PyQt6.QtWidgets import QApplication
        c = QApplication.palette().color(QPalette.ColorRole.WindowText)
        name = c.name()
        return name if isinstance(name, str) and name.startswith("#") else "#202124"
    except Exception:
        return "#202124"


def icon(name: str):
    """QIcon für ein Symbol (leer, falls unbekannt oder SVG nicht ladbar)."""
    from PyQt6.QtGui import QIcon, QPixmap
    color = _text_color()
    key = (name, color)
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    if name in ICONS:
        data = svg(name, color).encode()
        for size in (16, 24, 32, 48):
            data_sized = data.replace(b'viewBox="0 0 24 24"',
                                      f'viewBox="0 0 24 24" width="{size}" height="{size}"'.encode(), 1)
            pm = QPixmap()
            if pm.loadFromData(data_sized, "SVG"):
                ic.addPixmap(pm)
    _cache[key] = ic
    return ic


def icon_for_text(text: str):
    key = text.replace("&", "").rstrip("…").rstrip(".").strip()
    name = BY_TEXT.get(key)
    return icon(name) if name else None

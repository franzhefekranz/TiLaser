# TiLaser

Programm zum Schneiden und Gravieren mit G-Code-gesteuerten Lasern (GRBL/FluidNC, Marlin, Smoothieware).
Python 3.10+ mit PyQt6-Oberfläche. Lizenz: GPL-3.0.

## Download

Fertige Pakete gibt es unter **Releases** (werden von GitHub Actions bei jedem Tag `v*` gebaut):

| Datei | System |
|---|---|
| `TiLaser-<version>-Setup.exe` | Windows-Installer (Startmenü, optional Desktop-Symbol und Verknüpfung mit `.tlp`-Projekten) |
| `TiLaser-<version>-portable-win64.zip` | Windows portabel: entpacken, `TiLaser.exe` starten. Einstellungen landen im Unterordner `daten`, nichts in Registry oder Benutzerprofil |
| `TiLaser-<version>-x86_64.AppImage` | Linux PC (x86_64): `chmod +x` und starten |
| `tilaser_<version>_amd64.deb` | Debian, Ubuntu, Mint auf PC (x86_64): `sudo apt install ./tilaser_<version>_amd64.deb` |
| `TiLaser-<version>-aarch64.AppImage` | Linux ARM64, z. B. Raspberry Pi 4/5 mit 64-Bit-System |
| `tilaser_<version>_arm64.deb` | Raspberry Pi OS 64-Bit, Debian, Ubuntu auf ARM64: `sudo apt install ./tilaser_<version>_arm64.deb` |

**Systemvoraussetzungen Linux** (vorgegeben durch die PyQt6-Pakete):

| Architektur | glibc | z. B. |
|---|---|---|
| x86_64 | ≥ 2.35 | Ubuntu 22.04+, Debian 12+, Mint 21+ |
| ARM64 | ≥ 2.39 | **Raspberry Pi OS Trixie (Debian 13)**, Ubuntu 24.04+. Raspberry Pi OS *Bookworm* ist zu alt. Dort aus dem Quellcode starten (`sudo apt install python3-pyqt6 python3-numpy python3-pil python3-fonttools python3-serial`, dann `python3 main.py`) |

32-Bit-Systeme (armhf) werden nicht unterstützt.

Unter Linux braucht der Benutzer Zugriff auf den USB-Seriell-Port: `sudo usermod -aG dialout $USER`, danach
ab- und wieder anmelden.

## Aus dem Quellcode starten

```bash
pip install -r requirements.txt
python main.py            # oder: python -m tilaser  [dateien…]
```

Abhängigkeiten: PyQt6, numpy, Pillow, fonttools, pyserial. SVG- und DXF-Import sind selbst geschrieben,
es braucht also keine zusätzlichen Bibliotheken dafür.

## Pakete selbst bauen

| Ziel | Befehl | Ergebnis in `dist/release/` |
|---|---|---|
| Windows: EXE, Installer, portabel | `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` (benötigt [Inno Setup 6](https://jrsoftware.org/isdl.php)) | `TiLaser-…-Setup.exe`, `TiLaser-…-portable-win64.zip` |
| Linux: AppImage und deb | `bash packaging/build_linux.sh` (baut für die Architektur des Rechners, x86_64 oder ARM64, z. B. direkt auf dem Pi) | `TiLaser-…-<arch>.AppImage`, `tilaser_…_<arch>.deb` |

PyInstaller baut einen Ordner (`dist/TiLaser`), keine Einzeldatei-EXE. Das startet schneller und wird seltener
von Virenscannern beanstandet. Die Konfiguration steht in `packaging/tilaser.spec`, das Installer-Skript in
`packaging/windows/tilaser.iss`.

**Neue Version veröffentlichen:** `__version__` in `tilaser/__init__.py` erhöhen, committen, dann
`git tag v0.2.0 && git push --tags`. Der Workflow `.github/workflows/build.yml` baut alle Pakete und hängt sie an
ein GitHub-Release an.

## Funktionen

| Bereich | Umfang |
|---|---|
| Import | **SVG** (Pfade inkl. Bögen, Rechtecke mit Rundung, Kreise, Ellipsen, Linien, Polygone, Gruppen, `use`, Transformationen, viewBox, Einheiten), **DXF** (LINE, LWPOLYLINE mit Bulges, POLYLINE, CIRCLE, ARC, ELLIPSE, SPLINE/NURBS, Blöcke/INSERT, `$INSUNITS`), **Bilder** (PNG, JPG, BMP, GIF, TIFF, WebP), **Text** aus jeder TTF/OTF-Schrift |
| Ebenen | Automatisch nach SVG-Farbe bzw. DXF-Layer. Modi: *Schneiden/Linie*, *Füllen (Schraffur)*, *Rasterbild*. Leistung, Geschwindigkeit, Durchgänge, Zeilenabstand, Winkel, Kreuzschraffur, Overscan, bidirektional, Air Assist, Reihenfolge per ▲▼ |
| Bilder | Floyd-Steinberg, Atkinson, Schwellwert, Graustufen (Leistung min/max), Invertieren, Vorschau im Canvas |
| Optimierung | Innen- vor Außenkonturen, Nächster-Nachbar-Sortierung, Startpunkt geschlossener Konturen wird optimiert |
| Presets | Material-Presets anwenden und eigene speichern (`~/.tilaser/presets.json`, portabel: `daten/`) |
| Maschine | Verbindung per USB/seriell, **automatische Firmware-Erkennung**, Jog, Home, Entsperren, Nullpunkt setzen, **Rahmen fahren** (Laser aus oder mit geringer Leistung), Start/Pause/Stopp, Fortschritt, Konsole |
| Ausgabe | G-Code-Export, Werkzeugweg-Vorschau (Brenn- und Leerwege), Zeitabschätzung, Warnung bei Objekten außerhalb des Arbeitsbereichs |
| Projekt | Speichern/Laden als `.tlp` (JSON, Bilder eingebettet) |

## Bedienung

- **Importieren** (Strg+I) oder Dateien ins Fenster ziehen.
- Mausrad = Zoom, mittlere Maustaste oder rechte Maustaste *ziehen* = Ansicht verschieben,
  rechte Maustaste *klicken* = Kontextmenü.
- Pfeiltasten verschieben die Auswahl um 1 mm, mit Shift um 10 mm. Entf löscht.
- **Objektleiste** über der Arbeitsfläche: Position, Größe, Skalierung, Drehung, Spiegeln, Ebene.
- **Gruppieren** (Strg+G) und **Gruppierung aufheben** (Strg+Umschalt+G): Beim Aufheben bleibt jede
  Außenkontur mit ihren Löchern zusammen (z. B. ein „O“ oder eine Platte mit Bohrungen). Noch einmal
  aufheben zerlegt so ein Teil in einzelne Konturen. Danach lassen sich Teile einzeln löschen oder
  über *Kontextmenü → Ebene zuweisen* einer anderen Ebene (Farbe) zuordnen, auch einer neuen.
- Rechte Seitenleiste mit Tabs:
  - **Ebenen**: Parameter einstellen bzw. Preset anwenden. Das Häkchen bestimmt, ob die Ebene ausgegeben wird.
  - **Verbindung**: Port, Baudrate, Referenzfahrt, Entsperren, Nullpunkt.
  - **Bewegen**: relativ in 8 Richtungen sowie **absolut** auf eingegebene X/Y-Koordinaten
    (Werkstückkoordinaten; „Aktuelle Position übernehmen“ füllt die Felder).
  - **Konsole**: Maschinenmeldungen und eigene Befehle.
- Darunter ist immer sichtbar: Status, Position, *Rahmen*, *Start*, *Pause* und *Stopp*.
- **Ansicht → Werkzeugweg-Vorschau** (Strg+P) zeigt, was der Laser tatsächlich fährt.
- Erkennt das Programm beim Verbinden eine andere Firmware als im Profil eingestellt,
  fragt es, ob es umstellen soll.

### Reihenfolge

Ebenen werden von oben nach unten abgearbeitet. **Schneiden gehört nach unten**, damit Teile erst
herausfallen, wenn alle Gravuren darauf fertig sind.

## Firmware-Hinweise

Weil die Firmware noch nicht bekannt ist: Beim Verbinden wird sie automatisch erkannt. Alternativ
unter **Maschine → Maschineneinstellungen** einstellen und *Standardwerte übernehmen* klicken.

| Firmware | Ausgabe | Wichtig |
|---|---|---|
| GRBL 1.1 / FluidNC | `M4` (dynamische Leistung), `S` direkt in den `G1`-Zeilen, Streaming mit Puffer-Zeichenzählung | **`$32=1`** (Lasermodus) muss aktiv sein, sonst brennt der Laser auch bei G0 und im Stillstand. `$30` = S bei 100 % (wird beim Verbinden automatisch übernommen). |
| Marlin | `M3 S…` vor jedem Brennpfad, `M5` vor jeder Leerfahrt | Raster ist so langsam. Ist Marlin mit `LASER_POWER_INLINE` gebaut, kann *Inline-Leistung* in den Einstellungen aktiviert werden. |
| Smoothieware | `S` von 0 bis 1 direkt in `G1` | — |
| Generisch | wie Marlin, alle Werte frei einstellbar | Anfangs- und End-G-Code im Profil anpassen |

**Koordinaten:** Der G-Code ist absolut (`G90`) in mm, Nullpunkt links unten (rote Ecke im Canvas).
Bei GRBL setzt *Nullpunkt hier* den Werkstück-Nullpunkt (`G10 L20 P1 X0 Y0`) auf die aktuelle Kopfposition.
Bei Marlin wird dafür `G92` verwendet.

## Sicherheit

- Immer eine **Schutzbrille passend zur Wellenlänge** tragen, Absaugung einschalten, den Laser **nie
  unbeaufsichtigt** laufen lassen und einen Feuerlöscher bereithalten.
- **Stopp** sendet bei GRBL einen Soft-Reset (sofortiger Halt). Danach ggf. *Entsperren* drücken.
- Kein PVC, Vinyl oder Kunstleder lasern (Chlorgas).
- Die mitgelieferten Presets sind **Startwerte für Diodenlaser um 10 W**. Für jede Maschine mit einem Testraster prüfen.

## Aufbau

```
tilaser/
  core/                 # ohne GUI nutzbar und getestet
    geometry.py         # Polylinien, Matrizen, Bézier/Bogen-Abtastung, Pfadoptimierung
    svg_import.py       # SVG-Parser
    dxf_import.py       # DXF-Parser inkl. NURBS und Blöcke
    text_import.py      # Schrift -> Konturen (fontTools)
    raster.py           # Bild laden, skalieren, Dithering
    fill.py             # Scanline-Schraffur (Even-Odd)
    gcode.py            # Maschinenprofil, Dialekte, G-Code-Generator, Framing
    sender.py           # Serielles Streaming, Firmware-Erkennung
    model.py            # Ebenen, Objekte, Projekt
    storage.py          # Presets, Maschinenprofil, Projektdateien
  gui/                  # PyQt6: Hauptfenster, Canvas, Ebenen-/Objekt-/Maschinenpanel, Dialoge
  data/                 # Standard-Presets, Icon
tests/test_core.py      # python tests/test_core.py
packaging/              # PyInstaller-Spec, Inno Setup, Linux-Desktop-/MIME-Dateien, Build-Skripte, Icons
.github/workflows/      # automatischer Build und Release
```

Ohne Oberfläche, z. B. im Skript:

```python
from tilaser.core.model import Project
from tilaser.core.svg_import import import_svg
from tilaser.core.gcode import MachineProfile, generate

p = Project()
p.add_vector_import("teil", import_svg("teil.svg")[0])
prof = MachineProfile(); prof.apply_dialect("grbl")
open("teil.gcode", "w").write(generate(p, prof).text)
```

## Bekannte Grenzen / Ideen für später

- Keine Z-Achse und keine Rotationsachse, keine Kamera.
- Keine Schnittspaltkorrektur (Kerf-Offset) und keine Haltestege (Tabs).
- SVG-Texte müssen vorher in Pfade umgewandelt werden (Inkscape: *Objekt in Pfad*). Alternativ den Text in TiLaser einfügen.
- DXF: TEXT, MTEXT, HATCH und Bemaßungen werden ignoriert. Binär-DXF wird nicht unterstützt.
- Keine Rückgängig-Funktion. Gruppieren lässt sich mit „Gruppierung aufheben“ umkehren, Löschen nicht.

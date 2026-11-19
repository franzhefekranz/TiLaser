# Änderungen / Changelog

Jede Version hat einen Abschnitt `## <Version>`; der Release-Workflow übernimmt ihn als Beschreibung des
GitHub-Releases. / Each version has a `## <version>` section; the release workflow uses it as the description of
the GitHub release.

## 0.9.0

**Erste öffentliche Version (Beta).** TiLaser ist ein freies Programm (GPL-3.0) zum Laserschneiden und
-gravieren mit G-Code-Lasern (GRBL/FluidNC, grblHAL, Marlin, Smoothieware) für Windows und Linux, auch auf dem
Raspberry Pi. Rückmeldungen und Fehlerberichte bitte als [Issue](https://github.com/franzhefekranz/TiLaser/issues).

### Funktionen

- **Dateien:** Import von SVG, DXF, PDF/AI, EPS, Bildern und LightBurn-Projekten (`.lbrn2`/`.lbrn`); Export als
  G-Code, SVG, DXF und LightBurn-Datei; eigenes Projektformat `.tlp`
- **Zeichnen und Bearbeiten:** Rechteck, Ellipse, Polygon/Stern, Polylinie, Knotenbearbeitung mit Bézierkurven,
  Text aus allen Schriften, Text auf Pfad, boolesche Operationen, Offset, Pfade verbinden, Ausrichten, Verteilen,
  **Kanten aneinanderlegen** (Alt+K), Array, Gruppieren, Undo/Redo; Polylinie mit Shift waagrecht/senkrecht;
  Auswahlrahmen nach rechts = ganz umschlossene, nach links = berührte Objekte
- **Ebenen:** Schneiden, Füllen, Offset-Füllung, Rasterbild mit Dithering; Kerf-Ausgleich, Haltestege,
  Unterebenen, Overscan, Air Assist, Z-Versatz; 32 Ebenenfarben; Material-Presets (u. a. AlgoLaser DIY KIT MK2 20 W)
- **Nesting** mit Löchern, bündigem Anlegen und Speichern Platte für Platte
- **Generatoren:** Box mit Fingerzinken, Fingerzinken an beliebigen Kanten (ineinandergreifend oder als
  Eckverbindung) mit Dogbones, symmetrisches Biegescharnier (normal, dreifach versetzt, Wellenschlitze),
  Lochmuster/Lüftungsgitter, Gravurmuster (Hahnentritt, Fischgrät, Waben …), Zahnräder, Puzzle,
  Schablonen-Stege, QR-Code und Code 128, variable Texte mit CSV
- **Tests:** Materialtest, Fokustest (auch ohne Z-Achse), Zeilenabstandstest
- **Maschine:** USB und Netzwerk, automatische Firmware-Erkennung und Wiederverbindung, Rahmen und
  Gummiband-Rahmen, Pause/Fortsetzen ab Zeile, Restzeit, Makros, GRBL-Einstellungen, Rotationsachse,
  Startpunkt und Schnittreihenfolge
- **Kamera:** Ausrichten mit Linsenkorrektur, Live-Bild mit Videoaufnahme, **Flammenwächter**
- **Weboberfläche** zum Beobachten und Bedienen vom Handy oder PC, **Auftragsprotokoll** mit Kostenrechnung
- **Deutsch und Englisch**, weitere Sprachen über eine Textdatei
- **Bedienungsanleitung** im Programm mit kontextsensitiver Hilfe (F1), als PDF und online

### Downloads

| Datei | System |
|---|---|
| `TiLaser-0.9.0-Setup.exe` | Windows-Installer |
| `TiLaser-0.9.0-portable-win64.zip` | Windows portabel (entpacken, `TiLaser.exe` starten) |
| `TiLaser-0.9.0-x86_64.AppImage`, `tilaser_0.9.0_amd64.deb` | Linux PC (Ubuntu 22.04+, Debian 12+) |
| `TiLaser-0.9.0-aarch64.AppImage`, `tilaser_0.9.0_arm64.deb` | Linux ARM64, z. B. Raspberry Pi OS Trixie |
| `TiLaser-Bedienungsanleitung.pdf`, `TiLaser-Manual.pdf` | Anleitung Deutsch / English |

Windows meldet beim ersten Start eventuell „Unbekannter Herausgeber“ (*Weitere Informationen → Trotzdem
ausführen*), weil das Programm nicht kostenpflichtig signiert ist.

### Hinweis

Beta-Version: Bitte jedes neue Material zuerst mit dem Materialtest prüfen und den Laser **nie unbeaufsichtigt**
laufen lassen.

---

**English:** First public release (beta) of TiLaser, a free (GPL-3.0) program for laser cutting and engraving with
G-code lasers on Windows and Linux (including Raspberry Pi). It reads and writes LightBurn files, nests parts,
generates boxes, finger joints, living hinges, patterns, gears and puzzles, controls the laser via USB or network,
watches the job with a webcam and flame guard, offers a web interface and a job log, and comes with a full manual
in German and English, built into the program with context help (F1). The user interface can be switched to
English under *View → Sprache / Language*.

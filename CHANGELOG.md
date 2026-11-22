# Änderungen / Changelog

Jede Version hat einen Abschnitt `## <Version>`; der Release-Workflow übernimmt ihn als Beschreibung des
GitHub-Releases. / Each version has a `## <version>` section; the release workflow uses it as the description of
the GitHub release.

## Unveröffentlicht

- Biegescharnier (in einer Fläche und zwischen zwei Kanten): optional *Stress Relief* – kleine Löcher an den
  Schlitzenden im Material, wie Dogbones, damit das Holz beim Biegen nicht einreißt.
- Biegescharnier zwischen zwei Kanten: Die Kontur des verbundenen Teils ist jetzt exakt – an den Enden der
  Biegezone blieb sonst eine minimal schräge statt einer geraden Kante; bei gleich breiten Teilen entsteht ein
  glattes Rechteck ohne Zwischenpunkte.
- Fingerzinken an Kante: neue Art *Kante an Fläche* (T-Verbindung) – Zinken am einen Teil, passende Löcher in der
  Fläche des anderen im einstellbaren Abstand von dessen Kante; Zinkenlänge und Spiel einstellbar.
- Neu: *Ansicht → An Knoten fangen* – ein Teil an einem Knoten greifen und ziehen; der Knoten rastet an Knoten
  anderer Teile ein (orange Markierung, Alt = ohne Fangen).
- Feineres Verschieben mit den Pfeiltasten: Strg = 0,1 mm (weiterhin 1 mm, Shift = 10 mm); alle drei Schrittweiten
  einstellbar unter *Ansicht → Schrittweiten der Pfeiltasten…* (werden gespeichert).
- Neu: *Werkzeuge → Federriegel mit Haken…* – Riegel hinter der Vorderwand, dessen Haken durch Schlitze im Deckel
  greifen; T-Halter in Öse (am Ende einer langen Mäanderfeder) und Langloch, Öffnen durch Schieben der Hakenköpfe. Hakennase mit
  steiler, einstellbarer Schräge (Standard 60°) und verrundeter Spitze. Optional
  mit innerer Lage (zweiter Boden), zwischen der und der Wand der Riegel liegt; dort Schlitze für die Halterköpfe.
- Neu: *Werkzeuge → Federverschluss an Kante…* – Schnappverschluss für Deckel: Zunge mit Haken an einer
  Mäanderfeder (S-Form, runde Wendungen) im einen Teil, Aussparung mit Schulter im anderen; Öffnen per Drucktaste.
- Gruppieren und Gruppierung aufheben sind bei vielen Teilen deutlich schneller (Zuordnung der Löcher zu ihren
  Außenkonturen vorgefiltert, Auswahl-Aktualisierung nur noch einmal am Ende statt für jedes Teil).
- Knöchelscharnier: Beim mitgelaserten Vierkantstift haben der erste und der letzte Knöchel des ersten
  Teils ein Vierkantloch (bei ungerader Anzahl die beiden äußeren) – der Stift sitzt dort fest und dreht sich nur in den übrigen Knöcheln; die Knöchel werden je Teil getrennt abgelegt.
- Materialtest, Fokustest und Zeilenabstandstest stehen gemeinsam unter *Werkzeuge → Lasertests*.
- **Teile nummerieren** (*Werkzeuge*): Teile in Lesereihenfolge durchnummerieren und die Nummer an der Stelle
  mit dem meisten Platz eingravieren; gleiche Teile (auch gedreht, wahlweise gespiegelt) bekommen dieselbe
  Nummer.
- **Biegescharnier zwischen zwei Kanten** (*Werkzeuge*): zwei Kanten anklicken, Radius und Winkel eingeben –
  TiLaser legt die Teile im Abstand der Biegelänge aneinander, verbindet sie zu einem Teil und versieht die
  Biegezone mit dem Biegescharnier-Muster.
- **Knöchelscharnier:** beliebig viele Scharniere entlang der Kante (für breite Teile).
- **Behoben:** *Rückgängig* nach Werkzeugen, die Objekte ersetzen (Fingerzinken, Puzzle-, Snap-Lock-,
  Hammerverbindung, Knöchelscharnier u. a.), meldete einen Fehler bzw. beendete das Programm. Ursache: Beim
  Neuaufbau der Zeichenfläche wurde die Auswahl mit Objekten gemeldet, die es im wiederhergestellten Projekt
  nicht mehr gab.
- **Puzzle-Verbindung an Kante** (*Werkzeuge*): verbindet zwei flach Kante an Kante liegende Teile mit
  runden Puzzle-Nasen (schmaler Hals, formschlüssig); Nasengröße, Anzahl, Spiel und Nasen abwechselnd an
  beiden Teilen einstellbar.
- **Snap-Lock an Kante** (*Werkzeuge*): federnde Rastlaschen mit Haken an einem Teil und passende Schlitze im
  anderen – Steckverbindung im rechten Winkel ohne Leim.
- **Hammerverbindung an Kante** (*Werkzeuge*, nach der Hammer-Joint-Zeichnung von Lasersheets): Zapfen mit
  mitgelasertem Keil an zwei Haltestegen und passende Zapfenlöcher – der eingeschlagene Keil spreizt den Zapfen
  im Loch (ohne Schrauben und Leim).
- **Knöchelscharnier an Kante** (*Werkzeuge*): Scharnier nur aus Laserteilen zwischen zwei Boxteilen (z. B.
  Rückwand von Box und Deckel) – abgeschrägte Knöchel mit je zwei Fingerzinken in Schlitzen der Wände und ein
  mitgelaserter Vierkantstift (oder Rundstab).

## 0.9.1

**Sicherheitsupdate (Beta).** Behebt Fehler aus einem Code-Review, die den Laser oder das Programm betreffen.
Allen Nutzern der 0.9.0 wird das Update empfohlen.

### Laser und Steuerung

- **Marlin/Smoothieware:** *Pause* sendet sofort `M5`, damit der Laser nach den gepufferten Bewegungen nicht im
  Stillstand weiterbrennt; *Fortsetzen* schaltet ihn wieder ein. *Stopp* sendet `M410`, `M5` und *Luft aus*
  sofort statt hinter dem laufenden Befehl. Der Flammenwächter stoppt bei diesen Steuerungen statt zu pausieren.
- **GRBL:** Vor jeder Leerfahrt wird die Leistung ausdrücklich auf `S0` gesetzt. Meldet GRBL `$32=0`
  (Lasermodus aus), startet TiLaser keinen Auftrag, der den Laser einschaltet.
- Meldet die Steuerung während eines Auftrags `error:…`, wird der Auftrag angehalten statt fortgesetzt.
- Ungültige Koordinaten (NaN/unendlich) gelangen nicht mehr in den G-Code.
- *Fertig* wird erst gemeldet, wenn die Maschine wirklich steht (GRBL: Status *Idle*, Marlin/Smoothie:
  angehängtes `M400`) – vorher konnte schon der nächste Auftrag gestartet werden, während der Laser noch lief.

### Import, Kerf und Weboberfläche

- DXF-Dateien mit extremen Winkelwerten oder verschachtelten Block-Arrays und SVG-Dateien mit sich
  vervielfachenden `<use>`-Verweisen hängen das Programm nicht mehr auf, sondern werden mit Meldung abgelehnt.
- Kerf-Ausgleich: Löcher, die kleiner als die Schnittbreite sind, erzeugen ohne pyclipper keine falsche
  Kontur mehr; entfallene Konturen werden beim Start gemeldet.
- Weboberfläche: ungültige Anfragen (z. B. negative `Content-Length`) können den Server nicht mehr blockieren;
  Verbindungen haben ein Zeitlimit.
- Weboberfläche: PIN ist Pflicht (mindestens 4 Zeichen), Anfragen über fremde Rechnernamen werden abgewiesen
  (Schutz vor DNS-Rebinding), Sitzungen laufen nach 24 Stunden auch im Server ab.
- Unerwartete Fehler werden als Meldung angezeigt, statt das Programm zu beenden.

### Geschwindigkeit

- G-Code-Erzeugung und Import laufen im Hintergrund; die Oberfläche, der Laser-Status, die Kamera und der
  Flammenwächter bleiben dabei bedienbar bzw. aktiv.
- Pfadoptimierung bei vielen Konturen deutlich schneller (3000 Konturen: 17 s → 0,5 s), mit identischem Ergebnis.
- Große Rasterbilder brauchen bei der G-Code-Erzeugung etwa ein Viertel des Speichers (Vorschau zeilenweise).

### Neu und verbessert

- Füllen: Option *Überlappende Objekte vereinigen* – überlappende Objekte werden als eine Fläche graviert
  statt die Überlappung auszusparen (Löcher in einem Objekt bleiben Löcher).
- *Fortsetzen* stellt auch den Fokus-Versatz der Z-Achse wieder her und fragt, auf welcher Höhe der Kopf steht.
- Automatische Prüfung bei jedem Push: Tests, Lint und Typprüfung des Kerns (mypy) mit Python 3.10 und 3.12.

### Downloads

Wie bei 0.9.0, mit Versionsnummer 0.9.1.

---

**English:** Safety update. Marlin/Smoothieware: *Pause* sends `M5` immediately and *Stop* sends `M410`/`M5`/air
off without waiting; the flame guard stops instead of pausing. GRBL: explicit `S0` before travel moves, no job
start with `$32=0`. Controller errors pause the job. Fixed hangs on malicious DXF/SVG files and in the web
server, wrong contours from kerf compensation without pyclipper, and crashes on unexpected errors. *Done* is
reported only when the machine has stopped. Web interface: mandatory PIN, host check against DNS rebinding.
G-code generation and import run in the background; path optimisation is up to 30× faster. New fill option
*Merge overlapping objects*; *Resume* restores the Z focus offset. CI with tests, lint and mypy.

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

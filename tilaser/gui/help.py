"""Bedienungsanleitung im Programm und kontextsensitive Hilfe (F1).

Die Anleitung (docs/Bedienungsanleitung.md bzw. docs/Manual_en.md) wird direkt als Markdown angezeigt.
Überschriften, vor denen ``<!-- hilfe: kennung -->`` steht, bekommen den Anker ``hilfe-kennung``; über
diese Kennungen springt die Kontexthilfe in beiden Sprachen an dieselbe Stelle.

F1 ermittelt, was gerade bedient wird: Menüpunkt im offenen Menü, Element unter der Maus bzw. mit dem
Fokus, das aktive Fenster. Jedes Fenster mit Knopfleiste bekommt außerdem einen Knopf „Hilfe“.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QEvent, QObject, Qt, QUrl
from PyQt6.QtGui import QAction, QCursor, QDesktopServices, QImage, QKeySequence, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLineEdit, QMenu, QMessageBox,
                             QSplitter, QTextBrowser, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from ..core import i18n


# Fenster (Klassenname) -> Abschnitt
DIALOGS = {
    "MachineDialog": "dlg-maschine", "TextDialog": "dlg-text", "GcodeDialog": "dlg-gcode",
    "ArrayDialog": "dlg-array", "TraceDialog": "dlg-trace", "MaterialTestDialog": "dlg-materialtest",
    "OffsetDialog": "dlg-offset", "PointPickDialog": "dlg-passmarken", "TextPathDialog": "dlg-textpfad",
    "NestDialog": "dlg-nesting", "FocusTestDialog": "dlg-fokustest", "BoxDialog": "dlg-box",
    "IntervalTestDialog": "dlg-zeilenabstand", "BarcodeDialog": "dlg-barcode", "VarDialog": "dlg-variablen",
    "GrblSettingsDialog": "dlg-grbl", "MacroDialog": "dlg-makros", "WebDialog": "dlg-web",
    "JobLogDialog": "dlg-protokoll", "PaletteDialog": "dlg-farbe", "CameraWindow": "fenster-kamera",
    "HelpWindow": "hilfe",
}
# ParamDialog nach (deutschem) Titel
PARAM_DIALOGS = {"Biegescharnier": "muster", "Lochmuster": "muster", "Gravurmuster": "muster",
                 "Fingerzinken an Kante": "fingerzinken", "Puzzle-Verbindung an Kante": "puzzle-verbindung",
                 "Snap-Lock an Kante": "snap-lock",
                 "Schablonen-Stege": "zahnrad-puzzle",
                 "Zahnrad": "zahnrad-puzzle", "Puzzle": "zahnrad-puzzle"}
# Hauptmenüs (deutscher Titel) -> Abschnitt der Referenz
MENUS = {"&Datei": "menu-datei", "&Bearbeiten": "menu-bearbeiten", "&Anordnen": "menu-anordnen",
         "&Formen": "menu-formen", "&Werkzeuge": "menu-werkzeuge", "&Ansicht": "menu-ansicht",
         "&Maschine": "menu-maschine", "&Hilfe": "menu-hilfe"}
# Menüpunkte (deutscher Text), die einen ausführlicheren Abschnitt haben
ACTIONS = {
    "Importieren…": "import", "G-Code exportieren…": "export", "Als SVG exportieren…": "export",
    "Als DXF exportieren…": "export", "Als LightBurn-Datei speichern…": "lightburn", "Projekt öffnen…": "projekte",
    "Text einfügen…": "text", "Gruppieren": "gruppieren", "Gruppierung aufheben": "gruppieren",
    "Array erstellen…": "array", "Nesting (Teile anordnen)…": "nesting", "Offset…": "dlg-offset",
    "Bild nachzeichnen…": "nachzeichnen", "Materialtest…": "materialtest", "Text auf Pfad…": "text-auf-pfad",
    "QR-Code / Barcode einfügen…": "variabler-text", "Variable Texte…": "variabler-text",
    "Box-Generator…": "box", "Zahnrad…": "zahnrad-puzzle", "Puzzle…": "zahnrad-puzzle",
    "Biegescharnier…": "muster", "Lochmuster / Lüftungsgitter…": "muster", "Gravurmuster…": "muster",
    "Fingerzinken an Kante…": "fingerzinken", "Puzzle-Verbindung an Kante…": "puzzle-verbindung",
    "Snap-Lock an Kante…": "snap-lock",
    "Schablonen-Stege…": "zahnrad-puzzle",
    "Fokustest…": "fokustest", "Zeilenabstandstest…": "zeilenabstandstest",
    "Werkzeugweg-Vorschau": "vorschau", "Am Raster fangen": "anordnen",
    "Maschineneinstellungen…": "maschineneinstellungen", "GRBL-Einstellungen ($$)…": "grbl",
    "Makros bearbeiten…": "dlg-makros", "Auftragsprotokoll…": "auftragsprotokoll",
    "Weboberfläche…": "weboberflaeche", "Rotationsachse aktiv": "rotation",
    "Startpunkt auf gewählten Knoten setzen": "knoten", "Live-Kamera": "live-kamera",
    "Großes Kamerafenster": "fenster-kamera", "Passmarken lasern": "kamera-ausrichten",
    "Kamera ausrichten…": "kamera-ausrichten", "Kamera wählen…": "kamera-ausrichten",
    "Kamerabild aktualisieren": "kamera-ausrichten", "Lizenzen…": "lizenz", "Über TiLaser": "lizenz",
    "Bedienungsanleitung": "hilfe", "Kanten aneinanderlegen": "kanten-anlegen", "Eigene Übersetzung anlegen / bearbeiten…": "sprache",
}


from ..core.manual import FILES, ONLINE, docs_dir, manual_files, prepare, slug   # noqa: F401


# --------------------------------------------------------------------------- Fenster
class HelpWindow(QWidget):
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = HelpWindow()
        return cls._instance

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("TiLaser – Hilfe")
        self.resize(1150, 820)
        self.lang = i18n.current().code
        self.base = docs_dir()
        self.ids: set[str] = set()
        self._hist, self._pos = [], -1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        tb = QToolBar()
        self.a_back = QAction("◀", self)
        self.a_back.setToolTip("Zurück")
        self.a_back.setShortcut(QKeySequence(QKeySequence.StandardKey.Back))
        self.a_back.triggered.connect(lambda: self._step(-1))
        self.a_fwd = QAction("▶", self)
        self.a_fwd.setToolTip("Vor")
        self.a_fwd.setShortcut(QKeySequence(QKeySequence.StandardKey.Forward))
        self.a_fwd.triggered.connect(lambda: self._step(1))
        a_home = QAction("Inhalt", self)
        a_home.setToolTip("Zum Anfang der Anleitung")
        a_home.triggered.connect(lambda: self.go(None))
        for a in (self.a_back, self.a_fwd, a_home):
            tb.addAction(a)
        tb.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen …")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        self.search.returnPressed.connect(lambda: self.find(
            backward=bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)))
        tb.addWidget(self.search)
        a_prev = QAction("↑", self)
        a_prev.setToolTip("Vorheriger Treffer (Umschalt+Enter)")
        a_prev.triggered.connect(lambda: self.find(backward=True))
        a_next = QAction("↓", self)
        a_next.setToolTip("Nächster Treffer (Enter)")
        a_next.triggered.connect(lambda: self.find())
        tb.addAction(a_prev)
        tb.addAction(a_next)
        tb.addSeparator()
        a_pdf = QAction("PDF", self)
        a_pdf.setToolTip("Die Anleitung als PDF öffnen")
        a_pdf.triggered.connect(self.open_pdf)
        a_web = QAction("Online", self)
        a_web.setToolTip("Die Anleitung auf der Projektseite im Browser öffnen")
        a_web.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(ONLINE.get(self.lang, ONLINE["en"]))))
        tb.addAction(a_pdf)
        tb.addAction(a_web)
        lay.addWidget(tb)
        split = QSplitter()
        self.toc = QTreeWidget()
        self.toc.setHeaderHidden(True)
        self.toc.itemClicked.connect(lambda it, _c: self.go_anchor(it.data(0, Qt.ItemDataRole.UserRole)))
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        split.addWidget(self.toc)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 850])
        lay.addWidget(split, 1)
        self.load()
        from .i18n_qt import translate_tree
        translate_tree(self)

    # -- Laden
    def load(self):
        md_name, _pdf = manual_files(self.lang)
        if not self.base or not os.path.exists(os.path.join(self.base, md_name)):
            self.view.setPlainText("Die Bedienungsanleitung wurde nicht gefunden. Sie steht auch online unter\n"
                                   + ONLINE.get(self.lang, ONLINE["en"]))
            return
        md = open(os.path.join(self.base, md_name), encoding="utf-8").read()
        text, heads = prepare(md, self.base)
        self.view.setSearchPaths([self.base])
        self.view.document().setMarkdown(text)
        self._anchors(heads)
        self._images()
        self._toc(heads)
        self.ids = {h[3] for h in heads if h[3]}

    def _anchors(self, heads):
        doc = self.view.document()
        queue = list(heads)
        block = doc.begin()
        while block.isValid() and queue:
            if block.blockFormat().headingLevel() > 0:
                text = block.text().strip()
                k = next((i for i, h in enumerate(queue[:4]) if h[1].strip() == text), 0)
                lvl, title, sl, hid = queue[k]
                del queue[:k + 1]
                cur = QTextCursor(block)
                cur.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                fmt = cur.charFormat()
                fmt.setAnchor(True)
                fmt.setAnchorNames([sl] + ([f"hilfe-{hid}"] if hid else []))
                cur.mergeCharFormat(fmt)
            block = block.next()

    def _images(self):
        doc = self.view.document()
        maxw = 820
        found = []
        text = doc.toPlainText()
        cur = QTextCursor(doc)
        pos = text.find("\ufffc")                      # Bilder stehen als Ersatzzeichen im Text
        while pos >= 0:
            cur.setPosition(pos)
            cur.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
            fmt = cur.charFormat()
            if fmt.isImageFormat():
                found.append((pos, 1, fmt.toImageFormat()))
            pos = text.find("\ufffc", pos + 1)
        for pos, length, imf in found:
            img = QImage(os.path.join(self.base, imf.name()))
            if img.isNull():
                continue
            if os.path.basename(imf.name()).startswith("icon_"):
                w = h = 22
            else:
                w = min(img.width(), maxw)
                h = img.height() * w / max(1, img.width())
            imf.setWidth(w)
            imf.setHeight(h)
            cur = QTextCursor(doc)
            cur.setPosition(pos)
            cur.setPosition(pos + length, QTextCursor.MoveMode.KeepAnchor)
            cur.setCharFormat(imf)

    def _toc(self, heads):
        self.toc.clear()
        parents = {}
        for lvl, title, sl, _hid in heads:
            if lvl < 2 or lvl > 3 or title in ("Inhalt", "Contents"):
                continue
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, sl)
            if lvl == 2:
                self.toc.addTopLevelItem(item)
                parents[2] = item
            elif 2 in parents:
                parents[2].addChild(item)

    # -- Navigation
    def go(self, hid: str | None):
        """Zur Hilfe-Kennung springen (None = Anfang)."""
        if hid and hid in self.ids:
            self.go_anchor(f"hilfe-{hid}")
        else:
            self.go_anchor("")

    def go_anchor(self, anchor: str, record: bool = True):
        if anchor:
            self.view.scrollToAnchor(anchor)
        else:
            self.view.moveCursor(QTextCursor.MoveOperation.Start)
            self.view.verticalScrollBar().setValue(0)
        if record:
            self._hist = self._hist[: self._pos + 1] + [anchor]
            self._pos = len(self._hist) - 1
        self.a_back.setEnabled(self._pos > 0)
        self.a_fwd.setEnabled(self._pos < len(self._hist) - 1)

    def _step(self, d):
        p = self._pos + d
        if 0 <= p < len(self._hist):
            self._pos = p
            self.go_anchor(self._hist[p], record=False)

    def _link(self, url: QUrl):
        s = url.toString()
        if s.startswith("#"):
            self.go_anchor(url.fragment())
        elif url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        elif self.base:
            target = os.path.join(self.base, url.path())
            if os.path.exists(target):
                QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def find(self, backward: bool = False):
        text = self.search.text().strip()
        if not text:
            return
        def search():
            return self.view.find(text, QTextDocument.FindFlag.FindBackward) if backward else self.view.find(text)
        if not search():
            self.view.moveCursor(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            if not search():
                self.search.setStyleSheet("background: #ffd6d6;")
                return
        self.search.setStyleSheet("")

    def open_pdf(self):
        open_pdf()


def open_pdf():
    lang = i18n.current().code
    _md, pdf = manual_files(lang)
    base = docs_dir()
    if base and os.path.exists(os.path.join(base, pdf)):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(base, pdf)))
    else:
        url = ONLINE.get(lang, ONLINE["en"]).rsplit("/", 1)[0] + "/" + pdf
        QDesktopServices.openUrl(QUrl(url))


def show_help(hid: str | None = None):
    w = HelpWindow.instance()
    w.show()
    w.raise_()
    w.activateWindow()
    w.go(hid)
    return w


# --------------------------------------------------------------------------- Kontext
def action_help(a) -> str | None:
    if a is None:
        return None
    v = a.property("help")
    return str(v) if v else None


def widget_help(w) -> str | None:
    """Hilfe-Kennung für ein Bedienelement (sucht nach oben bis zum Fenster)."""
    while w is not None:
        if isinstance(w, QToolButton) and w.defaultAction() is not None:
            h = action_help(w.defaultAction())
            if h:
                return h
        v = w.property("help")
        if v:
            return str(v)
        name = type(w).__name__
        if name == "ParamDialog":
            return PARAM_DIALOGS.get(getattr(w, "_title", ""), "dlg-generator")
        if name in DIALOGS:
            return DIALOGS[name]
        if isinstance(w, QDialog) and w.isWindow() and not isinstance(w, QMessageBox):
            return "fenster"
        w = w.parentWidget()
    return None


def context_help_id() -> str | None:
    app = QApplication.instance()
    popup = app.activePopupWidget()
    if isinstance(popup, QMenu):
        h = action_help(popup.activeAction())
        popup.close()
        return h or "referenz"
    win = app.activeWindow()
    under = QApplication.widgetAt(QCursor.pos())
    if under is not None and win is not None and under.window() is win:
        h = widget_help(under)
        if h:
            return h
    return widget_help(app.focusWidget()) or widget_help(win)


class _Filter(QObject):
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_F1 and not ev.modifiers():
            show_help(context_help_id())
            return True
        if t == QEvent.Type.Show and isinstance(obj, QDialog) and not isinstance(obj, QMessageBox) \
                and obj.property("_help_button") is None:
            obj.setProperty("_help_button", True)
            hid = widget_help(obj)
            if hid and hid != "fenster":
                for bb in obj.findChildren(QDialogButtonBox):
                    if bb.button(QDialogButtonBox.StandardButton.Help) is None:
                        b = bb.addButton(QDialogButtonBox.StandardButton.Help)
                        b.setText(i18n.tr("Hilfe"))
                        b.clicked.connect(lambda _c=False, h=hid: show_help(h))
                    break
        return False


_filter = None


def install(main_window):
    """F1-Hilfe für die ganze Anwendung einschalten und Kennungen an Menüs/Bedienelemente hängen."""
    global _filter
    if _filter is None:
        _filter = _Filter(QApplication.instance())
        QApplication.instance().installEventFilter(_filter)
    tag_main_window(main_window)


def tag_main_window(w):
    """Kennungen setzen – vor der Übersetzung aufrufen, solange Menütexte noch deutsch sind."""
    for top in w.menuBar().actions():
        menu = top.menu()
        hid = MENUS.get(top.text())
        if menu is not None:
            _tag_menu(menu, hid or "referenz")
    for a in (w.__dict__.get("tool_actions") or {}).values():
        a.setProperty("help", "zeichenwerkzeuge")
    for a in w.findChildren(QAction):
        if not a.property("help"):
            a.setProperty("help", ACTIONS.get(a.text(), "oberflaeche"))
    props = {"canvas": "arbeitsflaeche", "objects": "objektleiste", "camera_panel": "tab-kamera",
             "layers": "tab-ebenen"}
    for attr, hid in props.items():
        obj = w.__dict__.get(attr)
        if obj is not None:
            obj.setProperty("help", hid)
    m = w.__dict__.get("machine")
    if m is not None:
        for attr, hid in (("w_connection", "tab-verbindung"), ("w_jog", "tab-bewegen"),
                          ("w_console", "tab-konsole"), ("w_jobbar", "auftragsleiste")):
            obj = m.__dict__.get(attr)
            if obj is not None:
                obj.setProperty("help", hid)
    tabs = w.__dict__.get("tabs")
    if tabs is not None and tabs.count():
        tabs.widget(0).setProperty("help", "tab-ebenen")


def _tag_menu(menu, default):
    for a in menu.actions():
        if a.menu() is not None:
            _tag_menu(a.menu(), ACTIONS.get(a.text(), default))
        if not a.property("help") and not a.isSeparator():
            a.setProperty("help", ACTIONS.get(a.text(), default))

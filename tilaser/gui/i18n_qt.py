"""Übersetzung der Qt-Oberfläche ohne Änderungen an jedem einzelnen Text.

``install(code)`` (vor dem Hauptfenster aufrufen) ersetzt die Qt-Methoden, über die Texte gesetzt
werden (setText, setToolTip, addItem, QMessageBox.information …), durch Varianten, die den Text
vorher übersetzen. Texte, die schon im Konstruktor übergeben werden (``QLabel("…")``), übersetzt
``translate_tree`` beim Anzeigen eines Fensters bzw. Dialogs.
"""
from __future__ import annotations

from ..core import i18n

_installed = False


def _wrap(cls, name, positions=(0,), kw=()):
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_tl_wrapped", False):
        return

    def f(*args, **kwargs):
        args = list(args)
        for p in positions:
            if p < len(args) and isinstance(args[p], str):
                args[p] = i18n.tr(args[p])
        for k in kw:
            if isinstance(kwargs.get(k), str):
                kwargs[k] = i18n.tr(kwargs[k])
        return orig(*args, **kwargs)
    f._tl_wrapped = True
    try:
        setattr(cls, name, f)
    except (AttributeError, TypeError):
        pass


def _wrap_static(cls, name, positions):
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_tl_wrapped", False):
        return

    def f(*args, **kwargs):
        args = list(args)
        for p in positions:
            if p < len(args) and isinstance(args[p], str):
                args[p] = i18n.tr(args[p])
        return orig(*args, **kwargs)
    f._tl_wrapped = True
    setattr(cls, name, staticmethod(f))


def _wrap_strs(cls, name):
    """Alle Zeichenketten-Argumente übersetzen (z. B. QComboBox.addItem mit/ohne Symbol)."""
    orig = getattr(cls, name, None)
    if orig is None or getattr(orig, "_tl_wrapped", False):
        return

    def f(self, *args, **kwargs):
        args = [i18n.tr(a) if isinstance(a, str) else a for a in args]
        return orig(self, *args, **kwargs)
    f._tl_wrapped = True
    setattr(cls, name, f)


def install(code: str):
    """Sprache setzen und Qt-Methoden einhängen."""
    global _installed
    tl = i18n.set_language(code)
    if not tl.active:
        _load_qt_translations(code)          # auch auf Deutsch: Qt-Knöpfe wie „Hilfe“, „Abbrechen“
        return tl
    if _installed:
        return tl
    _installed = True
    from PyQt6 import QtGui, QtWidgets as W
    # Texte (Instanzmethoden: Position 1 = erstes Argument nach self)
    for cls, names in ((W.QLabel, ("setText",)), (W.QAbstractButton, ("setText",)),
                       (W.QWidget, ("setToolTip", "setWindowTitle", "setStatusTip", "setWhatsThis")),
                       (QtGui.QAction, ("setText", "setToolTip", "setStatusTip", "setIconText")),
                       (W.QGroupBox, ("setTitle",)), (W.QMenu, ("setTitle",)),
                       (W.QLineEdit, ("setPlaceholderText",)), (W.QPlainTextEdit, ("setPlaceholderText",)),
                       (W.QTextEdit, ("setPlaceholderText",)), (W.QProgressBar, ("setFormat",)),
                       (W.QDockWidget, ("setWindowTitle",)), (W.QMessageBox, ("setText", "setInformativeText", "setWindowTitle",
                                                                              "setDetailedText"))):
        for n in names:
            _wrap(cls, n, (1,))
    _wrap(W.QStatusBar, "showMessage", (1,))
    _wrap(W.QPlainTextEdit, "appendPlainText", (1,))
    _wrap(W.QTabWidget, "setTabText", (2,))
    _wrap(W.QTabWidget, "setTabToolTip", (2,))
    _wrap(W.QComboBox, "setItemText", (2,))
    _wrap(W.QToolBox, "setItemText", (2,))
    for cls, name in ((W.QComboBox, "addItem"), (W.QComboBox, "insertItem"), (W.QTabWidget, "addTab"),
                      (W.QTabWidget, "insertTab"), (W.QMenu, "addMenu"), (W.QMenuBar, "addMenu"),
                      (W.QMenu, "addAction"), (W.QMenu, "addSection"), (W.QToolBar, "addAction"),
                      (W.QDialogButtonBox, "addButton"), (W.QMessageBox, "addButton"),
                      (W.QListWidget, "addItem"), (W.QFormLayout, "addRow"), (W.QFormLayout, "insertRow"),
                      (W.QToolBox, "addItem"), (W.QStackedWidget, "addWidget")):
        _wrap_strs(cls, name)
    orig_items = W.QComboBox.addItems
    W.QComboBox.addItems = lambda self, items: orig_items(self, [i18n.tr(x) for x in items])
    for cls, name in ((W.QTableWidget, "setHorizontalHeaderLabels"), (W.QTableWidget, "setVerticalHeaderLabels"),
                      (W.QTreeWidget, "setHeaderLabels")):
        orig = getattr(cls, name)
        setattr(cls, name, (lambda o: lambda self, labels: o(self, [i18n.tr(x) for x in labels]))(orig))
    # Statische Dialoge: (Elternfenster, Titel, Text, …)
    for name in ("information", "warning", "critical", "question", "about"):
        _wrap_static(W.QMessageBox, name, (1, 2))
    for name in ("getInt", "getDouble", "getText", "getItem", "getMultiLineText"):
        _wrap_static(W.QInputDialog, name, (1, 2))
    for name in ("getSaveFileName", "getOpenFileName", "getOpenFileNames"):
        _wrap_static(W.QFileDialog, name, (1, 3))
    _wrap_static(W.QFileDialog, "getExistingDirectory", (1,))
    # Text, der direkt gezeichnet wird (Platzhalter im Kamerabild, Beschriftungen)
    orig_draw = QtGui.QPainter.drawText

    def draw(self, *args):
        return orig_draw(self, *[i18n.tr(a) if isinstance(a, str) else a for a in args])
    QtGui.QPainter.drawText = draw
    # Fenster/Dialoge beim Anzeigen durchgehen
    for cls, name in ((W.QDialog, "exec"), (W.QDialog, "show"), (W.QDialog, "open"), (W.QMenu, "exec"),
                      (W.QMessageBox, "exec")):
        orig = getattr(cls, name)

        def make(o):
            def f(self, *a, **k):
                translate_tree(self)
                return o(self, *a, **k)
            return f
        setattr(cls, name, make(orig))
    _load_qt_translations(code)
    return tl


def _load_qt_translations(code: str):
    """Qt-eigene Texte (OK, Abbrechen, Dateidialog …) in der gewählten Sprache."""
    try:
        from PyQt6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator
        app = QCoreApplication.instance()
        if app is None:
            return
        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        for base in ("qtbase", "qt"):
            t = QTranslator(app)
            if t.load(QLocale(code), base, "_", path):
                app.installTranslator(t)
                break
        QLocale.setDefault(QLocale(code))
    except Exception:
        pass


def translate_tree(root):
    """Alle Texte eines Fensters samt Unterelementen übersetzen (nur bei aktiver Übersetzung)."""
    if not i18n.current().active or root is None:
        return
    from PyQt6 import QtGui, QtWidgets as W
    tr = i18n.tr
    try:
        widgets = [root] + root.findChildren(W.QWidget)
    except Exception:
        return
    for w in widgets:
        try:
            if w.isWindow() and w.windowTitle():
                w.setWindowTitle(w.windowTitle())
            if w.toolTip():
                w.setToolTip(w.toolTip())
            if isinstance(w, (W.QLabel, W.QAbstractButton)) and w.text():
                w.setText(w.text())
            if isinstance(w, W.QGroupBox) and w.title():
                w.setTitle(w.title())
            if isinstance(w, W.QMenu) and w.title():
                w.setTitle(w.title())
            if isinstance(w, W.QComboBox):
                for i in range(w.count()):
                    t = w.itemText(i)
                    if t:
                        w.setItemText(i, t)
            if isinstance(w, W.QTabWidget):
                for i in range(w.count()):
                    w.setTabText(i, w.tabText(i))
            if isinstance(w, W.QToolBox):
                for i in range(w.count()):
                    w.setItemText(i, w.itemText(i))
            if isinstance(w, (W.QLineEdit, W.QPlainTextEdit)) and w.placeholderText():
                w.setPlaceholderText(w.placeholderText())
            if isinstance(w, W.QAbstractSpinBox):
                for get, set_ in (("suffix", "setSuffix"), ("prefix", "setPrefix"),
                                  ("specialValueText", "setSpecialValueText")):
                    if hasattr(w, get) and getattr(w, get)():
                        getattr(w, set_)(tr(getattr(w, get)()))
            if isinstance(w, W.QTableWidget):
                for i in range(w.columnCount()):
                    it = w.horizontalHeaderItem(i)
                    if it is not None and it.text():
                        it.setText(tr(it.text()))
            if isinstance(w, W.QListWidget):
                for i in range(w.count()):
                    it = w.item(i)
                    it.setText(tr(it.text()))
        except RuntimeError:          # Objekt inzwischen gelöscht
            continue
    try:
        for a in root.findChildren(QtGui.QAction):
            if a.text():
                a.setText(a.text())
            if a.toolTip():
                a.setToolTip(a.toolTip())
            if a.statusTip():
                a.setStatusTip(a.statusTip())
            m = a.menu()
            if m is not None and m.title():
                m.setTitle(m.title())
    except RuntimeError:
        pass

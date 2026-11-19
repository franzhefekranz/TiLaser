"""Start: python -m tilaser"""
import sys


def _install_excepthook():
    """Unbehandelte Ausnahmen in Qt-Slots anzeigen statt das Programm zu beenden (PyQt6 bricht sonst ab)."""
    import traceback

    def hook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        sys.stderr.write(text)
        try:
            from PyQt6.QtWidgets import QMessageBox
            box = QMessageBox(QMessageBox.Icon.Critical, "TiLaser", str(value) or etype.__name__)
            box.setDetailedText(text)
            box.exec()
        except Exception:
            pass
    sys.excepthook = hook


def main():
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("PyQt6 fehlt. Installation:  pip install -r requirements.txt")
        sys.exit(1)
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    _install_excepthook()
    app.setApplicationName("TiLaser")
    app.setStyle("Fusion")
    import os
    from PyQt6.QtGui import QIcon
    icon = os.path.join(os.path.dirname(__file__), "data", "tilaser.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    app.setDesktopFileName("tilaser")
    from .core import i18n, storage
    from .gui import i18n_qt
    lang = os.environ.get("TILASER_LANG") or getattr(storage.load_machine(), "language", "") or i18n.system_language()
    i18n_qt.install(lang)
    w = MainWindow()
    i18n_qt.translate_tree(w)
    w.show()
    files = [a for a in sys.argv[1:] if not a.startswith("-")]
    if files:
        w.import_files(files)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

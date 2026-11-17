"""Start: python -m tilaser"""
import sys


def main():
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("PyQt6 fehlt. Installation:  pip install -r requirements.txt")
        sys.exit(1)
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("TiLaser")
    app.setStyle("Fusion")
    import os
    from PyQt6.QtGui import QIcon
    icon = os.path.join(os.path.dirname(__file__), "data", "tilaser.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    app.setDesktopFileName("tilaser")
    w = MainWindow()
    w.show()
    files = [a for a in sys.argv[1:] if not a.startswith("-")]
    if files:
        w.import_files(files)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

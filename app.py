import sys
import threading
from PySide6.QtWidgets import QApplication
from gui import MainWindow
from api import run_flask

if __name__ == "__main__":
    # Run Flask in separate thread.
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Run Qt in main thread.
    qt_app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(qt_app.exec())

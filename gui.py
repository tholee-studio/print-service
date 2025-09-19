from bridge import bridge
from printer import printer

from PySide6.QtWidgets import (
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QLabel,
    QTextEdit,
)

from PySide6.QtPrintSupport import QPrintDialog
from PySide6.QtCore import QDateTime


CONFIG_PRINT_FILE = "config.json"

DEFAULT_BLE_SERVICE_UUID = ""
DEFAULT_BLE_CHAR_UUID = ""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Print Service - Tholee Studio")

        # Main layout.
        layout = QVBoxLayout()

        # Regular printer config (Qt).
        config_group = QWidget()
        config_layout = QVBoxLayout()

        self.printer_info = QLabel(
            "Printer has not been configured yet. Will be using default setting."
        )
        self.warning = QLabel(
            "<b>WARNING!</b> Currently this app only saves: printer name, paper size, and orientation. "
            "<br>If you want other settings, change them in OS Printer Preferences."
        )

        config_layout.addWidget(self.printer_info)
        config_layout.addWidget(self.warning)

        self.settings_btn = QPushButton("Configure Regular Printer")
        self.settings_btn.clicked.connect(self.open_printer_dialog)
        config_layout.addWidget(self.settings_btn)

        # Attach config group.
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # Log widget.
        self.log_label = QLabel("Activity Log:")
        layout.addWidget(self.log_label)
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        layout.addWidget(self.log_widget)

        # Set main widget.
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        # Load printer config.
        self.printer_config = printer.load_printer_config()
        if self.printer_config != None:
            self.update_printer_info()

        bridge.add_log.connect(self.log_message)
        bridge.add_log.emit("Application started")

    # ------------------- Qt Printer Config -------------------
    def open_printer_dialog(self):
        local_printer = printer.load_printer_with_config()
        print_dialog = QPrintDialog(local_printer, self)
        if print_dialog.exec() == QPrintDialog.Accepted:
            self.printer_config = printer.save_printer_config(local_printer)
            self.update_printer_info()

    def update_printer_info(self):
        paper_name = self.printer_config.get("paper_size", {}).get("name", "-")
        orientation = self.printer_config.get("orientation", "-")
        self.printer_info.setText(
            f"PRINTER: <b>{self.printer_config.get('printer_name','-')}</b> <br> PAPER: <b>{paper_name}</b> <br> ORIENTATION: <b>{orientation}</b>"
        )

    def log_message(self, message):
        timestamp = QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")
        log_entry = f"[{timestamp}] {message}"
        scroll_bar = self.log_widget.verticalScrollBar()
        at_bottom = scroll_bar.value() == scroll_bar.maximum()
        self.log_widget.append(log_entry)
        if at_bottom:
            cursor = self.log_widget.textCursor()
            cursor.movePosition(cursor.MoveOperation.EndOfLine)
            self.log_widget.setTextCursor(cursor)
            self.log_widget.ensureCursorVisible()

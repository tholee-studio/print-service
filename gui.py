from bridge import bridge
from printer import printer
from thermal import thermal

from PySide6.QtWidgets import (
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QLabel,
    QTextEdit,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QFormLayout,
)

from PySide6.QtPrintSupport import QPrintDialog
from PySide6.QtCore import QDateTime

from threading import Thread


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

        # ------------------- Thermal mode selector -------------------
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Thermal Mode:"))
        self.thermal_mode_combo = QComboBox()
        self.thermal_mode_combo.addItems(["BLE", "USB"])
        # self.thermal_mode_combo.currentTextChanged.connect(self.on_mode_changed)
        mode_row.addWidget(self.thermal_mode_combo)
        mode_row.addStretch()
        config_layout.addLayout(mode_row)

        # ------------------- BLE thermal section -------------------
        ble_group = QWidget()
        ble_layout = QVBoxLayout()

        self.thermal_ble_refresh_btn = QPushButton("Scan Printers")
        self.thermal_ble_refresh_btn.clicked.connect(self.scan_ble_thermal)
        ble_layout.addWidget(self.thermal_ble_refresh_btn)
        self.thermal_ble_dropdown = QComboBox()
        self.thermal_ble_dropdown.currentIndexChanged.connect(self.on_ble_selected)
        ble_layout.addWidget(self.thermal_ble_dropdown)

        form = QFormLayout()
        self.thermal_service_uuid_edit = QLineEdit(DEFAULT_BLE_SERVICE_UUID)
        self.thermal_char_uuid_edit = QLineEdit(DEFAULT_BLE_CHAR_UUID)
        form.addRow("Service UUID", self.thermal_service_uuid_edit)
        form.addRow("Char UUID", self.thermal_char_uuid_edit)
        ble_layout.addLayout(form)

        save_ble_row = QHBoxLayout()
        self.thermal_ble_save_btn = QPushButton("Save BLE Settings")
        # self.thermal_ble_save_btn.clicked.connect(self.save_thermal_printer_config)
        save_ble_row.addWidget(self.thermal_ble_save_btn)
        save_ble_row.addStretch()
        ble_layout.addLayout(save_ble_row)

        self.ble_info = QLabel("No BLE printer selected")
        ble_layout.addWidget(self.ble_info)

        ble_group.setLayout(ble_layout)
        config_layout.addWidget(ble_group)

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

        # self.print_lock = Lock()

        # Load printer config.
        self.printer_config = printer.load_printer_config()
        if self.printer_config != None:
            self.update_printer_info()

        bridge.add_log.connect(self.log_message)
        bridge.update_thermal_info.connect(self.update_thermal_info)
        bridge.update_thermal_edit.connect(self.update_thermal_edit)
        bridge.add_log.emit("Application started")
        # self.apply_mode_visibility()

    # ------------------- Qt Printer -------------------
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

    # ------------------- Thermal Printer -------------------
    def scan_ble_thermal(self):
        def worker():
            self.thermal_ble_dropdown.clear()
            self.thermal_ble_refresh_btn.setDisabled(True)
            self.thermal_ble_save_btn.setDisabled(True)
            self.thermal_mode_combo.setDisabled(True)
            self.thermal_ble_dropdown.setDisabled(True)

            thermal.scan_ble_thermal()

            if thermal.ble_devices:
                for d in thermal.ble_devices:
                    self.thermal_ble_dropdown.addItem(f"{d['name']} — {d['address']}")

            self.thermal_ble_refresh_btn.setDisabled(False)
            self.thermal_ble_save_btn.setDisabled(False)
            self.thermal_mode_combo.setDisabled(False)
            self.thermal_ble_dropdown.setDisabled(False)

        # Jalankan scan di thread baru
        t = Thread(target=worker)
        t.start()

    def on_ble_selected(self, index: int):
        thermal.on_ble_selected(index)

    def update_thermal_info(self, type, text):
        if type == "BLE":
            self.ble_info.setText(text)

    def update_thermal_edit(self, service, char):
        self.thermal_service_uuid_edit.setText(service)
        self.thermal_char_uuid_edit.setText(char)

    # ------------------- Other -------------------
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

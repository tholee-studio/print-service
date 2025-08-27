import sys
import json
import os
import asyncio
from typing import List, Optional

from PySide6.QtWidgets import (
    QApplication,
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
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtGui import QPainter, QImage, QScreen, QPageSize, QPageLayout
from PySide6.QtCore import Qt, QDateTime, QSizeF, QMarginsF

from flask import Flask, jsonify, request
from flask_cors import CORS
from threading import Thread, Lock
import tempfile

# USB thermal printing
from escpos.printer import Usb
import usb.core
import usb.util

# BLE
from bleak import BleakScanner, BleakClient

# Image utils for USB flow
from PIL import Image, ImageDraw, ImageFont

# ESC/POS builder for BLE flow
from escpos.printer import Dummy

CONFIG_USB_FILE = "thermal_printer_settings.json"
CONFIG_PRINT_FILE = "print_settings.json"

DEFAULT_BLE_SERVICE_UUID = ""
DEFAULT_BLE_CHAR_UUID = ""


class PrintServerApp(QMainWindow):
    def __init__(self):
        super().__init__()

        # Create main window.
        self.setWindowTitle("Tholee Print Service")
        self.setGeometry(100, 100, 700, 600)
        screen_center = QScreen.availableGeometry(QApplication.primaryScreen()).center()
        window_geometry = self.frameGeometry()
        window_geometry.moveCenter(screen_center)
        self.move(window_geometry.topLeft())

        # Photobooth printer setting state.
        self.print_settings = {}

        # Thermal printer mode.
        self.thermal_mode: str = "USB"  # or "BLE"

        # USB Thermal printer state.
        self.thermal_printers = []
        self.selected_thermal_printer = None

        # BLE Thermal printer state.
        self.ble_devices = []  # list of dict {name, address}
        self.selected_ble_device_addr: Optional[str] = None
        self.ble_service_uuid = ""
        self.ble_char_uuid = ""

        # BLE Thermal printer client state.
        self.ble_client = None
        self.ble_connected = False
        self.ble_connection_lock = asyncio.Lock()
        self.ble_event_loop = None

        # Flask endpoint.
        self.flask_app = Flask(__name__)
        CORS(self.flask_app)
        self.setup_flask_routes()
        self.flask_thread = Thread(target=self.run_flask_app, daemon=True)
        self.flask_thread.start()

        self.print_lock = Lock()

        # UI
        self.init_ui()
        self.detect_thermal_printers()
        self.load_thermal_printer_config()

    # ========================= UI =========================
    def init_ui(self):
        central_widget = QWidget()
        layout = QVBoxLayout()

        # Regular printer config (Qt)
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
        self.settings_btn.clicked.connect(self.open_print_settings)
        config_layout.addWidget(self.settings_btn)

        # ===== Thermal mode selector =====
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Thermal Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["USB", "BLE"])
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        config_layout.addLayout(mode_row)

        # ===== USB thermal section =====
        usb_group = QWidget()
        usb_layout = QVBoxLayout()
        usb_layout.addWidget(QLabel("<b>USB Thermal Printer</b>"))
        usb_controls = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh USB Printers")
        self.refresh_btn.clicked.connect(self.detect_thermal_printers)
        usb_controls.addWidget(self.refresh_btn)
        self.thermal_printer_dropdown = QComboBox()
        self.thermal_printer_dropdown.currentIndexChanged.connect(
            self.on_thermal_printer_selected
        )
        usb_controls.addWidget(self.thermal_printer_dropdown)
        usb_layout.addLayout(usb_controls)
        self.thermal_printer_info = QLabel("No USB thermal printer selected")
        usb_layout.addWidget(self.thermal_printer_info)
        usb_group.setLayout(usb_layout)
        config_layout.addWidget(usb_group)

        # ===== BLE thermal section =====
        ble_group = QWidget()
        ble_layout = QVBoxLayout()
        ble_layout.addWidget(QLabel("<b>BLE Thermal Printer</b>"))

        ble_row = QHBoxLayout()
        self.ble_refresh_btn = QPushButton("Refresh BLE Printers")
        self.ble_refresh_btn.clicked.connect(self.scan_ble_printers)
        ble_row.addWidget(self.ble_refresh_btn)
        self.ble_dropdown = QComboBox()
        self.ble_dropdown.currentIndexChanged.connect(self.on_ble_selected)
        ble_row.addWidget(self.ble_dropdown)
        ble_layout.addLayout(ble_row)

        form = QFormLayout()
        self.service_uuid_edit = QLineEdit(DEFAULT_BLE_SERVICE_UUID)
        self.char_uuid_edit = QLineEdit(DEFAULT_BLE_CHAR_UUID)
        form.addRow("Service UUID", self.service_uuid_edit)
        form.addRow("Char UUID", self.char_uuid_edit)
        ble_layout.addLayout(form)

        save_ble_row = QHBoxLayout()
        self.ble_save_btn = QPushButton("Save BLE Settings")
        self.ble_save_btn.clicked.connect(self.save_thermal_printer_config)
        save_ble_row.addWidget(self.ble_save_btn)
        save_ble_row.addStretch()
        ble_layout.addLayout(save_ble_row)

        self.ble_info = QLabel("No BLE printer selected")
        ble_layout.addWidget(self.ble_info)

        ble_group.setLayout(ble_layout)
        config_layout.addWidget(ble_group)

        # attach config group
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # Log
        self.log_label = QLabel("Activity Log:")
        layout.addWidget(self.log_label)
        self.log_widget = QTextEdit()
        self.log_widget.setReadOnly(True)
        layout.addWidget(self.log_widget)

        # finalize
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

        self.load_print_config()
        self.log_message("Application started")
        self.apply_mode_visibility()

    def apply_mode_visibility(self):
        # In this simple version we just visually hint with labels; all widgets are shown.
        # You can enhance to actually hide/show groups based on mode.
        pass

    # ========================= USB detection =========================
    def detect_thermal_printers(self):
        """Detect connected USB thermal printers"""
        self.thermal_printers = []
        self.thermal_printer_dropdown.clear()

        try:
            devices = usb.core.find(find_all=True)
        except Exception as e:
            self.log_message(f"USB discovery error: {e}")
            devices = []

        for device in devices:
            try:
                if device.idVendor in [0x0416, 0x0FE6, 0x28E9, 0x04B8, 0x067B] or (
                    device.idVendor == 0x6868 and device.idProduct == 0x0500
                ):
                    printer_info = {
                        "vendor_id": device.idVendor,
                        "product_id": device.idProduct,
                        "manufacturer": usb.util.get_string(
                            device, device.iManufacturer
                        ),
                        "product": usb.util.get_string(device, device.iProduct),
                        "serial": usb.util.get_string(device, device.iSerialNumber),
                    }
                    self.thermal_printers.append(printer_info)
                    display_name = (
                        f"{printer_info['manufacturer'] or 'Unknown'} "
                        f"{printer_info['product'] or 'Printer'} "
                        f"(0x{printer_info['vendor_id']:04X}:0x{printer_info['product_id']:04X})"
                    )
                    self.thermal_printer_dropdown.addItem(display_name)
            except usb.core.USBError as e:
                self.log_message(f"USB Error while detecting printers: {str(e)}")
                continue

        if not self.thermal_printers:
            self.thermal_printer_dropdown.addItem("No thermal printers found")
            self.log_message("No thermal printers detected")
        else:
            self.log_message(
                f"Detected {len(self.thermal_printers)} USB thermal printer(s)"
            )

    def on_thermal_printer_selected(self, index):
        if index < 0 or index >= len(self.thermal_printers):
            self.selected_thermal_printer = None
            self.thermal_printer_info.setText("No USB thermal printer selected")
        else:
            self.selected_thermal_printer = self.thermal_printers[index]
            info = self.selected_thermal_printer
            self.thermal_printer_info.setText(
                f"Selected: <b>{info.get('product', 'Unknown Printer')}</b><br>"
                f"Vendor ID: 0x{info['vendor_id']:04X}, Product ID: 0x{info['product_id']:04X}<br>"
                f"Manufacturer: {info.get('manufacturer', 'Unknown')}<br>"
                f"Serial: {info.get('serial', 'N/A')}"
            )
            self.save_thermal_printer_config()

    # ========================= BLE scan & select =========================
    def on_mode_changed(self, mode_text: str):
        self.thermal_mode = mode_text
        self.save_thermal_printer_config()
        self.apply_mode_visibility()
        self.log_message(f"Thermal mode set: {mode_text}")

    def scan_ble_printers(self):
        self.log_message("Scanning BLE devices...")

        async def _scan():
            devices = await BleakScanner.discover()
            return devices

        try:
            devices = asyncio.run(_scan())
        except RuntimeError:
            # If there's already a running loop (rare on macOS GUI), create new loop in thread
            loop = asyncio.new_event_loop()
            devices = loop.run_until_complete(BleakScanner.discover())
            loop.close()

        self.ble_devices = []
        self.ble_dropdown.clear()
        if not devices:
            self.ble_dropdown.addItem("No BLE devices found")
            self.log_message("No BLE devices found")
            return

        for d in devices:
            name = d.name or "(unknown)"
            addr = getattr(d, "address", None) or getattr(d, "mac_address", None) or ""
            self.ble_devices.append({"name": name, "address": addr})
            self.ble_dropdown.addItem(f"{name} — {addr}")

        self.log_message(f"Found {len(self.ble_devices)} BLE device(s)")

    def on_ble_selected(self, index: int):
        if index < 0 or index >= len(self.ble_devices):
            self.selected_ble_device_addr = None
            self.ble_info.setText("No BLE printer selected")
            self.disconnect_ble()

        else:
            self.selected_ble_device_addr = self.ble_devices[index]["address"]
            name = self.ble_devices[index]["name"]
            self.ble_info.setText(
                f"Selected BLE: <b>{name}</b> ({self.selected_ble_device_addr})"
            )
            # Coba auto detect service & characteristic UUID
            self.log_message(
                "Trying to auto-detect BLE Service UUID and Characteristic UUID..."
            )
            # Coba connect ke printer BLE
            self.connect_ble()

            async def _detect_uuids():
                try:
                    async with BleakClient(self.selected_ble_device_addr) as client:
                        services = client.services
                        # Cari service dan char yang kira-kira untuk ESC/POS (biasanya write tanpa response)
                        for service in services:
                            for char in service.characteristics:
                                if (
                                    "write" in char.properties
                                    or "write_without_response" in char.properties
                                ):
                                    return service.uuid, char.uuid
                except Exception as e:
                    self.log_message(f"BLE UUID detection failed: {e}")
                return None, None

            try:
                service_uuid, char_uuid = asyncio.run(_detect_uuids())
            except RuntimeError:
                # Jika event loop sudah jalan (macOS GUI case)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                service_uuid, char_uuid = loop.run_until_complete(_detect_uuids())
                loop.close()

            if service_uuid and char_uuid:
                self.service_uuid_edit.setText(service_uuid)
                self.char_uuid_edit.setText(char_uuid)
                self.log_message(f"Auto-detected Service UUID: {service_uuid}")
                self.log_message(f"Auto-detected Characteristic UUID: {char_uuid}")
            else:
                self.log_message("Failed to auto-detect UUIDs, please enter manually.")

            self.save_thermal_printer_config()

    # Tambahkan fungsi-fungsi BLE connection management:
    async def _connect_ble_async(self):
        if not self.selected_ble_device_addr:
            return False, "No BLE device selected"

        try:
            async with self.ble_connection_lock:
                if self.ble_client and self.ble_connected:
                    return True, "Already connected"

                self.ble_client = BleakClient(self.selected_ble_device_addr)
                await self.ble_client.connect()
                self.ble_connected = True
                self.log_message(f"BLE connected to {self.selected_ble_device_addr}")
                return True, None
        except Exception as e:
            self.ble_connected = False
            error_msg = f"BLE connection failed: {str(e)}"
            self.log_message(error_msg)
            return False, error_msg

    def connect_ble(self):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # Jika tidak ada, buat baru
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Simpan referensi loop
        self.ble_event_loop = loop

        # Jalankan coroutine di loop yang benar
        if loop.is_running():
            # Jika loop sudah berjalan, buat task
            task = loop.create_task(self._connect_ble_async())
            return True, "Connecting..."  # Ini sederhana, bisa diperbaiki
        else:
            # Jika loop tidak berjalan, jalankan sampai selesai
            success, error = loop.run_until_complete(self._connect_ble_async())
            return success, error

    async def _disconnect_ble_async(self):
        async with self.ble_connection_lock:
            if self.ble_client and self.ble_connected:
                try:
                    await self.ble_client.disconnect()
                    self.log_message("BLE disconnected")
                except Exception as e:
                    self.log_message(f"BLE disconnection error: {str(e)}")
                finally:
                    self.ble_connected = False
                    self.ble_client = None

    def disconnect_ble(self):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(self._disconnect_ble_async())

    # Modifikasi fungsi _send_ble_escpos untuk menggunakan koneksi yang sudah ada:
    def _send_ble_escpos(self, data: bytes):
        if not self.ble_client or not self.ble_connected:
            success, error = self.connect_ble()
            if not success:
                raise RuntimeError(f"Not connected to BLE: {error}")

        service_uuid = self.service_uuid_edit.text().strip() or DEFAULT_BLE_SERVICE_UUID
        char_uuid = self.char_uuid_edit.text().strip() or DEFAULT_BLE_CHAR_UUID

        async def _send():
            try:
                # Periksa koneksi lagi dalam lock
                if not self.ble_connected:
                    await self.ble_client.connect()
                    self.ble_connected = True

                # chunk writes (safe ~180B)
                chunk = 180
                for i in range(0, len(data), chunk):
                    await self.ble_client.write_gatt_char(
                        char_uuid, data[i : i + chunk]
                    )
                return True, None
            except Exception as e:
                self.ble_connected = False
                return False, str(e)

        # Gunakan event loop yang sudah disimpan
        if self.ble_event_loop is None:
            raise RuntimeError("No event loop available for BLE operations")

        if self.ble_event_loop.is_running():
            # Jika loop sedang berjalan, kita perlu menunggu hasilnya
            future = asyncio.run_coroutine_threadsafe(_send(), self.ble_event_loop)
            try:
                ok, err = future.result(timeout=30)  # Timeout 30 detik
                if not ok:
                    raise RuntimeError(err or "BLE write failed")
            except Exception as e:
                raise RuntimeError(f"BLE operation failed: {str(e)}")
        else:
            # Jika loop tidak berjalan, jalankan langsung
            ok, err = self.ble_event_loop.run_until_complete(_send())
            if not ok:
                raise RuntimeError(err or "BLE write failed")

    def closeEvent(self, event):
        """Override untuk cleanup saat aplikasi ditutup"""
        if self.ble_client and self.ble_connected:

            async def _disconnect():
                try:
                    await self.ble_client.disconnect()
                except Exception:
                    pass

            if self.ble_event_loop and not self.ble_event_loop.is_closed():
                if self.ble_event_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        _disconnect(), self.ble_event_loop
                    )
                    try:
                        future.result(timeout=5)
                    except Exception:
                        pass
                else:
                    self.ble_event_loop.run_until_complete(_disconnect())

        event.accept()

    # ========================= Persist config =========================
    def save_thermal_printer_config(self):
        try:
            cfg = {
                "mode": self.thermal_mode,
                "usb": self.selected_thermal_printer or {},
                "ble": {
                    "address": self.selected_ble_device_addr or "",
                    "service_uuid": self.service_uuid_edit.text().strip()
                    or DEFAULT_BLE_SERVICE_UUID,
                    "char_uuid": self.char_uuid_edit.text().strip()
                    or DEFAULT_BLE_CHAR_UUID,
                },
            }
            with open(CONFIG_USB_FILE, "w") as f:
                json.dump(cfg, f, indent=4)
            self.log_message("Thermal printer configuration saved")
        except Exception as e:
            self.log_message(f"Error saving thermal printer config: {e}")

    def load_thermal_printer_config(self):
        try:
            with open(CONFIG_USB_FILE, "r") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            self.log_message(f"Error loading thermal printer config: {e}")
            return

        # mode
        mode = cfg.get("mode", "USB")
        if mode in ("USB", "BLE"):
            self.thermal_mode = mode
            self.mode_combo.setCurrentText(mode)

        # USB selection (try match)
        usb_cfg = cfg.get("usb") or {}
        if usb_cfg:
            for idx, p in enumerate(self.thermal_printers):
                if (
                    p.get("vendor_id") == usb_cfg.get("vendor_id")
                    and p.get("product_id") == usb_cfg.get("product_id")
                    and (p.get("serial") or "") == (usb_cfg.get("serial") or "")
                ):
                    self.thermal_printer_dropdown.setCurrentIndex(idx)
                    break

        # BLE cfg
        ble_cfg = cfg.get("ble") or {}
        self.selected_ble_device_addr = ble_cfg.get("address") or None
        self.ble_service_uuid = ble_cfg.get("service_uuid") or DEFAULT_BLE_SERVICE_UUID
        self.ble_char_uuid = ble_cfg.get("char_uuid") or DEFAULT_BLE_CHAR_UUID
        self.service_uuid_edit.setText(self.ble_service_uuid)
        self.char_uuid_edit.setText(self.ble_char_uuid)

        # show loaded BLE info text if not in list yet
        if self.selected_ble_device_addr:
            self.ble_info.setText(
                f"Selected BLE: <b>{self.selected_ble_device_addr}</b> (address)"
            )

        self.log_message("Loaded thermal printer configuration")

    # ========================= Qt Printer config =========================
    def open_print_settings(self):
        printer = QPrinter()
        self.load_print_config_to_printer(printer)
        print_dialog = QPrintDialog(printer, self)
        if print_dialog.exec() == QPrintDialog.Accepted:
            self.save_print_config(printer)
            self.log_message(f"Printer settings updated: {self.print_settings}")

    def save_print_config(self, printer: QPrinter):
        with self.print_lock:
            layout = printer.pageLayout()
            page_size = layout.pageSize()
            self.print_settings = {
                "printer_name": printer.printerName(),
                "paper_size": {
                    "type": (
                        "custom" if page_size.id() == QPageSize.Custom else "standard"
                    ),
                    "name": page_size.name(),
                    "width_mm": page_size.size(QPageSize.Millimeter).width(),
                    "height_mm": page_size.size(QPageSize.Millimeter).height(),
                },
                "orientation": layout.orientation().name,
            }
            with open(CONFIG_PRINT_FILE, "w") as f:
                json.dump(self.print_settings, f, indent=4)
            self.update_printer_info()

    def load_print_config(self):
        try:
            with self.print_lock:
                with open(CONFIG_PRINT_FILE, "r") as f:
                    self.print_settings = json.load(f)
                self.update_printer_info()
                return True
        except FileNotFoundError:
            self.print_settings = {}
            return False
        except json.JSONDecodeError:
            self.print_settings = {}
            return False

    def update_printer_info(self):
        paper_name = self.print_settings.get("paper_size", {}).get("name", "-")
        orientation = self.print_settings.get("orientation", "-")
        self.printer_info.setText(
            f"PRINTER: <b>{self.print_settings.get('printer_name','-')}</b> <br> PAPER: <b>{paper_name}</b> <br> ORIENTATION: <b>{orientation}</b>"
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

    def load_print_config_to_printer(self, printer: QPrinter):
        try:
            with self.print_lock:
                if not self.print_settings:
                    return False
                printer.setPrinterName(self.print_settings.get("printer_name", ""))
                paper_config = self.print_settings.get("paper_size", {})
                if paper_config.get("type") == "custom":
                    width = paper_config["width_mm"]
                    height = paper_config["height_mm"]
                    custom_size = QSizeF(width, height)
                    page_size = QPageSize(custom_size, QPageSize.Millimeter)
                    printer.setPageSize(page_size)
                else:
                    try:
                        size_name = paper_config.get("name", "A4")
                        page_size = QPageSize(
                            getattr(QPageSize, size_name, QPageSize.A4)
                        )
                        printer.setPageSize(page_size)
                    except Exception:
                        printer.setPageSize(QPageSize(QPageSize.A4))
                orientation = (
                    printer.pageLayout().Orientation.Landscape
                    if self.print_settings.get("orientation") == "Landscape"
                    else printer.pageLayout().Orientation.Portrait
                )
                printer.setPageOrientation(orientation)
                return True
        except Exception as e:
            self.log_message(f"Error loading config to printer: {str(e)}")
            return False

    # ========================= Flask =========================
    def setup_flask_routes(self):
        @self.flask_app.route("/print", methods=["POST"])
        def handle_print():
            # identical to your previous implementation
            if "file" not in request.files:
                error_msg = "'file' field is required."
                self.log_message(f"Print error: {error_msg}")
                return jsonify({"ok": False, "message": error_msg}), 400
            try:
                printer = QPrinter(QPrinter.PrinterMode.HighResolution)
                if not self.load_print_config_to_printer(printer):
                    error_msg = "Printer configuration failed"
                    self.log_message(f"Print error: {error_msg}")
                    return jsonify({"ok": False, "message": error_msg}), 500
                image_file = request.files["file"]
                temp_dir = tempfile.gettempdir()
                temp_path = os.path.join(temp_dir, image_file.filename)
                image_file.save(temp_path)
                image = QImage(temp_path)
                if image.isNull():
                    error_msg = "Image is not valid."
                    self.log_message(f"Print error: {error_msg}.")
                    return jsonify({"ok": False, "message": error_msg}), 400
                printer.setDocName(image_file.filename)
                # printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                # printer.setDocName("output.pdf")  # Nama dokumen
                # printer.setOutputFileName("output.pdf")  # Simpan ke file PDF
                printer.setPageMargins(
                    QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter
                )

                # Deteksi platform
                is_mac = sys.platform == "darwin"

                # Deteksi orientasi gambar dan atur orientasi halaman
                img_width = image.width()
                img_height = image.height()

                # Tentukan orientasi berdasarkan aspek rasio gambar
                if img_width > img_height:
                    # Gambar landscape - gunakan orientasi landscape
                    page_orientation = (
                        printer.pageLayout().Orientation.Landscape
                        if not is_mac
                        else printer.pageLayout().Orientation.Portrait
                    )
                else:
                    # Gambar portrait atau square - gunakan orientasi portrait
                    page_orientation = (
                        printer.pageLayout().Orientation.Portrait
                        if not is_mac
                        else printer.pageLayout().Orientation.Landscape
                    )

                printer.setPageOrientation(page_orientation)
                painter = QPainter()
                painter.begin(printer)

                # Dapatkan ukuran halaman PDF (area yang bisa dipakai)
                page_rect = painter.viewport()

                # Hitung scaling agar gambar fit di halaman (jaga aspect ratio)
                img_size = image.size()
                img_size.scale(page_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)

                # Hitung posisi x dan y untuk center
                x = (page_rect.width() - img_size.width()) // 2
                y = (page_rect.height() - img_size.height()) // 2

                # Atur viewport (area gambar) dan window (koordinat gambar)
                painter.setViewport(x, y, img_size.width(), img_size.height())
                painter.setWindow(image.rect())

                # Gambar di (0,0) karena window sudah diatur
                painter.drawImage(0, 0, image)
                painter.end()

                os.remove(temp_path)
                success_msg = f"Print job sent: {image_file.filename}."
                self.log_message(success_msg)
                return jsonify({"status": "success", "message": "Print job sent."})
            except Exception as e:
                error_msg = str(e)
                self.log_message(f"Print error: {error_msg}")
                return jsonify({"ok": False, "message": error_msg}), 500

        @self.flask_app.route("/print/thermal", methods=["POST"])
        def handle_print_thermal():
            mode = self.thermal_mode
            if mode == "USB":
                return self._handle_print_thermal_usb()
            elif mode == "BLE":
                return self._handle_print_thermal_ble()
            else:
                return jsonify({"ok": False, "message": f"Unknown mode: {mode}"}), 400

        @self.flask_app.route("/status", methods=["GET"])
        def check_status():
            return jsonify(
                {
                    "status": "ready",
                    "qt_printer": self.print_settings.get(
                        "printer_name", "Not configured"
                    ),
                    "paper": self.print_settings.get("paper_size", "Not configured"),
                    "thermal_mode": self.thermal_mode,
                }
            )

    def run_flask_app(self):
        self.flask_app.run(host="0.0.0.0", port=2462)

    # ========================= Thermal print helpers =========================
    def _extract_common_payload(self):
        # Accept either form-data or query params
        url = request.form.get("url") or request.args.get("url")
        code = request.form.get("code") or request.args.get("code")
        if not url or not code:
            return None, None, "'url' and 'code' are required"
        return url, code, None

    def _build_escpos_bytes_for_ble(self, url: str, code: str) -> bytes:
        """Build ESC/POS sequence similar to USB flow using Dummy()"""
        dummy = Dummy()
        # center
        dummy.set(align="center")
        # optional logo
        logo_path = os.path.join(os.path.dirname(__file__), "assets/logo.png")
        if os.path.exists(logo_path):
            try:
                dummy.image(logo_path, center=True)
                dummy.textln("-------------------------------")
            except Exception as e:
                self.log_message(f"BLE: failed to add logo: {e}")
        # QR + code
        try:
            dummy.qr(url, size=8)
        except Exception as e:
            self.log_message(f"BLE: failed to add QR: {e}")
        dummy.textln(f"CODE: {code}")
        dummy.textln("")
        # Big headings (ESC/POS doesn't support variable fonts; rely on double size)
        dummy.set(width=2, height=2, align="center")
        dummy.textln("SCAN QR")
        dummy.set(width=1, height=2)
        dummy.textln("TO DOWNLOAD")
        dummy.set(width=1, height=1)
        dummy.textln("")
        dummy.textln("-------------------------------")
        dummy.textln("powered by Tholee Studio")
        dummy.textln("@tholee.studio | 0895 2500 9655")
        dummy.textln("-------------------------------")
        dummy.ln(2)
        # dummy.cut()

        return dummy.output

    # def _send_ble_escpos(
    #     self, address: str, service_uuid: str, char_uuid: str, data: bytes
    # ):
    #     async def _send():
    #         client = BleakClient(address)
    #         try:
    #             await client.connect()
    #             # chunk writes (safe ~180B)
    #             chunk = 180
    #             for i in range(0, len(data), chunk):
    #                 await client.write_gatt_char(char_uuid, data[i : i + chunk])
    #             return True, None
    #         except Exception as e:
    #             return False, str(e)
    #         finally:
    #             if client.is_connected:
    #                 try:
    #                     await client.disconnect()
    #                 except Exception:
    #                     pass

    #     try:
    #         ok, err = asyncio.run(_send())
    #     except RuntimeError:
    #         # existing loop case
    #         loop = asyncio.new_event_loop()
    #         ok, err = loop.run_until_complete(_send())
    #         loop.close()
    #     if not ok:
    #         raise RuntimeError(err or "BLE write failed")

    # ========================= USB route =========================
    def _handle_print_thermal_usb(self):
        if not self.selected_thermal_printer:
            error_msg = "No USB thermal printer selected"
            self.log_message(f"Print error: {error_msg}")
            return jsonify({"ok": False, "message": error_msg}), 400

        url, code, err = self._extract_common_payload()
        if err:
            self.log_message(f"Print error: {err}")
            return jsonify({"ok": False, "message": err}), 400

        try:
            pinfo = self.selected_thermal_printer
            printer = Usb(
                pinfo["vendor_id"], pinfo["product_id"]
            )  # may need permissions on macOS/Linux
            # feedback
            try:
                printer.buzzer(2, 1)
            except Exception:
                pass

            def print_with_font(
                text, size=20, weight=600, slant=0, padding=0, margintop=0
            ):
                font_path = "assets/Exo-VariableFont_wght.ttf"
                try:
                    font = ImageFont.truetype(font_path, size)
                    if hasattr(font, "set_variation_by_axes"):
                        font.set_variation_by_axes([weight, slant])
                except Exception:
                    font = ImageFont.load_default()
                if hasattr(font, "getbbox"):
                    left, top, right, bottom = font.getbbox(text)
                    text_width = right - left
                    text_height = bottom - top + padding
                else:
                    text_width, text_height = font.getsize(text)
                    text_height += padding
                img = Image.new("1", (384, text_height + 6), 1)
                draw = ImageDraw.Draw(img)
                x_position = (384 - text_width) // 2
                draw.text((x_position, margintop), text, font=font, fill=0)
                printer.image(img)

            logo_path = os.path.join(os.path.dirname(__file__), "assets/logo.png")
            try:
                if os.path.exists(logo_path):
                    printer.set(align="center")
                    printer.image(logo_path, center=True)
                    printer.ln()
                    printer.textln("-------------------------------")
                    printer.ln(2)
            except Exception as e:
                self.log_message(f"Failed to print logo: {str(e)}")

            printer.set(align="center")
            try:
                printer.qr(url, size=8)
            except Exception as e:
                self.log_message(f"Failed to print QR: {e}")
            print_with_font("CODE: " + code, margintop=-5)
            printer.ln(2)
            print_with_font("SCAN QR", 48, 800, padding=5, margintop=-5)
            print_with_font("TO DOWNLOAD", 36, 800, padding=5, margintop=-5)
            printer.ln(3)
            printer.textln("-------------------------------")
            print_with_font("powered by Tholee Studio")
            print_with_font("@tholee.studio | 0895 2500 9655")
            printer.textln("-------------------------------")
            printer.ln(2)
            try:
                printer.close()
            except Exception:
                pass

            success_msg = "USB thermal print job sent successfully"
            self.log_message(success_msg)
            return jsonify(
                {
                    "status": "success",
                    "message": success_msg,
                    "mode": "USB",
                    "printer": {
                        "vendor_id": hex(pinfo["vendor_id"]),
                        "product_id": hex(pinfo["product_id"]),
                    },
                }
            )
        except Exception as e:
            error_msg = f"USB thermal print error: {str(e)}"
            self.log_message(error_msg)
            return jsonify({"ok": False, "message": str(e)}), 500

    # ========================= BLE route =========================
    def _handle_print_thermal_ble(self):
        # ensure BLE target & uuids
        addr = self.selected_ble_device_addr or (
            self._read_cfg_key(["ble", "address"]) or None
        )
        service_uuid = self.service_uuid_edit.text().strip() or DEFAULT_BLE_SERVICE_UUID
        char_uuid = self.char_uuid_edit.text().strip() or DEFAULT_BLE_CHAR_UUID
        if not addr:
            error_msg = "No BLE printer selected"
            self.log_message(f"Print error: {error_msg}")
            return jsonify({"ok": False, "message": error_msg}), 400
        if not service_uuid or not char_uuid:
            error_msg = "BLE Service/Characteristic UUID not set"
            self.log_message(f"Print error: {error_msg}")
            return jsonify({"ok": False, "message": error_msg}), 400

        url, code, err = self._extract_common_payload()
        if err:
            self.log_message(f"Print error: {err}")
            return jsonify({"ok": False, "message": err}), 400

        try:
            data = self._build_escpos_bytes_for_ble(url, code)
            self._send_ble_escpos(data)  # Tidak perlu parameter tambahan
            # self._send_ble_escpos(addr, service_uuid, char_uuid, data)
            success_msg = "BLE thermal print job sent successfully"
            self.log_message(success_msg)
            return jsonify(
                {
                    "status": "success",
                    "message": success_msg,
                    "mode": "BLE",
                    "printer": {
                        "address": addr,
                        "service_uuid": service_uuid,
                        "char_uuid": char_uuid,
                    },
                }
            )
        except Exception as e:
            error_msg = f"BLE thermal print error: {str(e)}"
            self.log_message(error_msg)
            return jsonify({"ok": False, "message": str(e)}), 500

    def _read_cfg_key(self, path: List[str]):
        try:
            with open(CONFIG_USB_FILE, "r") as f:
                cfg = json.load(f)
            cur = cfg
            for k in path:
                cur = cur.get(k, {})
            return cur if cur != {} else None
        except Exception:
            return None


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PrintServerApp()
    window.show()
    sys.exit(app.exec())
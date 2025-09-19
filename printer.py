from bridge import bridge

import os
import sys
import json
import tempfile

from PySide6.QtPrintSupport import QPrinter
from PySide6.QtGui import QPainter, QImage, QPageSize, QPageLayout
from PySide6.QtCore import Qt, QSizeF, QMarginsF

CONFIG_PRINT_FILE = "config.json"


class Printer:
    def __init__(self):
        self.printer_config = {}

    def load_printer_with_config(self):
        try:
            local_printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            self.load_printer_config()
            local_printer.setPrinterName(self.printer_config.get("printer_name", ""))
            paper_config = self.printer_config.get("paper_size", {})
            if paper_config.get("type") == "custom":
                width = paper_config["width_mm"]
                height = paper_config["height_mm"]
                custom_size = QSizeF(width, height)
                page_size = QPageSize(custom_size, QPageSize.Millimeter)
                local_printer.setPageSize(page_size)
            else:
                try:
                    size_name = paper_config.get("name", "A4")
                    page_size = QPageSize(getattr(QPageSize, size_name, QPageSize.A4))
                    local_printer.setPageSize(page_size)
                except Exception:
                    local_printer.setPageSize(QPageSize(QPageSize.A4))
            orientation = (
                local_printer.pageLayout().Orientation.Landscape
                if self.printer_config.get("orientation") == "Landscape"
                else local_printer.pageLayout().Orientation.Portrait
            )
            local_printer.setPageOrientation(orientation)
            return local_printer

        except Exception as e:
            bridge.add_log.emit(f"Error loading config to printer: {str(e)}")
            return None

    def load_printer_config(self):
        try:
            with open(CONFIG_PRINT_FILE, "r") as f:
                self.printer_config = json.load(f)
            return self.printer_config
        except FileNotFoundError:
            self.printer_config = {}
            return None
        except json.JSONDecodeError:
            self.printer_config = {}
            return None

    def save_printer_config(self, local_printer: QPrinter):
        layout = local_printer.pageLayout()
        page_size = layout.pageSize()
        self.printer_config = {
            "printer_name": local_printer.printerName(),
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
            json.dump(self.printer_config, f, indent=4)

        bridge.add_log.emit(f"Printer settings updated: {self.printer_config}")
        return self.printer_config

    def print_file(self, image_file):
        # Save image from request to tempdir.
        filename = image_file.filename
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, filename)
        image_file.save(temp_path)

        # Set printer.
        local_printer = self.load_printer_with_config()
        local_printer.setDocName(filename)
        local_printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)

        # Create image object.
        image = QImage(temp_path)
        if image.isNull():
            error_msg = "Image is not valid."
            bridge.add_log.emit(f"Print error: {error_msg}")
            raise Exception(error_msg)

        # Check platform.
        is_mac = sys.platform == "darwin"

        # Check image orientation and modify page orientation.
        img_width = image.width()
        img_height = image.height()

        # Set orientation same as image aspect ratio.
        if img_width > img_height:
            # Landscape image - use landscape orientation.
            page_orientation = (
                local_printer.pageLayout().Orientation.Landscape
                if not is_mac
                else local_printer.pageLayout().Orientation.Portrait
            )
        else:
            # Portrait or Square image - use portrait orientation.
            page_orientation = (
                local_printer.pageLayout().Orientation.Portrait
                if not is_mac
                else local_printer.pageLayout().Orientation.Landscape
            )

        local_printer.setPageOrientation(page_orientation)

        painter = QPainter()
        painter.begin(local_printer)

        # Get PDF size (usable area).
        page_rect = painter.viewport()

        # Calculate scaling so the image fit with page (preserve aspect ratio).
        img_size = image.size()
        img_size.scale(page_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)

        # Calculate x and y position to center.
        x = (page_rect.width() - img_size.width()) // 2
        y = (page_rect.height() - img_size.height()) // 2

        # Set viewport (image area) and window (image coordinate).
        painter.setViewport(x, y, img_size.width(), img_size.height())
        painter.setWindow(image.rect())

        # Draw in (0,0) because window is configured.
        painter.drawImage(0, 0, image)
        painter.end()

        os.remove(temp_path)
        success_msg = f"Print job sent: {filename}."
        bridge.add_log.emit(success_msg)


printer = Printer()

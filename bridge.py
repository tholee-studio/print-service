from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    add_log = Signal(str)
    update_thermal_info = Signal(str, str) # BLE or USB, Text.
    update_thermal_edit = Signal(str, str) # service, characteristics.


bridge = Bridge()

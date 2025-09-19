from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    add_log = Signal(str)


bridge = Bridge()

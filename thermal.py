from bridge import bridge
import asyncio
import os

from typing import List, Optional
from bleak import BleakScanner, BleakClient

# ESC/POS builder for BLE flow
from escpos.printer import Dummy

DEFAULT_BLE_SERVICE_UUID = ""
DEFAULT_BLE_CHAR_UUID = ""


class Thermal:
    def __init__(self):
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

    def scan_ble_thermal(self):
        bridge.add_log.emit("Scanning BLE devices...")

        async def _scan():
            devices = await BleakScanner.discover()
            return devices

        devices = asyncio.run(_scan())

        self.ble_devices = []
        if not devices:
            bridge.add_log.emit("No BLE devices found")
            return None

        for d in devices:
            name = d.name or "(unknown)"
            addr = getattr(d, "address", None) or getattr(d, "mac_address", None) or ""
            self.ble_devices.append({"name": name, "address": addr})

        bridge.add_log.emit(f"Found {len(devices)} BLE device(s)")
        return self.ble_devices

    def on_ble_selected(self, index: int):
        if index < 0 or index >= len(self.ble_devices):
            self.selected_ble_device_addr = None
            bridge.add_log.emit("No BLE printer selected")

            self.disconnect_ble()
        else:
            self.selected_ble_device_addr = self.ble_devices[index]["address"]
            name = self.ble_devices[index]["name"]
            bridge.add_log.emit(
                f"Selected BLE: <b>{name}</b> ({self.selected_ble_device_addr})"
            )
            bridge.add_log.emit(
                "Trying to auto-detect BLE Service UUID and Characteristic UUID..."
            )

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
                    bridge.add_log.emit(f"BLE UUID detection failed: {e}")
                return None, None

            try:
                service_uuid, char_uuid = asyncio.run(_detect_uuids())
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                service_uuid, char_uuid = loop.run_until_complete(_detect_uuids())
                loop.close()

            if service_uuid and char_uuid:
                bridge.update_thermal_edit.emit(service_uuid, char_uuid)
                self.ble_service_uuid = service_uuid
                self.ble_char_uuid = char_uuid
                bridge.add_log.emit(f"Auto-detected Service UUID: {service_uuid}")
                bridge.add_log.emit(f"Auto-detected Characteristic UUID: {char_uuid}")
            else:
                bridge.add_log.emit(
                    "Failed to auto-detect UUIDs, please enter manually."
                )

            # self.save_thermal_printer_config()

    def connect_ble(self):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        self.ble_event_loop = loop

        if loop.is_running():
            task = loop.create_task(self._connect_ble_async())
            return True, "Connecting..."
        else:
            success, error = loop.run_until_complete(self._connect_ble_async())
            return success, error

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
                bridge.add_log.emit(f"BLE connected to {self.selected_ble_device_addr}")
                return True, None
        except Exception as e:
            self.ble_connected = False
            error_msg = f"BLE connection failed: {str(e)}"
            bridge.add_log.emit(error_msg)
            return False, error_msg

    async def _disconnect_ble_async(self):
        async with self.ble_connection_lock:
            if self.ble_client and self.ble_connected:
                try:
                    await self.ble_client.disconnect()
                    bridge.add_log.emit("BLE disconnected")
                except Exception as e:
                    bridge.add_log.emit(f"BLE disconnection error: {str(e)}")
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

    def print_thermal_ble(self, url, code):
        # ensure BLE target & uuids
        addr = self.selected_ble_device_addr
        # service_uuid = self.service_uuid_edit.text().strip() or DEFAULT_BLE_SERVICE_UUID
        # char_uuid = self.char_uuid_edit.text().strip() or DEFAULT_BLE_CHAR_UUID

        if not addr:
            error_msg = "No BLE printer selected"
            bridge.add_log.emit(f"Print error: {error_msg}")
            raise Exception(error_msg)
        if not self.ble_service_uuid or not self.ble_char_uuid:
            error_msg = "BLE Service/Characteristic UUID not set"
            bridge.add_log.emit(f"Print error: {error_msg}")
            raise Exception(error_msg)

        try:
            data = self._build_escpos_bytes_for_ble(url, code)
            self._send_ble_escpos(data)  # Tidak perlu parameter tambahan
            success_msg = "BLE thermal print job sent successfully"
            bridge.add_log.emit(success_msg)
            return True
        except Exception as e:
            error_msg = f"BLE thermal print error: {str(e)}"
            bridge.add_log.emit(error_msg)
            raise Exception(str(e))

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
                self.bridge.add_log.emit(f"BLE: failed to add logo: {e}")
        # QR + code
        try:
            dummy.qr(url, size=8)
        except Exception as e:
            self.bridge.add_log.emit(f"BLE: failed to add QR: {e}")
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

    def _send_ble_escpos(self, data: bytes):
        if not self.ble_client or not self.ble_connected:
            success, error = self.connect_ble()
            if not success:
                raise RuntimeError(f"Not connected to BLE: {error}")

        # service_uuid = self.service_uuid_edit.text().strip() or DEFAULT_BLE_SERVICE_UUID
        # char_uuid = self.char_uuid_edit.text().strip() or DEFAULT_BLE_CHAR_UUID

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
                        self.ble_char_uuid, data[i : i + chunk]
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


thermal = Thermal()

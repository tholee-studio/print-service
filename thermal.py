from bridge import bridge
import asyncio
import os
import json
import usb.core
import usb.util

from typing import List, Optional
from bleak import BleakScanner, BleakClient

# ESC/POS builder for BLE flow
from escpos.printer import Dummy

DEFAULT_BLE_SERVICE_UUID = ""
DEFAULT_BLE_CHAR_UUID = ""
CONFIG_FILE = "config.json"


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

        # USB Thermal printer state.
        self.usb_devices = []  # list of dict with device info
        self.selected_usb_device = None

        # Known thermal printer vendor IDs
        self.thermal_vendor_ids = [
            0x0416,  # SEWOO
            0x0FE6,  # ICS Advent
            0x28E9,  # GoDEX International
            0x04B8,  # EPSON
            0x067B,  # Prolific Technology
            0x6868,  # Custom vendor (common for Chinese printers)
            0x1FC9,  # NXP Semiconductors (some thermal printers)
            0x0483,  # STMicroelectronics (some thermal printers)
        ]

    def scan_ble_thermal(self):
        bridge.add_log.emit("Starting BLE scan...")
        bridge.add_log.emit("⏳ This may take 10-30 seconds, please wait...")

        async def _scan():
            try:
                # Add timeout to discovery
                devices = await BleakScanner.discover(timeout=15.0)
                return devices
            except Exception as e:
                bridge.add_log.emit(f"BLE scan failed: {str(e)}")
                return []

        try:
            devices = asyncio.run(_scan())
        except Exception as e:
            bridge.add_log.emit(f"BLE scan error: {str(e)}")
            return []

        self.ble_devices = []
        if not devices:
            bridge.add_log.emit("❌ No BLE devices found")
            return None

        bridge.add_log.emit(f"📡 Processing {len(devices)} discovered device(s)...")

        for d in devices:
            name = d.name or "(unknown)"
            addr = getattr(d, "address", None) or getattr(d, "mac_address", None) or ""
            self.ble_devices.append({"name": name, "address": addr})

        bridge.add_log.emit(f"✅ Found {len(devices)} BLE device(s)")
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
            bridge.add_log.emit("🔍 Connecting to device...")
            bridge.add_log.emit("⏳ Auto-detecting Service and Characteristic UUID...")
            bridge.add_log.emit("   This process may take 10-15 seconds...")

            # Run connection and UUID detection in separate thread to avoid UI freeze
            def detection_worker():
                try:
                    # First connect
                    success, error = self.connect_ble()
                    if not success:
                        bridge.add_log.emit(f"❌ Connection failed: {error}")
                        return

                    # Then detect UUIDs with timeout
                    async def _detect_uuids():
                        try:
                            bridge.add_log.emit("🔌 Connected! Analyzing device services...")
                            async with BleakClient(self.selected_ble_device_addr) as client:
                                await asyncio.sleep(0.5)  # Small delay for stability
                                services = client.services
                                
                                # Convert services to list to get count
                                service_list = list(services.services.values()) if hasattr(services, 'services') else list(services)
                                bridge.add_log.emit(f"📋 Found {len(service_list)} service(s), checking characteristics...")
                                
                                # Look for service and char suitable for ESC/POS
                                for service in services:
                                    bridge.add_log.emit(f"🔍 Checking service: {service.uuid}")
                                    for char in service.characteristics:
                                        bridge.add_log.emit(f"   - Characteristic: {char.uuid} (properties: {char.properties})")
                                        if (
                                            "write" in char.properties
                                            or "write-without-response" in char.properties
                                        ):
                                            bridge.add_log.emit(f"✅ Found suitable characteristic for printing!")
                                            return service.uuid, char.uuid
                                
                                bridge.add_log.emit("⚠️ No suitable write characteristics found")
                        except Exception as e:
                            bridge.add_log.emit(f"UUID detection failed: {e}")
                        return None, None

                    async def _detect_with_timeout():
                        return await asyncio.wait_for(_detect_uuids(), timeout=15.0)

                    try:
                        # Run the detection with proper event loop handling
                        try:
                            service_uuid, char_uuid = asyncio.run(_detect_with_timeout())
                        except RuntimeError:
                            # Handle event loop issues
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                service_uuid, char_uuid = loop.run_until_complete(_detect_with_timeout())
                            finally:
                                loop.close()
                    except asyncio.TimeoutError:
                        bridge.add_log.emit("⏰ UUID detection timeout - please set manually")
                        return

                    if service_uuid and char_uuid:
                        bridge.update_thermal_edit.emit(service_uuid, char_uuid)
                        self.ble_service_uuid = service_uuid
                        self.ble_char_uuid = char_uuid
                        bridge.add_log.emit(f"✅ Auto-detected Service UUID: {service_uuid}")
                        bridge.add_log.emit(f"✅ Auto-detected Characteristic UUID: {char_uuid}")
                        bridge.add_log.emit("🎉 Device ready for printing!")
                    else:
                        bridge.add_log.emit("⚠️ Failed to auto-detect UUIDs, please enter manually.")

                except Exception as e:
                    bridge.add_log.emit(f"❌ Detection process failed: {str(e)}")

            # Run in separate thread to keep UI responsive
            from threading import Thread
            t = Thread(target=detection_worker)
            t.daemon = True
            t.start()

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

                bridge.add_log.emit("🔌 Establishing BLE connection...")
                self.ble_client = BleakClient(self.selected_ble_device_addr)
                
                # Add timeout to connection
                await asyncio.wait_for(self.ble_client.connect(), timeout=10.0)
                self.ble_connected = True
                bridge.add_log.emit(f"✅ BLE connected to {self.selected_ble_device_addr}")
                return True, None
        except asyncio.TimeoutError:
            self.ble_connected = False
            error_msg = "BLE connection timeout (10s) - device may be out of range"
            bridge.add_log.emit(f"⏰ {error_msg}")
            return False, error_msg
        except Exception as e:
            self.ble_connected = False
            error_msg = f"BLE connection failed: {str(e)}"
            bridge.add_log.emit(f"❌ {error_msg}")
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
                bridge.add_log.emit(f"BLE: failed to add logo: {e}")
        # QR + code
        try:
            dummy.qr(url, size=8)
        except Exception as e:
            bridge.add_log.emit(f"BLE: failed to add QR: {e}")
        dummy.textln(f"CODE: {code}")
        dummy.textln("")

        # Reset to normal first
        dummy.set(width=1, height=1, align="center")

        dummy._raw(b"\x1b\x21\x30")  # Double height and width
        dummy.textln("SCAN QR")
        dummy.textln("UNTUK DOWNLOAD")
        dummy._raw(b"\x1b\x21\x00")  # Reset to normal

        # Reset to normal size
        dummy.set(width=1, height=1, align="center")
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

    # ================== USB Thermal Printer Methods ==================
    def scan_usb_thermal(self):
        """Scan for USB thermal printers"""
        bridge.add_log.emit("Scanning USB thermal printers...")
        self.usb_devices = []

        try:
            devices = usb.core.find(find_all=True)
        except Exception as e:
            bridge.add_log.emit(f"USB discovery error: {e}")
            return []

        found_count = 0
        for device in devices:
            try:
                if device.idVendor in self.thermal_vendor_ids or (
                    device.idVendor == 0x6868 and device.idProduct == 0x0500
                ):
                    try:
                        manufacturer = (
                            usb.util.get_string(device, device.iManufacturer)
                            if device.iManufacturer
                            else "Unknown"
                        )
                        product = (
                            usb.util.get_string(device, device.iProduct)
                            if device.iProduct
                            else "Thermal Printer"
                        )
                        serial = (
                            usb.util.get_string(device, device.iSerialNumber)
                            if device.iSerialNumber
                            else "N/A"
                        )
                    except (usb.core.USBError, ValueError):
                        manufacturer = "Unknown"
                        product = "Thermal Printer"
                        serial = "N/A"

                    printer_info = {
                        "vendor_id": device.idVendor,
                        "product_id": device.idProduct,
                        "manufacturer": manufacturer,
                        "product": product,
                        "serial": serial,
                        "device": device,
                        "display_name": f"{manufacturer} {product} (0x{device.idVendor:04X}:0x{device.idProduct:04X})",
                    }
                    self.usb_devices.append(printer_info)
                    found_count += 1
                    bridge.add_log.emit(
                        f"Found USB thermal printer: {printer_info['display_name']}"
                    )

            except usb.core.USBError as e:
                # Skip devices that can't be accessed
                continue
            except Exception as e:
                bridge.add_log.emit(f"Error processing USB device: {str(e)}")
                continue

        bridge.add_log.emit(f"Found {found_count} USB thermal printer(s)")
        return self.usb_devices

    def select_usb_thermal(self, index: int):
        """Select a USB thermal printer by index"""
        if index < 0 or index >= len(self.usb_devices):
            self.selected_usb_device = None
            bridge.add_log.emit("No USB thermal printer selected")
            bridge.update_thermal_info.emit("USB", "No USB thermal printer selected")
            return False

        self.selected_usb_device = self.usb_devices[index]
        bridge.add_log.emit(
            f"Selected USB thermal printer: {self.selected_usb_device['display_name']}"
        )
        bridge.update_thermal_info.emit(
            "USB", f"Selected: {self.selected_usb_device['display_name']}"
        )
        return True

    def print_thermal_usb(self, url: str, code: str):
        """Print to USB thermal printer"""
        if not self.selected_usb_device:
            error_msg = "No USB thermal printer selected"
            bridge.add_log.emit(f"Print error: {error_msg}")
            raise Exception(error_msg)

        try:
            # Build ESC/POS data
            data = self._build_escpos_bytes_for_usb(url, code)

            # Send to USB printer
            self._send_usb_escpos(data)

            success_msg = "USB thermal print job sent successfully"
            bridge.add_log.emit(success_msg)
            return True

        except Exception as e:
            error_msg = f"USB thermal print error: {str(e)}"
            bridge.add_log.emit(error_msg)
            raise Exception(str(e))

    def _build_escpos_bytes_for_usb(self, url: str, code: str) -> bytes:
        """Build ESC/POS sequence for USB thermal printer"""
        dummy = Dummy()

        # Center alignment
        dummy.set(align="center")

        # Optional logo
        logo_path = os.path.join(os.path.dirname(__file__), "assets/logo.png")
        if os.path.exists(logo_path):
            try:
                dummy.image(logo_path, center=True)
                dummy.textln("-------------------------------")
            except Exception as e:
                bridge.add_log.emit(f"USB: failed to add logo: {e}")

        # QR code and info
        try:
            dummy.qr(url, size=8)
        except Exception as e:
            bridge.add_log.emit(f"USB: failed to add QR: {e}")

        dummy.textln(f"CODE: {code}")
        dummy.textln("")

        # Reset to normal first
        dummy.set(width=1, height=1, align="center")

        # Method 2: Try raw ESC/POS commands as backup
        dummy._raw(b"\x1b\x21\x30")  # Double height and width
        dummy.textln("SCAN QR")
        dummy.textln("UNTUK DOWNLOAD")
        dummy._raw(b"\x1b\x21\x00")  # Reset to normal

        # Reset to normal size and alignment
        dummy.set(width=1, height=1, align="center")
        dummy.textln("")
        dummy.textln("-------------------------------")
        dummy.textln("powered by Tholee Studio")
        dummy.textln("@tholee.studio | 0895 2500 9655")
        dummy.textln("-------------------------------")
        dummy.ln(2)

        return dummy.output

    def _send_usb_escpos(self, data: bytes):
        """Send ESC/POS data to USB thermal printer"""
        device = self.selected_usb_device["device"]

        try:
            # Try to detach kernel driver if it exists (Linux)
            if device.is_kernel_driver_active(0):
                try:
                    device.detach_kernel_driver(0)
                    bridge.add_log.emit("Detached kernel driver")
                except usb.core.USBError:
                    pass  # May not be supported on all systems

            # Set configuration
            try:
                device.set_configuration()
            except usb.core.USBError as e:
                if "Configuration value" not in str(e):
                    raise e

            # Find the endpoint
            cfg = device.get_active_configuration()
            intf = cfg[(0, 0)]

            # Find OUT endpoint
            ep_out = None
            for ep in intf:
                if (
                    usb.util.endpoint_direction(ep.bEndpointAddress)
                    == usb.util.ENDPOINT_OUT
                ):
                    ep_out = ep
                    break

            if ep_out is None:
                raise Exception("Could not find OUT endpoint")

            bridge.add_log.emit(f"Using endpoint: 0x{ep_out.bEndpointAddress:02x}")

            # Send data in chunks
            chunk_size = 8192  # 8KB chunks
            total_sent = 0

            for i in range(0, len(data), chunk_size):
                chunk = data[i : i + chunk_size]
                try:
                    sent = ep_out.write(chunk, timeout=5000)  # 5 second timeout
                    total_sent += sent
                    bridge.add_log.emit(f"Sent chunk {i//chunk_size + 1}: {sent} bytes")
                except usb.core.USBTimeoutError:
                    bridge.add_log.emit("USB write timeout - printer may be processing")
                    break
                except usb.core.USBError as e:
                    bridge.add_log.emit(f"USB write error: {e}")
                    break

            bridge.add_log.emit(f"Total bytes sent: {total_sent}/{len(data)}")

        except Exception as e:
            bridge.add_log.emit(f"USB communication error: {str(e)}")
            raise Exception(f"Failed to send to USB printer: {str(e)}")

        finally:
            # Try to release interface and reattach kernel driver
            try:
                usb.util.release_interface(device, 0)
            except:
                pass

    # ------------------- Config Management -------------------
    def save_thermal_mode(self, mode: str):
        """Save thermal mode (BLE/USB) to config file"""
        try:
            # Load existing config or create new one
            config = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
            
            # Update thermal mode
            config['thermal_mode'] = mode
            
            # Save back to file
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
                
            bridge.add_log.emit(f"Thermal mode saved: {mode}")
            return True
            
        except Exception as e:
            bridge.add_log.emit(f"Failed to save thermal mode: {str(e)}")
            return False

    def load_thermal_mode(self) -> str:
        """Load thermal mode from config file. Returns 'BLE' as default."""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    return config.get('thermal_mode', 'BLE')  # Default to BLE
            return 'BLE'  # Default if no config file exists
            
        except Exception as e:
            bridge.add_log.emit(f"Failed to load thermal mode: {str(e)}")
            return 'BLE'  # Default on error

    def get_thermal_mode(self) -> str:
        """Get current thermal mode"""
        return self.load_thermal_mode()


thermal = Thermal()

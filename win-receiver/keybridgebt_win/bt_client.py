"""
RFCOMM client — connects to the mac-sender's Bluetooth RFCOMM channel
via a serial COM port (pyserial). Reads raw bytes and delivers to a callback.
Auto-reconnects with exponential backoff.

See docs/ARCHITECTURE.md §5.1 and docs/TASKS.md Task 8.
"""

import serial
import serial.tools.list_ports
import threading
import logging
import time

log = logging.getLogger(__name__)

MAX_BACKOFF = 30  # seconds


class RFCOMMClient:
    """Connect to a Bluetooth serial (RFCOMM/COM) port and read raw data."""

    def __init__(self, port=None, baudrate=115200, callback=None):
        self._port = port
        self._baudrate = baudrate
        self._callback = callback
        self._serial = None
        self._thread = None
        self._running = False
        self._connected = threading.Event()
        self._on_connect_callback = None
        self._on_disconnect_callback = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def port_name(self) -> str:
        return self._port or "auto"

    def set_callbacks(self, on_connect=None, on_disconnect=None):
        self._on_connect_callback = on_connect
        self._on_disconnect_callback = on_disconnect

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected.clear()
        if self._thread:
            self._thread.join(timeout=5)

    def _connect_loop(self):
        backoff = 1
        while self._running:
            port = self._port or self._detect_bt_port()
            if port is None:
                log.info("No Bluetooth serial port found, retrying in %ds…", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue

            try:
                log.info("Connecting to %s…", port)
                self._serial = serial.Serial(
                    port=port,
                    baudrate=self._baudrate,
                    timeout=0.1,
                )
                self._connected.set()
                backoff = 1  # reset on successful connect
                log.info("Connected to %s", port)
                if self._on_connect_callback:
                    self._on_connect_callback()
                self._read_loop()
            except serial.SerialException as e:
                log.warning("Connection failed: %s", e)
            finally:
                self._connected.clear()
                if self._serial and self._serial.is_open:
                    self._serial.close()
                if self._on_disconnect_callback:
                    self._on_disconnect_callback()

            if self._running:
                log.info("Reconnecting in %ds…", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _read_loop(self):
        """Read raw bytes and deliver to callback."""
        while self._running and self._serial and self._serial.is_open:
            try:
                data = self._serial.read(1024)
                if data and self._callback:
                    self._callback(data)
            except serial.SerialException:
                log.warning("Serial read error, connection lost")
                break
            except Exception:
                log.exception("Unexpected error in read loop")
                break

    @staticmethod
    def _detect_bt_port() -> str | None:
        """Auto-detect a Bluetooth serial port."""
        for port_info in serial.tools.list_ports.comports():
            desc = (port_info.description or "").lower()
            hwid = (port_info.hwid or "").lower()
            if "bluetooth" in desc or "bthenum" in hwid:
                log.info("Auto-detected BT port: %s (%s)", port_info.device, desc)
                return port_info.device
        return None

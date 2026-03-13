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
        """Auto-detect a Bluetooth RFCOMM serial port.

        Windows exposes Bluetooth serial ports in several ways depending on the
        BT stack version and driver.  We try five heuristics in priority order:

        1. HWID contains "BTHENUM"  — standard Microsoft BT enumerator
        2. HWID contains "BTH"      — alternative BT HWID prefix
        3. Description contains "bluetooth"
        4. Description contains "standard serial over bluetooth"
        5. Manufacturer contains "bluetooth"

        Preference is given to *outgoing* ports (used for connecting *to* the
        Mac), but we accept any match.
        """
        all_ports = serial.tools.list_ports.comports()

        # Log all ports at DEBUG level so users can diagnose without editing code
        if all_ports:
            log.debug("Available COM ports:")
            for p in all_ports:
                log.debug("  %s | desc=%r | hwid=%r | mfr=%r",
                          p.device, p.description, p.hwid, p.manufacturer)
        else:
            log.debug("No COM ports found at all")

        BT_HWID_KEYWORDS    = ("bthenum", "bth\\")
        BT_DESC_KEYWORDS    = ("bluetooth", "standard serial over bluetooth link")
        BT_MFR_KEYWORDS     = ("bluetooth",)

        # Serial Port Profile (SPP) UUID — present in outgoing port HWIDs
        SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"

        outgoing = []   # outgoing ports — Windows connects TO the Mac (correct)
        incoming = []   # incoming ports — remote device connects TO Windows (wrong direction)
        other    = []   # BT ports without a clear direction

        for p in all_ports:
            desc = (p.description  or "").lower()
            hwid = (p.hwid         or "").lower()
            mfr  = (p.manufacturer or "").lower()

            is_bt = (
                any(kw in hwid for kw in BT_HWID_KEYWORDS)
                or any(kw in desc for kw in BT_DESC_KEYWORDS)
                or any(kw in mfr  for kw in BT_MFR_KEYWORDS)
            )
            if not is_bt:
                continue

            # Classify direction
            # Outgoing: HWID has SPP UUID, or description says "outgoing"
            if SPP_UUID in hwid or "outgoing" in desc:
                outgoing.append((p.device, p.description, "outgoing"))
            # Incoming: description says "incoming"
            elif "incoming" in desc:
                incoming.append((p.device, p.description, "incoming"))
            else:
                other.append((p.device, p.description, "unknown-direction"))

        # Priority: outgoing > unknown-direction > incoming
        ranked = outgoing + other + incoming

        if ranked:
            device, description, direction = ranked[0]
            log.info("Auto-detected BT port: %s (%s) [%s]", device, description, direction)
            if len(ranked) > 1:
                log.debug(
                    "Other BT port candidates (set com_port in config.yaml to pin one):"
                )
                for d, desc, dr in ranked[1:]:
                    log.debug("  %s (%s) [%s]", d, desc, dr)
            if direction == "incoming":
                log.warning(
                    "Selected port %s appears to be an INCOMING port. "
                    "The receiver needs an OUTGOING port to connect to the Mac. "
                    "In Bluetooth Settings → COM Ports, add an Outgoing port, "
                    "or set com_port explicitly in config.yaml.", device
                )
            return device

        log.warning(
            "No Bluetooth serial port found.\n"
            "  Make sure you have completed these steps on Windows:\n"
            "  1. Open Settings → Bluetooth → 'Add a device' and pair with your Mac.\n"
            "  2. Open Control Panel → Devices and Printers → right-click the Mac →\n"
            "     'Properties' → 'Services' tab → check 'Serial port (RFCOMM)'.\n"
            "     This creates an outgoing COM port.\n"
            "  3. Alternatively: Control Panel → Hardware and Sound →\n"
            "     Bluetooth Settings → 'COM Ports' tab → 'Add' → Outgoing.\n"
            "  4. If a COM port exists but was not detected, specify it explicitly:\n"
            "     Set  com_port: \"COMx\"  in win-receiver/config.yaml.\n"
            "  Run with LOG_LEVEL=DEBUG (or set log_level: DEBUG in config.yaml)\n"
            "  to print the full list of detected COM ports."
        )
        return None

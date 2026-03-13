"""
HID keyboard capture using hidapi.

Opens the Apple internal keyboard HID device (non-seizing) and reads
8-byte boot-protocol keyboard reports in a background thread.

See docs/ARCHITECTURE.md §4.1 for spec.
"""

import hid
import threading
import logging

log = logging.getLogger(__name__)

APPLE_VID = 0x05AC
USAGE_PAGE_KEYBOARD = 0x01
USAGE_KEYBOARD = 0x06


class HIDKeyboardReader:
    """Read raw HID keyboard reports from the Apple keyboard (non-seizing)."""

    def __init__(self, callback):
        """
        Args:
            callback: Called with 8-byte report (bytes) on each HID event.
        """
        self._callback = callback
        self._device = None
        self._thread = None
        self._running = False

    def start(self):
        """Open the HID device (non-seizing) and begin reading in a background thread."""
        dev_info = self._find_apple_keyboard()
        if dev_info is None:
            raise RuntimeError("No Apple keyboard HID device found")

        self._device = hid.device()
        self._device.open_path(dev_info["path"])
        self._device.set_nonblocking(False)
        log.info("Opened keyboard: %s [%s]",
                 dev_info.get("product_string", "?"),
                 dev_info["path"])

        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop reading and close the HID device."""
        self._running = False
        if self._device:
            self._device.close()
            self._device = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _find_apple_keyboard(self):
        """Find the first Apple keyboard HID device."""
        for dev in hid.enumerate():
            if (dev.get("vendor_id") == APPLE_VID
                    and dev.get("usage_page") == USAGE_PAGE_KEYBOARD
                    and dev.get("usage") == USAGE_KEYBOARD):
                return dev
        # Fallback: any Apple device on the keyboard usage page
        for dev in hid.enumerate():
            if (dev.get("vendor_id") == APPLE_VID
                    and dev.get("usage_page") == USAGE_PAGE_KEYBOARD):
                return dev
        return None

    def _read_loop(self):
        """Continuously read 8-byte HID reports."""
        while self._running:
            try:
                data = self._device.read(64, timeout_ms=100)
                if data and len(data) >= 8:
                    report = bytes(data[:8])
                    self._callback(report)
            except OSError:
                if self._running:
                    log.warning("HID keyboard read error, device may have disconnected")
                break
            except Exception:
                if self._running:
                    log.exception("HID keyboard read error")
                break

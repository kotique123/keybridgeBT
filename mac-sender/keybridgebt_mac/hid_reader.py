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
# On Apple Silicon (and newer Intel) Macs the internal keyboard enumerates with
# VID=0x0000 rather than 0x05AC.  We accept both.
APPLE_VID_INTERNAL = 0x0000
USAGE_PAGE_KEYBOARD = 0x01
USAGE_KEYBOARD = 0x06


def _is_apple_device(dev: dict) -> bool:
    """Return True if the device looks like an Apple keyboard regardless of VID."""
    vid = dev.get("vendor_id", -1)
    mfr = (dev.get("manufacturer_string") or "").lower()
    prod = (dev.get("product_string") or "").lower()
    return (
        vid == APPLE_VID
        or vid == APPLE_VID_INTERNAL
        or "apple" in mfr
        or "apple" in prod
    )


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
        """Find the built-in (or external) Apple keyboard HID device.

        Apple Silicon and newer Intel Macs enumerate the internal keyboard with
        VID=0x0000 rather than the traditional 0x05AC.  We therefore match on
        usage page + usage first and confirm the device is Apple-branded.
        """
        devices = hid.enumerate()

        # Pass 1 — exact keyboard boot-protocol interface (usage_page=0x01, usage=0x06)
        for dev in devices:
            if (dev.get("usage_page") == USAGE_PAGE_KEYBOARD
                    and dev.get("usage") == USAGE_KEYBOARD
                    and _is_apple_device(dev)):
                log.debug("Found keyboard (pass 1): VID=%#06x product=%r path=%r",
                          dev.get("vendor_id"), dev.get("product_string"), dev.get("path"))
                return dev

        # Pass 2 — any Apple device on the generic keyboard usage page
        for dev in devices:
            if (dev.get("usage_page") == USAGE_PAGE_KEYBOARD
                    and _is_apple_device(dev)):
                log.debug("Found keyboard (pass 2): VID=%#06x product=%r path=%r",
                          dev.get("vendor_id"), dev.get("product_string"), dev.get("path"))
                return dev

        # Pass 3 — any device whose product string mentions "keyboard"
        for dev in devices:
            prod = (dev.get("product_string") or "").lower()
            if "keyboard" in prod and _is_apple_device(dev):
                log.debug("Found keyboard (pass 3): VID=%#06x product=%r path=%r",
                          dev.get("vendor_id"), dev.get("product_string"), dev.get("path"))
                return dev

        log.error(
            "No Apple keyboard HID device found. Ensure Input Monitoring permission "
            "is granted in System Settings → Privacy & Security → Input Monitoring."
        )
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

"""
Keyboard capture via Quartz CGEventTap.

Replaces hidapi-based HID reader. Uses the same Accessibility permission
as the trackpad reader, so only one OS permission grant is needed.

Produces 8-byte HID boot-protocol keyboard reports identical to what the
old HID reader produced, so the Windows receiver needs no changes.

Also handles the global hotkey (Cmd+Shift+F12) and input seizure:
when ``seize`` is True, keyboard events are consumed from the Mac and
only forwarded to Windows.

See docs/ARCHITECTURE-v2.md §5.1 for spec.
"""

import threading
import logging
import Quartz

log = logging.getLogger(__name__)

# ── macOS virtual keycode → HID usage code mapping ──────────────────────
# Source: Apple Technical Note TN2450 / USB HID Usage Tables 1.12
_MAC_TO_HID = {
    # Letters
    0x00: 0x04, 0x01: 0x16, 0x02: 0x07, 0x03: 0x09, 0x04: 0x0B,  # A S D F H
    0x05: 0x0A, 0x06: 0x1D, 0x07: 0x1B, 0x08: 0x06, 0x09: 0x19,  # G Z X C V
    0x0B: 0x05, 0x0C: 0x14, 0x0D: 0x1A, 0x0E: 0x08, 0x0F: 0x15,  # B Q W E R
    0x10: 0x1C, 0x11: 0x17,                                        # Y T
    # Digits
    0x12: 0x1E, 0x13: 0x1F, 0x14: 0x20, 0x15: 0x21, 0x16: 0x23,  # 1 2 3 4 6
    0x17: 0x22, 0x18: 0x2E, 0x19: 0x26, 0x1A: 0x24, 0x1B: 0x2D,  # 5 = 9 7 -
    0x1C: 0x25, 0x1D: 0x27,                                        # 8 0
    # Punctuation / symbols
    0x1E: 0x30, 0x1F: 0x12, 0x20: 0x18, 0x21: 0x2F, 0x22: 0x0C,  # ] O U [ I
    0x23: 0x13, 0x24: 0x28, 0x25: 0x0F, 0x26: 0x0D, 0x27: 0x34,  # P Ret L J '
    0x28: 0x0E, 0x29: 0x33, 0x2A: 0x31, 0x2B: 0x36, 0x2C: 0x38,  # K ; \ , /
    0x2D: 0x11, 0x2E: 0x10, 0x2F: 0x37,                            # N M .
    # Whitespace / editing
    0x30: 0x2B, 0x31: 0x2C, 0x32: 0x35, 0x33: 0x2A,              # Tab Spc ` BS
    0x35: 0x29,                                                     # Esc
    0x0A: 0x64,                                                     # § (ISO)
    # Arrow keys
    0x7B: 0x50, 0x7C: 0x4F, 0x7D: 0x51, 0x7E: 0x52,              # ← → ↓ ↑
    # Function keys
    0x7A: 0x3A, 0x78: 0x3B, 0x63: 0x3C, 0x76: 0x3D,              # F1–F4
    0x60: 0x3E, 0x61: 0x3F, 0x62: 0x40, 0x64: 0x41,              # F5–F8
    0x65: 0x42, 0x6D: 0x43, 0x67: 0x44, 0x6F: 0x45,              # F9–F12
    0x69: 0x46, 0x6B: 0x47, 0x71: 0x48,                            # F13–F15
    # Navigation
    0x72: 0x49, 0x73: 0x4A, 0x74: 0x4B, 0x75: 0x4C,              # Ins Home PgUp Del
    0x77: 0x4D, 0x79: 0x4E,                                        # End PgDn
    # Caps Lock
    0x39: 0x39,
    # Numpad
    0x52: 0x62, 0x53: 0x59, 0x54: 0x5A, 0x55: 0x5B,              # KP 0–3
    0x56: 0x5C, 0x57: 0x5D, 0x58: 0x5E, 0x59: 0x5F,              # KP 4–7
    0x5B: 0x60, 0x5C: 0x61,                                        # KP 8–9
    0x41: 0x63, 0x43: 0x55, 0x45: 0x57, 0x4B: 0x54,              # KP . * + /
    0x4C: 0x58, 0x4E: 0x56, 0x51: 0x67, 0x47: 0x53,              # KP Enter - = NumLk
}

# ── Modifier keycode → HID modifier-byte bit ────────────────────────────
_MODIFIER_BIT = {
    0x3B: 0x01,  # Left Control
    0x38: 0x02,  # Left Shift
    0x3A: 0x04,  # Left Option (Alt)
    0x37: 0x08,  # Left Command (GUI)
    0x3E: 0x10,  # Right Control
    0x3C: 0x20,  # Right Shift
    0x3D: 0x40,  # Right Option (Alt)
    0x36: 0x80,  # Right Command (GUI)
}

_MODIFIER_KEYCODES = set(_MODIFIER_BIT.keys())

# ── Keyboard event types we tap ─────────────────────────────────────────
_KEY_EVENTS = (
    Quartz.kCGEventKeyDown,
    Quartz.kCGEventKeyUp,
    Quartz.kCGEventFlagsChanged,
)


class KeyboardTap:
    """CGEventTap-based keyboard reader with hotkey toggle and input seizure.

    Produces 8-byte HID boot-protocol reports::

        [modifier, reserved, key1, key2, key3, key4, key5, key6]

    Parameters
    ----------
    report_callback : callable(bytes)
        Called with each 8-byte report whenever the key state changes.
    toggle_callback : callable()
        Called when the hotkey combo is detected.
    hotkey_keycode : int
        macOS virtual keycode for the hotkey (default: F12 = 111 = 0x6F).
    hotkey_modifiers : int
        CGEventFlags bitmask for the hotkey modifiers.
    """

    def __init__(self, report_callback, toggle_callback,
                 hotkey_keycode=111, hotkey_modifiers=0x180000):
        self._report_cb = report_callback
        self._toggle_cb = toggle_callback
        self._hotkey_kc = hotkey_keycode
        self._hotkey_mod = hotkey_modifiers

        self._seize = False
        self._pressed_keys: set[int] = set()   # HID usage codes currently down
        self._pressed_mods: set[int] = set()   # modifier keycodes currently down
        self._modifier_byte = 0

        self._tap = None
        self._running = False
        self._thread: threading.Thread | None = None

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def seize(self) -> bool:
        return self._seize

    @seize.setter
    def seize(self, value: bool):
        self._seize = value
        if not value:
            # When seizure ends, send an all-keys-released report so
            # Windows doesn't keep stuck keys
            self._pressed_keys.clear()
            self._pressed_mods.clear()
            self._modifier_byte = 0
            self._emit_report()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, name="KeyboardTap", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    # ── Internal ─────────────────────────────────────────────────────────

    def _run(self):
        mask = 0
        for etype in _KEY_EVENTS:
            mask |= Quartz.CGEventMaskBit(etype)

        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,   # active: can consume events
            mask,
            self._tap_callback,
            None,
        )

        if self._tap is None:
            log.error(
                "Failed to create keyboard event tap. "
                "Grant Accessibility permission in System Settings → "
                "Privacy & Security → Accessibility."
            )
            return

        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(self._tap, True)

        log.info("Keyboard capture started (CGEventTap)")

        while self._running:
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.2, False)

    def _tap_callback(self, proxy, event_type, event, refcon):
        # Re-enable if the system timed out the tap
        if event_type == Quartz.kCGEventTapDisabledByTimeout:
            log.warning("Keyboard event tap disabled by timeout, re-enabling")
            if self._tap:
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        try:
            return self._handle_event(event_type, event)
        except Exception:
            log.exception("Error in keyboard tap callback")
            return event

    def _handle_event(self, event_type, event):
        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        flags = Quartz.CGEventGetFlags(event)

        # ── Hotkey detection (always active, always consumed) ────────
        if event_type == Quartz.kCGEventKeyDown:
            if (keycode == self._hotkey_kc
                    and (flags & self._hotkey_mod) == self._hotkey_mod):
                log.info("Hotkey triggered (Cmd+Shift+F12)")
                self._toggle_cb()
                return None  # consume hotkey

        # ── Modifier key change ──────────────────────────────────────
        if event_type == Quartz.kCGEventFlagsChanged:
            if keycode in _MODIFIER_KEYCODES:
                if keycode in self._pressed_mods:
                    # Was pressed → now released
                    self._pressed_mods.discard(keycode)
                    self._modifier_byte &= ~_MODIFIER_BIT[keycode]
                else:
                    # Was released → now pressed
                    self._pressed_mods.add(keycode)
                    self._modifier_byte |= _MODIFIER_BIT[keycode]
                self._emit_report()

            if self._seize:
                return None
            return event

        # ── Regular key down/up ──────────────────────────────────────
        hid_code = _MAC_TO_HID.get(keycode)
        if hid_code is None:
            # Unknown key — consume if seizing, pass through otherwise
            return None if self._seize else event

        if event_type == Quartz.kCGEventKeyDown:
            self._pressed_keys.add(hid_code)
        elif event_type == Quartz.kCGEventKeyUp:
            self._pressed_keys.discard(hid_code)

        self._emit_report()

        if self._seize:
            return None  # consume — Mac doesn't see it
        return event      # pass through — Mac sees it normally

    def _emit_report(self):
        """Build 8-byte HID boot-protocol report and fire the callback."""
        # Non-modifier keys (6KRO boot protocol: max 6 simultaneous keys)
        keys = sorted(self._pressed_keys)[:6]
        report = bytes([
            self._modifier_byte & 0xFF,
            0x00,  # reserved
            *keys,
            *([0x00] * (6 - len(keys))),
        ])
        try:
            self._report_cb(report)
        except Exception:
            log.exception("report_callback raised")

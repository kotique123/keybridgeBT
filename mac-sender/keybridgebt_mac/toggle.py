"""
Global hotkey toggle for pausing/resuming HID forwarding.

Uses Quartz CGEventTap to intercept key combos system-wide.
Default: Ctrl+Option+K — configurable.

See docs/ARCHITECTURE.md §4.9 for spec.
"""

import threading
import logging
import Quartz

log = logging.getLogger(__name__)

kCGEventFlagMaskControl = 1 << 18
kCGEventFlagMaskAlternate = 1 << 19

DEFAULT_MODIFIERS = kCGEventFlagMaskControl | kCGEventFlagMaskAlternate
DEFAULT_KEYCODE = 40  # macOS virtual keycode for K


class HotkeyMonitor:
    """
    Listen for a global hotkey combo and call a toggle callback.
    Runs a CGEventTap in a background thread.
    """

    def __init__(self, callback, keycode=DEFAULT_KEYCODE,
                 modifiers=DEFAULT_MODIFIERS):
        self._callback = callback
        self._keycode = keycode
        self._modifiers = modifiers
        self._thread = None
        self._running = False
        self._tap = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        def tap_callback(proxy, event_type, event, refcon):
            if event_type == Quartz.kCGEventKeyDown:
                keycode = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode
                )
                flags = Quartz.CGEventGetFlags(event)
                if (keycode == self._keycode
                        and (flags & self._modifiers) == self._modifiers):
                    log.info("Hotkey triggered")
                    self._callback()
                    return None  # consume the event
            # Re-enable tap if system disabled it
            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                log.warning("Event tap disabled by timeout, re-enabling")
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
            tap_callback,
            None,
        )

        if self._tap is None:
            log.error(
                "Failed to create hotkey event tap. "
                "Grant Accessibility permission in System Settings → "
                "Privacy & Security → Accessibility."
            )
            return

        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(self._tap, True)

        log.info("Hotkey listener active (Ctrl+Option+K)")

        while self._running:
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.2, False)

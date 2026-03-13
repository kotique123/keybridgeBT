"""
Trackpad / mouse capture using Quartz CGEventTap.

Uses CGEventTap (NOT raw HID) because Apple's trackpad uses proprietary
multitouch HID extensions that don't map to standard usage IDs.
CGEventTap provides clean x/y deltas, scroll axes, and button state.

Requires Accessibility permission.

See docs/ARCHITECTURE.md §4.2 for spec.
"""

import struct
import threading
import logging
import Quartz

log = logging.getLogger(__name__)

# Event types we tap
_POINTER_EVENTS = (
    Quartz.kCGEventMouseMoved,
    Quartz.kCGEventLeftMouseDown,
    Quartz.kCGEventLeftMouseUp,
    Quartz.kCGEventRightMouseDown,
    Quartz.kCGEventRightMouseUp,
    Quartz.kCGEventOtherMouseDown,
    Quartz.kCGEventOtherMouseUp,
    Quartz.kCGEventLeftMouseDragged,
    Quartz.kCGEventRightMouseDragged,
    Quartz.kCGEventOtherMouseDragged,
    Quartz.kCGEventScrollWheel,
)


class TrackpadReader:
    """Capture trackpad/mouse input via CGEventTap."""

    def __init__(self, callback):
        """
        Args:
            callback: Called with (buttons: int, dx: int, dy: int,
                      scroll_v: int, scroll_h: int) on each event.
        """
        self._callback = callback
        self._thread = None
        self._running = False
        self._tap = None
        self._buttons = 0  # bitmask: bit0=left, bit1=right, bit2=middle
        self._seize = False

    @property
    def seize(self) -> bool:
        return self._seize

    @seize.setter
    def seize(self, value: bool):
        self._seize = value

    def start(self):
        """Create CGEventTap for pointer events, run in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the event tap."""
        self._running = False
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        """Set up CGEventTap and run the CFRunLoop."""
        mask = 0
        for etype in _POINTER_EVENTS:
            mask |= Quartz.CGEventMaskBit(etype)

        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,  # active: can consume events when seizing
            mask,
            self._tap_callback,
            None,
        )

        if self._tap is None:
            log.error(
                "Failed to create trackpad event tap. "
                "Grant Accessibility permission in System Settings → "
                "Privacy & Security → Accessibility."
            )
            return

        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(self._tap, True)

        log.info("Trackpad/mouse capture started (CGEventTap)")

        while self._running:
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.01, False)

        log.info("Trackpad/mouse capture stopped")

    def _tap_callback(self, proxy, event_type, event, refcon):
        """CGEventTap callback — extract pointer data and fire callback."""
        # Re-enable tap if the system disabled it (e.g. after timeout or
        # permission change). This mirrors the same handling in toggle.py.
        if event_type == Quartz.kCGEventTapDisabledByTimeout:
            log.warning("Trackpad event tap disabled by timeout, re-enabling")
            if self._tap:
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        try:
            dx = 0
            dy = 0
            scroll_v = 0
            scroll_h = 0

            # Movement events
            if event_type in (Quartz.kCGEventMouseMoved,
                              Quartz.kCGEventLeftMouseDragged,
                              Quartz.kCGEventRightMouseDragged,
                              Quartz.kCGEventOtherMouseDragged):
                dx = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaX)
                dy = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaY)

            # Button press events
            elif event_type == Quartz.kCGEventLeftMouseDown:
                self._buttons |= 0x01
            elif event_type == Quartz.kCGEventLeftMouseUp:
                self._buttons &= ~0x01
            elif event_type == Quartz.kCGEventRightMouseDown:
                self._buttons |= 0x02
            elif event_type == Quartz.kCGEventRightMouseUp:
                self._buttons &= ~0x02
            elif event_type == Quartz.kCGEventOtherMouseDown:
                self._buttons |= 0x04
            elif event_type == Quartz.kCGEventOtherMouseUp:
                self._buttons &= ~0x04

            # Scroll events
            elif event_type == Quartz.kCGEventScrollWheel:
                scroll_v = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGScrollWheelEventDeltaAxis1)
                scroll_h = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGScrollWheelEventDeltaAxis2)

            self._callback(self._buttons, dx, dy, scroll_v, scroll_h)

        except Exception:
            log.exception("Error in trackpad tap callback")

        # When seizing, consume the event so the Mac doesn't see it
        if self._seize:
            return None
        return event

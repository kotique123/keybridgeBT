"""
Mouse / pointer injection via SendInput on Windows.

Handles relative movement, button clicks, and scroll events.

See docs/ARCHITECTURE.md §5.8 and docs/TASKS.md Task 13.
"""

import logging
import ctypes
from ctypes import wintypes

log = logging.getLogger(__name__)

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000

WHEEL_DELTA = 120


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),  # ULONG_PTR — pointer-sized on 32/64-bit
    ]


class INPUT(ctypes.Structure):
    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


SendInput = ctypes.windll.user32.SendInput
SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype = ctypes.c_uint


class MouseInjector:
    """Inject mouse/pointer events via Windows SendInput."""

    def __init__(self):
        self._prev_buttons = 0

    def inject_pointer(self, buttons: int, dx: int, dy: int,
                       scroll_v: int = 0, scroll_h: int = 0):
        inputs = []

        # Movement
        if dx != 0 or dy != 0:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.dx = dx
            inp.mi.dy = dy
            inp.mi.mouseData = 0
            inp.mi.dwFlags = MOUSEEVENTF_MOVE
            inp.mi.time = 0
            inp.mi.dwExtraInfo = 0
            inputs.append(inp)

        # Button state changes
        button_map = [
            (0x01, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
            (0x02, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
            (0x04, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        ]
        for mask, down_flag, up_flag in button_map:
            was = bool(self._prev_buttons & mask)
            now = bool(buttons & mask)
            if now and not was:
                inp = INPUT()
                inp.type = INPUT_MOUSE
                inp.mi.dwFlags = down_flag
                inp.mi.dwExtraInfo = 0
                inputs.append(inp)
            elif not now and was:
                inp = INPUT()
                inp.type = INPUT_MOUSE
                inp.mi.dwFlags = up_flag
                inp.mi.dwExtraInfo = 0
                inputs.append(inp)

        self._prev_buttons = buttons

        # Vertical scroll
        if scroll_v != 0:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.dwFlags = MOUSEEVENTF_WHEEL
            inp.mi.mouseData = ctypes.c_ulong(
                scroll_v * WHEEL_DELTA & 0xFFFFFFFF
            ).value
            inp.mi.dwExtraInfo = 0
            inputs.append(inp)

        # Horizontal scroll
        if scroll_h != 0:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.dwFlags = MOUSEEVENTF_HWHEEL
            inp.mi.mouseData = ctypes.c_ulong(
                scroll_h * WHEEL_DELTA & 0xFFFFFFFF
            ).value
            inp.mi.dwExtraInfo = 0
            inputs.append(inp)

        if inputs:
            arr = (INPUT * len(inputs))(*inputs)
            sent = SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
            if sent != len(inputs):
                log.warning("SendInput mouse: sent %d/%d events", sent, len(inputs))

    def release_all(self):
        """Release all held mouse buttons. Call on disconnect."""
        inputs = []
        if self._prev_buttons & 0x01:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.dwFlags = MOUSEEVENTF_LEFTUP
            inp.mi.dwExtraInfo = 0
            inputs.append(inp)
        if self._prev_buttons & 0x02:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.dwFlags = MOUSEEVENTF_RIGHTUP
            inp.mi.dwExtraInfo = 0
            inputs.append(inp)
        if self._prev_buttons & 0x04:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi.dwFlags = MOUSEEVENTF_MIDDLEUP
            inp.mi.dwExtraInfo = 0
            inputs.append(inp)
        if inputs:
            arr = (INPUT * len(inputs))(*inputs)
            SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        self._prev_buttons = 0
